"""Generation / envelope fence checks for Reading Record Ask tools.

Fence checkers are injectable so tests can force pre-tool or post-tool
stale states without touching production streams.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from app.services.reader_record_ask.context_envelope import (
    ReadingRecordAskContextEnvelope,
)


@dataclass(frozen=True, slots=True)
class FenceCheckResult:
    """Result of comparing the turn envelope to the live generation."""

    ok: bool
    reason: str | None = None


class GenerationFence(Protocol):
    """Callable fence used before and after tool I/O."""

    async def __call__(
        self,
        envelope: ReadingRecordAskContextEnvelope,
    ) -> FenceCheckResult: ...


FenceFn = Callable[
    [ReadingRecordAskContextEnvelope],
    Awaitable[FenceCheckResult] | FenceCheckResult,
]


@dataclass(slots=True)
class StaticGenerationFence:
    """Fence that compares envelope generation to an injectable live value."""

    live_generation: int
    # Flip to force failure even when generation matches (fingerprint stale, etc.).
    force_stale_reason: str | None = None

    async def __call__(
        self,
        envelope: ReadingRecordAskContextEnvelope,
    ) -> FenceCheckResult:
        if self.force_stale_reason:
            return FenceCheckResult(ok=False, reason=self.force_stale_reason)
        if self.live_generation != envelope.record_generation:
            return FenceCheckResult(
                ok=False,
                reason=(
                    f"active generation {self.live_generation} does not match "
                    f"envelope generation {envelope.record_generation}"
                ),
            )
        return FenceCheckResult(ok=True)


@dataclass(slots=True)
class SequenceGenerationFence:
    """Fence that returns a scripted sequence of results (for pre/post tests).

    Exhausted scripts default to the last result, or ok if empty.
    """

    results: list[FenceCheckResult]
    call_count: int = 0

    async def __call__(
        self,
        envelope: ReadingRecordAskContextEnvelope,
    ) -> FenceCheckResult:
        del envelope
        index = self.call_count
        self.call_count += 1
        if not self.results:
            return FenceCheckResult(ok=True)
        if index >= len(self.results):
            return self.results[-1]
        return self.results[index]


async def run_fence(
    fence: FenceFn,
    envelope: ReadingRecordAskContextEnvelope,
) -> FenceCheckResult:
    """Invoke a sync or async fence callable."""
    result = fence(envelope)
    if isinstance(result, FenceCheckResult):
        return result
    return await result
