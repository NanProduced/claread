from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from app.agents.reader_ask_agent import ReaderAskRuntimeState
from app.schemas.reader_ask import (
    ReaderAskAnchorRef,
    ReaderAskAttachment,
    ReaderAskCitation,
    ReaderAskContextPlan,
    ReaderAskCurrentRecordContext,
    ReaderAskDisambiguationCandidate,
    ReaderAskDisambiguation,
    ReaderAskEntryAction,
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

_PRACTICE_INTENT_RE = re.compile(r"(练习|习题|exercise|quiz|practice)", re.IGNORECASE)
_BREAKDOWN_INTENT_RE = re.compile(r"(拆句|拆解|主干|阅读顺序|break\s*down|breakdown)", re.IGNORECASE)
_GRAMMAR_INTENT_RE = re.compile(r"(语法|句法|从句|时态|语态|grammar|syntax)", re.IGNORECASE)
_VOCABULARY_INTENT_RE = re.compile(r"(词义|短语|搭配|表达|词汇|单词|vocabulary|phrase|word)", re.IGNORECASE)
_AMBIGUOUS_REF_RE = re.compile(r"(这里|这句|这段|刚刚那段|上一段|this|that|here|it)", re.IGNORECASE)
_REFERENCE_MARKER_RE = re.compile(
    r"(之前那篇|以前那篇|那篇|上一篇|另一篇|另一条|earlier article|previous article|that article|another article|other article)",
    re.IGNORECASE,
)
_QUOTED_REFERENCE_RE = re.compile(r"[\"“”'']([^\"“”'']{3,80})[\"“”'']")
_TITLEISH_REFERENCE_RE = re.compile(
    r"(?:那篇|上一篇|关于|article|analysis|piece|title)\s+([A-Za-z0-9][A-Za-z0-9\-:\s]{2,80}?)(?=\s*(?:的|里|中|文章|解析)|[？?。.!]|\s*$)",
    re.IGNORECASE,
)
_ARTICLE_LEVEL_RE = re.compile(
    r"(全文|整篇|整篇文章|本文主线|主线|核心论点|整体|通篇|这篇文章|这篇|overall|whole article|main point|main thread|big picture)",
    re.IGNORECASE,
)
_LOCAL_WINDOW_ATTACHMENT_SUBTYPES = {
    "sentence",
    "text_range",
    "multi_text",
    "translation",
    "sentence_analysis",
    "grammar_note",
    "supplement_ref",
}
_DICTIONARY_ATTACHMENT_SUBTYPES = {
    "dictionary_entry",
}


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
class ReaderAskWorkingSet:
    primary_anchor: ReaderAskAnchorRef | None = None
    local_context_window_needed: bool = False
    record_insights_needed: bool = False
    article_overview_needed: bool = False
    dictionary_needed: bool = False
    history_assets_allowed: bool = False
    external_record_refs: list[dict[str, str]] = field(default_factory=list)


@dataclass(slots=True)
class ReaderAskPlanningSnapshot:
    resolved_intent: ReaderAskResolvedIntent
    resolved_context_input: ReaderAskResolvedContextInput
    reference_needs: ReaderAskReferenceNeeds
    retrieval_needs: ReaderAskRetrievalNeeds
    resolved_references: ReaderAskReferenceResolution
    working_set: ReaderAskWorkingSet
    context_plan: ReaderAskContextPlan
    trace_summary: ReaderAskTraceSummary
    disambiguation_state: ReaderAskDisambiguation | None = None
    clarification_only: bool = False


def _clean_reference_query(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"\s+", " ", value).strip(" \t\r\n,.;:!?，。；：！？")
    for suffix in (" 的", " 里", " 中", " 文章", " 解析"):
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)].strip()
    return cleaned or None


def _attachment_target_record(attachment: ReaderAskAttachment) -> str | None:
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


