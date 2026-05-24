from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.agents.reader_ask_agent import ReaderAskRuntimeState
from app.schemas.reader_ask import (
    ReaderAskAnchorRef,
    ReaderAskAssetDisambiguation,
    ReaderAskAssetDisambiguationCandidate,
    ReaderAskAttachment,
    ReaderAskCitation,
    ReaderAskClarificationMode,
    ReaderAskContextPlan,
    ReaderAskCurrentRecordContext,
    ReaderAskCurrentRecordAffordances,
    ReaderAskDisambiguationCandidate,
    ReaderAskDisambiguation,
    ReaderAskEntryAction,
    ReaderAskExternalAssetContext,
    ReaderAskExternalRecordContext,
    ReaderAskPageIdentity,
    ReaderAskPlannerDecision,
    ReaderAskPlannerHistoryMessage,
    ReaderAskPlannerInput,
    ReaderAskPlannerReferenceRequest,
    ReaderAskPlannerStructuredAssetRequest,
    ReaderAskPlannerWorkingSetDecision,
    ReaderAskReferenceResolutionStatus,
    ReaderAskResolvedContextInput,
    ReaderAskResolvedContextSummary,
    ReaderAskResolvedIntent,
    ReaderAskTraceSummary,
    ReaderAskWorkingSetMode,
)
from app.services.reader_ask import utils

ReaderAskRetrievalNeeds = Literal["none", "known_reference_only"]


@dataclass(slots=True)
class ReaderAskReferenceNeeds:
    requested: bool = False
    query: str | None = None
    reason: str | None = None


@dataclass(slots=True)
class ReaderAskReferenceResolution:
    attempted: bool = False
    status: ReaderAskReferenceResolutionStatus = "not_needed"
    query: str | None = None
    reason: str | None = None
    resolved_records: list[dict[str, str]] = field(default_factory=list)
    ambiguous_records: list[dict[str, str]] = field(default_factory=list)


@dataclass(slots=True)
class ReaderAskStructuredAssetNeeds:
    requested: bool = False
    requested_asset_type: Literal["analysis", "supplement"] | None = None
    reason: str | None = None


@dataclass(slots=True)
class ReaderAskStructuredAssetResolution:
    attempted: bool = False
    status: Literal["not_needed", "resolved", "ambiguous", "not_found"] = "not_needed"
    requested_asset_type: Literal["analysis", "supplement"] | None = None
    reason: str | None = None
    record_id: str | None = None
    record_title: str | None = None
    resolved_assets: list[dict[str, object]] = field(default_factory=list)
    ambiguous_assets: list[dict[str, object]] = field(default_factory=list)


@dataclass(slots=True)
class ReaderAskWorkingSet:
    primary_anchor: ReaderAskAnchorRef | None = None
    local_context_window_needed: bool = False
    record_insights_needed: bool = False
    article_overview_needed: bool = False
    dictionary_needed: bool = False
    cross_record_context_allowed: bool = False
    external_record_refs: list[dict[str, str]] = field(default_factory=list)
    external_asset_refs: list[dict[str, str]] = field(default_factory=list)
    external_asset_lookup_needed: bool = False


@dataclass(slots=True)
class ReaderAskPlanningSnapshot:
    resolved_intent: ReaderAskResolvedIntent
    planner_decision: ReaderAskPlannerDecision
    planner_validation_status: str
    resolved_context_input: ReaderAskResolvedContextInput
    reference_needs: ReaderAskReferenceNeeds
    retrieval_needs: ReaderAskRetrievalNeeds
    resolved_references: ReaderAskReferenceResolution
    structured_asset_needs: ReaderAskStructuredAssetNeeds
    structured_asset_resolution: ReaderAskStructuredAssetResolution
    working_set: ReaderAskWorkingSet
    context_plan: ReaderAskContextPlan | None = None
    trace_summary: ReaderAskTraceSummary | None = None
    disambiguation_state: ReaderAskDisambiguation | None = None
    external_asset_disambiguation_state: ReaderAskAssetDisambiguation | None = None
    clarification_only: bool = False
    clarification_mode: ReaderAskClarificationMode = "none"


def _normalize_text(value: str | None) -> str:
    return utils.normalize_text(value)


def _clean_reference_query(value: str | None) -> str | None:
    return utils.clean_reference_query(value)


