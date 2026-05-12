"""Workflow Nodes for article_analysis.

节点设计：
1. prepare_input - 输入清洗、分段分句
2. derive_user_config - 用户配置推导
3. parallel_agents - 词汇、结构、翻译并行标注
4. normalize_and_ground - 确定性归一化
5. repair_agent - 可选修复
6. project_render_scene - 前端协议投影
7. assemble_result - 结果收敛
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from typing import Any

from langchain_core.runnables import RunnableConfig
from langsmith import traceable

from app.agents.grammar_agent import GrammarAgentDeps
from app.agents.repair_agent import RepairAgentDeps
from app.agents.translation_agent import TranslationAgentDeps
from app.agents.vocabulary_agent import VocabularyAgentDeps
from app.config.settings import get_settings
from app.llm.agent_runner import extract_run_usage
from app.llm.router import resolve_model_config
from app.llm.routes import MODEL_ROUTE_ANNOTATION_GENERATION
from app.llm.runtime import get_model_selection
from app.llm.types import ModelSelection
from app.schemas.analysis import AnalyzeRequestMeta, ArticleStructure, RenderSceneModel, Warning
from app.schemas.internal.analysis import PreparedSentence
from app.schemas.internal.drafts import GrammarDraft, TranslationDraft, VocabularyDraft
from app.services.analysis.planning.goal_planner import build_goal_execution_plan
from app.services.analysis.postprocess.draft_validators import validate_all_drafts
from app.services.analysis.postprocess.normalize_and_ground import normalize_and_ground
from app.services.analysis.postprocess.projection import project_to_render_scene
from app.services.analysis.preprocess.input_preparation import prepare_input
from app.services.analysis.prompting.strategy_builder import (
    build_grammar_bundle_async,
    build_translation_bundle,
    build_vocabulary_bundle,
)
from app.services.analysis.runtime.runners import (
    run_grammar_agent,
    run_translation_agent,
    run_vocabulary_agent,
)
from app.workflow.analyze_state import AnalyzeState
from app.workflow.tracing import build_llm_trace_metadata

logger = logging.getLogger(__name__)
WORKFLOW_NAME = "article_analysis"
WORKFLOW_VERSION = "3.0.0"
MAX_ANNOTATION_ATTEMPTS = 3

# 触发 repair 的条件
ANCHOR_FAILURE_THRESHOLD = 0.35


def _annotation_count_by_type(annotations: list[Any]) -> dict[str, int]:
    counts = Counter(getattr(a, "type", str(type(a).__name__)) for a in annotations)
    return dict(sorted(counts.items()))


def _model_selection(config: RunnableConfig | None) -> ModelSelection | None:
    return get_model_selection(config)


def _aggregate_usage_summary(
    usages: dict[str, dict[str, object] | None],
) -> dict[str, object]:
    per_agent = {name: usage for name, usage in usages.items() if usage}
    if not per_agent:
        return {
            "available": False,
            "per_agent": {},
            "aggregate": {
                "input_tokens": None,
                "output_tokens": None,
                "total_tokens": None,
            },
            "note": "workflow 当前未从 agent 结果中提取到 usage。",
        }

    def _sum_token(field: str) -> int:
        return sum(int(usage.get(field, 0) or 0) for usage in per_agent.values())

    return {
        "available": True,
        "per_agent": per_agent,
        "aggregate": {
            "input_tokens": _sum_token("input_tokens"),
            "output_tokens": _sum_token("output_tokens"),
            "total_tokens": _sum_token("total_tokens"),
        },
    }


def _empty_result(
    *,
    request_id: str,
    payload: Any,
    profile_id: str,
) -> RenderSceneModel:
    return RenderSceneModel(
        request=AnalyzeRequestMeta(
            request_id=request_id,
            source_type=payload.source_type,
            reading_goal=payload.reading_goal,
            reading_variant=payload.reading_variant,
            profile_id=profile_id,
        ),
        article=ArticleStructure(
            source_type=payload.source_type,
            source_text=payload.text,
            render_text="",
            paragraphs=[],
            sentences=[],
        ),
        translations=[],
        inline_marks=[],
        sentence_entries=[],
        warnings=[],
    )


def _build_agent_trace_metadata(
    state: AnalyzeState,
    node_name: str,
    model_selection: ModelSelection | None = None,
) -> dict[str, object]:
    payload = state["payload"]
    plan = state["goal_execution_plan"]
    model_config = resolve_model_config(
        get_settings(), MODEL_ROUTE_ANNOTATION_GENERATION, model_selection
    )
    return build_llm_trace_metadata(
        workflow_name=WORKFLOW_NAME,
        workflow_version=WORKFLOW_VERSION,
        request_id=payload.request_id or "",
        source_type=payload.source_type,
        reading_goal=payload.reading_goal,
        reading_variant=payload.reading_variant,
        profile_id=plan.prompt_profile,
        model_name=model_config.model_name if model_config else "unconfigured",
        model_provider=model_config.provider if model_config else "unconfigured",
        extra={
            "node": node_name,
            "model_profile": model_config.profile_name if model_config else "unconfigured",
            "sentence_count": len(state["prepared_input"].sentences),
        },
    )


@traceable(name="vocabulary_llm_call", run_type="llm")
async def _run_vocabulary_llm_span(
    *,
    deps: VocabularyAgentDeps,
    metadata: dict[str, object],
    model_selection: ModelSelection | None = None,
) -> dict[str, Any]:
    result = await run_vocabulary_agent(deps, model_selection=model_selection)
    usage = extract_run_usage(result)
    return {"output": result.output if hasattr(result, "output") else result, "usage_metadata": usage}


@traceable(name="grammar_llm_call", run_type="llm")
async def _run_grammar_llm_span(
    *,
    deps: GrammarAgentDeps,
    metadata: dict[str, object],
    model_selection: ModelSelection | None = None,
) -> dict[str, Any]:
    result = await run_grammar_agent(deps, model_selection=model_selection)
    usage = extract_run_usage(result)
    return {"output": result.output if hasattr(result, "output") else result, "usage_metadata": usage}


@traceable(name="translation_llm_call", run_type="llm")
async def _run_translation_llm_span(
    *,
    deps: TranslationAgentDeps,
    metadata: dict[str, object],
    model_selection: ModelSelection | None = None,
) -> dict[str, Any]:
    result = await run_translation_agent(deps, model_selection=model_selection)
    usage = extract_run_usage(result)
    return {"output": result.output if hasattr(result, "output") else result, "usage_metadata": usage}


# -------------------------------------------------------------------
# Node implementations
# -------------------------------------------------------------------


async def prepare_input_node(state: AnalyzeState) -> AnalyzeState:
    payload = state["payload"]
    prepared_input = prepare_input(payload.text)
    warnings: list[Any] = []

    if not prepared_input.render_text.strip():
        return {
            "prepared_input": prepared_input,
            "warnings": warnings,
            "render_scene": _empty_result(
                request_id=payload.request_id or "",
                payload=payload,
                profile_id="unresolved",
            ),
        }

    FAIL_WARN = {"code_like", "other"}
    PROCEED_WARN = {"article_mixed", "structured_doc", "html_like"}

    if prepared_input.text_type in FAIL_WARN:
        warnings.append(
            Warning(
                code="UNSUPPORTED_TEXT_TYPE",
                level="warning",
                message=f"文本类型为 {prepared_input.text_type}，可能影响标注质量。",
            )
        )
    elif prepared_input.text_type in PROCEED_WARN:
        warnings.append(
            Warning(
                code="TEXT_TYPE_NEEDS_CARE",
                level="info",
                message=f"文本类型为 {prepared_input.text_type}，将继续分析但需注意质量。",
            )
        )

    if prepared_input.english_ratio < 0.45 or not prepared_input.sentences:
        warnings.append(
            Warning(
                code="LOW_ENGLISH_RATIO",
                level="warning",
                message=f"英文占比 {prepared_input.english_ratio:.0%} 低于阈值，或无有效句子。",
            )
        )

    if prepared_input.noise_ratio >= 0.55:
        warnings.append(
            Warning(
                code="HIGH_NOISE_RATIO",
                level="warning",
                message="输入中存在较多噪音内容，结果可能需要结合原文查看。",
            )
        )

    return {"prepared_input": prepared_input, "warnings": warnings}


async def derive_user_config_node(state: AnalyzeState) -> AnalyzeState:
    existing_plan = state.get("goal_execution_plan")
    if existing_plan is not None:
        return {}

    payload = state["payload"]
    plan = build_goal_execution_plan(payload.reading_goal, payload.reading_variant)
    return {"goal_execution_plan": plan}


async def _run_parallel_agents(
    state: AnalyzeState,
    model_selection: ModelSelection | None,
) -> dict[str, Any]:
    """并行运行三个 agent。"""
    prepared_input = state["prepared_input"]
    plan = state["goal_execution_plan"]

    sentences_data = [
        {"sentence_id": s.sentence_id, "text": s.text}
        for s in prepared_input.sentences
    ]

    vocab_bundle = build_vocabulary_bundle(plan)
    grammar_bundle = await build_grammar_bundle_async(plan, sentences=sentences_data)
    translation_bundle = build_translation_bundle(plan)

    logger.info(
        "Grammar strategy: mode=%s, examples=%d, rag_debug=%s",
        grammar_bundle.example_strategy.selection_mode,
        len(grammar_bundle.example_strategy.examples),
        "yes" if grammar_bundle.rag_debug else "no",
    )

    vocab_deps = VocabularyAgentDeps(
        sentences=sentences_data,
        prompt_strategy=vocab_bundle.prompt_strategy,
        examples=vocab_bundle.example_strategy.examples,
    )
    grammar_deps = GrammarAgentDeps(
        sentences=sentences_data,
        prompt_strategy=grammar_bundle.prompt_strategy,
        examples=grammar_bundle.example_strategy.examples,
    )
    translation_deps = TranslationAgentDeps(
        sentences=sentences_data,
        prompt_strategy=translation_bundle.prompt_strategy,
        examples=translation_bundle.example_strategy.examples,
    )

    vocab_meta = _build_agent_trace_metadata(state, "vocabulary_agent", model_selection)
    grammar_meta = _build_agent_trace_metadata(state, "grammar_agent", model_selection)
    translation_meta = _build_agent_trace_metadata(state, "translation_agent", model_selection)

    vocab_task = _run_vocabulary_llm_span(
        deps=vocab_deps, metadata=vocab_meta, model_selection=model_selection
    )
    grammar_task = _run_grammar_llm_span(
        deps=grammar_deps, metadata=grammar_meta, model_selection=model_selection
    )
    translation_task = _run_translation_llm_span(
        deps=translation_deps, metadata=translation_meta, model_selection=model_selection
    )

    results = await asyncio.gather(vocab_task, grammar_task, translation_task, return_exceptions=True)

    vocab_result = results[0] if not isinstance(results[0], Exception) else None
    grammar_result = results[1] if not isinstance(results[1], Exception) else None
    translation_result = results[2] if not isinstance(results[2], Exception) else None

    errors: list[Warning] = []
    if isinstance(results[0], Exception):
        logger.exception("vocabulary_agent 调用失败")
        errors.append(Warning(code="VOCABULARY_AGENT_FAILED", level="error", message=f"vocabulary agent 调用失败: {results[0]}"))
    if isinstance(results[1], Exception):
        logger.exception("grammar_agent 调用失败")
        errors.append(Warning(code="GRAMMAR_AGENT_FAILED", level="error", message=f"grammar agent 调用失败: {results[1]}"))
    if isinstance(results[2], Exception):
        logger.exception("translation_agent 调用失败")
        errors.append(Warning(code="TRANSLATION_AGENT_FAILED", level="error", message=f"translation agent 调用失败: {results[2]}"))

    vocabulary_output = vocab_result.get("output") if vocab_result else None
    grammar_output = grammar_result.get("output") if grammar_result else None
    translation_output = translation_result.get("output") if translation_result else None
    vocabulary_usage = (
        vocab_result.get("usage_metadata") or vocab_result.get("usage")
        if vocab_result else None
    )
    grammar_usage = (
        grammar_result.get("usage_metadata") or grammar_result.get("usage")
        if grammar_result else None
    )
    translation_usage = (
        translation_result.get("usage_metadata") or translation_result.get("usage")
        if translation_result else None
    )
    usage_summary = _aggregate_usage_summary({"vocabulary": vocabulary_usage, "grammar": grammar_usage, "translation": translation_usage})

    return {
        "vocabulary_draft": vocabulary_output,
        "grammar_draft": grammar_output,
        "translation_draft": translation_output,
        "vocabulary_usage": vocabulary_usage,
        "grammar_usage": grammar_usage,
        "translation_usage": translation_usage,
        "usage_summary": usage_summary,
        "agent_errors": errors,
    }


async def parallel_agents_node(state: AnalyzeState, config: RunnableConfig) -> AnalyzeState:
    """Parallel agents node."""
    model_selection = _model_selection(config)
    result = await _run_parallel_agents(state, model_selection)
    errors = result.get("agent_errors", [])

    return {
        "vocabulary_draft": result.get("vocabulary_draft"),
        "grammar_draft": result.get("grammar_draft"),
        "translation_draft": result.get("translation_draft"),
        "vocabulary_usage": result.get("vocabulary_usage"),
        "grammar_usage": result.get("grammar_usage"),
        "translation_usage": result.get("translation_usage"),
        "usage_summary": result.get("usage_summary"),
        "warnings": [*state.get("warnings", []), *errors],
    }


async def normalize_and_ground_node(state: AnalyzeState) -> AnalyzeState:
    """Normalize and ground node。"""
    prepared_input = state["prepared_input"]
    vocabulary_draft = state.get("vocabulary_draft")
    grammar_draft = state.get("grammar_draft")
    translation_draft = state.get("translation_draft")

    partial_warnings: list[Warning] = []
    if vocabulary_draft is None:
        vocabulary_draft = VocabularyDraft()
        partial_warnings.append(Warning(code="VOCABULARY_AGENT_FAILED", level="error", message="vocabulary agent 未返回有效结果，词汇标注已降级为空"))
    if grammar_draft is None:
        grammar_draft = GrammarDraft()
        partial_warnings.append(Warning(code="GRAMMAR_AGENT_FAILED", level="error", message="grammar agent 未返回有效结果，语法标注已降级为空"))
    if translation_draft is None:
        translation_draft = TranslationDraft(title="（翻译不可用）", sentence_translations=[])
        partial_warnings.append(Warning(code="TRANSLATION_AGENT_FAILED", level="error", message="translation agent 未返回有效结果，翻译已降级为空"))

    sentences = [PreparedSentence.model_validate(s) if not isinstance(s, PreparedSentence) else s for s in prepared_input.sentences]
    validation_warnings = validate_all_drafts(vocabulary_draft, grammar_draft, translation_draft, sentences)
    draft_warnings = [Warning(code="DRAFT_VALIDATION", level="warning", message=msg) for msg in validation_warnings]

    normalized_result = normalize_and_ground(
        vocabulary_draft=vocabulary_draft,
        grammar_draft=grammar_draft,
        translation_draft=translation_draft,
        sentences=sentences,
        policy=state["goal_execution_plan"].policy,
    )

    return {
        "normalized_result": normalized_result,
        "drop_log": normalized_result.drop_log,
        "warnings": [*state.get("warnings", []), *partial_warnings, *draft_warnings],
    }


async def repair_agent_node(state: AnalyzeState, config: RunnableConfig) -> AnalyzeState:
    """Repair agent node（条件触发）。"""
    normalized_result = state.get("normalized_result")

    if normalized_result is not None and normalized_result.drop_log:
        quality_drops = [
            d for d in normalized_result.drop_log
            if d.drop_stage != "density_control"
        ]
        quality_drop_count = len(quality_drops)
        annotation_count = len(normalized_result.annotations)
        failure_ratio = quality_drop_count / (annotation_count + quality_drop_count) if annotation_count > 0 else 0.0
        if failure_ratio <= ANCHOR_FAILURE_THRESHOLD:
            return {"repair_request": None}
    elif normalized_result is not None:
        return {"repair_request": None}

    prepared_input = state["prepared_input"]
    vocabulary_draft = state.get("vocabulary_draft")
    grammar_draft = state.get("grammar_draft")
    translation_draft = state.get("translation_draft")

    if vocabulary_draft is None or grammar_draft is None or translation_draft is None:
        return {"repair_request": None}

    quality_drop_count = len([d for d in (normalized_result.drop_log or []) if d.drop_stage != "density_control"])
    total_drop_count = len(normalized_result.drop_log) if normalized_result else 0
    error_context = f"normalized_result 锚点失败率过高或结构异常。quality_drops: {quality_drop_count}, density_drops: {total_drop_count - quality_drop_count}"
    repair_deps = RepairAgentDeps(
        sentences=[{"sentence_id": s.sentence_id, "text": s.text} for s in prepared_input.sentences],
        original_drafts={
            "vocabulary_draft": vocabulary_draft.model_dump(mode="json") if vocabulary_draft else {},
            "grammar_draft": grammar_draft.model_dump(mode="json") if grammar_draft else {},
            "translation_draft": translation_draft.model_dump(mode="json") if translation_draft else {},
        },
    )
    repair_meta = _build_agent_trace_metadata(state, "repair_agent", _model_selection(config))
    repair_meta["extra"] = {**(repair_meta.get("extra") or {}), "error_context": error_context}

    try:
        repair_result = await _run_repair_llm_span(deps=repair_deps, metadata=repair_meta, error_context=error_context)
        repaired_result = repair_result.get("output")
        repair_usage = repair_result.get("usage_metadata")
        usage_summary = _aggregate_usage_summary({
            "vocabulary": state.get("vocabulary_usage"),
            "grammar": state.get("grammar_usage"),
            "translation": state.get("translation_usage"),
            "repair": repair_usage,
        })
        return {
            "repair_request": {"error_context": error_context, "repaired": True},
            "normalized_result": repaired_result,
            "drop_log": repaired_result.drop_log if repaired_result else state.get("drop_log", []),
            "repair_usage": repair_usage,
            "usage_summary": usage_summary,
        }
    except Exception:
        logger.exception("repair_agent 调用失败")
        usage_summary = _aggregate_usage_summary({
            "vocabulary": state.get("vocabulary_usage"),
            "grammar": state.get("grammar_usage"),
            "translation": state.get("translation_usage"),
        })
        return {
            "repair_request": {"error_context": error_context, "repaired": False},
            "warnings": [
                *state.get("warnings", []),
                Warning(code="REPAIR_AGENT_FAILED", level="warning", message="repair agent 调用失败，继续使用归一化结果"),
            ],
            "usage_summary": usage_summary,
        }


@traceable(name="repair_llm_call", run_type="llm")
async def _run_repair_llm_span(
    *,
    deps: RepairAgentDeps,
    metadata: dict[str, object],
    error_context: str,
) -> dict[str, Any]:
    from app.agents.repair_agent import build_repair_prompt, get_repair_agent
    from app.llm.agent_runner import run_agent_with_route
    from app.llm.routes import MODEL_ROUTE_ANNOTATION_GENERATION

    result = await run_agent_with_route(
        agent=get_repair_agent(),
        prompt=build_repair_prompt(deps, error_context),
        deps=deps,
        route=MODEL_ROUTE_ANNOTATION_GENERATION,
        model_selection=None,
    )
    usage = extract_run_usage(result)
    return {"output": result.output if hasattr(result, "output") else result, "usage_metadata": usage}


async def project_render_scene_node(state: AnalyzeState) -> AnalyzeState:
    """Project to render scene node。"""
    payload = state["payload"]
    prepared_input = state["prepared_input"]
    normalized_result = state.get("normalized_result")
    plan = state.get("goal_execution_plan")

    if normalized_result is None:
        return {"render_scene": _empty_result(request_id=payload.request_id or "", payload=payload, profile_id=plan.prompt_profile if plan else "unresolved")}

    from app.schemas.internal.analysis import AnnotationOutput
    annotation_output = AnnotationOutput(annotations=normalized_result.annotations, sentence_translations=normalized_result.sentence_translations)
    projection_outcome = project_to_render_scene(
        annotation_output=annotation_output,
        prepared_input=prepared_input,
        source_type=payload.source_type,
        reading_goal=payload.reading_goal,
        reading_variant=payload.reading_variant,
        profile_id=plan.prompt_profile if plan else "unknown",
        request_id=payload.request_id or "",
    )

    return {
        "render_scene": projection_outcome.result,
        "warnings": [*state.get("warnings", []), *[Warning(**w) for w in projection_outcome.warnings]],
    }


async def assemble_result_node(state: AnalyzeState) -> AnalyzeState:
    """Assemble result node。"""
    render_scene = state.get("render_scene")

    if render_scene is None:
        payload = state["payload"]
        plan = state.get("goal_execution_plan")
        return {"render_scene": _empty_result(request_id=payload.request_id or "", payload=payload, profile_id=plan.prompt_profile if plan else "unresolved")}

    existing_warnings = state.get("warnings", [])
    if existing_warnings and hasattr(render_scene, "warnings"):
        seen_keys = {(w.code, w.sentence_id) for w in render_scene.warnings}
        for w in existing_warnings:
            if (w.code, w.sentence_id) not in seen_keys:
                render_scene.warnings.append(w)
                seen_keys.add((w.code, w.sentence_id))

    heavy_failure_codes = {"VOCABULARY_AGENT_FAILED", "GRAMMAR_AGENT_FAILED", "TRANSLATION_AGENT_FAILED", "NORMALIZE_AND_GROUND_FAILED"}
    has_heavy_failure = any(w.code in heavy_failure_codes for w in render_scene.warnings)
    has_no_entries = len(render_scene.sentence_entries) == 0 and len(render_scene.inline_marks) == 0
    informational_codes = {"LOW_ENGLISH_RATIO", "HIGH_NOISE_RATIO", "UNSUPPORTED_TEXT_TYPE", "DRAFT_VALIDATION"}
    has_informational_only = len(render_scene.warnings) > 0 and all(w.code in informational_codes for w in render_scene.warnings)

    if has_heavy_failure and has_no_entries:
        render_scene.user_facing_state = "degraded_heavy"
    elif len(render_scene.warnings) > 0 and not has_informational_only:
        render_scene.user_facing_state = "degraded_light"
    else:
        render_scene.user_facing_state = "normal"

    return {"render_scene": render_scene}
