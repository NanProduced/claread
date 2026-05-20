from __future__ import annotations

import re
from dataclasses import dataclass

from app.agents.reader_ask_agent import ReaderAskRuntimeState
from app.schemas.reader_ask import (
    ReaderAskAnchorRef,
    ReaderAskAttachment,
    ReaderAskCitation,
    ReaderAskContextPlan,
    ReaderAskEntryAction,
    ReaderAskPageIdentity,
    ReaderAskResolvedContextInput,
    ReaderAskResolvedContextSummary,
    ReaderAskResolvedIntent,
)

_PRACTICE_INTENT_RE = re.compile(r"(练习|习题|exercise|quiz|practice)", re.IGNORECASE)
_BREAKDOWN_INTENT_RE = re.compile(r"(拆句|拆解|主干|阅读顺序|break\s*down|breakdown)", re.IGNORECASE)
_GRAMMAR_INTENT_RE = re.compile(r"(语法|句法|从句|时态|语态|grammar|syntax)", re.IGNORECASE)
_VOCABULARY_INTENT_RE = re.compile(r"(词义|短语|搭配|表达|词汇|单词|vocabulary|phrase|word)", re.IGNORECASE)
_AMBIGUOUS_REF_RE = re.compile(r"(这里|这句|这段|刚刚那段|上一段|this|that|here|it)", re.IGNORECASE)


@dataclass(slots=True)
class ReaderAskPlanningSnapshot:
    resolved_intent: ReaderAskResolvedIntent
    resolved_context_input: ReaderAskResolvedContextInput


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
) -> ReaderAskContextPlan:
    return ReaderAskContextPlan(
        entry_action=entry_action,
        explicit_attachment_count=len(attachments),
        normalized_anchor_count=len(anchors),
        primary_anchor_type=anchors[0].anchor_type if anchors else None,
        used_history_lookup=runtime_state.used_history_lookup,
        used_record_context=runtime_state.latest_record_context is not None,
        used_record_insights=bool(runtime_state.latest_record_insights or runtime_state.latest_record_excerpt_assets),
        used_dictionary=any(citation.kind in {"dictionary_entry", "dictionary_ai"} for citation in citations),
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
