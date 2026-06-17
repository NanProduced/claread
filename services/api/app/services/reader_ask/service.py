from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from fastapi import HTTPException

from app.agents.reader_ask_agent import (
    ReaderAskRuntimeActionRequest,
    ReaderAskRuntimeState,
)
from app.services.reader_ask.agent_deps_factory import build_reader_ask_agent_deps
from app.services.reader_ask.agent_invocation import (
    AgentStreamRuntime,
    ReaderAskStreamCompleted,
    ReaderAskStreamSseEvent,
    resolve_reader_ask_agent,
    run_reader_ask_replan,
    stream_reader_ask_agent_run,
)
from app.services.reader_ask.agent_runner import is_degenerate_answer
from app.config.settings import get_settings
from app.database import connection as db_connection
from app.llm.agent_runner import extract_run_usage
from app.llm.types import RunModelSettings
from app.agents.grammar_agent import GrammarAgentDeps
from app.schemas.reader_ask import (
    ReaderAskActionConfirmRequest,
    ReaderAskActionConfirmResponse,
    ReaderAskActionConfirmResult,
    ReaderAskActionProposal,
    ReaderAskAttachment,
    ReaderAskAttachmentPayload,
    ReaderAskAnchorRef,
    ReaderAskCitation,
    ReaderAskCompletedPayload,
    ReaderAskContextRecordSearchResponse,
    ReaderAskContextPlan,
    ReaderAskCurrentRecordAffordances,
    ReaderAskDeleteSupplementResponse,
    ReaderAskAssetDisambiguation,
    ReaderAskDisambiguation,
    ReaderAskEvidenceItem,
    ReaderAskEntryAction,
    ReaderAskGrammarNoteCard,
    ReaderAskGrammarNoteCardSpan,
    ReaderAskMessage,
    ReaderAskMessageStreamRequest,
    ReaderAskModelOptionListResponse,
    ReaderAskModelOptionSummary,
    ReaderAskPageIdentity,
    ReaderAskPersistedSupplement,
    ReaderAskResolvedContextInput,
    ReaderAskResolvedIntent,
    ReaderAskResolvedContextSummary,
    ReaderAskResponseCard,
    ReaderAskRunInfo,
    ReaderAskSentenceBreakdownCard,
    ReaderAskSentenceBreakdownPart,
    ReaderAskSelectedModel,
    ReaderAskSubmissionMode,
    ReaderAskSupplementCandidate,
    ReaderAskThreadCreateRequest,
    ReaderAskThreadDetail,
    ReaderAskThreadListResponse,
    ReaderAskThreadSummary,
    ReaderAskTaskMode,
    ReaderAskTraceSummary,
    ReaderAskToolTraceEntry,
    ReaderAskUserVisibleOutput,
)
from app.schemas.internal.analysis import ReadingGoal, ReadingVariant
from app.schemas.internal.drafts import draft_to_annotation
from app.services.analysis.planning.goal_planner import build_goal_execution_plan
from app.services.analysis.postprocess.projection import (
    _format_grammar_note_content,
    _format_sentence_analysis_content,
)
from app.services.analysis.prompting.strategy_builder import build_grammar_bundle_async
from app.services.analysis.runtime.runners import run_grammar_agent
from app.services.analysis.validators import validate_grammar_note, validate_sentence_analysis
from app.schemas.reader_notes import ReaderNoteCreateRequest
from app.schemas.user_annotations import UserAnnotationCreateRequest, UserAnnotationSegment
from app.services.ai_usage import (
    AIUsageEventCreate,
    BILLING_MODE_USER_POINTS,
    CAPABILITY_READER_ASK,
    STATUS_FAILED,
    STATUS_SUCCEEDED,
    USAGE_SCOPE_USER_BILLED,
    build_model_metadata,
    build_reader_ask_billing_metadata,
    compute_reader_ask_cost_points,
    record_ai_usage_event,
)
from app.services.analysis.credit_service import (
    CreditReservation,
    LEDGER_ENTRY_TYPE_AI_CAPABILITY_DEDUCT,
    check_quota,
    deduct_points,
    ensure_credit_account,
    refund_reserved_points,
    reserve_points,
)
from app.services.analysis.prompting.prompt_loader import get_prompt_version
from app.services.reader_ask import capabilities as capabilities_svc
from app.services.reader_ask import context_runtime as context_runtime_svc
from app.services.reader_ask import output_contract as output_contract_svc
from app.services.reader_ask import planner
from app.services.reader_ask import post_process as post_process_svc
from app.services.reader_ask import prompt_preparation as prompt_preparation_svc
from app.services.reader_ask import recovery as recovery_svc
from app.services.reader_ask import repository as repo
from app.services.reader_ask import runtime_contract as runtime_contract_svc
from app.services.reader_ask import stream_events as stream_events_svc
from app.services.reader_ask import supplements as supplements_svc
from app.services.reader_ask import planner_runtime as planner_runtime_svc
from app.services.reader_ask import config as cfg
from app.services.reader_ask import model_options as model_options_svc
from app.services.reader_ask import stream_checkpoint as stream_checkpoint_svc
from app.services.reader_ask import utils

logger = logging.getLogger(__name__)


from app.services.text_anchors import ensure_json_dict, sentence_map
from app.services.user_assets import vocabulary as vocabulary_svc
from app.services import reader_notes as reader_notes_svc
from app.services import user_annotations as user_annotations_svc


_WORKFLOW_NAME = "reader_ask"
_WORKFLOW_VERSION = "1.0.0"
_SCHEMA_VERSION = "reader-ask-v2"
_EVAL_TRACE_SCHEMA_VERSION = "reader-ask-eval-trace-v1"
_TASK_MODE_LABELS: dict[ReaderAskTaskMode, str] = {
    "explain": "讲解",
    "breakdown": "拆句",
    "vocabulary": "词义",
    "grammar": "语法",
    "practice": "练习",
    "general": "通用",
}


@dataclass(slots=True)
class _RecordBundle:
    record_id: UUID
    title: str | None
    source_text: str
    render_scene: dict[str, Any]
    page_state_json: dict[str, Any]
    workflow_version: str | None
    schema_version: str | None


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


def _normalize_text(value: str | None) -> str:
    return utils.normalize_text(value)


def _truncate_text(value: str | None, limit: int) -> str:
    return utils.truncate_text(value, limit)


