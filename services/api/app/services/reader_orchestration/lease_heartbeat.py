"""Shared lease-heartbeat management for reader orchestration workers.

Long-running model phases (grammar batch generate + publish, grammar
window LLM call) must hold their job lease for the whole processing
duration. Before each worker carried its own renewal loop — and
the grammar batch path had none at all: a ``generate_batch`` call
longer than the lease let ``recover_stale_leases`` requeue the job
(``heartbeat_lost``), after which the worker's publish/transition
failed and the completed model call's usage was never recorded.

``LeaseHeartbeat`` is the single shared implementation used by both
workers (the grammar window worker's ``_heartbeat_loop`` delegates to
:func:`LeaseHeartbeat.run_forever` so the renewal loop exists exactly
once):

- ``start()`` spawns one background task that renews the lease every
  ``heartbeat_interval`` (default ``lease_duration / 4`` — strictly
  shorter than the lease and derived from the lease configuration,
  never a lease extension that papers over missing renewals).
- A renewal failure (lease expired, token mismatch, or job no longer
  ``claimed`` → ``IllegalTransitionError`` / ``FenceViolationError`` /
  DB error) terminates the loop and records the exception: it is
  logged at WARNING and exposed via ``lost`` / ``error`` /
  ``assert_ownership``. It is never silently swallowed, and it does
  not crash the main task — the caller MUST check ``lost`` before
  publishing. The publisher's in-transaction claim/fence validation
  remains the authoritative ownership check; the heartbeat never
  replaces it.
- ``stop()`` cancels the task and awaits its termination on every exit
  path (success, exception, external cancellation) and is idempotent —
  no residual background tasks.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class HeartbeatRuntime(Protocol):
    """Minimal job-runtime contract needed by :class:`LeaseHeartbeat`."""

    async def heartbeat(
        self,
        *,
        job_id: UUID,
        lease_token: UUID,
        lease_duration: timedelta,
    ) -> Any: ...


class LeaseHeartbeat:
    """One background lease-renewal task for a claimed job attempt."""

    def __init__(
        self,
        *,
        job_runtime: HeartbeatRuntime,
        job_id: UUID,
        lease_token: UUID,
        lease_duration: timedelta,
        heartbeat_interval: timedelta | None = None,
    ) -> None:
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")
        if heartbeat_interval is not None:
            interval = heartbeat_interval
        else:
            interval = max(lease_duration / 4, timedelta(milliseconds=50))
        if interval >= lease_duration:
            # The claimed lease can legitimately be shorter than the
            # configured interval (fast-lease tests, tuned pipelines).
            # Clamp to half the lease so renewals stay strictly more
            # frequent than expiry — the fix is more frequent
            # renewals, never a longer lease.
            interval = lease_duration / 2
        if interval <= timedelta(0):
            raise ValueError("heartbeat_interval must be positive")
        self._job_runtime = job_runtime
        self._job_id = job_id
        self._lease_token = lease_token
        self._lease_duration = lease_duration
        self._heartbeat_interval = interval
        self._task: asyncio.Task[None] | None = None
        self._error: BaseException | None = None
        self._stopped = False

    @property
    def interval(self) -> timedelta:
        return self._heartbeat_interval

    @property
    def lost(self) -> bool:
        """True once a lease renewal failed (ownership invalid)."""
        return self._error is not None

    @property
    def error(self) -> BaseException | None:
        """The captured renewal failure, if any (already logged)."""
        return self._error

    def assert_ownership(self) -> None:
        """Raise the captured renewal failure if ownership was lost.

        Call AFTER the model call returns and BEFORE publishing so a
        lease lost during generation aborts the attempt without
        publishing.
        """
        if self._error is not None:
            raise self._error

    async def verify_ownership(self) -> None:
        """Actively probe ownership RIGHT NOW.

        Raises the captured error if the renewal loop already failed;
        otherwise performs an immediate renewal, which raises when the
        lease/token/status is invalid — e.g. the lease expired between
        renewals, or renewals stalled/were neutered. Call after the
        model returns and before publishing: it closes the gap
        between the last background renewal and the publish fence.
        (The publisher's in-transaction fence remains the final
        authoritative check.)
        """
        if self._error is not None:
            raise self._error
        await self._job_runtime.heartbeat(
            job_id=self._job_id,
            lease_token=self._lease_token,
            lease_duration=self._lease_duration,
        )

    async def run_forever(self) -> None:
        """The raw renewal loop: sleep(interval) → heartbeat, forever.

        Runs until cancelled (clean shutdown via :meth:`stop`). A
        renewal failure is recorded on the owning ``LeaseHeartbeat``
        instance semantics — when run via ``start()`` the failure is
        captured on THIS instance — logged at WARNING, and terminates
        the loop; it is never silently swallowed.
        """
        try:
            while True:
                await asyncio.sleep(self._heartbeat_interval.total_seconds())
                await self._job_runtime.heartbeat(
                    job_id=self._job_id,
                    lease_token=self._lease_token,
                    lease_duration=self._lease_duration,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - record ANY renewal failure
            # Ordinary renewal failures (IllegalTransitionError /
            # FenceViolationError / DB errors) are recorded + logged
            # and terminate the loop. BaseException (KeyboardInterrupt,
            # SystemExit) is deliberately NOT caught here — it must
            # propagate.
            self._error = exc
            logger.warning(
                "lease heartbeat failed for job %s; ownership is invalid "
                "and this attempt must not publish: %s",
                self._job_id,
                exc,
            )

    async def start(self) -> None:
        """Spawn the renewal task (idempotent)."""
        if self._task is not None:
            return
        self._stopped = False
        self._error = None
        self._task = asyncio.create_task(
            self.run_forever(),
            name=f"lease-heartbeat-{self._job_id}",
        )

    async def stop(self) -> None:
        """Cancel the renewal task and await its termination.

        Idempotent; safe on every exit path (success / exception /
        external cancellation). Does NOT raise: a renewal failure is
        already logged by the loop and remains visible via ``lost`` /
        ``error`` / ``assert_ownership`` — the caller decides the
        outcome semantics (e.g. the publisher fence is authoritative).
        """
        task = self._task
        if task is None:
            return
        self._stopped = True
        self._task = None
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001 - already recorded + logged
            pass

    async def __aenter__(self) -> LeaseHeartbeat:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: Any,
    ) -> bool:
        await self.stop()
        if exc is None and self._error is not None:
            # Clean body but lost lease: surface the failure instead of
            # pretending the phase succeeded. When the body already
            # raised, its error takes precedence (ours was logged).
            raise self._error
        return False
