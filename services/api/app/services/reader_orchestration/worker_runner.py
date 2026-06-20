"""Internal translation worker runner for D4.

Drives translation ticks on top of :class:`ReaderOrchestrator` without
exposing any public HTTP endpoint and without starting a background
process. The runner is a callable service intended for tests, internal
admin tooling, or future internal routes that carry their own permission
guards.

D4 auth/permission boundary
---------------------------
No public HTTP endpoint is added in D4:

- Ticks are explicit and driven by tests or internal service calls.
- D4 does not run a background worker process; the PostgreSQL
  run/job/event tables remain the durable control plane.
- An internal admin route would require auth infrastructure (service
  accounts, rate limiting, audit logging) that is out of scope for D4.
- When a future internal route is needed, it MUST be permission-guarded
  (service-account or admin role) and MUST NOT accept unauthenticated
  public traffic. The runner itself stays transport-agnostic so the
  same callable can be reused behind any protected route.

The runner itself does not choose models or provider behavior; the
injected orchestrator/worker owns translation execution. The runner
never introduces LangGraph, MQ, Temporal, or SSE.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

from app.services.reader_orchestration.job_runtime import FenceViolationError
from app.services.reader_orchestration.orchestrator import (
    ReaderOrchestrator,
    TranslationTickResult,
)
from app.services.reader_orchestration.translation_worker import (
    DEFAULT_TRANSLATION_RETRY_DELAY,
)

WorkerTickStatus = Literal[
    "no_job",
    "succeeded",
    "retry_later",
    "failed_terminal",
    "fence_rejected",
]

DEFAULT_DRAIN_MAX_TICKS = 10


@dataclass(frozen=True, slots=True)
class WorkerTickOutcome:
    """Classified outcome of a single runner tick.

    ``status`` maps the underlying worker result (or fence rejection)
    to a stable enum-like string so callers do not need to inspect the
    raw ``TranslationTickResult``.
    """

    status: WorkerTickStatus
    tick_result: TranslationTickResult | None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class WorkerDrainResult:
    """Aggregate outcome of a drain run.

    ``ticks`` is ordered chronologically. ``stopped_reason`` explains
    why the drain loop ended.
    """

    ticks: tuple[WorkerTickOutcome, ...]
    total_processed: int
    total_succeeded: int
    total_retry_later: int
    total_failed_terminal: int
    total_fence_rejected: int
    stopped_reason: Literal["no_job", "max_ticks_reached"]


class TranslationWorkerRunner:
    """Internal callable runner that drives translation ticks.

    The runner is transport-agnostic: it can be called from tests,
    internal services, or a future permission-guarded admin route. It
    never starts a background process and never exposes a public
    endpoint.
    """

    def __init__(self, orchestrator: ReaderOrchestrator) -> None:
        self._orchestrator = orchestrator

    async def run_single_tick(
        self,
        *,
        lease_owner: str,
        lease_duration: timedelta,
        retry_delay: timedelta = DEFAULT_TRANSLATION_RETRY_DELAY,
    ) -> WorkerTickOutcome:
        """Run exactly one translation tick and classify the outcome.

        Fence rejections raised by the publisher are caught and mapped
        to ``status="fence_rejected"`` so callers can drain remaining
        jobs without propagating exceptions. All other worker statuses
        are mapped from the underlying ``TranslationTickResult``.
        """
        try:
            tick_result = await self._orchestrator.tick_translation_worker(
                lease_owner=lease_owner,
                lease_duration=lease_duration,
                retry_delay=retry_delay,
            )
        except FenceViolationError:
            return WorkerTickOutcome(
                status="fence_rejected",
                tick_result=None,
                error_code="publish_fence_failed",
            )

        return self._classify_tick_result(tick_result)

    async def run_drain(
        self,
        *,
        lease_owner: str,
        lease_duration: timedelta,
        retry_delay: timedelta = DEFAULT_TRANSLATION_RETRY_DELAY,
        max_ticks: int = DEFAULT_DRAIN_MAX_TICKS,
    ) -> WorkerDrainResult:
        """Run ticks until no job is available or ``max_ticks`` is hit.

        The drain stops on the first ``no_job`` outcome. Retryable and
        terminal failures do not stop the drain because other queued
        jobs may still be processable. Fence rejections likewise do not
        stop the drain.
        """
        if max_ticks < 1:
            raise ValueError("max_ticks must be >= 1")

        outcomes: list[WorkerTickOutcome] = []
        succeeded = 0
        retry_later = 0
        failed_terminal = 0
        fence_rejected = 0
        stopped_reason: Literal["no_job", "max_ticks_reached"] = "max_ticks_reached"

        for _ in range(max_ticks):
            outcome = await self.run_single_tick(
                lease_owner=lease_owner,
                lease_duration=lease_duration,
                retry_delay=retry_delay,
            )

            if outcome.status == "no_job":
                stopped_reason = "no_job"
                break

            outcomes.append(outcome)

            if outcome.status == "succeeded":
                succeeded += 1
            elif outcome.status == "retry_later":
                retry_later += 1
            elif outcome.status == "failed_terminal":
                failed_terminal += 1
            elif outcome.status == "fence_rejected":
                fence_rejected += 1

        return WorkerDrainResult(
            ticks=tuple(outcomes),
            total_processed=len(outcomes),
            total_succeeded=succeeded,
            total_retry_later=retry_later,
            total_failed_terminal=failed_terminal,
            total_fence_rejected=fence_rejected,
            stopped_reason=stopped_reason,
        )

    @staticmethod
    def _classify_tick_result(
        tick_result: TranslationTickResult,
    ) -> WorkerTickOutcome:
        worker_result = tick_result.worker_result
        if worker_result is None:
            return WorkerTickOutcome(
                status="no_job",
                tick_result=tick_result,
            )

        if worker_result.status == "succeeded":
            return WorkerTickOutcome(
                status="succeeded",
                tick_result=tick_result,
            )

        if worker_result.status == "retry_later":
            return WorkerTickOutcome(
                status="retry_later",
                tick_result=tick_result,
                error_code="translation_retryable_failure",
            )

        if worker_result.status == "failed_terminal":
            return WorkerTickOutcome(
                status="failed_terminal",
                tick_result=tick_result,
                error_code="translation_terminal_failure",
            )

        return WorkerTickOutcome(
            status="failed_terminal",
            tick_result=tick_result,
            error_code=f"unknown_worker_status:{worker_result.status}",
        )