def _truncate_history_message(value: str | None, *, role: str, limit: int) -> str:
    normalized = _normalize_text(value)
    if len(normalized) <= limit:
        return normalized
    if role != "assistant":
        return _truncate_text(normalized, limit)

    head_limit = max(limit // 2, 240)
    tail_limit = max(limit - head_limit - 5, 160)
    head = normalized[:head_limit].rstrip()
    tail = normalized[-tail_limit:].lstrip()
    return f"{head}\n...\n{tail}"


def _parse_uuid(value: str, detail: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=detail) from exc


def _anchor_to_citation(anchor: ReaderAskAnchorRef, *, record_id: str, record_title: str | None) -> ReaderAskCitation:
    label = anchor.label or anchor.selected_text or anchor.entry_type or anchor.anchor_type
    return ReaderAskCitation(
        citation_id=str(uuid4()),
        kind="anchor",
        label=_truncate_text(label, 80) or anchor.anchor_type,
        anchor_type=anchor.anchor_type,
        sentence_id=anchor.sentence_id,
        target_key=anchor.target_key,
        selected_text=_truncate_text(anchor.selected_text, 180) or None,
        record_id=record_id,
        source_article_title=record_title,
        metadata_json={"anchor_id": anchor.anchor_id, "entry_type": anchor.entry_type},
    )


def _sentence_ids_from_anchor(anchor: ReaderAskAnchorRef) -> list[str]:
    if anchor.anchor_type == "multi_text":
        return [segment.sentence_id for segment in anchor.segments]
    if anchor.sentence_id:
        return [anchor.sentence_id]
    return []


def _first_anchor_text(anchor: ReaderAskAnchorRef) -> str:
    if anchor.selected_text:
        return anchor.selected_text
    if anchor.segments:
        return " ... ".join(segment.selected_text for segment in anchor.segments[:3])
    return anchor.label or anchor.entry_type or anchor.anchor_type


def _attachment_payload_json(attachment: ReaderAskAttachment) -> dict[str, Any]:
    payload = attachment.anchor_payload.model_dump(mode="json") if attachment.anchor_payload is not None else None
    return {
        "attachment_kind": attachment.kind,
        "attachment_subtype": attachment.subtype,
        "entry_action": attachment.metadata.entry_action,
        "source_surface": attachment.metadata.source_surface,
        "attachment_metadata": attachment.metadata.model_dump(mode="json"),
        "anchor_payload": payload,
    }


def _anchor_ref_from_attachment_payload(payload: ReaderAskAttachmentPayload) -> ReaderAskAnchorRef:
    return ReaderAskAnchorRef(
        anchor_type=payload.anchor_type,
        target_key=payload.target_key,
        sentence_id=payload.sentence_id,
        paragraph_id=payload.paragraph_id,
        selected_text=payload.selected_text,
        start_offset=payload.start_offset,
        end_offset=payload.end_offset,
        text_hash=payload.text_hash,
        segments=[segment.model_copy() for segment in payload.segments],
        payload_json={"anchor_payload": payload.model_dump(mode="json")},
    )


def _attachment_to_anchor(attachment: ReaderAskAttachment) -> ReaderAskAnchorRef | None:
    payload_json = _attachment_payload_json(attachment)
    payload = attachment.anchor_payload

    if attachment.kind == "record_ref":
        return None

    if attachment.kind == "text_selection":
        if payload is None:
            raise HTTPException(status_code=400, detail="text_selection attachments require anchor_payload")
        anchor = _anchor_ref_from_attachment_payload(payload)
        anchor.payload_json = payload_json
        anchor.label = attachment.label
        return anchor

    if attachment.kind == "annotation_ref":
        if payload is None:
            raise HTTPException(status_code=400, detail="annotation_ref attachments require anchor_payload")
        anchor = _anchor_ref_from_attachment_payload(payload)
        anchor.anchor_type = "reader_note" if attachment.subtype == "reader_note" else "user_annotation"
        anchor.anchor_id = attachment.metadata.asset_id
        anchor.note = attachment.metadata.note
        anchor.label = attachment.label
        anchor.payload_json = payload_json
        return anchor

    if attachment.kind in {"analysis_ref", "supplement_ref"} and attachment.subtype == "sentence":
        if payload is None:
            raise HTTPException(status_code=400, detail="sentence analysis attachments require anchor_payload")
        anchor = _anchor_ref_from_attachment_payload(payload)
        anchor.label = attachment.label
        anchor.payload_json = payload_json
        return anchor

    return ReaderAskAnchorRef(
        anchor_type="sentence_entry",
        target_key=attachment.target_key,
        sentence_id=attachment.metadata.sentence_id or (payload.sentence_id if payload else None),
        paragraph_id=attachment.metadata.paragraph_id or (payload.paragraph_id if payload else None),
        entry_type=attachment.metadata.entry_type or attachment.subtype,
        label=attachment.label,
        selected_text=attachment.selected_text,
        query=attachment.metadata.lookup_text or attachment.metadata.query,
        payload_json=payload_json,
    )


def _attachments_to_anchor_refs(attachments: list[ReaderAskAttachment]) -> list[ReaderAskAnchorRef]:
    resolved: list[ReaderAskAnchorRef] = []
    for attachment in attachments:
        anchor = _attachment_to_anchor(attachment)
        if anchor is not None:
            resolved.append(anchor)
    return resolved


def _query_seed(content: str, anchors: list[ReaderAskAnchorRef]) -> str:
    for anchor in anchors:
        selected = _first_anchor_text(anchor)
        if selected:
            return selected
    return _truncate_text(content, 80)


def _make_tool_trace(tool_name: str, status: str, *, summary: str | None = None, metadata: dict[str, Any] | None = None) -> ReaderAskToolTraceEntry:
    now = _iso_now()
    if status == "started":
        return ReaderAskToolTraceEntry(
            tool_name=tool_name,
            status="started",
            started_at=now,
            metadata_json=metadata or {},
        )
    return ReaderAskToolTraceEntry(
        tool_name=tool_name,
        status=status,  # type: ignore[arg-type]
        started_at=now,
        completed_at=now,
        summary=summary,
        metadata_json=metadata or {},
    )


def _build_reference_reranker() -> Any:
    """Build reference reranker based on config. Returns None by default."""
    from app.services.reader_ask.known_reference_resolver import build_reference_reranker
    return build_reference_reranker(enabled=cfg.REFERENCE_RERANKER_ENABLED)


async def _load_record_bundle(user_id: UUID, record_id: UUID) -> _RecordBundle:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT r.id, r.title, r.source_text, a.render_scene_json, a.page_state_json, a.workflow_version, a.schema_version
            FROM analysis_records r
            LEFT JOIN analysis_results a ON a.record_id = r.id
            WHERE r.id = $1 AND r.user_id = $2 AND r.deleted_at IS NULL
            """,
            record_id,
            user_id,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="Analysis record not found")
    return _RecordBundle(
        record_id=row["id"],
        title=row["title"],
        source_text=row["source_text"] or "",
        render_scene=ensure_json_dict(row["render_scene_json"]),
        page_state_json=ensure_json_dict(row["page_state_json"]),
        workflow_version=row["workflow_version"],
        schema_version=row["schema_version"],
    )


def _render_scene_sentence_text(record: _RecordBundle, sentence_id: str | None) -> str | None:
    if not sentence_id:
        return None
    sentence = sentence_map(record.render_scene).get(sentence_id)
    text = sentence.get("text") if sentence else None
    return text if isinstance(text, str) and text.strip() else None


def _translations_map(record: _RecordBundle) -> dict[str, str]:
    translations: dict[str, str] = {}
    raw = record.render_scene.get("translations")
    if not isinstance(raw, list):
        return translations
    for item in raw:
        if not isinstance(item, dict):
            continue
        sentence_id = item.get("sentence_id") or item.get("sentenceId")
        translation = item.get("translation_zh") or item.get("translationZh")
        if isinstance(sentence_id, str) and isinstance(translation, str) and translation.strip():
            translations[sentence_id] = translation.strip()
    return translations



def _render_scene_has_sentence_entries(record: _RecordBundle) -> bool:
    entries = record.render_scene.get("sentence_entries") or record.render_scene.get("sentenceEntries")
    return isinstance(entries, list) and bool(entries)


def _current_record_affordances(
    *,
    record: _RecordBundle,
    page_identity: ReaderAskPageIdentity,
) -> ReaderAskCurrentRecordAffordances:
    return ReaderAskCurrentRecordAffordances(
        title=record.title or page_identity.title,
        available_context_capabilities=list(page_identity.available_context_capabilities),
        has_article_overview=context_runtime_svc.render_scene_article_overview(record) is not None,
        has_sentence_entries=_render_scene_has_sentence_entries(record),
        has_annotations=page_identity.has_annotations,
        has_reader_notes=page_identity.has_reader_notes,
    )


def _reading_goal_from_record(record: _RecordBundle) -> ReadingGoal:
    request = record.render_scene.get("request")
    goal = request.get("reading_goal") if isinstance(request, dict) else None
    if goal in {"exam", "daily_reading", "academic"}:
        return cast(ReadingGoal, goal)
    return "daily_reading"


def _reading_variant_from_record(record: _RecordBundle, reading_goal: ReadingGoal) -> ReadingVariant:
    request = record.render_scene.get("request")
    variant = request.get("reading_variant") if isinstance(request, dict) else None
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
    selection_mode = anchor.anchor_type if anchor.anchor_type in {"sentence", "text_range"} else "sentence"
    guidance: dict[str, object] = {
        "focus_text": focus_text,
        "selection_mode": selection_mode,
        "sentence_id": anchor.sentence_id or "",
        "analysis_scope_hint": "focus_span" if selection_mode == "text_range" else "full_sentence",
    }
    if selection_mode == "text_range" and anchor.start_offset is not None and anchor.end_offset is not None:
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


async def _generate_sentence_annotation(
    *,
    record: _RecordBundle,
    anchor: ReaderAskAnchorRef | None,
    kind: Literal["grammar_note", "sentence_analysis"],
) -> dict[str, Any] | None:
    sentence_id = anchor.sentence_id if anchor is not None else None
    sentence_text = _render_scene_sentence_text(record, sentence_id)
    if not sentence_id or not sentence_text:
        return None
    focus_text = (anchor.selected_text or sentence_text).strip()
    focus_guidance = _focus_guidance_from_anchor(anchor, sentence_text)
    selection_mode = anchor.anchor_type if anchor is not None else "sentence"

    if kind == "sentence_analysis" and selection_mode != "sentence":
        return planner_runtime_svc.quick_action_not_applicable(
            kind=kind,
            sentence_id=sentence_id,
            sentence_text=sentence_text,
            focus_text=focus_text,
            reason="当前片段不足以稳定拆出整句结构。",
            suggestion="建议先扩展到整句，再使用“句子拆分”。",
        )

    reading_goal = _reading_goal_from_record(record)
    reading_variant = _reading_variant_from_record(record, reading_goal)
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
            if selection_mode == "text_range":
                if not any(_textual_overlap(str(span.text), focus_text) for span in note.spans):
                    continue
            chosen_note = note
            break
        if chosen_note is None:
            result = planner_runtime_svc.quick_action_not_applicable(
                kind=kind,
                sentence_id=sentence_id,
                sentence_text=sentence_text,
                focus_text=focus_text,
                reason="当前片段没有稳定到值得单独讲解的语法点。",
                suggestion="可以改为选中更完整的从句或整句，再做语法解析。",
            )
            result["usage_summary"] = usage_summary
            return result
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
            "analysis_scope": "focus_span" if selection_mode == "text_range" else "full_sentence",
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
    result = planner_runtime_svc.quick_action_not_applicable(
        kind=kind,
        sentence_id=sentence_id,
        sentence_text=sentence_text,
        focus_text=focus_text,
        reason="当前句子没有稳定到值得单独拆分的结构层次。",
        suggestion="可以改问这句话在段落中的作用，或换一条更复杂的句子再拆解。",
    )
    result["usage_summary"] = usage_summary
    return result




async def _resolve_annotation_anchor(conn: Any, user_id: UUID, anchor: ReaderAskAnchorRef) -> ReaderAskAnchorRef:
    if not anchor.anchor_id and not anchor.target_key:
        return anchor


    row = await conn.fetchrow(
        """
        SELECT id, anchor_type, target_key, paragraph_id, sentence_id,
               selected_text, start_offset, end_offset, text_hash, color, payload_json
        FROM user_annotations
        WHERE user_id = $1
          AND deleted_at IS NULL
          AND (($2::uuid IS NOT NULL AND id = $2) OR ($3::text IS NOT NULL AND target_key = $3))
        LIMIT 1
        """,
        user_id,
        UUID(anchor.anchor_id) if anchor.anchor_id else None,
        anchor.target_key,
    )
    if row is None:
        return anchor

    payload = row["payload_json"] or {}
    segments = payload.get("segments") if isinstance(payload, dict) else []
    return anchor.model_copy(
        update={
            "anchor_id": str(row["id"]),
            "anchor_type": "user_annotation",
            "target_key": row["target_key"],
            "sentence_id": row["sentence_id"],
            "paragraph_id": row["paragraph_id"],
            "selected_text": row["selected_text"],
            "start_offset": row["start_offset"],
            "end_offset": row["end_offset"],
            "text_hash": row["text_hash"],
            "payload_json": payload,
            "segments": segments or [],
            "label": row["color"],
        }
    )


async def _resolve_reader_note_anchor(conn: Any, user_id: UUID, anchor: ReaderAskAnchorRef) -> ReaderAskAnchorRef:
    if not anchor.anchor_id and not anchor.target_key:
        return anchor

    row = await conn.fetchrow(
        """
        SELECT id, target_key, anchor_sentence_id, quote_mode, paragraph_id, sentence_id,
               selected_text, start_offset, end_offset, text_hash, note_text, payload_json
        FROM reader_notes
        WHERE user_id = $1
          AND deleted_at IS NULL
          AND (($2::uuid IS NOT NULL AND id = $2) OR ($3::text IS NOT NULL AND target_key = $3))
        LIMIT 1
        """,
        user_id,
        UUID(anchor.anchor_id) if anchor.anchor_id else None,
        anchor.target_key,
    )
    if row is None:
        return anchor

    payload = row["payload_json"] or {}
    segments = payload.get("segments") if isinstance(payload, dict) else []
    return anchor.model_copy(
        update={
            "anchor_id": str(row["id"]),
            "anchor_type": "reader_note",
            "target_key": row["target_key"],
            "sentence_id": row["sentence_id"] or row["anchor_sentence_id"],
            "paragraph_id": row["paragraph_id"],
            "selected_text": row["selected_text"],
            "start_offset": row["start_offset"],
            "end_offset": row["end_offset"],
            "text_hash": row["text_hash"],
            "note": row["note_text"],
            "payload_json": payload,
            "segments": segments or [],
            "label": row["quote_mode"],
        }
    )


def _resolve_sentence_entry_anchor(record: _RecordBundle, anchor: ReaderAskAnchorRef) -> ReaderAskAnchorRef:
    entries_raw = record.render_scene.get("sentence_entries") or record.render_scene.get("sentenceEntries")
    if not isinstance(entries_raw, list):
        return anchor
    for entry in entries_raw:
        if not isinstance(entry, dict):
            continue
        sentence_id = entry.get("sentence_id") or entry.get("sentenceId")
        entry_type = entry.get("entry_type") or entry.get("entryType")
        if sentence_id != anchor.sentence_id:
            continue
        if anchor.entry_type and entry_type != anchor.entry_type:
            continue
        return anchor.model_copy(
            update={
                "label": entry.get("title") or entry.get("label") or entry_type,
                "entry_type": entry_type,
                "note": entry.get("content"),
                "selected_text": anchor.selected_text or _render_scene_sentence_text(record, anchor.sentence_id),
                "payload_json": entry,
            }
        )
    return anchor


def _resolve_sentence_anchor(record: _RecordBundle, anchor: ReaderAskAnchorRef) -> ReaderAskAnchorRef:
    if anchor.anchor_type not in {"sentence", "text_range"}:
        return anchor
    if anchor.selected_text:
        return anchor
    sentence_text = _render_scene_sentence_text(record, anchor.sentence_id)
    if sentence_text:
        return anchor.model_copy(update={"selected_text": sentence_text})
    return anchor


def _citation_to_anchor(citation: dict[str, Any]) -> ReaderAskAnchorRef | None:
    anchor_type = citation.get("anchor_type")
    if anchor_type not in {"sentence", "text_range", "multi_text", "sentence_entry"}:
        return None
    return ReaderAskAnchorRef(
        anchor_type=anchor_type,
        sentence_id=citation.get("sentence_id"),
        target_key=citation.get("target_key"),
        selected_text=citation.get("selected_text"),
    )


async def _resolve_anchor_refs(
    user_id: UUID,
    record: _RecordBundle,
    *,
    anchors: list[ReaderAskAnchorRef],
) -> list[ReaderAskAnchorRef]:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    resolved: list[ReaderAskAnchorRef] = []
    async with pool.acquire() as conn:
        for raw_anchor in anchors:
            anchor = raw_anchor
            if anchor.anchor_type == "user_annotation":
                anchor = await _resolve_annotation_anchor(conn, user_id, anchor)
            elif anchor.anchor_type == "reader_note":
                anchor = await _resolve_reader_note_anchor(conn, user_id, anchor)
            elif anchor.anchor_type == "sentence_entry":
                anchor = _resolve_sentence_entry_anchor(record, anchor)
            elif anchor.anchor_type in {"sentence", "text_range"}:
                anchor = _resolve_sentence_anchor(record, anchor)
            resolved.append(anchor)

    if resolved:
        return resolved
    return []


def _collect_sentence_windows(record: _RecordBundle, anchors: list[ReaderAskAnchorRef]) -> list[dict[str, Any]]:
    sentences = record.render_scene.get("article", {}).get("sentences")
    if not isinstance(sentences, list):
        first_paragraph = _truncate_text(record.source_text, 260)
        if not first_paragraph:
            return []
        return [
            {
                "sentence_id": None,
                "anchor_text": first_paragraph,
                "window": [
                    {
                        "sentence_id": None,
                        "paragraph_id": None,
                        "text": first_paragraph,
                        "translation_zh": None,
                    }
                ],
                "fallback_window": True,
            }
        ]
    translations = _translations_map(record)
    sentence_ids: list[str] = []
    for anchor in anchors:
        sentence_ids.extend(_sentence_ids_from_anchor(anchor))
    sentence_id_set = {sentence_id for sentence_id in sentence_ids if sentence_id}
    if not sentence_id_set:
        fallback_items = []
        for candidate in sentences[:2]:
            if not isinstance(candidate, dict):
                continue
            fallback_items.append(
                {
                    "sentence_id": candidate.get("sentence_id"),
                    "paragraph_id": candidate.get("paragraph_id"),
                    "text": _truncate_text(candidate.get("text"), 240),
                    "translation_zh": _truncate_text(
                        translations.get(candidate.get("sentence_id")) if isinstance(candidate.get("sentence_id"), str) else None,
                        180,
                    )
                    or None,
                }
            )
        if fallback_items:
            return [
                {
                    "sentence_id": fallback_items[0].get("sentence_id"),
                    "anchor_text": fallback_items[0].get("text"),
                    "window": fallback_items,
                    "fallback_window": True,
                }
            ]
        return []

    ordered: list[dict[str, Any]] = []
    for index, item in enumerate(sentences):
        if not isinstance(item, dict):
            continue
        current_id = item.get("sentence_id")
        if current_id not in sentence_id_set:
            continue
        window_items = []
        for candidate in sentences[max(index - 1, 0):min(index + 2, len(sentences))]:
            if not isinstance(candidate, dict):
                continue
            sentence_id = candidate.get("sentence_id")
            if not isinstance(sentence_id, str):
                continue
            window_items.append(
                {
                    "sentence_id": sentence_id,
                    "paragraph_id": candidate.get("paragraph_id"),
                    "text": _truncate_text(candidate.get("text"), 240),
                    "translation_zh": _truncate_text(translations.get(sentence_id), 180) or None,
                }
            )
        ordered.append(
            {
                "sentence_id": current_id,
                "anchor_text": _truncate_text(item.get("text"), 240),
                "window": window_items,
            }
        )
    return ordered


def _collect_paragraph_sentences(
    record: _RecordBundle,
    *,
    target_sentence_id: str | None,
) -> list[dict[str, Any]]:
    """Return the sentences in the paragraph containing the target sentence.

    Paragraph identity is derived from ``render_scene.article.sentences``
    using each entry's ``paragraph_id``. If no paragraph_id metadata exists
    we fall back to a 3-sentence window around the target sentence.
    """
    sentences = record.render_scene.get("article", {}).get("sentences")
    if not isinstance(sentences, list):
        return []

    if not target_sentence_id:
        return []

    target_paragraph_id: str | None = None
    target_index: int | None = None
    for index, item in enumerate(sentences):
        if not isinstance(item, dict):
            continue
        if item.get("sentence_id") == target_sentence_id:
            target_paragraph_id = item.get("paragraph_id")
            target_index = index
            break

    if target_paragraph_id is not None:
        paragraph_sentences = [
            sentence for sentence in sentences
            if isinstance(sentence, dict)
            and sentence.get("paragraph_id") == target_paragraph_id
        ]
        return [_format_sentence_span(s) for s in paragraph_sentences if isinstance(s, dict)]

    if target_index is None:
        return []

    # Fallback: 3-sentence window.
    fallback = sentences[max(target_index - 1, 0):min(target_index + 2, len(sentences))]
    return [_format_sentence_span(s) for s in fallback if isinstance(s, dict)]


def _format_sentence_span(sentence: dict[str, Any]) -> dict[str, Any]:
    sentence_id = sentence.get("sentence_id")
    text = _truncate_text(sentence.get("text"), 320)
    return {
        "sentence_id": sentence_id,
        "paragraph_id": sentence.get("paragraph_id"),
        "text": text,
        "is_active_anchor": False,
    }


def _build_record_context_payload(
    record: _RecordBundle,
    *,
    scope: str,
    target_sentence_id: str | None,
) -> dict[str, Any]:
    """Build a context payload for ``get_record_context`` based on scope.

    ``scope`` is one of ``window`` (default), ``paragraph``, ``full``.
    Length cap on ``scope='full'`` is 10000 chars; oversized articles are
    truncated and marked ``truncated: true``.
    """
    can_load_more = scope
    truncated = False
    article_text = record.source_text or ""

    if scope == "full":
        if len(article_text) > 10000:
            article_text = article_text[:10000]
            truncated = True
        sentence_window: list[dict[str, Any]] = [
            {
                "sentence_id": None,
                "paragraph_id": None,
                "text": article_text,
                "is_active_anchor": False,
            }
        ]
    elif scope == "paragraph":
        sentence_window = _collect_paragraph_sentences(
            record, target_sentence_id=target_sentence_id,
        )
        if not sentence_window:
            # Fallback to 3 sentences around target so the tool still
            # returns something useful.
            sentences = record.render_scene.get("article", {}).get("sentences") or []
            target_index = next(
                (
                    index
                    for index, item in enumerate(sentences)
                    if isinstance(item, dict) and item.get("sentence_id") == target_sentence_id
                ),
                None,
            )
            if target_index is not None:
                sentence_window = [
                    _format_sentence_span(s)
                    for s in sentences[max(target_index - 1, 0):min(target_index + 2, len(sentences))]
                    if isinstance(s, dict)
                ]
    else:  # 'window' default
        anchors = _extract_active_anchors(record)
        sentence_window = _collect_sentence_windows(record, anchors)
        # Flatten the (anchor->window) shape to sentence spans.
        flattened: list[dict[str, Any]] = []
        for entry in sentence_window:
            for item in entry.get("window", []):
                flattened.append(
                    {
                        "sentence_id": item.get("sentence_id"),
                        "paragraph_id": item.get("paragraph_id"),
                        "text": item.get("text"),
                        "translation_zh": item.get("translation_zh"),
                        "is_active_anchor": False,
                    }
                )
        sentence_window = flattened

    active_anchor = None
    sentences_lookup = sentence_map(record.render_scene)
    if target_sentence_id:
        target = sentences_lookup.get(target_sentence_id)
        if target is not None:
            active_anchor = {
                "sentence_id": target_sentence_id,
                "text": _truncate_text(target.get("text"), 240),
                "paragraph_id": target.get("paragraph_id"),
            }

    return {
        "record_id": str(record.record_id),
        "record_title": record.title,
        "active_anchor": active_anchor,
        "sentence_window": sentence_window,
        "can_load_more": can_load_more,
        "scope": scope,
        "target_sentence_id": target_sentence_id,
        "truncated": truncated,
    }


def _extract_active_anchors(record: _RecordBundle) -> list[ReaderAskAnchorRef]:
    """Return the anchors whose sentence IDs are known to this record.

    When called outside the request scope (e.g. tests), ``record`` carries
    no per-run anchors; we fall back to the first two sentences as a
    cheap window so the tool still returns something useful.
    """
    sentences = record.render_scene.get("article", {}).get("sentences") or []
    fallback_targets: list[str] = []
    for sentence in sentences:
        if isinstance(sentence, dict):
            sid = sentence.get("sentence_id")
            if isinstance(sid, str):
                fallback_targets.append(sid)
            if len(fallback_targets) >= 2:
                break
    if not fallback_targets:
        return []
    return [
        ReaderAskAnchorRef(
            anchor_type="sentence",
            target_key=f"record:{record.record_id}:sentence:{sid}",
            sentence_id=sid,
        )
        for sid in fallback_targets
    ]


def _collect_insight_entries(
    record: _RecordBundle,
    *,
    target_sentence_id: str | None,
    kind: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    """Round 2: collect insights with optional filters + translation_zh."""
    translations = _translations_map(record)
    entries_raw = record.render_scene.get("sentence_entries") or []
    if not isinstance(entries_raw, list):
        entries_raw = []

    results: list[dict[str, Any]] = []
    for entry in entries_raw:
        if not isinstance(entry, dict):
            continue
        sentence_id = entry.get("sentence_id") or entry.get("sentenceId")
        if not isinstance(sentence_id, str):
            continue
        if target_sentence_id and sentence_id != target_sentence_id:
            continue
        entry_kind = str(entry.get("entry_type") or entry.get("entryType") or "")
        if kind and entry_kind != kind:
            continue
        results.append(
            {
                "insight_id": str(entry.get("id")),
                "sentence_id": sentence_id,
                "kind": entry_kind,
                "title": _truncate_text(entry.get("title") or entry.get("label"), 80),
                "content_md": _truncate_text(entry.get("content"), 360),
                "translation_zh": _truncate_text(translations.get(sentence_id), 240) or None,
                "source": "workflow",
                "confidence": entry.get("confidence"),
                "created_at": entry.get("created_at"),
            }
        )
        if len(results) >= limit:
            break
    return results


def _collect_sentence_entries(record: _RecordBundle, anchors: list[ReaderAskAnchorRef]) -> list[dict[str, Any]]:
    entries_raw = record.render_scene.get("sentence_entries") or record.render_scene.get("sentenceEntries")
    if not isinstance(entries_raw, list):
        return []
    sentence_ids = {sentence_id for anchor in anchors for sentence_id in _sentence_ids_from_anchor(anchor)}
    results: list[dict[str, Any]] = []
    for entry in entries_raw:
        if not isinstance(entry, dict):
            continue
        sentence_id = entry.get("sentence_id") or entry.get("sentenceId")
        if sentence_id not in sentence_ids:
            continue
        results.append(
            {
                "id": entry.get("id"),
                "sentence_id": sentence_id,
                "entry_type": entry.get("entry_type") or entry.get("entryType"),
                "title": _truncate_text(entry.get("title") or entry.get("label"), 80),
                "content": _truncate_text(entry.get("content"), 220),
            }
        )
    return results[:cfg.MAX_PROMPT_ASSET_ITEMS]


async def _tool_get_user_vocabulary_book(
    user_id: UUID,
    *,
    lemma: str | None,
    limit: int,
    sort_by: str,
) -> list[dict[str, Any]]:
    """Round 2 tool: list vocabulary book entries for the user.

    The vocabulary backend does not support search; we load the user's
    entries and filter / sort in memory. Returns at most ``limit`` rows.
    """
    items, _ = await vocabulary_svc.list_vocabulary(
        user_id=user_id,
        page=1,
        limit=200,
        lite=False,
    )

    # Filter by lemma substring (case-insensitive).
    query_lower = _normalize_text(lemma).lower() if lemma else ""
    filtered: list[dict[str, Any]] = []
    for item in items:
        item_lemma = str(item.get("lemma") or "")
        item_display = str(item.get("display_word") or "")
        item_source = str(item.get("source_sentence") or "")
        if query_lower:
            haystack = " ".join([item_lemma, item_display, item_source]).lower()
            if query_lower not in haystack:
                continue
        filtered.append(item)

    # Sort.
    if sort_by == "lemma_asc":
        filtered.sort(key=lambda it: str(it.get("lemma") or "").lower())
    else:
        # Default: 'recent' — vocabulary service returns ORDER BY created_at DESC.
        # list_vocabulary already sorts this way; we trust the service order
        # but enforce here defensively.
        filtered.sort(
            key=lambda it: str(it.get("created_at") or ""),
            reverse=True,
        )

    # Project to the agent-facing shape.
    matches: list[dict[str, Any]] = []
    for item in filtered[:limit]:
        matches.append(
            {
                "id": str(item.get("id")),
                "lemma": str(item.get("lemma") or ""),
                "display_word": str(item.get("display_word") or ""),
                "short_meaning": _truncate_text(item.get("short_meaning"), 80),
                "source_sentence": _truncate_text(item.get("source_sentence"), 120),
                "mastery_status": item.get("mastery_status"),
            }
        )
    return matches


async def _tool_resolve_known_reference_for_agent(
    *,
    user_id: UUID,
    current_record_id: UUID,
    query: str,
    top_k: int = 5,
) -> dict[str, Any]:
    """Round 2 resolver tool: wrap existing known-reference resolver.

    Returns a stable dict shape with one of three statuses:
    - ``resolved``  — single candidate match.
    - ``ambiguous`` — multiple candidates; ``disambiguation_needed=True``,
      model must trigger HITL picker.
    - ``not_found`` — zero candidates.

    No cross-HTTP HITL resume this round: results are returned to the
    model so the main loop can decide how to present them.
    """
    # Lazy import to avoid circular import: resolver.py depends on this module
    # only via the resolver facade.
    from app.services.reader_ask.resolver import resolve_known_references

    reference_needs = planner.ReaderAskReferenceNeeds(
        requested=True,
        query=query.strip() if isinstance(query, str) else None,
        reason="agent_tool",
    )

    resolution = await resolve_known_references(
        user_id=user_id,
        current_record_id=current_record_id,
        reference_needs=reference_needs,
    )

    status = resolution.status or "not_needed"
    if status == "not_needed":
        # Should not happen since reference_needs.requested=True, but fall back.
        status = "not_found"

    candidates: list[dict[str, Any]] = []
    if status == "resolved":
        candidates = [dict(record) for record in resolution.resolved_records]
    elif status == "ambiguous":
        candidates = [dict(record) for record in resolution.ambiguous_records]

    # Apply top_k limit.
    candidates = candidates[: max(1, top_k)]

    if status == "resolved" and len(candidates) == 1:
        return {
            "status": "resolved",
            "query": query,
            "summary": f"Resolved to {candidates[0].get('title', 'one record')}",
            "next_actions": [
                "Use the resolved record's overview to ground the answer.",
            ],
            "artifacts": [f"record:{candidates[0].get('record_id')}"],
            "ok": True,
            "record": candidates[0] if candidates else None,
            "disambiguation_needed": False,
        }

    if status == "ambiguous":
        return {
            "status": "ambiguous",
            "query": query,
            "summary": f"Multiple matches ({len(candidates)}) — disambiguation needed.",
            "next_actions": [
                "Ask the user to pick one of the candidates.",
            ],
            "artifacts": [
                f"record:{c.get('record_id')}" for c in candidates if c.get("record_id")
            ],
            "ok": True,
            "candidates": candidates,
            "disambiguation_needed": True,
        }

    # not_found
    return {
        "status": "not_found",
        "query": query,
        "summary": "No matching records in workspace",
        "next_actions": [
            "Ask the user to be more specific or attach a record.",
        ],
        "artifacts": [],
        "ok": False,
        "disambiguation_needed": False,
    }


def _build_allowed_external_attachments(
    attachments: list[ReaderAskAttachment],
) -> list[dict[str, str]]:
    """Build the allowlist of external attachments the agent may load.

    Round 10 fix: only records/assets present in this manifest can be
    loaded by load_explicit_attachment_context. This prevents the agent
    from reading arbitrary records not explicitly attached by the user.

    Uses planner's _attachment_target_record for record_ref (which supports
    metadata.asset_id as record id fallback) and _attachment_record_id /
    _attachment_asset_id for analysis_ref / supplement_ref.
    """
    from app.services.reader_ask.planner import (
        _attachment_asset_id,
        _attachment_record_id,
        _attachment_target_record,
    )

    manifest: list[dict[str, str]] = []
    for att in attachments:
        if att.kind not in ("record_ref", "analysis_ref", "supplement_ref"):
            continue
        # record_ref uses _attachment_target_record (asset_id fallback for record id)
        # analysis_ref / supplement_ref use _attachment_record_id + _attachment_asset_id
        if att.kind == "record_ref":
            record_id = _attachment_target_record(att)
        else:
            record_id = _attachment_record_id(att)
        if not record_id:
            continue
        entry: dict[str, str] = {"tool_record_id": record_id}
        # record_ref: no asset_id (loads whole record overview)
        # analysis_ref / supplement_ref: resolve asset_id
        if att.kind == "record_ref":
            entry["tool_asset_id"] = ""
        else:
            asset_id = _attachment_asset_id(att)
            if asset_id:
                entry["tool_asset_id"] = asset_id
            else:
                entry["tool_asset_id"] = ""
        manifest.append(entry)
    return manifest


async def _tool_load_explicit_attachment_context(
    *,
    user_id: UUID,
    current_record_id: UUID,
    record_id: str,
    asset_id: str | None = None,
) -> dict[str, Any]:
    """Round 10 tool: load context for an explicitly attached external reference.

    For record_ref (asset_id is None): loads the referenced record's
    article overview and record insights.

    For analysis_ref / supplement_ref (asset_id provided): loads the
    specific asset's content from the referenced record using the
    resolver service and supplements service.

    Returns a dict with status="loaded" on success or status="not_found"
    on failure.
    """
    from app.services.reader_ask import resolver as resolver_svc

    try:
        target_uuid = UUID(record_id)
    except ValueError:
        return {
            "status": "not_found",
            "record_id": record_id,
            "summary": "Invalid record_id format",
            "ok": False,
        }

    if target_uuid == current_record_id:
        return {
            "status": "not_found",
            "record_id": record_id,
            "summary": "Cannot load current record as external attachment",
            "ok": False,
        }

    try:
        bundle = await _load_record_bundle(user_id, target_uuid)
    except HTTPException:
        return {
            "status": "not_found",
            "record_id": record_id,
            "summary": f"Record {record_id} not found or not accessible",
            "ok": False,
        }

    if asset_id is None:
        # record_ref: return overview + insights
        structured = resolver_svc.lookup_structured_record_assets(
            record_id=str(bundle.record_id),
            record_title=bundle.title,
            render_scene=bundle.render_scene,
            page_state_json=bundle.page_state_json,
            reason="explicit_attachment",
        )
        return {
            "status": "loaded",
            "record_id": record_id,
            "record_title": structured.get("record_title"),
            "article_overview": structured.get("article_overview"),
            "article_overview_status": structured.get("article_overview_status"),
            "article_overview_source": structured.get("article_overview_source"),
            "article_overview_confidence": structured.get("article_overview_confidence"),
            "record_insights": structured.get("record_insights", []),
            "source_labels": structured.get("source_labels", []),
            "ok": True,
        }

    # analysis_ref / supplement_ref: use resolver to find the specific asset
    # across both sentence_entries (analysis) and supplements (supplement).
    asset_resolution = await resolver_svc.resolve_structured_asset_references(
        user_id=user_id,
        current_record_id=current_record_id,
        external_record_refs=[{"record_id": record_id}],
        structured_asset_needs=planner.ReaderAskStructuredAssetNeeds(
            requested=True,
            requested_asset_type=None,
        ),
        bundle_loader=lambda uid, rid: _load_record_bundle_dict(uid, rid),
        supplement_loader=lambda uid, rid: _list_supplements_for_resolver(uid, rid),
        explicit_asset_refs=[{
            "record_id": record_id,
            "asset_id": asset_id,
        }],
    )

    resolved = asset_resolution.resolved_assets
    if resolved:
        asset = resolved[0]
        return {
            "status": "loaded",
            "record_id": record_id,
            "record_title": asset.get("record_title"),
            "asset_id": asset_id,
            "asset_type": asset.get("asset_type", "analysis"),
            "entry_type": asset.get("entry_type"),
            "asset_title": asset.get("asset_title") or asset.get("title"),
            "content_md": asset.get("content_md") or asset.get("content"),
            "content_summary": asset.get("content_summary"),
            "source_labels": asset.get("source_labels", ["external_attachment", "external_assets"]),
            "ok": True,
        }

    return {
        "status": "not_found",
        "record_id": record_id,
        "asset_id": asset_id,
        "summary": f"Asset {asset_id} not found in record {record_id}",
        "ok": False,
    }


async def _load_record_bundle_dict(user_id: UUID, record_id: UUID) -> dict[str, Any]:
    """Load a record bundle and return as dict for resolver consumption."""
    bundle = await _load_record_bundle(user_id, record_id)
    return {
        "record_id": str(bundle.record_id),
        "title": bundle.title,
        "render_scene": bundle.render_scene,
        "page_state_json": bundle.page_state_json,
    }


async def _list_supplements_for_resolver(user_id: UUID, record_id: UUID) -> list[dict[str, Any]]:
    """Load supplements for a record for resolver consumption."""
    from app.services.reader_ask import supplements as supplements_svc
    try:
        return await supplements_svc.list_supplements_for_record(user_id, record_id)
    except Exception:
        return []


async def _tool_suggest_prompts(
    suggestions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Round 2 suggestion tool: emit 2-3 follow-up chips.

    The agent has already validated the suggestions before calling this
    function (the agent tool layer enforces 2-3 + label/prompt). This
    function is a thin observability seam that lets Round 2 record the
    suggestions in tool trace / state. Front-end rendering wires up
    in Round 4.
    """
    cleaned = [
        {
            "label": str(item.get("label", "")).strip()[:40],
            "prompt": str(item.get("prompt", "")).strip()[:200],
        }
        for item in suggestions
        if isinstance(item, dict)
        and isinstance(item.get("label"), str)
        and isinstance(item.get("prompt"), str)
    ]
    return {
        "status": "success",
        "summary": f"Suggested {len(cleaned)} follow-up prompt(s).",
        "next_actions": [
            "Render chips at the tail of the assistant message.",
        ],
        "artifacts": [f"suggestion:{item['label']}" for item in cleaned],
        "ok": True,
        "suggestions": cleaned,
    }


def _vocabulary_item_to_citation(item: dict[str, Any]) -> ReaderAskCitation:
    return ReaderAskCitation(
        citation_id=str(uuid4()),
        kind="vocabulary",
        label=item.get("display_word") or item.get("lemma") or "生词本",
        selected_text=item.get("source_sentence"),
        metadata_json={
            "vocab_id": item.get("id"),
            "lemma": item.get("lemma"),
            "mastery_status": item.get("mastery_status"),
            "short_meaning": item.get("short_meaning"),
        },
    )


def _merge_citation(citations: list[ReaderAskCitation], citation: ReaderAskCitation) -> None:
    utils.merge_citation(citations, citation)


def _current_turn_run_id(message_dict: dict[str, Any], run_info: ReaderAskRunInfo | None = None) -> UUID | None:
    current_turn_run_id = message_dict.get("current_turn_run_id")
    if isinstance(current_turn_run_id, str) and current_turn_run_id.strip():
        try:
            return UUID(current_turn_run_id)
        except ValueError:
            return None
    run_id = run_info.run_id if run_info is not None else None
    if isinstance(run_id, str) and run_id.strip():
        try:
            return UUID(run_id)
        except ValueError:
            return None
    return None


def _build_run_info(
    *,
    turn_id: str,
    run_id: str,
    attempt: int = 1,
    supersedes_run_id: str | None = None,
) -> dict[str, Any]:
    return {
        "turn_id": turn_id,
        "run_id": run_id,
        "run_attempt": max(attempt, 1),
        "supersedes_run_id": supersedes_run_id,
    }


def _user_message_metadata(
    *,
    resolved_intent: ReaderAskResolvedIntent | None = None,
    resolved_context_input: ReaderAskResolvedContextInput | None = None,
    submission_mode: ReaderAskSubmissionMode = "chat",
) -> dict[str, Any]:
    return output_contract_svc.build_user_message_metadata(
        resolved_intent=resolved_intent,
        resolved_context_input=resolved_context_input,
        submission_mode=submission_mode,
    )


def _assistant_message_metadata(
    *,
    resolved_intent: ReaderAskResolvedIntent | None = None,
    run_info: dict[str, Any] | None = None,
    run_history: list[dict[str, Any]] | None = None,
    resolved_context_input: ReaderAskResolvedContextInput | None = None,
    submission_mode: ReaderAskSubmissionMode = "chat",
) -> dict[str, Any]:
    return output_contract_svc.build_assistant_message_metadata(
        resolved_intent=resolved_intent,
        run_info=run_info,
        run_history=run_history,
        resolved_context_input=resolved_context_input,
        submission_mode=submission_mode,
    )


