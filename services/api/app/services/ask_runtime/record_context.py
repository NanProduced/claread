from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal, cast
from uuid import UUID

from app.database import connection as db_connect
from app.llm.agent_runner import extract_run_usage
from app.agents.grammar_agent import GrammarAgentDeps
from app.schemas.internal.analysis import ReadingGoal, ReadingVariant
from app.schemas.internal.drafts import draft_to_annotation
from app.schemas.reader_ask import (
    ReaderAskAnchorRef,
    ReaderAskCurrentRecordContext,
    ReaderAskEntryAction,
    ReaderAskPageIdentity,
    ReaderAskReadingRecordAnchor,
    ReaderAskResolvedContextInput,
)
from app.services.analysis.planning.goal_planner import build_goal_execution_plan
from app.services.analysis.postprocess.projection import (
    _format_grammar_note_content,
    _format_sentence_analysis_content,
)
from app.services.analysis.prompting.strategy_builder import build_grammar_bundle_async
from app.services.analysis.runtime.runners import run_grammar_agent
from app.services.analysis.validators import validate_grammar_note, validate_sentence_analysis
from app.services.reader_ask import planner
from app.services.reader_ask import planner_runtime as planner_runtime_svc
from app.services.reader_orchestration.anchor_gate import (
    ValidatedReadingRecordAnchor,
    load_validated_reading_record_anchor,
)
from app.services.reader_orchestration.repository import (
    LoadedReaderSnapshotFacts,
    ReaderOrchestrationRepository,
)


@dataclass(slots=True)
class ReadingRecordRuntimeBundle:
    record_id: UUID
    title: str | None
    source_text: str
    render_scene: dict[str, Any]
    page_state_json: dict[str, Any]
    workflow_version: str | None
    schema_version: str | None


@dataclass(slots=True)
class ReadingRecordAskContext:
    record: ReadingRecordRuntimeBundle
    facts: LoadedReaderSnapshotFacts
    reading_record_anchor: ReaderAskReadingRecordAnchor | None
    validated_anchor: ValidatedReadingRecordAnchor | None
    legacy_anchor: ReaderAskAnchorRef | None
    page_identity: ReaderAskPageIdentity
    resolved_context_input: ReaderAskResolvedContextInput


def _sentence_text_map(facts: LoadedReaderSnapshotFacts) -> dict[str, str]:
    sentence_texts: dict[str, list[tuple[int, str]]] = {}
    for segment in facts.build_result.anchor_segments:
        sentence_texts.setdefault(segment.sentence_id, []).append((segment.order_index, segment.text))
    return {
        sentence_id: "".join(text for _, text in sorted(parts, key=lambda item: item[0])).strip()
        for sentence_id, parts in sentence_texts.items()
    }


def _sentence_rows(facts: LoadedReaderSnapshotFacts) -> list[dict[str, Any]]:
    sentence_lookup = _sentence_text_map(facts)
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for segment in facts.build_result.anchor_segments:
        if segment.sentence_id in seen:
            continue
        seen.add(segment.sentence_id)
        rows.append(
            {
                "sentence_id": segment.sentence_id,
                "paragraph_id": segment.paragraph_id,
                "text": sentence_lookup.get(segment.sentence_id) or segment.text,
                "order_index": segment.order_index,
            }
        )
    return rows


