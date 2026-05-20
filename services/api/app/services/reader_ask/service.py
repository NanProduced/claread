from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, Callable
from uuid import UUID, uuid4

from fastapi import HTTPException

from app.agents.reader_ask_agent import (
    ReaderAskAgentDeps,
    ReaderAskRuntimeActionRequest,
    ReaderAskRuntimeState,
    build_reader_ask_prompt,
    get_reader_ask_agent,
)
from app.config.settings import get_settings
from app.contracts.annotation import build_multi_text_target_key
from app.llm.router import build_model_for_route
from app.llm.routes import MODEL_ROUTE_READER_ASK
from app.llm.types import RunModelSettings
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
    ReaderAskCurrentRecordContext,
    ReaderAskDeleteSupplementResponse,
    ReaderAskEvidenceItem,
    ReaderAskEntryAction,
    ReaderAskExternalRecordContext,
    ReaderAskMessage,
    ReaderAskMessageStreamRequest,
    ReaderAskPageIdentity,
    ReaderAskPracticeCard,
    ReaderAskPersistedSupplement,
    ReaderAskReferenceResolutionStatus,
    ReaderAskResolvedContextInput,
    ReaderAskResolvedIntent,
    ReaderAskResolvedContextSummary,
    ReaderAskResponseCard,
    ReaderAskRunInfo,
    ReaderAskSentenceBreakdownCard,
    ReaderAskSentenceBreakdownPart,
    ReaderAskSupplementCandidate,
    ReaderAskThreadCreateRequest,
    ReaderAskThreadDetail,
    ReaderAskThreadListResponse,
    ReaderAskThreadSummary,
    ReaderAskTaskMode,
    ReaderAskTraceSummary,
    ReaderAskToolTraceEntry,
    ReaderAskVocabularyInContextCard,
)
from app.schemas.user_annotations import UserAnnotationCreateRequest, UserAnnotationSegment
from app.services import excerpt_assets as excerpt_assets_svc
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
    READER_ASK_RESERVED_POINTS,
)
from app.services.ai_usage.billing import MULTIPLIER_OUTPUT, TOKENS_PER_POINT
from app.services.analysis.credit_service import (
    CreditReservation,
    LEDGER_ENTRY_TYPE_AI_CAPABILITY_DEDUCT,
    check_quota,
    ensure_credit_account,
    refund_reserved_points,
    reserve_points,
)
from app.services.analysis.prompting.prompt_loader import get_prompt_version
from app.services.dictionary import get_service as get_dictionary_service
from app.services.dictionary.errors import ServiceUnavailableError, WordNotFoundError
from app.services.dictionary.schemas import DictionaryLookupRequest
from app.services.dictionary_ai.schemas import DictionaryAIContextExplainRequest
from app.services.dictionary_ai.service import get_service as get_dictionary_ai_service
from app.services.reader_ask import capabilities as capabilities_svc
from app.services.reader_ask import planner
from app.services.reader_ask import post_process as post_process_svc
from app.services.reader_ask import prompting as prompt_layers_svc
from app.services.reader_ask import repository as repo
from app.services.reader_ask import resolver as resolver_svc
from app.services.reader_ask import runtime_contract as runtime_contract_svc
from app.services.reader_ask import supplements as supplements_svc
from app.services.text_anchors import ensure_json_dict, sentence_map
from app.services.user_assets import favorites as favorites_svc
from app.services.user_assets import vocabulary as vocabulary_svc
from app.services import user_annotations as user_annotations_svc
from app.workflow.tracing import build_usage_metadata

_HISTORY_INTENT_RE = re.compile(r"(以前|之前|记过|收藏过|在哪见过|before|previous|earlier|history|seen this)")
_SAVE_NOTE_RE = re.compile(r"(保存.*笔记|记成笔记|save.*note|save this explanation)", re.IGNORECASE)
_SAVE_EXCERPT_RE = re.compile(r"(保存.*摘录|高亮一下|save.*excerpt|highlight this)", re.IGNORECASE)
_FAVORITE_RE = re.compile(r"(收藏|favorite|bookmark)", re.IGNORECASE)
_MAX_HISTORY_MESSAGES = 8
_MAX_CONTEXT_TEXT = 1200
_MAX_MESSAGE_TEXT = 800
_MAX_PROMPT_ASSET_ITEMS = 5
_DEFAULT_MAX_OUTPUT_TOKENS = 700
_MIN_MAX_OUTPUT_TOKENS = 160
_PROMPT_BUDGET_BUFFER_TOKENS = 800
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
}


@dataclass(slots=True)
class _RecordBundle:
    record_id: UUID
    title: str | None
    source_text: str
    render_scene: dict[str, Any]
    workflow_version: str | None
    schema_version: str | None


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.split()).strip()


def _truncate_text(value: str | None, limit: int) -> str:
    normalized = _normalize_text(value)
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[:limit]}..."


def _parse_uuid(value: str, detail: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=detail) from exc


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


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


def _matches_history_intent(content: str) -> bool:
    return bool(_HISTORY_INTENT_RE.search(content))


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
        anchor.anchor_type = "favorite" if attachment.subtype == "favorite" else "user_annotation"
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


def _resolve_intent(
    content: str,
    attachments: list[ReaderAskAttachment],
    entry_action: ReaderAskEntryAction,
) -> ReaderAskResolvedIntent:
    return planner.resolve_intent(content, attachments, entry_action)


def _needs_clarification(content: str, anchors: list[ReaderAskAnchorRef]) -> bool:
    return planner.needs_clarification(content, anchors)


def _query_seed(content: str, anchors: list[ReaderAskAnchorRef]) -> str:
    for anchor in anchors:
        selected = _first_anchor_text(anchor)
        if selected:
            return selected
    return _truncate_text(content, 80)


def _build_unused_reservation(reservation: CreditReservation, actual_cost_points: int) -> CreditReservation:
    if actual_cost_points >= reservation.total_points:
        return CreditReservation(total_points=0, deducted_from_daily=0, deducted_from_bonus=0)

    used_daily = min(actual_cost_points, reservation.deducted_from_daily)
    used_bonus = max(actual_cost_points - used_daily, 0)
    refund_daily = reservation.deducted_from_daily - used_daily
    refund_bonus = reservation.deducted_from_bonus - used_bonus
    refund_total = max(refund_daily, 0) + max(refund_bonus, 0)
    return CreditReservation(
        total_points=refund_total,
        deducted_from_daily=max(refund_daily, 0),
        deducted_from_bonus=max(refund_bonus, 0),
    )


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