def _build_user_visible_output(
    *,
    content_md: str,
    submission_mode: ReaderAskSubmissionMode,
    resolved_intent: ReaderAskResolvedIntent | None,
    citations: list[ReaderAskCitation],
    action_proposals: list[ReaderAskActionProposal],
    tool_trace: list[ReaderAskToolTraceEntry],
    evidence: list[ReaderAskEvidenceItem],
    trace_summary: ReaderAskTraceSummary | None,
    disambiguation: ReaderAskDisambiguation | None,
    external_asset_disambiguation: ReaderAskAssetDisambiguation | None,
    response_cards: list[ReaderAskResponseCard],
    usage_summary: dict[str, Any] | None,
    billed_points: int,
    resolved_context: ReaderAskResolvedContextSummary,
    context_plan: ReaderAskContextPlan | None,
    resolved_context_input: ReaderAskResolvedContextInput | None,
    run_info: dict[str, Any] | ReaderAskRunInfo | None,
    supplement_candidates: list[ReaderAskSupplementCandidate] | list[dict[str, Any]],
    persisted_supplements: list[ReaderAskPersistedSupplement] | list[dict[str, Any]],
    reasoning_md: str | None = None,
    reasoning_status: str | None = None,
    follow_up_suggestions: list[Any] | None = None,
) -> ReaderAskUserVisibleOutput:
    return output_contract_svc.build_user_visible_output(
        content_md=content_md,
        submission_mode=submission_mode,
        resolved_intent=resolved_intent,
        citations=citations,
        action_proposals=action_proposals,
        tool_trace=tool_trace,
        evidence=evidence,
        trace_summary=trace_summary,
        disambiguation=disambiguation,
        external_asset_disambiguation=external_asset_disambiguation,
        response_cards=response_cards,
        usage_summary=usage_summary,
        billed_points=billed_points,
        resolved_context=resolved_context,
        context_plan=context_plan,
        resolved_context_input=resolved_context_input,
        run_info=run_info,
        supplement_candidates=supplement_candidates,
        persisted_supplements=persisted_supplements,
        reasoning_md=reasoning_md,
        reasoning_status=reasoning_status,
        follow_up_suggestions=follow_up_suggestions,
    )


def _build_completed_payload(
    *,
    message_id: str,
    thread_id: str,
    output: ReaderAskUserVisibleOutput,
    usage_event_id: UUID | None = None,
) -> ReaderAskCompletedPayload:
    return output_contract_svc.to_completed_payload(
        message_id=message_id,
        thread_id=thread_id,
        output=output,
        usage_event_id=str(usage_event_id) if usage_event_id else None,
    )


def _build_stream_checkpoint_output_json(
    *,
    content_md: str,
    reasoning_md: str | None,
    reasoning_status: str | None,
    submission_mode: ReaderAskSubmissionMode,
    resolved_intent: ReaderAskResolvedIntent | None,
    record: _RecordBundle,
    anchors: list[ReaderAskAnchorRef],
    attachments: list[ReaderAskAttachment],
    runtime_state: ReaderAskRuntimeState,
    reference_resolution: planner.ReaderAskReferenceResolution,
    disambiguation: ReaderAskDisambiguation | None,
    external_asset_disambiguation: ReaderAskAssetDisambiguation | None,
    trace_summary: ReaderAskTraceSummary | None,
    context_plan: ReaderAskContextPlan | None,
    resolved_context_input: ReaderAskResolvedContextInput | None,
    run_info: dict[str, Any] | ReaderAskRunInfo | None,
    persisted_supplements: list[dict[str, Any]],
) -> dict[str, Any]:
    response_cards = _build_response_cards(
        task_mode=resolved_intent or "general",
        record=record,
        anchors=anchors,
        runtime_state=runtime_state,
    )
    supplement_candidates = _build_supplement_candidates_from_runtime(
        resolved_intent=resolved_intent or "general",
        anchors=anchors,
        runtime_state=runtime_state,
        assistant_content_md=content_md,
        created_from_turn_run_id=str(run_info["run_id"]) if isinstance(run_info, dict) and run_info.get("run_id") else str(uuid4()),
    )
    supplement_candidates_json = [candidate.model_dump(mode="json") for candidate in supplement_candidates]
    runtime_proposals = _build_action_proposals_from_runtime(
        record=record,
        action_requests=runtime_state.action_requests,
        assistant_content_md=content_md,
    )
    action_proposals = _merge_action_proposals(
        runtime_proposals,
        _build_supplement_action_proposals(supplement_candidates_json),
    )
    output = _build_user_visible_output(
        content_md=content_md,
        submission_mode=submission_mode,
        resolved_intent=resolved_intent,
        citations=runtime_state.citations,
        action_proposals=action_proposals,
        tool_trace=runtime_state.tool_trace,
        evidence=_build_evidence_items(
            attachments=attachments,
            citations=runtime_state.citations,
            current_record_id=str(record.record_id),
            current_record_title=record.title,
            external_record_contexts=runtime_state.latest_external_record_contexts,
            external_asset_contexts=runtime_state.latest_external_asset_contexts,
            reference_resolution=reference_resolution,
            supplement_candidates=supplement_candidates,
            disambiguation=disambiguation,
            external_asset_disambiguation=external_asset_disambiguation,
        ),
        trace_summary=trace_summary,
        disambiguation=disambiguation,
        external_asset_disambiguation=external_asset_disambiguation,
        response_cards=response_cards,
        usage_summary=None,
        billed_points=0,
        resolved_context=planner.build_resolved_context_summary(
            record_id=str(record.record_id),
            record_title=record.title,
            anchors=anchors,
            explicit_attachment_count=len(attachments),
            runtime_state=runtime_state,
            used_cross_record_context=runtime_state.used_cross_record_context,
            citations=runtime_state.citations,
        ),
        context_plan=context_plan,
        resolved_context_input=resolved_context_input,
        run_info=run_info,
        supplement_candidates=supplement_candidates,
        persisted_supplements=persisted_supplements,
        reasoning_md=reasoning_md,
        reasoning_status=reasoning_status,
        follow_up_suggestions=runtime_state.latest_suggestions or None,
    )
    return output.model_dump(mode="json")


def _visible_output_from_message(message: ReaderAskMessage, message_dict: dict[str, Any]) -> dict[str, Any]:
    return output_contract_svc.visible_output_from_message(message, message_dict)


def _build_minimal_contract(
    *,
    body: ReaderAskMessageStreamRequest,
    record: _RecordBundle,
    history_messages: list[dict[str, Any]],
    attachments: list[ReaderAskAttachment],
    anchors: list[ReaderAskAnchorRef],
    resolved_intent: ReaderAskResolvedIntent,
    resolved_intent_label: str,
) -> runtime_contract_svc.ReaderAskAnswerRuntimeInput:
    """Build a minimal ``ReaderAskAnswerRuntimeInput`` for the agent-loop-first path.

    The contract is read-only from the helpers' perspective — they consume
    ``entry_action`` / ``attachments`` / ``anchors`` to produce minimal
    context_plan and trace_summary shapes. The ``planning_snapshot`` field
    stays None because the helpers do not need it.
    """
    return runtime_contract_svc.ReaderAskAnswerRuntimeInput(
        thread={"id": str(getattr(body.page_identity, "thread_id", "")) or "", "title": None},
        record=record,
        user_message=body.content,
        history_messages=history_messages,
        page_identity=body.page_identity,
        attachments=attachments,
        anchors=anchors,
        resolved_intent=resolved_intent,
        resolved_intent_label=resolved_intent_label,
        entry_action=body.entry_action,
        submission_mode="chat",
        cross_record_context_allowed=False,
        resolved_context_input=None,
        quick_action_annotation=None,
        reference_resolution=None,
        planning_snapshot=None,
        max_history_messages=4,
        max_message_text=2000,
    )


def _planning_snapshot_json(
    planning_snapshot: planner.ReaderAskPlanningSnapshot | None,
    *,
    planner_route_used: str = "planner_first",
) -> dict[str, Any]:
    if planning_snapshot is None:
        return {
            "planner_skipped": planner_route_used == "agent_loop_first",
            "planner_route_used": planner_route_used,
        }
    # Round 1 — MinimalPlanningSnapshot is a lightweight dataclass that
    # does not carry planner_decision / reference_needs / structured_asset_*
    # fields. The legacy serializer assumes the full ReaderAskPlanningSnapshot
    # shape; emit a minimal JSON for the agent-loop-first path that preserves the
    # ``context_plan`` / ``trace_summary`` / ``working_set`` / ``retrieval_needs``
    # fields used by eval, and skip the legacy-only fields.
    if isinstance(planning_snapshot, planner.MinimalPlanningSnapshot):
        return {
            "resolved_intent": planning_snapshot.resolved_intent,
            "planner_decision": None,
            "planner_validation_status": planning_snapshot.planner_validation_status,
            "retrieval_needs": planning_snapshot.retrieval_needs,
            "working_set": {
                "primary_anchor": planning_snapshot.working_set.primary_anchor.model_dump(mode="json")
                if planning_snapshot.working_set.primary_anchor
                else None,
                "local_context_window_needed": planning_snapshot.working_set.local_context_window_needed,
                "record_insights_needed": planning_snapshot.working_set.record_insights_needed,
                "article_overview_needed": planning_snapshot.working_set.article_overview_needed,
                "dictionary_needed": planning_snapshot.working_set.dictionary_needed,
                "cross_record_context_allowed": planning_snapshot.working_set.cross_record_context_allowed,
                "external_record_refs": planning_snapshot.working_set.external_record_refs,
                "external_asset_refs": planning_snapshot.working_set.external_asset_refs,
                "external_asset_lookup_needed": planning_snapshot.working_set.external_asset_lookup_needed,
            },
            "context_plan": planning_snapshot.context_plan.model_dump(mode="json")
            if planning_snapshot.context_plan
            else None,
            "trace_summary": planning_snapshot.trace_summary.model_dump(mode="json")
            if planning_snapshot.trace_summary
            else None,
            "disambiguation_state": None,
            "external_asset_disambiguation_state": None,
            "planner_skipped": True,
            "planner_route_used": planner_route_used,
        }
    return {
        "resolved_intent": planning_snapshot.resolved_intent,
        "planner_decision": planning_snapshot.planner_decision.model_dump(mode="json"),
        "planner_validation_status": planning_snapshot.planner_validation_status,
        "reference_needs": {
            "requested": planning_snapshot.reference_needs.requested,
            "query": planning_snapshot.reference_needs.query,
            "reason": planning_snapshot.reference_needs.reason,
        },
        "retrieval_needs": planning_snapshot.retrieval_needs,
        "resolved_references": {
            "attempted": planning_snapshot.resolved_references.attempted,
            "status": planning_snapshot.resolved_references.status,
            "query": planning_snapshot.resolved_references.query,
            "reason": planning_snapshot.resolved_references.reason,
            "resolved_records": planning_snapshot.resolved_references.resolved_records,
            "ambiguous_records": planning_snapshot.resolved_references.ambiguous_records,
            "resolution_meta": planning_snapshot.resolved_references.resolution_meta,
        },
        "structured_asset_needs": {
            "requested": planning_snapshot.structured_asset_needs.requested,
            "requested_asset_type": planning_snapshot.structured_asset_needs.requested_asset_type,
            "reason": planning_snapshot.structured_asset_needs.reason,
        },
        "structured_asset_resolution": {
            "attempted": planning_snapshot.structured_asset_resolution.attempted,
            "status": planning_snapshot.structured_asset_resolution.status,
            "requested_asset_type": planning_snapshot.structured_asset_resolution.requested_asset_type,
            "reason": planning_snapshot.structured_asset_resolution.reason,
            "record_id": planning_snapshot.structured_asset_resolution.record_id,
            "record_title": planning_snapshot.structured_asset_resolution.record_title,
            "resolved_assets": planning_snapshot.structured_asset_resolution.resolved_assets,
            "ambiguous_assets": planning_snapshot.structured_asset_resolution.ambiguous_assets,
        },
        "working_set": {
            "primary_anchor": planning_snapshot.working_set.primary_anchor.model_dump(mode="json")
            if planning_snapshot.working_set.primary_anchor
            else None,
            "local_context_window_needed": planning_snapshot.working_set.local_context_window_needed,
            "record_insights_needed": planning_snapshot.working_set.record_insights_needed,
            "article_overview_needed": planning_snapshot.working_set.article_overview_needed,
            "dictionary_needed": planning_snapshot.working_set.dictionary_needed,
            "cross_record_context_allowed": planning_snapshot.working_set.cross_record_context_allowed,
            "external_record_refs": planning_snapshot.working_set.external_record_refs,
            "external_asset_refs": planning_snapshot.working_set.external_asset_refs,
            "external_asset_lookup_needed": planning_snapshot.working_set.external_asset_lookup_needed,
        },
        "context_plan": planning_snapshot.context_plan.model_dump(mode="json"),
        "trace_summary": planning_snapshot.trace_summary.model_dump(mode="json"),
        "disambiguation_state": planning_snapshot.disambiguation_state.model_dump(mode="json")
        if planning_snapshot.disambiguation_state
        else None,
        "external_asset_disambiguation_state": planning_snapshot.external_asset_disambiguation_state.model_dump(mode="json")
        if planning_snapshot.external_asset_disambiguation_state
        else None,
        "planner_skipped": planner_route_used == "agent_loop_first",
        "planner_route_used": planner_route_used,
    }


def _capability_trace_json(
    *,
    runtime_state: ReaderAskRuntimeState,
    context_plan: ReaderAskContextPlan | None,
) -> dict[str, Any]:
    return {
        "local_context_window": {
            "used": runtime_state.latest_record_context is not None,
            "reason": context_plan.record_context_reason if context_plan else None,
            "source_labels": ["current_record", "current_anchor", "current_paragraph"]
            if runtime_state.latest_record_context is not None
            else [],
        },
        "record_insights": {
            "used": bool(runtime_state.latest_record_insights),
            "reason": context_plan.record_insights_reason if context_plan else None,
            "source_labels": ["record_assets"] if runtime_state.latest_record_insights else [],
        },
        "article_overview": {
            "used": bool(runtime_state.latest_article_overview),
            "reason": context_plan.article_overview_reason if context_plan else None,
            "source_labels": ["article_overview"] if runtime_state.latest_article_overview else [],
        },
        "external_record_context": {
            "used": bool(runtime_state.latest_external_record_contexts),
            "reason": context_plan.external_record_context_reason if context_plan else None,
            "source_labels": ["external_record_context"] if runtime_state.latest_external_record_contexts else [],
        },
        "structured_asset_lookup": {
            "used": context_runtime_svc.external_context_has_structured_assets(runtime_state.latest_external_record_contexts),
            "reason": context_plan.structured_asset_lookup_reason if context_plan else None,
            "source_labels": ["article_overview", "record_assets"]
            if context_runtime_svc.external_context_has_structured_assets(runtime_state.latest_external_record_contexts)
            else [],
        },
        "external_asset_context": {
            "used": bool(runtime_state.latest_external_asset_contexts),
            "reason": context_plan.external_asset_selection_reason if context_plan else None,
            "source_labels": ["external_assets"] if runtime_state.latest_external_asset_contexts else [],
        },
    }


def _metrics_json(
    *,
    trace_summary: ReaderAskTraceSummary | None,
    billed_points: int,
    usage_event_id: UUID | None,
    planner_route: str | None = None,
    degenerate_detected: bool = False,
    degenerate_reason: str | None = None,
    runtime_state: ReaderAskRuntimeState | None = None,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "planner_mode": trace_summary.planner_mode if trace_summary else None,
        "working_set_mode": trace_summary.working_set_mode if trace_summary else None,
        "cross_record_context_allowed": trace_summary.cross_record_context_allowed if trace_summary else False,
        "cross_record_context_used": trace_summary.cross_record_context_used if trace_summary else False,
        "used_known_reference_resolution": trace_summary.used_known_reference_resolution if trace_summary else False,
        "used_external_record_context": trace_summary.used_external_record_context if trace_summary else False,
        "used_structured_asset_lookup": trace_summary.used_structured_asset_lookup if trace_summary else False,
        "used_hitp_disambiguation": trace_summary.used_hitp_disambiguation if trace_summary else False,
        "used_external_asset_context": trace_summary.used_external_asset_context if trace_summary else False,
        "used_external_asset_disambiguation": trace_summary.used_external_asset_disambiguation if trace_summary else False,
        "billed_points": billed_points,
        "usage_event_id": str(usage_event_id) if usage_event_id else None,
        "prompt_version": get_prompt_version(),
        "planner_route": planner_route,
        "degenerate_detected": degenerate_detected,
        "degenerate_reason": degenerate_reason,
    }

    # Round 6: extended observability from runtime_state
    if runtime_state is not None:
        # Route
        base["planner_skipped"] = runtime_state.planner_skipped

        # Tool metrics
        completed_traces = [t for t in runtime_state.tool_trace if t.status == "completed"]
        failed_traces = [t for t in runtime_state.tool_trace if t.status == "failed"]
        base["tool_call_count"] = runtime_state.tool_call_count
        base["tool_completed_count"] = len(completed_traces)
        base["tool_failed_count"] = len(failed_traces)
        base["tool_budget_exceeded"] = runtime_state.tool_call_count > runtime_state.max_tool_calls
        base["tool_durations_ms"] = {
            t.tool_name: t.metadata_json.get("duration_ms")
            for t in completed_traces
            if t.metadata_json.get("duration_ms") is not None
        }

        # Latency
        base["run_started_at"] = runtime_state.run_started_at
        base["first_token_at"] = runtime_state.first_token_at
        if runtime_state.run_started_at and runtime_state.first_token_at:
            _started = datetime.fromisoformat(runtime_state.run_started_at)
            _first = datetime.fromisoformat(runtime_state.first_token_at)
            base["ttft_ms"] = int((_first - _started).total_seconds() * 1000)

        # Output metrics
        base["citations_count"] = len(runtime_state.citations)
        base["follow_up_suggestions_count"] = len(runtime_state.latest_suggestions)
        base["action_proposals_count"] = len(runtime_state.action_requests)

        # Round 14: agent-loop repair telemetry
        base["repair_attempted"] = runtime_state.repair_attempted
        base["repair_reason"] = runtime_state.repair_reason
        base["repair_succeeded"] = runtime_state.repair_succeeded
        base["repair_route"] = runtime_state.repair_route

    return base


# ---------------------------------------------------------------------------
# Round 14 — agent-loop repair helper
# ---------------------------------------------------------------------------


async def _run_agent_loop_repair(
    *,
    user_id: UUID,
    record: Any,
    body: Any,
    attachments: list[ReaderAskAttachment],
    resolved_anchors: list[ReaderAskAnchorRef],
    history_messages: list[dict[str, Any]],
    thread: dict[str, Any],
    runtime_state: ReaderAskRuntimeState,
    primary_anchor: ReaderAskAnchorRef | None,
    submission_mode: str,
    resolved_intent: str,
    resolved_context_input: dict[str, Any],
    reference_resolution: Any,
    disambiguation: Any,
    external_asset_disambiguation: Any,
    trace_summary: Any,
    context_plan: Any,
    run_info: dict[str, Any] | None,
    route_settings: RunModelSettings,
    model_selection: Any,
    runtime_budget_kwargs: dict[str, Any],
    event_queue: asyncio.Queue,
    query_seed: str,
    get_record_context_cb: Any,
    get_record_insights_cb: Any,
    get_user_vocabulary_book_cb: Any,
    resolve_known_reference_cb: Any,
    load_explicit_attachment_context_cb: Any,
    generate_sentence_annotation_cb: Any,
    suggest_prompts_cb: Any,
    degenerate_content_md: str,
) -> tuple[str, ReaderAskRuntimeState]:
    """Run a single agent-loop repair attempt and return the repaired content.

    Round 14: when the agent_loop_first path produces a degenerate answer,
    this helper re-runs the same answer agent with a repair hint injected
    into the prompt payload. The repair reuses the canonical context /
    runtime state / history — it does NOT call resolve_semantic_planning.

    Returns a tuple of (repaired content_md, repair_runtime_state). The
    content may still be degenerate if repair failed; the caller decides
    whether to use it. The repair_runtime_state carries any citations /
    tool_trace / suggestions produced during the repair run — the caller
    MUST merge it into the canonical runtime_state when adopting repair
    content, otherwise the completed payload will carry stale evidence
    from the degenerate run.
    """
    repair_runtime_state = deepcopy(runtime_state)
    # Reset degenerate flags on the repair state so the repair run is not
    # itself flagged as degenerate before it produces output.
    repair_runtime_state.degenerate_detected = False
    repair_runtime_state.degenerate_reason = None

    # Reuse the same payload construction as the main answer, then inject
    # the repair hint so the agent knows the previous attempt failed.
    repair_payload = runtime_contract_svc.build_prompt_payload(
        runtime_contract_svc.ReaderAskAnswerRuntimeInput(
            thread=thread,
            record=record,
            user_message=body.content,
            history_messages=history_messages,
            page_identity=body.page_identity,
            attachments=attachments,
            anchors=resolved_anchors,
            resolved_intent=resolved_intent,
            resolved_intent_label=_TASK_MODE_LABELS[resolved_intent],
            entry_action=body.entry_action,
            submission_mode=submission_mode,
            cross_record_context_allowed=runtime_state.cross_record_context_allowed,
            resolved_context_input=resolved_context_input,
            quick_action_annotation=None,
            reference_resolution=reference_resolution,
            planning_snapshot=None,
            followup_hint=runtime_state.deictic_clarification_hint,
            cross_record_intent_hint=runtime_state.cross_record_intent_hint,
            external_attachment_hint=runtime_state.external_attachment_hint,
            dictionary_anchor_hint=runtime_state.dictionary_anchor_hint,
            long_history_hint=runtime_state.long_history_hint,
            max_history_messages=cfg.MAX_HISTORY_MESSAGES,
            max_message_text=cfg.MAX_MESSAGE_TEXT,
        )
    )
    repair_payload["repair_hint"] = {
        "previous_answer_degenerate": True,
        "previous_answer_preview": degenerate_content_md[:200],
        "instruction": (
            "Your previous answer was empty, a refusal, or otherwise degenerate. "
            "Using the context already provided, produce a direct, substantive answer. "
            "Do not refuse or claim lack of information unless the context is genuinely empty."
        ),
    }

    # Prepare (compress) the payload using the same budget as the main run.
    repair_payload, repair_max_output, _repair_compaction_audit, _repair_context_too_large = prompt_preparation_svc.prepare_prompt_payload(
        repair_payload,
        max_input_tokens=runtime_budget_kwargs["max_input_tokens"],
        budget_buffer_tokens=runtime_budget_kwargs["prompt_buffer_tokens"],
        default_max_output_tokens=route_settings.max_tokens or runtime_budget_kwargs["max_output_tokens"],
        min_max_output_tokens=cfg.MIN_MAX_OUTPUT_TOKENS,
    )
    if _repair_context_too_large:
        raise recovery_svc.ReplanContextTooLargeError()

    repair_deps = build_reader_ask_agent_deps(
        payload=repair_payload,
        event_queue=event_queue,
        state=repair_runtime_state,
        query_seed=query_seed,
        task_mode=resolved_intent,
        entry_action=body.entry_action,
        record_id=str(record.record_id),
        record_title=record.title,
        primary_anchor=primary_anchor,
        get_record_context_fn=get_record_context_cb,
        get_record_insights_fn=get_record_insights_cb,
        get_user_vocabulary_book_fn=get_user_vocabulary_book_cb,
        resolve_known_reference_fn=resolve_known_reference_cb,
        load_explicit_attachment_context_fn=load_explicit_attachment_context_cb,
        allowed_external_attachments=_build_allowed_external_attachments(attachments),
        generate_sentence_annotation_fn=generate_sentence_annotation_cb,
        suggest_prompts_fn=suggest_prompts_cb,
        vocabulary_item_to_citation_fn=_vocabulary_item_to_citation,
    )

    repair_content = await run_reader_ask_replan(
        replan_deps=repair_deps,
        replan_max_output=repair_max_output,
        route_settings=route_settings,
        model_selection=model_selection,
    )
    return repair_content, repair_runtime_state


