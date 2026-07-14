"""SSE wire helpers for the agentic Reading Record Ask path.

Mirrors the existing event *names* used by the legacy stream so clients
remain compatible, but lives entirely under ``reader_record_ask`` and
does not import ``reader_ask.stream_events`` or ``ask_runtime``.
"""

from __future__ import annotations

import json
from typing import Any

EVENT_THREAD_READY = "thread.ready"
EVENT_MESSAGE_STARTED = "message.started"
EVENT_MESSAGE_DELTA = "message.delta"
EVENT_MESSAGE_COMPLETED = "message.completed"
EVENT_MESSAGE_INTERRUPTED = "message.interrupted"
EVENT_ERROR = "error"
# Agentic-only typed progress (safe for clients that ignore unknown events).
EVENT_AGENTIC_PROGRESS = "agentic.progress"
EVENT_AGENTIC_RUN_STARTED = "agentic.run_started"
EVENT_AGENTIC_TERMINAL = "agentic.terminal"


def encode_sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