def _word_or_phrase_selection(attachments: list[ReaderAskAttachment], anchors: list[ReaderAskAnchorRef]) -> bool:
    candidates = [attachment.selected_text for attachment in attachments]
    candidates.extend(anchor.selected_text for anchor in anchors)
    for candidate in candidates:
        normalized = re.sub(r"\s+", " ", (candidate or "").strip())
        if not normalized:
            continue
        token_count = len(normalized.split(" "))
        if token_count <= 4 and len(normalized) <= 40:
            return True
    return False


def _has_local_anchor(attachments: list[ReaderAskAttachment], anchors: list[ReaderAskAnchorRef]) -> bool:
    if anchors:
        return True
    return any(
        attachment.kind in {"text_selection", "analysis_ref", "supplement_ref"}
        or attachment.subtype in _LOCAL_WINDOW_ATTACHMENT_SUBTYPES
        for attachment in attachments
    )


def _has_dictionary_request(
    *,
    content: str,
    entry_action: ReaderAskEntryAction,
    attachments: list[ReaderAskAttachment],
    anchors: list[ReaderAskAnchorRef],
) -> bool:
    if entry_action == "lookup_in_context":
        return True
    if any(anchor.anchor_type == "dictionary_entry" for anchor in anchors):
        return True
    if any(attachment.subtype in _DICTIONARY_ATTACHMENT_SUBTYPES for attachment in attachments):
        return True
    return resolve_intent(content, attachments, entry_action) == "vocabulary" and _word_or_phrase_selection(attachments, anchors)


def _is_article_level_question(content: str, entry_action: ReaderAskEntryAction) -> bool:
    if entry_action == "compare_translation":
        return False
    return bool(_ARTICLE_LEVEL_RE.search(content))


def resolve_intent(
    content: str,
    attachments: list[ReaderAskAttachment],
    entry_action: ReaderAskEntryAction,
) -> ReaderAskResolvedIntent:
    if _PRACTICE_INTENT_RE.search(content):
        return "practice"
    if _BREAKDOWN_INTENT_RE.search(content):
        return "breakdown"
    if _GRAMMAR_INTENT_RE.search(content):
        return "grammar"
    if _VOCABULARY_INTENT_RE.search(content):
        return "vocabulary"
    if entry_action == "lookup_in_context":
        return "vocabulary"
    if entry_action == "why_here":
        return "grammar"
    if entry_action == "compare_translation":
        return "explain"
    if any(attachment.subtype == "translation" for attachment in attachments):
        return "explain"
    return "explain"


def needs_clarification(
    content: str,
    anchors: list[ReaderAskAnchorRef],
    *,
    resolved_reference_status: ReaderAskReferenceResolutionStatus = "not_needed",
) -> bool:
    if resolved_reference_status in {"ambiguous", "not_found"}:
        return True
    if anchors:
        return False
    return bool(_AMBIGUOUS_REF_RE.search(content))


def build_reference_needs(content: str) -> ReaderAskReferenceNeeds:
    quoted = _QUOTED_REFERENCE_RE.search(content)
    if quoted:
        return ReaderAskReferenceNeeds(
            requested=True,
            query=_clean_reference_query(quoted.group(1)),
            reason="quoted_reference",
        )

    titleish = _TITLEISH_REFERENCE_RE.search(content)
    if titleish:
        return ReaderAskReferenceNeeds(
            requested=True,
            query=_clean_reference_query(titleish.group(1)),
            reason="title_like_reference",
        )

    if _REFERENCE_MARKER_RE.search(content):
        return ReaderAskReferenceNeeds(
            requested=True,
            query=None,
            reason="reference_marker_without_title",
        )

    return ReaderAskReferenceNeeds()


def build_resolved_context_input(
    *,
    page_identity: ReaderAskPageIdentity,
    entry_action: ReaderAskEntryAction,
    attachments: list[ReaderAskAttachment],
    anchors: list[ReaderAskAnchorRef],
    current_record_context: ReaderAskCurrentRecordContext | None = None,
    external_record_contexts: list[ReaderAskExternalRecordContext] | None = None,
) -> ReaderAskResolvedContextInput:
    return ReaderAskResolvedContextInput(
        page_identity=page_identity,
        entry_action=entry_action,
        attachments=attachments,
        normalized_anchors=anchors,
        current_record_context=current_record_context,
        external_record_contexts=external_record_contexts or [],
    )