def _attachment_target_record(attachment: ReaderAskAttachment) -> str | None:
    """用于 related_record 附件：record_id 可能来自 metadata.record_id 或 metadata.asset_id（asset_id 也可作为文章标识的备选来源）。"""
    record_id = attachment.metadata.record_id
    if isinstance(record_id, str) and record_id.strip():
        return record_id
    asset_id = attachment.metadata.asset_id
    if isinstance(asset_id, str) and asset_id.strip():
        return asset_id
    target_key = attachment.target_key
    if not target_key and attachment.anchor_payload is not None:
        target_key = attachment.anchor_payload.target_key
    if isinstance(target_key, str) and target_key.startswith("record:"):
        parts = target_key.split(":")
        if len(parts) >= 2 and parts[1]:
            return parts[1]
    return None


def _attachment_record_id(attachment: ReaderAskAttachment) -> str | None:
    """用于 analysis_ref / supplement_ref 附件：仅从 metadata.record_id 获取文章 ID，不含 asset_id 备选（asset_id 由独立的 _attachment_asset_id 获取）。"""
    metadata_record_id = attachment.metadata.record_id
    if isinstance(metadata_record_id, str) and metadata_record_id.strip():
        return metadata_record_id
    target_key = attachment.target_key
    if not target_key and attachment.anchor_payload is not None:
        target_key = attachment.anchor_payload.target_key
    if isinstance(target_key, str) and target_key.startswith("record:"):
        parts = target_key.split(":")
        if len(parts) >= 2 and parts[1]:
            return parts[1]
    return None


def _attachment_asset_id(attachment: ReaderAskAttachment) -> str | None:
    asset_id = attachment.metadata.asset_id or attachment.metadata.entry_id
    if isinstance(asset_id, str) and asset_id.strip():
        return asset_id
    target_key = attachment.target_key
    if not isinstance(target_key, str):
        return None
    parts = target_key.split(":")
    if len(parts) >= 5 and parts[0] == "record" and parts[2] == "analysis":
        return parts[-1] or None
    return None


def build_planner_input(
    *,
    user_message: str,
    entry_action: ReaderAskEntryAction,
    page_identity: ReaderAskPageIdentity,
    current_record_affordances: ReaderAskCurrentRecordAffordances,
    attachments: list[ReaderAskAttachment],
    anchors: list[ReaderAskAnchorRef],
    history_messages: list[dict[str, object]],
) -> ReaderAskPlannerInput:
    history: list[ReaderAskPlannerHistoryMessage] = []
    for item in history_messages:
        role = item.get("role")
        if role not in {"user", "assistant"}:
            continue
        content_md = _clean_reference_query(str(item.get("content_md") or "")) or ""
        if not content_md:
            continue
        history.append(
            ReaderAskPlannerHistoryMessage(
                role=role,
                content_md=content_md,
                resolved_intent=item.get("resolved_intent"),
            )
        )
    return ReaderAskPlannerInput(
        user_message=user_message,
        entry_action=entry_action,
        page_identity=page_identity,
        current_record_affordances=current_record_affordances,
        attachments=attachments,
        normalized_anchors=anchors,
        history=history,
    )


def reference_needs_from_decision(decision: ReaderAskPlannerDecision) -> ReaderAskReferenceNeeds:
    request = decision.reference_request
    return ReaderAskReferenceNeeds(
        requested=request.requested,
        query=_clean_reference_query(request.query),
        reason=request.reason,
    )


def _structured_asset_needs_from_decision(
    decision: ReaderAskPlannerDecision,
) -> ReaderAskStructuredAssetNeeds:
    request = decision.structured_asset_request
    return ReaderAskStructuredAssetNeeds(
        requested=request.requested,
        requested_asset_type=request.requested_asset_type,
        reason=request.reason,
    )


def build_resolved_context_input(
    *,
    page_identity: ReaderAskPageIdentity,
    entry_action: ReaderAskEntryAction,
    attachments: list[ReaderAskAttachment],
    anchors: list[ReaderAskAnchorRef],
    current_record_context: ReaderAskCurrentRecordContext | None = None,
    external_record_contexts: list[ReaderAskExternalRecordContext] | None = None,
    external_asset_contexts: list[ReaderAskExternalAssetContext] | None = None,
) -> ReaderAskResolvedContextInput:
    return ReaderAskResolvedContextInput(
        page_identity=page_identity,
        entry_action=entry_action,
        attachments=attachments,
        normalized_anchors=anchors,
        current_record_context=current_record_context,
        external_record_contexts=external_record_contexts or [],
        external_asset_contexts=external_asset_contexts or [],
    )


def _working_set_mode(
    *,
    clarification_mode: ReaderAskClarificationMode,
    working_set: ReaderAskWorkingSet,
    reference_resolution: ReaderAskReferenceResolution,
) -> ReaderAskWorkingSetMode:
    if clarification_mode == "must_clarify":
        return "clarification"
    if reference_resolution.status == "resolved":
        return "known_reference"
    if working_set.external_record_refs:
        return "explicit_external_record"
    if working_set.external_asset_refs and not working_set.external_record_refs:
        return "explicit_external_record"
    if working_set.article_overview_needed:
        return "article_overview"
    return "anchor_local"


