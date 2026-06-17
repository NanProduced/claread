from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from app.agents.reader_ask_agent import ReaderAskRuntimeState
from app.schemas.reader_ask import (
    ReaderAskAnchorRef,
    ReaderAskAssetDisambiguation,
    ReaderAskAttachment,
    ReaderAskCitation,
    ReaderAskClarificationMode,
    ReaderAskContextPlan,
    ReaderAskCurrentRecordContext,
    ReaderAskDisambiguation,
    ReaderAskEntryAction,
    ReaderAskExternalAssetContext,
    ReaderAskExternalRecordContext,
    ReaderAskPageIdentity,
    ReaderAskReferenceResolutionStatus,
    ReaderAskResolvedContextInput,
    ReaderAskResolvedContextSummary,
    ReaderAskResolvedIntent,
    ReaderAskTraceSummary,
    ReaderAskWorkingSetMode,
)

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
    resolution_meta: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Resolution meta observation contract (Phase 4 Round 2)
#
# Stable field names for eval trace / planning snapshot observability.
# These are NOT consumed by the answer agent prompt — they exist only
# in planning_snapshot_json and eval trace for offline analysis.
# ---------------------------------------------------------------------------

RESOLUTION_META_STRATEGY: Literal["strategy"] = "strategy"
RESOLUTION_META_CANDIDATE_COUNT: Literal["candidate_count"] = "candidate_count"
RESOLUTION_META_SCORED_CANDIDATE_COUNT: Literal["scored_candidate_count"] = "scored_candidate_count"
RESOLUTION_META_TOP_SCORE: Literal["top_score"] = "top_score"
RESOLUTION_META_RUNNER_UP_SCORE: Literal["runner_up_score"] = "runner_up_score"
RESOLUTION_META_FALLBACK_REASON: Literal["fallback_reason"] = "fallback_reason"

RESOLUTION_META_FIELDS: frozenset[str] = frozenset({
    RESOLUTION_META_STRATEGY,
    RESOLUTION_META_CANDIDATE_COUNT,
    RESOLUTION_META_SCORED_CANDIDATE_COUNT,
    RESOLUTION_META_TOP_SCORE,
    RESOLUTION_META_RUNNER_UP_SCORE,
    RESOLUTION_META_FALLBACK_REASON,
})

# Strategy values
RESOLUTION_STRATEGY_NOT_REQUESTED: Literal["not_requested"] = "not_requested"
RESOLUTION_STRATEGY_NO_QUERY_RECENT: Literal["no_query_recent"] = "no_query_recent"
RESOLUTION_STRATEGY_TITLE_SEARCH: Literal["title_search"] = "title_search"
RESOLUTION_STRATEGY_RECENT_FALLBACK: Literal["recent_fallback"] = "recent_fallback"

RESOLUTION_STRATEGIES: frozenset[str] = frozenset({
    RESOLUTION_STRATEGY_NOT_REQUESTED,
    RESOLUTION_STRATEGY_NO_QUERY_RECENT,
    RESOLUTION_STRATEGY_TITLE_SEARCH,
    RESOLUTION_STRATEGY_RECENT_FALLBACK,
})

# Fallback reason values
RESOLUTION_FALLBACK_ILIKE_EMPTY: Literal["ilike_empty"] = "ilike_empty"

ReaderAskResolutionStrategy = Literal[
    "not_requested",
    "no_query_recent",
    "title_search",
    "recent_fallback",
]


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
    planner_decision: Any
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


# ---------------------------------------------------------------------------
# Agent-loop-first minimal helpers
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class MinimalPlanningSnapshot:
    """Lightweight planning snapshot used by the agent-loop-first path.

    Satisfies the duck-typed access in ``runtime_contract.build_prompt_payload``
    while keeping the live path independent from the removed semantic planner.
    """

    retrieval_needs: str = "none"
    working_set: ReaderAskWorkingSet = field(
        default_factory=lambda: ReaderAskWorkingSet()
    )
    context_plan: ReaderAskContextPlan | None = None
    trace_summary: ReaderAskTraceSummary | None = None
    clarification_mode: str = "none"
    clarification_reason: str | None = None
    # Compatibility fields referenced defensively by build_prompt_payload.
    resolved_intent: Any = None
    planner_decision: Any = None
    planner_validation_status: str = "n/a"
    resolved_context_input: Any = None
    reference_needs: Any = None
    resolved_references: Any = None
    structured_asset_needs: Any = None
    structured_asset_resolution: Any = None
    disambiguation_state: Any | None = None
    external_asset_disambiguation_state: Any | None = None
    clarification_only: bool = False


