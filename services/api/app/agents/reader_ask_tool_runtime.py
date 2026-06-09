"""Ask Claread tool runtime — tool execution, tracing, and event emission.

This module contains the runtime machinery for executing Ask Claread tools:
budget tracking, trace entry creation, and SSE event emission.  It is
extracted from ``reader_ask_agent.py`` to separate execution concerns from
tool registration and business logic.

To avoid a circular import, this module uses a ``Protocol`` for the deps
type instead of directly referencing ``ReaderAskAgentDeps``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from app.agents.reader_ask_tool_observation import normalize_tool_observation
from app.agents.reader_ask_tool_policy import ToolAvailabilityResult
from app.schemas.reader_ask import ReaderAskToolTraceEntry

# ---------------------------------------------------------------------------
# Protocol for runtime deps (avoids circular import)
# ---------------------------------------------------------------------------

class _ToolRuntimeState(Protocol):
    """Minimal protocol for the state fields used by _run_tool."""

    tool_call_count: int
    max_tool_calls: int
    tool_trace: list[ReaderAskToolTraceEntry]


class _ToolRuntimeDeps(Protocol):
    """Minimal protocol that run_tool / _emit_tool_event need from deps."""

    event_queue: asyncio.Queue[tuple[str, dict[str, Any]]]
    tool_availability: ToolAvailabilityResult | None

    @property
    def state(self) -> _ToolRuntimeState: ...


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

ToolEventName = Literal["tool.started", "tool.completed", "tool.failed"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


def _tool_trace(
    tool_name: str,
    status: Literal["started", "completed", "failed"],
    *,
    input_summary: str | None = None,
    summary: str | None = None,
    next_actions: list[str] | None = None,
    artifacts: list[str] | None = None,
) -> ReaderAskToolTraceEntry:
    now = _iso_now()
    if status == "started":
        return ReaderAskToolTraceEntry(
            tool_name=tool_name,
            status=status,
            started_at=now,
            input_summary=input_summary,
        )
    return ReaderAskToolTraceEntry(
        tool_name=tool_name,
        status=status,
        started_at=now,
        completed_at=now,
        input_summary=input_summary,
        summary=summary,
        next_actions=next_actions or [],
        artifacts=artifacts or [],
    )


def truncate_tool_arg(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split()).strip()
    if not text:
        return None
    return text[:120]


# ---------------------------------------------------------------------------
# Event emission
# ---------------------------------------------------------------------------

async def _emit_tool_event(
    deps: _ToolRuntimeDeps,
    event: ToolEventName,
    *,
    tool_name: str,
    summary: str | None = None,
    detail: str | None = None,
) -> None:
    from app.services.reader_ask.stream_events import (
        EVENT_TOOL_COMPLETED,
        EVENT_TOOL_FAILED,
        EVENT_TOOL_STARTED,
        tool_completed_payload,
        tool_failed_payload,
        tool_started_payload,
    )

    if event == EVENT_TOOL_FAILED:
        payload = tool_failed_payload(tool_name, detail or "Tool failed")
    elif event == EVENT_TOOL_STARTED:
        payload = tool_started_payload(tool_name)
    elif event == EVENT_TOOL_COMPLETED:
        payload = tool_completed_payload(tool_name, summary or "")
    else:
        payload = {"tool_name": tool_name}
    await deps.event_queue.put((event, payload))


# ---------------------------------------------------------------------------
# Tool runner
# ---------------------------------------------------------------------------

async def run_tool(
    deps: _ToolRuntimeDeps,
    tool_name: str,
    runner: Callable[[], Awaitable[Any]],
    *,
    input_summary: str | None = None,
) -> Any:
    # -- Availability hard enforcement --
    if (
        deps.tool_availability is not None
        and tool_name not in deps.tool_availability.allowed_tool_names
    ):
        detail = f"Tool '{tool_name}' is not available in the current context."
        deps.state.tool_trace.append(
            _tool_trace(
                tool_name,
                "failed",
                input_summary=input_summary,
                summary=detail,
                next_actions=["Use only tools available in the current context."],
            )
        )
        await _emit_tool_event(deps, "tool.failed", tool_name=tool_name, detail=detail)
        return {
            "status": "error",
            "summary": detail,
            "reason": "tool_not_available",
            "next_actions": ["Use only tools available in the current context."],
            "artifacts": [],
        }

    deps.state.tool_call_count += 1
    if deps.state.tool_call_count > deps.state.max_tool_calls:
        detail = (
            f"Tool call limit exceeded ({deps.state.max_tool_calls}). "
            "Please provide a direct answer without additional tool calls."
        )
        deps.state.tool_trace.append(
            _tool_trace(
                tool_name,
                "failed",
                input_summary=input_summary,
                summary=detail,
                next_actions=["Answer directly without more tool calls."],
            )
        )
        await _emit_tool_event(deps, "tool.failed", tool_name=tool_name, detail=detail)
        raise RuntimeError(detail)
    deps.state.tool_trace.append(_tool_trace(tool_name, "started", input_summary=input_summary))
    await _emit_tool_event(deps, "tool.started", tool_name=tool_name)
    try:
        result = await runner()
    except Exception as exc:
        detail = str(exc) or "Tool failed"
        deps.state.tool_trace.append(
            _tool_trace(
                tool_name,
                "failed",
                input_summary=input_summary,
                summary=detail,
                next_actions=["Retry only after clarifying the missing input or context."],
            )
        )
        await _emit_tool_event(deps, "tool.failed", tool_name=tool_name, detail=detail)
        raise
    obs = normalize_tool_observation(result)
    summary, next_actions, artifacts = obs.summary, obs.next_actions, obs.artifacts
    deps.state.tool_trace.append(
        _tool_trace(
            tool_name,
            "completed",
            input_summary=input_summary,
            summary=summary,
            next_actions=next_actions,
            artifacts=artifacts,
        )
    )
    await _emit_tool_event(deps, "tool.completed", tool_name=tool_name, summary=summary)
    return result
