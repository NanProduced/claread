"""Per-turn serial latest-wins projector worker.

- At most one in-flight provider request per turn.
- New checkpoints only update ``pending`` while a request runs.
- Every **dispatched** request counts toward the turn-global limit of 3.
- Main answer path never awaits this worker mid-turn.
- Finalize uses a short grace drain of the in-flight request only.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.services.reader_record_ask.learner_reasoning.capacity import (
    NonBlockingCapacityLimiter,
    get_global_projector_limiter,
)
from app.services.reader_record_ask.learner_reasoning.projector import (
    ProjectorRunFn,
    run_learner_reasoning_projector,
)
from app.services.reader_record_ask.learner_reasoning.router import (
    ProjectorRoute,
)
from app.services.reader_record_ask.learner_reasoning.schemas import (
    MAX_DISPATCHES_PER_TURN,
    FrozenCheckpoint,
    ValidatedLearnerSummary,
)
from app.services.reader_record_ask.learner_reasoning.validator import (
    validate_learner_text_zh,
)

logger = logging.getLogger(__name__)

PublishFn = Callable[[ValidatedLearnerSummary], None]


@dataclass
class LearnerReasoningWorker:
    """Serial worker with latest-wins pending queue (depth 1)."""

    route: ProjectorRoute | None
    api_key: str
    publish: PublishFn
    run_fn: ProjectorRunFn | None = None
    model: Any | None = None
    limiter: NonBlockingCapacityLimiter | None = None
    max_dispatches: int = MAX_DISPATCHES_PER_TURN

    dispatch_count: int = 0
    _pending: FrozenCheckpoint | None = None
    _closed: bool = False
    _intake_frozen: bool = False
    _busy: bool = False
    _in_flight_generation: int | None = None
    _task: asyncio.Task[None] | None = field(default=None, repr=False)
    _wake: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    _previous_safe_summary: str | None = None
    _sequence: int = 0
    # Generations whose results must never publish (retry invalidation).
    _invalidated_generations: set[int] = field(default_factory=set)
    dropped_backpressure: int = 0
    dropped_budget: int = 0
    dropped_stale: int = 0
    published_count: int = 0

    def submit(self, checkpoint: FrozenCheckpoint) -> None:
        """Enqueue checkpoint (sync). Never awaits provider."""
        if self._closed or self._intake_frozen:
            return
        if checkpoint.generation_id in self._invalidated_generations:
            self.dropped_stale += 1
            return
        if self.dispatch_count >= self.max_dispatches:
            self.dropped_budget += 1
            return
        self._pending = checkpoint
        self._wake.set()
        self._ensure_task()

    def _ensure_task(self) -> None:
        if self._task is not None and not self._task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._task = loop.create_task(
            self._run_loop(), name="learner-reasoning-worker"
        )

    async def _run_loop(self) -> None:
        while not self._closed:
            checkpoint = self._pending
            if checkpoint is None:
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=0.05)
                except TimeoutError:
                    if self._closed:
                        break
                    if self._intake_frozen and self._pending is None:
                        break
                    if self._pending is None and not self._busy:
                        break
                    continue
                continue

            self._pending = None
            if checkpoint.generation_id in self._invalidated_generations:
                self.dropped_stale += 1
                continue
            if self.dispatch_count >= self.max_dispatches:
                self.dropped_budget += 1
                continue

            limiter = self.limiter or get_global_projector_limiter()
            if not limiter.try_acquire():
                self.dropped_backpressure += 1
                continue

            self._busy = True
            self._in_flight_generation = checkpoint.generation_id
            self.dispatch_count += 1
            try:
                text, detail = await run_learner_reasoning_projector(
                    raw_window=checkpoint.window_text,
                    previous_safe_summary=self._previous_safe_summary,
                    route=self.route,
                    api_key=self.api_key,
                    run_fn=self.run_fn,
                    model=self.model,
                )
                del detail
                if self._closed:
                    continue
                if checkpoint.generation_id in self._invalidated_generations:
                    self.dropped_stale += 1
                    continue
                if text is None:
                    continue
                validated = validate_learner_text_zh(text)
                if validated is None:
                    continue
                self._sequence += 1
                summary = ValidatedLearnerSummary(
                    text=validated,
                    stage=checkpoint.stage,
                    basis=checkpoint.basis,
                    revision=checkpoint.revision,
                    sequence=self._sequence,
                    generation_id=checkpoint.generation_id,
                )
                self._previous_safe_summary = validated
                try:
                    self.publish(summary)
                    self.published_count += 1
                except Exception:  # noqa: BLE001
                    logger.info(
                        "reader_record_ask learner_reasoning publish failed"
                    )
            finally:
                self._busy = False
                self._in_flight_generation = None
                try:
                    limiter.release()
                except Exception:  # noqa: BLE001
                    pass

            # After freeze: at most the one drained request — no more pending.
            if self._intake_frozen:
                self._pending = None
                break
            if self._pending is not None and not self._closed:
                continue
            break

    def invalidate_generation(self, generation_id: int) -> None:
        """Mark a generation unpublishable (retry paths)."""
        self._invalidated_generations.add(generation_id)
        if (
            self._pending is not None
            and self._pending.generation_id == generation_id
        ):
            self._pending = None
            self.dropped_stale += 1

    def note_generation(
        self,
        generation_id: int,
        *,
        invalidate_older: bool,
    ) -> None:
        """Drop pending from older generations; optionally invalidate them."""
        if invalidate_older:
            # Invalidate every generation strictly older than the new one.
            if (
                self._pending is not None
                and self._pending.generation_id < generation_id
            ):
                self._invalidated_generations.add(self._pending.generation_id)
                self._pending = None
                self.dropped_stale += 1
            if (
                self._in_flight_generation is not None
                and self._in_flight_generation < generation_id
            ):
                self._invalidated_generations.add(self._in_flight_generation)
            # Also mark all gens < generation_id if we know them via pending only;
            # for explicit older gen ids, callers may call invalidate_generation.
            for gid in list(range(0, generation_id)):
                self._invalidated_generations.add(gid)
        else:
            # normal_tool_result: drop pending older gens only (stale windows),
            # but allow in-flight older gens to still publish.
            if (
                self._pending is not None
                and self._pending.generation_id < generation_id
            ):
                self._pending = None
                self.dropped_stale += 1

    def freeze_intake(self) -> None:
        """Stop accepting *new* submits; keep existing pending for drain."""
        self._intake_frozen = True
        self._wake.set()

    async def drain_inflight(self, grace_seconds: float) -> None:
        """Start pending if any, wait for in-flight up to grace, then stop.

        Finalize must not drop a just-submitted CP3 that has not yet flipped
        ``_busy``. Pending is allowed to start exactly once after freeze.
        """
        self.freeze_intake()
        deadline = time.monotonic() + max(0.0, grace_seconds)
        self._ensure_task()
        await asyncio.sleep(0)
        # Wait until busy starts or pending is cleared or grace expires.
        while time.monotonic() < deadline:
            if self._busy:
                break
            if self._pending is None and (
                self._task is None or self._task.done()
            ):
                break
            await asyncio.sleep(0.01)
        while self._busy and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        # Drop anything not started after grace.
        self._pending = None

    async def aclose(self) -> None:
        """Cancel/await worker task safely (after freeze/drain).

        Always cancel a still-running task so fail/CAS terminals never
        observe a held limiter. The worker ``finally`` releases the slot.
        """
        self._closed = True
        self._intake_frozen = True
        self._pending = None
        self._wake.set()
        task = self._task
        if task is None:
            return
        if not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._task = None


__all__ = ["LearnerReasoningWorker", "PublishFn"]