def build_minimal_context_plan(
    *,
    entry_action: ReaderAskEntryAction,
    attachments: list[ReaderAskAttachment],
    anchors: list[ReaderAskAnchorRef],
) -> ReaderAskContextPlan:
    """Build a minimal ``ReaderAskContextPlan`` for the agent-loop-first path.

    Conservative defaults: no cross-record, no external refs, no
    disambiguation. Used when ``planning_snapshot=None`` to keep
    ``runtime_contract.build_prompt_payload`` shape stable.
    """
    primary_anchor_type = None
    used_record_context = False
    used_dictionary = False
    if anchors:
        primary_anchor_type = anchors[0].anchor_type
        # Anchors indicate the user is asking about a specific location —
        # record context is implicitly needed.
        used_record_context = True
        if any(anchor.anchor_type == "dictionary_entry" for anchor in anchors):
            used_dictionary = True
    if any(
        attachment.kind == "text_selection" and attachment.subtype == "dictionary_entry"
        for attachment in attachments
    ):
        used_dictionary = True
    return ReaderAskContextPlan(
        entry_action=entry_action,
        explicit_attachment_count=len(attachments),
        normalized_anchor_count=len(anchors),
        primary_anchor_type=primary_anchor_type,
        reference_resolution_status="not_needed",
        used_record_context=used_record_context,
        used_dictionary=used_dictionary,
        source_labels=[],
    )


def build_minimal_trace_summary(
    *,
    entry_action: ReaderAskEntryAction,
    attachments: list[ReaderAskAttachment],
    anchors: list[ReaderAskAnchorRef],
    planner_skipped: bool,
) -> ReaderAskTraceSummary:
    """Build a minimal ``ReaderAskTraceSummary`` for the agent-loop-first path.

    ``planner_mode='direct_answer'`` signals the eval pipeline that the answer
    was produced directly by the agent-loop-first path.
    """
    notes: list[str] = []
    if planner_skipped:
        notes.append(f"planner_skipped: semantic planner removed (entry_action={entry_action})")
    if attachments:
        notes.append(f"{len(attachments)} attachment(s) carried into agent loop")
    return ReaderAskTraceSummary(
        planner_mode="direct_answer",
        reference_resolution_status="not_needed",
        working_set_mode="anchor_local",
        used_known_reference_resolution=False,
        used_external_record_context=False,
        used_structured_asset_lookup=False,
        used_hitp_disambiguation=False,
        used_external_asset_context=False,
        used_external_asset_disambiguation=False,
        supplement_generation_used=False,
        supplement_persisted_count=0,
        supplement_deleted_count=0,
        cross_record_context_allowed=False,
        cross_record_context_used=False,
        tool_steps=[],
        notes=notes,
    )


# entry_action -> (resolved_intent, label) mapping used by the agent-loop-first path.
_MINIMAL_INTENT_BY_ENTRY_ACTION: dict[str, tuple[ReaderAskResolvedIntent, str]] = {
    "ask_about_this": ("general", "ask_about_this"),
    "explain_this": ("explain", "explain_this"),
    "why_here": ("grammar", "why_here"),
    "lookup_in_context": ("vocabulary", "lookup_in_context"),
}


def build_minimal_resolved_intent(
    entry_action: str,
) -> tuple[ReaderAskResolvedIntent, str]:
    """Map an ``entry_action`` to a minimal ``(resolved_intent, label)``.

    Pure deterministic function used by the agent-loop-first path to construct
    ``ReaderAskAnswerRuntimeInput.resolved_intent`` / ``resolved_intent_label``
    without consulting the removed semantic planner.
    """
    if entry_action in _MINIMAL_INTENT_BY_ENTRY_ACTION:
        return _MINIMAL_INTENT_BY_ENTRY_ACTION[entry_action]
    return ("general", entry_action)
