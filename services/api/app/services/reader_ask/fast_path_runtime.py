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


def should_use_fast_path(
    *,
    entry_action: ReaderAskEntryAction,
    history_messages: list[dict[str, Any]],
    attachments: list[ReaderAskAttachment],
    cross_record_toggle: bool,
    latest_user_message: str,
) -> bool:
    """Return True when the request is eligible for the agent-loop fast path.

    A request is eligible when ALL of the following hold:

    - ``entry_action`` is one of ``_FAST_PATH_ACTIONS``.
    - ``len(history_messages) <= 4`` (short thread).
    - ``cross_record_toggle`` is False (the user has not opted into cross-article mode).
    - No attachment has ``kind='record_ref' and subtype='related_record'``
      (no explicit cross-record attachment).
    - The latest user message does not contain any cross-article intent keyword.
    """
    if entry_action not in _FAST_PATH_ACTIONS:
        return False
    if len(history_messages) > 4:
        return False
    if cross_record_toggle:
        return False
    for attachment in attachments:
        if attachment.kind == "record_ref" and attachment.subtype == "related_record":
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
