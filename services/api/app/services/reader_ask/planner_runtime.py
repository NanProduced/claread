"""Planner Runtime: semantic decision logic for Ask Claread.

This module handles:
- Planner LLM agent invocation with retry and fallback
- Deterministic fallback decision when planner is unavailable
- History trimming for planner context
- Quick action mapping (entry_action → kind/label/content)
- Reference query extraction from user messages

The actual planning snapshot construction (working set derivation,
clarification rules, deictic detection) lives in planner.py.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal, Protocol
from uuid import UUID

from app.agents.reader_ask_planner_agent import (
    ReaderAskPlannerAgentDeps,
    build_reader_ask_planner_prompt,
    get_reader_ask_planner_agent,
)
from app.schemas.reader_ask import (
    ReaderAskAnchorRef,
    ReaderAskAttachment,
    ReaderAskContextScope,
    ReaderAskCurrentRecordAffordances,
    ReaderAskEntryAction,
    ReaderAskPageIdentity,
    ReaderAskPlannerDecision,
    ReaderAskResolvedIntent,
    ReaderAskSubmissionMode,
    ReaderAskTaskMode,
)
from app.llm.agent_runner import extract_run_usage
from app.llm.types import RunModelSettings
from app.services.reader_ask import config as cfg
from app.services.reader_ask import context_runtime as context_runtime_svc
from app.services.reader_ask import planner
from app.services.reader_ask import utils

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Record bundle protocol (mirrors service._RecordBundle)
# ---------------------------------------------------------------------------

class _RecordBundle(Protocol):
    record_id: UUID
    title: str | None
    render_scene: dict[str, Any]


# ---------------------------------------------------------------------------
# Result data class
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class SemanticPlanningResult:
    planner_decision: ReaderAskPlannerDecision
    planner_validation_status: str
    planner_usage_summary: dict[str, Any] | None
    reference_resolution: planner.ReaderAskReferenceResolution
    planning_snapshot: planner.ReaderAskPlanningSnapshot


# ---------------------------------------------------------------------------
# Dependency injection for async functions
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class RunPlannerDeps:
    """Callbacks injected by service.py for run_semantic_planner."""
    current_record_affordances_cb: Callable[..., ReaderAskCurrentRecordAffordances]
    build_model_route_cb: Callable[[], tuple[Any, Any]]


@dataclass(slots=True)
class ResolvePlanningDeps:
    """Callbacks injected by service.py for resolve_semantic_planning."""
    run_planner_deps: RunPlannerDeps
    resolve_known_references_cb: Callable[..., Awaitable[planner.ReaderAskReferenceResolution]]
    load_record_bundle_cb: Callable[[UUID, UUID], Awaitable[_RecordBundle]]
    resolve_structured_asset_refs_cb: Callable[..., Awaitable[planner.ReaderAskStructuredAssetResolution]]
    list_supplements_cb: Callable[..., Awaitable[list[Any]]]
    reference_reranker: Any | None = None


# ---------------------------------------------------------------------------
# Pure functions: quick action mapping
# ---------------------------------------------------------------------------

def annotation_quick_action_kind(
    task_mode: ReaderAskTaskMode,
    entry_action: ReaderAskEntryAction,
) -> Literal["grammar_note", "sentence_analysis"] | None:
    if task_mode == "grammar" or entry_action == "why_here":
        return "grammar_note"
    if task_mode == "breakdown" or entry_action == "explain_this":
        return "sentence_analysis"
    return None


def submission_mode(
    *,
    entry_action: ReaderAskEntryAction,
    attachments: list[ReaderAskAttachment],
) -> ReaderAskSubmissionMode:
    if entry_action not in {"why_here", "explain_this"}:
        return "chat"
    if any(attachment.metadata.source_surface == "selection_toolbar" for attachment in attachments):
        return "quick_action"
    return "chat"


def quick_action_not_applicable(
    *,
    kind: Literal["grammar_note", "sentence_analysis"],
    sentence_id: str,
    sentence_text: str,
    focus_text: str,
    reason: str,
    suggestion: str,
) -> dict[str, Any]:
    return {
        "status": "not_applicable",
        "kind": kind,
        "sentence_id": sentence_id,
        "source_sentence": sentence_text,
        "focus_text": focus_text,
        "reason": reason,
        "suggestion": suggestion,
    }


def quick_action_label(entry_action: ReaderAskEntryAction) -> str:
    if entry_action == "why_here":
        return "语法解析"
    if entry_action == "explain_this":
        return "句子拆分"
    return "快捷分析"


def quick_action_content(
    *,
    entry_action: ReaderAskEntryAction,
    generated_annotation: dict[str, Any] | None,
) -> str:
    if generated_annotation is None:
        return f"{quick_action_label(entry_action)}暂时无法完成。"
    if generated_annotation.get("status") == "not_applicable":
        reason = str(generated_annotation.get("reason") or "").strip()
        suggestion = str(generated_annotation.get("suggestion") or "").strip()
        pieces = [f"这次没有直接生成{quick_action_label(entry_action)}卡。"]
        if reason:
            pieces.append(reason)
        if suggestion:
            pieces.append(suggestion)
        return "\n\n".join(pieces)

    label = str(generated_annotation.get("label") or "").strip()
    focus_text = str(generated_annotation.get("focus_text") or "").strip()
    if generated_annotation.get("kind") == "grammar_note":
        note = str(generated_annotation.get("note_zh") or generated_annotation.get("content") or "").strip()
        scope_hint = (
            "我先基于整句理解，再聚焦你选中的片段。"
            if generated_annotation.get("analysis_scope") == "focus_span"
            else "我直接围绕这句话的关键结构来解释。"
        )
        pieces = [scope_hint]
        if label:
            pieces.append(f"关键语法点：**{label}**")
        if focus_text and generated_annotation.get("analysis_scope") == "focus_span":
            pieces.append(f"聚焦片段：`{focus_text}`")
        if note:
            pieces.append(note)
        return "\n\n".join(pieces)

    analysis = str(generated_annotation.get("analysis_zh") or generated_annotation.get("content") or "").strip()
    pieces = ["我先给你一个结构拆解卡，再补一句阅读顺序说明。"]
    if label:
        pieces.append(f"句型概述：**{label}**")
    if analysis:
        pieces.append(analysis)
    return "\n\n".join(pieces)


# ---------------------------------------------------------------------------
# Pure functions: planner history, keyword matching, reference query
# ---------------------------------------------------------------------------

def planner_history_messages(
    history_messages: list[dict[str, Any]],
    *,
    max_messages: int = cfg.PLANNER_MAX_HISTORY_MESSAGES,
    truncate_history_message_cb: Callable[..., dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    """Trim and format conversation history for the planner agent.

    If truncate_history_message_cb is not provided, a simple truncation
    is applied.  Service.py passes its own _truncate_history_message.
    """
    from app.services.reader_ask.runtime_contract import (
        build_structured_history_summary,
        format_structured_history_summary,
    )

    structured_summary = build_structured_history_summary(
        history_messages, recent_window=max_messages
    )

    normalized: list[dict[str, object]] = []
    if structured_summary:
        normalized.append(
            {
                "role": "system",
                "content_md": format_structured_history_summary(structured_summary),
                "resolved_intent": None,
            }
        )

    for item in history_messages[-max_messages:]:
        role = item.get("role")
        if role not in {"user", "assistant"}:
            continue
        content_md = str(item.get("content_md") or "")
        if truncate_history_message_cb is not None:
            content_md = truncate_history_message_cb(content_md, role=str(role), limit=cfg.MAX_MESSAGE_TEXT)
        else:
            content_md = content_md[:cfg.MAX_MESSAGE_TEXT]
        normalized.append(
            {
                "role": role,
                "content_md": content_md,
                "resolved_intent": item.get("resolved_intent"),
            }
        )
    return normalized


def fallback_reference_query(user_message: str) -> str | None:
    """Extract a reference query from explicit title markers only.

    P3-S3: Weak reference regex patterns removed. Natural language
    references like "之前那篇/that article about" are now handled
    by the LLM planner's reference_request. This function only
    extracts from explicit structural markers: book title marks (《》),
    curly quotes (""), and straight quotes.
    """
    normalized = utils.normalize_text(user_message)
    if not normalized:
        return None
    for pattern in (r"《([^》]+)》", r"\u201c([^\u201d]+)\u201d", r"\"([^\"]+)\"", r"'([^']+)'"):
        match = re.search(pattern, normalized)
        if not match:
            continue
        query = utils.clean_reference_query(match.group(1))
        if query:
            return query
    return None


# ---------------------------------------------------------------------------
# Fallback semantic planner decision (deterministic)
# ---------------------------------------------------------------------------

def fallback_semantic_planner_decision(
    *,
    user_message: str,
    entry_action: ReaderAskEntryAction,
    page_identity: ReaderAskPageIdentity,
    attachments: list[ReaderAskAttachment],
    anchors: list[ReaderAskAnchorRef],
    record: _RecordBundle,
    failure_reason: str | None,
    render_overview_cb: Callable[[_RecordBundle], str | None],
    has_sentence_entries_cb: Callable[[_RecordBundle], bool],
) -> ReaderAskPlannerDecision:
    """Produce a deterministic fallback planner decision when the LLM fails."""
    has_external_record_attachment = any(
        attachment.kind == "record_ref" and attachment.subtype == "related_record"
        for attachment in attachments
    )
    has_external_asset_attachment = any(
        attachment.kind in {"analysis_ref", "supplement_ref"}
        and (
            (attachment.metadata.record_id and attachment.metadata.record_id != page_identity.record_id)
            or (attachment.target_key and f"record:{page_identity.record_id}:" not in attachment.target_key)
        )
        for attachment in attachments
    )
    has_dictionary_anchor = any(anchor.anchor_type == "dictionary_entry" for anchor in anchors)
    has_local_anchor = bool(anchors)
    ref_query = fallback_reference_query(user_message)
    has_article_overview = render_overview_cb(record) is not None
    has_sentence_entries = has_sentence_entries_cb(record)

    resolved_intent: ReaderAskResolvedIntent = "explain"
    if entry_action == "lookup_in_context" or has_dictionary_anchor:
        resolved_intent = "vocabulary"
    elif entry_action == "why_here":
        resolved_intent = "grammar"

    has_title_reference = ref_query is not None
    clarification_only = False
    clarification_reason: str | None = None
    cross_record_context_allowed = (
        has_external_record_attachment or has_external_asset_attachment or has_title_reference
    )
    article_overview_needed = False
    local_context_window_needed = False
    record_insights_needed = False
    dictionary_needed = resolved_intent == "vocabulary" or entry_action == "lookup_in_context" or has_dictionary_anchor
    structured_asset_requested = has_external_asset_attachment
    structured_asset_type = (
        "supplement"
        if any(attachment.kind == "supplement_ref" for attachment in attachments)
        else "analysis"
        if structured_asset_requested
        else None
    )

    if has_local_anchor:
        local_context_window_needed = True
        if resolved_intent in {"grammar", "breakdown", "practice"} and has_sentence_entries:
            record_insights_needed = True
        context_scope: ReaderAskContextScope = "sentence"
    elif cross_record_context_allowed:
        if has_article_overview:
            article_overview_needed = True
        local_context_window_needed = True
        context_scope = "cross_article"
    elif has_article_overview:
        article_overview_needed = True
        local_context_window_needed = True
        context_scope = "article"
    else:
        local_context_window_needed = True
        context_scope = "article"

    if has_title_reference and not has_local_anchor:
        clarification_reason = "fallback_title_reference_without_anchor"

    return ReaderAskPlannerDecision(
        resolved_intent=resolved_intent,
        clarification_only=clarification_only,
        clarification_reason=clarification_reason,
        reference_request={
            "requested": bool(ref_query),
            "query": ref_query,
            "reason": (
                "fallback_title_reference_without_anchor" if has_title_reference and clarification_reason
                else "fallback_title_like_reference" if ref_query
                else None
            ),
        },
        structured_asset_request={
            "requested": structured_asset_requested,
            "requested_asset_type": structured_asset_type,
            "reason": "fallback_from_explicit_external_asset" if structured_asset_requested else None,
        },
        working_set={
            "local_context_window_needed": local_context_window_needed,
            "record_insights_needed": record_insights_needed,
            "article_overview_needed": article_overview_needed,
            "dictionary_needed": dictionary_needed,
            "cross_record_context_allowed": cross_record_context_allowed,
            "external_asset_lookup_needed": structured_asset_requested and has_external_record_attachment,
        },
        rationale=(
            "planner validation failed; used deterministic fallback"
            + (f": {failure_reason}" if failure_reason else "")
        ),
        context_scope=context_scope,
        decision_confidence="low",
    )


# ---------------------------------------------------------------------------
# Async: run planner with retry
# ---------------------------------------------------------------------------

async def run_semantic_planner(
    *,
    user_message: str,
    page_identity: ReaderAskPageIdentity,
    entry_action: ReaderAskEntryAction,
    attachments: list[ReaderAskAttachment],
    anchors: list[ReaderAskAnchorRef],
    history_messages: list[dict[str, Any]],
    record: _RecordBundle,
    deps: RunPlannerDeps,
    truncate_history_message_cb: Callable[..., dict[str, object]] | None = None,
) -> tuple[ReaderAskPlannerDecision, str, dict[str, Any] | None]:
    """Invoke the planner LLM agent with retry; fall back to deterministic on failure."""
    planner_input = planner.build_planner_input(
        user_message=user_message,
        entry_action=entry_action,
        page_identity=page_identity,
        current_record_affordances=deps.current_record_affordances_cb(record=record, page_identity=page_identity),
        attachments=attachments,
        anchors=anchors,
        history_messages=planner_history_messages(
            history_messages,
            truncate_history_message_cb=truncate_history_message_cb,
        ),
    )
    agent = get_reader_ask_planner_agent()
    model, model_config = deps.build_model_route_cb()
    if model is None:
        raise RuntimeError("model route is not configured: reader_ask_planner")

    route_settings = RunModelSettings(
        max_tokens=cfg.DEFAULT_PLANNER_MAX_OUTPUT_TOKENS,
        temperature=cfg.PLANNER_TEMPERATURE,
        timeout=cfg.PLANNER_TIMEOUT_S,
    )
    if model_config and model_config.model_settings is not None:
        route_settings = route_settings.merged_with(model_config.model_settings)
    route_settings = route_settings.with_max_tokens(
        route_settings.max_tokens or cfg.DEFAULT_PLANNER_MAX_OUTPUT_TOKENS
    )

    last_error: Exception | None = None
    for attempt in range(cfg.PLANNER_MAX_RETRIES):
        try:
            result = await agent.run(
                build_reader_ask_planner_prompt(
                    ReaderAskPlannerAgentDeps(planner_input=planner_input)
                ),
                deps=ReaderAskPlannerAgentDeps(planner_input=planner_input),
                model=model,
                model_settings=route_settings.to_pydantic_ai(),
            )
            validation_status = "valid" if attempt == 0 else "retry_succeeded"
            return result.output, validation_status, extract_run_usage(result)
        except Exception as exc:
            last_error = exc
    logger.warning(
        "reader_ask planner agent failed after retries, using deterministic fallback: %s",
        last_error,
        extra={"failure_reason": str(last_error) if last_error else None},
    )
    return (
        fallback_semantic_planner_decision(
            user_message=user_message,
            entry_action=entry_action,
            page_identity=page_identity,
            attachments=attachments,
            anchors=anchors,
            record=record,
            failure_reason=str(last_error) if last_error else None,
            render_overview_cb=context_runtime_svc.render_scene_article_overview,
            has_sentence_entries_cb=_render_scene_has_sentence_entries,
        ),
        "fallback_deterministic",
        None,
    )


# ---------------------------------------------------------------------------
# Async: resolve full semantic planning
# ---------------------------------------------------------------------------

async def resolve_semantic_planning(
    *,
    user_id: UUID,
    record: _RecordBundle,
    history_messages: list[dict[str, Any]],
    user_message: str,
    page_identity: ReaderAskPageIdentity,
    entry_action: ReaderAskEntryAction,
    attachments: list[ReaderAskAttachment],
    anchors: list[ReaderAskAnchorRef],
    deps: ResolvePlanningDeps,
    truncate_history_message_cb: Callable[..., dict[str, object]] | None = None,
) -> SemanticPlanningResult:
    """Orchestrate the full semantic planning pipeline."""
    planner_decision, planner_validation_status, planner_usage_summary = await run_semantic_planner(
        user_message=user_message,
        page_identity=page_identity,
        entry_action=entry_action,
        attachments=attachments,
        anchors=anchors,
        history_messages=history_messages,
        record=record,
        deps=deps.run_planner_deps,
        truncate_history_message_cb=truncate_history_message_cb,
    )

    reference_resolution = await deps.resolve_known_references_cb(
        user_id=user_id,
        current_record_id=record.record_id,
        reference_needs=planner.reference_needs_from_decision(planner_decision),
        reranker=deps.reference_reranker,
    )
    pre_planning_snapshot = planner.plan_request(
        content=user_message,
        page_identity=page_identity,
        entry_action=entry_action,
        attachments=attachments,
        anchors=anchors,
        planner_decision=planner_decision,
        planner_validation_status=planner_validation_status,
        reference_resolution=reference_resolution,
        skip_expensive_fields=True,
    )

    async def _bundle_loader(lookup_user_id: UUID, lookup_record_id: UUID) -> dict[str, Any]:
        bundle = await deps.load_record_bundle_cb(lookup_user_id, lookup_record_id)
        return {
            "title": bundle.title,
            "render_scene": bundle.render_scene,
        }

    structured_asset_resolution = await deps.resolve_structured_asset_refs_cb(
        user_id=user_id,
        current_record_id=record.record_id,
        external_record_refs=pre_planning_snapshot.working_set.external_record_refs,
        structured_asset_needs=pre_planning_snapshot.structured_asset_needs,
        explicit_asset_refs=pre_planning_snapshot.working_set.external_asset_refs,
        bundle_loader=_bundle_loader,
        supplement_loader=deps.list_supplements_cb,
    )
    planning_snapshot = planner.plan_request(
        content=user_message,
        page_identity=page_identity,
        entry_action=entry_action,
        attachments=attachments,
        anchors=anchors,
        planner_decision=planner_decision,
        planner_validation_status=planner_validation_status,
        reference_resolution=reference_resolution,
        structured_asset_resolution=structured_asset_resolution,
    )
    return SemanticPlanningResult(
        planner_decision=planner_decision,
        planner_validation_status=planner_validation_status,
        planner_usage_summary=planner_usage_summary,
        reference_resolution=reference_resolution,
        planning_snapshot=planning_snapshot,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _render_scene_has_sentence_entries(record: _RecordBundle) -> bool:
    entries = record.render_scene.get("sentence_entries") or record.render_scene.get("sentenceEntries")
    return isinstance(entries, list) and bool(entries)