def _merge_repair_runtime_state(
    target: ReaderAskRuntimeState,
    repair: ReaderAskRuntimeState,
) -> None:
    """Merge evidence-producing fields from repair state into target state.

    Round 14: when a repair attempt succeeds, the citations / tool_trace /
    suggestions / action_requests produced during the repair run must
    replace the stale evidence from the degenerate run. Routing telemetry
    fields (planner_route_used, repair_*, degenerate_*) on ``target`` are
    preserved — only evidence fields are merged.
    """
    target.citations = repair.citations
    target.tool_trace = repair.tool_trace
    target.action_requests = repair.action_requests
    target.source_labels = repair.source_labels
    target.used_cross_record_context = repair.used_cross_record_context
    target.tool_call_count = repair.tool_call_count
    target.latest_record_context = repair.latest_record_context
    target.latest_record_insights = repair.latest_record_insights
    target.latest_article_overview = repair.latest_article_overview
    target.latest_external_record_contexts = repair.latest_external_record_contexts
    target.latest_external_asset_contexts = repair.latest_external_asset_contexts
    target.latest_user_vocabulary = repair.latest_user_vocabulary
    target.latest_resolved_references = repair.latest_resolved_references
    target.latest_generated_annotations = repair.latest_generated_annotations
    target.latest_suggestions = repair.latest_suggestions


_TRACE_SUMMARY_METRIC_KEYS = (
    "planner_mode",
    "working_set_mode",
    "cross_record_context_allowed",
    "cross_record_context_used",
    "used_known_reference_resolution",
    "used_external_record_context",
    "used_structured_asset_lookup",
    "used_hitp_disambiguation",
    "used_external_asset_context",
    "used_external_asset_disambiguation",
)


def _merge_eval_metrics_json(
    *,
    existing_metrics: dict[str, Any],
    next_metrics: dict[str, Any],
    trace_summary: ReaderAskTraceSummary | None,
    billed_points: int,
    usage_event_id: UUID | None,
) -> dict[str, Any]:
    if not existing_metrics:
        return next_metrics

    merged = {**existing_metrics, **next_metrics}
    if trace_summary is None:
        for key in _TRACE_SUMMARY_METRIC_KEYS:
            if key in existing_metrics:
                merged[key] = existing_metrics[key]
    if billed_points == 0 and usage_event_id is None and "billed_points" in existing_metrics:
        merged["billed_points"] = existing_metrics["billed_points"]
    if usage_event_id is None and "usage_event_id" in existing_metrics:
        merged["usage_event_id"] = existing_metrics["usage_event_id"]
    return merged


async def _upsert_eval_trace_record(
    *,
    turn_run_id: UUID,
    planning_snapshot: planner.ReaderAskPlanningSnapshot | None,
    runtime_state: ReaderAskRuntimeState,
    context_plan: ReaderAskContextPlan | None,
    action_audit_json: list[dict[str, Any]] | None = None,
    supplement_audit_json: list[dict[str, Any]] | None = None,
    trace_summary: ReaderAskTraceSummary | None = None,
    billed_points: int = 0,
    usage_event_id: UUID | None = None,
) -> dict[str, Any]:
    existing = await repo.get_eval_trace(turn_run_id)
    existing_metrics = (existing or {}).get("metrics_json") or {}
    next_metrics = _merge_eval_metrics_json(
        existing_metrics=existing_metrics,
        next_metrics=_metrics_json(
            trace_summary=trace_summary,
            billed_points=billed_points,
            usage_event_id=usage_event_id,
            planner_route=runtime_state.planner_route_used,
            degenerate_detected=runtime_state.degenerate_detected,
            degenerate_reason=runtime_state.degenerate_reason,
            runtime_state=runtime_state,
        ),
        trace_summary=trace_summary,
        billed_points=billed_points,
        usage_event_id=usage_event_id,
    )
    return await repo.upsert_eval_trace(
        turn_run_id=turn_run_id,
        trace_schema_version=_EVAL_TRACE_SCHEMA_VERSION,
        planning_snapshot_json=_planning_snapshot_json(planning_snapshot, planner_route_used=runtime_state.planner_route_used) or (existing or {}).get("planning_snapshot_json") or {},
        capability_trace_json=_capability_trace_json(runtime_state=runtime_state, context_plan=context_plan)
        if context_plan is not None or runtime_state.source_labels
        else (existing or {}).get("capability_trace_json") or {},
        action_audit_json=action_audit_json if action_audit_json is not None else (existing or {}).get("action_audit_json") or [],
        supplement_audit_json=supplement_audit_json
        if supplement_audit_json is not None
        else (existing or {}).get("supplement_audit_json") or [],
        metrics_json=next_metrics,
    )



