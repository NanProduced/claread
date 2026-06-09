"""Ask Claread tool availability policy — static contract for tool gating.

This module defines the inputs and outputs of the tool availability decision,
plus a conservative first-version policy that does **not** change agent behavior.

This module is placed at ``app.agents.reader_ask_tool_policy`` (not inside
``app.services.reader_ask``) to avoid a circular import, same as
``reader_ask_tool_registry``.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.agents.reader_ask_tool_registry import (
    READER_ASK_TOOL_REGISTRY,
    TOOL_PROPOSE_SAVE_HIGHLIGHT,
    TOOL_PROPOSE_SAVE_NOTE,
)
from app.schemas.reader_ask import ReaderAskEntryAction, ReaderAskTaskMode

# ---------------------------------------------------------------------------
# Input / Output contracts
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ToolAvailabilityInput:
    """Runtime context that influences which tools are available.

    All fields are read-only snapshots taken before the agent loop starts.
    """

    task_mode: ReaderAskTaskMode
    entry_action: ReaderAskEntryAction
    has_primary_anchor: bool
    has_dictionary_anchor: bool = False
    has_generated_annotation_cache: bool = False


@dataclass(frozen=True, slots=True)
class ToolAvailabilityResult:
    """Result of the tool availability policy evaluation.

    - ``allowed_tool_names``: the set of tools the agent may attempt to call.
      In this first version it always equals the full set of ``agent_callable``
      tools from the registry — no tool is removed.
    - ``unavailable_reasons``: for tools that are technically allowed but have
      an unsatisfied precondition, this dict maps tool name to a reason string.
      The tool is **not** removed from ``allowed_tool_names``; the reason is
      informational only, for audit / future guard use.
    """

    allowed_tool_names: frozenset[str]
    unavailable_reasons: dict[str, str]


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------

_REASON_REQUIRES_PRIMARY_ANCHOR = "requires_primary_anchor"


def build_tool_availability(inp: ToolAvailabilityInput) -> ToolAvailabilityResult:
    """Evaluate the tool availability policy.

    First-version policy (conservative — no tool is removed):

    1. All tools with ``agent_callable=True`` in the registry are included in
       ``allowed_tool_names``.
    2. Write-proposal tools (``propose_save_note``, ``propose_save_highlight``)
       that require a primary anchor but none is present are recorded in
       ``unavailable_reasons`` with ``"requires_primary_anchor"``.  They are
       **not** removed from ``allowed_tool_names``.
    3. No tool is removed based on ``task_mode``, ``entry_action``,
       ``has_dictionary_anchor``, or ``has_generated_annotation_cache``.
    """
    allowed: frozenset[str] = frozenset(
        spec.name for spec in READER_ASK_TOOL_REGISTRY.values() if spec.agent_callable
    )

    unavailable_reasons: dict[str, str] = {}

    if not inp.has_primary_anchor:
        for tool_name in (TOOL_PROPOSE_SAVE_NOTE, TOOL_PROPOSE_SAVE_HIGHLIGHT):
            spec = READER_ASK_TOOL_REGISTRY.get(tool_name)
            if spec is not None and spec.requires_anchor:
                unavailable_reasons[tool_name] = _REASON_REQUIRES_PRIMARY_ANCHOR

    return ToolAvailabilityResult(
        allowed_tool_names=allowed,
        unavailable_reasons=unavailable_reasons,
    )
