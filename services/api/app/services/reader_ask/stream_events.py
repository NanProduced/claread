"""Stream event contract: SSE encoding, event names, and payload builders.

This module owns the SSE wire format and all event name/payload constants
for the reader ask streaming protocol. Service code should use these
constants and builders instead of hand-crafting event strings and dicts.

The event names and payload shapes are the public contract between backend
and frontend — any change here must be validated against the frontend
ReaderAskStreamEventName type and SSE consumer.
"""

from __future__ import annotations

import json
from typing import Any

# ---------------------------------------------------------------------------
# SSE wire format
# ---------------------------------------------------------------------------

def encode_sse(event: str, data: dict[str, Any]) -> str:
    """Encode an SSE frame with event name and JSON data payload."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ---------------------------------------------------------------------------
# Event name constants
# ---------------------------------------------------------------------------

EVENT_THREAD_READY = "thread.ready"
EVENT_MESSAGE_STARTED = "message.started"
EVENT_MESSAGE_DELTA = "message.delta"
EVENT_MESSAGE_COMPLETED = "message.completed"
EVENT_MESSAGE_INTERRUPTED = "message.interrupted"

EVENT_REASONING_STARTED = "reasoning.started"
EVENT_REASONING_DELTA = "reasoning.delta"
EVENT_REASONING_COMPLETED = "reasoning.completed"

EVENT_TOOL_STARTED = "tool.started"
EVENT_TOOL_COMPLETED = "tool.completed"
EVENT_TOOL_FAILED = "tool.failed"

EVENT_CONTEXT_COMPACTING = "context.compacting"
EVENT_REPLAN_STARTED = "replan.started"

EVENT_ERROR = "error"


# ---------------------------------------------------------------------------
# Error payload builders
# ---------------------------------------------------------------------------

def insufficient_credits_payload(
    remaining_points: int,
    required_points: int,
) -> dict[str, Any]:
    """Build the INSUFFICIENT_CREDITS error payload."""
    user_message = (
        f"当前积分不足：剩余 {remaining_points} 点，本次 Ask Claread 至少需要 "
        f"{required_points} 点。本轮请求未发送给模型。"
    )
    return {
        "code": "INSUFFICIENT_CREDITS",
        "detail": "Not enough credits for this Ask Claread request.",
        "user_message": user_message,
        "remaining_points": remaining_points,
        "required_points": required_points,
    }


def context_too_large_payload() -> dict[str, Any]:
    """Build the CONTEXT_TOO_LARGE error payload."""
    return {
        "code": "CONTEXT_TOO_LARGE",
        "detail": "Context exceeds budget even after aggressive compaction.",
        "user_message": "当前对话上下文过长，无法继续。请尝试精简问题或开始新对话。",
    }


def model_unavailable_payload() -> dict[str, Any]:
    """Build the MODEL_UNAVAILABLE error payload."""
    return {
        "code": "MODEL_UNAVAILABLE",
        "detail": "Ask Claread is temporarily unavailable.",
    }


def reader_ask_failed_payload(detail: str) -> dict[str, Any]:
    """Build the READER_ASK_FAILED error payload."""
    return {
        "code": "READER_ASK_FAILED",
        "detail": detail,
    }


def http_exception_payload(status_code: int, detail: str) -> dict[str, Any]:
    """Build an error payload from an HTTPException."""
    return {
        "code": str(status_code),
        "detail": detail,
    }


# ---------------------------------------------------------------------------
# Event payload builders
# ---------------------------------------------------------------------------

def thread_ready_payload(thread_id: str | Any, record_id: str | Any) -> dict[str, Any]:
    """Build the thread.ready event payload."""
    return {"thread_id": str(thread_id), "record_id": str(record_id)}


def message_started_payload(message_id: str | Any, reply_to: str | Any) -> dict[str, Any]:
    """Build the message.started event payload."""
    return {"message_id": str(message_id), "reply_to": str(reply_to)}


def message_delta_payload(message_id: str | Any, delta: str) -> dict[str, Any]:
    """Build the message.delta event payload."""
    return {"message_id": str(message_id), "delta": delta}


def context_compacting_payload(message_id: str | Any) -> dict[str, Any]:
    """Build the context.compacting event payload."""
    return {"message_id": str(message_id)}


def replan_started_payload(message_id: str | Any, reason: str) -> dict[str, Any]:
    """Build the replan.started event payload."""
    return {"message_id": str(message_id), "reason": reason}


def reasoning_started_payload(message_id: str | Any) -> dict[str, Any]:
    """Build the reasoning.started event payload."""
    return {"message_id": str(message_id)}


def reasoning_delta_payload(message_id: str | Any, delta: str) -> dict[str, Any]:
    """Build the reasoning.delta event payload."""
    return {"message_id": str(message_id), "delta": delta}


def reasoning_completed_payload(message_id: str | Any) -> dict[str, Any]:
    """Build the reasoning.completed event payload."""
    return {"message_id": str(message_id)}


def tool_started_payload(tool_name: str) -> dict[str, Any]:
    """Build the tool.started event payload."""
    return {"tool_name": tool_name}


def tool_completed_payload(tool_name: str, summary: str) -> dict[str, Any]:
    """Build the tool.completed event payload."""
    return {"tool_name": tool_name, "summary": summary}


def tool_failed_payload(tool_name: str, detail: str) -> dict[str, Any]:
    """Build the tool.failed event payload."""
    return {"tool_name": tool_name, "detail": detail}


def message_interrupted_payload(
    message_id: str | Any,
    content_md: str,
    detail: str,
    can_retry: bool = True,
) -> dict[str, Any]:
    """Build the message.interrupted event payload."""
    return {
        "message_id": str(message_id),
        "content_md": content_md,
        "detail": detail,
        "can_retry": can_retry,
    }