def _estimate_token_count(payload: dict[str, Any]) -> int:
    serialized = json.dumps(payload, ensure_ascii=False)
    return max((len(serialized) + 3) // 4, 1)


def _compact_prompt_payload(payload: dict[str, Any]) -> dict[str, Any]:
    compact = json.loads(json.dumps(payload, ensure_ascii=False))
    history = compact.get("history")
    if isinstance(history, list) and len(history) > 4:
        compact["history"] = history[-4:]

    record_assets = compact.get("record_assets")
    if isinstance(record_assets, list) and len(record_assets) > 3:
        compact["record_assets"] = record_assets[:3]

    history_assets = compact.get("history_assets")
    if isinstance(history_assets, list) and len(history_assets) > 2:
        compact["history_assets"] = history_assets[:2]

    vocabulary_items = compact.get("vocabulary_items")
    if isinstance(vocabulary_items, list) and len(vocabulary_items) > 3:
        compact["vocabulary_items"] = vocabulary_items[:3]

    record_insights = compact.get("record_insights")
    if isinstance(record_insights, list) and len(record_insights) > 3:
        compact["record_insights"] = record_insights[:3]

    record_context = compact.get("record_context")
    if isinstance(record_context, dict):
        sentence_windows = record_context.get("sentence_windows")
        if isinstance(sentence_windows, list) and len(sentence_windows) > 3:
            record_context["sentence_windows"] = sentence_windows[:3]
        source_excerpt = record_context.get("source_excerpt")
        if isinstance(source_excerpt, str) and len(source_excerpt) > 800:
            record_context["source_excerpt"] = _truncate_text(source_excerpt, 800)
    article_overview = compact.get("article_overview")
    if isinstance(article_overview, str) and len(article_overview) > 400:
        compact["article_overview"] = _truncate_text(article_overview, 400)
    return compact


def _prepare_prompt_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
    prompt_payload = payload
    estimated_input_tokens = _estimate_token_count(prompt_payload)
    if estimated_input_tokens > 4500:
        prompt_payload = _compact_prompt_payload(payload)
        estimated_input_tokens = _estimate_token_count(prompt_payload)

    weighted_budget = READER_ASK_RESERVED_POINTS * TOKENS_PER_POINT
    weighted_remaining = max(weighted_budget - estimated_input_tokens - _PROMPT_BUDGET_BUFFER_TOKENS, 0)
    budgeted_output_tokens = max(
        _MIN_MAX_OUTPUT_TOKENS,
        min(_DEFAULT_MAX_OUTPUT_TOKENS, weighted_remaining // MULTIPLIER_OUTPUT if weighted_remaining else 0),
    )
    return prompt_payload, budgeted_output_tokens


async def _load_record_bundle(user_id: UUID, record_id: UUID) -> _RecordBundle:
    pool = repo.db_connection.DB_POOL if hasattr(repo, "db_connection") else None
    if pool is None:
        from app.database import connection as db_connection

        pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT r.id, r.title, r.source_text, a.render_scene_json, a.workflow_version, a.schema_version
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


def _render_scene_article_overview(record: _RecordBundle) -> str | None:
    direct = record.render_scene.get("content_summary")
    if isinstance(direct, dict):
        overview = direct.get("overview")
        if isinstance(overview, str) and overview.strip():
            return overview.strip()

    queue: list[Any] = [record.render_scene]
    while queue:
        current = queue.pop(0)
        if isinstance(current, dict):
            entry_type = current.get("entryType") or current.get("entry_type")
            node_type = current.get("type")
            overview = current.get("overview")
            if entry_type == "content_summary" or node_type == "reader_content_summary":
                if isinstance(overview, str) and overview.strip():
                    return overview.strip()
            queue.extend(current.values())
        elif isinstance(current, list):
            queue.extend(current)
    return None


def _current_record_source_labels(runtime_state: ReaderAskRuntimeState) -> list[str]:
    labels: list[str] = []
    if runtime_state.latest_record_context is not None:
        labels.append("current_paragraph")
    if runtime_state.latest_record_insights or runtime_state.latest_record_excerpt_assets:
        labels.append("record_assets")
    if runtime_state.latest_article_overview:
        labels.append("article_overview")
    if runtime_state.latest_dictionary_entry or runtime_state.latest_dictionary_ai:
        labels.append("dictionary")
    return labels


async def _load_external_record_contexts(
    user_id: UUID,
    *,
    current_record_id: UUID,
    planned_external_refs: list[dict[str, str]],
) -> list[ReaderAskExternalRecordContext]:
    contexts: list[ReaderAskExternalRecordContext] = []
    seen: set[str] = set()
    for item in planned_external_refs:
        record_id = str(item.get("record_id") or "").strip()
        if not record_id or record_id in seen:
            continue
        seen.add(record_id)
        record_uuid = _parse_uuid(record_id, "external record id is invalid")
        if record_uuid == current_record_id:
            continue
        bundle = await _load_record_bundle(user_id, record_uuid)
        article_overview = _render_scene_article_overview(bundle)
        contexts.append(
            ReaderAskExternalRecordContext(
                record_id=str(bundle.record_id),
                record_title=bundle.title or item.get("title"),
                article_overview=article_overview,
                source_labels=["external_record", *([] if article_overview else ["overview_missing"])],
                reason=str(item.get("reason") or "explicit_attachment"),
            )
        )
    return contexts


async def _materialize_planned_context(
    *,
    user_id: UUID,
    record: _RecordBundle,
    runtime_state: ReaderAskRuntimeState,
    planning_snapshot: planner.ReaderAskPlanningSnapshot,
    page_identity: ReaderAskPageIdentity,
    entry_action: ReaderAskEntryAction,
    attachments: list[ReaderAskAttachment],
    anchors: list[ReaderAskAnchorRef],
    get_record_context_cb: Callable[[], Any],
    get_record_insights_cb: Callable[[], Any],
) -> ReaderAskResolvedContextInput:
    working_set = planning_snapshot.working_set
    if working_set.local_context_window_needed and runtime_state.latest_record_context is None:
        runtime_state.latest_record_context = await get_record_context_cb()
        if runtime_state.latest_record_context is not None:
            runtime_state.source_labels.update({"current_record", "current_anchor", "current_paragraph"})
    if working_set.record_insights_needed and not runtime_state.latest_record_insights:
        runtime_state.latest_record_insights = await get_record_insights_cb()
        if runtime_state.latest_record_insights:
            runtime_state.source_labels.add("record_assets")
    if working_set.article_overview_needed and not runtime_state.latest_article_overview:
        article_overview = _render_scene_article_overview(record)
        if article_overview:
            runtime_state.latest_article_overview = article_overview
            runtime_state.source_labels.add("article_overview")

    external_record_contexts = await _load_external_record_contexts(
        user_id,
        current_record_id=record.record_id,
        planned_external_refs=working_set.external_record_refs,
    )
    if external_record_contexts:
        runtime_state.latest_external_record_contexts = [
            item.model_dump(mode="json") for item in external_record_contexts
        ]
        runtime_state.used_history_lookup = True
        runtime_state.source_labels.add("history_assets")

    current_record_context = ReaderAskCurrentRecordContext(
        record_id=str(record.record_id),
        record_title=record.title,
        local_context=runtime_state.latest_record_context,
        record_insights=runtime_state.latest_record_insights,
        article_overview=runtime_state.latest_article_overview,
        source_labels=_current_record_source_labels(runtime_state),
    )
    return planner.build_resolved_context_input(
        page_identity=page_identity,
        entry_action=entry_action,
        attachments=attachments,
        anchors=anchors,
        current_record_context=current_record_context,
        external_record_contexts=external_record_contexts,
    )


async def _resolve_annotation_anchor(conn: Any, user_id: UUID, anchor: ReaderAskAnchorRef) -> ReaderAskAnchorRef:
    if not anchor.anchor_id and not anchor.target_key:
        return anchor

    row = await conn.fetchrow(
        """
        SELECT id, annotation_type, anchor_type, target_key, paragraph_id, sentence_id,
               selected_text, start_offset, end_offset, text_hash, note, payload_json
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
            "note": row["note"],
            "payload_json": payload,
            "segments": segments or [],
            "label": row["annotation_type"],
        }
    )


async def _resolve_favorite_anchor(conn: Any, user_id: UUID, anchor: ReaderAskAnchorRef) -> ReaderAskAnchorRef:
    if not anchor.anchor_id and not anchor.target_key:
        return anchor

    row = await conn.fetchrow(
        """
        SELECT id, target_type, target_key, payload_json, note
        FROM favorite_records
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
            "anchor_type": "favorite",
            "target_key": row["target_key"],
            "target_type": row["target_type"],
            "sentence_id": payload.get("sentence_id"),
            "paragraph_id": payload.get("paragraph_id"),
            "selected_text": payload.get("selected_text"),
            "start_offset": payload.get("start_offset"),
            "end_offset": payload.get("end_offset"),
            "text_hash": payload.get("text_hash"),
            "note": row["note"],
            "payload_json": payload,
            "segments": segments or [],
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
    from app.database import connection as db_connection

    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    resolved: list[ReaderAskAnchorRef] = []
    async with pool.acquire() as conn:
        for raw_anchor in anchors:
            anchor = raw_anchor
            if anchor.anchor_type == "user_annotation":
                anchor = await _resolve_annotation_anchor(conn, user_id, anchor)
            elif anchor.anchor_type == "favorite":
                anchor = await _resolve_favorite_anchor(conn, user_id, anchor)
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
        return []
    translations = _translations_map(record)
    sentence_ids: list[str] = []
    for anchor in anchors:
        sentence_ids.extend(_sentence_ids_from_anchor(anchor))
    sentence_id_set = {sentence_id for sentence_id in sentence_ids if sentence_id}
    if not sentence_id_set:
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
    return results[:_MAX_PROMPT_ASSET_ITEMS]


def _flatten_excerpt_items(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for group in groups:
        group_title = group.get("title")
        group_record_id = group.get("record_id")
        for item in group.get("items", []):
            if not isinstance(item, dict):
                continue
            enriched = dict(item)
            enriched["record_id"] = group_record_id
            enriched["source_article_title"] = group_title
            items.append(enriched)
    return items


def _score_excerpt_item(item: dict[str, Any], *, sentence_ids: set[str], query: str) -> int:
    score = 0
    if sentence_ids and item.get("sentence_id") in sentence_ids:
        score += 5
    haystack = " ".join(
        _normalize_text(part)
        for part in (
            item.get("selected_text"),
            item.get("translation"),
            item.get("note"),
            item.get("source_article_title"),
        )
    ).lower()
    for token in _normalize_text(query).lower().split():
        if token and token in haystack:
            score += 2
    return score


async def _tool_get_record_excerpt_assets(
    user_id: UUID,
    record: _RecordBundle,
    anchors: list[ReaderAskAnchorRef],
    query: str,
) -> list[dict[str, Any]]:
    response = await excerpt_assets_svc.list_excerpt_assets(
        user_id=user_id,
        page=1,
        limit=20,
        record_id=str(record.record_id),
    )
    items = _flatten_excerpt_items(response.model_dump(mode="python")["groups"])
    sentence_ids = {sentence_id for anchor in anchors for sentence_id in _sentence_ids_from_anchor(anchor)}
    items.sort(key=lambda item: _score_excerpt_item(item, sentence_ids=sentence_ids, query=query), reverse=True)
    selected = [item for item in items if _score_excerpt_item(item, sentence_ids=sentence_ids, query=query) > 0]
    return selected[:_MAX_PROMPT_ASSET_ITEMS] or items[: min(_MAX_PROMPT_ASSET_ITEMS, len(items))]


async def _tool_search_user_excerpt_assets(
    user_id: UUID,
    current_record_id: UUID,
    query: str,
) -> list[dict[str, Any]]:
    response = await excerpt_assets_svc.list_excerpt_assets(
        user_id=user_id,
        page=1,
        limit=40,
        record_id=None,
    )
    items = [
        item for item in _flatten_excerpt_items(response.model_dump(mode="python")["groups"])
        if item.get("record_id") != str(current_record_id)
    ]
    items.sort(key=lambda item: _score_excerpt_item(item, sentence_ids=set(), query=query), reverse=True)
    return [item for item in items if _score_excerpt_item(item, sentence_ids=set(), query=query) > 0][:_MAX_PROMPT_ASSET_ITEMS]


async def _tool_search_user_vocabulary(user_id: UUID, query: str) -> list[dict[str, Any]]:
    items, _ = await vocabulary_svc.list_vocabulary(
        user_id=user_id,
        page=1,
        limit=200,
        lite=False,
    )
    query_lower = _normalize_text(query).lower()
    matches: list[dict[str, Any]] = []
    for item in items:
        lemma = str(item.get("lemma") or "")
        display_word = str(item.get("display_word") or "")
        source_sentence = str(item.get("source_sentence") or "")
        if query_lower and query_lower not in lemma.lower() and query_lower not in display_word.lower() and query_lower not in source_sentence.lower():
            continue
        matches.append(
            {
                "id": str(item.get("id")),
                "lemma": lemma,
                "display_word": display_word,
                "short_meaning": _truncate_text(item.get("short_meaning"), 80),
                "source_sentence": _truncate_text(source_sentence, 120),
                "mastery_status": item.get("mastery_status"),
            }
        )
    return matches[:_MAX_PROMPT_ASSET_ITEMS]


async def _tool_lookup_dictionary_entry(
    *,
    query: str | None,
    entry_id: int | None,
    query_type: str | None,
    context_sentence: str | None,
    occurrence: int | None,
) -> dict[str, Any] | None:
    service = get_dictionary_service()
    try:
        if entry_id is not None:
            result = await service.lookup_entry(entry_id)
        elif query:
            result = await service.lookup(
                DictionaryLookupRequest(
                    query=query,
                    query_type=query_type if query_type in {"word", "phrase"} else ("phrase" if " " in query else "word"),
                    context_sentence=context_sentence,
                    occurrence=occurrence,
                )
            )
        else:
            return None
    except (WordNotFoundError, ServiceUnavailableError):
        return None

    entry = result.get("entry") if isinstance(result, dict) else None
    if not isinstance(entry, dict):
        return None
    return {
        "id": entry.get("id"),
        "word": entry.get("word"),
        "base_word": entry.get("base_word"),
        "phonetic": entry.get("phonetic"),
        "meanings": entry.get("meanings", [])[:2],
        "query": result.get("query") if isinstance(result, dict) else query,
    }


async def _tool_run_dictionary_ai_context_explain(
    *,
    query: str,
    entry_id: int,
    context_sentence: str,
    query_type: str,
    occurrence: int | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    service = get_dictionary_ai_service()
    result = await service.run_context_explain(
        DictionaryAIContextExplainRequest(
            query=query,
            query_type=query_type if query_type in {"word", "phrase"} else ("phrase" if " " in query else "word"),
            context_sentence=context_sentence,
            occurrence=occurrence,
            entry_id=entry_id,
        )
    )
    response = result.response
    return (
        {
            "mode": response.mode,
            "query": response.query,
            "summary": response.summary,
            "best_fit_sense": response.best_fit_sense,
            "why_here": response.why_here,
            "cue": response.cue,
            "translation": response.translation,
            "contrast": response.contrast,
            "learning_tip": response.learning_tip,
            "confidence": response.confidence,
            "entry_id": entry_id,
        },
        result.usage_data,
    )


def _excerpt_item_to_citation(item: dict[str, Any], *, kind: str) -> ReaderAskCitation:
    return ReaderAskCitation(
        citation_id=str(uuid4()),
        kind=kind,  # type: ignore[arg-type]
        label=_truncate_text(item.get("selected_text"), 80) or _truncate_text(item.get("source_article_title"), 80) or "摘录资产",
        anchor_type=item.get("anchor_type"),
        sentence_id=item.get("sentence_id"),
        target_key=item.get("target_key"),
        selected_text=_truncate_text(item.get("selected_text"), 180) or None,
        record_id=item.get("record_id"),
        source_article_title=item.get("source_article_title"),
        metadata_json={
            "note": _truncate_text(item.get("note"), 120),
            "is_favorited": item.get("is_favorited"),
            "is_noted": item.get("is_noted"),
            "is_highlighted": item.get("is_highlighted"),
        },
    )


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


def _dictionary_item_to_citation(item: dict[str, Any]) -> ReaderAskCitation:
    return ReaderAskCitation(
        citation_id=str(uuid4()),
        kind="dictionary_entry",
        label=item.get("word") or item.get("base_word") or "词典词条",
        metadata_json={
            "dict_entry_id": item.get("id"),
            "phonetic": item.get("phonetic"),
            "meanings": item.get("meanings"),
        },
    )


def _dictionary_ai_to_citation(item: dict[str, Any], query: str, entry_id: int) -> ReaderAskCitation:
    return ReaderAskCitation(
        citation_id=str(uuid4()),
        kind="dictionary_ai",
        label=query or "词典 AI 解释",
        metadata_json={
            "dict_entry_id": entry_id,
            "summary": _truncate_text(item.get("summary"), 160),
            "best_fit_sense": item.get("best_fit_sense"),
            "translation": item.get("translation"),
            "confidence": item.get("confidence"),
        },
    )


def _merge_citation(citations: list[ReaderAskCitation], citation: ReaderAskCitation) -> None:
    for existing in citations:
        if (
            existing.kind == citation.kind
            and existing.label == citation.label
            and existing.record_id == citation.record_id
            and existing.target_key == citation.target_key
            and existing.sentence_id == citation.sentence_id
        ):
            return
    citations.append(citation)


def _build_prompt_payload(
    *,
    thread: dict[str, Any],
    record: _RecordBundle,
    user_message: str,
    history_messages: list[dict[str, Any]],
    page_identity: ReaderAskPageIdentity,
    attachments: list[ReaderAskAttachment],
    anchors: list[ReaderAskAnchorRef],
    resolved_intent: ReaderAskResolvedIntent,
    entry_action: ReaderAskEntryAction,
    history_lookup_allowed: bool,
    resolved_context_input: ReaderAskResolvedContextInput | None = None,
    reference_resolution: planner.ReaderAskReferenceResolution | None = None,
    planning_snapshot: planner.ReaderAskPlanningSnapshot | None = None,
) -> dict[str, Any]:
    prompt_layers = prompt_layers_svc.load_prompt_layers()
    history = [
        {
            "role": item["role"],
            "content_md": _truncate_text(item["content_md"], _MAX_MESSAGE_TEXT),
        }
        for item in history_messages[-_MAX_HISTORY_MESSAGES:]
    ]
    anchor_payload = [
        {
            "anchor_type": anchor.anchor_type,
            "label": anchor.label,
            "sentence_id": anchor.sentence_id,
            "selected_text": _truncate_text(_first_anchor_text(anchor), 200),
            "note": _truncate_text(anchor.note, 180) or None,
            "entry_type": anchor.entry_type,
        }
        for anchor in anchors
    ]
    attachment_payload = [
        {
            "kind": attachment.kind,
            "subtype": attachment.subtype,
            "label": attachment.label,
            "selected_text": _truncate_text(attachment.selected_text, 200) or None,
            "target_key": attachment.target_key,
            "metadata": attachment.metadata.model_dump(mode="json"),
        }
        for attachment in attachments
    ]
    return {
        "thread": {
            "id": thread["id"],
            "title": thread.get("title"),
        },
        "record": {
            "record_id": str(record.record_id),
            "title": record.title,
            "workflow_version": record.workflow_version,
            "schema_version": record.schema_version,
        },
        "page_identity": page_identity.model_dump(mode="json"),
        "entry_action": entry_action,
        "user_message": user_message,
        "resolved_intent": resolved_intent,
        "resolved_intent_label": _TASK_MODE_LABELS[resolved_intent],
        "prompt_layers": prompt_layers,
        "history": history,
        "attachments": attachment_payload,
        "anchors": anchor_payload,
        "reference_resolution": {
            "status": reference_resolution.status if reference_resolution else "not_needed",
            "query": reference_resolution.query if reference_resolution else None,
            "reason": reference_resolution.reason if reference_resolution else None,
            "resolved_records": reference_resolution.resolved_records if reference_resolution else [],
            "ambiguous_records": reference_resolution.ambiguous_records if reference_resolution else [],
        },
        "resolved_context_input": resolved_context_input.model_dump(mode="json") if resolved_context_input else None,
        "planning": {
            "retrieval_needs": planning_snapshot.retrieval_needs if planning_snapshot else "none",
            "working_set": {
                "primary_anchor_type": planning_snapshot.working_set.primary_anchor.anchor_type
                if planning_snapshot and planning_snapshot.working_set.primary_anchor
                else None,
                "local_context_window_needed": planning_snapshot.working_set.local_context_window_needed
                if planning_snapshot
                else bool(anchors),
                "record_insights_needed": planning_snapshot.working_set.record_insights_needed
                if planning_snapshot
                else False,
                "article_overview_needed": planning_snapshot.working_set.article_overview_needed
                if planning_snapshot
                else False,
                "dictionary_needed": planning_snapshot.working_set.dictionary_needed
                if planning_snapshot
                else False,
                "history_assets_allowed": planning_snapshot.working_set.history_assets_allowed
                if planning_snapshot
                else history_lookup_allowed,
                "external_record_refs": planning_snapshot.working_set.external_record_refs
                if planning_snapshot
                else [],
            },
            "context_plan": planning_snapshot.context_plan.model_dump(mode="json") if planning_snapshot else None,
            "trace_summary": planning_snapshot.trace_summary.model_dump(mode="json") if planning_snapshot else None,
        },
        "history_lookup_allowed": history_lookup_allowed,
        "tooling_contract": {
            "call_tools_on_demand": True,
            "history_lookup_requires_explicit_intent": history_lookup_allowed,
            "writes_require_confirmation": True,
            "dictionary_context_explain_available": True,
        },
        "response_contract": {
            "format": "markdown",
            "be_concise": True,
            "article_bound": True,
            "do_not_claim_unknown_history": True,
            "structured_cards_available": [
                "sentence_breakdown_card",
                "vocabulary_in_context_card",
                "practice_card",
            ],
        },
        "intent_instructions": {
            "explain": "优先解释这句话或这段在当前文章里的意思，回答以简洁 Markdown 为主。",
            "breakdown": "优先拆主干、修饰和阅读顺序；需要时调用解析相关工具。",
            "vocabulary": "优先解释词义、短语义和为什么在这里是这个意思；需要时使用词典和词典 AI。",
            "grammar": "优先解释当前句子里的语法作用和句法关系，不要泛化成整节语法课。",
            "practice": "优先围绕当前句子或段落生成练习，帮助用户主动复述、辨析或判断结构。",
        }[resolved_intent],
    }


def _message_metadata(
    *,
    resolved_intent: ReaderAskResolvedIntent | None = None,
    resolved_context: ReaderAskResolvedContextSummary | None = None,
    context_plan: ReaderAskContextPlan | None = None,
    resolved_context_input: ReaderAskResolvedContextInput | None = None,
    evidence: list[ReaderAskEvidenceItem] | None = None,
    trace_summary: ReaderAskTraceSummary | None = None,
    response_cards: list[ReaderAskResponseCard] | None = None,
    run_info: dict[str, Any] | None = None,
    supplement_candidates: list[dict[str, Any]] | None = None,
    persisted_supplements: list[dict[str, Any]] | None = None,
    run_history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    if resolved_intent is not None:
        metadata["resolved_intent"] = resolved_intent
    if resolved_context is not None:
        metadata["resolved_context"] = resolved_context.model_dump(mode="json")
    if context_plan is not None:
        metadata["context_plan"] = context_plan.model_dump(mode="json")
    if resolved_context_input is not None:
        metadata["resolved_context_input"] = resolved_context_input.model_dump(mode="json")
    if evidence:
        metadata["evidence"] = [item.model_dump(mode="json") for item in evidence]
    if trace_summary is not None:
        metadata["trace_summary"] = trace_summary.model_dump(mode="json")
    if response_cards:
        metadata["response_cards"] = [card.model_dump(mode="json") for card in response_cards]
    if run_info is not None:
        metadata["run_info"] = run_info
    if supplement_candidates:
        metadata["supplement_candidates"] = supplement_candidates
    if persisted_supplements:
        metadata["persisted_supplements"] = persisted_supplements
    if run_history:
        metadata["run_history"] = run_history
    return metadata


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


def _visible_output_from_message(message: ReaderAskMessage, message_dict: dict[str, Any]) -> dict[str, Any]:
    current = message_dict.get("current_user_visible_output")
    if isinstance(current, dict):
        return dict(current)
    return {
        "id": message.id,
        "thread_id": message.thread_id,
        "content_md": message.content_md,
        "resolved_intent": message.resolved_intent,
        "citations": [item.model_dump(mode="json") for item in message.citations],
        "action_proposals": [item.model_dump(mode="json") for item in message.action_proposals],
        "tool_trace": [item.model_dump(mode="json") for item in message.tool_trace],
        "evidence": [item.model_dump(mode="json") for item in message.evidence],
        "trace_summary": message.trace_summary.model_dump(mode="json") if message.trace_summary else None,
        "response_cards": [item.model_dump(mode="json") for item in message.response_cards],
        "usage_summary": None,
        "billed_points": 0,
        "resolved_context": message.resolved_context.model_dump(mode="json") if message.resolved_context else None,
        "context_plan": message.context_plan.model_dump(mode="json") if message.context_plan else None,
        "resolved_context_input": message.resolved_context_input.model_dump(mode="json")
        if message.resolved_context_input
        else None,
        "run_info": message.run_info.model_dump(mode="json") if message.run_info else None,
        "supplement_candidates": [item.model_dump(mode="json") for item in message.supplement_candidates],
        "persisted_supplements": [item.model_dump(mode="json") for item in message.persisted_supplements],
    }


def _planning_snapshot_json(planning_snapshot: planner.ReaderAskPlanningSnapshot | None) -> dict[str, Any]:
    if planning_snapshot is None:
        return {}
    return {
        "resolved_intent": planning_snapshot.resolved_intent,
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
        },
        "working_set": {
            "primary_anchor": planning_snapshot.working_set.primary_anchor.model_dump(mode="json")
            if planning_snapshot.working_set.primary_anchor
            else None,
            "local_context_window_needed": planning_snapshot.working_set.local_context_window_needed,
            "record_insights_needed": planning_snapshot.working_set.record_insights_needed,
            "article_overview_needed": planning_snapshot.working_set.article_overview_needed,
            "dictionary_needed": planning_snapshot.working_set.dictionary_needed,
            "history_assets_allowed": planning_snapshot.working_set.history_assets_allowed,
            "external_record_refs": planning_snapshot.working_set.external_record_refs,
        },
        "context_plan": planning_snapshot.context_plan.model_dump(mode="json"),
        "trace_summary": planning_snapshot.trace_summary.model_dump(mode="json"),
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
        "dictionary": {
            "used": bool(runtime_state.latest_dictionary_entry or runtime_state.latest_dictionary_ai),
            "reason": context_plan.dictionary_reason if context_plan else None,
            "source_labels": ["dictionary"]
            if runtime_state.latest_dictionary_entry or runtime_state.latest_dictionary_ai
            else [],
        },
        "external_record_context": {
            "used": bool(runtime_state.latest_external_record_contexts),
            "reason": context_plan.reference_resolution_reason if context_plan else None,
            "source_labels": ["history_assets"] if runtime_state.latest_external_record_contexts else [],
        },
    }


def _metrics_json(
    *,
    trace_summary: ReaderAskTraceSummary | None,
    billed_points: int,
    usage_event_id: UUID | None,
) -> dict[str, Any]:
    return {
        "planner_mode": trace_summary.planner_mode if trace_summary else None,
        "working_set_mode": trace_summary.working_set_mode if trace_summary else None,
        "history_lookup_allowed": trace_summary.history_lookup_allowed if trace_summary else False,
        "history_lookup_used": trace_summary.history_lookup_used if trace_summary else False,
        "used_known_reference_resolution": trace_summary.used_known_reference_resolution if trace_summary else False,
        "used_external_record_context": trace_summary.used_external_record_context if trace_summary else False,
        "billed_points": billed_points,
        "usage_event_id": str(usage_event_id) if usage_event_id else None,
        "prompt_version": get_prompt_version(),
    }


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
    return await repo.upsert_eval_trace(
        turn_run_id=turn_run_id,
        trace_schema_version=_EVAL_TRACE_SCHEMA_VERSION,
        planning_snapshot_json=_planning_snapshot_json(planning_snapshot) or (existing or {}).get("planning_snapshot_json") or {},
        capability_trace_json=_capability_trace_json(runtime_state=runtime_state, context_plan=context_plan)
        if context_plan is not None or runtime_state.source_labels
        else (existing or {}).get("capability_trace_json") or {},
        action_audit_json=action_audit_json if action_audit_json is not None else (existing or {}).get("action_audit_json") or [],
        supplement_audit_json=supplement_audit_json
        if supplement_audit_json is not None
        else (existing or {}).get("supplement_audit_json") or [],
        metrics_json=_metrics_json(
            trace_summary=trace_summary,
            billed_points=billed_points,
            usage_event_id=usage_event_id,
        )
        if trace_summary is not None or usage_event_id is not None or billed_points
        else (existing or {}).get("metrics_json") or {},
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


def _vocabulary_card(
    *,
    runtime_state: ReaderAskRuntimeState,
    record: _RecordBundle,
    anchors: list[ReaderAskAnchorRef],
) -> ReaderAskVocabularyInContextCard | None:
    dictionary_entry = runtime_state.latest_dictionary_entry
    if not dictionary_entry:
        dictionary_anchor = next((anchor for anchor in anchors if anchor.anchor_type == "dictionary_entry"), None)
        if dictionary_anchor and dictionary_anchor.query:
            return ReaderAskVocabularyInContextCard(
                query=dictionary_anchor.query,
                display_word=dictionary_anchor.query,
                source_sentence=_render_scene_sentence_text(record, dictionary_anchor.sentence_id) if dictionary_anchor.sentence_id else None,
            )
        return None

    meanings = dictionary_entry.get("meanings")
    meaning_zh = None
    if isinstance(meanings, list) and meanings:
        first_meaning = meanings[0]
        if isinstance(first_meaning, dict):
            meaning_zh = _truncate_text(first_meaning.get("definition_zh") or first_meaning.get("definition"), 160) or None
        else:
            meaning_zh = _truncate_text(str(first_meaning), 160) or None

    ai_context = runtime_state.latest_dictionary_ai
    source_sentence = None
    if anchors:
        source_sentence = _render_scene_sentence_text(record, anchors[0].sentence_id) or _first_anchor_text(anchors[0])
    return ReaderAskVocabularyInContextCard(
        query=str(dictionary_entry.get("query") or dictionary_entry.get("word") or ""),
        display_word=dictionary_entry.get("word") or dictionary_entry.get("base_word"),
        phonetic=dictionary_entry.get("phonetic"),
        meaning_zh=meaning_zh,
        why_here=_truncate_text(ai_context.get("why_here"), 180) if ai_context else None,
        translation_zh=_truncate_text(ai_context.get("translation"), 120) if ai_context else None,
        learning_tip=_truncate_text(ai_context.get("learning_tip"), 160) if ai_context else None,
        source_sentence=source_sentence,
    )


def _practice_card(
    *,
    record: _RecordBundle,
    anchors: list[ReaderAskAnchorRef],
    runtime_state: ReaderAskRuntimeState,
) -> ReaderAskPracticeCard | None:
    sentence_text = None
    sentence_id = None
    if anchors:
        sentence_id = anchors[0].sentence_id
        sentence_text = _render_scene_sentence_text(record, sentence_id) or _first_anchor_text(anchors[0])
    if not sentence_text and runtime_state.latest_record_context:
        windows = runtime_state.latest_record_context.get("sentence_windows")
        if isinstance(windows, list) and windows:
            first_window = windows[0]
            if isinstance(first_window, dict):
                sentence_text = first_window.get("anchor_text")
                sentence_id = first_window.get("sentence_id")
    if not sentence_text:
        return None

    insights = runtime_state.latest_record_insights
    insight_labels = [
        str(item.get("title") or item.get("entry_type"))
        for item in insights
        if isinstance(item, dict) and (item.get("title") or item.get("entry_type"))
    ]
    hints = [
        "先用自己的话说出这句话在段落里的意思。",
        "再指出一个关键结构或修饰关系。",
    ]
    if insight_labels:
        hints.append(f"可以特别留意：{insight_labels[0]}")
    return ReaderAskPracticeCard(
        title="围绕当前句做一题",
        prompt=f"请根据这句话完成练习：\n\n> {sentence_text}\n\n先解释句意，再指出一个关键结构或语法作用。",
        expected_focus=insight_labels[0] if insight_labels else "句意理解 + 结构识别",
        hints=hints[:3],
        answer_guidance="回答时尽量先说整体意思，再补一句你观察到的结构线索。",
        source_sentence=_render_scene_sentence_text(record, sentence_id) if sentence_id else sentence_text,
    )


def _build_response_cards(
    *,
    task_mode: ReaderAskTaskMode,
    record: _RecordBundle,
    anchors: list[ReaderAskAnchorRef],
    runtime_state: ReaderAskRuntimeState,
) -> list[ReaderAskResponseCard]:
    cards: list[ReaderAskResponseCard] = []
    if task_mode == "breakdown":
        card = _sentence_analysis_card(record=record, anchors=anchors, runtime_state=runtime_state)
        if card is not None:
            cards.append(card)
    elif task_mode == "vocabulary":
        card = _vocabulary_card(runtime_state=runtime_state, record=record, anchors=anchors)
        if card is not None:
            cards.append(card)
    elif task_mode == "practice":
        card = _practice_card(record=record, anchors=anchors, runtime_state=runtime_state)
        if card is not None:
            cards.append(card)
    return cards


async def _ensure_task_card_data(
    *,
    task_mode: ReaderAskTaskMode,
    runtime_state: ReaderAskRuntimeState,
    get_record_context_cb: Callable[[], Any],
    get_record_insights_cb: Callable[[], Any],
    lookup_dictionary_entry_cb: Callable[..., Any],
    run_dictionary_ai_context_explain_cb: Callable[..., Any],
    record: _RecordBundle,
    anchors: list[ReaderAskAnchorRef],
) -> None:
    if task_mode == "breakdown":
        if not runtime_state.latest_record_insights:
            runtime_state.latest_record_insights = await get_record_insights_cb()
            if runtime_state.latest_record_insights:
                runtime_state.source_labels.add("record_assets")
        return
    if task_mode == "practice":
        if runtime_state.latest_record_context is None:
            runtime_state.latest_record_context = await get_record_context_cb()
            if runtime_state.latest_record_context is not None:
                runtime_state.source_labels.add("current_paragraph")
        if not runtime_state.latest_record_insights:
            runtime_state.latest_record_insights = await get_record_insights_cb()
            if runtime_state.latest_record_insights:
                runtime_state.source_labels.add("record_assets")
        return
    if task_mode != "vocabulary":
        return

    if runtime_state.latest_dictionary_entry is None:
        dictionary_anchor = next((anchor for anchor in anchors if anchor.anchor_type == "dictionary_entry"), None)
        query = dictionary_anchor.query if dictionary_anchor else None
        entry_id = dictionary_anchor.dict_entry_id if dictionary_anchor else None
        sentence_text = None
        if anchors:
            sentence_text = _render_scene_sentence_text(record, anchors[0].sentence_id) or _first_anchor_text(anchors[0])
        runtime_state.latest_dictionary_entry = await lookup_dictionary_entry_cb(
            query,
            entry_id,
            "phrase" if query and " " in query else "word",
            sentence_text,
            None,
        )
        if runtime_state.latest_dictionary_entry is not None:
            runtime_state.source_labels.add("dictionary")
    if runtime_state.latest_dictionary_entry is None or runtime_state.latest_dictionary_ai is not None:
        return
    sentence_text = None
    if anchors:
        sentence_text = _render_scene_sentence_text(record, anchors[0].sentence_id) or _first_anchor_text(anchors[0])
    if not sentence_text:
        return
    query = str(runtime_state.latest_dictionary_entry.get("query") or runtime_state.latest_dictionary_entry.get("word") or "")
    entry_id = runtime_state.latest_dictionary_entry.get("id")
    if not query or not isinstance(entry_id, int):
        return
    runtime_state.latest_dictionary_ai = await run_dictionary_ai_context_explain_cb(
        query,
        entry_id,
        sentence_text,
        "phrase" if " " in query else "word",
        None,
    )
    if runtime_state.latest_dictionary_ai is not None:
        runtime_state.source_labels.add("dictionary")


def _build_action_proposals(
    *,
    user_message: str,
    record: _RecordBundle,
    anchors: list[ReaderAskAnchorRef],
    assistant_content_md: str,
) -> list[ReaderAskActionProposal]:
    proposals: list[ReaderAskActionProposal] = []
    primary_anchor = anchors[0] if anchors else None
    if primary_anchor is None:
        return proposals

    if _SAVE_NOTE_RE.search(user_message):
        proposals.append(
            ReaderAskActionProposal(
                id=str(uuid4()),
                action_type="save_answer_note",
                label="保存为笔记",
                description="把本条解释保存到当前锚点笔记",
                payload_json={
                    "record_id": str(record.record_id),
                    "anchor": primary_anchor.model_dump(mode="json"),
                    "note_text": assistant_content_md,
                },
            )
        )
    if _SAVE_EXCERPT_RE.search(user_message):
        proposals.append(
            ReaderAskActionProposal(
                id=str(uuid4()),
                action_type="save_excerpt",
                label="保存为高亮",
                description="把当前锚点保存成高亮/摘录",
                payload_json={
                    "record_id": str(record.record_id),
                    "anchor": primary_anchor.model_dump(mode="json"),
                },
            )
        )
    if _FAVORITE_RE.search(user_message):
        proposals.append(
            ReaderAskActionProposal(
                id=str(uuid4()),
                action_type="favorite_anchor",
                label="加入收藏",
                description="收藏当前锚点",
                payload_json={
                    "record_id": str(record.record_id),
                    "anchor": primary_anchor.model_dump(mode="json"),
                },
            )
        )
    return proposals


def _build_action_proposals_from_runtime(
    *,
    record: _RecordBundle,
    action_requests: list[ReaderAskRuntimeActionRequest],
    assistant_content_md: str,
) -> list[ReaderAskActionProposal]:
    proposals: list[ReaderAskActionProposal] = []
    for request in action_requests:
        payload_json = dict(request.payload_json)
        if request.action_type == "save_answer_note" and not str(payload_json.get("note_text") or "").strip():
            payload_json["note_text"] = assistant_content_md
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


def _build_context_plan(
    *,
    entry_action: ReaderAskEntryAction,
    attachments: list[ReaderAskAttachment],
    anchors: list[ReaderAskAnchorRef],
    runtime_state: ReaderAskRuntimeState,
    citations: list[ReaderAskCitation],
    reference_resolution: planner.ReaderAskReferenceResolution | None = None,
    planning_snapshot: planner.ReaderAskPlanningSnapshot | None = None,
) -> ReaderAskContextPlan:
    return planner.build_context_plan(
        entry_action=entry_action,
        attachments=attachments,
        anchors=anchors,
        runtime_state=runtime_state,
        citations=citations,
        reference_resolution=reference_resolution,
        planning_snapshot=planning_snapshot,
    )


def _build_resolved_context_input(
    *,
    page_identity: ReaderAskPageIdentity,
    entry_action: ReaderAskEntryAction,
    attachments: list[ReaderAskAttachment],
    anchors: list[ReaderAskAnchorRef],
    current_record_context: ReaderAskCurrentRecordContext | None = None,
    external_record_contexts: list[ReaderAskExternalRecordContext] | None = None,
) -> ReaderAskResolvedContextInput:
    return planner.build_resolved_context_input(
        page_identity=page_identity,
        entry_action=entry_action,
        attachments=attachments,
        anchors=anchors,
        current_record_context=current_record_context,
        external_record_contexts=external_record_contexts,
    )


def _resolved_context_summary(
    *,
    record: _RecordBundle,
    anchors: list[ReaderAskAnchorRef],
    explicit_attachment_count: int,
    runtime_state: ReaderAskRuntimeState,
    used_history_lookup: bool,
    citations: list[ReaderAskCitation],
) -> ReaderAskResolvedContextSummary:
    return planner.build_resolved_context_summary(
        record_id=str(record.record_id),
        record_title=record.title,
        anchors=anchors,
        explicit_attachment_count=explicit_attachment_count,
        runtime_state=runtime_state,
        used_history_lookup=used_history_lookup,
        citations=citations,
    )


def _build_trace_summary(
    *,
    runtime_state: ReaderAskRuntimeState,
    context_plan: ReaderAskContextPlan,
    planning_snapshot: planner.ReaderAskPlanningSnapshot | None = None,
    clarification_only: bool = False,
) -> ReaderAskTraceSummary:
    return planner.build_trace_summary(
        runtime_state=runtime_state,
        context_plan=context_plan,
        planning_snapshot=planning_snapshot,
        clarification_only=clarification_only,
    )


def _build_evidence_items(
    *,
    attachments: list[ReaderAskAttachment],
    citations: list[ReaderAskCitation],
    current_record_id: str | None = None,
    current_record_title: str | None = None,
    external_record_contexts: list[dict[str, Any]] | None = None,
    reference_resolution: planner.ReaderAskReferenceResolution | None = None,
    supplement_candidates: list[ReaderAskSupplementCandidate] | None = None,
    include_clarification: bool = False,
) -> list[ReaderAskEvidenceItem]:
    return post_process_svc.build_evidence_items(
        attachments=attachments,
        citations=citations,
        current_record_id=current_record_id,
        current_record_title=current_record_title,
        external_record_contexts=external_record_contexts,
        reference_resolution=reference_resolution,
        supplement_candidates=supplement_candidates,
        include_clarification=include_clarification,
    )


async def list_threads(user_id: UUID, record_id: str) -> ReaderAskThreadListResponse:
    record_uuid = _parse_uuid(record_id, "record_id must be a UUID")
    await repo.ensure_record_access(user_id, record_uuid)
    items = await repo.list_threads(user_id, record_uuid)
    return ReaderAskThreadListResponse(items=[ReaderAskThreadSummary.model_validate(item) for item in items])


async def list_context_records(
    user_id: UUID,
    *,
    query: str,
    exclude_record_id: str | None = None,
) -> ReaderAskContextRecordSearchResponse:
    normalized_query = query.strip()
    if not normalized_query:
        return ReaderAskContextRecordSearchResponse(items=[])

    exclude_uuid = _parse_uuid(exclude_record_id, "exclude_record_id must be a UUID") if exclude_record_id else None
    rows = await repo.search_records_by_title(
        user_id,
        query=normalized_query,
        exclude_record_id=exclude_uuid,
        limit=8,
    )
    return ReaderAskContextRecordSearchResponse(
        items=[
            {
                "record_id": row["id"],
                "title": row.get("title"),
                "updated_at": row.get("updated_at"),
            }
            for row in rows
        ]
    )


async def create_thread(user_id: UUID, body: ReaderAskThreadCreateRequest) -> ReaderAskThreadSummary:
    record_uuid = _parse_uuid(body.record_id, "record_id must be a UUID")
    record = await repo.ensure_record_access(user_id, record_uuid)
    if body.mode == "default":
        thread = await repo.get_or_create_default_thread(
            user_id,
            record_uuid,
            title=body.title or record.get("title") or "Ask Claread",
        )
    else:
        thread = await repo.create_new_chat_thread(
            user_id,
            record_uuid,
            title=body.title or "New chat",
        )
    return ReaderAskThreadSummary.model_validate(thread)


async def get_thread_detail(user_id: UUID, thread_id: UUID) -> ReaderAskThreadDetail:
    thread = await repo.get_thread(user_id, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Reader ask thread not found")
    messages = await repo.list_messages(thread_id, limit=100)
    return ReaderAskThreadDetail.model_validate({**thread, "messages": messages})


async def reset_thread(user_id: UUID, thread_id: UUID) -> ReaderAskThreadDetail:
    thread = await repo.get_thread(user_id, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Reader ask thread not found")

    record_id = _parse_uuid(thread["record_id"], "thread record_id is invalid")
    archived = await repo.archive_thread(user_id, thread_id)
    if archived is None:
        raise HTTPException(status_code=404, detail="Reader ask thread not found")

    next_thread = await repo.get_or_create_default_thread(
        user_id,
        record_id,
        title=thread.get("title") or "Ask Claread",
    )
    messages = await repo.list_messages(_parse_uuid(next_thread["id"], "thread id is invalid"), limit=100)
    return ReaderAskThreadDetail.model_validate({**next_thread, "messages": messages})


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
    resolved_intent: ReaderAskResolvedIntent | None = None
    resolved_context_input: ReaderAskResolvedContextInput | None = None
    context_plan: ReaderAskContextPlan | None = None
    evidence: list[ReaderAskEvidenceItem] = []
    trace_summary: ReaderAskTraceSummary | None = None
    run_info: dict[str, Any] | None = None
    active_turn_run_id: UUID | None = None
    planning_snapshot: planner.ReaderAskPlanningSnapshot | None = None
    reference_resolution = planner.ReaderAskReferenceResolution()
    persisted_supplements_json: list[dict[str, Any]] = []

    try:
        thread = await repo.get_thread(user_id, thread_id)
        if thread is None:
            raise HTTPException(status_code=404, detail="Reader ask thread not found")

        record_id = _parse_uuid(thread["record_id"], "thread record_id is invalid")
        record = await _load_record_bundle(user_id, record_id)
        history_messages = await repo.list_messages(thread_id, limit=100)
        if _parse_uuid(body.page_identity.record_id, "page_identity.record_id must be a UUID") != record.record_id:
            raise HTTPException(status_code=400, detail="page_identity.record_id does not match thread record")

        attachments = body.attachments
        incoming_anchors = _attachments_to_anchor_refs(attachments)
        resolved_anchors = await _resolve_anchor_refs(
            user_id,
            record,
            anchors=incoming_anchors,
        )
        anchor_payload = [anchor.model_dump(mode="json") for anchor in resolved_anchors]
        draft_plan = planner.plan_request(
            content=body.content,
            page_identity=body.page_identity,
            entry_action=body.entry_action,
            attachments=attachments,
            anchors=resolved_anchors,
        )
        reference_resolution = await resolver_svc.resolve_known_references(
            user_id=user_id,
            current_record_id=record.record_id,
            reference_needs=draft_plan.reference_needs,
        )
        planning_snapshot = planner.plan_request(
            content=body.content,
            page_identity=body.page_identity,
            entry_action=body.entry_action,
            attachments=attachments,
            anchors=resolved_anchors,
            reference_resolution=reference_resolution,
        )
        resolved_intent = planning_snapshot.resolved_intent
        resolved_context_input = planning_snapshot.resolved_context_input
        clarification_only = planning_snapshot.clarification_only
        if clarification_only:
            user_message = await repo.create_message(
                thread_id=thread_id,
                role="user",
                status="completed",
                content_md=body.content,
                context_anchors=anchor_payload,
                metadata=_message_metadata(
                    resolved_intent=resolved_intent,
                    resolved_context_input=resolved_context_input,
                ),
            )
            yield _sse("thread.ready", {"thread_id": str(thread_id), "record_id": str(record.record_id)})

            assistant_md = post_process_svc.build_clarification_message(
                local_anchor_required=_needs_clarification(body.content, resolved_anchors),
                reference_resolution=reference_resolution,
            )
            assistant_message = await repo.create_message(
                thread_id=thread_id,
                role="assistant",
                status="completed",
                content_md=assistant_md,
                context_anchors=anchor_payload,
                metadata=_message_metadata(
                    resolved_intent=resolved_intent,
                    resolved_context_input=resolved_context_input,
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
                status="completed",
                resolved_intent=resolved_intent,
            )
            run_info = _build_run_info(turn_id=user_message["id"], run_id=turn_run["id"], attempt=1)
            citations = [
                _anchor_to_citation(anchor, record_id=str(record.record_id), record_title=record.title)
                for anchor in resolved_anchors
            ]
            runtime_state = ReaderAskRuntimeState(
                citations=list(citations),
                source_labels={"current_record", *({"current_anchor"} if resolved_anchors else set())},
            )
            context_plan = _build_context_plan(
                entry_action=body.entry_action,
                attachments=attachments,
                anchors=resolved_anchors,
                runtime_state=runtime_state,
                citations=citations,
                reference_resolution=reference_resolution,
                planning_snapshot=planning_snapshot,
            )
            evidence = _build_evidence_items(
                attachments=attachments,
                citations=citations,
                current_record_id=str(record.record_id),
                current_record_title=record.title,
                reference_resolution=reference_resolution,
                include_clarification=True,
            )
            trace_summary = _build_trace_summary(
                runtime_state=runtime_state,
                context_plan=context_plan,
                planning_snapshot=planning_snapshot,
                clarification_only=True,
            )
            payload = ReaderAskCompletedPayload(
                id=assistant_message["id"],
                thread_id=str(thread_id),
                content_md=assistant_md,
                resolved_intent=resolved_intent,
                citations=citations,
                action_proposals=[],
                tool_trace=[],
                evidence=evidence,
                trace_summary=trace_summary,
                response_cards=[],
                usage_summary=None,
                billed_points=0,
                resolved_context=_resolved_context_summary(
                    record=record,
                    anchors=resolved_anchors,
                    explicit_attachment_count=len(attachments),
                    runtime_state=runtime_state,
                    used_history_lookup=False,
                    citations=citations,
                ),
                context_plan=context_plan,
                resolved_context_input=resolved_context_input,
                run_info=run_info,
                persisted_supplements=[],
            )
            assistant_metadata = _message_metadata(
                resolved_intent=resolved_intent,
                resolved_context=payload.resolved_context,
                context_plan=payload.context_plan,
                resolved_context_input=resolved_context_input,
                evidence=evidence,
                trace_summary=trace_summary,
                response_cards=[],
                run_info=run_info,
                persisted_supplements=[],
            )
            yield _sse("message.started", {"message_id": assistant_message["id"], "reply_to": user_message["id"]})
            yield _sse("message.delta", {"message_id": assistant_message["id"], "delta": assistant_md})
            await repo.update_message(
                message_id=_parse_uuid(assistant_message["id"], "assistant message id is invalid"),
                status="completed",
                content_md=assistant_md,
                context_anchors=anchor_payload,
                citations=[citation.model_dump(mode="json") for citation in citations],
                action_proposals=[],
                tool_trace=[],
                metadata=assistant_metadata,
                usage_event_id=None,
                current_turn_run_id=_parse_uuid(turn_run["id"], "turn run id is invalid"),
            )
            await repo.update_turn_run(
                turn_run_id=_parse_uuid(turn_run["id"], "turn run id is invalid"),
                status="completed",
                resolved_intent=resolved_intent,
                user_visible_output_json=payload.model_dump(mode="json"),
                completed_at=datetime.now(UTC),
            )
            await _upsert_eval_trace_record(
                turn_run_id=_parse_uuid(turn_run["id"], "turn run id is invalid"),
                planning_snapshot=planning_snapshot,
                runtime_state=runtime_state,
                context_plan=context_plan,
                trace_summary=trace_summary,
            )
            yield _sse("message.completed", payload.model_dump(mode="json"))
            return

        await ensure_credit_account(user_id)
        remaining = await check_quota(user_id)
        if remaining < READER_ASK_RESERVED_POINTS:
            yield _sse(
                "error",
                {
                    "code": "INSUFFICIENT_CREDITS",
                    "detail": "Not enough credits for this Ask Claread request.",
                    "remaining_points": remaining,
                    "required_points": READER_ASK_RESERVED_POINTS,
                },
            )
            return

        reservation_metadata = {
            "capability_code": CAPABILITY_READER_ASK,
            "thread_id": str(thread_id),
            "record_id": str(record.record_id),
            "billing_policy_version": build_reader_ask_billing_metadata(None)["billing_policy_version"],
            "reserved_points": READER_ASK_RESERVED_POINTS,
            "user_message": _truncate_text(body.content, 200),
        }
        reservation = await reserve_points(
            user_id,
            READER_ASK_RESERVED_POINTS,
            task_id=None,
            entry_type=LEDGER_ENTRY_TYPE_AI_CAPABILITY_DEDUCT,
            metadata=reservation_metadata,
        )
        if reservation is None:
            remaining = await check_quota(user_id)
            yield _sse(
                "error",
                {
                    "code": "INSUFFICIENT_CREDITS",
                    "detail": "Not enough credits for this Ask Claread request.",
                    "remaining_points": remaining,
                    "required_points": READER_ASK_RESERVED_POINTS,
                },
            )
            return

        user_message = await repo.create_message(
            thread_id=thread_id,
            role="user",
            status="completed",
            content_md=body.content,
            context_anchors=anchor_payload,
            metadata=_message_metadata(
                resolved_intent=resolved_intent,
                resolved_context_input=resolved_context_input,
            ),
        )
        yield _sse("thread.ready", {"thread_id": str(thread_id), "record_id": str(record.record_id)})

        assistant_message = await repo.create_message(
            thread_id=thread_id,
            role="assistant",
            status="streaming",
            content_md="",
            context_anchors=anchor_payload,
            metadata=_message_metadata(
                resolved_intent=resolved_intent,
                resolved_context_input=resolved_context_input,
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
            metadata=_message_metadata(
                resolved_intent=resolved_intent,
                resolved_context_input=resolved_context_input,
                run_info=run_info,
            ),
            usage_event_id=None,
            current_turn_run_id=_parse_uuid(turn_run["id"], "turn run id is invalid"),
        )
        yield _sse("message.started", {"message_id": assistant_message["id"], "reply_to": user_message["id"]})

        base_citations = [
            _anchor_to_citation(anchor, record_id=str(record.record_id), record_title=record.title)
            for anchor in resolved_anchors
        ]
        runtime_state = ReaderAskRuntimeState(
            citations=list(base_citations),
            source_labels={"current_record", *({"current_anchor"} if resolved_anchors else set())},
        )
        query_seed = _query_seed(body.content, resolved_anchors)
        if planning_snapshot is None:
            raise RuntimeError("planning snapshot is required")
        history_lookup_allowed = planning_snapshot.retrieval_needs == "known_reference_only"

        agent = get_reader_ask_agent()
        model, model_config = build_model_for_route(get_settings(), MODEL_ROUTE_READER_ASK)
        if model is None:
            raise RuntimeError("model route is not configured: reader_ask")

        route_settings = RunModelSettings(max_tokens=_DEFAULT_MAX_OUTPUT_TOKENS, temperature=0.3, timeout=45.0)
        if model_config and model_config.model_settings is not None:
            route_settings = route_settings.merged_with(model_config.model_settings)
        route_settings = RunModelSettings(
            max_tokens=route_settings.max_tokens or _DEFAULT_MAX_OUTPUT_TOKENS,
            temperature=route_settings.temperature,
            timeout=route_settings.timeout,
        )

        event_queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
        primary_anchor = resolved_anchors[0] if resolved_anchors else None
        dictionary_anchor = next((anchor for anchor in resolved_anchors if anchor.anchor_type == "dictionary_entry"), None)

        async def get_record_context_cb() -> dict[str, Any]:
            return {
                "title": record.title,
                "source_excerpt": _truncate_text(record.source_text, _MAX_CONTEXT_TEXT),
                "sentence_windows": _collect_sentence_windows(record, resolved_anchors),
            }

        async def get_record_insights_cb() -> list[dict[str, Any]]:
            return _collect_sentence_entries(record, resolved_anchors)

        async def get_record_excerpt_assets_cb(query: str) -> list[dict[str, Any]]:
            return await _tool_get_record_excerpt_assets(user_id, record, resolved_anchors, query)

        async def search_user_excerpt_assets_cb(query: str) -> list[dict[str, Any]]:
            return await _tool_search_user_excerpt_assets(user_id, record.record_id, query)

        async def search_user_vocabulary_cb(query: str) -> list[dict[str, Any]]:
            return await _tool_search_user_vocabulary(user_id, query)

        async def lookup_dictionary_entry_cb(
            query: str | None,
            entry_id: int | None,
            query_type: str | None,
            context_sentence: str | None,
            occurrence: int | None,
        ) -> dict[str, Any] | None:
            fallback_query = query
            fallback_entry_id = entry_id
            if dictionary_anchor is not None:
                fallback_query = fallback_query or dictionary_anchor.query
                fallback_entry_id = fallback_entry_id or dictionary_anchor.dict_entry_id
            return await _tool_lookup_dictionary_entry(
                query=fallback_query,
                entry_id=fallback_entry_id,
                query_type=query_type,
                context_sentence=context_sentence,
                occurrence=occurrence,
            )

        async def run_dictionary_ai_context_explain_cb(
            query: str,
            entry_id: int,
            context_sentence: str,
            query_type: str,
            occurrence: int | None,
        ) -> dict[str, Any] | None:
            result, usage = await _tool_run_dictionary_ai_context_explain(
                query=query,
                entry_id=entry_id,
                context_sentence=context_sentence,
                query_type=query_type,
                occurrence=occurrence,
            )
            if usage:
                nested_tool_usages.append({"tool_name": "run_dictionary_ai_context_explain", "usage_summary": usage})
            return result

        resolved_context_input = await _materialize_planned_context(
            user_id=user_id,
            record=record,
            runtime_state=runtime_state,
            planning_snapshot=planning_snapshot,
            page_identity=body.page_identity,
            entry_action=body.entry_action,
            attachments=attachments,
            anchors=resolved_anchors,
            get_record_context_cb=get_record_context_cb,
            get_record_insights_cb=get_record_insights_cb,
        )
        context_plan = _build_context_plan(
            entry_action=body.entry_action,
            attachments=attachments,
            anchors=resolved_anchors,
            runtime_state=runtime_state,
            citations=runtime_state.citations,
            reference_resolution=reference_resolution,
            planning_snapshot=planning_snapshot,
        )
        trace_summary = _build_trace_summary(
            runtime_state=runtime_state,
            context_plan=context_plan,
            planning_snapshot=planning_snapshot,
        )
        await _upsert_eval_trace_record(
            turn_run_id=_parse_uuid(turn_run["id"], "turn run id is invalid"),
            planning_snapshot=planning_snapshot,
            runtime_state=runtime_state,
            context_plan=context_plan,
            trace_summary=trace_summary,
        )
        prompt_payload = runtime_contract_svc.build_prompt_payload(
            thread=thread,
            record=record,
            user_message=body.content,
            history_messages=history_messages,
            page_identity=body.page_identity,
            attachments=attachments,
            anchors=resolved_anchors,
            resolved_intent=resolved_intent,
            entry_action=body.entry_action,
            history_lookup_allowed=history_lookup_allowed,
            resolved_context_input=resolved_context_input,
            reference_resolution=reference_resolution,
            planning_snapshot=planning_snapshot,
            resolved_intent_label=_TASK_MODE_LABELS[resolved_intent],
            max_history_messages=_MAX_HISTORY_MESSAGES,
            max_message_text=_MAX_MESSAGE_TEXT,
        )
        prompt_payload, max_output_tokens = runtime_contract_svc.prepare_prompt_payload(
            prompt_payload,
            reserved_points=READER_ASK_RESERVED_POINTS,
            tokens_per_point=TOKENS_PER_POINT,
            multiplier_output=MULTIPLIER_OUTPUT,
            budget_buffer_tokens=_PROMPT_BUDGET_BUFFER_TOKENS,
            default_max_output_tokens=_DEFAULT_MAX_OUTPUT_TOKENS,
            min_max_output_tokens=_MIN_MAX_OUTPUT_TOKENS,
        )
        route_settings = RunModelSettings(
            max_tokens=min(route_settings.max_tokens or _DEFAULT_MAX_OUTPUT_TOKENS, max_output_tokens),
            temperature=route_settings.temperature,
            timeout=route_settings.timeout,
        )

        deps = ReaderAskAgentDeps(
            payload=prompt_payload,
            event_queue=event_queue,
            state=runtime_state,
            query_seed=query_seed,
            task_mode=resolved_intent,
            record_id=str(record.record_id),
            record_title=record.title,
            primary_anchor=primary_anchor,
            history_lookup_allowed=history_lookup_allowed,
            get_record_context_fn=get_record_context_cb,
            get_record_insights_fn=get_record_insights_cb,
            get_record_excerpt_assets_fn=get_record_excerpt_assets_cb,
            search_user_excerpt_assets_fn=search_user_excerpt_assets_cb,
            search_user_vocabulary_fn=search_user_vocabulary_cb,
            lookup_dictionary_entry_fn=lookup_dictionary_entry_cb,
            run_dictionary_ai_context_explain_fn=run_dictionary_ai_context_explain_cb,
            excerpt_item_to_citation_fn=lambda item, kind: _excerpt_item_to_citation(item, kind=kind),
            vocabulary_item_to_citation_fn=_vocabulary_item_to_citation,
            dictionary_item_to_citation_fn=_dictionary_item_to_citation,
            dictionary_ai_to_citation_fn=_dictionary_ai_to_citation,
        )

        content_parts: list[str] = []
        usage_summary: dict[str, Any] | None = None
        producer_done = asyncio.Event()
        producer_error: Exception | None = None

        async def run_agent_stream() -> None:
            nonlocal usage_summary, producer_error
            try:
                async with agent.run_stream(
                    build_reader_ask_prompt(deps),
                    deps=deps,
                    model=model,
                    model_settings=route_settings.to_pydantic_ai(),
                ) as result:
                    async for delta in result.stream_text(delta=True, debounce_by=None):
                        if not delta:
                            continue
                        content_parts.append(delta)
                        await event_queue.put(
                            (
                                "message.delta",
                                {"message_id": assistant_message["id"], "delta": delta},
                            )
                        )
                    usage_summary = build_usage_metadata(result.usage())
            except Exception as exc:
                producer_error = exc
            finally:
                producer_done.set()

        producer_task = asyncio.create_task(run_agent_stream())
        try:
            while not producer_done.is_set() or not event_queue.empty():
                try:
                    event_name, event_payload = await asyncio.wait_for(event_queue.get(), timeout=0.1)
                except TimeoutError:
                    continue
                yield _sse(event_name, event_payload)
        finally:
            await producer_task

        if producer_error is not None:
            raise producer_error

        final_content_md = "".join(content_parts).strip()
        await _ensure_task_card_data(
            task_mode=resolved_intent,
            runtime_state=runtime_state,
            get_record_context_cb=get_record_context_cb,
            get_record_insights_cb=get_record_insights_cb,
            lookup_dictionary_entry_cb=lookup_dictionary_entry_cb,
            run_dictionary_ai_context_explain_cb=run_dictionary_ai_context_explain_cb,
            record=record,
            anchors=resolved_anchors,
        )
        if resolved_intent == "vocabulary":
            if runtime_state.latest_dictionary_entry is not None:
                runtime_state.source_labels.add("dictionary")
                _merge_citation(
                    runtime_state.citations,
                    _dictionary_item_to_citation(runtime_state.latest_dictionary_entry),
                )
            if runtime_state.latest_dictionary_ai is not None and runtime_state.latest_dictionary_entry is not None:
                query = str(
                    runtime_state.latest_dictionary_entry.get("query")
                    or runtime_state.latest_dictionary_entry.get("word")
                    or ""
                )
                entry_id = runtime_state.latest_dictionary_entry.get("id")
                if query and isinstance(entry_id, int):
                    _merge_citation(
                        runtime_state.citations,
                        _dictionary_ai_to_citation(runtime_state.latest_dictionary_ai, query, entry_id),
                    )
        runtime_proposals = _build_action_proposals_from_runtime(
            record=record,
            action_requests=runtime_state.action_requests,
            assistant_content_md=final_content_md,
        )
        fallback_proposals = _build_action_proposals(
            user_message=body.content,
            record=record,
            anchors=resolved_anchors,
            assistant_content_md=final_content_md,
        )
        action_proposals = _merge_action_proposals(runtime_proposals, fallback_proposals)
        usage_summary = _merge_usage_summaries(usage_summary, nested_tool_usages)
        response_cards = _build_response_cards(
            task_mode=resolved_intent,
            record=record,
            anchors=resolved_anchors,
            runtime_state=runtime_state,
        )
        resolved_context = _resolved_context_summary(
            record=record,
            anchors=resolved_anchors,
            explicit_attachment_count=len(attachments),
            runtime_state=runtime_state,
            used_history_lookup=runtime_state.used_history_lookup,
            citations=runtime_state.citations,
        )
        typed_supplement_candidates = capabilities_svc.build_supplement_candidates(
            resolved_intent=resolved_intent,
            anchors=resolved_anchors,
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
            reference_resolution=reference_resolution,
            supplement_candidates=typed_supplement_candidates,
        )
        trace_summary = trace_summary.model_copy(
            update={
                "supplement_generation_used": bool(typed_supplement_candidates),
                "supplement_persisted_count": 0,
                "supplement_deleted_count": 0,
            }
        )

        computed_cost_points = compute_reader_ask_cost_points(usage_summary)
        billed_points = min(computed_cost_points, reservation.total_points)
        unused_reservation = _build_unused_reservation(reservation, billed_points)
        if unused_reservation.total_points > 0:
            await refund_reserved_points(
                user_id,
                unused_reservation,
                metadata={
                    "reason": "reader_ask_unused_reservation",
                    "thread_id": str(thread_id),
                    "record_id": str(record.record_id),
                },
            )
            reservation = billed_points and CreditReservation(
                total_points=billed_points,
                deducted_from_daily=min(billed_points, reservation.deducted_from_daily),
                deducted_from_bonus=max(billed_points - min(billed_points, reservation.deducted_from_daily), 0),
            ) or CreditReservation(total_points=0, deducted_from_daily=0, deducted_from_bonus=0)
        else:
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
                billing_policy_version=build_reader_ask_billing_metadata(usage_summary).get("billing_policy_version"),
                metadata_json={
                    "entrypoint": "/reader-ask/threads/{thread_id}/messages/stream",
                    "thread_id": str(thread_id),
                    "message_id": assistant_message["id"],
                    "history_lookup_used": runtime_state.used_history_lookup,
                    "anchor_count": len(resolved_anchors),
                    "tool_names": [entry.tool_name for entry in runtime_state.tool_trace if entry.status == "completed"],
                    "reservation_points": READER_ASK_RESERVED_POINTS,
                    "computed_cost_points": computed_cost_points,
                    "clamped_to_reservation": computed_cost_points > READER_ASK_RESERVED_POINTS,
                },
                **build_model_metadata(model_config),
            )
        )

        updated = await repo.update_message(
            message_id=_parse_uuid(assistant_message["id"], "assistant message id is invalid"),
            status="completed",
            content_md=final_content_md,
            context_anchors=anchor_payload,
            citations=[citation.model_dump(mode="json") for citation in runtime_state.citations],
            action_proposals=[proposal.model_dump(mode="json") for proposal in action_proposals],
            tool_trace=[entry.model_dump(mode="json") for entry in runtime_state.tool_trace],
            metadata=_message_metadata(
                resolved_intent=resolved_intent,
                resolved_context=resolved_context,
                context_plan=context_plan,
                resolved_context_input=resolved_context_input,
                evidence=evidence,
                trace_summary=trace_summary,
                response_cards=response_cards,
                run_info=run_info,
                supplement_candidates=supplement_candidates,
                persisted_supplements=[],
            ),
            usage_event_id=usage_event_id,
            current_turn_run_id=_parse_uuid(turn_run["id"], "turn run id is invalid"),
        )
        payload = ReaderAskCompletedPayload(
            id=updated["id"],
            thread_id=str(thread_id),
            content_md=final_content_md,
            resolved_intent=resolved_intent,
            citations=runtime_state.citations,
            action_proposals=action_proposals,
            tool_trace=runtime_state.tool_trace,
            evidence=evidence,
            trace_summary=trace_summary,
            response_cards=response_cards,
            usage_summary=usage_summary,
            billed_points=billed_points,
            resolved_context=resolved_context,
            context_plan=context_plan,
            resolved_context_input=resolved_context_input,
            run_info=run_info,
            supplement_candidates=supplement_candidates,
            persisted_supplements=[],
        )
        await repo.update_turn_run(
            turn_run_id=_parse_uuid(turn_run["id"], "turn run id is invalid"),
            status="completed",
            resolved_intent=resolved_intent,
            user_visible_output_json=payload.model_dump(mode="json"),
            usage_summary_json=usage_summary,
            usage_event_id=usage_event_id,
            completed_at=datetime.now(UTC),
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
        yield _sse("message.completed", payload.model_dump(mode="json"))
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
                content_md="",
                context_anchors=anchor_payload,
                citations=[citation.model_dump(mode="json") for citation in runtime_state.citations],
                action_proposals=[],
                tool_trace=[entry.model_dump(mode="json") for entry in runtime_state.tool_trace],
                metadata=_message_metadata(
                    resolved_intent=resolved_intent,
                    resolved_context_input=resolved_context_input,
                    evidence=evidence,
                    trace_summary=trace_summary,
                    run_info=run_info,
                ),
                usage_event_id=None,
                current_turn_run_id=active_turn_run_id,
            )
            if active_turn_run_id is not None:
                await repo.update_turn_run(
                    turn_run_id=active_turn_run_id,
                    status="failed",
                    resolved_intent=resolved_intent,
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
                },
            )
        if isinstance(exc, HTTPException):
            yield _sse("error", {"code": str(exc.status_code), "detail": exc.detail})
            return
        if "model route is not configured" in str(exc):
            yield _sse("error", {"code": "MODEL_UNAVAILABLE", "detail": "Ask Claread is temporarily unavailable."})
            return
        detail = str(exc) if get_settings().app_env != "production" else "Ask Claread is temporarily unavailable."
        yield _sse("error", {"code": "READER_ASK_FAILED", "detail": detail})


async def retry_thread_message(
    user_id: UUID,
    thread_id: UUID,
    message_id: UUID,
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
    reference_resolution = planner.ReaderAskReferenceResolution()

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
        )

        record_id = _parse_uuid(thread["record_id"], "thread record_id is invalid")
        record = await _load_record_bundle(user_id, record_id)
        if _parse_uuid(body.page_identity.record_id, "page_identity.record_id must be a UUID") != record.record_id:
            raise HTTPException(status_code=400, detail="page_identity.record_id does not match thread record")

        attachments = body.attachments
        incoming_anchors = _attachments_to_anchor_refs(attachments)
        resolved_anchors = await _resolve_anchor_refs(
            user_id,
            record,
            anchors=incoming_anchors,
        )
        anchor_payload = [anchor.model_dump(mode="json") for anchor in resolved_anchors]
        draft_plan = planner.plan_request(
            content=body.content,
            page_identity=body.page_identity,
            entry_action=body.entry_action,
            attachments=attachments,
            anchors=resolved_anchors,
        )
        reference_resolution = await resolver_svc.resolve_known_references(
            user_id=user_id,
            current_record_id=record.record_id,
            reference_needs=draft_plan.reference_needs,
        )
        planning_snapshot = planner.plan_request(
            content=body.content,
            page_identity=body.page_identity,
            entry_action=body.entry_action,
            attachments=attachments,
            anchors=resolved_anchors,
            reference_resolution=reference_resolution,
        )
        resolved_intent = planning_snapshot.resolved_intent
        resolved_context_input = planning_snapshot.resolved_context_input
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
        clarification_only = planning_snapshot.clarification_only
        if clarification_only:
            assistant_md = post_process_svc.build_clarification_message(
                local_anchor_required=_needs_clarification(body.content, resolved_anchors),
                reference_resolution=reference_resolution,
            )
            citations = [
                _anchor_to_citation(anchor, record_id=str(record.record_id), record_title=record.title)
                for anchor in resolved_anchors
            ]
            runtime_state = ReaderAskRuntimeState(
                citations=list(citations),
                source_labels={"current_record", *({"current_anchor"} if resolved_anchors else set())},
            )
            resolved_context = _resolved_context_summary(
                record=record,
                anchors=resolved_anchors,
                explicit_attachment_count=len(attachments),
                runtime_state=runtime_state,
                used_history_lookup=False,
                citations=citations,
            )
            context_plan = _build_context_plan(
                entry_action=body.entry_action,
                attachments=attachments,
                anchors=resolved_anchors,
                runtime_state=runtime_state,
                citations=citations,
                reference_resolution=reference_resolution,
                planning_snapshot=planning_snapshot,
            )
            evidence = _build_evidence_items(
                attachments=attachments,
                citations=citations,
                current_record_id=str(record.record_id),
                current_record_title=record.title,
                reference_resolution=reference_resolution,
                include_clarification=True,
            )
            trace_summary = _build_trace_summary(
                runtime_state=runtime_state,
                context_plan=context_plan,
                planning_snapshot=planning_snapshot,
                clarification_only=True,
            )
            yield _sse("thread.ready", {"thread_id": str(thread_id), "record_id": str(record.record_id)})
            yield _sse("message.started", {"message_id": assistant_message["id"], "reply_to": user_message["id"]})
            yield _sse("message.delta", {"message_id": assistant_message["id"], "delta": assistant_md})
            await repo.update_message(
                message_id=message_id,
                status="completed",
                content_md=assistant_md,
                context_anchors=anchor_payload,
                citations=[citation.model_dump(mode="json") for citation in citations],
                action_proposals=[],
                tool_trace=[],
                metadata=_message_metadata(
                    resolved_intent=resolved_intent,
                    resolved_context=resolved_context,
                    context_plan=context_plan,
                    resolved_context_input=resolved_context_input,
                    evidence=evidence,
                    trace_summary=trace_summary,
                    response_cards=[],
                    run_info=run_info,
                    run_history=run_history,
                    persisted_supplements=persisted_supplements_json,
                ),
                usage_event_id=None,
                current_turn_run_id=_parse_uuid(turn_run["id"], "turn run id is invalid"),
            )
            payload = ReaderAskCompletedPayload(
                id=str(message_id),
                thread_id=str(thread_id),
                content_md=assistant_md,
                resolved_intent=resolved_intent,
                citations=citations,
                action_proposals=[],
                tool_trace=[],
                evidence=evidence,
                trace_summary=trace_summary,
                response_cards=[],
                usage_summary=None,
                billed_points=0,
                resolved_context=resolved_context,
                context_plan=context_plan,
                resolved_context_input=resolved_context_input,
                run_info=run_info,
                supplement_candidates=[],
                persisted_supplements=persisted_supplements_json,
            )
            await repo.update_turn_run(
                turn_run_id=_parse_uuid(turn_run["id"], "turn run id is invalid"),
                status="completed",
                resolved_intent=resolved_intent,
                user_visible_output_json=payload.model_dump(mode="json"),
                completed_at=datetime.now(UTC),
            )
            await _upsert_eval_trace_record(
                turn_run_id=_parse_uuid(turn_run["id"], "turn run id is invalid"),
                planning_snapshot=planning_snapshot,
                runtime_state=runtime_state,
                context_plan=context_plan,
                trace_summary=trace_summary,
            )
            yield _sse("message.completed", payload.model_dump(mode="json"))
            return

        await ensure_credit_account(user_id)
        remaining = await check_quota(user_id)
        if remaining < READER_ASK_RESERVED_POINTS:
            yield _sse(
                "error",
                {
                    "code": "INSUFFICIENT_CREDITS",
                    "detail": "Not enough credits for this Ask Claread request.",
                    "remaining_points": remaining,
                    "required_points": READER_ASK_RESERVED_POINTS,
                },
            )
            return

        reservation_metadata = {
            "capability_code": CAPABILITY_READER_ASK,
            "thread_id": str(thread_id),
            "record_id": str(record.record_id),
            "billing_policy_version": build_reader_ask_billing_metadata(None)["billing_policy_version"],
            "reserved_points": READER_ASK_RESERVED_POINTS,
            "user_message": _truncate_text(body.content, 200),
            "retry_message_id": str(message_id),
        }
        reservation = await reserve_points(
            user_id,
            READER_ASK_RESERVED_POINTS,
            task_id=None,
            entry_type=LEDGER_ENTRY_TYPE_AI_CAPABILITY_DEDUCT,
            metadata=reservation_metadata,
        )
        if reservation is None:
            remaining = await check_quota(user_id)
            yield _sse(
                "error",
                {
                    "code": "INSUFFICIENT_CREDITS",
                    "detail": "Not enough credits for this Ask Claread request.",
                    "remaining_points": remaining,
                    "required_points": READER_ASK_RESERVED_POINTS,
                },
            )
            return

        assistant_message = await repo.update_message(
            message_id=message_id,
            status="streaming",
            content_md="",
            context_anchors=anchor_payload,
            citations=[],
            action_proposals=[],
            tool_trace=[],
            metadata=_message_metadata(
                resolved_intent=resolved_intent,
                resolved_context_input=resolved_context_input,
                run_info=run_info,
                run_history=run_history,
                persisted_supplements=persisted_supplements_json,
            ),
            usage_event_id=None,
            current_turn_run_id=_parse_uuid(turn_run["id"], "turn run id is invalid"),
        )
        yield _sse("thread.ready", {"thread_id": str(thread_id), "record_id": str(record.record_id)})
        yield _sse("message.started", {"message_id": assistant_message["id"], "reply_to": user_message["id"]})

        base_citations = [
            _anchor_to_citation(anchor, record_id=str(record.record_id), record_title=record.title)
            for anchor in resolved_anchors
        ]
        runtime_state = ReaderAskRuntimeState(
            citations=list(base_citations),
            source_labels={"current_record", *({"current_anchor"} if resolved_anchors else set())},
        )
        query_seed = _query_seed(body.content, resolved_anchors)
        if planning_snapshot is None:
            raise RuntimeError("planning snapshot is required")
        history_lookup_allowed = planning_snapshot.retrieval_needs == "known_reference_only"

        agent = get_reader_ask_agent()
        model, model_config = build_model_for_route(get_settings(), MODEL_ROUTE_READER_ASK)
        if model is None:
            raise RuntimeError("model route is not configured: reader_ask")

        route_settings = RunModelSettings(max_tokens=_DEFAULT_MAX_OUTPUT_TOKENS, temperature=0.3, timeout=45.0)
        if model_config and model_config.model_settings is not None:
            route_settings = route_settings.merged_with(model_config.model_settings)
        route_settings = RunModelSettings(
            max_tokens=route_settings.max_tokens or _DEFAULT_MAX_OUTPUT_TOKENS,
            temperature=route_settings.temperature,
            timeout=route_settings.timeout,
        )

        event_queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
        primary_anchor = resolved_anchors[0] if resolved_anchors else None
        dictionary_anchor = next((anchor for anchor in resolved_anchors if anchor.anchor_type == "dictionary_entry"), None)

        async def get_record_context_cb() -> dict[str, Any]:
            return {
                "title": record.title,
                "source_excerpt": _truncate_text(record.source_text, _MAX_CONTEXT_TEXT),
                "sentence_windows": _collect_sentence_windows(record, resolved_anchors),
            }

        async def get_record_insights_cb() -> list[dict[str, Any]]:
            return _collect_sentence_entries(record, resolved_anchors)

        async def get_record_excerpt_assets_cb(query: str) -> list[dict[str, Any]]:
            return await _tool_get_record_excerpt_assets(user_id, record, resolved_anchors, query)

        async def search_user_excerpt_assets_cb(query: str) -> list[dict[str, Any]]:
            return await _tool_search_user_excerpt_assets(user_id, record.record_id, query)

        async def search_user_vocabulary_cb(query: str) -> list[dict[str, Any]]:
            return await _tool_search_user_vocabulary(user_id, query)

        async def lookup_dictionary_entry_cb(
            query: str | None,
            entry_id: int | None,
            query_type: str | None,
            context_sentence: str | None,
            occurrence: int | None,
        ) -> dict[str, Any] | None:
            fallback_query = query
            fallback_entry_id = entry_id
            if dictionary_anchor is not None:
                fallback_query = fallback_query or dictionary_anchor.query
                fallback_entry_id = fallback_entry_id or dictionary_anchor.dict_entry_id
            return await _tool_lookup_dictionary_entry(
                query=fallback_query,
                entry_id=fallback_entry_id,
                query_type=query_type,
                context_sentence=context_sentence,
                occurrence=occurrence,
            )

        async def run_dictionary_ai_context_explain_cb(
            query: str,
            entry_id: int,
            context_sentence: str,
            query_type: str,
            occurrence: int | None,
        ) -> dict[str, Any] | None:
            result, usage = await _tool_run_dictionary_ai_context_explain(
                query=query,
                entry_id=entry_id,
                context_sentence=context_sentence,
                query_type=query_type,
                occurrence=occurrence,
            )
            if usage:
                nested_tool_usages.append({"tool_name": "run_dictionary_ai_context_explain", "usage_summary": usage})
            return result

        resolved_context_input = await _materialize_planned_context(
            user_id=user_id,
            record=record,
            runtime_state=runtime_state,
            planning_snapshot=planning_snapshot,
            page_identity=body.page_identity,
            entry_action=body.entry_action,
            attachments=attachments,
            anchors=resolved_anchors,
            get_record_context_cb=get_record_context_cb,
            get_record_insights_cb=get_record_insights_cb,
        )
        context_plan = _build_context_plan(
            entry_action=body.entry_action,
            attachments=attachments,
            anchors=resolved_anchors,
            runtime_state=runtime_state,
            citations=runtime_state.citations,
            reference_resolution=reference_resolution,
            planning_snapshot=planning_snapshot,
        )
        trace_summary = _build_trace_summary(
            runtime_state=runtime_state,
            context_plan=context_plan,
            planning_snapshot=planning_snapshot,
        )
        await _upsert_eval_trace_record(
            turn_run_id=_parse_uuid(turn_run["id"], "turn run id is invalid"),
            planning_snapshot=planning_snapshot,
            runtime_state=runtime_state,
            context_plan=context_plan,
            trace_summary=trace_summary,
        )
        prompt_payload = runtime_contract_svc.build_prompt_payload(
            thread=thread,
            record=record,
            user_message=body.content,
            history_messages=history_messages,
            page_identity=body.page_identity,
            attachments=attachments,
            anchors=resolved_anchors,
            resolved_intent=resolved_intent,
            entry_action=body.entry_action,
            history_lookup_allowed=history_lookup_allowed,
            resolved_context_input=resolved_context_input,
            reference_resolution=reference_resolution,
            planning_snapshot=planning_snapshot,
            resolved_intent_label=_TASK_MODE_LABELS[resolved_intent],
            max_history_messages=_MAX_HISTORY_MESSAGES,
            max_message_text=_MAX_MESSAGE_TEXT,
        )
        prompt_payload, max_output_tokens = runtime_contract_svc.prepare_prompt_payload(
            prompt_payload,
            reserved_points=READER_ASK_RESERVED_POINTS,
            tokens_per_point=TOKENS_PER_POINT,
            multiplier_output=MULTIPLIER_OUTPUT,
            budget_buffer_tokens=_PROMPT_BUDGET_BUFFER_TOKENS,
            default_max_output_tokens=_DEFAULT_MAX_OUTPUT_TOKENS,
            min_max_output_tokens=_MIN_MAX_OUTPUT_TOKENS,
        )
        route_settings = RunModelSettings(
            max_tokens=min(route_settings.max_tokens or _DEFAULT_MAX_OUTPUT_TOKENS, max_output_tokens),
            temperature=route_settings.temperature,
            timeout=route_settings.timeout,
        )

        deps = ReaderAskAgentDeps(
            payload=prompt_payload,
            event_queue=event_queue,
            state=runtime_state,
            query_seed=query_seed,
            task_mode=resolved_intent,
            record_id=str(record.record_id),
            record_title=record.title,
            primary_anchor=primary_anchor,
            history_lookup_allowed=history_lookup_allowed,
            get_record_context_fn=get_record_context_cb,
            get_record_insights_fn=get_record_insights_cb,
            get_record_excerpt_assets_fn=get_record_excerpt_assets_cb,
            search_user_excerpt_assets_fn=search_user_excerpt_assets_cb,
            search_user_vocabulary_fn=search_user_vocabulary_cb,
            lookup_dictionary_entry_fn=lookup_dictionary_entry_cb,
            run_dictionary_ai_context_explain_fn=run_dictionary_ai_context_explain_cb,
            excerpt_item_to_citation_fn=lambda item, kind: _excerpt_item_to_citation(item, kind=kind),
            vocabulary_item_to_citation_fn=_vocabulary_item_to_citation,
            dictionary_item_to_citation_fn=_dictionary_item_to_citation,
            dictionary_ai_to_citation_fn=_dictionary_ai_to_citation,
        )

        content_parts: list[str] = []
        usage_summary: dict[str, Any] | None = None
        producer_done = asyncio.Event()
        producer_error: Exception | None = None

        async def run_agent_stream() -> None:
            nonlocal usage_summary, producer_error
            try:
                async with agent.run_stream(
                    build_reader_ask_prompt(deps),
                    deps=deps,
                    model=model,
                    model_settings=route_settings.to_pydantic_ai(),
                ) as result:
                    async for delta in result.stream_text(delta=True, debounce_by=None):
                        if not delta:
                            continue
                        content_parts.append(delta)
                        await event_queue.put(
                            (
                                "message.delta",
                                {"message_id": assistant_message["id"], "delta": delta},
                            )
                        )
                    usage_summary = build_usage_metadata(result.usage())
            except Exception as exc:
                producer_error = exc
            finally:
                producer_done.set()

        producer_task = asyncio.create_task(run_agent_stream())
        try:
            while not producer_done.is_set() or not event_queue.empty():
                try:
                    event_name, event_payload = await asyncio.wait_for(event_queue.get(), timeout=0.1)
                except TimeoutError:
                    continue
                yield _sse(event_name, event_payload)
        finally:
            await producer_task

        if producer_error is not None:
            raise producer_error

        final_content_md = "".join(content_parts).strip()
        await _ensure_task_card_data(
            task_mode=resolved_intent,
            runtime_state=runtime_state,
            get_record_context_cb=get_record_context_cb,
            get_record_insights_cb=get_record_insights_cb,
            lookup_dictionary_entry_cb=lookup_dictionary_entry_cb,
            run_dictionary_ai_context_explain_cb=run_dictionary_ai_context_explain_cb,
            record=record,
            anchors=resolved_anchors,
        )
        if resolved_intent == "vocabulary":
            if runtime_state.latest_dictionary_entry is not None:
                runtime_state.source_labels.add("dictionary")
                _merge_citation(
                    runtime_state.citations,
                    _dictionary_item_to_citation(runtime_state.latest_dictionary_entry),
                )
            if runtime_state.latest_dictionary_ai is not None and runtime_state.latest_dictionary_entry is not None:
                query = str(
                    runtime_state.latest_dictionary_entry.get("query")
                    or runtime_state.latest_dictionary_entry.get("word")
                    or ""
                )
                entry_id = runtime_state.latest_dictionary_entry.get("id")
                if query and isinstance(entry_id, int):
                    _merge_citation(
                        runtime_state.citations,
                        _dictionary_ai_to_citation(runtime_state.latest_dictionary_ai, query, entry_id),
                    )
        runtime_proposals = _build_action_proposals_from_runtime(
            record=record,
            action_requests=runtime_state.action_requests,
            assistant_content_md=final_content_md,
        )
        fallback_proposals = _build_action_proposals(
            user_message=body.content,
            record=record,
            anchors=resolved_anchors,
            assistant_content_md=final_content_md,
        )
        action_proposals = _merge_action_proposals(runtime_proposals, fallback_proposals)
        usage_summary = _merge_usage_summaries(usage_summary, nested_tool_usages)
        response_cards = _build_response_cards(
            task_mode=resolved_intent,
            record=record,
            anchors=resolved_anchors,
            runtime_state=runtime_state,
        )
        resolved_context = _resolved_context_summary(
            record=record,
            anchors=resolved_anchors,
            explicit_attachment_count=len(attachments),
            runtime_state=runtime_state,
            used_history_lookup=runtime_state.used_history_lookup,
            citations=runtime_state.citations,
        )
        typed_supplement_candidates = capabilities_svc.build_supplement_candidates(
            resolved_intent=resolved_intent,
            anchors=resolved_anchors,
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
            reference_resolution=reference_resolution,
            supplement_candidates=typed_supplement_candidates,
        )
        trace_summary = trace_summary.model_copy(
            update={
                "supplement_generation_used": bool(typed_supplement_candidates),
                "supplement_persisted_count": 0,
                "supplement_deleted_count": 0,
            }
        )

        computed_cost_points = compute_reader_ask_cost_points(usage_summary)
        billed_points = min(computed_cost_points, reservation.total_points)
        unused_reservation = _build_unused_reservation(reservation, billed_points)
        if unused_reservation.total_points > 0:
            await refund_reserved_points(
                user_id,
                unused_reservation,
                metadata={
                    "reason": "reader_ask_unused_reservation",
                    "thread_id": str(thread_id),
                    "record_id": str(record.record_id),
                    "retry_message_id": str(message_id),
                },
            )
            reservation = billed_points and CreditReservation(
                total_points=billed_points,
                deducted_from_daily=min(billed_points, reservation.deducted_from_daily),
                deducted_from_bonus=max(billed_points - min(billed_points, reservation.deducted_from_daily), 0),
            ) or CreditReservation(total_points=0, deducted_from_daily=0, deducted_from_bonus=0)
        else:
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
                billing_policy_version=build_reader_ask_billing_metadata(usage_summary).get("billing_policy_version"),
                metadata_json={
                    "entrypoint": "/reader-ask/threads/{thread_id}/messages/{message_id}/retry/stream",
                    "thread_id": str(thread_id),
                    "message_id": str(message_id),
                    "history_lookup_used": runtime_state.used_history_lookup,
                    "anchor_count": len(resolved_anchors),
                    "tool_names": [entry.tool_name for entry in runtime_state.tool_trace if entry.status == "completed"],
                    "reservation_points": READER_ASK_RESERVED_POINTS,
                    "computed_cost_points": computed_cost_points,
                    "clamped_to_reservation": computed_cost_points > READER_ASK_RESERVED_POINTS,
                },
                **build_model_metadata(model_config),
            )
        )

        await repo.update_message(
            message_id=message_id,
            status="completed",
            content_md=final_content_md,
            context_anchors=anchor_payload,
            citations=[citation.model_dump(mode="json") for citation in runtime_state.citations],
            action_proposals=[proposal.model_dump(mode="json") for proposal in action_proposals],
            tool_trace=[entry.model_dump(mode="json") for entry in runtime_state.tool_trace],
            metadata=_message_metadata(
                resolved_intent=resolved_intent,
                resolved_context=resolved_context,
                context_plan=context_plan,
                resolved_context_input=resolved_context_input,
                evidence=evidence,
                trace_summary=trace_summary,
                response_cards=response_cards,
                run_info=run_info,
                supplement_candidates=supplement_candidates,
                persisted_supplements=persisted_supplements_json,
                run_history=run_history,
            ),
            usage_event_id=usage_event_id,
            current_turn_run_id=_parse_uuid(turn_run["id"], "turn run id is invalid"),
        )
        payload = ReaderAskCompletedPayload(
            id=str(message_id),
            thread_id=str(thread_id),
            content_md=final_content_md,
            resolved_intent=resolved_intent,
            citations=runtime_state.citations,
            action_proposals=action_proposals,
            tool_trace=runtime_state.tool_trace,
            evidence=evidence,
            trace_summary=trace_summary,
            response_cards=response_cards,
            usage_summary=usage_summary,
            billed_points=billed_points,
            resolved_context=resolved_context,
            context_plan=context_plan,
            resolved_context_input=resolved_context_input,
            run_info=run_info,
            supplement_candidates=supplement_candidates,
            persisted_supplements=persisted_supplements_json,
        )
        await repo.update_turn_run(
            turn_run_id=_parse_uuid(turn_run["id"], "turn run id is invalid"),
            status="completed",
            resolved_intent=resolved_intent,
            user_visible_output_json=payload.model_dump(mode="json"),
            usage_summary_json=usage_summary,
            usage_event_id=usage_event_id,
            completed_at=datetime.now(UTC),
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
        yield _sse("message.completed", payload.model_dump(mode="json"))
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
                content_md="",
                context_anchors=anchor_payload,
                citations=[citation.model_dump(mode="json") for citation in runtime_state.citations],
                action_proposals=[],
                tool_trace=[entry.model_dump(mode="json") for entry in runtime_state.tool_trace],
                metadata=_message_metadata(
                    resolved_intent=resolved_intent,
                    resolved_context_input=resolved_context_input,
                    evidence=evidence,
                    trace_summary=trace_summary,
                    run_info=run_info,
                    run_history=run_history,
                    persisted_supplements=persisted_supplements_json,
                ),
                usage_event_id=None,
                current_turn_run_id=active_turn_run_id,
            )
            if active_turn_run_id is not None:
                await repo.update_turn_run(
                    turn_run_id=active_turn_run_id,
                    status="failed",
                    resolved_intent=resolved_intent,
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
                },
            )
        if isinstance(exc, HTTPException):
            yield _sse("error", {"code": str(exc.status_code), "detail": exc.detail})
            return
        if "model route is not configured" in str(exc):
            yield _sse("error", {"code": "MODEL_UNAVAILABLE", "detail": "Ask Claread is temporarily unavailable."})
            return
        detail = str(exc) if get_settings().app_env != "production" else "Ask Claread is temporarily unavailable."
        yield _sse("error", {"code": "READER_ASK_FAILED", "detail": detail})


def _favorite_payload_from_anchor(record_id: UUID, anchor: ReaderAskAnchorRef) -> tuple[str, str, dict[str, Any]]:
    if anchor.anchor_type == "sentence":
        sentence_id = anchor.sentence_id
        if not sentence_id:
            raise HTTPException(status_code=400, detail="sentence anchor is missing sentence_id")
        target_key = anchor.target_key or f"record:{record_id}:sentence:{sentence_id}"
        payload = {
            "sentence_id": sentence_id,
            "paragraph_id": anchor.paragraph_id,
            "selected_text": anchor.selected_text,
        }
        return "sentence", target_key, payload
    if anchor.anchor_type == "text_range":
        if not anchor.sentence_id or anchor.start_offset is None or anchor.end_offset is None or not anchor.text_hash:
            raise HTTPException(status_code=400, detail="text_range anchor is incomplete")
        target_key = anchor.target_key or (
            f"record:{record_id}:range:{anchor.sentence_id}:{anchor.start_offset}:{anchor.end_offset}:{anchor.text_hash}"
        )
        payload = {
            "sentence_id": anchor.sentence_id,
            "paragraph_id": anchor.paragraph_id,
            "selected_text": anchor.selected_text,
            "start_offset": anchor.start_offset,
            "end_offset": anchor.end_offset,
            "text_hash": anchor.text_hash,
        }
        return "text_range", target_key, payload
    if anchor.anchor_type == "multi_text":
        segments = [segment.model_dump(mode="json") for segment in anchor.segments]
        target_key = anchor.target_key or build_multi_text_target_key(str(record_id), segments)
        return "multi_text", target_key, {"segments": segments, "selected_text": anchor.selected_text}
    raise HTTPException(status_code=400, detail="favorite action only supports sentence/text anchors in V1")


def _annotation_request_from_anchor(
    *,
    record_id: UUID,
    anchor: ReaderAskAnchorRef,
    annotation_type: str,
    note: str | None,
) -> UserAnnotationCreateRequest:
    if anchor.anchor_type == "sentence":
        if not anchor.sentence_id or not anchor.selected_text:
            raise HTTPException(status_code=400, detail="sentence anchor is incomplete")
        return UserAnnotationCreateRequest(
            analysis_record_id=str(record_id),
            annotation_type=annotation_type,
            anchor_type="sentence",
            sentence_id=anchor.sentence_id,
            paragraph_id=anchor.paragraph_id,
            selected_text=anchor.selected_text,
            note=note,
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
            annotation_type=annotation_type,
            anchor_type="text_range",
            sentence_id=anchor.sentence_id,
            paragraph_id=anchor.paragraph_id,
            selected_text=anchor.selected_text,
            start_offset=anchor.start_offset,
            end_offset=anchor.end_offset,
            text_hash=anchor.text_hash,
            note=note,
            payload_json=anchor.payload_json,
        )
    if anchor.anchor_type == "multi_text":
        if len(anchor.segments) < 2:
            raise HTTPException(status_code=400, detail="multi_text anchor is incomplete")
        return UserAnnotationCreateRequest(
            analysis_record_id=str(record_id),
            annotation_type=annotation_type,
            anchor_type="multi_text",
            sentence_id=anchor.segments[0].sentence_id,
            selected_text=anchor.selected_text or " ... ".join(segment.selected_text for segment in anchor.segments),
            segments=[UserAnnotationSegment.model_validate(segment.model_dump(mode="json")) for segment in anchor.segments],
            note=note,
            payload_json=anchor.payload_json,
        )
    raise HTTPException(status_code=400, detail="annotation action only supports sentence/text anchors in V1")


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
            metadata=_message_metadata(
                resolved_intent=message.resolved_intent,
                resolved_context=message.resolved_context,
                context_plan=message.context_plan,
                resolved_context_input=message.resolved_context_input,
                evidence=message.evidence,
                trace_summary=message.trace_summary,
                response_cards=message.response_cards,
                run_info=message.run_info.model_dump(mode="json") if message.run_info else None,
                supplement_candidates=[item.model_dump(mode="json") for item in message.supplement_candidates],
                persisted_supplements=persisted_supplements,
                run_history=run_history,
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

        if proposal.action_type == "favorite_anchor":
            target_type, target_key, payload_json = _favorite_payload_from_anchor(record_id, anchor)
            favorite_id = await favorites_svc.add_favorite(
                user_id=user_id,
                target_type=target_type,
                target_key=target_key,
                analysis_record_id=record_id,
                payload_json=payload_json,
                note=None,
            )
            result = ReaderAskActionConfirmResult(favorite_id=str(favorite_id), target_key=target_key)
        elif proposal.action_type == "save_excerpt":
            annotation = await user_annotations_svc.create_user_annotation(
                user_id,
                _annotation_request_from_anchor(
                    record_id=record_id,
                    anchor=anchor,
                    annotation_type="highlight",
                    note=None,
                ),
            )
            result = ReaderAskActionConfirmResult(
                annotation_id=str(annotation.id),
                annotation_type=annotation.annotation_type,
            )
        elif proposal.action_type in {"save_note", "save_answer_note"}:
            note_text = proposal.payload_json.get("note_text")
            if not isinstance(note_text, str) or not note_text.strip():
                raise HTTPException(status_code=400, detail="Action proposal is missing note_text")
            annotation = await user_annotations_svc.create_user_annotation(
                user_id,
                _annotation_request_from_anchor(
                    record_id=record_id,
                    anchor=anchor,
                    annotation_type="note",
                    note=note_text,
                ),
            )
            result = ReaderAskActionConfirmResult(
                annotation_id=str(annotation.id),
                annotation_type=annotation.annotation_type,
            )
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported action type: {proposal.action_type}")

    updated_proposals = [
        proposal_item.model_copy(update={"status": "executed"}) if proposal_item.id == action_id else proposal_item
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
        metadata=_message_metadata(
            resolved_intent=message.resolved_intent,
            resolved_context=message.resolved_context,
            context_plan=message.context_plan,
            resolved_context_input=message.resolved_context_input,
            evidence=updated_evidence,
            trace_summary=updated_trace_summary,
            response_cards=message.response_cards,
            run_info=message.run_info.model_dump(mode="json") if message.run_info else None,
            supplement_candidates=[item.model_dump(mode="json") for item in message.supplement_candidates],
            persisted_supplements=persisted_supplements,
            run_history=run_history,
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
        output = dict(source_turn_run.get("user_visible_output_json") or {})
        persisted_items = list(output.get("persisted_supplements") or [])
        next_items: list[dict[str, Any]] = []
        replaced = False
        for item in persisted_items:
            if item.get("supplement_id") == persisted_supplement.supplement_id:
                next_items.append(persisted_supplement.model_dump(mode="json"))
                replaced = True
            else:
                next_items.append(item)
        if not replaced:
            next_items.append(persisted_supplement.model_dump(mode="json"))
        output["persisted_supplements"] = next_items
        await repo.update_turn_run(
            turn_run_id=source_turn_run_id,
            status=source_turn_run["status"],
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