def _planned_context_plan(
    *,
    entry_action: ReaderAskEntryAction,
    attachments: list[ReaderAskAttachment],
    anchors: list[ReaderAskAnchorRef],
    planner_decision: ReaderAskPlannerDecision,
    working_set: ReaderAskWorkingSet,
    reference_resolution: ReaderAskReferenceResolution,
    structured_asset_resolution: ReaderAskStructuredAssetResolution,
    clarification_mode: ReaderAskClarificationMode,
) -> ReaderAskContextPlan:
    clarification_reason = None
    external_record_context_reason = None
    structured_asset_lookup_reason = None
    if clarification_mode == "must_clarify":
        clarification_reason = planner_decision.clarification_reason
        if not clarification_reason:
            if reference_resolution.status == "ambiguous":
                clarification_reason = "ambiguous_known_reference"
            elif reference_resolution.status == "not_found":
                clarification_reason = "known_reference_not_found"
            elif structured_asset_resolution.status == "ambiguous":
                clarification_reason = "ambiguous_external_asset"
            else:
                clarification_reason = "missing_required_context"
    if working_set.external_record_refs:
        external_record_context_reason = (
            "known_reference_resolved"
            if reference_resolution.status == "resolved"
            else "explicit_external_record_context"
        )
        structured_asset_lookup_reason = "external_record_stable_assets_planned"
    return ReaderAskContextPlan(
        entry_action=entry_action,
        explicit_attachment_count=len(attachments),
        normalized_anchor_count=len(anchors),
        primary_anchor_type=working_set.primary_anchor.anchor_type if working_set.primary_anchor else (anchors[0].anchor_type if anchors else None),
        reference_query=reference_resolution.query,
        reference_resolution_attempted=reference_resolution.attempted,
        reference_resolution_status=reference_resolution.status,
        reference_resolution_reason=reference_resolution.reason,
        expanded_record_ids=[item["record_id"] for item in reference_resolution.resolved_records],
        used_cross_record_context=working_set.cross_record_context_allowed,
        cross_record_context_reason=(
            planner_decision.reference_request.reason
            if planner_decision.reference_request.requested
            else "explicit_external_record_context"
            if working_set.external_record_refs
            else "known_reference_resolved"
            if reference_resolution.status == "resolved"
            else None
        ),
        used_record_context=working_set.local_context_window_needed,
        record_context_reason=(
            "semantic_planner_requested_local_context"
            if working_set.local_context_window_needed
            else None
        ),
        used_record_insights=working_set.record_insights_needed,
        record_insights_reason=(
            "semantic_planner_requested_record_insights"
            if working_set.record_insights_needed
            else None
        ),
        used_article_overview=working_set.article_overview_needed,
        article_overview_reason=(
            "semantic_planner_requested_article_overview"
            if working_set.article_overview_needed
            else None
        ),
        used_dictionary=working_set.dictionary_needed,
        dictionary_reason=(
            "semantic_planner_requested_dictionary"
            if working_set.dictionary_needed
            else None
        ),
        external_record_context_reason=external_record_context_reason,
        structured_asset_lookup_reason=(
            planner_decision.structured_asset_request.reason
            if structured_asset_lookup_reason
            else None
        ),
        external_asset_selection_reason=(
            "explicit_external_asset"
            if working_set.external_asset_refs
            and any(item.get("reason") == "explicit_attachment" for item in working_set.external_asset_refs)
            else "structured_asset_resolved"
            if working_set.external_asset_refs
            else "structured_asset_ambiguous"
            if structured_asset_resolution.status == "ambiguous"
            else None
        ),
        clarification_reason=clarification_reason,
        source_labels=[],
    )


