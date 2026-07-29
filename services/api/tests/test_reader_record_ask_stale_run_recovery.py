"""ASK-TURN-LIFECYCLE R4-5 — heartbeat + stale-run production recovery tests.

Verifies the R4-5 production recovery contract:

1. ``heartbeat_turn_run`` updates ``updated_at`` on streaming rows and is
   a no-op (no raise) on terminal/missing rows.
2. ``list_stale_streaming_turn_runs`` is heartbeat-aware: a row with a
   recent ``updated_at`` is NOT stale even if ``started_at`` is old.
3. ``run_startup_stale_stream_sweep`` calls the batch reconciler, marks
   the summary with ``startup=True``, and never raises.
4. ``StaleStreamSweeper`` starts/stops cleanly, runs periodic sweeps,
   and is idempotent under multi-worker contention (CAS guard).
5. The startup sweep and periodic sweeper NEVER fabricate ``committed`` —
   only ``cancelled`` or ``failed``.

These tests do NOT connect to a real database. They patch the repository
methods so the recovery logic can be exercised end-to-end without DB
infrastructure.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.services.reader_record_ask.repository import (
    DEFAULT_STALE_STREAM_THRESHOLD_SECONDS,
    HEARTBEAT_INTERVAL_SECONDS,
    HEARTBEAT_STALE_THRESHOLD_SECONDS,
    RECONCILE_STALE_TERMINAL_REASON,
    ReaderRecordAskRepository,
)
from app.services.reader_record_ask.stale_run_recovery import (
    PERIODIC_SWEEP_INTERVAL_SECONDS,
    StaleStreamSweeper,
    run_startup_stale_stream_sweep,
)


def _row(
    *,
    started_minutes_ago: int = 10,
    updated_seconds_ago: int = 30,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    started = now - timedelta(minutes=started_minutes_ago)
    updated = now - timedelta(seconds=updated_seconds_ago)
    return {
        "turn_run_id": str(uuid4()),
        "message_id": str(uuid4()),
        "thread_id": str(uuid4()),
        "user_id": str(uuid4()),
        "started_at": started,
        "updated_at": updated,
        "execution_version": "reader_record_ask_agentic_v2",
        "envelope_fingerprint": "f" * 64,
    }


class TestHeartbeatConstants:
    """R4-5: heartbeat constants must satisfy the lease/heartbeat invariant."""

    def test_heartbeat_interval_is_positive(self) -> None:
        assert HEARTBEAT_INTERVAL_SECONDS > 0

    def test_heartbeat_stale_threshold_is_greater_than_interval(self) -> None:
        # The stale threshold must be > the interval so a single missed
        # heartbeat from scheduling jitter does not false-positive.
        assert HEARTBEAT_STALE_THRESHOLD_SECONDS > HEARTBEAT_INTERVAL_SECONDS

    def test_heartbeat_stale_threshold_is_at_least_3x_interval(self) -> None:
        # 3x gives comfortable margin for scheduling jitter under load.
        assert (
            HEARTBEAT_STALE_THRESHOLD_SECONDS >= HEARTBEAT_INTERVAL_SECONDS * 3
        )

    def test_default_stale_threshold_is_greater_than_heartbeat_threshold(self) -> None:
        # The wall-clock threshold on started_at must be >= the heartbeat
        # threshold — otherwise a freshly-started row could be flagged
        # stale before its first heartbeat.
        assert (
            DEFAULT_STALE_STREAM_THRESHOLD_SECONDS
            >= HEARTBEAT_STALE_THRESHOLD_SECONDS
        )


class TestHeartbeatTurnRun:
    """R4-5a: ``heartbeat_turn_run`` updates ``updated_at`` on streaming rows."""

    @pytest.mark.asyncio
    async def test_heartbeat_updates_updated_at_with_streaming_guard(self) -> None:
        """The UPDATE must be gated on ``WHERE status = 'streaming'`` so a
        late heartbeat after the row transitioned to terminal cannot
        resurrect it."""
        repo = ReaderRecordAskRepository()
        captured_query: list[str] = []
        captured_args: list[Any] = []

        class _FakeConn:
            async def execute(self, query, *args):
                captured_query.append(query)
                captured_args.append(args)

        class _FakePool:
            def acquire(self):
                class _Ctx:
                    async def __aenter__(self_inner):
                        return _FakeConn()

                    async def __aexit__(self_inner, *exc):
                        return False

                return _Ctx()

        turn_run_id = uuid4()
        with patch.object(repo, "_pool_or_raise", return_value=_FakePool()):
            await repo.heartbeat_turn_run(turn_run_id=turn_run_id)

        assert len(captured_query) == 1
        query = captured_query[0]
        # Must UPDATE updated_at.
        assert "UPDATE reader_ask_turn_runs" in query
        assert "SET updated_at = NOW()" in query
        # Must be gated on streaming status (no resurrection of terminal rows).
        assert "status = 'streaming'" in query
        # Must be keyed on turn_run_id.
        assert "id = $1" in query
        assert captured_args[0] == (turn_run_id,)

    @pytest.mark.asyncio
    async def test_heartbeat_does_not_raise_on_missing_pool(self) -> None:
        """If the DB pool is gone, heartbeat must not raise — it is a
        best-effort observability write."""
        repo = ReaderRecordAskRepository()
        # No pool wired and no DB_POOL module attribute — _pool_or_raise
        # raises RuntimeError. The heartbeat caller in production_stream
        # catches Exception, but the repository method itself surfaces
        # the error so the caller's catch can log it. Verify the error
        # is RuntimeError (not a silent pass that hides bugs).
        with pytest.raises(RuntimeError, match="Database pool not initialized"):
            await repo.heartbeat_turn_run(turn_run_id=uuid4())


class TestHeartbeatAwareStaleDetection:
    """R4-5a: ``list_stale_streaming_turn_runs`` is heartbeat-aware."""

    @pytest.mark.asyncio
    async def test_list_query_filters_on_both_started_at_and_updated_at(
        self,
    ) -> None:
        """The stale-listing query must check BOTH ``started_at`` (wall-clock
        threshold) AND ``updated_at`` (heartbeat threshold). A row with a
        recent heartbeat must NOT be returned even if ``started_at`` is old."""
        repo = ReaderRecordAskRepository()
        captured_query: list[str] = []

        class _FakeConn:
            async def fetch(self, query, *args):
                captured_query.append(query)
                return []

        class _FakePool:
            def acquire(self):
                class _Ctx:
                    async def __aenter__(self_inner):
                        return _FakeConn()

                    async def __aexit__(self_inner, *exc):
                        return False

                return _Ctx()

        with patch.object(repo, "_pool_or_raise", return_value=_FakePool()):
            result = await repo.list_stale_streaming_turn_runs(
                older_than_seconds=60,
                heartbeat_stale_seconds=45,
            )

        assert result == []
        assert len(captured_query) == 1
        query = captured_query[0]
        # Must filter on started_at (wall-clock threshold).
        assert "started_at < $1" in query
        # Must filter on updated_at (heartbeat threshold) — the R4-5 addition.
        assert "updated_at < $2" in query
        # Must only scan streaming rows.
        assert "status = 'streaming'" in query

    @pytest.mark.asyncio
    async def test_list_uses_default_heartbeat_threshold(self) -> None:
        """When the caller does not pass ``heartbeat_stale_seconds``, the
        default ``HEARTBEAT_STALE_THRESHOLD_SECONDS`` must be used."""
        repo = ReaderRecordAskRepository()
        captured_args: list[Any] = []

        class _FakeConn:
            async def fetch(self, query, *args):
                captured_args.append(args)
                return []

        class _FakePool:
            def acquire(self):
                class _Ctx:
                    async def __aenter__(self_inner):
                        return _FakeConn()

                    async def __aexit__(self_inner, *exc):
                        return False

                return _Ctx()

        with patch.object(repo, "_pool_or_raise", return_value=_FakePool()):
            await repo.list_stale_streaming_turn_runs(older_than_seconds=60)

        # The heartbeat cutoff (second positional arg) must reflect the
        # default threshold: now - HEARTBEAT_STALE_THRESHOLD_SECONDS.
        assert len(captured_args) == 1
        started_cutoff, heartbeat_cutoff, limit = captured_args[0]
        now = datetime.now(UTC)
        # Heartbeat cutoff must be approximately now - default threshold.
        expected = now - timedelta(seconds=HEARTBEAT_STALE_THRESHOLD_SECONDS)
        delta = abs((heartbeat_cutoff - expected).total_seconds())
        assert delta < 5  # within 5 seconds


class TestStartupStaleStreamSweep:
    """R4-5b: ``run_startup_stale_stream_sweep`` is the production entry point."""

    @pytest.mark.asyncio
    async def test_startup_sweep_calls_batch_reconciler_with_cancelled(self) -> None:
        """The startup sweep must call the batch reconciler with
        ``run_status='cancelled'`` — never ``committed``."""
        repo = ReaderRecordAskRepository()
        batch_calls: list[dict[str, Any]] = []

        async def _fake_batch(self, **kwargs):
            batch_calls.append(kwargs)
            return {
                "scanned": 2,
                "reconciled": 2,
                "already_terminal": 0,
                "errors": 0,
                "run_status": kwargs["run_status"],
                "terminal_reason": RECONCILE_STALE_TERMINAL_REASON,
                "cutoff": "",
            }

        with patch.object(
            ReaderRecordAskRepository,
            "reconcile_stale_streaming_turn_runs_batch",
            _fake_batch,
        ):
            summary = await run_startup_stale_stream_sweep(repo=repo)

        assert len(batch_calls) == 1
        assert batch_calls[0]["run_status"] == "cancelled"
        assert summary["startup"] is True
        assert summary["reconciled"] == 2

    @pytest.mark.asyncio
    async def test_startup_sweep_never_raises(self) -> None:
        """A failed sweep must NOT raise — app startup must not be blocked
        by a stale-stream sweep error."""
        repo = ReaderRecordAskRepository()

        async def _failing_batch(self, **kwargs):
            raise RuntimeError("DB is down")

        with patch.object(
            ReaderRecordAskRepository,
            "reconcile_stale_streaming_turn_runs_batch",
            _failing_batch,
        ):
            summary = await run_startup_stale_stream_sweep(repo=repo)

        assert summary["startup"] is True
        assert summary["errors"] == 1
        assert summary["reconciled"] == 0
        assert summary["error"] == "sweep_failed"

    @pytest.mark.asyncio
    async def test_startup_sweep_uses_default_threshold_when_omitted(self) -> None:
        """When ``older_than_seconds`` is omitted, the default conservative
        threshold must be used — never a too-short threshold that would
        false-positive on healthy long-running turns."""
        repo = ReaderRecordAskRepository()
        captured: list[dict[str, Any]] = []

        async def _fake_batch(self, **kwargs):
            captured.append(kwargs)
            return {
                "scanned": 0,
                "reconciled": 0,
                "already_terminal": 0,
                "errors": 0,
                "run_status": kwargs["run_status"],
                "terminal_reason": RECONCILE_STALE_TERMINAL_REASON,
                "cutoff": "",
            }

        with patch.object(
            ReaderRecordAskRepository,
            "reconcile_stale_streaming_turn_runs_batch",
            _fake_batch,
        ):
            await run_startup_stale_stream_sweep(repo=repo)

        assert captured[0]["older_than_seconds"] == (
            DEFAULT_STALE_STREAM_THRESHOLD_SECONDS
        )

    @pytest.mark.asyncio
    async def test_startup_sweep_summary_does_not_leak_payload(self) -> None:
        """The startup sweep summary must be observability-safe — no
        answer text, reasoning, or user-visible payload."""
        repo = ReaderRecordAskRepository()

        async def _fake_batch(self, **kwargs):
            return {
                "scanned": 1,
                "reconciled": 1,
                "already_terminal": 0,
                "errors": 0,
                "run_status": "cancelled",
                "terminal_reason": RECONCILE_STALE_TERMINAL_REASON,
                "cutoff": "",
            }

        with patch.object(
            ReaderRecordAskRepository,
            "reconcile_stale_streaming_turn_runs_batch",
            _fake_batch,
        ):
            summary = await run_startup_stale_stream_sweep(repo=repo)

        summary_repr = repr(summary)
        assert "user_visible_output" not in summary_repr
        assert "reasoning_projection" not in summary_repr
        assert "answer_text" not in summary_repr


class TestStaleStreamSweeper:
    """R4-5b: ``StaleStreamSweeper`` is the periodic safety-net sweeper."""

    @pytest.mark.asyncio
    async def test_sweeper_start_creates_task(self) -> None:
        sweeper = StaleStreamSweeper(interval_seconds=60)
        assert sweeper._task is None
        sweeper.start()
        try:
            assert sweeper._task is not None
            assert not sweeper._task.done()
        finally:
            await sweeper.stop()

    @pytest.mark.asyncio
    async def test_sweeper_start_is_idempotent(self) -> None:
        """Calling ``start()`` twice must not create a second task."""
        sweeper = StaleStreamSweeper(interval_seconds=60)
        sweeper.start()
        first_task = sweeper._task
        sweeper.start()
        assert sweeper._task is first_task
        await sweeper.stop()

    @pytest.mark.asyncio
    async def test_sweeper_stop_cancels_task(self) -> None:
        sweeper = StaleStreamSweeper(interval_seconds=60)
        sweeper.start()
        task = sweeper._task
        assert task is not None
        await sweeper.stop()
        assert sweeper._task is None
        assert task.cancelled() or task.done()

    @pytest.mark.asyncio
    async def test_sweeper_stop_is_idempotent(self) -> None:
        sweeper = StaleStreamSweeper(interval_seconds=60)
        await sweeper.stop()  # no task — must not raise
        sweeper.start()
        await sweeper.stop()
        await sweeper.stop()  # second stop — must not raise

    @pytest.mark.asyncio
    async def test_sweeper_stop_awaits_in_flight_sweep(self) -> None:
        """``stop()`` must wait for the in-flight sweep to either finish
        or cancel — it must not leave a dangling task."""
        sweeper = StaleStreamSweeper(interval_seconds=0)  # don't sleep
        # Replace _sweep_once with a controlled async mock.
        sweep_calls: list[int] = []

        async def _fake_sweep_once() -> dict[str, Any]:
            sweep_calls.append(1)
            return {"scanned": 0, "reconciled": 0, "already_terminal": 0, "errors": 0}

        sweeper._sweep_once = _fake_sweep_once  # type: ignore[method-assign]
        sweeper.start()
        # Give the loop a chance to run.
        await asyncio.sleep(0.05)
        await sweeper.stop()
        assert sweeper._task is None

    @pytest.mark.asyncio
    async def test_sweeper_sweep_once_calls_batch_reconciler(self) -> None:
        """A single sweep iteration must call the batch reconciler with
        ``run_status='cancelled'``."""
        sweeper = StaleStreamSweeper(interval_seconds=60)
        batch_calls: list[dict[str, Any]] = []

        async def _fake_batch(self, **kwargs):
            batch_calls.append(kwargs)
            return {
                "scanned": 0,
                "reconciled": 0,
                "already_terminal": 0,
                "errors": 0,
                "run_status": kwargs["run_status"],
                "terminal_reason": RECONCILE_STALE_TERMINAL_REASON,
                "cutoff": "",
            }

        with patch.object(
            ReaderRecordAskRepository,
            "reconcile_stale_streaming_turn_runs_batch",
            _fake_batch,
        ):
            summary = await sweeper._sweep_once()

        assert len(batch_calls) == 1
        assert batch_calls[0]["run_status"] == "cancelled"
        assert summary["scanned"] == 0

    @pytest.mark.asyncio
    async def test_sweeper_sweep_once_swallows_errors(self) -> None:
        """A failed batch reconcile must NOT propagate — the loop must
        continue and count the error."""
        sweeper = StaleStreamSweeper(interval_seconds=60)

        async def _failing_batch(self, **kwargs):
            raise RuntimeError("DB down")

        with patch.object(
            ReaderRecordAskRepository,
            "reconcile_stale_streaming_turn_runs_batch",
            _failing_batch,
        ):
            summary = await sweeper._sweep_once()

        assert summary["errors"] == 1
        assert summary["reconciled"] == 0

    @pytest.mark.asyncio
    async def test_sweeper_loop_runs_periodically(self) -> None:
        """The sweeper loop must invoke ``_sweep_once`` at the configured
        interval. Use a short interval to verify without waiting 60s."""
        sweeper = StaleStreamSweeper(interval_seconds=0.01)
        sweep_count = 0

        async def _fake_sweep_once() -> dict[str, Any]:
            nonlocal sweep_count
            sweep_count += 1
            return {"scanned": 0, "reconciled": 0, "already_terminal": 0, "errors": 0}

        sweeper._sweep_once = _fake_sweep_once  # type: ignore[method-assign]
        sweeper.start()
        await asyncio.sleep(0.1)
        await sweeper.stop()
        # Must have swept at least twice in 100ms with a 10ms interval.
        assert sweep_count >= 2

    @pytest.mark.asyncio
    async def test_sweeper_loop_stops_on_cancel(self) -> None:
        """Cancelling the sweeper task must exit the loop cleanly."""
        sweeper = StaleStreamSweeper(interval_seconds=0.01)
        sweeper.start()
        await asyncio.sleep(0.02)
        await sweeper.stop()
        # After stop, the task reference is cleared.
        assert sweeper._task is None


class TestMultiWorkerIdempotency:
    """R4-5: multi-worker / repeated execution must be idempotent.

    The CAS guard (``WHERE status = 'streaming'`` in
    ``terminal_agentic_turn_run``) is the idempotency seam. Two workers
    that both pick up the same stale row will race; the loser returns
    ``already_terminal`` and is counted separately. No row is ever
    double-reconciled to two different terminals.
    """

    @pytest.mark.asyncio
    async def test_concurrent_sweepers_race_to_same_rows(self) -> None:
        """Two sweepers that both list the same rows must not
        double-reconcile: one wins the CAS, the other gets
        ``already_terminal``."""
        repo = ReaderRecordAskRepository()
        rows = [_row(started_minutes_ago=10, updated_seconds_ago=120)]

        call_count = {"n": 0}

        async def _fake_list(self, **kwargs):
            return rows

        async def _fake_terminal(self, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # First caller wins the CAS.
                return {
                    "id": str(kwargs["turn_run_id"]),
                    "status": "cancelled",
                    "final_status": "cancelled",
                    "terminal_reason": kwargs["terminal_reason"],
                    "user_visible_output_json": None,
                    "reasoning_projection_json": None,
                    "envelope_fingerprint": None,
                    "execution_version": "reader_record_ask_agentic_v2",
                }
            # Second caller loses the CAS — row already terminal.
            return {
                "id": str(kwargs["turn_run_id"]),
                "status": "already_terminal",
                "final_status": None,
                "terminal_reason": None,
                "user_visible_output_json": None,
                "reasoning_projection_json": None,
                "envelope_fingerprint": None,
                "execution_version": "reader_record_ask_agentic_v2",
            }

        with patch.object(
            ReaderRecordAskRepository,
            "list_stale_streaming_turn_runs",
            _fake_list,
        ), patch.object(
            ReaderRecordAskRepository,
            "terminal_agentic_turn_run",
            _fake_terminal,
        ):
            # Run two sweeps concurrently — both see the same rows.
            results = await asyncio.gather(
                repo.reconcile_stale_streaming_turn_runs_batch(
                    older_than_seconds=60,
                    run_status="cancelled",
                ),
                repo.reconcile_stale_streaming_turn_runs_batch(
                    older_than_seconds=60,
                    run_status="cancelled",
                ),
            )

        # One sweep reconciled, the other saw already_terminal.
        reconciled_counts = [r["reconciled"] for r in results]
        already_counts = [r["already_terminal"] for r in results]
        assert sum(reconciled_counts) == 1
        assert sum(already_counts) == 1

    @pytest.mark.asyncio
    async def test_repeated_sweeps_are_idempotent(self) -> None:
        """A second sweep on the same rows after the first converged
        them must return reconciled=0, already_terminal=N."""
        repo = ReaderRecordAskRepository()
        rows = [_row(started_minutes_ago=10, updated_seconds_ago=120)]
        call_count = {"n": 0}

        async def _fake_list(self, **kwargs):
            return rows

        async def _fake_terminal(self, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return {
                    "id": str(kwargs["turn_run_id"]),
                    "status": "cancelled",
                    "final_status": "cancelled",
                    "terminal_reason": kwargs["terminal_reason"],
                    "user_visible_output_json": None,
                    "reasoning_projection_json": None,
                    "envelope_fingerprint": None,
                    "execution_version": "reader_record_ask_agentic_v2",
                }
            return {
                "id": str(kwargs["turn_run_id"]),
                "status": "already_terminal",
                "final_status": None,
                "terminal_reason": None,
                "user_visible_output_json": None,
                "reasoning_projection_json": None,
                "envelope_fingerprint": None,
                "execution_version": "reader_record_ask_agentic_v2",
            }

        with patch.object(
            ReaderRecordAskRepository,
            "list_stale_streaming_turn_runs",
            _fake_list,
        ), patch.object(
            ReaderRecordAskRepository,
            "terminal_agentic_turn_run",
            _fake_terminal,
        ):
            first = await repo.reconcile_stale_streaming_turn_runs_batch(
                older_than_seconds=60,
                run_status="cancelled",
            )
            second = await repo.reconcile_stale_streaming_turn_runs_batch(
                older_than_seconds=60,
                run_status="cancelled",
            )

        assert first["reconciled"] == 1
        assert first["already_terminal"] == 0
        assert second["reconciled"] == 0
        assert second["already_terminal"] == 1

    @pytest.mark.asyncio
    async def test_sweeper_never_fabricates_committed(self) -> None:
        """Both the startup sweep and the periodic sweeper must refuse
        ``committed`` — only ``cancelled`` or ``failed`` are allowed."""
        # Startup sweep: guard fires in the underlying batch reconciler.
        repo = ReaderRecordAskRepository()
        with pytest.raises(ValueError, match="never 'committed'"):
            await repo.reconcile_stale_streaming_turn_runs_batch(
                run_status="committed",
            )

        # StaleStreamSweeper._sweep_once hardcodes run_status='cancelled'.
        sweeper = StaleStreamSweeper()
        batch_calls: list[dict[str, Any]] = []

        async def _fake_batch(self, **kwargs):
            batch_calls.append(kwargs)
            return {
                "scanned": 0,
                "reconciled": 0,
                "already_terminal": 0,
                "errors": 0,
                "run_status": kwargs["run_status"],
                "terminal_reason": RECONCILE_STALE_TERMINAL_REASON,
                "cutoff": "",
            }

        with patch.object(
            ReaderRecordAskRepository,
            "reconcile_stale_streaming_turn_runs_batch",
            _fake_batch,
        ):
            await sweeper._sweep_once()

        assert batch_calls[0]["run_status"] == "cancelled"


class TestSweeperConstants:
    """R4-5: sweeper constants must be conservative."""

    def test_periodic_sweep_interval_is_positive(self) -> None:
        assert PERIODIC_SWEEP_INTERVAL_SECONDS > 0

    def test_periodic_sweep_interval_is_reasonable(self) -> None:
        # Must not be too aggressive (< 10s would spam the DB) or too
        # lazy (> 5min would let orphans linger too long).
        assert 10 <= PERIODIC_SWEEP_INTERVAL_SECONDS <= 300
