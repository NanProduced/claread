from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.agents.reader_ask_agent import ReaderAskRuntimeState
from app.schemas.reader_ask import (
    ReaderAskAnchorRef,
    ReaderAskAttachment,
    ReaderAskCitation,
    ReaderAskContextPlan,
    ReaderAskEntryAction,
    ReaderAskPageIdentity,
    ReaderAskReferenceResolutionStatus,
    ReaderAskResolvedContextInput,
    ReaderAskResolvedContextSummary,
    ReaderAskResolvedIntent,
    ReaderAskTraceSummary,
)

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
class ReaderAskPlanningSnapshot:
    resolved_intent: ReaderAskResolvedIntent
    resolved_context_input: ReaderAskResolvedContextInput


def _clean_reference_query(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"\s+", " ", value).strip(" \t\r\n,.;:!?，。；：！？")
    for suffix in (" 的", " 里", " 中", " 文章", " 解析"):
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)].strip()
    return cleaned or None


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


def needs_clarification(content: str, anchors: list[ReaderAskAnchorRef]) -> bool:
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
) -> ReaderAskResolvedContextInput:
    return ReaderAskResolvedContextInput(
        page_identity=page_identity,
        entry_action=entry_action,
        attachments=attachments,
        normalized_anchors=anchors,
    )


def build_context_plan(
    *,
    entry_action: ReaderAskEntryAction,
    attachments: list[ReaderAskAttachment],
    anchors: list[ReaderAskAnchorRef],
    runtime_state: ReaderAskRuntimeState,
    citations: list[ReaderAskCitation],
    reference_resolution: ReaderAskReferenceResolution | None = None,
) -> ReaderAskContextPlan:
    has_record_insights = bool(runtime_state.latest_record_insights or runtime_state.latest_record_excerpt_assets)
    used_dictionary = any(citation.kind in {"dictionary_entry", "dictionary_ai"} for citation in citations)
    used_record_context = runtime_state.latest_record_context is not None
    used_history_lookup = runtime_state.used_history_lookup
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
        history_lookup_reason="explicit_cross_article_request" if used_history_lookup else None,
        used_record_context=used_record_context,
        record_context_reason="anchor_window_loaded" if used_record_context else None,
        used_record_insights=has_record_insights,
        record_insights_reason="article_assets_loaded" if has_record_insights else None,
        used_dictionary=used_dictionary,
        dictionary_reason="dictionary_entry_or_ai_used" if used_dictionary else None,
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
        used_record_assets=bool(runtime_state.latest_record_insights or runtime_state.latest_record_excerpt_assets),
        used_dictionary=any(citation.kind in {"dictionary_entry", "dictionary_ai"} for citation in citations),
        source_labels=labels,
    )


def build_trace_summary(
    *,
    runtime_state: ReaderAskRuntimeState,
    context_plan: ReaderAskContextPlan,
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
    if context_plan.used_record_context:
        notes.append("已加载当前锚点附近的正文窗口。")
    if context_plan.used_record_insights:
        notes.append("已加载当前文章的稳定解析资产。")
    if context_plan.used_dictionary:
        notes.append("已使用词典或词典 AI。")
    if context_plan.used_history_lookup:
        notes.append("本轮允许历史资产扩展。")

    return ReaderAskTraceSummary(
        planner_mode=planner_mode,
        reference_resolution_status=context_plan.reference_resolution_status,
        history_lookup_allowed=context_plan.reference_resolution_attempted or context_plan.used_history_lookup,
        history_lookup_used=runtime_state.used_history_lookup,
        tool_steps=[entry.tool_name for entry in runtime_state.tool_trace if entry.status == "completed"],
        notes=notes,
    )