def _planned_trace_summary(
    *,
    planner_decision: ReaderAskPlannerDecision,
    reference_resolution: ReaderAskReferenceResolution,
    working_set: ReaderAskWorkingSet,
    clarification_mode: ReaderAskClarificationMode,
    disambiguation_state: ReaderAskDisambiguation | None = None,
    external_asset_disambiguation_state: ReaderAskAssetDisambiguation | None = None,
) -> ReaderAskTraceSummary:
    if clarification_mode == "must_clarify":
        planner_mode = "needs_local_clarification"
    elif clarification_mode == "can_answer_with_followup":
        planner_mode = "partial_answer_with_followup"
    elif reference_resolution.status == "resolved":
        planner_mode = "known_reference_resolved"
    elif reference_resolution.status == "ambiguous":
        planner_mode = "known_reference_ambiguous"
    elif reference_resolution.status == "not_found":
        planner_mode = "known_reference_not_found"
    else:
        planner_mode = "direct_answer"

    notes: list[str] = []
    if planner_decision.rationale:
        notes.append(planner_decision.rationale)
    if clarification_mode != "none" and planner_decision.clarification_reason:
        notes.append(f"需要澄清：{planner_decision.clarification_reason}")
    if working_set.article_overview_needed:
        notes.append("本轮优先使用当前文章概览。")
    if working_set.local_context_window_needed:
        notes.append("本轮优先使用当前文章正文窗口。")
    if working_set.record_insights_needed:
        notes.append("本轮优先使用当前文章稳定解析资产。")
    if working_set.external_record_refs:
        notes.append("本轮并入了其他文章记录。")
    if working_set.external_asset_refs:
        notes.append("本轮并入了外部文章里的稳定解析资产。")
    if external_asset_disambiguation_state and external_asset_disambiguation_state.required:
        notes.append("外部文章里的候选资产不唯一，需要先指定要并入哪一个。")

    return ReaderAskTraceSummary(
        planner_mode=planner_mode,
        reference_resolution_status=reference_resolution.status,
        working_set_mode=_working_set_mode(
            clarification_mode=clarification_mode,
            working_set=working_set,
            reference_resolution=reference_resolution,
        ),
        used_known_reference_resolution=reference_resolution.status == "resolved",
        used_external_record_context=bool(working_set.external_record_refs),
        used_structured_asset_lookup=bool(
            working_set.external_record_refs and planner_decision.structured_asset_request.requested
        ),
        used_hitp_disambiguation=bool(disambiguation_state and disambiguation_state.required),
        used_external_asset_context=bool(working_set.external_asset_refs),
        used_external_asset_disambiguation=bool(
            external_asset_disambiguation_state and external_asset_disambiguation_state.required
        ),
        supplement_generation_used=False,
        supplement_persisted_count=0,
        supplement_deleted_count=0,
        cross_record_context_allowed=working_set.cross_record_context_allowed,
        cross_record_context_used=False,
        tool_steps=[],
        notes=notes,
    )


def _planned_disambiguation_state(
    *,
    reference_resolution: ReaderAskReferenceResolution,
    clarification_mode: ReaderAskClarificationMode = "none",
) -> ReaderAskDisambiguation | None:
    if clarification_mode == "none" or reference_resolution.status != "ambiguous":
        return None
    candidates = [
        ReaderAskDisambiguationCandidate(
            record_id=item["record_id"],
            title=item.get("title"),
            updated_at=item.get("updated_at"),
            overview_hint=item.get("overview_hint"),
        )
        for item in reference_resolution.ambiguous_records
        if item.get("record_id")
    ]
    if not candidates:
        return None
    return ReaderAskDisambiguation(
        required=True,
        reason=reference_resolution.reason,
        query=reference_resolution.query,
        selection_mode="panel_cards",
        candidates=candidates,
    )


def _planned_external_asset_disambiguation_state(
    *,
    structured_asset_resolution: ReaderAskStructuredAssetResolution,
    clarification_mode: ReaderAskClarificationMode = "none",
) -> ReaderAskAssetDisambiguation | None:
    if clarification_mode == "none" or structured_asset_resolution.status != "ambiguous":
        return None
    candidates = [
        ReaderAskAssetDisambiguationCandidate(
            asset_type=item["asset_type"],
            asset_id=item["asset_id"],
            entry_type=item.get("entry_type"),
            title=item.get("title"),
            summary=item.get("summary"),
        )
        for item in structured_asset_resolution.ambiguous_assets
        if item.get("asset_id")
    ]
    if not candidates:
        return None
    return ReaderAskAssetDisambiguation(
        required=True,
        reason=structured_asset_resolution.reason,
        record_id=structured_asset_resolution.record_id,
        record_title=structured_asset_resolution.record_title,
        candidates=candidates,
    )


def _explicit_external_record_refs(
    attachments: list[ReaderAskAttachment],
) -> list[dict[str, str]]:
    return [
        {
            "record_id": record_id,
            "title": attachment.metadata.title or attachment.label,
            "reason": "explicit_attachment",
        }
        for attachment in attachments
        if attachment.kind == "record_ref" and attachment.subtype == "related_record"
        for record_id in [(_attachment_target_record(attachment) or "")]
        if record_id
    ]


