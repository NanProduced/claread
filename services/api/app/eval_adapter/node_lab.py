from __future__ import annotations

import asyncio
import json
import re
from dataclasses import asdict
from hashlib import sha256
from time import perf_counter
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from pydantic_ai import Agent

from app.agents.grammar_agent import GrammarAgentDeps, build_grammar_prompt
from app.agents.translation_agent import TranslationAgentDeps, build_translation_prompt
from app.agents.vocabulary_agent import VocabularyAgentDeps, build_vocabulary_prompt
from app.config.settings import get_settings
from app.eval_adapter.schemas import (
    ArticleAnalysisNodeLabCompareRequest,
    ArticleAnalysisNodeLabCompareResult,
    ArticleAnalysisNodeLabRunRequest,
    ArticleAnalysisNodeLabRunResult,
    EvalError,
    ModelIdentity,
    NodeLabBaselineConfig,
    NodeLabBaselineConfigRequest,
    NodeLabExampleEntry,
    NodeLabResultEntry,
    PromptIdentity,
    RequestSnapshot,
    SchemaIdentity,
    WorkflowIdentity,
)
from app.eval_adapter.shared import model_identity as build_model_identity
from app.eval_adapter.shared import trace_scope
from app.llm.agent_runner import extract_run_usage, run_agent_with_route
from app.llm.router import ModelSelectionError, validate_model_selection
from app.llm.routes import MODEL_ROUTE_ANNOTATION_GENERATION
from app.schemas.internal.drafts import GrammarDraft, TranslationDraft, VocabularyDraft
from app.schemas.internal.analysis import PreparedSentence
from app.schemas.internal.execution_plan import GoalExecutionPlan
from app.services.analysis.postprocess.anchor_resolution import (
    resolve_explicit_anchor_parts,
    resolve_grammar_anchor_to_source,
    resolve_vocabulary_anchor_spans,
)
from app.services.analysis.postprocess.draft_validators import (
    validate_grammar_draft,
    validate_vocabulary_draft,
)
from app.services.analysis.debug_snapshots import (
    build_preprocess_summary,
    build_runtime_summary,
    build_trace_refs,
)
from app.services.analysis.planning.goal_planner import build_goal_execution_plan
from app.services.analysis.planning.goal_views import get_annotation_style
from app.services.analysis.preprocess.input_preparation import prepare_input
from app.services.analysis.prompting.example_strategy import ExampleEntry
from app.services.analysis.prompting.node_lab_runtime import (
    NodeLabNodeName,
    NodeLabRuntimeOverride,
)
from app.services.analysis.prompting.prompt_loader import (
    get_prompt_version,
    load_agent_instructions,
    load_examples,
    load_policy_lines,
    load_policy_lines_raw,
)
from app.services.analysis.prompting.prompt_strategy import PromptStrategy
from app.services.analysis.prompting.rag.grammar_rag_service import (
    build_rag_debug_info,
    query_grammar_rag,
)

NODE_LAB_WORKFLOW_NAME = "article_analysis.node_lab"
NODE_LAB_WORKFLOW_VERSION = "1.0.0"


def _workflow_identity(topology_mode: str) -> WorkflowIdentity:
    return WorkflowIdentity(
        workflow_name=NODE_LAB_WORKFLOW_NAME,
        workflow_version=NODE_LAB_WORKFLOW_VERSION,
        topology_mode=topology_mode if topology_mode in {"learning", "academic"} else "unknown",
    )


def _schema_identity(topology_mode: str) -> SchemaIdentity:
    return SchemaIdentity(
        schema_version="article-analysis-node-lab-v1",
        topology_mode=topology_mode if topology_mode in {"learning", "academic"} else "unknown",
    )


def _request_snapshot(
    *,
    request_id: str,
    text: str,
    reading_goal: str,
    reading_variant: str,
    source_type: str,
    extended: bool,
    trace_scope_value: str,
    rag_mode: str,
) -> RequestSnapshot:
    return RequestSnapshot(
        request_id=request_id,
        source_text_hash=sha256(text.encode("utf-8")).hexdigest(),
        source_char_count=len(text),
        reading_goal=reading_goal,
        reading_variant=reading_variant,
        source_type=source_type,
        extended=extended,
        rag_mode=rag_mode,
        trace_scope=trace_scope_value,
    )


def _request_id(request: Any) -> str:
    if getattr(request, "request_id", None):
        return request.request_id
    if getattr(request, "trial_id", None):
        return f"node-lab:{request.trial_id}"
    return f"node-lab:{uuid4()}"


