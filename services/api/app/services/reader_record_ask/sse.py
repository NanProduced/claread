"""Canonical SSE wire helpers for Reading Record Ask v2."""

from __future__ import annotations

import json
from typing import Any

EVENT_THREAD_READY = "thread.ready"
EVENT_MESSAGE_STARTED = "message.started"
EVENT_MESSAGE_DELTA = "message.delta"
EVENT_MESSAGE_COMPLETED = "message.completed"
EVENT_MESSAGE_PREVIEW_RESET = "message.preview_reset"
EVENT_ERROR = "error"
# Agentic-only typed progress (safe for clients that ignore unknown events).
EVENT_AGENTIC_PROGRESS = "agentic.progress"
EVENT_AGENTIC_RUN_STARTED = "agentic.run_started"
EVENT_AGENTIC_TERMINAL = "agentic.terminal"
# Reasoning projection (ASK-REASONING-): safe projected provider
# reasoning, produced exclusively by the server-side reasoning projection
# chokepoint (reader_record_ask.reasoning_projection). Raw reasoning never
# enters SSE/DTO/DB/logs — only the deterministic redacted, quota-bounded
# projection does. Distinct from legacy ``reasoning.*`` (raw CoT passthrough
# on the legacy reader_ask path); the agentic path no longer maps analysis
# phase events onto reasoning lifecycle signals — progress and reasoning
# are separate channels.
# Learner reasoning summary. Snapshots are replace-semantics only — no empty
# started shell and no provider chain-of-thought event family.
EVENT_AGENTIC_LEARNER_REASONING_SNAPSHOT = "agentic.learner_reasoning.snapshot"
# Thread-memory lifecycle. These always precede reasoning.started for a turn.
EVENT_CONTEXT_COMPACTION_STARTED = "context.compaction.started"
EVENT_CONTEXT_COMPACTION_COMPLETED = "context.compaction.completed"
EVENT_CONTEXT_COMPACTION_FAILED = "context.compaction.failed"
EVENT_CONTEXT_COMPACTION_FALLBACK = "context.compaction.fallback"


def encode_sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