def _explicit_external_asset_refs(
    attachments: list[ReaderAskAttachment],
    *,
    current_record_id: str,
) -> list[dict[str, str]]:
    return [
        {
            "record_id": record_id,
            "record_title": attachment.metadata.record_title or None,
            "asset_type": "supplement" if attachment.kind == "supplement_ref" else "analysis",
            "asset_id": asset_id,
            "entry_type": attachment.metadata.entry_type or attachment.subtype,
            "asset_title": attachment.metadata.title or attachment.label,
            "reason": "explicit_attachment",
        }
        for attachment in attachments
        if attachment.kind in {"analysis_ref", "supplement_ref"}
        for record_id in [(_attachment_record_id(attachment) or "")]
        for asset_id in [(_attachment_asset_id(attachment) or "")]
        if record_id and record_id != current_record_id and asset_id
    ]


def _merge_external_record_refs(
    explicit_refs: list[dict[str, str]],
    reference_resolution: ReaderAskReferenceResolution,
) -> list[dict[str, str]]:
    resolved_refs = [
        {
            "record_id": item["record_id"],
            "title": item["title"],
            "reason": "known_reference_resolved",
        }
        for item in reference_resolution.resolved_records
    ]
    merged: list[dict[str, str]] = []
    seen_record_ids: set[str] = set()
    for item in [*explicit_refs, *resolved_refs]:
        record_id = item["record_id"]
        if record_id in seen_record_ids:
            continue
        seen_record_ids.add(record_id)
        merged.append(item)
    return merged


def _merge_external_asset_refs(
    explicit_refs: list[dict[str, str]],
    structured_asset_resolution: ReaderAskStructuredAssetResolution,
) -> list[dict[str, object]]:
    resolved_refs = [
        {
            "record_id": item["record_id"],
            "record_title": item.get("record_title"),
            "asset_type": item["asset_type"],
            "asset_id": item["asset_id"],
            "entry_type": item.get("entry_type"),
            "asset_title": item.get("title"),
            "content_md": item.get("content_md"),
            "content_summary": item.get("summary"),
            "source_labels": item.get("source_labels") or [],
            "reason": "structured_asset_resolved",
        }
        for item in structured_asset_resolution.resolved_assets
    ]
    merged: list[dict[str, object]] = []
    seen_asset_keys: set[tuple[str, str]] = set()
    for item in [*resolved_refs, *explicit_refs]:
        key = (str(item["asset_type"]), str(item["asset_id"]))
        if key in seen_asset_keys:
            continue
        seen_asset_keys.add(key)
        merged.append(item)
    return merged