def _normalize_persisted_supplements(items: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items or []:
        supplement_id = str(item.get("supplement_id") or "").strip()
        if not supplement_id or supplement_id in seen:
            continue
        seen.add(supplement_id)
        normalized.append(dict(item))
    return normalized


def _upsert_persisted_supplement(
    items: list[dict[str, Any]] | None,
    supplement: ReaderAskPersistedSupplement,
) -> list[dict[str, Any]]:
    supplement_json = supplement.model_dump(mode="json")
    next_items = _normalize_persisted_supplements(items)
    for index, item in enumerate(next_items):
        if item.get("supplement_id") == supplement.supplement_id:
            next_items[index] = supplement_json
            return next_items
    next_items.append(supplement_json)
    return next_items


def _mark_deleted_persisted_supplement(
    items: list[dict[str, Any]] | None,
    supplement: ReaderAskPersistedSupplement,
) -> list[dict[str, Any]]:
    supplement_json = supplement.model_dump(mode="json")
    next_items = _normalize_persisted_supplements(items)
    for index, item in enumerate(next_items):
        if item.get("supplement_id") == supplement.supplement_id:
            next_items[index] = supplement_json
            return next_items
    next_items.append(supplement_json)
    return next_items


def _new_run_info(
    *,
    turn_id: str | None = None,
    run_id: str | None = None,
    attempt: int = 1,
    supersedes_run_id: str | None = None,
) -> dict[str, Any]:
    return {
        "turn_id": turn_id or str(uuid4()),
        "run_id": run_id or str(uuid4()),
        "run_attempt": max(attempt, 1),
        "supersedes_run_id": supersedes_run_id,
    }


def _next_run_info(message_dict: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    current = message_dict.get("run_info")
    history = list(message_dict.get("run_history") or [])
    current_turn_run = message_dict.get("current_turn_run")
    if isinstance(current_turn_run, dict) and current_turn_run.get("id"):
        if current is not None:
            history.append(current)
        return (
            _new_run_info(
                turn_id=str(current_turn_run.get("turn_id") or (current or {}).get("turn_id") or str(uuid4())),
                attempt=int(current_turn_run.get("run_attempt") or 1) + 1,
                supersedes_run_id=str(current_turn_run.get("id")),
            ),
            history,
        )
    if current is not None:
        history.append(current)
    if current is None:
        return _new_run_info(), history
    return (
        _new_run_info(
            turn_id=current.get("turn_id"),
            attempt=int(current.get("run_attempt") or 1) + 1,
            supersedes_run_id=current.get("run_id"),
        ),
        history,
    )


def _extract_sentence_analysis_parts(content: str) -> tuple[str | None, list[ReaderAskSentenceBreakdownPart]]:
    normalized = content.replace("\r\n", "\n").strip()
    if not normalized:
        return None, []
    analysis_lines: list[str] = []
    parts: list[ReaderAskSentenceBreakdownPart] = []
    chunk_re = re.compile(r"^- \*\*(?:\d+\.\s*)?([^*]+?)\*\*：`(.+?)`$")
    for line in normalized.split("\n"):
        stripped = line.strip()
        match = chunk_re.match(stripped)
        if match:
            parts.append(
                ReaderAskSentenceBreakdownPart(
                    label=match.group(1).strip(),
                    text=match.group(2).strip(),
                )
            )
            continue
        if stripped:
            analysis_lines.append(stripped)
    analysis_text = "\n".join(analysis_lines).strip() or None
    return analysis_text, parts


def _sentence_analysis_card(
    *,
    record: _RecordBundle,
    anchors: list[ReaderAskAnchorRef],
    runtime_state: ReaderAskRuntimeState,
) -> ReaderAskSentenceBreakdownCard | None:
    generated = next(
        (item for item in runtime_state.latest_generated_annotations if item.get("kind") == "sentence_analysis"),
        None,
    )
    if generated is not None:
        sentence_id = str(generated.get("sentence_id") or anchors[0].sentence_id or "")
        parts = [
            ReaderAskSentenceBreakdownPart(
                label=str(chunk.get("label") or ""),
                text=str(chunk.get("text") or ""),
            )
            for chunk in generated.get("chunks") or []
            if isinstance(chunk, dict) and str(chunk.get("label") or "").strip() and str(chunk.get("text") or "").strip()
        ]
        if not parts:
            return None
        analysis_text = str(generated.get("analysis_zh") or generated.get("content") or "").strip() or None
    else:
        insights = runtime_state.latest_record_insights
        if not insights:
            return None
        analysis_entry = next((item for item in insights if item.get("entry_type") == "sentence_analysis"), None)
        if not analysis_entry:
            return None
        analysis_text, parts = _extract_sentence_analysis_parts(str(analysis_entry.get("content") or ""))
        if not parts:
            return None
        sentence_id = str(analysis_entry.get("sentence_id") or anchors[0].sentence_id or "")

    sentence_text = _render_scene_sentence_text(record, sentence_id) or _first_anchor_text(anchors[0])
    if not sentence_text:
        return None
    translations = _translations_map(record)
    main_clause = parts[0].text if parts else None
    return ReaderAskSentenceBreakdownCard(
        sentence_text=sentence_text,
        translation_zh=translations.get(sentence_id),
        main_clause=main_clause,
        analysis_zh=analysis_text,
        parts=parts,
    )


def _grammar_note_card(runtime_state: ReaderAskRuntimeState) -> ReaderAskGrammarNoteCard | None:
    generated = next(
        (
            item
            for item in runtime_state.latest_generated_annotations
            if item.get("kind") == "grammar_note" and item.get("status") == "ready"
        ),
        None,
    )
    if generated is None:
        return None
    sentence_text = str(generated.get("source_sentence") or "").strip()
    focus_text = str(generated.get("focus_text") or "").strip() or sentence_text
    label = str(generated.get("label") or "").strip()
    note_zh = str(generated.get("note_zh") or generated.get("content") or "").strip()
    analysis_scope = str(generated.get("analysis_scope") or "full_sentence")
    if not sentence_text or not focus_text or not label or not note_zh:
        return None
    spans = [
        ReaderAskGrammarNoteCardSpan(
            text=str(span.get("text") or "").strip(),
            role=str(span.get("role") or "").strip() or None,
        )
        for span in generated.get("spans") or []
        if isinstance(span, dict) and str(span.get("text") or "").strip()
    ]
    return ReaderAskGrammarNoteCard(
        sentence_text=sentence_text,
        focus_text=focus_text,
        label=label,
        note_zh=note_zh,
        spans=spans,
        analysis_scope="focus_span" if analysis_scope == "focus_span" else "full_sentence",
    )


def _build_response_cards(
    *,
    task_mode: ReaderAskTaskMode,
    record: _RecordBundle,
    anchors: list[ReaderAskAnchorRef],
    runtime_state: ReaderAskRuntimeState,
) -> list[ReaderAskResponseCard]:
    cards: list[ReaderAskResponseCard] = []
    if task_mode == "grammar":
        card = _grammar_note_card(runtime_state)
        if card is not None:
            cards.append(card)
    elif task_mode == "breakdown":
        card = _sentence_analysis_card(record=record, anchors=anchors, runtime_state=runtime_state)
        if card is not None:
            cards.append(card)
    return cards


def _build_supplement_candidates_from_runtime(
    *,
    resolved_intent: ReaderAskResolvedIntent,
    anchors: list[ReaderAskAnchorRef],
    runtime_state: ReaderAskRuntimeState,
    assistant_content_md: str,
    created_from_turn_run_id: str,
) -> list[ReaderAskSupplementCandidate]:
    generated_grammar_note = next(
        (
            item
            for item in runtime_state.latest_generated_annotations
            if item.get("kind") == "grammar_note" and item.get("status") == "ready"
        ),
        None,
    )
    if generated_grammar_note is not None and anchors:
        content = str(generated_grammar_note.get("content") or "").strip()
        if content:
            candidate = supplements_svc.build_grammar_note_candidate(
                anchor=anchors[0],
                assistant_content_md=content,
                created_from_turn_run_id=created_from_turn_run_id,
            )
            return [candidate] if candidate is not None else []
    return capabilities_svc.build_supplement_candidates(
        resolved_intent=resolved_intent,
        anchors=anchors,
        assistant_content_md=assistant_content_md,
        created_from_turn_run_id=created_from_turn_run_id,
    )


def _tool_trace_entry(
    *,
    tool_name: str,
    status: Literal["started", "completed", "failed"],
    summary: str | None = None,
) -> ReaderAskToolTraceEntry:
    now = datetime.now(UTC).isoformat()
    if status == "started":
        return ReaderAskToolTraceEntry(tool_name=tool_name, status=status, started_at=now)
    return ReaderAskToolTraceEntry(
        tool_name=tool_name,
        status=status,
        started_at=now,
        completed_at=now,
        summary=summary,
    )


async def _run_explicit_quick_action_annotation(
    *,
    submission_mode: ReaderAskSubmissionMode,
    task_mode: ReaderAskTaskMode,
    entry_action: ReaderAskEntryAction,
    record: _RecordBundle,
    primary_anchor: ReaderAskAnchorRef | None,
    runtime_state: ReaderAskRuntimeState,
    event_queue: asyncio.Queue[tuple[str, dict[str, Any]]] | None = None,
) -> dict[str, Any] | None:
    if submission_mode != "quick_action":
        return None
    kind = planner_runtime_svc.annotation_quick_action_kind(task_mode, entry_action)
    if kind is None or primary_anchor is None:
        return None

    runtime_state.tool_trace.append(_tool_trace_entry(tool_name="generate_sentence_annotation", status="started"))
    if event_queue is not None:
        await event_queue.put((stream_events_svc.EVENT_TOOL_STARTED, stream_events_svc.tool_started_payload("generate_sentence_annotation")))
    generated = await _generate_sentence_annotation(record=record, anchor=primary_anchor, kind=kind)
    if generated is not None:
        runtime_state.latest_generated_annotations.append(generated)
        if generated.get("status") == "ready":
            runtime_state.source_labels.add("record_assets")
    summary = (
        "Generated structured analysis"
        if generated and generated.get("status") == "ready"
        else str(generated.get("reason") or "No stable structured analysis") if generated
        else "No stable structured analysis"
    )
    runtime_state.tool_trace.append(
        _tool_trace_entry(tool_name="generate_sentence_annotation", status="completed", summary=summary)
    )
    if event_queue is not None:
        await event_queue.put((stream_events_svc.EVENT_TOOL_COMPLETED, stream_events_svc.tool_completed_payload("generate_sentence_annotation", summary)))
    return generated


def _build_action_proposals(
    *,
    user_message: str,
    record: _RecordBundle,
    anchors: list[ReaderAskAnchorRef],
    assistant_content_md: str,
) -> list[ReaderAskActionProposal]:
    # DEPRECATED: action proposals 已迁移至 agent runtime 生成（见 _build_action_proposals_from_runtime）。
    # 此函数保留仅为兼容性占位，后续版本应删除。
    del user_message, record, anchors, assistant_content_md
    return []


def _build_action_proposals_from_runtime(
    *,
    record: _RecordBundle,
    action_requests: list[ReaderAskRuntimeActionRequest],
    assistant_content_md: str,
) -> list[ReaderAskActionProposal]:
    proposals: list[ReaderAskActionProposal] = []
    for request in action_requests:
        payload_json = dict(request.payload_json)
        proposals.append(
            ReaderAskActionProposal(
                id=str(uuid4()),
                action_type=request.action_type,
                label=request.label,
                description=request.description,
                requires_confirmation=request.requires_confirmation,
                payload_json={
                    "record_id": str(record.record_id),
                    **payload_json,
                },
            )
        )
    return proposals


def _build_supplement_action_proposals(
    candidates: list[dict[str, Any]],
) -> list[ReaderAskActionProposal]:
    proposals: list[ReaderAskActionProposal] = []
    for candidate in candidates:
        proposals.append(
            ReaderAskActionProposal(
                id=str(uuid4()),
                action_type="create_supplement_grammar_note",
                label="加入当前页补充",
                description="把这条 AI 语法旁注加入当前文章，并固定显示在对应句子下。",
                payload_json={"candidate": candidate},
            )
        )
    return proposals


def _merge_action_proposals(
    runtime_proposals: list[ReaderAskActionProposal],
    fallback_proposals: list[ReaderAskActionProposal],
) -> list[ReaderAskActionProposal]:
    merged = list(runtime_proposals)
    seen = {
        (
            proposal.action_type,
            proposal.payload_json.get("anchor", {}).get("target_key"),
            proposal.payload_json.get("anchor", {}).get("sentence_id"),
        )
        for proposal in runtime_proposals
    }
    for proposal in fallback_proposals:
        signature = (
            proposal.action_type,
            proposal.payload_json.get("anchor", {}).get("target_key"),
            proposal.payload_json.get("anchor", {}).get("sentence_id"),
        )
        if signature in seen:
            continue
        merged.append(proposal)
    return merged


def _merge_usage_summaries(base_usage: dict[str, Any] | None, extra_usages: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not base_usage and not extra_usages:
        return None

    aggregate = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
    }
    details: dict[str, Any] = {"subtasks": []}

    def add_usage(item: dict[str, Any] | None, *, tool_name: str | None = None) -> None:
        if not item:
            return
        current = item.get("aggregate") if isinstance(item.get("aggregate"), dict) else item
        aggregate["input_tokens"] += int(current.get("input_tokens") or 0)
        aggregate["output_tokens"] += int(current.get("output_tokens") or 0)
        aggregate["total_tokens"] += int(current.get("total_tokens") or 0)
        if tool_name:
            details["subtasks"].append({"tool_name": tool_name, **current})

    add_usage(base_usage)
    for item in extra_usages:
        tool_name = str(item.get("tool_name") or "tool")
        usage = item.get("usage_summary")
        if isinstance(usage, dict):
            add_usage(usage, tool_name=tool_name)

    return {"aggregate": aggregate, **details}







def _build_evidence_items(
    *,
    attachments: list[ReaderAskAttachment],
    citations: list[ReaderAskCitation],
    current_record_id: str | None = None,
    current_record_title: str | None = None,
    external_record_contexts: list[dict[str, Any]] | None = None,
    external_asset_contexts: list[dict[str, Any]] | None = None,
    reference_resolution: planner.ReaderAskReferenceResolution | None = None,
    supplement_candidates: list[ReaderAskSupplementCandidate] | None = None,
    disambiguation: ReaderAskDisambiguation | None = None,
    external_asset_disambiguation: ReaderAskAssetDisambiguation | None = None,
    include_clarification: bool = False,
) -> list[ReaderAskEvidenceItem]:
    return post_process_svc.build_evidence_items(
        attachments=attachments,
        citations=citations,
        current_record_id=current_record_id,
        current_record_title=current_record_title,
        external_record_contexts=external_record_contexts,
        external_asset_contexts=external_asset_contexts,
        reference_resolution=reference_resolution,
        supplement_candidates=supplement_candidates,
        disambiguation=disambiguation,
        external_asset_disambiguation=external_asset_disambiguation,
        include_clarification=include_clarification,
    )


def _selected_model_payload(
    option: model_options_svc.ResolvedReaderAskModelOption,
) -> dict[str, Any]:
    return ReaderAskSelectedModel(
        key=option.key,
        label=option.label,
        description=option.description,
        model_name=option.main_model_name,
        replan_model_name=option.replan_model_name,
        price_multiplier=option.billing.price_multiplier,
    ).model_dump(mode="json")


def _model_option_summary_payload(
    option: model_options_svc.ResolvedReaderAskModelOption,
) -> dict[str, Any]:
    return ReaderAskModelOptionSummary(
        **_selected_model_payload(option),
        is_default=option.is_default,
    ).model_dump(mode="json")


def _thread_summary_payload(thread: dict[str, Any]) -> dict[str, Any]:
    option = model_options_svc.resolve_reader_ask_model_option(
        get_settings(),
        cast(str | None, thread.get("selected_model_key")),
        strict=False,
    )
    return {
        **thread,
        "selected_model": _selected_model_payload(option),
    }


def _runtime_budget_kwargs(
    option: model_options_svc.ResolvedReaderAskModelOption,
) -> dict[str, int]:
    return {
        "max_input_tokens": option.runtime_budget.max_input_tokens,
        "max_output_tokens": option.runtime_budget.max_output_tokens,
        "prompt_buffer_tokens": option.runtime_budget.prompt_buffer_tokens,
    }


async def _settle_reader_ask_reservation(
    *,
    user_id: UUID,
    reservation: CreditReservation,
    actual_cost_points: int,
    metadata: dict[str, Any],
) -> tuple[int, int]:
    if actual_cost_points <= 0:
        unused = recovery_svc.build_unused_reservation(reservation, 0)
        if unused.total_points > 0:
            await refund_reserved_points(user_id, unused, metadata=metadata)
        return 0, 0

    if actual_cost_points <= reservation.total_points:
        unused = recovery_svc.build_unused_reservation(reservation, actual_cost_points)
        if unused.total_points > 0:
            await refund_reserved_points(user_id, unused, metadata=metadata)
        return actual_cost_points, 0

    extra_needed = actual_cost_points - reservation.total_points
    extra_deducted = await deduct_points(
        user_id,
        extra_needed,
        entry_type=LEDGER_ENTRY_TYPE_AI_CAPABILITY_DEDUCT,
        metadata=metadata,
    )
    return reservation.total_points + extra_deducted, max(extra_needed - extra_deducted, 0)


def _reader_ask_model_metadata(
    option: model_options_svc.ResolvedReaderAskModelOption,
) -> dict[str, Any]:
    selection = option.selection
    return {
        "ask_model_option_key": option.key,
        "ask_model_option_label": option.label,
        "ask_model_price_multiplier": option.billing.price_multiplier,
        "ask_model_preset": selection.preset if selection is not None else None,
        "ask_model_used_fallback": option.used_fallback,
        "ask_model_requested_key": option.requested_key,
        "ask_runtime_max_input_tokens": option.runtime_budget.max_input_tokens,
        "ask_runtime_max_output_tokens": option.runtime_budget.max_output_tokens,
        "ask_runtime_prompt_buffer_tokens": option.runtime_budget.prompt_buffer_tokens,
    }


def _resolve_reader_ask_model_option_or_422(
    *,
    selected_key: str | None,
    strict: bool,
) -> model_options_svc.ResolvedReaderAskModelOption:
    try:
        return model_options_svc.resolve_reader_ask_model_option(
            get_settings(),
            selected_key,
            strict=strict,
        )
    except model_options_svc.ReaderAskModelOptionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


async def _resolve_thread_model_option(
    *,
    user_id: UUID,
    thread_id: UUID,
    thread: dict[str, Any],
    requested_key: str | None,
) -> tuple[dict[str, Any], model_options_svc.ResolvedReaderAskModelOption]:
    requested = requested_key or None
    current_key = cast(str | None, thread.get("selected_model_key"))
    selected_key = requested or current_key
    option = _resolve_reader_ask_model_option_or_422(
        selected_key=selected_key,
        strict=requested is not None,
    )
    should_persist = (
        requested is not None
        or option.used_fallback
        or current_key is None
    ) and current_key != option.key
    if should_persist:
        updated_thread = await repo.update_thread_selected_model(
            user_id,
            thread_id,
            selected_model_key=option.key,
        )
        if updated_thread is not None:
            thread = updated_thread
    return thread, option


async def list_threads(user_id: UUID, record_id: str) -> ReaderAskThreadListResponse:
    record_uuid = _parse_uuid(record_id, "record_id must be a UUID")
    await repo.ensure_record_access(user_id, record_uuid)
    items = await repo.list_threads(user_id, record_uuid)
    return ReaderAskThreadListResponse(
        items=[ReaderAskThreadSummary.model_validate(_thread_summary_payload(item)) for item in items]
    )


async def list_model_options() -> ReaderAskModelOptionListResponse:
    items, default_key = model_options_svc.list_reader_ask_model_options(get_settings())
    return ReaderAskModelOptionListResponse(
        default_key=default_key,
        items=[ReaderAskModelOptionSummary.model_validate(_model_option_summary_payload(item)) for item in items],
    )


async def list_context_records(
    user_id: UUID,
    *,
    query: str,
    exclude_record_id: str | None = None,
) -> ReaderAskContextRecordSearchResponse:
    normalized_query = query.strip()
    exclude_uuid = _parse_uuid(exclude_record_id, "exclude_record_id must be a UUID") if exclude_record_id else None
    rows = (
        await repo.search_records_by_title(
            user_id,
            query=normalized_query,
            exclude_record_id=exclude_uuid,
            limit=8,
        )
        if normalized_query
        else await repo.list_recent_records(
            user_id,
            exclude_record_id=exclude_uuid,
            limit=6,
        )
    )
    return ReaderAskContextRecordSearchResponse(
        items=[
            {
                "record_id": row["id"],
                "title": row.get("title"),
                "updated_at": row.get("updated_at"),
                "overview_hint": utils.truncate_text_optional(
                    (
                        overview := utils.resolve_record_overview(
                            render_scene=ensure_json_dict(row.get("render_scene_json")),
                            page_state_json=ensure_json_dict(row.get("page_state_json")),
                        )
                    ).get("overview"),
                    140,
                ),
                "overview_hint_status": overview.get("status"),
                "overview_hint_source": overview.get("source"),
            }
            for row in rows
        ]
    )


async def create_thread(user_id: UUID, body: ReaderAskThreadCreateRequest) -> ReaderAskThreadSummary:
    record_uuid = _parse_uuid(body.record_id, "record_id must be a UUID")
    record = await repo.ensure_record_access(user_id, record_uuid)
    selected_option = _resolve_reader_ask_model_option_or_422(
        selected_key=body.model,
        strict=body.model is not None,
    )
    thread = await repo.get_or_create_default_thread(
        user_id,
        record_uuid,
        title=body.title or record.get("title") or "Ask Claread",
        selected_model_key=selected_option.key if body.model is not None else None,
    )
    if thread.get("selected_model_key") is None:
        updated_thread = await repo.update_thread_selected_model(
            user_id,
            _parse_uuid(thread["id"], "thread id is invalid"),
            selected_model_key=selected_option.key,
        )
        if updated_thread is not None:
            thread = updated_thread
    return ReaderAskThreadSummary.model_validate(_thread_summary_payload(thread))


async def get_thread_detail(user_id: UUID, thread_id: UUID) -> ReaderAskThreadDetail:
    thread = await repo.get_thread(user_id, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Reader ask thread not found")
    messages = await repo.list_messages(thread_id, limit=100)
    return ReaderAskThreadDetail.model_validate({**_thread_summary_payload(thread), "messages": messages})


async def reset_thread(user_id: UUID, thread_id: UUID) -> ReaderAskThreadDetail:
    thread = await repo.get_thread(user_id, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Reader ask thread not found")

    record_id = _parse_uuid(thread["record_id"], "thread record_id is invalid")
    archived = await repo.archive_thread(user_id, thread_id)
    if archived is None:
        raise HTTPException(status_code=404, detail="Reader ask thread not found")
    next_thread_option = _resolve_reader_ask_model_option_or_422(
        selected_key=cast(str | None, thread.get("selected_model_key")),
        strict=False,
    )

    next_thread = await repo.get_or_create_default_thread(
        user_id,
        record_id,
        title=thread.get("title") or "Ask Claread",
        selected_model_key=next_thread_option.key,
    )
    messages = await repo.list_messages(_parse_uuid(next_thread["id"], "thread id is invalid"), limit=100)
    return ReaderAskThreadDetail.model_validate({**_thread_summary_payload(next_thread), "messages": messages})


async def _record_failure_event(
    *,
    user_id: UUID,
    record_id: UUID,
    thread_id: UUID,
    user_message: str,
    start_perf: float,
    error_code: str,
    error_message: str,
    metadata_json: dict[str, Any],
) -> None:
    await record_ai_usage_event(
        AIUsageEventCreate(
            usage_scope=USAGE_SCOPE_USER_BILLED,
            capability_code=CAPABILITY_READER_ASK,
            billing_mode=BILLING_MODE_USER_POINTS,
            status=STATUS_FAILED,
            user_id=user_id,
            record_id=record_id,
            workflow_name=_WORKFLOW_NAME,
            workflow_version=_WORKFLOW_VERSION,
            schema_version=_SCHEMA_VERSION,
            prompt_version=get_prompt_version(),
            latency_ms=int((perf_counter() - start_perf) * 1000),
            error_code=error_code,
            error_message=error_message,
            metadata_json={
                "entrypoint": "/reader-ask/threads/{thread_id}/messages/stream",
                "thread_id": str(thread_id),
                "user_message": _truncate_text(user_message, 200),
                **metadata_json,
            },
        )
    )


async def stream_thread_message(
    user_id: UUID,
    thread_id: UUID,
    body: ReaderAskMessageStreamRequest,
) -> AsyncIterator[str]:
    start_perf = perf_counter()
    thread: dict[str, Any] | None = None
    record: _RecordBundle | None = None
    history_messages: list[dict[str, Any]] = []
    attachments: list[ReaderAskAttachment] = []
    resolved_anchors: list[ReaderAskAnchorRef] = []
    anchor_payload: list[dict[str, Any]] = []
    reservation: CreditReservation | None = None
    user_message: dict[str, Any] | None = None
    assistant_message: dict[str, Any] | None = None
    runtime_state = ReaderAskRuntimeState()
    nested_tool_usages: list[dict[str, Any]] = []
    planner_usage_summary: dict[str, Any] | None = None
    resolved_intent: ReaderAskResolvedIntent | None = None
    resolved_context_input: ReaderAskResolvedContextInput | None = None
    context_plan: ReaderAskContextPlan | None = None
    evidence: list[ReaderAskEvidenceItem] = []
    trace_summary: ReaderAskTraceSummary | None = None
    run_info: dict[str, Any] | None = None
    active_turn_run_id: UUID | None = None
    planning_snapshot: planner.ReaderAskPlanningSnapshot | None = None
    disambiguation: ReaderAskDisambiguation | None = None
    external_asset_disambiguation: ReaderAskAssetDisambiguation | None = None
    reference_resolution = planner.ReaderAskReferenceResolution()
    selected_model_option: model_options_svc.ResolvedReaderAskModelOption | None = None
    final_content_md = ""
    stream_runtime: AgentStreamRuntime | None = None
    submission_mode: ReaderAskSubmissionMode = "chat"

    try:
        thread = await repo.get_thread(user_id, thread_id)
        if thread is None:
            raise HTTPException(status_code=404, detail="Reader ask thread not found")

        record_id = _parse_uuid(thread["record_id"], "thread record_id is invalid")
        record = await _load_record_bundle(user_id, record_id)
        history_messages = await repo.list_messages(thread_id, limit=100)
        if _parse_uuid(body.page_identity.record_id, "page_identity.record_id must be a UUID") != record.record_id:
            raise HTTPException(status_code=400, detail="page_identity.record_id does not match thread record")
        thread, selected_model_option = await _resolve_thread_model_option(
            user_id=user_id,
            thread_id=thread_id,
            thread=thread,
            requested_key=body.model,
        )
        runtime_budget_kwargs = _runtime_budget_kwargs(selected_model_option)

        attachments = body.attachments
        incoming_anchors = _attachments_to_anchor_refs(attachments)
        resolved_anchors = await _resolve_anchor_refs(
            user_id,
            record,
            anchors=incoming_anchors,
        )
        anchor_payload = [anchor.model_dump(mode="json") for anchor in resolved_anchors]

        await ensure_credit_account(user_id)
        remaining = await check_quota(user_id)
        if remaining < selected_model_option.billing.reserved_points:
            yield stream_events_svc.encode_sse(
                stream_events_svc.EVENT_ERROR,
                stream_events_svc.insufficient_credits_payload(
                    remaining,
                    required_points=selected_model_option.billing.reserved_points,
                ),
            )
            return

        reservation_metadata = {
            "capability_code": CAPABILITY_READER_ASK,
            "thread_id": str(thread_id),
            "record_id": str(record.record_id),
            **build_reader_ask_billing_metadata(None, selected_model_option.billing),
            **_reader_ask_model_metadata(selected_model_option),
            "user_message": _truncate_text(body.content, 200),
        }
        reservation = await reserve_points(
            user_id,
            selected_model_option.billing.reserved_points,
            task_id=None,
            entry_type=LEDGER_ENTRY_TYPE_AI_CAPABILITY_DEDUCT,
            metadata=reservation_metadata,
        )
        if reservation is None:
            remaining = await check_quota(user_id)
            yield stream_events_svc.encode_sse(
                stream_events_svc.EVENT_ERROR,
                stream_events_svc.insufficient_credits_payload(
                    remaining,
                    required_points=selected_model_option.billing.reserved_points,
                ),
            )
            return

        # Agent-loop is the only live route. ``planner_first`` survives only
        # as a historical trace value; service runtime no longer resolves a
        # planner route for new runs.
        runtime_state.planner_skipped = True
        runtime_state.planner_route_used = "agent_loop_first"
        resolved_intent, resolved_intent_label = (
            runtime_contract_svc.build_minimal_resolved_intent(body.entry_action)
        )
        planning_snapshot = None
        reference_resolution = None
        resolved_context_input = None
        disambiguation = None
        external_asset_disambiguation = None
        submission_mode = planner_runtime_svc.submission_mode(
            entry_action=body.entry_action, attachments=attachments
        )
        planner_usage_summary = None
        user_message = await repo.create_message(
            thread_id=thread_id,
            role="user",
            status="completed",
            content_md=body.content,
            context_anchors=anchor_payload,
            metadata=_user_message_metadata(
                resolved_intent=resolved_intent,
                resolved_context_input=resolved_context_input,
                submission_mode=submission_mode,
            ),
        )
        yield stream_events_svc.encode_sse(stream_events_svc.EVENT_THREAD_READY, stream_events_svc.thread_ready_payload(str(thread_id), str(record.record_id)))

        assistant_message = await repo.create_message(
            thread_id=thread_id,
            role="assistant",
            status="streaming",
            content_md="",
            context_anchors=anchor_payload,
            metadata=_assistant_message_metadata(
                resolved_intent=resolved_intent,
                resolved_context_input=resolved_context_input,
                submission_mode=submission_mode,
            ),
        )
        turn_run = await repo.create_turn_run(
            message_id=_parse_uuid(assistant_message["id"], "assistant message id is invalid"),
            thread_id=thread_id,
            user_id=user_id,
            record_id=record.record_id,
            turn_id=_parse_uuid(user_message["id"], "user message id is invalid"),
            run_attempt=1,
            supersedes_run_id=None,
            status="streaming",
            resolved_intent=resolved_intent,
        )
        active_turn_run_id = _parse_uuid(turn_run["id"], "turn run id is invalid")
        run_info = _build_run_info(turn_id=user_message["id"], run_id=turn_run["id"], attempt=1)
        assistant_message = await repo.update_message(
            message_id=_parse_uuid(assistant_message["id"], "assistant message id is invalid"),
            status="streaming",
            content_md="",
            context_anchors=anchor_payload,
            citations=[],
            action_proposals=[],
            tool_trace=[],
            metadata=_assistant_message_metadata(
                resolved_intent=resolved_intent,
                run_info=run_info,
                resolved_context_input=resolved_context_input,
                submission_mode=submission_mode,
            ),
            usage_event_id=None,
            current_turn_run_id=_parse_uuid(turn_run["id"], "turn run id is invalid"),
        )
        yield stream_events_svc.encode_sse(stream_events_svc.EVENT_MESSAGE_STARTED, stream_events_svc.message_started_payload(assistant_message["id"], user_message["id"]))

        base_citations = [
            _anchor_to_citation(anchor, record_id=str(record.record_id), record_title=record.title)
            for anchor in resolved_anchors
        ]
        # Preserve agent-loop-first telemetry across the runtime_state rebuild.
        _prev_planner_skipped = runtime_state.planner_skipped
        _prev_planner_route = runtime_state.planner_route_used
        runtime_state = ReaderAskRuntimeState(
            citations=list(base_citations),
            source_labels={"current_record", *({"current_anchor"} if resolved_anchors else set())},
            planner_skipped=_prev_planner_skipped,
            planner_route_used=_prev_planner_route,
        )
        query_seed = _query_seed(body.content, resolved_anchors)
        cross_record_context_allowed = runtime_state.cross_record_context_allowed

        resolved = resolve_reader_ask_agent(selected_model_option.selection)
        agent = resolved.agent
        model = resolved.model
        model_config = resolved.model_config

        route_settings = RunModelSettings(
            max_tokens=runtime_budget_kwargs["max_output_tokens"],
            temperature=cfg.AGENT_TEMPERATURE,
            timeout=cfg.AGENT_TIMEOUT_S,
        )
        if model_config and model_config.model_settings is not None:
            route_settings = route_settings.merged_with(model_config.model_settings)
        route_settings = route_settings.with_max_tokens(
            min(
                route_settings.max_tokens or runtime_budget_kwargs["max_output_tokens"],
                runtime_budget_kwargs["max_output_tokens"],
            )
        )

        event_queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
        primary_anchor = resolved_anchors[0] if resolved_anchors else None

        async def get_record_context_cb(
            _deps: Any = None,
            scope: str = "window",
            target_sentence_id: str | None = None,
        ) -> dict[str, Any]:
            return _build_record_context_payload(
                record,
                scope=scope,
                target_sentence_id=target_sentence_id,
            )

        async def get_record_insights_cb(
            _deps: Any = None,
            target_sentence_id: str | None = None,
            kind: str | None = None,
            limit: int = 5,
        ) -> list[dict[str, Any]]:
            return _collect_insight_entries(
                record,
                target_sentence_id=target_sentence_id,
                kind=kind,
                limit=limit,
            )

        async def get_user_vocabulary_book_cb(
            _deps: Any = None,
            lemma: str | None = None,
            limit: int = 10,
            sort_by: str = "recent",
        ) -> list[dict[str, Any]]:
            return await _tool_get_user_vocabulary_book(
                user_id,
                lemma=lemma,
                limit=limit,
                sort_by=sort_by,
            )

        async def resolve_known_reference_cb(
            _deps: Any = None,
            query: str = "",
            top_k: int = 5,
        ) -> dict[str, Any]:
            return await _tool_resolve_known_reference_for_agent(
                user_id=user_id,
                current_record_id=record.record_id,
                query=query,
                top_k=top_k,
            )

        async def load_explicit_attachment_context_cb(
            _deps: Any = None,
            record_id: str = "",
            asset_id: str | None = None,
        ) -> dict[str, Any]:
            return await _tool_load_explicit_attachment_context(
                user_id=user_id,
                current_record_id=record.record_id,
                record_id=record_id,
                asset_id=asset_id,
            )

        async def suggest_prompts_cb(
            suggestions: list[dict[str, Any]],
        ) -> dict[str, Any]:
            return await _tool_suggest_prompts(suggestions)

        async def generate_sentence_annotation_cb(
            kind: Literal["grammar_note", "sentence_analysis"],
        ) -> dict[str, Any] | None:
            # Cache check is handled at the agent tool layer (reader_ask_agent.py).
            # If we reach here, there is no pre-generated annotation of this kind.
            return await _generate_sentence_annotation(record=record, anchor=primary_anchor, kind=kind)

        quick_action_annotation: dict[str, Any] | None = None
        # Round 15: agent-loop-first is the only live route. The legacy
        # planner_first else-branch (materialize_planned_context with a
        # planning_snapshot) has been removed.
        resolved_context_input = context_runtime_svc.build_agent_loop_context(
            record=record,
            runtime_state=runtime_state,
            anchors=resolved_anchors,
            attachments=attachments,
            user_id=user_id,
            page_identity=body.page_identity,
            entry_action=body.entry_action,
            latest_user_message=body.content,
            cross_record_toggle=runtime_state.cross_record_context_allowed,
            history_messages=history_messages,
        )
        quick_action_annotation = await _run_explicit_quick_action_annotation(
            submission_mode=submission_mode,
            task_mode=resolved_intent,
            entry_action=body.entry_action,
            record=record,
            primary_anchor=primary_anchor,
            runtime_state=runtime_state,
            event_queue=event_queue,
        )
        if quick_action_annotation and isinstance(quick_action_annotation.get("usage_summary"), dict):
            nested_tool_usages.append(
                {
                    "tool_name": "generate_sentence_annotation",
                    "usage_summary": quick_action_annotation["usage_summary"],
                }
            )
        context_plan = planner.build_context_plan(
            entry_action=body.entry_action,
            attachments=attachments,
            anchors=resolved_anchors,
            runtime_state=runtime_state,
            citations=runtime_state.citations,
            reference_resolution=reference_resolution,
            planning_snapshot=planning_snapshot,
        )
        trace_summary = planner.build_trace_summary(
            runtime_state=runtime_state,
            context_plan=context_plan,
            planning_snapshot=planning_snapshot,
            clarification_mode="can_answer_with_followup" if runtime_state.deictic_clarification_hint else "none",
        )
        await _upsert_eval_trace_record(
            turn_run_id=_parse_uuid(turn_run["id"], "turn run id is invalid"),
            planning_snapshot=planning_snapshot,
            runtime_state=runtime_state,
            context_plan=context_plan,
            trace_summary=trace_summary,
        )
        prompt_payload = runtime_contract_svc.build_prompt_payload(
            runtime_contract_svc.ReaderAskAnswerRuntimeInput(
                thread=thread,
                record=record,
                user_message=body.content,
                history_messages=history_messages,
                page_identity=body.page_identity,
                attachments=attachments,
                anchors=resolved_anchors,
                resolved_intent=resolved_intent,
                resolved_intent_label=_TASK_MODE_LABELS[resolved_intent],
                entry_action=body.entry_action,
                submission_mode=submission_mode,
                cross_record_context_allowed=cross_record_context_allowed,
                resolved_context_input=resolved_context_input,
                quick_action_annotation=quick_action_annotation,
                reference_resolution=reference_resolution,
                planning_snapshot=planning_snapshot,
                followup_hint=runtime_state.deictic_clarification_hint,
                cross_record_intent_hint=runtime_state.cross_record_intent_hint,
                external_attachment_hint=runtime_state.external_attachment_hint,
                dictionary_anchor_hint=runtime_state.dictionary_anchor_hint,
                long_history_hint=runtime_state.long_history_hint,
                max_history_messages=cfg.MAX_HISTORY_MESSAGES,
                max_message_text=cfg.MAX_MESSAGE_TEXT,
            )
        )
        # Emit context.compacting *before* compression so the user sees
        # "上下文压缩中" while compaction is in progress, not after.
        _max_input_budget = prompt_preparation_svc.compute_max_input_budget(
            max_input_tokens=runtime_budget_kwargs["max_input_tokens"],
        )
        if prompt_preparation_svc.should_emit_compacting(prompt_payload, max_input_budget=_max_input_budget):
            yield stream_events_svc.encode_sse(stream_events_svc.EVENT_CONTEXT_COMPACTING, stream_events_svc.context_compacting_payload(assistant_message["id"]))
        prompt_payload, max_output_tokens, _compaction_audit, _context_too_large = prompt_preparation_svc.prepare_prompt_payload(
            prompt_payload,
            max_input_tokens=runtime_budget_kwargs["max_input_tokens"],
            budget_buffer_tokens=runtime_budget_kwargs["prompt_buffer_tokens"],
            default_max_output_tokens=route_settings.max_tokens or runtime_budget_kwargs["max_output_tokens"],
            min_max_output_tokens=cfg.MIN_MAX_OUTPUT_TOKENS,
        )
        if _context_too_large:
            cleanup_plan = recovery_svc.build_context_too_large_cleanup_plan(
                user_id=user_id,
                thread_id=thread_id,
                record_id=record.record_id if record else None,
                reservation=reservation,
                assistant_message_id=_parse_uuid(assistant_message["id"], "assistant message id is invalid"),
                active_turn_run_id=active_turn_run_id,
                runtime_state=runtime_state,
                resolved_intent=resolved_intent,
                resolved_context_input=resolved_context_input,
                run_info=run_info,
                submission_mode=submission_mode,
                anchor_payload=anchor_payload,
                error_code="reader_ask_failed",
                compaction_audit=_compaction_audit,
                trace_summary=trace_summary,
                build_message_metadata_cb=_assistant_message_metadata,
                build_turn_run_output_cb=_build_stream_checkpoint_output_json if active_turn_run_id and record else None,
                run_history=None,
                record_bundle=record,
                resolved_anchors=resolved_anchors,
                attachments=attachments,
                reference_resolution=reference_resolution,
                disambiguation=disambiguation,
                external_asset_disambiguation=external_asset_disambiguation,
                planning_snapshot=planning_snapshot,
                context_plan=context_plan,
                persisted_supplements_json=None,
                user_message_text=body.content,
                start_perf=start_perf,
                thread=thread,
            )
            # Execute cleanup plan
            if cleanup_plan.refund is not None:
                await refund_reserved_points(user_id, cleanup_plan.refund.reservation, metadata=cleanup_plan.refund.metadata)
            await repo.update_message(
                message_id=cleanup_plan.message_failed.message_id,
                status="failed",
                content_md=cleanup_plan.message_failed.content_md,
                context_anchors=anchor_payload,
                citations=[c.model_dump(mode="json") for c in runtime_state.citations],
                action_proposals=[],
                tool_trace=[e.model_dump(mode="json") for e in runtime_state.tool_trace],
                metadata=cleanup_plan.message_failed.metadata,
                usage_event_id=None,
                current_turn_run_id=cleanup_plan.message_failed.current_turn_run_id,
            )
            if cleanup_plan.turn_run_failed is not None:
                await repo.update_turn_run(
                    turn_run_id=cleanup_plan.turn_run_failed.turn_run_id,
                    status="failed",
                    resolved_intent=resolved_intent,
                    user_visible_output_json=cleanup_plan.turn_run_failed.user_visible_output_json,
                    failed_at=datetime.now(UTC),
                )
            if cleanup_plan.eval_trace is not None:
                await _upsert_eval_trace_record(
                    turn_run_id=cleanup_plan.eval_trace.turn_run_id,
                    planning_snapshot=cleanup_plan.eval_trace.planning_snapshot,
                    runtime_state=cleanup_plan.eval_trace.runtime_state,
                    context_plan=cleanup_plan.eval_trace.context_plan,
                    trace_summary=cleanup_plan.eval_trace.trace_summary,
                )
            if cleanup_plan.failure_event is not None:
                await _record_failure_event(
                    user_id=cleanup_plan.failure_event.user_id,
                    record_id=cleanup_plan.failure_event.record_id,
                    thread_id=cleanup_plan.failure_event.thread_id,
                    user_message=cleanup_plan.failure_event.user_message,
                    start_perf=cleanup_plan.failure_event.start_perf,
                    error_code=cleanup_plan.failure_event.error_code,
                    error_message=cleanup_plan.failure_event.error_message,
                    metadata_json=cleanup_plan.failure_event.metadata_json,
                )
            yield stream_events_svc.encode_sse(stream_events_svc.EVENT_ERROR, stream_events_svc.context_too_large_payload())
            return
        trace_summary = prompt_preparation_svc.inject_compaction_audit(trace_summary, _compaction_audit)
        route_settings = route_settings.with_max_tokens(
            min(route_settings.max_tokens or cfg.DEFAULT_MAX_OUTPUT_TOKENS, max_output_tokens)
        )

        deps = build_reader_ask_agent_deps(
            payload=prompt_payload,
            event_queue=event_queue,
            state=runtime_state,
            query_seed=query_seed,
            task_mode=resolved_intent,
            entry_action=body.entry_action,
            record_id=str(record.record_id),
            record_title=record.title,
            primary_anchor=primary_anchor,
            get_record_context_fn=get_record_context_cb,
            get_record_insights_fn=get_record_insights_cb,
            get_user_vocabulary_book_fn=get_user_vocabulary_book_cb,
            resolve_known_reference_fn=resolve_known_reference_cb,
            load_explicit_attachment_context_fn=load_explicit_attachment_context_cb,
            allowed_external_attachments=_build_allowed_external_attachments(attachments),
            generate_sentence_annotation_fn=generate_sentence_annotation_cb,
            suggest_prompts_fn=suggest_prompts_cb,
            vocabulary_item_to_citation_fn=_vocabulary_item_to_citation,
        )
        checkpoint = stream_checkpoint_svc.TurnRunStreamCheckpoint(
            turn_run_id=active_turn_run_id,
            build_output_json=lambda content_md, reasoning_md, reasoning_status: _build_stream_checkpoint_output_json(
                content_md=content_md,
                reasoning_md=reasoning_md,
                reasoning_status=reasoning_status,
                submission_mode=submission_mode,
                resolved_intent=resolved_intent,
                record=record,
                anchors=resolved_anchors,
                attachments=attachments,
                runtime_state=runtime_state,
                reference_resolution=reference_resolution,
                disambiguation=disambiguation,
                external_asset_disambiguation=external_asset_disambiguation,
                trace_summary=trace_summary,
                context_plan=context_plan,
                resolved_context_input=resolved_context_input,
                run_info=run_info,
                persisted_supplements=[],
            ),
            update_turn_run_cb=repo.update_turn_run,
        )
        # Round 6: record run start time for latency tracking
        runtime_state.run_started_at = datetime.now(UTC).isoformat()
        async for stream_item in stream_reader_ask_agent_run(
            agent=agent,
            deps=deps,
            model=model,
            route_settings=route_settings,
            assistant_message_id=assistant_message["id"],
            model_config=model_config,
            checkpoint_flush=stream_checkpoint_svc.make_checkpoint_flush(checkpoint),
        ):
            if isinstance(stream_item, ReaderAskStreamSseEvent):
                yield stream_item.encoded_sse
            elif isinstance(stream_item, ReaderAskStreamCompleted):
                stream_outcome = stream_item.outcome
                final_content_md = stream_outcome.content_md
                usage_summary = stream_outcome.usage_summary
                stream_runtime = stream_item.stream_runtime

        # Round 14: agent-loop repair — when the main answer is
        # degenerate, attempt a single agent-loop repair (re-run answer
        # agent with repair hint) instead of falling back to a planner.
        _agent_loop_repair_eligible = (
            runtime_state.planner_route_used == "agent_loop_first"
            and is_degenerate_answer(final_content_md)
            and not stream_outcome.interrupted
        )
        if _agent_loop_repair_eligible:
            runtime_state.degenerate_detected = True
            runtime_state.degenerate_reason = "degenerate_answer"
            runtime_state.repair_attempted = True
            runtime_state.repair_reason = runtime_state.degenerate_reason
            runtime_state.repair_route = "agent_loop_repair"
            try:
                repair_content, repair_runtime_state = await _run_agent_loop_repair(
                    user_id=user_id,
                    record=record,
                    body=body,
                    attachments=attachments,
                    resolved_anchors=resolved_anchors,
                    history_messages=history_messages,
                    thread=thread,
                    runtime_state=runtime_state,
                    primary_anchor=primary_anchor,
                    submission_mode=submission_mode,
                    resolved_intent=resolved_intent,
                    resolved_context_input=resolved_context_input,
                    reference_resolution=reference_resolution,
                    disambiguation=disambiguation,
                    external_asset_disambiguation=external_asset_disambiguation,
                    trace_summary=trace_summary,
                    context_plan=context_plan,
                    run_info=run_info,
                    route_settings=route_settings,
                    model_selection=selected_model_option.selection,
                    runtime_budget_kwargs=runtime_budget_kwargs,
                    event_queue=event_queue,
                    query_seed=query_seed,
                    get_record_context_cb=get_record_context_cb,
                    get_record_insights_cb=get_record_insights_cb,
                    get_user_vocabulary_book_cb=get_user_vocabulary_book_cb,
                    resolve_known_reference_cb=resolve_known_reference_cb,
                    load_explicit_attachment_context_cb=load_explicit_attachment_context_cb,
                    generate_sentence_annotation_cb=generate_sentence_annotation_cb,
                    suggest_prompts_cb=suggest_prompts_cb,
                    degenerate_content_md=final_content_md,
                )
                if repair_content and not is_degenerate_answer(repair_content):
                    final_content_md = repair_content
                    runtime_state.repair_succeeded = True
                    # Merge evidence-producing fields (citations, tool_trace,
                    # suggestions, etc.) from the repair run so the completed
                    # payload reflects the repair's tool calls, not the stale
                    # evidence from the degenerate run.
                    _merge_repair_runtime_state(runtime_state, repair_runtime_state)
                    logger.info(
                        "reader_ask_agent_loop_repair_succeeded: repair produced non-degenerate answer (%d chars)",
                        len(repair_content),
                    )
                else:
                    runtime_state.repair_succeeded = False
                    logger.warning(
                        "reader_ask_agent_loop_repair_failed: repair still degenerate (%d chars), using original",
                        len(repair_content or ""),
                    )
            except Exception:
                runtime_state.repair_succeeded = False
                logger.warning(
                    "reader_ask_agent_loop_repair_exception: repair raised, using original answer",
                    exc_info=True,
                )

        # Round 15: the legacy bounded-replan block (which called
        # resolve_semantic_planning) has been removed. For agent_loop_first,
        # build_replan_event always returns None, so the replan path was
        # unreachable dead code. Degenerate answers are now handled by the
        # agent-loop repair above.

        runtime_proposals = _build_action_proposals_from_runtime(
            record=record,
            action_requests=runtime_state.action_requests,
            assistant_content_md=final_content_md,
        )
        action_proposals = _merge_action_proposals(runtime_proposals, [])
        extra_usage_summaries = list(nested_tool_usages)
        if planner_usage_summary:
            extra_usage_summaries.insert(0, {"tool_name": "semantic_planner", "usage_summary": planner_usage_summary})
        usage_summary = _merge_usage_summaries(usage_summary, extra_usage_summaries)
        response_cards = _build_response_cards(
            task_mode=resolved_intent,
            record=record,
            anchors=resolved_anchors,
            runtime_state=runtime_state,
        )
        resolved_context = planner.build_resolved_context_summary(
            record_id=str(record.record_id),
            record_title=record.title,
            anchors=resolved_anchors,
            explicit_attachment_count=len(attachments),
            runtime_state=runtime_state,
            used_cross_record_context=runtime_state.used_cross_record_context,
            citations=runtime_state.citations,
        )
        typed_supplement_candidates = _build_supplement_candidates_from_runtime(
            resolved_intent=resolved_intent,
            anchors=resolved_anchors,
            runtime_state=runtime_state,
            assistant_content_md=final_content_md,
            created_from_turn_run_id=str(run_info["run_id"]) if run_info is not None else str(uuid4()),
        )
        supplement_candidates = [candidate.model_dump(mode="json") for candidate in typed_supplement_candidates]
        action_proposals = [
            *action_proposals,
            *_build_supplement_action_proposals(supplement_candidates),
        ]
        evidence = _build_evidence_items(
            attachments=attachments,
            citations=runtime_state.citations,
            current_record_id=str(record.record_id),
            current_record_title=record.title,
            external_record_contexts=runtime_state.latest_external_record_contexts,
            external_asset_contexts=runtime_state.latest_external_asset_contexts,
            reference_resolution=reference_resolution,
            supplement_candidates=typed_supplement_candidates,
            disambiguation=disambiguation,
            external_asset_disambiguation=external_asset_disambiguation,
        )
        trace_summary = trace_summary.model_copy(
            update={
                "supplement_generation_used": bool(typed_supplement_candidates),
                "supplement_persisted_count": 0,
                "supplement_deleted_count": 0,
            }
        )

        computed_cost_points = compute_reader_ask_cost_points(
            usage_summary,
            selected_model_option.billing,
        )
        billed_points, under_collected_points = await _settle_reader_ask_reservation(
            user_id=user_id,
            reservation=reservation,
            actual_cost_points=computed_cost_points,
            metadata={
                "reason": "reader_ask_settlement",
                "thread_id": str(thread_id),
                "record_id": str(record.record_id),
                "computed_cost_points": computed_cost_points,
                **_reader_ask_model_metadata(selected_model_option),
            },
        )
        reservation = CreditReservation(total_points=0, deducted_from_daily=0, deducted_from_bonus=0)

        usage_event_id = await record_ai_usage_event(
            AIUsageEventCreate(
                usage_scope=USAGE_SCOPE_USER_BILLED,
                capability_code=CAPABILITY_READER_ASK,
                billing_mode=BILLING_MODE_USER_POINTS,
                status=STATUS_SUCCEEDED,
                user_id=user_id,
                record_id=record.record_id,
                workflow_name=_WORKFLOW_NAME,
                workflow_version=_WORKFLOW_VERSION,
                schema_version=_SCHEMA_VERSION,
                prompt_version=get_prompt_version(),
                usage_data=usage_summary,
                latency_ms=int((perf_counter() - start_perf) * 1000),
                billed_points=billed_points,
                billing_policy_version=build_reader_ask_billing_metadata(
                    usage_summary,
                    selected_model_option.billing,
                ).get("billing_policy_version"),
                metadata_json={
                    "entrypoint": "/reader-ask/threads/{thread_id}/messages/stream",
                    "thread_id": str(thread_id),
                    "message_id": assistant_message["id"],
                    "cross_record_context_used": runtime_state.used_cross_record_context,
                    "anchor_count": len(resolved_anchors),
                    "tool_names": [entry.tool_name for entry in runtime_state.tool_trace if entry.status == "completed"],
                    "reservation_points": selected_model_option.billing.reserved_points,
                    "computed_cost_points": computed_cost_points,
                    "under_collected_points": under_collected_points,
                    **_reader_ask_model_metadata(selected_model_option),
                },
                **build_model_metadata(model_config),
            )
        )

        output = _build_user_visible_output(
            content_md=final_content_md,
            submission_mode=submission_mode,
            resolved_intent=resolved_intent,
            citations=runtime_state.citations,
            action_proposals=action_proposals,
            tool_trace=runtime_state.tool_trace,
            evidence=evidence,
            trace_summary=trace_summary,
            disambiguation=disambiguation,
            external_asset_disambiguation=external_asset_disambiguation,
            response_cards=response_cards,
            usage_summary=usage_summary,
            billed_points=billed_points,
            resolved_context=resolved_context,
            context_plan=context_plan,
            resolved_context_input=resolved_context_input,
            run_info=run_info,
            supplement_candidates=typed_supplement_candidates,
            persisted_supplements=[],
            reasoning_md=stream_runtime.emitted_reasoning or None,
            reasoning_status=stream_checkpoint_svc.terminal_reasoning_status(stream_runtime.reasoning_started),
            follow_up_suggestions=runtime_state.latest_suggestions or None,
        )
        final_message_status = "interrupted" if stream_outcome.interrupted else "completed"
        updated = await repo.update_message(
            message_id=_parse_uuid(assistant_message["id"], "assistant message id is invalid"),
            status=final_message_status,
            content_md=final_content_md,
            context_anchors=anchor_payload,
            citations=[citation.model_dump(mode="json") for citation in runtime_state.citations],
            action_proposals=[proposal.model_dump(mode="json") for proposal in action_proposals],
            tool_trace=[entry.model_dump(mode="json") for entry in runtime_state.tool_trace],
            metadata=_assistant_message_metadata(
                resolved_intent=resolved_intent,
                run_info=run_info,
                resolved_context_input=resolved_context_input,
                submission_mode=submission_mode,
            ),
            usage_event_id=usage_event_id,
            current_turn_run_id=_parse_uuid(turn_run["id"], "turn run id is invalid"),
        )
        payload = _build_completed_payload(
            message_id=updated["id"],
            thread_id=str(thread_id),
            output=output,
            usage_event_id=usage_event_id,
        )
        await repo.update_turn_run(
            turn_run_id=_parse_uuid(turn_run["id"], "turn run id is invalid"),
            status="interrupted" if stream_outcome.interrupted else "completed",
            resolved_intent=resolved_intent,
            user_visible_output_json=output.model_dump(mode="json"),
            usage_summary_json=usage_summary,
            usage_event_id=usage_event_id,
            completed_at=None if stream_outcome.interrupted else datetime.now(UTC),
            failed_at=None if stream_outcome.interrupted else None,
        )
        await _upsert_eval_trace_record(
            turn_run_id=_parse_uuid(turn_run["id"], "turn run id is invalid"),
            planning_snapshot=planning_snapshot,
            runtime_state=runtime_state,
            context_plan=context_plan,
            trace_summary=trace_summary,
            supplement_audit_json=[
                {
                    "event": "candidate_generated",
                    "supplement_type": item.supplement_type,
                    "candidate_id": item.candidate_id,
                    "created_from_turn_run_id": item.created_from_turn_run_id,
                    "timestamp": _iso_now(),
                }
                for item in typed_supplement_candidates
            ],
            billed_points=billed_points,
            usage_event_id=usage_event_id,
        )
        if not stream_outcome.interrupted:
            yield stream_events_svc.encode_sse(stream_events_svc.EVENT_MESSAGE_COMPLETED, payload.model_dump(mode="json"))
    except Exception as exc:
        if reservation is not None and reservation.total_points > 0 and record is not None:
            await refund_reserved_points(
                user_id,
                reservation,
                metadata={
                    "reason": "reader_ask_failed",
                    "thread_id": str(thread_id),
                    "record_id": str(record.record_id),
                },
            )
        if assistant_message is not None:
            await repo.update_message(
                message_id=_parse_uuid(assistant_message["id"], "assistant message id is invalid"),
                status="failed",
                content_md=final_content_md,
                context_anchors=anchor_payload,
                citations=[citation.model_dump(mode="json") for citation in runtime_state.citations],
                action_proposals=[],
                tool_trace=[entry.model_dump(mode="json") for entry in runtime_state.tool_trace],
                metadata=_assistant_message_metadata(
                    resolved_intent=resolved_intent,
                    resolved_context_input=resolved_context_input,
                    run_info=run_info,
                    submission_mode=submission_mode,
                ),
                usage_event_id=None,
                current_turn_run_id=active_turn_run_id,
            )
            if active_turn_run_id is not None:
                failed_output_json = (
                    _build_stream_checkpoint_output_json(
                        content_md=final_content_md,
                        reasoning_md=(stream_runtime.emitted_reasoning or None) if stream_runtime is not None else None,
                        reasoning_status=stream_checkpoint_svc.terminal_reasoning_status(stream_runtime.reasoning_started)
                        if stream_runtime is not None
                        else None,
                        submission_mode=submission_mode,
                        resolved_intent=resolved_intent,
                        record=record,
                        anchors=resolved_anchors,
                        attachments=attachments,
                        runtime_state=runtime_state,
                        reference_resolution=reference_resolution,
                        disambiguation=disambiguation,
                        external_asset_disambiguation=external_asset_disambiguation,
                        trace_summary=trace_summary,
                        context_plan=context_plan,
                        resolved_context_input=resolved_context_input,
                        run_info=run_info,
                        persisted_supplements=[],
                    )
                    if record is not None
                    else None
                )
                await repo.update_turn_run(
                    turn_run_id=active_turn_run_id,
                    status="failed",
                    resolved_intent=resolved_intent,
                    user_visible_output_json=failed_output_json,
                    failed_at=datetime.now(UTC),
                )
                await _upsert_eval_trace_record(
                    turn_run_id=active_turn_run_id,
                    planning_snapshot=planning_snapshot,
                    runtime_state=runtime_state,
                    context_plan=context_plan,
                    trace_summary=trace_summary,
                )
        if record is not None and thread is not None:
            await _record_failure_event(
                user_id=user_id,
                record_id=record.record_id,
                thread_id=thread_id,
                user_message=body.content,
                start_perf=start_perf,
                error_code="reader_ask_failed",
                error_message=str(exc),
                metadata_json={
                    "anchor_count": len(resolved_anchors),
                    "tool_names": [entry.tool_name for entry in runtime_state.tool_trace],
                    **(_reader_ask_model_metadata(selected_model_option) if selected_model_option is not None else {}),
                },
            )
        if isinstance(exc, HTTPException):
            yield stream_events_svc.encode_sse(stream_events_svc.EVENT_ERROR, stream_events_svc.http_exception_payload(exc.status_code, exc.detail))
            return
        if "model route is not configured" in str(exc):
            yield stream_events_svc.encode_sse(stream_events_svc.EVENT_ERROR, stream_events_svc.model_unavailable_payload())
            return
        detail = str(exc) if get_settings().app_env != "production" else "Ask Claread is temporarily unavailable."
        yield stream_events_svc.encode_sse(stream_events_svc.EVENT_ERROR, stream_events_svc.reader_ask_failed_payload(detail))


async def retry_thread_message(
    user_id: UUID,
    thread_id: UUID,
    message_id: UUID,
    retry_body: ReaderAskMessageRetryRequest | None = None,
) -> AsyncIterator[str]:
    """Regenerate (not resume/continue) the assistant answer for a message.

    This performs a full re-run: re-plan + re-materialize + re-generate.
    The previous answer is replaced entirely; it is NOT a continuation.
    """
    start_perf = perf_counter()
    thread: dict[str, Any] | None = None
    record: _RecordBundle | None = None
    history_messages: list[dict[str, Any]] = []
    attachments: list[ReaderAskAttachment] = []
    resolved_anchors: list[ReaderAskAnchorRef] = []
    anchor_payload: list[dict[str, Any]] = []
    reservation: CreditReservation | None = None
    user_message: dict[str, Any] | None = None
    assistant_message: dict[str, Any] | None = None
    runtime_state = ReaderAskRuntimeState()
    nested_tool_usages: list[dict[str, Any]] = []
    planner_usage_summary: dict[str, Any] | None = None
    resolved_intent: ReaderAskResolvedIntent | None = None
    resolved_context_input: ReaderAskResolvedContextInput | None = None
    context_plan: ReaderAskContextPlan | None = None
    evidence: list[ReaderAskEvidenceItem] = []
    trace_summary: ReaderAskTraceSummary | None = None
    run_info: dict[str, Any] | None = None
    active_turn_run_id: UUID | None = None
    run_history: list[dict[str, Any]] = []
    body: ReaderAskMessageStreamRequest | None = None
    original_user_message = ""
    planning_snapshot: planner.ReaderAskPlanningSnapshot | None = None
    disambiguation: ReaderAskDisambiguation | None = None
    external_asset_disambiguation: ReaderAskAssetDisambiguation | None = None
    reference_resolution = planner.ReaderAskReferenceResolution()
    selected_model_option: model_options_svc.ResolvedReaderAskModelOption | None = None
    final_content_md = ""
    persisted_supplements_json: list[dict[str, Any]] = []
    stream_runtime: AgentStreamRuntime | None = None
    submission_mode: ReaderAskSubmissionMode = "chat"

    try:
        thread = await repo.get_thread(user_id, thread_id)
        if thread is None:
            raise HTTPException(status_code=404, detail="Reader ask thread not found")

        assistant_message = await repo.get_message(message_id)
        if assistant_message is None or assistant_message.get("thread_id") != str(thread_id):
            raise HTTPException(status_code=404, detail="Reader ask message not found")
        if assistant_message.get("role") != "assistant":
            raise HTTPException(status_code=400, detail="Only assistant messages can be regenerated")
        assistant_message_model = ReaderAskMessage.model_validate(assistant_message)
        persisted_supplements_json = [
            item.model_dump(mode="json")
            for item in assistant_message_model.persisted_supplements
            if item.lifecycle_status != "deleted"
        ]

        messages = await repo.list_messages(thread_id, limit=100)
        assistant_index = next((index for index, item in enumerate(messages) if item["id"] == str(message_id)), -1)
        if assistant_index <= 0:
            raise HTTPException(status_code=400, detail="No user turn found for this assistant message")

        for index in range(assistant_index - 1, -1, -1):
            candidate = messages[index]
            if candidate["role"] == "user":
                user_message = candidate
                history_messages = messages[:assistant_index]
                break
        if user_message is None:
            raise HTTPException(status_code=400, detail="No user turn found for this assistant message")

        user_message_model = ReaderAskMessage.model_validate(user_message)
        if user_message_model.resolved_context_input is None:
            raise HTTPException(status_code=400, detail="User turn is missing retry context")

        original_user_message = user_message_model.content_md
        body = ReaderAskMessageStreamRequest(
            content=user_message_model.content_md,
            page_identity=user_message_model.resolved_context_input.page_identity,
            attachments=user_message_model.resolved_context_input.attachments,
            entry_action=user_message_model.resolved_context_input.entry_action,
            model=retry_body.model if retry_body is not None else None,
        )

        record_id = _parse_uuid(thread["record_id"], "thread record_id is invalid")
        record = await _load_record_bundle(user_id, record_id)
        if _parse_uuid(body.page_identity.record_id, "page_identity.record_id must be a UUID") != record.record_id:
            raise HTTPException(status_code=400, detail="page_identity.record_id does not match thread record")
        thread, selected_model_option = await _resolve_thread_model_option(
            user_id=user_id,
            thread_id=thread_id,
            thread=thread,
            requested_key=body.model,
        )
        runtime_budget_kwargs = _runtime_budget_kwargs(selected_model_option)

        attachments = body.attachments
        incoming_anchors = _attachments_to_anchor_refs(attachments)
        resolved_anchors = await _resolve_anchor_refs(
            user_id,
            record,
            anchors=incoming_anchors,
        )
        anchor_payload = [anchor.model_dump(mode="json") for anchor in resolved_anchors]

        await ensure_credit_account(user_id)
        remaining = await check_quota(user_id)
        if remaining < selected_model_option.billing.reserved_points:
            yield stream_events_svc.encode_sse(
                stream_events_svc.EVENT_ERROR,
                stream_events_svc.insufficient_credits_payload(
                    remaining,
                    required_points=selected_model_option.billing.reserved_points,
                ),
            )
            return

        reservation_metadata = {
            "capability_code": CAPABILITY_READER_ASK,
            "thread_id": str(thread_id),
            "record_id": str(record.record_id),
            **build_reader_ask_billing_metadata(None, selected_model_option.billing),
            **_reader_ask_model_metadata(selected_model_option),
            "user_message": _truncate_text(body.content, 200),
            "retry_message_id": str(message_id),
        }
        reservation = await reserve_points(
            user_id,
            selected_model_option.billing.reserved_points,
            task_id=None,
            entry_type=LEDGER_ENTRY_TYPE_AI_CAPABILITY_DEDUCT,
            metadata=reservation_metadata,
        )
        if reservation is None:
            remaining = await check_quota(user_id)
            yield stream_events_svc.encode_sse(
                stream_events_svc.EVENT_ERROR,
                stream_events_svc.insufficient_credits_payload(
                    remaining,
                    required_points=selected_model_option.billing.reserved_points,
                ),
            )
            return

        # Agent-loop is the only live route. ``planner_first`` survives only
        # as a historical trace value; service runtime no longer resolves a
        # planner route for retry runs.
        runtime_state.planner_skipped = True
        runtime_state.planner_route_used = "agent_loop_first"
        resolved_intent, resolved_intent_label = (
            runtime_contract_svc.build_minimal_resolved_intent(body.entry_action)
        )
        planning_snapshot = None
        reference_resolution = None
        resolved_context_input = None
        disambiguation = None
        external_asset_disambiguation = None
        submission_mode = planner_runtime_svc.submission_mode(
            entry_action=body.entry_action, attachments=attachments
        )
        planner_usage_summary = None
        run_info, run_history = _next_run_info(assistant_message)
        turn_run = await repo.create_turn_run(
            message_id=message_id,
            thread_id=thread_id,
            user_id=user_id,
            record_id=record.record_id,
            turn_id=_parse_uuid(user_message["id"], "user message id is invalid"),
            run_attempt=int(run_info.get("run_attempt") or 1),
            supersedes_run_id=_parse_uuid(str(run_info["supersedes_run_id"]), "supersedes run id is invalid")
            if run_info.get("supersedes_run_id")
            else None,
            status="streaming",
            resolved_intent=resolved_intent,
        )
        active_turn_run_id = _parse_uuid(turn_run["id"], "turn run id is invalid")
        run_info = _build_run_info(
            turn_id=user_message["id"],
            run_id=turn_run["id"],
            attempt=int(run_info.get("run_attempt") or 1),
            supersedes_run_id=str(run_info.get("supersedes_run_id")) if run_info.get("supersedes_run_id") else None,
        )
        assistant_message = await repo.update_message(
            message_id=message_id,
            status="streaming",
            content_md="",
            context_anchors=anchor_payload,
            citations=[],
            action_proposals=[],
            tool_trace=[],
            metadata=_assistant_message_metadata(
                resolved_intent=resolved_intent,
                run_info=run_info,
                run_history=run_history,
                resolved_context_input=resolved_context_input,
                submission_mode=submission_mode,
            ),
            usage_event_id=None,
            current_turn_run_id=_parse_uuid(turn_run["id"], "turn run id is invalid"),
        )
        yield stream_events_svc.encode_sse(stream_events_svc.EVENT_THREAD_READY, stream_events_svc.thread_ready_payload(str(thread_id), str(record.record_id)))
        yield stream_events_svc.encode_sse(stream_events_svc.EVENT_MESSAGE_STARTED, stream_events_svc.message_started_payload(assistant_message["id"], user_message["id"]))

        base_citations = [
            _anchor_to_citation(anchor, record_id=str(record.record_id), record_title=record.title)
            for anchor in resolved_anchors
        ]
        # Preserve agent-loop-first telemetry across the runtime_state rebuild.
        _prev_planner_skipped = runtime_state.planner_skipped
        _prev_planner_route = runtime_state.planner_route_used
        runtime_state = ReaderAskRuntimeState(
            citations=list(base_citations),
            source_labels={"current_record", *({"current_anchor"} if resolved_anchors else set())},
            planner_skipped=_prev_planner_skipped,
            planner_route_used=_prev_planner_route,
        )
        query_seed = _query_seed(body.content, resolved_anchors)
        cross_record_context_allowed = runtime_state.cross_record_context_allowed

        resolved = resolve_reader_ask_agent(selected_model_option.selection)
        agent = resolved.agent
        model = resolved.model
        model_config = resolved.model_config

        route_settings = RunModelSettings(
            max_tokens=runtime_budget_kwargs["max_output_tokens"],
            temperature=cfg.AGENT_TEMPERATURE,
            timeout=cfg.AGENT_TIMEOUT_S,
        )
        if model_config and model_config.model_settings is not None:
            route_settings = route_settings.merged_with(model_config.model_settings)
        route_settings = route_settings.with_max_tokens(
            min(
                route_settings.max_tokens or runtime_budget_kwargs["max_output_tokens"],
                runtime_budget_kwargs["max_output_tokens"],
            )
        )

        event_queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
        primary_anchor = resolved_anchors[0] if resolved_anchors else None

        async def get_record_context_cb(
            _deps: Any = None,
            scope: str = "window",
            target_sentence_id: str | None = None,
        ) -> dict[str, Any]:
            return _build_record_context_payload(
                record,
                scope=scope,
                target_sentence_id=target_sentence_id,
            )

        async def get_record_insights_cb(
            _deps: Any = None,
            target_sentence_id: str | None = None,
            kind: str | None = None,
            limit: int = 5,
        ) -> list[dict[str, Any]]:
            return _collect_insight_entries(
                record,
                target_sentence_id=target_sentence_id,
                kind=kind,
                limit=limit,
            )

        async def get_user_vocabulary_book_cb(
            _deps: Any = None,
            lemma: str | None = None,
            limit: int = 10,
            sort_by: str = "recent",
        ) -> list[dict[str, Any]]:
            return await _tool_get_user_vocabulary_book(
                user_id,
                lemma=lemma,
                limit=limit,
                sort_by=sort_by,
            )

        async def resolve_known_reference_cb(
            _deps: Any = None,
            query: str = "",
            top_k: int = 5,
        ) -> dict[str, Any]:
            return await _tool_resolve_known_reference_for_agent(
                user_id=user_id,
                current_record_id=record.record_id,
                query=query,
                top_k=top_k,
            )

        async def load_explicit_attachment_context_cb(
            _deps: Any = None,
            record_id: str = "",
            asset_id: str | None = None,
        ) -> dict[str, Any]:
            return await _tool_load_explicit_attachment_context(
                user_id=user_id,
                current_record_id=record.record_id,
                record_id=record_id,
                asset_id=asset_id,
            )

        async def suggest_prompts_cb(
            suggestions: list[dict[str, Any]],
        ) -> dict[str, Any]:
            return await _tool_suggest_prompts(suggestions)

        async def generate_sentence_annotation_cb(
            kind: Literal["grammar_note", "sentence_analysis"],
        ) -> dict[str, Any] | None:
            # Cache check is handled at the agent tool layer (reader_ask_agent.py).
            # If we reach here, there is no pre-generated annotation of this kind.
            return await _generate_sentence_annotation(record=record, anchor=primary_anchor, kind=kind)

        quick_action_annotation: dict[str, Any] | None = None
        # Round 15: agent-loop-first is the only live route. The legacy
        # planner_first else-branch (materialize_planned_context with a
        # planning_snapshot) has been removed.
        resolved_context_input = context_runtime_svc.build_agent_loop_context(
            record=record,
            runtime_state=runtime_state,
            anchors=resolved_anchors,
            attachments=attachments,
            user_id=user_id,
            page_identity=body.page_identity,
            entry_action=body.entry_action,
            latest_user_message=body.content,
            cross_record_toggle=runtime_state.cross_record_context_allowed,
            history_messages=history_messages,
        )
        quick_action_annotation = await _run_explicit_quick_action_annotation(
            submission_mode=submission_mode,
            task_mode=resolved_intent,
            entry_action=body.entry_action,
            record=record,
            primary_anchor=primary_anchor,
            runtime_state=runtime_state,
            event_queue=event_queue,
        )
        if quick_action_annotation and isinstance(quick_action_annotation.get("usage_summary"), dict):
            nested_tool_usages.append(
                {
                    "tool_name": "generate_sentence_annotation",
                    "usage_summary": quick_action_annotation["usage_summary"],
                }
            )
        context_plan = planner.build_context_plan(
            entry_action=body.entry_action,
            attachments=attachments,
            anchors=resolved_anchors,
            runtime_state=runtime_state,
            citations=runtime_state.citations,
            reference_resolution=reference_resolution,
            planning_snapshot=planning_snapshot,
        )
        trace_summary = planner.build_trace_summary(
            runtime_state=runtime_state,
            context_plan=context_plan,
            planning_snapshot=planning_snapshot,
            clarification_mode="can_answer_with_followup" if runtime_state.deictic_clarification_hint else "none",
        )
        await _upsert_eval_trace_record(
            turn_run_id=_parse_uuid(turn_run["id"], "turn run id is invalid"),
            planning_snapshot=planning_snapshot,
            runtime_state=runtime_state,
            context_plan=context_plan,
            trace_summary=trace_summary,
        )
        prompt_payload = runtime_contract_svc.build_prompt_payload(
            runtime_contract_svc.ReaderAskAnswerRuntimeInput(
                thread=thread,
                record=record,
                user_message=body.content,
                history_messages=history_messages,
                page_identity=body.page_identity,
                attachments=attachments,
                anchors=resolved_anchors,
                resolved_intent=resolved_intent,
                resolved_intent_label=_TASK_MODE_LABELS[resolved_intent],
                entry_action=body.entry_action,
                submission_mode=submission_mode,
                cross_record_context_allowed=cross_record_context_allowed,
                resolved_context_input=resolved_context_input,
                quick_action_annotation=quick_action_annotation,
                reference_resolution=reference_resolution,
                planning_snapshot=planning_snapshot,
                followup_hint=runtime_state.deictic_clarification_hint,
                cross_record_intent_hint=runtime_state.cross_record_intent_hint,
                external_attachment_hint=runtime_state.external_attachment_hint,
                dictionary_anchor_hint=runtime_state.dictionary_anchor_hint,
                long_history_hint=runtime_state.long_history_hint,
                max_history_messages=cfg.MAX_HISTORY_MESSAGES,
                max_message_text=cfg.MAX_MESSAGE_TEXT,
            )
        )
        # Emit context.compacting *before* compression so the user sees
        # "上下文压缩中" while compaction is in progress, not after.
        _max_input_budget = prompt_preparation_svc.compute_max_input_budget(
            max_input_tokens=runtime_budget_kwargs["max_input_tokens"],
        )
        if prompt_preparation_svc.should_emit_compacting(prompt_payload, max_input_budget=_max_input_budget):
            yield stream_events_svc.encode_sse(stream_events_svc.EVENT_CONTEXT_COMPACTING, stream_events_svc.context_compacting_payload(message_id))
        prompt_payload, max_output_tokens, _compaction_audit, _context_too_large = prompt_preparation_svc.prepare_prompt_payload(
            prompt_payload,
            max_input_tokens=runtime_budget_kwargs["max_input_tokens"],
            budget_buffer_tokens=runtime_budget_kwargs["prompt_buffer_tokens"],
            default_max_output_tokens=route_settings.max_tokens or runtime_budget_kwargs["max_output_tokens"],
            min_max_output_tokens=cfg.MIN_MAX_OUTPUT_TOKENS,
        )
        if _context_too_large:
            cleanup_plan = recovery_svc.build_context_too_large_cleanup_plan(
                user_id=user_id,
                thread_id=thread_id,
                record_id=record.record_id if record else None,
                reservation=reservation,
                assistant_message_id=message_id,
                active_turn_run_id=active_turn_run_id,
                runtime_state=runtime_state,
                resolved_intent=resolved_intent,
                resolved_context_input=resolved_context_input,
                run_info=run_info,
                submission_mode=submission_mode,
                anchor_payload=anchor_payload,
                error_code="reader_ask_retry_failed",
                retry_message_id=message_id,
                compaction_audit=_compaction_audit,
                trace_summary=trace_summary,
                build_message_metadata_cb=_assistant_message_metadata,
                build_turn_run_output_cb=_build_stream_checkpoint_output_json if active_turn_run_id and record else None,
                run_history=run_history,
                record_bundle=record,
                resolved_anchors=resolved_anchors,
                attachments=attachments,
                reference_resolution=reference_resolution,
                disambiguation=disambiguation,
                external_asset_disambiguation=external_asset_disambiguation,
                planning_snapshot=planning_snapshot,
                context_plan=context_plan,
                persisted_supplements_json=persisted_supplements_json,
                user_message_text=original_user_message or (body.content if body else ""),
                start_perf=start_perf,
                thread=thread,
            )
            # Execute cleanup plan
            if cleanup_plan.refund is not None:
                await refund_reserved_points(user_id, cleanup_plan.refund.reservation, metadata=cleanup_plan.refund.metadata)
            await repo.update_message(
                message_id=cleanup_plan.message_failed.message_id,
                status="failed",
                content_md=cleanup_plan.message_failed.content_md,
                context_anchors=anchor_payload,
                citations=[c.model_dump(mode="json") for c in runtime_state.citations],
                action_proposals=[],
                tool_trace=[e.model_dump(mode="json") for e in runtime_state.tool_trace],
                metadata=cleanup_plan.message_failed.metadata,
                usage_event_id=None,
                current_turn_run_id=cleanup_plan.message_failed.current_turn_run_id,
            )
            if cleanup_plan.turn_run_failed is not None:
                await repo.update_turn_run(
                    turn_run_id=cleanup_plan.turn_run_failed.turn_run_id,
                    status="failed",
                    resolved_intent=resolved_intent,
                    user_visible_output_json=cleanup_plan.turn_run_failed.user_visible_output_json,
                    failed_at=datetime.now(UTC),
                )
            if cleanup_plan.eval_trace is not None:
                await _upsert_eval_trace_record(
                    turn_run_id=cleanup_plan.eval_trace.turn_run_id,
                    planning_snapshot=cleanup_plan.eval_trace.planning_snapshot,
                    runtime_state=cleanup_plan.eval_trace.runtime_state,
                    context_plan=cleanup_plan.eval_trace.context_plan,
                    trace_summary=cleanup_plan.eval_trace.trace_summary,
                )
            if cleanup_plan.failure_event is not None:
                await _record_failure_event(
                    user_id=cleanup_plan.failure_event.user_id,
                    record_id=cleanup_plan.failure_event.record_id,
                    thread_id=cleanup_plan.failure_event.thread_id,
                    user_message=cleanup_plan.failure_event.user_message,
                    start_perf=cleanup_plan.failure_event.start_perf,
                    error_code=cleanup_plan.failure_event.error_code,
                    error_message=cleanup_plan.failure_event.error_message,
                    metadata_json=cleanup_plan.failure_event.metadata_json,
                )
            yield stream_events_svc.encode_sse(stream_events_svc.EVENT_ERROR, stream_events_svc.context_too_large_payload())
            return
        trace_summary = prompt_preparation_svc.inject_compaction_audit(trace_summary, _compaction_audit)
        route_settings = route_settings.with_max_tokens(
            min(route_settings.max_tokens or cfg.DEFAULT_MAX_OUTPUT_TOKENS, max_output_tokens)
        )

        deps = build_reader_ask_agent_deps(
            payload=prompt_payload,
            event_queue=event_queue,
            state=runtime_state,
            query_seed=query_seed,
            task_mode=resolved_intent,
            entry_action=body.entry_action,
            record_id=str(record.record_id),
            record_title=record.title,
            primary_anchor=primary_anchor,
            get_record_context_fn=get_record_context_cb,
            get_record_insights_fn=get_record_insights_cb,
            get_user_vocabulary_book_fn=get_user_vocabulary_book_cb,
            resolve_known_reference_fn=resolve_known_reference_cb,
            load_explicit_attachment_context_fn=load_explicit_attachment_context_cb,
            allowed_external_attachments=_build_allowed_external_attachments(attachments),
            generate_sentence_annotation_fn=generate_sentence_annotation_cb,
            suggest_prompts_fn=suggest_prompts_cb,
            vocabulary_item_to_citation_fn=_vocabulary_item_to_citation,
        )
        checkpoint = stream_checkpoint_svc.TurnRunStreamCheckpoint(
            turn_run_id=active_turn_run_id,
            build_output_json=lambda content_md, reasoning_md, reasoning_status: _build_stream_checkpoint_output_json(
                content_md=content_md,
                reasoning_md=reasoning_md,
                reasoning_status=reasoning_status,
                submission_mode=submission_mode,
                resolved_intent=resolved_intent,
                record=record,
                anchors=resolved_anchors,
                attachments=attachments,
                runtime_state=runtime_state,
                reference_resolution=reference_resolution,
                disambiguation=disambiguation,
                external_asset_disambiguation=external_asset_disambiguation,
                trace_summary=trace_summary,
                context_plan=context_plan,
                resolved_context_input=resolved_context_input,
                run_info=run_info,
                persisted_supplements=persisted_supplements_json,
            ),
            update_turn_run_cb=repo.update_turn_run,
        )
        # Round 6: record run start time for latency tracking
        runtime_state.run_started_at = datetime.now(UTC).isoformat()
        async for stream_item in stream_reader_ask_agent_run(
            agent=agent,
            deps=deps,
            model=model,
            route_settings=route_settings,
            assistant_message_id=assistant_message["id"],
            model_config=model_config,
            checkpoint_flush=stream_checkpoint_svc.make_checkpoint_flush(checkpoint),
        ):
            if isinstance(stream_item, ReaderAskStreamSseEvent):
                yield stream_item.encoded_sse
            elif isinstance(stream_item, ReaderAskStreamCompleted):
                stream_outcome = stream_item.outcome
                final_content_md = stream_outcome.content_md
                usage_summary = stream_outcome.usage_summary
                stream_runtime = stream_item.stream_runtime

        # Round 14: agent-loop repair — when the main answer is
        # degenerate, attempt a single agent-loop repair (re-run answer
        # agent with repair hint) instead of falling back to a planner.
        _agent_loop_repair_eligible = (
            runtime_state.planner_route_used == "agent_loop_first"
            and is_degenerate_answer(final_content_md)
            and not stream_outcome.interrupted
        )
        if _agent_loop_repair_eligible:
            runtime_state.degenerate_detected = True
            runtime_state.degenerate_reason = "degenerate_answer"
            runtime_state.repair_attempted = True
            runtime_state.repair_reason = runtime_state.degenerate_reason
            runtime_state.repair_route = "agent_loop_repair"
            try:
                repair_content, repair_runtime_state = await _run_agent_loop_repair(
                    user_id=user_id,
                    record=record,
                    body=body,
                    attachments=attachments,
                    resolved_anchors=resolved_anchors,
                    history_messages=history_messages,
                    thread=thread,
                    runtime_state=runtime_state,
                    primary_anchor=primary_anchor,
                    submission_mode=submission_mode,
                    resolved_intent=resolved_intent,
                    resolved_context_input=resolved_context_input,
                    reference_resolution=reference_resolution,
                    disambiguation=disambiguation,
                    external_asset_disambiguation=external_asset_disambiguation,
                    trace_summary=trace_summary,
                    context_plan=context_plan,
                    run_info=run_info,
                    route_settings=route_settings,
                    model_selection=selected_model_option.selection,
                    runtime_budget_kwargs=runtime_budget_kwargs,
                    event_queue=event_queue,
                    query_seed=query_seed,
                    get_record_context_cb=get_record_context_cb,
                    get_record_insights_cb=get_record_insights_cb,
                    get_user_vocabulary_book_cb=get_user_vocabulary_book_cb,
                    resolve_known_reference_cb=resolve_known_reference_cb,
                    load_explicit_attachment_context_cb=load_explicit_attachment_context_cb,
                    generate_sentence_annotation_cb=generate_sentence_annotation_cb,
                    suggest_prompts_cb=suggest_prompts_cb,
                    degenerate_content_md=final_content_md,
                )
                if repair_content and not is_degenerate_answer(repair_content):
                    final_content_md = repair_content
                    runtime_state.repair_succeeded = True
                    # Merge evidence-producing fields (citations, tool_trace,
                    # suggestions, etc.) from the repair run so the completed
                    # payload reflects the repair's tool calls, not the stale
                    # evidence from the degenerate run.
                    _merge_repair_runtime_state(runtime_state, repair_runtime_state)
                    logger.info(
                        "reader_ask_agent_loop_repair_succeeded: repair produced non-degenerate answer (%d chars)",
                        len(repair_content),
                    )
                else:
                    runtime_state.repair_succeeded = False
                    logger.warning(
                        "reader_ask_agent_loop_repair_failed: repair still degenerate (%d chars), using original",
                        len(repair_content or ""),
                    )
            except Exception:
                runtime_state.repair_succeeded = False
                logger.warning(
                    "reader_ask_agent_loop_repair_exception: repair raised, using original answer",
                    exc_info=True,
                )

        # Round 15: the legacy bounded-replan block (which called
        # resolve_semantic_planning) has been removed. For agent_loop_first,
        # build_replan_event always returns None, so the replan path was
        # unreachable dead code. Degenerate answers are now handled by the
        # agent-loop repair above.

        runtime_proposals = _build_action_proposals_from_runtime(
            record=record,
            action_requests=runtime_state.action_requests,
            assistant_content_md=final_content_md,
        )
        action_proposals = _merge_action_proposals(runtime_proposals, [])
        extra_usage_summaries = list(nested_tool_usages)
        if planner_usage_summary:
            extra_usage_summaries.insert(0, {"tool_name": "semantic_planner", "usage_summary": planner_usage_summary})
        usage_summary = _merge_usage_summaries(usage_summary, extra_usage_summaries)
        response_cards = _build_response_cards(
            task_mode=resolved_intent,
            record=record,
            anchors=resolved_anchors,
            runtime_state=runtime_state,
        )
        resolved_context = planner.build_resolved_context_summary(
            record_id=str(record.record_id),
            record_title=record.title,
            anchors=resolved_anchors,
            explicit_attachment_count=len(attachments),
            runtime_state=runtime_state,
            used_cross_record_context=runtime_state.used_cross_record_context,
            citations=runtime_state.citations,
        )
        typed_supplement_candidates = _build_supplement_candidates_from_runtime(
            resolved_intent=resolved_intent,
            anchors=resolved_anchors,
            runtime_state=runtime_state,
            assistant_content_md=final_content_md,
            created_from_turn_run_id=str(run_info["run_id"]) if run_info is not None else str(uuid4()),
        )
        supplement_candidates = [candidate.model_dump(mode="json") for candidate in typed_supplement_candidates]
        action_proposals = [
            *action_proposals,
            *_build_supplement_action_proposals(supplement_candidates),
        ]
        evidence = _build_evidence_items(
            attachments=attachments,
            citations=runtime_state.citations,
            current_record_id=str(record.record_id),
            current_record_title=record.title,
            external_record_contexts=runtime_state.latest_external_record_contexts,
            external_asset_contexts=runtime_state.latest_external_asset_contexts,
            reference_resolution=reference_resolution,
            supplement_candidates=typed_supplement_candidates,
            disambiguation=disambiguation,
            external_asset_disambiguation=external_asset_disambiguation,
        )
        trace_summary = trace_summary.model_copy(
            update={
                "supplement_generation_used": bool(typed_supplement_candidates),
                "supplement_persisted_count": 0,
                "supplement_deleted_count": 0,
            }
        )

        computed_cost_points = compute_reader_ask_cost_points(
            usage_summary,
            selected_model_option.billing,
        )
        billed_points, under_collected_points = await _settle_reader_ask_reservation(
            user_id=user_id,
            reservation=reservation,
            actual_cost_points=computed_cost_points,
            metadata={
                "reason": "reader_ask_retry_settlement",
                "thread_id": str(thread_id),
                "record_id": str(record.record_id),
                "retry_message_id": str(message_id),
                "computed_cost_points": computed_cost_points,
                **_reader_ask_model_metadata(selected_model_option),
            },
        )
        reservation = CreditReservation(total_points=0, deducted_from_daily=0, deducted_from_bonus=0)

        usage_event_id = await record_ai_usage_event(
            AIUsageEventCreate(
                usage_scope=USAGE_SCOPE_USER_BILLED,
                capability_code=CAPABILITY_READER_ASK,
                billing_mode=BILLING_MODE_USER_POINTS,
                status=STATUS_SUCCEEDED,
                user_id=user_id,
                record_id=record.record_id,
                workflow_name=_WORKFLOW_NAME,
                workflow_version=_WORKFLOW_VERSION,
                schema_version=_SCHEMA_VERSION,
                prompt_version=get_prompt_version(),
                usage_data=usage_summary,
                latency_ms=int((perf_counter() - start_perf) * 1000),
                billed_points=billed_points,
                billing_policy_version=build_reader_ask_billing_metadata(
                    usage_summary,
                    selected_model_option.billing,
                ).get("billing_policy_version"),
                metadata_json={
                    "entrypoint": "/reader-ask/threads/{thread_id}/messages/{message_id}/retry/stream",
                    "thread_id": str(thread_id),
                    "message_id": str(message_id),
                    "cross_record_context_used": runtime_state.used_cross_record_context,
                    "anchor_count": len(resolved_anchors),
                    "tool_names": [entry.tool_name for entry in runtime_state.tool_trace if entry.status == "completed"],
                    "reservation_points": selected_model_option.billing.reserved_points,
                    "computed_cost_points": computed_cost_points,
                    "under_collected_points": under_collected_points,
                    **_reader_ask_model_metadata(selected_model_option),
                },
                **build_model_metadata(model_config),
            )
        )

        output = _build_user_visible_output(
            content_md=final_content_md,
            submission_mode=submission_mode,
            resolved_intent=resolved_intent,
            citations=runtime_state.citations,
            action_proposals=action_proposals,
            tool_trace=runtime_state.tool_trace,
            evidence=evidence,
            trace_summary=trace_summary,
            disambiguation=disambiguation,
            external_asset_disambiguation=external_asset_disambiguation,
            response_cards=response_cards,
            usage_summary=usage_summary,
            billed_points=billed_points,
            resolved_context=resolved_context,
            context_plan=context_plan,
            resolved_context_input=resolved_context_input,
            run_info=run_info,
            supplement_candidates=typed_supplement_candidates,
            persisted_supplements=persisted_supplements_json,
            reasoning_md=stream_runtime.emitted_reasoning or None,
            reasoning_status=stream_checkpoint_svc.terminal_reasoning_status(stream_runtime.reasoning_started),
            follow_up_suggestions=runtime_state.latest_suggestions or None,
        )
        final_message_status = "interrupted" if stream_outcome.interrupted else "completed"
        await repo.update_message(
            message_id=message_id,
            status=final_message_status,
            content_md=final_content_md,
            context_anchors=anchor_payload,
            citations=[citation.model_dump(mode="json") for citation in runtime_state.citations],
            action_proposals=[proposal.model_dump(mode="json") for proposal in action_proposals],
            tool_trace=[entry.model_dump(mode="json") for entry in runtime_state.tool_trace],
            metadata=_assistant_message_metadata(
                resolved_intent=resolved_intent,
                run_info=run_info,
                run_history=run_history,
                resolved_context_input=resolved_context_input,
                submission_mode=submission_mode,
            ),
            usage_event_id=usage_event_id,
            current_turn_run_id=_parse_uuid(turn_run["id"], "turn run id is invalid"),
        )
        payload = _build_completed_payload(
            message_id=str(message_id),
            thread_id=str(thread_id),
            output=output,
            usage_event_id=usage_event_id,
        )
        await repo.update_turn_run(
            turn_run_id=_parse_uuid(turn_run["id"], "turn run id is invalid"),
            status="interrupted" if stream_outcome.interrupted else "completed",
            resolved_intent=resolved_intent,
            user_visible_output_json=output.model_dump(mode="json"),
            usage_summary_json=usage_summary,
            usage_event_id=usage_event_id,
            completed_at=None if stream_outcome.interrupted else datetime.now(UTC),
            failed_at=None if stream_outcome.interrupted else None,
        )
        await _upsert_eval_trace_record(
            turn_run_id=_parse_uuid(turn_run["id"], "turn run id is invalid"),
            planning_snapshot=planning_snapshot,
            runtime_state=runtime_state,
            context_plan=context_plan,
            trace_summary=trace_summary,
            supplement_audit_json=[
                {
                    "event": "candidate_generated",
                    "supplement_type": item.supplement_type,
                    "candidate_id": item.candidate_id,
                    "created_from_turn_run_id": item.created_from_turn_run_id,
                    "timestamp": _iso_now(),
                }
                for item in typed_supplement_candidates
            ],
            billed_points=billed_points,
            usage_event_id=usage_event_id,
        )
        if not stream_outcome.interrupted:
            yield stream_events_svc.encode_sse(stream_events_svc.EVENT_MESSAGE_COMPLETED, payload.model_dump(mode="json"))
    except Exception as exc:
        if reservation is not None and reservation.total_points > 0 and record is not None:
            await refund_reserved_points(
                user_id,
                reservation,
                metadata={
                    "reason": "reader_ask_retry_failed",
                    "thread_id": str(thread_id),
                    "record_id": str(record.record_id),
                    "retry_message_id": str(message_id),
                },
            )
        if assistant_message is not None:
            await repo.update_message(
                message_id=message_id,
                status="failed",
                content_md=final_content_md,
                context_anchors=anchor_payload,
                citations=[citation.model_dump(mode="json") for citation in runtime_state.citations],
                action_proposals=[],
                tool_trace=[entry.model_dump(mode="json") for entry in runtime_state.tool_trace],
                metadata=_assistant_message_metadata(
                    resolved_intent=resolved_intent,
                    run_info=run_info,
                    run_history=run_history,
                    resolved_context_input=resolved_context_input,
                    submission_mode=submission_mode,
                ),
                usage_event_id=None,
                current_turn_run_id=active_turn_run_id,
            )
            if active_turn_run_id is not None:
                failed_output_json = (
                    _build_stream_checkpoint_output_json(
                        content_md=final_content_md,
                        reasoning_md=(stream_runtime.emitted_reasoning or None) if stream_runtime is not None else None,
                        reasoning_status=stream_checkpoint_svc.terminal_reasoning_status(stream_runtime.reasoning_started)
                        if stream_runtime is not None
                        else None,
                        submission_mode=submission_mode,
                        resolved_intent=resolved_intent,
                        record=record,
                        anchors=resolved_anchors,
                        attachments=attachments,
                        runtime_state=runtime_state,
                        reference_resolution=reference_resolution,
                        disambiguation=disambiguation,
                        external_asset_disambiguation=external_asset_disambiguation,
                        trace_summary=trace_summary,
                        context_plan=context_plan,
                        resolved_context_input=resolved_context_input,
                        run_info=run_info,
                        persisted_supplements=persisted_supplements_json,
                    )
                    if record is not None
                    else None
                )
                await repo.update_turn_run(
                    turn_run_id=active_turn_run_id,
                    status="failed",
                    resolved_intent=resolved_intent,
                    user_visible_output_json=failed_output_json,
                    failed_at=datetime.now(UTC),
                )
                await _upsert_eval_trace_record(
                    turn_run_id=active_turn_run_id,
                    planning_snapshot=planning_snapshot,
                    runtime_state=runtime_state,
                    context_plan=context_plan,
                    trace_summary=trace_summary,
                )
        if record is not None and thread is not None:
            await _record_failure_event(
                user_id=user_id,
                record_id=record.record_id,
                thread_id=thread_id,
                user_message=original_user_message or (body.content if body else ""),
                start_perf=start_perf,
                error_code="reader_ask_retry_failed",
                error_message=str(exc),
                metadata_json={
                    "anchor_count": len(resolved_anchors),
                    "tool_names": [entry.tool_name for entry in runtime_state.tool_trace],
                    "retry_message_id": str(message_id),
                    **(_reader_ask_model_metadata(selected_model_option) if selected_model_option is not None else {}),
                },
            )
        if isinstance(exc, HTTPException):
            yield stream_events_svc.encode_sse(stream_events_svc.EVENT_ERROR, stream_events_svc.http_exception_payload(exc.status_code, exc.detail))
            return
        if "model route is not configured" in str(exc):
            yield stream_events_svc.encode_sse(stream_events_svc.EVENT_ERROR, stream_events_svc.model_unavailable_payload())
            return
        detail = str(exc) if get_settings().app_env != "production" else "Ask Claread is temporarily unavailable."
        yield stream_events_svc.encode_sse(stream_events_svc.EVENT_ERROR, stream_events_svc.reader_ask_failed_payload(detail))


def _annotation_request_from_anchor(
    *,
    record_id: UUID,
    anchor: ReaderAskAnchorRef,
) -> UserAnnotationCreateRequest:
    if anchor.anchor_type == "sentence":
        if not anchor.sentence_id or not anchor.selected_text:
            raise HTTPException(status_code=400, detail="sentence anchor is incomplete")
        return UserAnnotationCreateRequest(
            analysis_record_id=str(record_id),
            anchor_type="sentence",
            sentence_id=anchor.sentence_id,
            paragraph_id=anchor.paragraph_id,
            selected_text=anchor.selected_text,
            payload_json=anchor.payload_json,
        )
    if anchor.anchor_type == "text_range":
        if (
            not anchor.sentence_id
            or not anchor.selected_text
            or anchor.start_offset is None
            or anchor.end_offset is None
            or not anchor.text_hash
        ):
            raise HTTPException(status_code=400, detail="text_range anchor is incomplete")
        return UserAnnotationCreateRequest(
            analysis_record_id=str(record_id),
            anchor_type="text_range",
            sentence_id=anchor.sentence_id,
            paragraph_id=anchor.paragraph_id,
            selected_text=anchor.selected_text,
            start_offset=anchor.start_offset,
            end_offset=anchor.end_offset,
            text_hash=anchor.text_hash,
            payload_json=anchor.payload_json,
        )
    if anchor.anchor_type == "multi_text":
        if len(anchor.segments) < 2:
            raise HTTPException(status_code=400, detail="multi_text anchor is incomplete")
        return UserAnnotationCreateRequest(
            analysis_record_id=str(record_id),
            anchor_type="multi_text",
            sentence_id=anchor.segments[0].sentence_id,
            selected_text=anchor.selected_text or " ... ".join(segment.selected_text for segment in anchor.segments),
            segments=[UserAnnotationSegment.model_validate(segment.model_dump(mode="json")) for segment in anchor.segments],
            payload_json=anchor.payload_json,
        )
    raise HTTPException(status_code=400, detail="annotation action only supports sentence/text anchors")


def _reader_note_request_from_anchor(
    *,
    record_id: UUID,
    anchor: ReaderAskAnchorRef,
    note_text: str,
) -> ReaderNoteCreateRequest:
    if anchor.anchor_type == "sentence":
        if not anchor.sentence_id or not anchor.selected_text:
            raise HTTPException(status_code=400, detail="sentence anchor is incomplete")
        return ReaderNoteCreateRequest(
            analysis_record_id=str(record_id),
            quote_mode="sentence",
            anchor_sentence_id=anchor.sentence_id,
            sentence_id=anchor.sentence_id,
            paragraph_id=anchor.paragraph_id,
            selected_text=anchor.selected_text,
            note_text=note_text,
            payload_json=anchor.payload_json,
        )
    if anchor.anchor_type == "text_range":
        if (
            not anchor.sentence_id
            or not anchor.selected_text
            or anchor.start_offset is None
            or anchor.end_offset is None
            or not anchor.text_hash
        ):
            raise HTTPException(status_code=400, detail="text_range anchor is incomplete")
        return ReaderNoteCreateRequest(
            analysis_record_id=str(record_id),
            quote_mode="text_range",
            anchor_sentence_id=anchor.sentence_id,
            sentence_id=anchor.sentence_id,
            paragraph_id=anchor.paragraph_id,
            selected_text=anchor.selected_text,
            start_offset=anchor.start_offset,
            end_offset=anchor.end_offset,
            text_hash=anchor.text_hash,
            note_text=note_text,
            payload_json=anchor.payload_json,
        )
    if anchor.anchor_type == "multi_text":
        if len(anchor.segments) < 2:
            raise HTTPException(status_code=400, detail="multi_text anchor is incomplete")
        return ReaderNoteCreateRequest(
            analysis_record_id=str(record_id),
            quote_mode="multi_text",
            anchor_sentence_id=anchor.segments[0].sentence_id,
            sentence_id=anchor.segments[0].sentence_id,
            paragraph_id=anchor.segments[0].paragraph_id,
            selected_text=anchor.selected_text or " ... ".join(segment.selected_text for segment in anchor.segments),
            segments=[UserAnnotationSegment.model_validate(segment.model_dump(mode="json")) for segment in anchor.segments],
            note_text=note_text,
            payload_json=anchor.payload_json,
        )
    raise HTTPException(status_code=400, detail="reader note action only supports sentence/text anchors")


async def confirm_action(
    user_id: UUID,
    thread_id: UUID,
    action_id: str,
    body: ReaderAskActionConfirmRequest,
) -> ReaderAskActionConfirmResponse:
    message_dict, proposal_dict = await repo.find_action_proposal(
        user_id=user_id,
        thread_id=thread_id,
        action_id=action_id,
    )
    if message_dict is None or proposal_dict is None:
        raise HTTPException(status_code=404, detail="Reader ask action proposal not found")

    # Idempotency: if the proposal is already in a terminal state, return
    # immediately without re-executing the side effect.
    proposal_status = proposal_dict.get("status", "pending")
    if proposal_status == "executed":
        # Already executed — return stable result with the persisted result_json
        # so the client can recover local state even after a lost response.
        result_data = proposal_dict.get("result_json")
        result = ReaderAskActionConfirmResult.model_validate(result_data) if result_data else ReaderAskActionConfirmResult()
        return ReaderAskActionConfirmResponse(ok=True, action_id=action_id, status="executed", result=result)
    if proposal_status == "rejected":
        raise HTTPException(status_code=409, detail="Action proposal has already been rejected")
    # "executing" status: a previous request started but may not have completed.
    # The underlying business writes are idempotent (ON CONFLICT), so it is
    # safe to retry. Fall through to the normal confirm path.

    message = ReaderAskMessage.model_validate(message_dict)
    proposal = ReaderAskActionProposal.model_validate(proposal_dict)
    run_history = message_dict.get("run_history") or None
    persisted_supplements = [
        item.model_dump(mode="json") for item in message.persisted_supplements
    ]
    turn_run_id = _current_turn_run_id(message_dict, message.run_info)
    visible_output = _visible_output_from_message(message, message_dict)
    if not body.confirmed:
        updated_proposals = [
            proposal_item.model_copy(update={"status": "rejected"}) if proposal_item.id == action_id else proposal_item
            for proposal_item in message.action_proposals
        ]
        await repo.update_message(
            message_id=_parse_uuid(message.id, "message id is invalid"),
            status=message.status,
            content_md=message.content_md,
            context_anchors=[anchor.model_dump(mode="json") for anchor in message.context_anchors],
            citations=[citation.model_dump(mode="json") for citation in message.citations],
            action_proposals=[item.model_dump(mode="json") for item in updated_proposals],
            tool_trace=[item.model_dump(mode="json") for item in message.tool_trace],
            metadata=_assistant_message_metadata(
                resolved_intent=message.resolved_intent,
                run_info=message.run_info.model_dump(mode="json") if message.run_info else None,
                run_history=run_history,
                resolved_context_input=message.resolved_context_input,
            ),
            usage_event_id=_parse_uuid(message.usage_event_id, "usage_event_id is invalid") if message.usage_event_id else None,
            current_turn_run_id=turn_run_id,
        )
        if turn_run_id is not None:
            visible_output["action_proposals"] = [item.model_dump(mode="json") for item in updated_proposals]
            await repo.update_turn_run(
                turn_run_id=turn_run_id,
                status=message.status,
                user_visible_output_json=visible_output,
            )
            existing_trace = await repo.get_eval_trace(turn_run_id)
            action_audit = list((existing_trace or {}).get("action_audit_json") or [])
            action_audit.append(
                {
                    "action_id": action_id,
                    "action_type": proposal.action_type,
                    "decision": "rejected",
                    "timestamp": _iso_now(),
                    "status_after_decision": "rejected",
                }
            )
            await _upsert_eval_trace_record(
                turn_run_id=turn_run_id,
                planning_snapshot=None,
                runtime_state=ReaderAskRuntimeState(),
                context_plan=None,
                action_audit_json=action_audit,
            )
        return ReaderAskActionConfirmResponse(ok=True, action_id=action_id, status="rejected")

    thread = await repo.get_thread(user_id, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Reader ask thread not found")
    record_id = _parse_uuid(thread["record_id"], "thread record_id is invalid")

    # Mark proposal as "executing" before the business write, so that a
    # partially-completed request (business write succeeded but
    # update_message failed) can be safely retried.
    executing_proposals = [
        proposal_item.model_copy(update={"status": "executing"}) if proposal_item.id == action_id else proposal_item
        for proposal_item in message.action_proposals
    ]
    await repo.update_message(
        message_id=_parse_uuid(message.id, "message id is invalid"),
        status=message.status,
        content_md=message.content_md,
        context_anchors=[anchor.model_dump(mode="json") for anchor in message.context_anchors],
        citations=[citation.model_dump(mode="json") for citation in message.citations],
        action_proposals=[item.model_dump(mode="json") for item in executing_proposals],
        tool_trace=[item.model_dump(mode="json") for item in message.tool_trace],
        metadata=_assistant_message_metadata(
            resolved_intent=message.resolved_intent,
            run_info=message.run_info.model_dump(mode="json") if message.run_info else None,
            run_history=run_history,
            resolved_context_input=message.resolved_context_input,
        ),
        usage_event_id=_parse_uuid(message.usage_event_id, "usage_event_id is invalid") if message.usage_event_id else None,
        current_turn_run_id=turn_run_id,
    )

    result = ReaderAskActionConfirmResult()
    updated_trace_summary = message.trace_summary
    updated_evidence = list(message.evidence)
    if proposal.action_type == "create_supplement_grammar_note":
        candidate_payload = proposal.payload_json.get("candidate")
        if not isinstance(candidate_payload, dict):
            raise HTTPException(status_code=400, detail="Action proposal is missing supplement candidate")
        candidate = ReaderAskSupplementCandidate.model_validate(candidate_payload)
        record_summary = await repo.ensure_record_access(user_id, record_id)
        created = await supplements_svc.create_supplement(
            user_id=user_id,
            record_id=record_id,
            candidate=candidate,
        )
        persisted_supplement = supplements_svc.row_to_persisted_supplement(
            created,
            record_title=record_summary.get("title"),
        )
        persisted_supplements = _upsert_persisted_supplement(persisted_supplements, persisted_supplement)
        updated_evidence.append(
            ReaderAskEvidenceItem(
                kind="supplement_candidate",
                label=persisted_supplement.title,
                detail="已写入当前页",
                scope="current_record",
                record_id=persisted_supplement.record_id,
                record_title=persisted_supplement.record_title,
                reason="supplement_persisted",
                target_key=persisted_supplement.target_key,
                metadata_json={"supplement_id": persisted_supplement.supplement_id},
            )
        )
        if updated_trace_summary is not None:
            updated_trace_summary = updated_trace_summary.model_copy(
                update={
                    "supplement_persisted_count": len(
                        [
                            item
                            for item in persisted_supplements
                            if item.get("lifecycle_status") == "persisted"
                        ]
                    ),
                }
            )
        result = ReaderAskActionConfirmResult(
            record_id=str(created["record_id"]),
            supplement_projection=supplements_svc.supplement_projection_entry(created),
            persisted_supplement=persisted_supplement,
        )
    else:
        anchor_payload = proposal.payload_json.get("anchor")
        if not isinstance(anchor_payload, dict):
            raise HTTPException(status_code=400, detail="Action proposal is missing anchor payload")
        anchor = ReaderAskAnchorRef.model_validate(anchor_payload)

        if proposal.action_type == "save_highlight":
            annotation = await user_annotations_svc.create_user_annotation(
                user_id,
                _annotation_request_from_anchor(
                    record_id=record_id,
                    anchor=anchor,
                ),
            )
            result = ReaderAskActionConfirmResult(
                annotation_id=str(annotation.id),
                annotation_type="highlight",
                target_key=annotation.target_key,
            )
        elif proposal.action_type == "save_note":
            note_text = proposal.payload_json.get("note_text")
            if not isinstance(note_text, str) or not note_text.strip():
                raise HTTPException(status_code=400, detail="Action proposal is missing note_text")
            note = await reader_notes_svc.create_reader_note(
                user_id,
                _reader_note_request_from_anchor(
                    record_id=record_id,
                    anchor=anchor,
                    note_text=note_text,
                ),
            )
            result = ReaderAskActionConfirmResult(
                target_key=note.target_key,
                note_id=str(note.id),
            )
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported action type: {proposal.action_type}")

    updated_proposals = [
        proposal_item.model_copy(update={"status": "executed", "result_json": result.model_dump(mode="json")})
        if proposal_item.id == action_id
        else proposal_item
        for proposal_item in message.action_proposals
    ]
    await repo.update_message(
        message_id=_parse_uuid(message.id, "message id is invalid"),
        status=message.status,
        content_md=message.content_md,
        context_anchors=[anchor_item.model_dump(mode="json") for anchor_item in message.context_anchors],
        citations=[citation.model_dump(mode="json") for citation in message.citations],
        action_proposals=[item.model_dump(mode="json") for item in updated_proposals],
        tool_trace=[item.model_dump(mode="json") for item in message.tool_trace],
        metadata=_assistant_message_metadata(
            resolved_intent=message.resolved_intent,
            run_info=message.run_info.model_dump(mode="json") if message.run_info else None,
            run_history=run_history,
            resolved_context_input=message.resolved_context_input,
        ),
        usage_event_id=_parse_uuid(message.usage_event_id, "usage_event_id is invalid") if message.usage_event_id else None,
        current_turn_run_id=turn_run_id,
    )
    if turn_run_id is not None:
        visible_output["action_proposals"] = [item.model_dump(mode="json") for item in updated_proposals]
        visible_output["evidence"] = [item.model_dump(mode="json") for item in updated_evidence]
        visible_output["trace_summary"] = (
            updated_trace_summary.model_dump(mode="json") if updated_trace_summary is not None else None
        )
        visible_output["persisted_supplements"] = persisted_supplements
        await repo.update_turn_run(
            turn_run_id=turn_run_id,
            status=message.status,
            user_visible_output_json=visible_output,
        )
        existing_trace = await repo.get_eval_trace(turn_run_id)
        action_audit = list((existing_trace or {}).get("action_audit_json") or [])
        action_audit.append(
            {
                "action_id": action_id,
                "action_type": proposal.action_type,
                "decision": "confirmed",
                "timestamp": _iso_now(),
                "status_after_decision": "executed",
            }
        )
        supplement_audit = list((existing_trace or {}).get("supplement_audit_json") or [])
        if result.persisted_supplement is not None:
            supplement_audit.append(
                {
                    "event": "persisted",
                    "supplement_id": result.persisted_supplement.supplement_id,
                    "supplement_type": result.persisted_supplement.supplement_type,
                    "created_from_turn_run_id": result.persisted_supplement.created_from_turn_run_id,
                    "timestamp": _iso_now(),
                }
            )
        await _upsert_eval_trace_record(
            turn_run_id=turn_run_id,
            planning_snapshot=None,
            runtime_state=ReaderAskRuntimeState(),
            context_plan=None,
            action_audit_json=action_audit,
            supplement_audit_json=supplement_audit,
        )
    return ReaderAskActionConfirmResponse(ok=True, action_id=action_id, status="executed", result=result)


async def delete_supplement(user_id: UUID, supplement_id: UUID) -> ReaderAskDeleteSupplementResponse:
    supplement = await supplements_svc.get_supplement_projection_or_404(user_id, supplement_id)
    record_summary = await repo.ensure_record_access(user_id, _parse_uuid(str(supplement["record_id"]), "supplement record id is invalid"))
    deleted = await supplements_svc.delete_supplement(user_id, supplement_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Reader ask supplement not found")
    persisted_supplement = supplements_svc.row_to_persisted_supplement(
        deleted,
        record_title=record_summary.get("title"),
        lifecycle_status="deleted",
    )
    source_turn_run_id = _parse_uuid(
        persisted_supplement.created_from_turn_run_id,
        "supplement created_from_turn_run_id is invalid",
    )
    source_turn_run = await repo.get_turn_run(source_turn_run_id)
    if source_turn_run is not None:
        source_message_id = _parse_uuid(source_turn_run["message_id"], "turn run message id is invalid")
        turn_runs = await repo.list_turn_runs_for_message(source_message_id)
        for turn_run in turn_runs:
            turn_run_id = _parse_uuid(turn_run["id"], "turn run id is invalid")
            output = dict(turn_run.get("user_visible_output_json") or {})
            output["persisted_supplements"] = _mark_deleted_persisted_supplement(
                list(output.get("persisted_supplements") or []),
                persisted_supplement,
            )
            await repo.update_turn_run(
                turn_run_id=turn_run_id,
                status=turn_run["status"],
                user_visible_output_json=output,
            )
        existing_trace = await repo.get_eval_trace(source_turn_run_id)
        supplement_audit = list((existing_trace or {}).get("supplement_audit_json") or [])
        supplement_audit.append(
            {
                "event": "deleted",
                "supplement_id": persisted_supplement.supplement_id,
                "supplement_type": persisted_supplement.supplement_type,
                "created_from_turn_run_id": persisted_supplement.created_from_turn_run_id,
                "timestamp": _iso_now(),
            }
        )
        await _upsert_eval_trace_record(
            turn_run_id=source_turn_run_id,
            planning_snapshot=None,
            runtime_state=ReaderAskRuntimeState(),
            context_plan=None,
            supplement_audit_json=supplement_audit,
        )
    return ReaderAskDeleteSupplementResponse(
        deleted=True,
        supplement_id=str(supplement_id),
        record_id=str(supplement["record_id"]),
        target_key=str(supplement.get("target_key")) if supplement.get("target_key") else None,
        lifecycle_status="deleted",
        persisted_supplement=persisted_supplement,
    )
