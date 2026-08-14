"""ASK-TURN-LIFECYCLE — Production stale-stream recovery.

Provides the real production entry point for orphan streaming
``reader_ask_turn_runs`` rows left behind by process crash / restart.

Design
------

**Owner/heartbeat proof** (no schema change needed):

1. **Heartbeat** (``heartbeat_turn_run`` in repository): during active
   streaming, the production generator updates ``updated_at`` on the
   turn_run row every ``HEARTBEAT_INTERVAL_SECONDS`` (15s). This proves
   the owner process is alive.

2. **Heartbeat-aware stale detection** (``list_stale_streaming_turn_runs``
   in repository): a row is stale ONLY if BOTH ``started_at`` is old
   enough AND ``updated_at`` has no recent heartbeat. A long-running
   turn with recent heartbeats is NOT stale.

3. **Startup sweep** (``run_startup_stale_stream_sweep``): called once
   on app startup. At startup, ALL streaming rows are orphans — the
   previous process is dead and no heartbeats are being written. The
   heartbeat check catches them because their ``updated_at`` is stale.

4. **Periodic safety-net sweeper** (``StaleStreamSweeper``): a
   lightweight background task that runs every 60s and reconciles
   heartbeat-dead rows. This catches orphans during normal operation
   (e.g., a generator that crashed without triggering the route
   ``finally`` block). It does NOT touch rows with recent heartbeats.

**Route ``finally``** (``_StreamLifecycleContext`` in the route):
continues to handle per-request abort/disconnect — the lease release.
This module is the safety-net for rows whose lease-holder process is
gone entirely.

**Convergence**: stale recovery ONLY converges to ``cancelled`` or
``failed`` — NEVER to ``committed`` (no fabricated success). The
existing ``reconcile_stale_streaming_turn_runs_batch`` guard enforces
this.

**Multi-worker idempotency**: the CAS guard
(``WHERE status = 'streaming'`` in ``terminal_agentic_turn_run``) makes
every reconciliation idempotent. If worker A's sweeper reconciles a row
that worker B's sweeper also picked up, the second call returns
``already_terminal`` and is counted separately. No row is ever
double-reconciled.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.services.reader_record_ask.repository import (
    DEFAULT_STALE_STREAM_THRESHOLD_SECONDS,
    ReaderRecordAskRepository,
)

logger = logging.getLogger(__name__)

# Interval for the periodic safety-net sweeper. Conservative —
# the startup sweep is the primary recovery path; this is only for
# in-process orphans that slipped through the route ``finally``.
PERIODIC_SWEEP_INTERVAL_SECONDS: int = 60


async def run_startup_stale_stream_sweep(
    *,
    repo: ReaderRecordAskRepository | None = None,
    older_than_seconds: int = DEFAULT_STALE_STREAM_THRESHOLD_SECONDS,
) -> dict[str, Any]:
    """Reconcile all stale streaming rows on app startup.

    ASK-TURN-LIFECYCLE the primary production entry point for
    orphan streaming rows. Called once from the FastAPI ``lifespan``
    handler after the DB pool is initialized. At startup, ALL streaming
    rows are orphans — the previous process is dead and no heartbeats
    are being written. The heartbeat-aware
    ``list_stale_streaming_turn_runs`` catches them because their
    ``updated_at`` is stale (no heartbeat since the crash).

    Safe to call when no streaming rows exist — returns a zero-summary.

    Never raises: a failed sweep must NOT prevent app startup. Errors
    are logged and reflected in the ``errors`` count.
    """
    repository = repo or ReaderRecordAskRepository()
    try:
        summary = await repository.reconcile_stale_streaming_turn_runs_batch(
            older_than_seconds=older_than_seconds,
            run_status="cancelled",
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "startup_stale_stream_sweep failed; orphan rows may linger "
            "until the next periodic sweep"
        )
        return {
            "scanned": 0,
            "reconciled": 0,
            "already_terminal": 0,
            "errors": 1,
            "run_status": "cancelled",
            "terminal_reason": "stale_stream_reconciled",
            "cutoff": "",
            "startup": True,
            "error": "sweep_failed",
        }
    logger.info(
        "startup_stale_stream_sweep: scanned=%d reconciled=%d "
        "already_terminal=%d errors=%d",
        summary["scanned"],
        summary["reconciled"],
        summary["already_terminal"],
        summary["errors"],
    )
    return {**summary, "startup": True}


class StaleStreamSweeper:
    """Periodic background sweeper for in-process orphan streaming rows.

    ASK-TURN-LIFECYCLE a lightweight background task that runs
    every ``PERIODIC_SWEEP_INTERVAL_SECONDS`` and reconciles
    heartbeat-dead streaming rows. This catches orphans that slipped
    through the route ``finally`` block (e.g., ASGI cancellation without
    generator close, or a generator crash that bypassed ``finally``).

    The sweeper does NOT touch rows with recent heartbeats — those are
    provably still alive. It only reconciles rows where both
    ``started_at`` is old enough AND ``updated_at`` has no recent
    heartbeat.

    The sweeper is idempotent and multi-worker safe: the CAS guard in
    ``terminal_agentic_turn_run`` ensures no row is double-reconciled.
    """

    def __init__(
        self,
        *,
        interval_seconds: int = PERIODIC_SWEEP_INTERVAL_SECONDS,
        older_than_seconds: int = DEFAULT_STALE_STREAM_THRESHOLD_SECONDS,
        repo: ReaderRecordAskRepository | None = None,
    ) -> None:
        self._interval = interval_seconds
        self._older_than = older_than_seconds
        self._repo = repo or ReaderRecordAskRepository()
        self._task: asyncio.Task[None] | None = None
        self._stopped = False

    def start(self) -> None:
        """Start the periodic sweep background task."""
        if self._task is not None:
            return
        self._stopped = False
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        """Stop the periodic sweep background task."""
        self._stopped = True
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def _run_loop(self) -> None:
        """Run the periodic sweep loop until stopped."""
        while not self._stopped:
            try:
                await asyncio.sleep(self._interval)
                if self._stopped:
                    break
                await self._sweep_once()
            except asyncio.CancelledError:
                break
            except Exception:  # noqa: BLE001
                logger.exception(
                    "stale_stream_sweeper: sweep iteration failed; "
                    "will retry next interval"
                )

    async def _sweep_once(self) -> dict[str, Any]:
        """Run one sweep iteration."""
        try:
            summary = await self._repo.reconcile_stale_streaming_turn_runs_batch(
                older_than_seconds=self._older_than,
                run_status="cancelled",
            )
        except Exception:  # noqa: BLE001
            logger.exception("stale_stream_sweeper: batch reconcile failed")
            return {
                "scanned": 0,
                "reconciled": 0,
                "already_terminal": 0,
                "errors": 1,
            }
        if summary["reconciled"] > 0 or summary["errors"] > 0:
            logger.info(
                "stale_stream_sweeper: scanned=%d reconciled=%d "
                "already_terminal=%d errors=%d",
                summary["scanned"],
                summary["reconciled"],
                summary["already_terminal"],
                summary["errors"],
            )
        return summary


__all__ = [
    "PERIODIC_SWEEP_INTERVAL_SECONDS",
    "StaleStreamSweeper",
    "run_startup_stale_stream_sweep",
]