def plan_request(
    *,
    content: str,
    page_identity: ReaderAskPageIdentity,
    entry_action: ReaderAskEntryAction,
    attachments: list[ReaderAskAttachment],
    anchors: list[ReaderAskAnchorRef],
    planner_decision: ReaderAskPlannerDecision,
    planner_validation_status: str = "valid",
    reference_resolution: ReaderAskReferenceResolution | None = None,
    structured_asset_resolution: ReaderAskStructuredAssetResolution | None = None,
    skip_expensive_fields: bool = False,
) -> ReaderAskPlanningSnapshot:
    del content
    resolved_reference = reference_resolution or ReaderAskReferenceResolution()
    resolved_asset_resolution = structured_asset_resolution or ReaderAskStructuredAssetResolution()
    reference_needs = reference_needs_from_decision(planner_decision)
    structured_asset_needs = _structured_asset_needs_from_decision(planner_decision)

    explicit_external_record_refs = _explicit_external_record_refs(attachments)
    explicit_external_asset_refs = _explicit_external_asset_refs(
        attachments,
        current_record_id=page_identity.record_id,
    )
    merged_external_record_refs = _merge_external_record_refs(
        explicit_external_record_refs,
        resolved_reference,
    )
    merged_external_asset_refs = _merge_external_asset_refs(
        explicit_external_asset_refs,
        resolved_asset_resolution,
    )

    clarification_only = planner_decision.clarification_only
    clarification_mode: ReaderAskClarificationMode = planner_decision.clarification_mode
    if resolved_reference.status in {"ambiguous", "not_found"}:
        if anchors or (resolved_reference.status == "ambiguous" and planner_decision.working_set.local_context_window_needed):
            clarification_mode = "can_answer_with_followup"
        else:
            clarification_mode = "must_clarify"
    if resolved_asset_resolution.status == "ambiguous":
        # Asset ambiguity does not block answer generation; always downgrade to followup
        clarification_mode = "can_answer_with_followup"

    # Derive clarification_only from clarification_mode for backward compat
    if clarification_mode == "must_clarify":
        clarification_only = True
    elif clarification_mode == "can_answer_with_followup":
        clarification_only = False

    decision_working_set = planner_decision.working_set
    cross_record_context_allowed = bool(merged_external_record_refs) or decision_working_set.cross_record_context_allowed
    retrieval_needs: ReaderAskRetrievalNeeds = "known_reference_only" if cross_record_context_allowed else "none"
    working_set = ReaderAskWorkingSet(
        primary_anchor=anchors[0] if anchors else None,
        local_context_window_needed=decision_working_set.local_context_window_needed and clarification_mode != "must_clarify",
        record_insights_needed=decision_working_set.record_insights_needed and clarification_mode != "must_clarify",
        article_overview_needed=decision_working_set.article_overview_needed and clarification_mode != "must_clarify",
        dictionary_needed=decision_working_set.dictionary_needed and clarification_mode != "must_clarify",
        cross_record_context_allowed=cross_record_context_allowed,
        external_record_refs=merged_external_record_refs,
        external_asset_refs=merged_external_asset_refs,
        external_asset_lookup_needed=bool(
            (
                decision_working_set.external_asset_lookup_needed
                or structured_asset_needs.requested
                or explicit_external_asset_refs
            )
            and merged_external_record_refs
            and not merged_external_asset_refs
        ),
    )
    resolved_context_input = build_resolved_context_input(
        page_identity=page_identity,
        entry_action=entry_action,
        attachments=attachments,
        anchors=anchors,
    )
    if skip_expensive_fields:
        return ReaderAskPlanningSnapshot(
            resolved_intent=planner_decision.resolved_intent,
            planner_decision=planner_decision,
            planner_validation_status=planner_validation_status,
            resolved_context_input=resolved_context_input,
            reference_needs=reference_needs,
            retrieval_needs=retrieval_needs,
            resolved_references=resolved_reference,
            structured_asset_needs=structured_asset_needs,
            structured_asset_resolution=resolved_asset_resolution,
            working_set=working_set,
            context_plan=None,
            trace_summary=None,
            disambiguation_state=None,
            external_asset_disambiguation_state=None,
            clarification_only=clarification_only,
            clarification_mode=clarification_mode,
        )
    context_plan = _planned_context_plan(
        entry_action=entry_action,
        attachments=attachments,
        anchors=anchors,
        planner_decision=planner_decision,
        working_set=working_set,
        reference_resolution=resolved_reference,
        structured_asset_resolution=resolved_asset_resolution,
        clarification_mode=clarification_mode,
    )
    disambiguation_state = _planned_disambiguation_state(
        reference_resolution=resolved_reference,
        clarification_mode=clarification_mode,
    )
    external_asset_disambiguation_state = _planned_external_asset_disambiguation_state(
        structured_asset_resolution=resolved_asset_resolution,
        clarification_mode=clarification_mode,
    )
    trace_summary = _planned_trace_summary(
        planner_decision=planner_decision,
        reference_resolution=resolved_reference,
        working_set=working_set,
        clarification_mode=clarification_mode,
        disambiguation_state=disambiguation_state,
        external_asset_disambiguation_state=external_asset_disambiguation_state,
    )
    return ReaderAskPlanningSnapshot(
        resolved_intent=planner_decision.resolved_intent,
        planner_decision=planner_decision,
        planner_validation_status=planner_validation_status,
        resolved_context_input=resolved_context_input,
        reference_needs=reference_needs,
        retrieval_needs=retrieval_needs,
        resolved_references=resolved_reference,
        structured_asset_needs=structured_asset_needs,
        structured_asset_resolution=resolved_asset_resolution,
        working_set=working_set,
        context_plan=context_plan,
        trace_summary=trace_summary,
        disambiguation_state=disambiguation_state,
        external_asset_disambiguation_state=external_asset_disambiguation_state,
        clarification_only=clarification_only,
        clarification_mode=clarification_mode,
    )

