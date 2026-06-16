"""Route policy and planner-route helpers for Ask Claread.

Round 3 flips the default from planner-first to agent-loop-first. The route
policy determines whether a request should use the agent-loop path (model
decides context on demand via tools) or fall back to the legacy planner-first
path (planner pre-fetches working set before the answer agent runs).

Round 8 migrates the deictic-without-anchor fallback from planner_first to
agent_loop_first with a clarification hint, so the agent asks the user to
select a specific location instead of silently falling back to the planner.

Round 9 migrates the cross-record-toggle + keywords fallback from
planner_first to agent_loop_first with a cross-record intent hint, so the
agent calls resolve_known_reference on demand instead of requiring planner
pre-resolution.

Route values:

- ``"agent_loop_first"`` — default for article-bound queries. The main agent
  receives a minimal payload (overview, anchors, attachments, history) and
  calls read tools on demand.
- ``"planner_first"`` — legacy fallback for complex scenarios that need the
  planner to resolve context before answering (external references, dictionary
  anchors, long threads).

Decision logic lives in :func:`resolve_planner_route`.

See ``docs/tmp/ask-claread/TMP-ask-claread-agent-loop-refactor-task-tracker-2026-06-13.md``
§Round 3 for the design rationale.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from app.schemas.reader_ask import (
    ReaderAskAnchorRef,
    ReaderAskAttachment,
    ReaderAskContextPlan,
    ReaderAskEntryAction,
    ReaderAskTraceSummary,
)

if TYPE_CHECKING:
    from app.services.reader_ask.runtime_contract import ReaderAskAnswerRuntimeInput


# ---------------------------------------------------------------------------
# Route type
# ---------------------------------------------------------------------------

PlannerRoute = Literal["agent_loop_first", "planner_first"]
"""Possible planner route values.

- ``"agent_loop_first"``: default — model calls tools on demand.
- ``"planner_first"``: legacy fallback — planner pre-fetches context.
"""

# ---------------------------------------------------------------------------
# Planner-first trigger conditions
# ---------------------------------------------------------------------------

# Attachment kinds that require the planner to resolve context before
# answering. These carry external references (records, analyses, supplements)
# that need planner-level resolution.
_PLANNER_REQUIRED_ATTACHMENT_KINDS: frozenset[str] = frozenset(
    {"record_ref", "analysis_ref", "supplement_ref"}
)

# Substring keywords that, when present in the user's latest message, indicate
# cross-article intent. When combined with cross_record_toggle, these trigger
# the planner-first fallback.
_CROSS_RECORD_KEYWORDS: tuple[str, ...] = (
    "另一篇",
    "之前那篇",
    "previous",
    "earlier",
    "另一",
    "上篇",
)

# Deictic expressions that strongly refer to a specific location in the
# text without an anchor. Round 8: these no longer trigger planner_first;
# instead, the agent-loop-first path injects a clarification hint so the
# agent asks the user to select a specific location.
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

# History length threshold beyond which the planner-first fallback is used.
# Long threads have complex context that benefits from planner pre-resolution.
_LONG_HISTORY_THRESHOLD: int = 10


# ---------------------------------------------------------------------------
# Helper predicates
# ---------------------------------------------------------------------------


def detect_cross_record_in_message(text: str) -> bool:
    """Return True if ``text`` contains any known cross-article intent keyword.

    Exported for unit testing and future extension.
    """
    if not text:
        return False
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in _CROSS_RECORD_KEYWORDS)


def has_deictic_without_anchor(text: str, anchors: list[ReaderAskAnchorRef]) -> bool:
    """Return True if ``text`` contains a strong deictic expression but
    the request has no anchors to ground the reference.

    Round 8: this is now a public API used by ``build_agent_loop_context``
    to inject a clarification hint instead of triggering planner_first.
    """
    if not text:
        return False
    if anchors:
        return False
    lowered = text.lower()
    return any(pattern.lower() in lowered for pattern in _DEICTIC_PATTERNS)


def has_cross_record_intent(cross_record_toggle: bool, text: str) -> bool:
    """Return True if the user has enabled cross-record context and the
    message contains cross-article intent keywords.

    Round 9: this is a public API used by ``build_agent_loop_context``
    to inject a cross-record intent hint instead of triggering planner_first.
    The agent can then call ``resolve_known_reference`` on demand.
    """
    if not cross_record_toggle:
        return False
    return detect_cross_record_in_message(text)


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


def _has_planner_required_attachments(attachments: list[ReaderAskAttachment]) -> bool:
    """Return True if any attachment requires planner-level resolution."""
    return any(attachment.kind in _PLANNER_REQUIRED_ATTACHMENT_KINDS for attachment in attachments)


# ---------------------------------------------------------------------------
# Route resolution
# ---------------------------------------------------------------------------


def resolve_planner_route(
    *,
    entry_action: ReaderAskEntryAction,
    history_messages: list[dict[str, Any]],
    attachments: list[ReaderAskAttachment],
    anchors: list[ReaderAskAnchorRef],
    cross_record_toggle: bool,
    latest_user_message: str,
) -> PlannerRoute:
    """Determine the planner route for a given request.

    Returns ``"agent_loop_first"`` by default. Returns ``"planner_first"``
    when any of the following conditions hold:

    - Has attachments requiring planner resolution (record_ref / analysis_ref /
      supplement_ref).
    - Has a dictionary anchor or dictionary attachment.
    - History exceeds ``_LONG_HISTORY_THRESHOLD`` messages.

    Round 8: deictic-without-anchor no longer triggers planner_first. Instead,
    the agent-loop-first path detects deictic expressions and injects a
    clarification hint so the agent asks the user to select a specific location.
    See :func:`has_deictic_without_anchor`.

    Round 9: cross-record-toggle + keywords no longer triggers planner_first.
    Instead, the agent-loop-first path detects cross-record intent and injects
    a hint so the agent calls ``resolve_known_reference`` on demand.
    See :func:`has_cross_record_intent`.

    The ``entry_action`` is no longer used as a whitelist gate — all entry
    actions default to agent-loop-first unless one of the explicit fallback
    conditions triggers.
    """
    if _has_planner_required_attachments(attachments):
        return "planner_first"
    if _has_dictionary_anchor_or_attachment(anchors, attachments):
        return "planner_first"
    if len(history_messages) > _LONG_HISTORY_THRESHOLD:
        return "planner_first"
    return "agent_loop_first"


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
