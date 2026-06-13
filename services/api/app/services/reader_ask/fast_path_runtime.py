"""Fast-path runtime helpers for the Ask Claread agent-loop.

Round 1 introduces a controlled bypass of the legacy ``resolve_semantic_planning``
LLM call for article-bound / low-risk / short-history queries. The fast
path:

- skips the planner LLM call;
- skips the working_set-driven pre-fetch in ``materialize_planned_context``
  (but still calls it with ``planning_snapshot=None`` so article_overview
  can be picked up and source_labels remain accurate);
- builds a minimal ``context_plan`` / ``trace_summary`` from request data only;
- runs the same main answer agent stream as the legacy path.

The legacy planner-first path remains the default and is fully preserved.

Decision logic lives in :func:`should_use_fast_path`. See
``docs/tmp/ask-claread/TMP-ask-claread-agent-loop-refactor-task-tracker-2026-06-13.md``
§Round 1 for the full design rationale.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.schemas.reader_ask import (
    ReaderAskAnchorRef,
    ReaderAskAttachment,
    ReaderAskContextPlan,
    ReaderAskEntryAction,
    ReaderAskTraceSummary,
)

if TYPE_CHECKING:
    from app.services.reader_ask.runtime_contract import ReaderAskAnswerRuntimeInput


# Entry actions eligible for the fast path. Anything outside this set always
# uses the legacy planner-first path.
_FAST_PATH_ACTIONS: frozenset[ReaderAskEntryAction] = frozenset(
    {"explain_this", "ask_about_this", "why_here"}
)

# Attachment kinds that require the planner to resolve context before
# answering. The fast path cannot handle these because they carry
# external references (records, analyses, supplements) that need
# planner-level resolution.
_PLANNER_REQUIRED_ATTACHMENT_KINDS: frozenset[str] = frozenset(
    {"record_ref", "analysis_ref", "supplement_ref"}
)

# Substring keywords that, when present in the user's latest message, indicate
# cross-article intent. The fast path defers these to the legacy planner.
_CROSS_RECORD_KEYWORDS: tuple[str, ...] = (
    "另一篇",
    "之前那篇",
    "previous",
    "earlier",
    "另一",
    "上篇",
)

# Deictic expressions that strongly refer to a specific location in the
# text without an anchor. These require the planner to resolve the
# reference before answering.
_DEICTIC_PATTERNS: tuple[str, ...] = (
    "这里",
    "这句",
    "这段",
    "这一句",
    "这一段",
    "这行",
    "this sentence",
    "that sentence",
    "this paragraph",
    "that paragraph",
    "this line",
    "that line",
    "this part",
    "that part",
    "here",
)


def detect_cross_record_in_message(text: str) -> bool:
    """Return True if ``text`` contains any known cross-article intent keyword.

    Exported for unit testing and future extension. The keyword list is
    intentionally small for Round 1; expansion is deferred to Round 2
    alongside the tool registry rewrite.
    """
    if not text:
        return False
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in _CROSS_RECORD_KEYWORDS)


def _has_deictic_without_anchor(text: str, anchors: list[ReaderAskAnchorRef]) -> bool:
    """Return True if ``text`` contains a strong deictic expression but
    the request has no anchors to ground the reference.

    When the user says "explain this sentence" but no anchor is provided,
    the planner is needed to resolve the reference.
    """
    if not text:
        return False
    if anchors:
        return False
    lowered = text.lower()
    return any(pattern.lower() in lowered for pattern in _DEICTIC_PATTERNS)


def _has_dictionary_anchor_or_attachment(
    anchors: list[ReaderAskAnchorRef],
    attachments: list[ReaderAskAttachment],
) -> bool:
    """Return True if any anchor is a dictionary_entry or any attachment
    carries a dictionary-related subtype.

    Dictionary lookups need the planner to decide retrieval strategy.
    """
    for anchor in anchors:
        if anchor.anchor_type == "dictionary_entry":
            return True
    for attachment in attachments:
        if attachment.kind == "dictionary_entry" or attachment.subtype == "dictionary_entry":
            return True
    return False


def should_use_fast_path(
    *,
    entry_action: ReaderAskEntryAction,
    history_messages: list[dict[str, Any]],
    attachments: list[ReaderAskAttachment],
    anchors: list[ReaderAskAnchorRef],
    cross_record_toggle: bool,
    latest_user_message: str,
) -> bool:
    """Return True when the request is eligible for the agent-loop fast path.

    A request is eligible when ALL of the following hold:

    - ``entry_action`` is one of ``_FAST_PATH_ACTIONS``.
    - ``len(history_messages) <= 4`` (short thread).
    - ``cross_record_toggle`` is False (the user has not opted into cross-article mode).
    - No attachment has a kind in ``_PLANNER_REQUIRED_ATTACHMENT_KINDS``
      (no record_ref / analysis_ref / supplement_ref attachments).
    - No dictionary anchor or dictionary attachment is present.
    - ``why_here`` entry action requires at least one anchor.
    - No strong deictic expression without an anchor.
    - The latest user message does not contain any cross-article intent keyword.
    """
    if entry_action not in _FAST_PATH_ACTIONS:
        return False
    if len(history_messages) > 4:
        return False
    if cross_record_toggle:
        return False
    for attachment in attachments:
        if attachment.kind in _PLANNER_REQUIRED_ATTACHMENT_KINDS:
            return False
    if _has_dictionary_anchor_or_attachment(anchors, attachments):
        return False
    # why_here without any anchor requires the planner to resolve context.
    if entry_action == "why_here" and not anchors:
        return False
    # Strong deictic references without an anchor need the planner.
    if _has_deictic_without_anchor(latest_user_message, anchors):
        return False
    if detect_cross_record_in_message(latest_user_message):
        return False
    return True


def build_minimal_context_plan_for_runtime_input(
    contract: ReaderAskAnswerRuntimeInput,
) -> ReaderAskContextPlan:
    """Thin wrapper around :func:`app.services.reader_ask.planner.build_minimal_context_plan`
    using the contract's request data.
    """
    from app.services.reader_ask.planner import build_minimal_context_plan

    return build_minimal_context_plan(
        entry_action=contract.entry_action,
        attachments=list(contract.attachments),
        anchors=list(contract.anchors),
    )


def build_minimal_trace_summary_for_runtime_input(
    contract: ReaderAskAnswerRuntimeInput,
    *,
    planner_skipped: bool,
) -> ReaderAskTraceSummary:
    """Thin wrapper around :func:`app.services.reader_ask.planner.build_minimal_trace_summary`
    using the contract's request data.
    """
    from app.services.reader_ask.planner import build_minimal_trace_summary

    return build_minimal_trace_summary(
        entry_action=contract.entry_action,
        attachments=list(contract.attachments),
        anchors=list(contract.anchors),
        planner_skipped=planner_skipped,
    )