def build_context_plan(
    *,
    entry_action: ReaderAskEntryAction,
    attachments: list[ReaderAskAttachment],
    anchors: list[ReaderAskAnchorRef],
    runtime_state: ReaderAskRuntimeState,
    citations: list[ReaderAskCitation],
    reference_resolution: ReaderAskReferenceResolution | None = None,
    planning_snapshot: ReaderAskPlanningSnapshot | None = None,
) -> ReaderAskContextPlan:
    has_record_insights = bool(runtime_state.latest_record_insights)
    used_dictionary = any(citation.kind in {"dictionary_entry", "dictionary_ai"} for citation in citations)
    used_record_context = runtime_state.latest_record_context is not None
    used_article_overview = bool(runtime_state.latest_article_overview)
    used_cross_record_context = runtime_state.used_cross_record_context or bool(
        planning_snapshot and planning_snapshot.working_set.external_record_refs
    )
    working_set = planning_snapshot.working_set if planning_snapshot else None
    clarification_reason = (
        planning_snapshot.context_plan.clarification_reason
        if planning_snapshot and planning_snapshot.context_plan.clarification_reason
        else None
    )
    return ReaderAskContextPlan(
        entry_action=entry_action,
        explicit_attachment_count=len(attachments),
        normalized_anchor_count=len(anchors),
        primary_anchor_type=anchors[0].anchor_type if anchors else None,
        reference_query=reference_resolution.query if reference_resolution else None,
        reference_resolution_attempted=bool(reference_resolution and reference_resolution.attempted),
        reference_resolution_status=reference_resolution.status if reference_resolution else "not_needed",
        reference_resolution_reason=reference_resolution.reason if reference_resolution else None,
        expanded_record_ids=[item["record_id"] for item in (reference_resolution.resolved_records if reference_resolution else [])],
        used_cross_record_context=used_cross_record_context,
        cross_record_context_reason=(
            "explicit_external_record_context"
            if working_set and working_set.external_record_refs
            else "known_reference_resolved"
            if reference_resolution and reference_resolution.status == "resolved"
            else "explicit_cross_article_request"
            if runtime_state.used_cross_record_context
            else None
        ),
        used_record_context=used_record_context,
        record_context_reason=(
            "anchor_window_loaded"
            if used_record_context
            else "anchor_window_planned"
            if working_set and working_set.local_context_window_needed
            else None
        ),
        used_record_insights=has_record_insights,
        record_insights_reason=(
            "record_insights_loaded"
            if has_record_insights
            else "grammar_or_breakdown_anchor"
            if working_set and working_set.record_insights_needed
            else None
        ),
        used_article_overview=used_article_overview,
        article_overview_reason=(
            "article_overview_loaded"
            if used_article_overview
            else "article_level_question"
            if working_set and working_set.article_overview_needed
            else None
        ),
        used_dictionary=used_dictionary,
        dictionary_reason=(
            "dictionary_entry_or_ai_used"
            if used_dictionary
            else "dictionary_lookup_planned"
            if working_set and working_set.dictionary_needed
            else None
        ),
        external_record_context_reason=(
            "external_record_context_loaded"
            if runtime_state.latest_external_record_contexts
            else "known_reference_resolved"
            if working_set and working_set.external_record_refs and reference_resolution and reference_resolution.status == "resolved"
            else "explicit_external_record_context"
            if working_set and working_set.external_record_refs
            else None
        ),
        structured_asset_lookup_reason=(
            "external_record_stable_assets_loaded"
            if runtime_state.latest_external_record_contexts
            and any(
                item.get("article_overview") or item.get("record_insights")
                for item in runtime_state.latest_external_record_contexts
            )
            else "external_record_stable_assets_planned"
            if working_set and working_set.external_record_refs
            else None
        ),
        external_asset_selection_reason=(
            "external_asset_context_loaded"
            if runtime_state.latest_external_asset_contexts
            else "explicit_external_asset"
            if working_set and working_set.external_asset_refs and any(item.get("reason") == "explicit_attachment" for item in working_set.external_asset_refs)
            else "structured_asset_resolved"
            if working_set and working_set.external_asset_refs
            else "structured_asset_ambiguous"
            if planning_snapshot
            and planning_snapshot.external_asset_disambiguation_state
            and planning_snapshot.external_asset_disambiguation_state.required
            else None
        ),
        clarification_reason=clarification_reason,
        source_labels=sorted(runtime_state.source_labels),
    )


def build_resolved_context_summary(
    *,
    record_id: str,
    record_title: str | None,
    anchors: list[ReaderAskAnchorRef],
    explicit_attachment_count: int,
    runtime_state: ReaderAskRuntimeState,
    used_cross_record_context: bool,
    citations: list[ReaderAskCitation],
) -> ReaderAskResolvedContextSummary:
    labels = []
    if anchors:
        labels.append("current_anchor")
    labels.append("current_record")
    if runtime_state.latest_record_context:
        labels.append("current_paragraph")
    if runtime_state.latest_article_overview:
        labels.append("article_overview")
    if runtime_state.latest_record_insights:
        labels.append("record_assets")
    if used_cross_record_context:
        labels.append("external_record_context")
    if runtime_state.latest_external_asset_contexts:
        labels.append("external_assets")
    if any(citation.kind == "vocabulary" for citation in citations):
        labels.append("vocabulary")
    if any(citation.kind in {"dictionary_entry", "dictionary_ai"} for citation in citations):
        labels.append("dictionary")
    return ReaderAskResolvedContextSummary(
        record_id=record_id,
        record_title=record_title,
        anchor_count=len(anchors),
        explicit_attachment_count=explicit_attachment_count,
        used_cross_record_context=used_cross_record_context,
        current_sentence_used=bool(anchors),
        current_paragraph_used=runtime_state.latest_record_context is not None,
        used_record_insights=bool(
            runtime_state.latest_article_overview
            or runtime_state.latest_record_insights
        ),
        used_dictionary=any(citation.kind in {"dictionary_entry", "dictionary_ai"} for citation in citations),
        source_labels=labels,
    )