def _working_set_mode(
    *,
    clarification_only: bool,
    working_set: ReaderAskWorkingSet,
    reference_resolution: ReaderAskReferenceResolution,
) -> ReaderAskWorkingSetMode:
    if clarification_only:
        return "clarification"
    if reference_resolution.status == "resolved":
        return "known_reference"
    if working_set.external_record_refs:
        return "explicit_external_record"
    if working_set.article_overview_needed:
        return "article_overview"
    return "anchor_local"


def _planned_context_plan(
    *,
    entry_action: ReaderAskEntryAction,
    attachments: list[ReaderAskAttachment],
    anchors: list[ReaderAskAnchorRef],
    working_set: ReaderAskWorkingSet,
    reference_resolution: ReaderAskReferenceResolution,
    clarification_only: bool,
) -> ReaderAskContextPlan:
    clarification_reason = None
    external_record_context_reason = None
    structured_asset_lookup_reason = None
    if clarification_only:
        if reference_resolution.status == "ambiguous":
            clarification_reason = "ambiguous_known_reference"
        elif reference_resolution.status == "not_found":
            clarification_reason = "known_reference_not_found"
        else:
            clarification_reason = "missing_local_anchor"
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
        used_history_lookup=working_set.history_assets_allowed,
        history_lookup_reason=(
            "explicit_external_record_context"
            if working_set.external_record_refs
            else "known_reference_resolved"
            if reference_resolution.status == "resolved"
            else None
        ),
        used_record_context=working_set.local_context_window_needed,
        record_context_reason="anchor_window_planned" if working_set.local_context_window_needed else None,
        used_record_insights=working_set.record_insights_needed,
        record_insights_reason="grammar_or_breakdown_anchor" if working_set.record_insights_needed else None,
        used_article_overview=working_set.article_overview_needed,
        article_overview_reason="article_level_question" if working_set.article_overview_needed else None,
        used_dictionary=working_set.dictionary_needed,
        dictionary_reason="dictionary_lookup_planned" if working_set.dictionary_needed else None,
        external_record_context_reason=external_record_context_reason,
        structured_asset_lookup_reason=structured_asset_lookup_reason,
        clarification_reason=clarification_reason,
        source_labels=[],
    )


def _planned_trace_summary(
    *,
    reference_resolution: ReaderAskReferenceResolution,
    working_set: ReaderAskWorkingSet,
    clarification_only: bool,
    disambiguation_state: ReaderAskDisambiguation | None = None,
) -> ReaderAskTraceSummary:
    if clarification_only:
        planner_mode = "needs_local_clarification"
    elif reference_resolution.status == "resolved":
        planner_mode = "known_reference_resolved"
    elif reference_resolution.status == "ambiguous":
        planner_mode = "known_reference_ambiguous"
    elif reference_resolution.status == "not_found":
        planner_mode = "known_reference_not_found"
    else:
        planner_mode = "direct_answer"

    notes: list[str] = []
    if clarification_only:
        notes.append("当前问题缺少可定位的本文锚点，需要先澄清。")
    if working_set.article_overview_needed:
        notes.append("本轮优先使用当前文章概览。")
    if working_set.local_context_window_needed:
        notes.append("本轮优先使用当前锚点附近的正文窗口。")
    if working_set.external_record_refs:
        notes.append("本轮显式并入了其他文章记录。")

    return ReaderAskTraceSummary(
        planner_mode=planner_mode,
        reference_resolution_status=reference_resolution.status,
        working_set_mode=_working_set_mode(
            clarification_only=clarification_only,
            working_set=working_set,
            reference_resolution=reference_resolution,
        ),
        used_known_reference_resolution=reference_resolution.status == "resolved",
        used_external_record_context=bool(working_set.external_record_refs),
        used_structured_asset_lookup=bool(working_set.external_record_refs),
        used_hitp_disambiguation=bool(disambiguation_state and disambiguation_state.required),
        supplement_generation_used=False,
        supplement_persisted_count=0,
        supplement_deleted_count=0,
        history_lookup_allowed=working_set.history_assets_allowed,
        history_lookup_used=False,
        tool_steps=[],
        notes=notes,
    )


