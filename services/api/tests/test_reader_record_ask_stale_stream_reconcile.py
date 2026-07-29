"""ASK-TURN-LIFECYCLE R1 — stale-stream batch reconciliation tests.

Verifies the safety-net reconciliation contract:

1. ``list_stale_streaming_turn_runs`` uses a wall-clock threshold on
   ``started_at`` and never touches the DB state.
2. ``reconcile_stale_streaming_turn_runs_batch`` refuses to fabricate
   ``committed`` (run_status guard).
3. The typed ``stale_stream_reconciled`` terminal reason is recorded on
   every reconciled row.
4. Idempotent: a row already transitioned to terminal returns
   ``already_terminal`` and is counted separately.
5. Default threshold is conservative (>= 60s) so healthy turns are
   never misclassified as stale.
6. The summary payload is typed (no row content / no answer text / no
   reasoning leak) — observability-safe.

These tests do NOT connect to a real database. They patch the
``list_stale_streaming_turn_runs`` and
``terminal_agentic_turn_run`` methods on
``ReaderRecordAskRepository`` so the batch reconciler's logic can be
exercised end-to-end without DB infrastructure.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.services.reader_record_ask.repository import (
    DEFAULT_STALE_STREAM_THRESHOLD_SECONDS,
    RECONCILE_STALE_TERMINAL_REASON,
    ReaderRecordAskRepository,
)


def _row(
    *,
    started_minutes_ago: int = 10,
) -> dict[str, Any]:
    started = datetime.now(UTC) - timedelta(minutes=started_minutes_ago)
    return {
        "turn_run_id": str(uuid4()),
        "message_id": str(uuid4()),
        "thread_id": str(uuid4()),
        "user_id": str(uuid4()),
        "started_at": started,
        "updated_at": started,
        "execution_version": "reader_record_ask_agentic_v2",
        "envelope_fingerprint": "f" * 64,
    }


class TestStaleStreamReconciliationContract:
    """R1 contract: stale-stream batch reconciliation safety guarantees."""

    def test_default_threshold_is_conservative(self) -> None:
        """The default threshold must be >= 60s so no healthy turn is
        misclassified as stale. The audit observed DeepSeek V4 Pro p99
        around 95s, so 300s (5min) gives ample headroom."""
        assert DEFAULT_STALE_STREAM_THRESHOLD_SECONDS >= 60

    def test_terminal_reason_is_typed_constant(self) -> None:
        assert RECONCILE_STALE_TERMINAL_REASON == "stale_stream_reconciled"

    def test_batch_reconcile_refuses_committed(self) -> None:
        """The batch reconciler must NEVER fabricate a committed row —
        run_status must be 'cancelled' or 'failed' only."""
        repo = ReaderRecordAskRepository()
        with pytest.raises(ValueError, match="never 'committed'"):
            # The guard fires before any DB access, so no pool is needed.
            import asyncio

            asyncio.get_event_loop().run_until_complete(
                repo.reconcile_stale_streaming_turn_runs_batch(
                    run_status="committed",
                )
            )

    def test_single_reconcile_refuses_committed(self) -> None:
        """The single-row reconciler must also refuse 'committed'."""
        repo = ReaderRecordAskRepository()
        with pytest.raises(ValueError, match="never 'committed'"):
            import asyncio

            asyncio.get_event_loop().run_until_complete(
                repo.reconcile_stale_streaming_turn_run(
                    turn_run_id=uuid4(),
                    message_id=uuid4(),
                    run_status="completed",
                )
            )

    @pytest.mark.asyncio
    async def test_batch_reconcile_uses_typed_terminal_reason(self) -> None:
        """Every reconciled row must carry the typed
        ``stale_stream_reconciled`` terminal_reason — observers rely on
        this to distinguish reconciliation from a real provider terminal."""
        repo = ReaderRecordAskRepository()
        rows = [_row(started_minutes_ago=10), _row(started_minutes_ago=20)]

        terminal_calls: list[dict[str, Any]] = []

        async def _fake_list(self, **kwargs):
            return rows

        async def _fake_terminal(self, **kwargs):
            terminal_calls.append(kwargs)
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

        with patch.object(
            ReaderRecordAskRepository,
            "list_stale_streaming_turn_runs",
            _fake_list,
        ), patch.object(
            ReaderRecordAskRepository,
            "terminal_agentic_turn_run",
            _fake_terminal,
        ):
            summary = await repo.reconcile_stale_streaming_turn_runs_batch(
                older_than_seconds=60,
                run_status="cancelled",
            )

        assert summary["scanned"] == 2
        assert summary["reconciled"] == 2
        assert summary["already_terminal"] == 0
        assert summary["errors"] == 0
        assert summary["run_status"] == "cancelled"
        assert summary["terminal_reason"] == RECONCILE_STALE_TERMINAL_REASON
        # Every call must carry the typed terminal_reason.
        for call in terminal_calls:
            assert call["terminal_reason"] == RECONCILE_STALE_TERMINAL_REASON
            assert call["run_status"] == "cancelled"
            assert call["final_status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_batch_reconcile_counts_already_terminal(self) -> None:
        """Rows that already transitioned (concurrent reconciler / in-process
        lifecycle hook fired) must return ``already_terminal`` and be counted
        separately, NOT as reconciled."""
        repo = ReaderRecordAskRepository()
        rows = [_row(started_minutes_ago=10), _row(started_minutes_ago=20)]

        async def _fake_list(self, **kwargs):
            return rows

        async def _fake_terminal(self, **kwargs):
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
            summary = await repo.reconcile_stale_streaming_turn_runs_batch(
                older_than_seconds=60,
            )

        assert summary["scanned"] == 2
        assert summary["reconciled"] == 0
        assert summary["already_terminal"] == 2
        assert summary["errors"] == 0

    @pytest.mark.asyncio
    async def test_batch_reconcile_isolates_per_row_errors(self) -> None:
        """A single bad row must NOT abort the whole batch — each row is
        reconciled in its own transaction so the bad row is counted as
        an error and the batch continues."""
        repo = ReaderRecordAskRepository()
        rows = [_row(started_minutes_ago=10), _row(started_minutes_ago=20)]

        call_count = {"n": 0}

        async def _fake_list(self, **kwargs):
            return rows

        async def _fake_terminal(self, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("simulated per-row DB failure")
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

        with patch.object(
            ReaderRecordAskRepository,
            "list_stale_streaming_turn_runs",
            _fake_list,
        ), patch.object(
            ReaderRecordAskRepository,
            "terminal_agentic_turn_run",
            _fake_terminal,
        ):
            summary = await repo.reconcile_stale_streaming_turn_runs_batch(
                older_than_seconds=60,
            )

        assert summary["scanned"] == 2
        assert summary["reconciled"] == 1
        assert summary["errors"] == 1
        assert summary["already_terminal"] == 0

    @pytest.mark.asyncio
    async def test_batch_summary_does_not_leak_answer_or_reasoning(self) -> None:
        """The summary payload is observability-safe — it must NOT echo
        answer text, reasoning, citations, or any user-visible payload
        from the reconciled rows. Only counts + typed identifiers."""
        repo = ReaderRecordAskRepository()
        rows = [_row(started_minutes_ago=10)]

        async def _fake_list(self, **kwargs):
            return rows

        async def _fake_terminal(self, **kwargs):
            return {
                "id": str(kwargs["turn_run_id"]),
                "status": "cancelled",
                "final_status": "cancelled",
                "terminal_reason": kwargs["terminal_reason"],
                # Even if the underlying write returned user content, the
                # summary must not propagate it.
                "user_visible_output_json": {"answer_text": "secret answer"},
                "reasoning_projection_json": {"reasoning_text": "secret reasoning"},
                "envelope_fingerprint": "f" * 64,
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
            summary = await repo.reconcile_stale_streaming_turn_runs_batch(
                older_than_seconds=60,
            )

        # The summary must contain only typed counts + identifiers.
        summary_json = repr(summary)
        assert "secret answer" not in summary_json
        assert "secret reasoning" not in summary_json
        assert "user_visible_output_json" not in summary
        assert "reasoning_projection_json" not in summary
        # Typed fields that ARE allowed in the summary.
        for key in (
            "scanned",
            "reconciled",
            "already_terminal",
            "errors",
            "run_status",
            "terminal_reason",
            "cutoff",
        ):
            assert key in summary, f"missing typed key: {key}"

    @pytest.mark.asyncio
    async def test_list_does_not_mutate_database(self) -> None:
        """``list_stale_streaming_turn_runs`` is a read-only scan — it
        must NOT issue any UPDATE / DELETE / INSERT. We verify this by
        patching the connection's ``execute`` to fail if called."""
        repo = ReaderRecordAskRepository()

        # Build a fake pool + connection that records all calls.
        execute_calls: list[str] = []

        class _FakeConn:
            async def fetch(self, query, *args):
                # Verify the query is a SELECT, not a mutation.
                assert query.strip().upper().startswith("SELECT"), (
                    f"list_stale_streaming_turn_runs must be read-only; "
                    f"got: {query[:80]}"
                )
                return []

            async def execute(self, query, *args):
                execute_calls.append(query)
                raise AssertionError(
                    f"list_stale_streaming_turn_runs must not execute "
                    f"mutations; got: {query[:80]}"
                )

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
            )

        assert result == []
        assert execute_calls == []