def _dump_model(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return value
    return {"value": value}


def _example_summary(selection_mode: str, examples: list[Any]) -> dict[str, Any]:
    dumped_examples = [
        asdict(example) if hasattr(example, "__dataclass_fields__") else _dump_model(example)
        for example in examples
    ]
    return {
        "selection_mode": selection_mode,
        "example_count": len(examples),
        "examples": dumped_examples,
    }


def _runtime_summary(
    usage: dict[str, Any] | None,
    *,
    latency_ms: int,
    node_name: str,
) -> dict[str, Any]:
    summary = build_runtime_summary(
        {
            "available": bool(usage),
            "per_agent": {node_name: usage} if usage else {},
            "aggregate": usage or {},
        },
        latency_ms=latency_ms,
        billed_points=0,
    )
    summary.pop("billed_points", None)
    return summary


def _parse_grammar_validation_warning(message: str) -> dict[str, Any]:
    text = str(message or "").strip()
    if not text:
        return {
            "code": "grammar_validation_warning",
            "message": "",
        }

    patterns = [
        (
            r"^grammar_note: sentence_id (?P<sentence_id>\S+) not found$",
            "grammar_sentence_missing",
        ),
        (
            r"^grammar_note: span text '(?P<anchor_text>.+)' not found in sentence (?P<sentence_id>\S+)$",
            "grammar_span_not_found",
        ),
        (
            r"^sentence_analysis: sentence_id (?P<sentence_id>\S+) not found$",
            "sentence_analysis_sentence_missing",
        ),
        (
            r"^sentence_analysis: chunks missing for sentence (?P<sentence_id>\S+); (?P<detail>.+)$",
            "sentence_analysis_chunks_missing",
        ),
        (
            r"^sentence_analysis: chunk text '(?P<anchor_text>.+)' not found in sentence (?P<sentence_id>\S+)$",
            "sentence_analysis_chunk_not_found",
        ),
    ]

    for pattern, code in patterns:
        match = re.match(pattern, text)
        if match:
            payload = {"code": code, "message": text}
            payload.update({key: value for key, value in match.groupdict().items() if value is not None})
            return payload

    return {
        "code": "grammar_validation_warning",
        "message": text,
    }


def _grammar_quick_validation(
    *,
    node_name: NodeLabNodeName,
    node_output: dict[str, Any] | None,
    prepared_input: Any,
) -> dict[str, Any] | None:
    if node_name != "grammar" or node_output is None:
        return None

    try:
        draft = GrammarDraft.model_validate(node_output)
    except Exception as exc:
        return {
            "validator": "grammar_draft_v1",
            "status": "error",
            "warning_count": 1,
            "hard_warning_count": 1,
            "soft_warning_count": 0,
            "warnings": [
                {
                    "code": "grammar_draft_parse_failed",
                    "message": str(exc),
                    "severity": "hard",
                }
            ],
            "soft_warnings": [],
        }

    sentences = [
        PreparedSentence.model_validate(sentence)
        if not isinstance(sentence, PreparedSentence)
        else sentence
        for sentence in (getattr(prepared_input, "sentences", None) or [])
    ]
    hard_warnings = []
    for message in validate_grammar_draft(draft, sentences):
        warning = _parse_grammar_validation_warning(message)
        warning["severity"] = "hard"
        hard_warnings.append(warning)
    soft_warnings = _grammar_anchor_quality_warnings(draft, sentences)
    return {
        "validator": "grammar_draft_v1",
        "status": "warning" if hard_warnings or soft_warnings else "pass",
        "warning_count": len(hard_warnings),
        "hard_warning_count": len(hard_warnings),
        "soft_warning_count": len(soft_warnings),
        "warnings": hard_warnings,
        "soft_warnings": soft_warnings,
    }


_GRAMMAR_ANCHOR_BOUNDARY_PUNCTUATION = " \t\r\n,.;:!?，。；：！？"
_GRAMMAR_ANCHOR_MAX_CHARS = 72
_GRAMMAR_ANCHOR_MAX_SENTENCE_RATIO = 0.6
_WEAK_SINGLE_GRAMMAR_ANCHORS = {
    "which", "that", "who", "whom", "whose", "where", "when", "why",
    "but", "and", "or", "than",
}


def _grammar_anchor_quality_warnings(
    draft: GrammarDraft,
    sentences: list[PreparedSentence],
) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    sentence_map = {sentence.sentence_id: sentence for sentence in sentences}

    for item in draft.grammar_notes:
        sentence = sentence_map.get(item.sentence_id)
        if sentence is None:
            continue

        sentence_text = sentence.text
        sentence_len = max(1, len(sentence_text.strip()))
        total_anchor_len = 0

        for span in item.spans:
            anchor_text = str(span.text or "")
            stripped_anchor = anchor_text.strip(_GRAMMAR_ANCHOR_BOUNDARY_PUNCTUATION)
            resolved_anchor = resolve_grammar_anchor_to_source(
                sentence,
                anchor_text,
                span.occurrence,
            )
            comparable_anchor_text = (
                resolved_anchor.text if resolved_anchor is not None else stripped_anchor or anchor_text
            )
            total_anchor_len += len(comparable_anchor_text.strip())

            if "..." in anchor_text and "..." not in sentence_text:
                ellipsis_warning = {
                    "sentence_id": item.sentence_id,
                    "anchor_text": anchor_text,
                    "severity": "soft",
                }
                if (
                    resolved_anchor is not None
                    and resolved_anchor.resolution_kind == "schematic_ellipsis_expanded"
                ):
                    warnings.append({
                        "code": "recovered_schematic_ellipsis_grammar_anchor",
                        "message": (
                            "grammar_note span 使用了讲义式省略号模板，但可被恢复为原句真实子串: "
                            f"{anchor_text}"
                        ),
                        "resolved_anchor_text": resolved_anchor.text,
                        **ellipsis_warning,
                    })
                else:
                    warnings.append({
                        "code": "schematic_ellipsis_grammar_anchor",
                        "message": f"grammar_note span 使用了讲义式省略号模板: {anchor_text}",
                        **ellipsis_warning,
                    })

            if stripped_anchor and stripped_anchor != anchor_text:
                warnings.append({
                    "code": "boundary_punctuation_grammar_anchor",
                    "message": f"grammar_note span 包含首尾无关标点或空格: {anchor_text}",
                    "sentence_id": item.sentence_id,
                    "anchor_text": anchor_text,
                    "resolved_anchor_text": comparable_anchor_text,
                    "severity": "soft",
                })

            comparable_anchor = comparable_anchor_text.strip().rstrip(".!?")
            comparable_sentence = sentence_text.strip().rstrip(".!?")
            if comparable_anchor and comparable_sentence and comparable_anchor == comparable_sentence:
                warnings.append({
                    "code": "full_sentence_grammar_anchor",
                    "message": f"grammar_note span 覆盖整句: {comparable_anchor_text}",
                    "sentence_id": item.sentence_id,
                    "anchor_text": comparable_anchor_text,
                    "severity": "soft",
                })

            anchor_words = re.findall(r"[A-Za-z]+", comparable_anchor_text)
            if len(anchor_words) == 1 and anchor_words[0].casefold() in _WEAK_SINGLE_GRAMMAR_ANCHORS:
                warnings.append({
                    "code": "weak_short_grammar_anchor",
                    "message": f"grammar_note span 只有低信息量关系词: {comparable_anchor_text}",
                    "sentence_id": item.sentence_id,
                    "anchor_text": comparable_anchor_text,
                    "severity": "soft",
                })

            if len(comparable_anchor_text.strip()) > _GRAMMAR_ANCHOR_MAX_CHARS:
                warnings.append({
                    "code": "long_grammar_anchor",
                    "message": f"grammar_note span 过长，不适合作为原文行内锚点: {comparable_anchor_text}",
                    "sentence_id": item.sentence_id,
                    "anchor_text": comparable_anchor_text,
                    "severity": "soft",
                })

        if total_anchor_len / sentence_len > _GRAMMAR_ANCHOR_MAX_SENTENCE_RATIO:
            warnings.append({
                "code": "broad_grammar_anchor",
                "message": f"grammar_note spans 覆盖原句比例过高: {item.sentence_id}",
                "sentence_id": item.sentence_id,
                "anchor_text": " || ".join(span.text for span in item.spans),
                "severity": "soft",
            })

    return warnings


def _parse_vocabulary_validation_warning(message: str) -> dict[str, Any]:
    text = str(message or "").strip()
    if not text:
        return {
            "code": "vocabulary_validation_warning",
            "message": "",
        }

    patterns = [
        (
            r"^(?P<annotation_type>vocab_highlight|phrase_gloss|context_gloss): sentence_id (?P<sentence_id>\S+) not found$",
            "vocabulary_sentence_missing",
        ),
        (
            r"^(?P<annotation_type>vocab_highlight|phrase_gloss|context_gloss): text '(?P<anchor_text>.+)' not found in sentence (?P<sentence_id>\S+)$",
            "vocabulary_anchor_not_found",
        ),
        (
            r"^(?P<annotation_type>phrase_gloss): span text '(?P<anchor_text>.+)' not found in sentence (?P<sentence_id>\S+)$",
            "vocabulary_anchor_not_found",
        ),
        (
            r"^(?P<annotation_type>phrase_gloss): spans out of source order in sentence (?P<sentence_id>\S+)$",
            "phrase_gloss_spans_out_of_order",
        ),
    ]

    for pattern, code in patterns:
        match = re.match(pattern, text)
        if match:
            payload = {"code": code, "message": text}
            payload.update({key: value for key, value in match.groupdict().items() if value is not None})
            return payload

    return {
        "code": "vocabulary_validation_warning",
        "message": text,
    }


def _span_payloads(
    item: Any,
    sentence_map: dict[str, PreparedSentence],
) -> list[tuple[int, int]] | None:
    sentence = sentence_map.get(str(getattr(item, "sentence_id", "")))
    if sentence is None:
        return None
    spans = getattr(item, "spans", None)
    if spans:
        parts = [
            {"anchor_text": span.text, "occurrence": span.occurrence, "role": span.role}
            for span in spans
        ]
        resolved_parts = resolve_explicit_anchor_parts(sentence, parts)
        if resolved_parts is None:
            return None
        return [(part.span.start, part.span.end) for part in resolved_parts]
    resolved = resolve_vocabulary_anchor_spans(
        sentence,
        str(getattr(item, "text", "")),
        getattr(item, "occurrence", None),
    )
    if resolved is None:
        return None
    return [(span.start, span.end) for span in resolved]


def _spans_overlap(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return left[0] < right[1] and right[0] < left[1]


def _span_contains(container: tuple[int, int], inner: tuple[int, int]) -> bool:
    return container[0] <= inner[0] and inner[1] <= container[1]


def _span_group_contains(
    container_group: list[tuple[int, int]],
    inner: tuple[int, int],
) -> bool:
    return any(_span_contains(container, inner) for container in container_group)


def _span_groups_overlap(
    left: list[tuple[int, int]],
    right: list[tuple[int, int]],
) -> bool:
    return any(_spans_overlap(left_span, right_span) for left_span in left for right_span in right)


def _vocabulary_duplicate_and_overlap_warnings(
    draft: VocabularyDraft,
    sentences: list[PreparedSentence],
) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    sentence_map = {sentence.sentence_id: sentence for sentence in sentences}
    typed_items: list[tuple[str, Any]] = [
        *[("vocab_highlight", item) for item in draft.vocab_highlights],
        *[("phrase_gloss", item) for item in draft.phrase_glosses],
        *[("context_gloss", item) for item in draft.context_glosses],
    ]

    text_groups: dict[tuple[str, str], list[tuple[str, Any]]] = {}
    for annotation_type, item in typed_items:
        sentence_id = str(getattr(item, "sentence_id", ""))
        text = str(getattr(item, "text", ""))
        spans = _span_payloads(item, sentence_map)
        anchor_signature = (
            json.dumps(spans, ensure_ascii=False, separators=(",", ":"))
            if spans is not None
            else text.casefold()
        )
        if sentence_id and anchor_signature:
            text_groups.setdefault((sentence_id, anchor_signature), []).append((annotation_type, item))

    for (sentence_id, _text_key), items in text_groups.items():
        annotation_types = sorted({annotation_type for annotation_type, _item in items})
        if len(annotation_types) <= 1:
            continue
        anchor_text = str(getattr(items[0][1], "text", ""))
        warnings.append({
            "code": "vocabulary_same_text_cross_type",
            "message": f"同一句同一文本被多个 vocabulary type 标注: {anchor_text}",
            "sentence_id": sentence_id,
            "anchor_text": anchor_text,
            "annotation_types": annotation_types,
        })

    rich_items = [
        (annotation_type, item, spans)
        for annotation_type, item in [
            *[("phrase_gloss", item) for item in draft.phrase_glosses],
            *[("context_gloss", item) for item in draft.context_glosses],
        ]
        if (spans := _span_payloads(item, sentence_map)) is not None
    ]
    for vocab in draft.vocab_highlights:
        vocab_spans = _span_payloads(vocab, sentence_map)
        if not vocab_spans:
            continue
        vocab_span = vocab_spans[0]
        for annotation_type, rich_item, rich_span in rich_items:
            if getattr(rich_item, "sentence_id", "") != vocab.sentence_id:
                continue
            if _span_group_contains(rich_span, vocab_span):
                warnings.append({
                    "code": f"vocab_highlight_subsumed_by_{annotation_type}",
                    "message": f"vocab_highlight 被 {annotation_type} 覆盖: {vocab.text}",
                    "sentence_id": vocab.sentence_id,
                    "anchor_text": vocab.text,
                    "container_text": getattr(rich_item, "text", ""),
                })
                break

    phrase_spans = [
        (item, spans)
        for item in draft.phrase_glosses
        if (spans := _span_payloads(item, sentence_map)) is not None
    ]
    context_spans = [
        (item, spans)
        for item in draft.context_glosses
        if (spans := _span_payloads(item, sentence_map)) is not None
    ]
    for phrase, phrase_span in phrase_spans:
        for context, context_span in context_spans:
            if phrase.sentence_id != context.sentence_id:
                continue
            if _span_groups_overlap(phrase_span, context_span):
                warnings.append({
                    "code": "phrase_context_overlap",
                    "message": f"phrase_gloss 与 context_gloss 锚点重叠: {phrase.text} / {context.text}",
                    "sentence_id": phrase.sentence_id,
                    "anchor_text": phrase.text,
                    "other_anchor_text": context.text,
                })

    return warnings


def _vocabulary_quick_validation(
    *,
    node_name: NodeLabNodeName,
    node_output: dict[str, Any] | None,
    prepared_input: Any,
) -> dict[str, Any] | None:
    if node_name != "vocabulary" or node_output is None:
        return None

    try:
        draft = VocabularyDraft.model_validate(node_output)
    except Exception as exc:
        return {
            "validator": "vocabulary_draft_v1",
            "status": "error",
            "warning_count": 1,
            "warnings": [
                {
                    "code": "vocabulary_draft_parse_failed",
                    "message": str(exc),
                }
            ],
        }

    sentences = [
        PreparedSentence.model_validate(sentence)
        if not isinstance(sentence, PreparedSentence)
        else sentence
        for sentence in (getattr(prepared_input, "sentences", None) or [])
    ]
    warnings = [
        _parse_vocabulary_validation_warning(message)
        for message in validate_vocabulary_draft(draft, sentences)
    ]
    warnings.extend(_vocabulary_duplicate_and_overlap_warnings(draft, sentences))
    return {
        "validator": "vocabulary_draft_v1",
        "status": "warning" if warnings else "pass",
        "warning_count": len(warnings),
        "warnings": warnings,
    }


def _quick_validation(
    *,
    node_name: NodeLabNodeName,
    node_output: dict[str, Any] | None,
    prepared_input: Any,
) -> dict[str, Any] | None:
    if node_name == "grammar":
        return _grammar_quick_validation(
            node_name=node_name,
            node_output=node_output,
            prepared_input=prepared_input,
        )
    if node_name == "vocabulary":
        return _vocabulary_quick_validation(
            node_name=node_name,
            node_output=node_output,
            prepared_input=prepared_input,
        )
    return None


def _policy_focus_for_node(plan: GoalExecutionPlan, node_name: NodeLabNodeName) -> str:
    if node_name == "grammar":
        return plan.policy.grammar_focus
    if node_name == "translation":
        return plan.policy.translation_focus
    return plan.policy.vocabulary_focus


def _baseline_examples(node_name: NodeLabNodeName, variant: str) -> list[ExampleEntry]:
    return [
        ExampleEntry(
            example_type=entry["example_type"],
            sentence_text=entry["sentence_text"],
            output_fragment=entry["output_fragment"],
        )
        for entry in load_examples(node_name, variant)
    ]


def _candidate_examples(runtime_override: NodeLabRuntimeOverride) -> list[ExampleEntry]:
    return [
        ExampleEntry(
            example_type=entry.example_type,
            sentence_text=entry.sentence_text,
            output_fragment=entry.output_fragment,
        )
        for entry in runtime_override.few_shot_override.examples
    ]


def _build_prompt_strategy(
    plan: GoalExecutionPlan,
    node_name: NodeLabNodeName,
    policy_lines: list[str],
) -> PromptStrategy:
    common = {
        "profile_id": plan.prompt_profile,
        "reading_goal": plan.goal_id,
        "reading_variant": plan.variant_id,
        "annotation_style": get_annotation_style(plan),
        "policy_lines": tuple(policy_lines),
    }
    if node_name == "grammar":
        return PromptStrategy(
            grammar_granularity=plan.policy.grammar_focus,
            **common,
        )
    if node_name == "translation":
        return PromptStrategy(
            annotation_style=None,
            translation_style=plan.policy.translation_focus,
            **{k: v for k, v in common.items() if k != "annotation_style"},
        )
    return PromptStrategy(
        vocabulary_policy=plan.policy.vocabulary_focus,
        **common,
    )


async def _resolve_examples(
    *,
    node_name: NodeLabNodeName,
    plan: GoalExecutionPlan,
    runtime_override: NodeLabRuntimeOverride | None,
    sentences_data: list[dict[str, Any]],
) -> tuple[list[ExampleEntry], str, dict[str, Any] | None]:
    if runtime_override is None:
        return _baseline_examples(node_name, plan.variant_id), "baseline", None

    mode = runtime_override.few_shot_override.few_shot_mode
    if mode == "off":
        return [], "off", None
    if mode == "candidate":
        return _candidate_examples(runtime_override), "candidate", None
    if mode == "baseline":
        return _baseline_examples(node_name, plan.variant_id), "baseline", None
    if mode != "rag":
        return _baseline_examples(node_name, plan.variant_id), "baseline", None

    if node_name != "grammar":
        raise ValueError("few_shot_mode='rag' is only supported for grammar in node_lab v1")

    settings = get_settings()
    if not getattr(settings, "grammar_rag_enabled", False):
        return _baseline_examples(node_name, plan.variant_id), "rag_fallback", {
            "disabled": True,
            "reason": "grammar_rag_enabled=false",
        }

    gn_result = await query_grammar_rag(
        variant=plan.variant_id,
        sentences=sentences_data,
        output_type="grammar_note",
    )
    sa_result = await query_grammar_rag(
        variant=plan.variant_id,
        sentences=sentences_data,
        output_type="sentence_analysis",
    )
    rag_examples = gn_result.examples + sa_result.examples
    rag_debug = {
        "grammar_note": build_rag_debug_info(gn_result),
        "sentence_analysis": build_rag_debug_info(sa_result),
    }
    if rag_examples:
        return rag_examples, "rag", rag_debug
    return _baseline_examples(node_name, plan.variant_id), "rag_fallback", rag_debug


def _resolved_agent_instructions(
    node_name: NodeLabNodeName,
    runtime_override: NodeLabRuntimeOverride | None,
) -> str:
    if runtime_override and runtime_override.instruction_override.mode == "override_text":
        return runtime_override.instruction_override.text or ""
    return load_agent_instructions(node_name)


def _resolved_policy_lines(
    node_name: NodeLabNodeName,
    plan: GoalExecutionPlan,
    runtime_override: NodeLabRuntimeOverride | None,
) -> list[str]:
    if runtime_override and runtime_override.policy_override.mode == "override_lines":
        return [line for line in runtime_override.policy_override.lines if line.strip()]
    return load_policy_lines_raw(node_name, _policy_focus_for_node(plan, node_name), plan.variant_id)


def _prompt_identity(runtime_override: NodeLabRuntimeOverride | None) -> PromptIdentity:
    return PromptIdentity(
        prompt_version=get_prompt_version(),
        prompt_snapshot_hash=runtime_override.snapshot_hash if runtime_override else None,
        prompt_variant_id=runtime_override.candidate_id if runtime_override else None,
    )


def _dynamic_agent_for_node(node_name: NodeLabNodeName, instructions: str) -> Agent[Any, Any]:
    if node_name == "vocabulary":
        return Agent[VocabularyAgentDeps, VocabularyDraft](
            model=None,
            output_type=VocabularyDraft,
            deps_type=VocabularyAgentDeps,
            instructions=instructions,
            name="node_lab_vocabulary_agent",
            retries=2,
            output_retries=3,
            instrument=False,
        )
    if node_name == "translation":
        return Agent[TranslationAgentDeps, TranslationDraft](
            model=None,
            output_type=TranslationDraft,
            deps_type=TranslationAgentDeps,
            instructions=instructions,
            name="node_lab_translation_agent",
            retries=2,
            output_retries=3,
            instrument=False,
        )
    return Agent[GrammarAgentDeps, GrammarDraft](
        model=None,
        output_type=GrammarDraft,
        deps_type=GrammarAgentDeps,
        instructions=instructions,
        name="node_lab_grammar_agent",
        retries=2,
        output_retries=3,
        instrument=False,
    )


def _build_deps_and_prompt(
    *,
    node_name: NodeLabNodeName,
    prompt_strategy: PromptStrategy,
    examples: list[ExampleEntry],
    sentences_data: list[dict[str, Any]],
) -> tuple[Any, str]:
    if node_name == "vocabulary":
        deps = VocabularyAgentDeps(
            sentences=sentences_data,
            prompt_strategy=prompt_strategy,
            examples=examples,
        )
        return deps, build_vocabulary_prompt(deps)
    if node_name == "translation":
        deps = TranslationAgentDeps(
            sentences=sentences_data,
            prompt_strategy=prompt_strategy,
            examples=examples,
        )
        return deps, build_translation_prompt(deps)
    deps = GrammarAgentDeps(
        sentences=sentences_data,
        prompt_strategy=prompt_strategy,
        examples=examples,
    )
    return deps, build_grammar_prompt(deps)


async def _run_dynamic_agent(
    *,
    node_name: NodeLabNodeName,
    deps: Any,
    prompt_preview: str,
    model_selection: Any,
    timeout_seconds: float | None,
    instructions: str,
) -> Any:
    async def _invoke() -> Any:
        return await run_agent_with_route(
            agent=_dynamic_agent_for_node(node_name, instructions),
            prompt=prompt_preview,
            deps=deps,
            route=MODEL_ROUTE_ANNOTATION_GENERATION,
            model_selection=model_selection,
        )

    if timeout_seconds is None:
        return await _invoke()
    return await asyncio.wait_for(_invoke(), timeout=timeout_seconds)


async def _run_node_lab_once(
    *,
    request: SimpleNamespace,
    participant_label: str,
    runtime_override: NodeLabRuntimeOverride | None,
    request_id: str,
) -> NodeLabResultEntry:
    started_at = perf_counter()
    model_selection = runtime_override.model_selection if runtime_override is not None else None
    model_identity: ModelIdentity | None = None
    requires_live_model = not getattr(request, "dry_run", False)

    try:
        # Dry-run only builds prompt/deps/debug output and does not actually call
        # the LLM, so resolve-only validation is enough there. Real execution
        # paths require a buildable model.
        validate_model_selection(
            get_settings(),
            model_selection,
            (MODEL_ROUTE_ANNOTATION_GENERATION,),
            buildable=requires_live_model,
        )
        model_identity = build_model_identity(model_selection, settings=get_settings())
    except ModelSelectionError as exc:
        latency_ms = int((perf_counter() - started_at) * 1000)
        return NodeLabResultEntry(
            participant_label=participant_label,
            candidate_id=runtime_override.candidate_id if runtime_override else None,
            snapshot_hash=runtime_override.snapshot_hash if runtime_override else None,
            status="failed",
            error=EvalError(code=type(exc).__name__, message=str(exc)),
            prompt_identity=_prompt_identity(runtime_override),
            model_identity=model_identity,
            runtime_summary={"latency_ms": latency_ms},
            trace_refs=build_trace_refs(request_id=request_id),
        )

    try:
        plan = build_goal_execution_plan(request.reading_goal, request.reading_variant)
        topology_mode = getattr(plan, "topology_mode", "unknown")
        if topology_mode != "learning":
            raise ValueError("node_lab v1 only supports learning topology; academic should use a dedicated academic lab/workflow")

        prepared_input = prepare_input(request.text)
        sentences_data = [
            {"sentence_id": sentence.sentence_id, "text": sentence.text}
            for sentence in prepared_input.sentences
        ]
        policy_lines = _resolved_policy_lines(request.node_name, plan, runtime_override)
        prompt_strategy = _build_prompt_strategy(plan, request.node_name, policy_lines)
        examples, selection_mode, rag_debug = await _resolve_examples(
            node_name=request.node_name,
            plan=plan,
            runtime_override=runtime_override,
            sentences_data=sentences_data,
        )
        agent_instructions_text = _resolved_agent_instructions(request.node_name, runtime_override)
        deps, prompt_preview = _build_deps_and_prompt(
            node_name=request.node_name,
            prompt_strategy=prompt_strategy,
            examples=examples,
            sentences_data=sentences_data,
        )
        node_output: dict[str, Any] | None = None
        usage: dict[str, Any] | None = None
        if not getattr(request, "dry_run", False):
            with trace_scope(request):
                result = await _run_dynamic_agent(
                    node_name=request.node_name,
                    deps=deps,
                    prompt_preview=prompt_preview,
                    model_selection=model_selection,
                    timeout_seconds=request.timeout_seconds,
                    instructions=agent_instructions_text,
                )
            output = result.output if hasattr(result, "output") else result
            node_output = _dump_model(output)
            usage = extract_run_usage(result)
    except TimeoutError as exc:
        latency_ms = int((perf_counter() - started_at) * 1000)
        return NodeLabResultEntry(
            participant_label=participant_label,
            candidate_id=runtime_override.candidate_id if runtime_override else None,
            snapshot_hash=runtime_override.snapshot_hash if runtime_override else None,
            status="timeout",
            error=EvalError(code=type(exc).__name__, message=str(exc)),
            prompt_identity=_prompt_identity(runtime_override),
            model_identity=model_identity,
            runtime_summary={"latency_ms": latency_ms},
            trace_refs=build_trace_refs(request_id=request_id),
        )
    except Exception as exc:
        latency_ms = int((perf_counter() - started_at) * 1000)
        return NodeLabResultEntry(
            participant_label=participant_label,
            candidate_id=runtime_override.candidate_id if runtime_override else None,
            snapshot_hash=runtime_override.snapshot_hash if runtime_override else None,
            status="failed",
            error=EvalError(code=type(exc).__name__, message=str(exc)),
            prompt_identity=_prompt_identity(runtime_override),
            model_identity=model_identity,
            runtime_summary={"latency_ms": latency_ms},
            trace_refs=build_trace_refs(request_id=request_id),
        )

    latency_ms = int((perf_counter() - started_at) * 1000)
    result_state = {
        "prepared_input": prepared_input,
        "goal_execution_plan": plan,
    }
    return NodeLabResultEntry(
        participant_label=participant_label,
        candidate_id=runtime_override.candidate_id if runtime_override else None,
        snapshot_hash=runtime_override.snapshot_hash if runtime_override else None,
        status="succeeded",
        prompt_identity=_prompt_identity(runtime_override),
        model_identity=model_identity,
        node_output=node_output,
        prompt_preview=prompt_preview,
        agent_instructions=agent_instructions_text,
        prepared_sentences=sentences_data,
        example_summary=_example_summary(selection_mode, examples),
        preprocess_summary=build_preprocess_summary(request.text, result_state),
        runtime_summary=_runtime_summary(usage, latency_ms=latency_ms, node_name=request.node_name),
        quick_validation=_quick_validation(
            node_name=request.node_name,
            node_output=node_output,
            prepared_input=prepared_input,
        ),
        rag_debug=rag_debug,
        trace_refs=build_trace_refs(request_id=request_id),
        warnings=[],
    )


def get_node_lab_baseline_config(
    request: NodeLabBaselineConfigRequest,
) -> NodeLabBaselineConfig:
    plan = build_goal_execution_plan(request.reading_goal, request.reading_variant)
    if getattr(plan, "topology_mode", "unknown") != "learning":
        raise ValueError("node_lab v1 only supports learning topology")

    policy_focus = _policy_focus_for_node(plan, request.node_name)
    baseline_examples = [
        NodeLabExampleEntry.model_validate(asdict(entry))
        for entry in _baseline_examples(request.node_name, request.reading_variant)
    ]

    settings = get_settings()
    return NodeLabBaselineConfig(
        node_name=request.node_name,
        reading_goal=request.reading_goal,
        reading_variant=request.reading_variant,
        prompt_version=get_prompt_version(),
        prompt_profile=plan.prompt_profile,
        policy_focus=policy_focus,
        agent_instructions=load_agent_instructions(request.node_name),
        policy_lines=load_policy_lines_raw(request.node_name, policy_focus, request.reading_variant),
        baseline_examples=baseline_examples,
        baseline_model_profile=settings.annotation_model_profile or settings.default_model_profile or None,
    )


async def run_article_analysis_node_lab(
    request: ArticleAnalysisNodeLabRunRequest,
) -> ArticleAnalysisNodeLabRunResult:
    request_id = _request_id(request)
    rag_mode = request.candidate_override.few_shot_override.few_shot_mode if request.candidate_override else "baseline"
    result_entry = await _run_node_lab_once(
        request=SimpleNamespace(**request.model_dump(mode="python")),
        participant_label="candidate" if request.candidate_override else "baseline",
        runtime_override=request.candidate_override,
        request_id=request_id,
    )
    request_snapshot = _request_snapshot(
        request_id=request_id,
        text=request.text,
        reading_goal=request.reading_goal,
        reading_variant=request.reading_variant,
        source_type=request.source_type,
        extended=request.extended,
        trace_scope_value=request.trace_scope,
        rag_mode="rag" if rag_mode == "rag" else "off",
    )
    return ArticleAnalysisNodeLabRunResult(
        node_name=request.node_name,
        request_snapshot=request_snapshot,
        workflow_identity=_workflow_identity("learning"),
        schema_identity=_schema_identity("learning"),
        run=result_entry,
    )


async def compare_article_analysis_node_lab(
    request: ArticleAnalysisNodeLabCompareRequest,
) -> ArticleAnalysisNodeLabCompareResult:
    request_id = _request_id(request)
    request_ns = SimpleNamespace(**request.model_dump(mode="python"))
    baseline, candidate = await asyncio.gather(
        _run_node_lab_once(
            request=request_ns,
            participant_label="baseline",
            runtime_override=None,
            request_id=f"{request_id}:baseline",
        ),
        _run_node_lab_once(
            request=request_ns,
            participant_label="candidate",
            runtime_override=request.candidate_override,
            request_id=f"{request_id}:candidate",
        ),
    )
    baseline_tokens = ((baseline.runtime_summary or {}).get("aggregate") or {}).get("total_tokens")
    candidate_tokens = ((candidate.runtime_summary or {}).get("aggregate") or {}).get("total_tokens")
    baseline_latency = (baseline.runtime_summary or {}).get("latency_ms") or 0
    candidate_latency = (candidate.runtime_summary or {}).get("latency_ms") or 0
    compare_status = (
        "complete"
        if baseline.status == "succeeded" and candidate.status == "succeeded"
        else "partial_failure"
        if "succeeded" in {baseline.status, candidate.status}
        else "total_failure"
    )
    compare_summary = {
        "result_status": {
            "baseline_status": baseline.status,
            "candidate_status": candidate.status,
            "compare_status": compare_status,
        },
        "prompt_changed": (baseline.prompt_preview or "") != (candidate.prompt_preview or ""),
        "token_delta": (
            candidate_tokens - baseline_tokens
            if isinstance(candidate_tokens, int) and isinstance(baseline_tokens, int)
            else None
        ),
        "baseline_latency_ms": baseline_latency,
        "candidate_latency_ms": candidate_latency,
        "latency_delta_ms": candidate_latency - baseline_latency,
    }
    request_snapshot = _request_snapshot(
        request_id=request_id,
        text=request.text,
        reading_goal=request.reading_goal,
        reading_variant=request.reading_variant,
        source_type=request.source_type,
        extended=request.extended,
        trace_scope_value=request.trace_scope,
        rag_mode="rag" if request.candidate_override.few_shot_override.few_shot_mode == "rag" else "off",
    )
    return ArticleAnalysisNodeLabCompareResult(
        node_name=request.node_name,
        request_snapshot=request_snapshot,
        workflow_identity=_workflow_identity("learning"),
        schema_identity=_schema_identity("learning"),
        baseline=baseline,
        candidate=candidate,
        compare_summary=compare_summary,
    )