def _planned_disambiguation_state(
    *,
    reference_resolution: ReaderAskReferenceResolution,
    clarification_only: bool,
) -> ReaderAskDisambiguation | None:
    if not clarification_only or reference_resolution.status != "ambiguous":
        return None
    candidates = [
        ReaderAskDisambiguationCandidate(
            record_id=item["record_id"],
            title=item.get("title"),
            updated_at=item.get("updated_at"),
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


def plan_request(
    *,
    content: str,
    page_identity: ReaderAskPageIdentity,
    entry_action: ReaderAskEntryAction,
    attachments: list[ReaderAskAttachment],
    anchors: list[ReaderAskAnchorRef],
    reference_resolution: ReaderAskReferenceResolution | None = None,
) -> ReaderAskPlanningSnapshot:
    resolved_reference = reference_resolution or ReaderAskReferenceResolution()
    resolved_intent = resolve_intent(content, attachments, entry_action)
    reference_needs = build_reference_needs(content)

    explicit_external_record_refs = [
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
    resolved_external_record_refs = [
        {
            "record_id": item["record_id"],
            "title": item["title"],
            "reason": "known_reference_resolved",
        }
        for item in resolved_reference.resolved_records
    ]
    merged_external_record_refs: list[dict[str, str]] = []
    seen_external_record_ids: set[str] = set()
    for item in [*explicit_external_record_refs, *resolved_external_record_refs]:
        record_id = item["record_id"]
        if record_id in seen_external_record_ids:
            continue
        seen_external_record_ids.add(record_id)
        merged_external_record_refs.append(item)

    local_anchor = _has_local_anchor(attachments, anchors)
    clarification_only = needs_clarification(
        content,
        anchors,
        resolved_reference_status=resolved_reference.status,
    )
    article_overview_needed = (
        not clarification_only
        and not merged_external_record_refs
        and resolved_reference.status != "resolved"
        and _is_article_level_question(content, entry_action)
        and page_identity.has_article_overview
    )
    dictionary_needed = (
        not clarification_only
        and _has_dictionary_request(
            content=content,
            entry_action=entry_action,
            attachments=attachments,
            anchors=anchors,
        )
    )
    record_insights_needed = (
        not clarification_only
        and local_anchor
        and resolved_intent in {"grammar", "breakdown"}
    )
    local_context_window_needed = (
        not clarification_only
        and local_anchor
        and not article_overview_needed
    )
    history_assets_allowed = bool(merged_external_record_refs) or resolved_reference.status == "resolved"
    retrieval_needs: ReaderAskRetrievalNeeds = "known_reference_only" if history_assets_allowed else "none"

    working_set = ReaderAskWorkingSet(
        primary_anchor=anchors[0] if anchors else None,
        local_context_window_needed=local_context_window_needed,
        record_insights_needed=record_insights_needed,
        article_overview_needed=article_overview_needed,
        dictionary_needed=dictionary_needed,
        history_assets_allowed=history_assets_allowed,
        external_record_refs=merged_external_record_refs,
    )
    resolved_context_input = build_resolved_context_input(
        page_identity=page_identity,
        entry_action=entry_action,
        attachments=attachments,
        anchors=anchors,
    )
    context_plan = _planned_context_plan(
        entry_action=entry_action,
        attachments=attachments,
        anchors=anchors,
        working_set=working_set,
        reference_resolution=resolved_reference,
        clarification_only=clarification_only,
    )
    disambiguation_state = _planned_disambiguation_state(
        reference_resolution=resolved_reference,
        clarification_only=clarification_only,
    )
    trace_summary = _planned_trace_summary(
        reference_resolution=resolved_reference,
        working_set=working_set,
        clarification_only=clarification_only,
        disambiguation_state=disambiguation_state,
    )
    return ReaderAskPlanningSnapshot(
        resolved_intent=resolved_intent,
        resolved_context_input=resolved_context_input,
        reference_needs=reference_needs,
        retrieval_needs=retrieval_needs,
        resolved_references=resolved_reference,
        working_set=working_set,
        context_plan=context_plan,
        trace_summary=trace_summary,
        disambiguation_state=disambiguation_state,
        clarification_only=clarification_only,
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
    has_record_insights = bool(runtime_state.latest_record_insights or runtime_state.latest_record_excerpt_assets)
    used_dictionary = any(citation.kind in {"dictionary_entry", "dictionary_ai"} for citation in citations)
    used_record_context = runtime_state.latest_record_context is not None
    used_article_overview = bool(runtime_state.latest_article_overview)
    used_history_lookup = runtime_state.used_history_lookup or bool(
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
        used_history_lookup=used_history_lookup,
        history_lookup_reason=(
            "explicit_external_record_context"
            if working_set and working_set.external_record_refs
            else "known_reference_resolved"
            if reference_resolution and reference_resolution.status == "resolved"
            else "explicit_cross_article_request"
            if runtime_state.used_history_lookup
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
    used_history_lookup: bool,
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
    if runtime_state.latest_record_insights or runtime_state.latest_record_excerpt_assets:
        labels.append("record_assets")
    if used_history_lookup:
        labels.append("history_assets")
    if any(citation.kind == "vocabulary" for citation in citations):
        labels.append("vocabulary")
    if any(citation.kind in {"dictionary_entry", "dictionary_ai"} for citation in citations):
        labels.append("dictionary")
    return ReaderAskResolvedContextSummary(
        record_id=record_id,
        record_title=record_title,
        anchor_count=len(anchors),
        explicit_attachment_count=explicit_attachment_count,
        used_history_lookup=used_history_lookup,
        current_sentence_used=bool(anchors),
        current_paragraph_used=runtime_state.latest_record_context is not None,
        used_record_assets=bool(
            runtime_state.latest_article_overview
            or runtime_state.latest_record_insights
            or runtime_state.latest_record_excerpt_assets
        ),
        used_dictionary=any(citation.kind in {"dictionary_entry", "dictionary_ai"} for citation in citations),
        source_labels=labels,
    )


def build_trace_summary(
    *,
    runtime_state: ReaderAskRuntimeState,
    context_plan: ReaderAskContextPlan,
    planning_snapshot: ReaderAskPlanningSnapshot | None = None,
    clarification_only: bool = False,
) -> ReaderAskTraceSummary:
    if clarification_only:
        planner_mode = "needs_local_clarification"
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
    if context_plan.used_history_lookup:
        notes.append("本轮允许历史资产扩展。")
    if runtime_state.latest_external_record_contexts and not any(
        item.get("article_overview") for item in runtime_state.latest_external_record_contexts
    ):
        notes.append("已定位到外部文章，但当前只有记录级信息，没有可用概览。")

    working_set_mode = (
        planning_snapshot.trace_summary.working_set_mode
        if planning_snapshot is not None
        else "clarification"
        if clarification_only
        else "known_reference"
        if context_plan.reference_resolution_status == "resolved"
        else "article_overview"
        if context_plan.used_article_overview
        else "anchor_local"
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
        supplement_generation_used=False,
        supplement_persisted_count=0,
        supplement_deleted_count=0,
        history_lookup_allowed=context_plan.reference_resolution_attempted or context_plan.used_history_lookup,
        history_lookup_used=runtime_state.used_history_lookup,
        tool_steps=[entry.tool_name for entry in runtime_state.tool_trace if entry.status == "completed"],
        notes=notes,
    )
