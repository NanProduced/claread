"""Stream Checkpoint: incremental turn_run persistence during streaming.

This module provides the data structure and flush logic for periodically
persisting partial AI output to the database while the agent is still
streaming.  The actual persistence (repo.update_turn_run) is injected
via callback so the module stays free of repo dependencies.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Awaitable, Callable, Literal
from uuid import UUID

from app.services.reader_ask.agent_runner import AgentStreamRuntime

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class TurnRunStreamCheckpoint:
    """Tracks how much content/reasoning has been flushed for a turn_run."""

    turn_run_id: UUID
    build_output_json: Callable[[str, str | None, str | None], dict[str, Any]]
    update_turn_run_cb: Callable[..., Awaitable[None]]
    min_flush_interval_s: float = 0.8
    min_content_chars: int = 48
    min_reasoning_chars: int = 48
    last_flushed_at: float = 0.0
    last_flushed_content_len: int = 0
    last_flushed_reasoning_len: int = 0


def terminal_reasoning_status(reasoning_started: bool) -> Literal["completed"] | None:
    """Return the terminal reasoning status for a finished stream."""
    return "completed" if reasoning_started else None


def make_checkpoint_flush(
    checkpoint: TurnRunStreamCheckpoint | None,
) -> Callable[..., Awaitable[None]] | None:
    """Create a checkpoint flush callback suitable for agent_runner.start_reader_ask_agent_stream."""
    if checkpoint is None:
        return None

    async def _flush(runtime: AgentStreamRuntime, *, force: bool = False) -> None:
        await maybe_flush_turn_run_stream_checkpoint(
            checkpoint=checkpoint,
            runtime=runtime,
            force=force,
        )

    return _flush


async def maybe_flush_turn_run_stream_checkpoint(
    *,
    checkpoint: TurnRunStreamCheckpoint,
    runtime: AgentStreamRuntime,
    force: bool = False,
) -> None:
    """Flush a streaming checkpoint if enough new content has accumulated."""
    content_text = runtime.emitted_text
    reasoning_text = runtime.emitted_reasoning
    content_len = len(content_text)
    reasoning_len = len(reasoning_text)

    if content_len == 0 and reasoning_len == 0 and not (force and runtime.reasoning_started):
        return

    grew_content = content_len - checkpoint.last_flushed_content_len
    grew_reasoning = reasoning_len - checkpoint.last_flushed_reasoning_len
    now = perf_counter()
    has_new_content = grew_content > 0 or grew_reasoning > 0
    interval_elapsed = (
        checkpoint.last_flushed_at > 0
        and has_new_content
        and (now - checkpoint.last_flushed_at) >= checkpoint.min_flush_interval_s
    )
    should_flush = (
        force
        or checkpoint.last_flushed_at == 0
        or grew_content >= checkpoint.min_content_chars
        or grew_reasoning >= checkpoint.min_reasoning_chars
        or interval_elapsed
    )

    if not should_flush:
        return

    reasoning_status = "streaming" if runtime.reasoning_started else None
    reasoning_md = reasoning_text or None
    snapshot = checkpoint.build_output_json(content_text, reasoning_md, reasoning_status)

    try:
        await checkpoint.update_turn_run_cb(
            turn_run_id=checkpoint.turn_run_id,
            status="streaming",
            user_visible_output_json=snapshot,
        )
    except Exception:
        logger.warning(
            "reader_ask_stream_checkpoint_flush_failed",
            exc_info=True,
            extra={"turn_run_id": str(checkpoint.turn_run_id)},
        )
        return

    checkpoint.last_flushed_at = now
    checkpoint.last_flushed_content_len = content_len
    checkpoint.last_flushed_reasoning_len = reasoning_len
