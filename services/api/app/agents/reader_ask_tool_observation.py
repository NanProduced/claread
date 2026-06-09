"""Ask Claread tool observation — stable contract for tool result normalization.

Every tool result returned through ``_run_tool`` is normalized into a
``ToolObservation`` so that the observation shape is explicit and testable.

This module is placed at ``app.agents.reader_ask_tool_observation`` (not inside
``app.services.reader_ask``) to avoid a circular import, same as
``reader_ask_tool_registry``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

ToolObservationStatus = Literal["success", "warning", "error"]


# ---------------------------------------------------------------------------
# ToolObservation
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ToolObservation:
    """Stable observation shape for Ask Claread tool results.

    - ``status``: success / warning / error.  Error observations are *not*
      exceptions — the tool function returns an error dict and ``_run_tool``
      still records a ``completed`` trace entry.
    - ``summary``: human-readable one-line description.
    - ``next_actions``: suggested follow-up actions (non-empty strings only).
    - ``artifacts``: references produced by the tool (non-empty strings only).
    """

    status: ToolObservationStatus
    summary: str
    next_actions: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _filter_str_list(items: Any) -> list[str]:
    """Filter a list to only non-empty, stripped strings."""
    if not isinstance(items, list):
        return []
    return [
        item.strip()
        for item in items
        if isinstance(item, str) and item.strip()
    ]


# ---------------------------------------------------------------------------
# Public normalizer
# ---------------------------------------------------------------------------

def normalize_tool_observation(result: Any) -> ToolObservation:
    """Normalize an arbitrary tool result into a stable ToolObservation.

    Fallback rules:

    - **dict** with ``status="error"``   → status=error, summary from
      ``summary`` / ``reason`` / ``"Loaded"``.
    - **dict** with ``status="warning"`` → status=warning.
    - **dict** otherwise                  → status=success, summary from
      ``summary`` / ``reason`` / ``"Loaded"``.
    - **list**                            → status=success,
      summary=``"{n} item(s)"``.
    - **None**                            → status=success,
      summary=``"Loaded"``.
    - **scalar**                          → status=success,
      summary=``"Loaded"``.
    """
    if isinstance(result, dict):
        raw_status = result.get("status")
        if raw_status == "error":
            status: ToolObservationStatus = "error"
        elif raw_status == "warning":
            status = "warning"
        else:
            status = "success"
        summary = str(result.get("summary") or result.get("reason") or "Loaded")
        next_actions = _filter_str_list(result.get("next_actions"))
        artifacts = _filter_str_list(result.get("artifacts"))
        return ToolObservation(
            status=status,
            summary=summary,
            next_actions=next_actions,
            artifacts=artifacts,
        )
    if isinstance(result, list):
        return ToolObservation(
            status="success",
            summary=f"{len(result)} item(s)",
        )
    if result is None:
        return ToolObservation(
            status="success",
            summary="Loaded",
        )
    return ToolObservation(
        status="success",
        summary="Loaded",
    )