def build_trace_summary(
    *,
    runtime_state: ReaderAskRuntimeState,
    context_plan: ReaderAskContextPlan,
    planning_snapshot: ReaderAskPlanningSnapshot | None = None,
    clarification_mode: ReaderAskClarificationMode = "none",
) -> ReaderAskTraceSummary:
    if clarification_mode == "must_clarify":
        planner_mode = "needs_local_clarification"
    elif clarification_mode == "can_answer_with_followup":
        planner_mode = "partial_answer_with_followup"
    elif context_plan.reference_resolution_status == "resolved":
        planner_mode = "known_reference_resolved"
    elif context_plan.reference_resolution_status == "ambiguous":
        planner_mode = "known_reference_ambiguous"
    elif context_plan.reference_resolution_status == "not_found":
        planner_mode = "known_reference_not_found"
    else:
        planner_mode = "direct_answer"

    notes: list[str] = []
    if context_plan.reference_resolution_status == "ambiguous":
        notes.append("跨文章引用未唯一命中，需要补充标题。")
    elif context_plan.reference_resolution_status == "not_found":
        notes.append("没有找到可直接纳入本轮上下文的已知文章标题。")
    if context_plan.used_article_overview:
        notes.append("已使用当前文章概览。")
    if context_plan.used_record_context:
        notes.append("已加载当前锚点附近的正文窗口。")
    if context_plan.used_record_insights:
        notes.append("已加载当前文章的稳定解析资产。")
    if context_plan.used_dictionary:
        notes.append("已使用词典或词典 AI。")
    if context_plan.used_cross_record_context:
        notes.append("本轮并入了跨文章上下文。")
    if runtime_state.latest_external_record_contexts and not any(
        item.get("article_overview") for item in runtime_state.latest_external_record_contexts
    ):
        notes.append("已定位到外部文章，但当前只有记录级信息，没有可用概览。")
    if runtime_state.latest_external_asset_contexts:
        notes.append("已并入外部文章里的稳定解析资产。")

    working_set_mode = (
        planning_snapshot.trace_summary.working_set_mode
        if planning_snapshot is not None
        else _working_set_mode(
            clarification_mode=clarification_mode,
            working_set=ReaderAskWorkingSet(
                cross_record_context_allowed=context_plan.used_cross_record_context,
                external_record_refs=[{"record_id": rid} for rid in context_plan.expanded_record_ids],
                article_overview_needed=context_plan.used_article_overview,
                external_asset_refs=[{"asset_id": ctx.get("asset_id")} for ctx in (runtime_state.latest_external_asset_contexts or []) if ctx.get("asset_id")],
            ),
            reference_resolution=ReaderAskReferenceResolution(
                status=context_plan.reference_resolution_status,
            ),
        )
    )

    return ReaderAskTraceSummary(
        planner_mode=planner_mode,
        reference_resolution_status=context_plan.reference_resolution_status,
        working_set_mode=working_set_mode,
        used_known_reference_resolution=context_plan.reference_resolution_status == "resolved",
        used_external_record_context=bool(runtime_state.latest_external_record_contexts),
        used_structured_asset_lookup=bool(
            runtime_state.latest_external_record_contexts
            and any(item.get("article_overview") or item.get("record_insights") for item in runtime_state.latest_external_record_contexts)
        ),
        used_hitp_disambiguation=context_plan.reference_resolution_status == "ambiguous",
        used_external_asset_context=bool(runtime_state.latest_external_asset_contexts),
        used_external_asset_disambiguation=bool(
            planning_snapshot
            and planning_snapshot.external_asset_disambiguation_state
            and planning_snapshot.external_asset_disambiguation_state.required
        ),
        supplement_generation_used=False,
        supplement_persisted_count=0,
        supplement_deleted_count=0,
        cross_record_context_allowed=context_plan.reference_resolution_attempted or context_plan.used_cross_record_context,
        cross_record_context_used=runtime_state.used_cross_record_context,
        tool_steps=[entry.tool_name for entry in runtime_state.tool_trace if entry.status == "completed"],
        notes=notes,
    )