def _resolve_overview(facts: LoadedReaderSnapshotFacts) -> dict[str, str | None]:
    for layer in facts.enhancement_layers:
        payload = layer.output if isinstance(layer.output, dict) else {}
        for key in ("article_overview", "overview", "summary", "summary_zh"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return {
                    "status": "ready",
                    "overview": value.strip(),
                    "source": f"{layer.layer_type}:{layer.target_scope}",
                    "confidence": None,
                }
    return {
        "status": None,
        "overview": None,
        "source": None,
        "confidence": None,
    }


def _synthetic_legacy_anchor(
    validated_anchor: ValidatedReadingRecordAnchor,
    reading_record_anchor: ReaderAskReadingRecordAnchor,
) -> ReaderAskAnchorRef:
    return ReaderAskAnchorRef(
        anchor_type="text_range",
        sentence_id=validated_anchor.anchor_segment.sentence_id,
        paragraph_id=validated_anchor.anchor_segment.paragraph_id,
        selected_text=reading_record_anchor.selected_text,
        start_offset=reading_record_anchor.start_offset,
        end_offset=reading_record_anchor.end_offset,
        text_hash=reading_record_anchor.text_hash,
        label=reading_record_anchor.selected_text,
        payload_json={
            "reading_record_id": reading_record_anchor.record_id,
            "base_id": reading_record_anchor.base_id,
            "generation": reading_record_anchor.generation,
            "unit_id": reading_record_anchor.unit_id,
            "anchor_segment_id": reading_record_anchor.anchor_segment_id,
        },
    )


def _build_page_identity(
    *,
    facts: LoadedReaderSnapshotFacts,
    record_id: UUID,
    has_article_overview: bool,
) -> ReaderAskPageIdentity:
    return ReaderAskPageIdentity(
        record_id=str(record_id),
        title=facts.record.title,
        available_context_capabilities=["snapshot_facts", "reading_record"],
        has_article_overview=has_article_overview,
        has_sentence_entries=bool(facts.build_result.anchor_segments),
        has_annotations=any(asset.asset_type == "highlight" for asset in facts.user_assets),
        has_reader_notes=any(asset.asset_type == "note" for asset in facts.user_assets),
    )


def _build_resolved_context_input(
    *,
    page_identity: ReaderAskPageIdentity,
    entry_action: ReaderAskEntryAction,
    legacy_anchor: ReaderAskAnchorRef | None,
    reading_record_anchor: ReaderAskReadingRecordAnchor | None,
    facts: LoadedReaderSnapshotFacts,
) -> ReaderAskResolvedContextInput:
    overview = _resolve_overview(facts)
    current_record_context = ReaderAskCurrentRecordContext(
        record_id=page_identity.record_id,
        record_title=facts.record.title,
        local_context=(
            {"reading_record_anchor": reading_record_anchor.model_dump(mode="json")}
            if reading_record_anchor is not None
            else None
        ),
        record_insights=[],
        article_overview=cast(str | None, overview["overview"]),
        article_overview_status=cast(str | None, overview["status"]),
        article_overview_source=cast(str | None, overview["source"]),
        article_overview_confidence=cast(str | None, overview["confidence"]),
        source_labels=["article_overview"] if overview["overview"] else [],
    )
    return planner.build_resolved_context_input(
        page_identity=page_identity,
        entry_action=entry_action,
        attachments=[],
        anchors=[legacy_anchor] if legacy_anchor is not None else [],
        current_record_context=current_record_context,
        external_record_contexts=[],
        external_asset_contexts=[],
    )


async def build_reading_record_context(
    *,
    user_id: UUID,
    reading_record_id: UUID,
    request_anchor: ReaderAskReadingRecordAnchor | None,
    entry_action: ReaderAskEntryAction,
    repository: ReaderOrchestrationRepository | None = None,
) -> ReadingRecordAskContext:
    repo = repository or ReaderOrchestrationRepository()
    async with db_connect.acquire_connection() as conn:
        facts = await repo.load_snapshot_facts(
            conn,
            record_id=reading_record_id,
            user_id=user_id,
        )
        validated_anchor: ValidatedReadingRecordAnchor | None = None
        legacy_anchor: ReaderAskAnchorRef | None = None
        if request_anchor is not None:
            validated_anchor = await load_validated_reading_record_anchor(
                conn,
                repository=repo,
                user_id=user_id,
                anchor=request_anchor,
            )
            legacy_anchor = _synthetic_legacy_anchor(validated_anchor, request_anchor)

    overview = _resolve_overview(facts)
    page_identity = _build_page_identity(
        facts=facts,
        record_id=reading_record_id,
        has_article_overview=bool(overview["overview"]),
    )
    resolved_context_input = _build_resolved_context_input(
        page_identity=page_identity,
        entry_action=entry_action,
        legacy_anchor=legacy_anchor,
        reading_record_anchor=request_anchor,
        facts=facts,
    )
    bundle = ReadingRecordRuntimeBundle(
        record_id=reading_record_id,
        title=facts.record.title,
        source_text=facts.build_result.base.text,
        render_scene={},
        page_state_json={},
        workflow_version="reader-record-ask-v1",
        schema_version="reader-record-ask-v1",
    )
    return ReadingRecordAskContext(
        record=bundle,
        facts=facts,
        reading_record_anchor=request_anchor,
        validated_anchor=validated_anchor,
        legacy_anchor=legacy_anchor,
        page_identity=page_identity,
        resolved_context_input=resolved_context_input,
    )


def build_record_context_payload(
    context: ReadingRecordAskContext,
    *,
    scope: str,
    target_sentence_id: str | None,
) -> dict[str, Any]:
    rows = _sentence_rows(context.facts)
    sentence_lookup = {row["sentence_id"]: row for row in rows}
    active_sentence_id = target_sentence_id or context.legacy_anchor.sentence_id if context.legacy_anchor else target_sentence_id
    active_anchor = None
    if active_sentence_id and active_sentence_id in sentence_lookup:
        target = sentence_lookup[active_sentence_id]
        active_anchor = {
            "sentence_id": target["sentence_id"],
            "paragraph_id": target["paragraph_id"],
            "text": target["text"][:240],
        }

    truncated = False
    if scope == "full":
        article_text = context.record.source_text
        if len(article_text) > 10000:
            article_text = article_text[:10000]
            truncated = True
        sentence_window = [
            {
                "sentence_id": None,
                "paragraph_id": None,
                "text": article_text,
                "is_active_anchor": False,
            }
        ]
    elif scope == "paragraph" and active_sentence_id and active_sentence_id in sentence_lookup:
        paragraph_id = sentence_lookup[active_sentence_id]["paragraph_id"]
        sentence_window = [
            {
                "sentence_id": row["sentence_id"],
                "paragraph_id": row["paragraph_id"],
                "text": row["text"],
                "is_active_anchor": row["sentence_id"] == active_sentence_id,
            }
            for row in rows
            if row["paragraph_id"] == paragraph_id
        ]
    else:
        target_index = 0
        if active_sentence_id:
            for index, row in enumerate(rows):
                if row["sentence_id"] == active_sentence_id:
                    target_index = index
                    break
        sentence_window = [
            {
                "sentence_id": row["sentence_id"],
                "paragraph_id": row["paragraph_id"],
                "text": row["text"],
                "is_active_anchor": row["sentence_id"] == active_sentence_id,
            }
            for row in rows[max(target_index - 2, 0):min(target_index + 3, len(rows))]
        ]

    return {
        "record_id": str(context.record.record_id),
        "record_title": context.record.title,
        "active_anchor": active_anchor,
        "sentence_window": sentence_window,
        "can_load_more": scope,
        "scope": scope,
        "target_sentence_id": active_sentence_id,
        "truncated": truncated,
    }


def collect_record_insights(
    context: ReadingRecordAskContext,
    *,
    target_sentence_id: str | None,
    kind: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for asset in context.facts.user_assets:
        asset_sentence_id = None
        if hasattr(asset.anchor, "anchor_segment_id"):
            asset_sentence_id = getattr(asset.anchor, "anchor_segment_id", None)
        if target_sentence_id and asset_sentence_id not in {target_sentence_id, None}:
            continue
        if kind == "vocabulary":
            continue
        items.append(
            {
                "insight_id": asset.asset_id,
                "sentence_id": asset_sentence_id,
                "kind": asset.asset_type,
                "title": "用户笔记" if asset.asset_type == "note" else "用户高亮",
                "content_md": asset.note_text or getattr(asset.anchor, "selected_text", "") or "",
                "translation_zh": None,
                "source": "user_asset",
                "confidence": None,
                "created_at": None,
            }
        )
        if len(items) >= limit:
            return items
    return items


def _reading_goal_from_snapshot(context: ReadingRecordAskContext) -> ReadingGoal:
    goal = context.facts.record.source_metadata.get("reading_goal")
    if goal in {"exam", "daily_reading", "academic"}:
        return cast(ReadingGoal, goal)
    return "daily_reading"


def _reading_variant_from_snapshot(
    context: ReadingRecordAskContext,
    reading_goal: ReadingGoal,
) -> ReadingVariant:
    variant = context.facts.record.source_metadata.get("reading_variant")
    if variant in {
        "gaokao",
        "cet",
        "kaoyan",
        "tem",
        "ielts_toefl",
        "beginner_reading",
        "intermediate_reading",
        "intensive_reading",
        "academic_general",
    }:
        return cast(ReadingVariant, variant)
    return "academic_general" if reading_goal == "academic" else "intermediate_reading"


def _focus_guidance_from_anchor(
    anchor: ReaderAskAnchorRef | None,
    sentence_text: str,
) -> dict[str, object] | None:
    if anchor is None:
        return None
    focus_text = (anchor.selected_text or sentence_text or "").strip()
    if not focus_text:
        return None
    guidance: dict[str, object] = {
        "focus_text": focus_text,
        "selection_mode": "text_range",
        "sentence_id": anchor.sentence_id or "",
        "analysis_scope_hint": "focus_span",
    }
    if anchor.start_offset is not None and anchor.end_offset is not None:
        guidance["start_offset"] = anchor.start_offset
        guidance["end_offset"] = anchor.end_offset
    return guidance


def _textual_overlap(left: str, right: str) -> bool:
    left_normalized = re.sub(r"\s+", " ", left).strip().lower()
    right_normalized = re.sub(r"\s+", " ", right).strip().lower()
    if not left_normalized or not right_normalized:
        return False
    if left_normalized in right_normalized or right_normalized in left_normalized:
        return True
    left_tokens = {token for token in re.split(r"\W+", left_normalized) if token}
    right_tokens = {token for token in re.split(r"\W+", right_normalized) if token}
    return bool(left_tokens and right_tokens and left_tokens.intersection(right_tokens))


async def generate_sentence_annotation(
    context: ReadingRecordAskContext,
    *,
    kind: Literal["grammar_note", "sentence_analysis"],
) -> dict[str, Any] | None:
    anchor = context.legacy_anchor
    sentence_id = anchor.sentence_id if anchor is not None else None
    sentence_text = _sentence_text_map(context.facts).get(sentence_id or "")
    if not sentence_id or not sentence_text:
        return None
    focus_text = (anchor.selected_text or sentence_text).strip()
    focus_guidance = _focus_guidance_from_anchor(anchor, sentence_text)

    reading_goal = _reading_goal_from_snapshot(context)
    reading_variant = _reading_variant_from_snapshot(context, reading_goal)
    plan = build_goal_execution_plan(reading_goal, reading_variant)
    sentences = [{"sentence_id": sentence_id, "text": sentence_text}]
    grammar_bundle = await build_grammar_bundle_async(plan, sentences=sentences)
    result = await run_grammar_agent(
        GrammarAgentDeps(
            sentences=sentences,
            prompt_strategy=grammar_bundle.prompt_strategy,
            examples=grammar_bundle.example_strategy.examples,
            focus_guidance=focus_guidance,
        )
    )
    usage_summary = extract_run_usage(result)
    draft = result.output if hasattr(result, "output") else result
    sentence_map_payload = {sentence_id: sentence_text}

    if kind == "grammar_note":
        chosen_note = None
        for draft_note in draft.grammar_notes:
            note = draft_to_annotation(draft_note)
            validation = validate_grammar_note(note, sentence_map_payload)
            if not validation.is_valid:
                continue
            if anchor is not None and not any(
                _textual_overlap(str(span.text), focus_text) for span in note.spans
            ):
                continue
            chosen_note = note
            break
        if chosen_note is None:
            result_payload = planner_runtime_svc.quick_action_not_applicable(
                kind=kind,
                sentence_id=sentence_id,
                sentence_text=sentence_text,
                focus_text=focus_text,
                reason="当前片段没有稳定到值得单独讲解的语法点。",
                suggestion="可以改为选中更完整的从句或整句，再做语法解析。",
            )
            result_payload["usage_summary"] = usage_summary
            return result_payload
        return {
            "status": "ready",
            "kind": "grammar_note",
            "sentence_id": chosen_note.sentence_id,
            "label": chosen_note.label,
            "content": _format_grammar_note_content(chosen_note),
            "note_zh": chosen_note.note_zh,
            "source_sentence": sentence_text,
            "annotation": chosen_note.model_dump(mode="json"),
            "focus_text": focus_text,
            "analysis_scope": "focus_span",
            "spans": [span.model_dump(mode="json") for span in chosen_note.spans],
            "usage_summary": usage_summary,
        }

    for draft_analysis in draft.sentence_analyses:
        analysis = draft_to_annotation(draft_analysis)
        validation = validate_sentence_analysis(analysis, sentence_map_payload)
        if validation.is_valid:
            return {
                "status": "ready",
                "kind": "sentence_analysis",
                "sentence_id": analysis.sentence_id,
                "label": analysis.label,
                "content": _format_sentence_analysis_content(analysis),
                "source_sentence": sentence_text,
                "focus_text": focus_text,
                "analysis_scope": "full_sentence",
                "chunks": [chunk.model_dump(mode="json") for chunk in analysis.chunks or []],
                "analysis_zh": analysis.analysis_zh,
                "annotation": analysis.model_dump(mode="json"),
                "usage_summary": usage_summary,
            }
    result_payload = planner_runtime_svc.quick_action_not_applicable(
        kind=kind,
        sentence_id=sentence_id,
        sentence_text=sentence_text,
        focus_text=focus_text,
        reason="当前句子没有稳定到值得单独拆分的结构层次。",
        suggestion="可以改问这句话在段落中的作用，或换一条更复杂的句子再拆解。",
    )
    result_payload["usage_summary"] = usage_summary
    return result_payload
