"""Ask Claread write proposal gate — precondition check for write-proposal tools.

This module extracts the write-proposal precondition logic from
``reader_ask_agent.py`` into a pure, testable contract.  The gate decides
whether a write-proposal tool may proceed; if not, it returns a stable
error payload that the agent tool function returns directly (bypassing
``_run_tool`` so no budget is consumed).

This module is placed at ``app.agents.reader_ask_write_gate`` (not inside
``app.services.reader_ask``) to avoid a circular import, same as
``reader_ask_tool_registry``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Precondition result
# ---------------------------------------------------------------------------

WriteGateReason = Literal["requires_primary_anchor"]


@dataclass(frozen=True, slots=True)
class WriteProposalPrecondition:
    """Result of a write-proposal precondition check.

    - ``allowed``: the tool may proceed through ``_run_tool``.
    - ``reason``: if not allowed, the stable reason code.
    - ``error_payload``: if not allowed, the dict to return to the agent
      (same shape as ``_NO_ANCHOR_ERROR`` / missing-note error).
    """

    allowed: bool
    reason: WriteGateReason | None = None
    error_payload: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Stable error payloads
# ---------------------------------------------------------------------------

NO_ANCHOR_ERROR_PAYLOAD: dict[str, Any] = {
    "status": "error",
    "summary": "No anchor available",
    "next_actions": ["Ask the user to select a sentence or text span first."],
    "artifacts": [],
}
"""Stable error payload returned when no primary anchor is available."""

MISSING_NOTE_TEXT_PAYLOAD: dict[str, Any] = {
    "status": "error",
    "summary": "Missing note_text",
    "next_actions": ["Provide the note content before proposing save_note."],
    "artifacts": [],
}
"""Stable error payload for missing note_text (used inside runner, not gate)."""


# ---------------------------------------------------------------------------
# Precondition checker
# ---------------------------------------------------------------------------

def check_write_proposal_precondition(
    tool_name: str,
    *,
    has_primary_anchor: bool,
) -> WriteProposalPrecondition:
    """Check whether a write-proposal tool may proceed.

    This is a pure function — no side effects, no budget consumption.

    The gate only checks **hard preconditions** that bypass ``_run_tool``
    entirely (no budget consumed, no tool trace emitted).  Softer validations
    (e.g. missing ``note_text``) are handled inside the tool runner so that
    ``_run_tool`` still records a started/completed trace and consumes budget.

    Returns:
        - ``allowed=True`` if the tool should proceed through ``_run_tool``.
        - ``allowed=False`` with ``reason`` and ``error_payload`` if the
          tool should return the error payload directly, bypassing
          ``_run_tool`` (no budget consumed).

    Hard precondition rules (first-version):

    1. ``propose_save_note`` and ``propose_save_highlight`` both require
       ``has_primary_anchor=True``.  If False, return
       ``reason="requires_primary_anchor"`` with ``NO_ANCHOR_ERROR_PAYLOAD``.
    """
    if not has_primary_anchor:
        return WriteProposalPrecondition(
            allowed=False,
            reason="requires_primary_anchor",
            error_payload=NO_ANCHOR_ERROR_PAYLOAD,
        )

    return WriteProposalPrecondition(allowed=True)
