"""Persistence seam for agentic Reading Record Ask turns.

Owns SQL against ``reader_ask_*`` tables for the agentic lane only.
Does not import ``app.services.reader_ask.repository``.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from app.database import connection as db_connection
from app.database.json_compat import jsonb_param
from app.schemas.reader_record_ask_stream import EXECUTION_VERSION_AGENTIC_V2

logger = logging.getLogger(__name__)

# ASK-TURN-LIFECYCLE R1: typed terminal reason for stale-stream
# reconciliation. Used when the host detects a streaming run/message
# whose owner has gone away (client disconnect, BFF disconnect,
# generator close without typed terminal, host restart). Never used
# to fabricate a ``committed`` row — only ``failed`` or ``cancelled``.
RECONCILE_STALE_TERMINAL_REASON = "stale_stream_reconciled"

# ASK-TURN-LIFECYCLE R1: default wall-clock threshold after which a
# streaming ``reader_ask_turn_runs`` row is considered orphaned. The
# in-process lifecycle hook (``_StreamLifecycleContext``) is the
# primary reconciliation path on generator close; this threshold is the
# safety net for host restart / process crash / leaked rows from prior
# deploys. Picked conservatively — long enough that no healthy turn
# exceeds it (DeepSeek V4 Pro p99 was ~95s as of the audit), short
# enough that an orphan does not linger for hours.
DEFAULT_STALE_STREAM_THRESHOLD_SECONDS: int = 300

# ASK-TURN-LIFECYCLE R4-5: heartbeat interval for active streaming turns.
# During streaming, the production generator updates ``updated_at`` on the
# turn_run row at this interval, proving the owner process is alive. The
# stale-stream reconciler checks ``updated_at`` — a row with a recent
# heartbeat is NOT stale even if ``started_at`` is old. This prevents the
# safety-net sweep from killing long-running turns that are still actively
# streaming. No schema change is needed: the existing ``updated_at`` column
# (present since 0001_initial_schema.sql) serves as the heartbeat column.
HEARTBEAT_INTERVAL_SECONDS: int = 15

# ASK-TURN-LIFECYCLE R4-5: a streaming row whose ``updated_at`` is older
# than this threshold (relative to now) is considered heartbeat-dead — the
# owner process is gone or stuck. Must be > HEARTBEAT_INTERVAL_SECONDS to
# avoid false positives from scheduling jitter; 3x gives comfortable margin.
HEARTBEAT_STALE_THRESHOLD_SECONDS: int = HEARTBEAT_INTERVAL_SECONDS * 3


class ReaderRecordAskRepository:
    """DB access for agentic message / turn-run rows."""

    def __init__(self, *, pool: Any | None = None) -> None:
        self._pool = pool

    def _pool_or_raise(self) -> Any:
        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("Database pool not initialized")
        return pool

    async def _read_winning_terminal(
        self,
        *,
        conn_pool: Any,
        turn_run_id: UUID,
    ) -> dict[str, Any]:
        """R4-3: read the actual winning terminal state after CAS loss.

        Returns a dict with ``final_status``, ``terminal_reason``,
        ``user_visible_output_json``, ``envelope_fingerprint``, and
        ``execution_version`` from the persisted row. If the row is
        missing (deleted), returns an empty dict — callers must treat
        this as an unknown terminal and converge silently.
        """
        async with conn_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT final_status,
                       terminal_reason,
                       user_visible_output_json,
                       envelope_fingerprint,
                       execution_version
                FROM reader_ask_turn_runs
                WHERE id = $1
                """,
                turn_run_id,
            )
        if row is None:
            return {}
        return {
            "final_status": row["final_status"],
            "terminal_reason": row["terminal_reason"],
            "user_visible_output_json": (
                json.loads(row["user_visible_output_json"])
                if row["user_visible_output_json"] is not None
                else None
            ),
            "envelope_fingerprint": row["envelope_fingerprint"],
            "execution_version": row["execution_version"],
        }

    async def _terminalize_superseded_turn_run(
        self,
        *,
        conn_pool: Any,
        turn_run_id: UUID,
    ) -> dict[str, Any]:
        """Close an old run that no longer owns its assistant message.

        This statement intentionally touches only ``reader_ask_turn_runs``.
        The newer run owns the message row and must remain unaffected.
        """
        now = datetime.now(UTC)
        async with conn_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE reader_ask_turn_runs
                SET status = 'cancelled',
                    final_status = 'cancelled',
                    terminal_reason = 'superseded_by_newer_turn',
                    user_visible_output_json = NULL,
                    resolved_evidence_json = '[]'::jsonb,
                    reasoning_projection_json = NULL,
                    failed_at = $2,
                    updated_at = $2
                WHERE id = $1 AND status = 'streaming'
                RETURNING final_status,
                          terminal_reason,
                          user_visible_output_json,
                          envelope_fingerprint,
                          execution_version
                """,
                turn_run_id,
                now,
            )
        if row is None:
            return await self._read_winning_terminal(
                conn_pool=conn_pool,
                turn_run_id=turn_run_id,
            )
        return {
            "final_status": row["final_status"],
            "terminal_reason": row["terminal_reason"],
            "user_visible_output_json": None,
            "envelope_fingerprint": row["envelope_fingerprint"],
            "execution_version": row["execution_version"],
        }

    async def get_thread(
        self,
        *,
        user_id: UUID,
        thread_id: UUID,
        reading_record_id: UUID,
    ) -> dict[str, Any] | None:
        pool = self._pool_or_raise()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, user_id, reading_record_id, title, is_default
                FROM reader_ask_threads
                WHERE id = $1
                  AND user_id = $2
                  AND reading_record_id = $3
                  AND archived_at IS NULL
                """,
                thread_id,
                user_id,
                reading_record_id,
            )
        if row is None:
            return None
        return {
            "id": str(row["id"]),
            "user_id": str(row["user_id"]),
            "reading_record_id": str(row["reading_record_id"]),
            "title": row["title"],
            "is_default": bool(row["is_default"]),
        }

    async def create_message(
        self,
        *,
        thread_id: UUID,
        role: str,
        status: str,
        content_md: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        pool = self._pool_or_raise()
        now = datetime.now(UTC)
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    INSERT INTO reader_ask_messages (
                        thread_id, role, status, content_md,
                        context_anchors_json, citations_json, action_proposals_json,
                        tool_trace_json, metadata_json,
                        created_at, updated_at
                    )
                    VALUES (
                        $1, $2, $3, $4,
                        '[]'::jsonb, '[]'::jsonb, '[]'::jsonb,
                        '[]'::jsonb, $5::jsonb,
                        $6, $6
                    )
                    RETURNING id, thread_id, role, status, content_md, created_at
                    """,
                    thread_id,
                    role,
                    status,
                    content_md,
                    jsonb_param(metadata or {}),
                    now,
                )
                await conn.execute(
                    """
                    UPDATE reader_ask_threads
                    SET last_message_at = $2, updated_at = $2
                    WHERE id = $1
                    """,
                    thread_id,
                    now,
                )
        assert row is not None
        return {
            "id": str(row["id"]),
            "thread_id": str(row["thread_id"]),
            "role": row["role"],
            "status": row["status"],
            "content_md": row["content_md"],
        }

    async def create_agentic_turn_run(
        self,
        *,
        message_id: UUID,
        thread_id: UUID,
        user_id: UUID,
        reading_record_id: UUID,
        base_id: UUID,
        generation: int,
        turn_id: UUID,
        envelope_fingerprint: str,
        envelope_snapshot: dict[str, Any],
        status: str = "streaming",
    ) -> dict[str, Any]:
        pool = self._pool_or_raise()
        now = datetime.now(UTC)
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    INSERT INTO reader_ask_turn_runs (
                        message_id, thread_id, user_id, analysis_record_id,
                        reading_record_id, base_id, generation, turn_id,
                        run_attempt, status, execution_version,
                        envelope_fingerprint, envelope_snapshot_json,
                        started_at, created_at, updated_at
                    )
                    VALUES (
                        $1, $2, $3, NULL,
                        $4, $5, $6, $7,
                        1, $8, $9,
                        $10, $11::jsonb,
                        $12, $12, $12
                    )
                    RETURNING id, status, execution_version, envelope_fingerprint
                    """,
                    message_id,
                    thread_id,
                    user_id,
                    reading_record_id,
                    base_id,
                    generation,
                    turn_id,
                    status,
                    EXECUTION_VERSION_AGENTIC_V2,
                    envelope_fingerprint,
                    jsonb_param(envelope_snapshot),
                    now,
                )
                assert row is not None
                claim = await conn.execute(
                    """
                    UPDATE reader_ask_messages
                    SET current_turn_run_id = $2,
                        status = 'streaming',
                        updated_at = $3
                    WHERE id = $1
                    """,
                    message_id,
                    row["id"],
                    now,
                )
                if claim != "UPDATE 1":
                    raise RuntimeError(
                        "agentic turn run could not claim assistant message"
                    )
        return {
            "id": str(row["id"]),
            "status": row["status"],
            "execution_version": row["execution_version"],
            "envelope_fingerprint": row["envelope_fingerprint"],
        }

    async def complete_agentic_turn_run(
        self,
        *,
        turn_run_id: UUID,
        message_id: UUID,
        answer_text: str,
        completed_dto: dict[str, Any],
        resolved_evidence: list[dict[str, Any]],
        final_status: str = "ok",
        reasoning_projection: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Idempotent success-terminal write.

        ASK-TURN-LIFECYCLE R1: the ``WHERE status = 'streaming'`` guard
        makes the write idempotent — a second call after the row already
        transitioned to a terminal state (committed / failed / cancelled)
        returns a typed ``already_terminal`` placeholder instead of
        flipping the row back or asserting. This closes the
        cancel-after-completed / completed-after-cancel race.

        ASK-TURN-LIFECYCLE R4-3: when the CAS fails (``row is None``),
        the method SELECTs the actual winning terminal state so the
        caller can project the real persisted terminal instead of
        fabricating a ``completed``. The returned dict carries
        ``winning_final_status`` / ``winning_terminal_reason`` /
        ``winning_user_visible_output_json`` so the caller can decide
        whether to emit a typed terminal, a no-op, or converge silently.
        """
        pool = self._pool_or_raise()
        now = datetime.now(UTC)
        async with pool.acquire() as conn:
            async with conn.transaction():
                owner = await conn.fetchrow(
                    """
                    SELECT status, current_turn_run_id
                    FROM reader_ask_messages
                    WHERE id = $1
                    FOR UPDATE
                    """,
                    message_id,
                )
                owns_message = (
                    owner is not None
                    and owner["status"] == "streaming"
                    and owner["current_turn_run_id"] == turn_run_id
                )
                row = (
                    await conn.fetchrow(
                        """
                        UPDATE reader_ask_turn_runs
                        SET status = 'completed',
                            final_status = $2,
                            terminal_reason = NULL,
                            user_visible_output_json = $3::jsonb,
                            resolved_evidence_json = $4::jsonb,
                            reasoning_projection_json = $6::jsonb,
                            completed_at = $5,
                            updated_at = $5
                        WHERE id = $1 AND status = 'streaming'
                        RETURNING id, status, final_status,
                                  user_visible_output_json,
                                  resolved_evidence_json,
                                  reasoning_projection_json,
                                  envelope_fingerprint,
                                  execution_version
                        """,
                        turn_run_id,
                        final_status,
                        jsonb_param(completed_dto),
                        jsonb_param(resolved_evidence),
                        now,
                        jsonb_param(reasoning_projection)
                        if reasoning_projection is not None
                        else None,
                    )
                    if owns_message
                    else None
                )
                if row is not None and owns_message:
                    await conn.execute(
                        """
                        UPDATE reader_ask_messages
                        SET status = 'completed',
                            content_md = $2,
                            current_turn_run_id = $3,
                            updated_at = $4
                        WHERE id = $1
                          AND status = 'streaming'
                          AND current_turn_run_id = $3
                        """,
                        message_id,
                        answer_text,
                        turn_run_id,
                        now,
                    )
        if row is None:
            # R4-3: CAS lost — read the actual winning terminal state so
            # the caller can project the real persisted terminal instead
            # of fabricating a completed. Never returns a fabricated ok.
            winning = await self._read_winning_terminal(
                conn_pool=pool,
                turn_run_id=turn_run_id,
            )
            if winning.get("final_status") is None:
                winning = await self._terminalize_superseded_turn_run(
                    conn_pool=pool,
                    turn_run_id=turn_run_id,
                )
            logger.info(
                "complete_agentic_turn_run skipped: turn_run_id=%s already "
                "terminal winning_final_status=%s",
                turn_run_id,
                winning.get("final_status"),
            )
            return {
                "id": str(turn_run_id),
                "status": "already_terminal",
                "final_status": None,
                "user_visible_output_json": None,
                "resolved_evidence_json": None,
                "reasoning_projection_json": None,
                "envelope_fingerprint": winning.get("envelope_fingerprint"),
                "execution_version": winning.get("execution_version")
                or EXECUTION_VERSION_AGENTIC_V2,
                # R4-3: winning terminal state for caller-side CAS
                # decisioning. ``winning_final_status`` is the actual
                # persisted final_status (ok / failed / cancelled /
                # context_stale). ``winning_terminal_reason`` is the
                # persisted terminal_reason (NULL when the winner was
                # a clean completed). ``winning_user_visible_output_json``
                # is the persisted terminal DTO (if any).
                "winning_final_status": winning.get("final_status"),
                "winning_terminal_reason": winning.get("terminal_reason"),
                "winning_user_visible_output_json": winning.get(
                    "user_visible_output_json"
                ),
            }
        return {
            "id": str(row["id"]),
            "status": row["status"],
            "final_status": row["final_status"],
            "user_visible_output_json": row["user_visible_output_json"],
            "resolved_evidence_json": row["resolved_evidence_json"],
            "reasoning_projection_json": row["reasoning_projection_json"],
            "envelope_fingerprint": row["envelope_fingerprint"],
            "execution_version": row["execution_version"],
        }

    async def terminal_agentic_turn_run(
        self,
        *,
        turn_run_id: UUID,
        message_id: UUID,
        run_status: str,
        final_status: str,
        terminal_reason: str | None,
        terminal_dto: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist non-ok terminal (stale / invalid / cancelled / failed).

        ASK-REASONING-R2: ``reasoning_projection_json`` is explicitly
        forced to NULL on every terminal path — fail-closed by statement,
        not by relying on fresh rows starting empty. Cancel / validation
        failure / budget / persist failure never persist reasoning.

        ASK-TURN-LIFECYCLE R1: the ``WHERE status = 'streaming'`` guard
        makes the write idempotent — a second call after the row already
        transitioned to a terminal state returns a typed
        ``already_terminal`` placeholder. This closes the
        cancel-after-completed / completed-after-cancel race.
        """
        pool = self._pool_or_raise()
        now = datetime.now(UTC)
        async with pool.acquire() as conn:
            async with conn.transaction():
                owner = await conn.fetchrow(
                    """
                    SELECT status, current_turn_run_id
                    FROM reader_ask_messages
                    WHERE id = $1
                    FOR UPDATE
                    """,
                    message_id,
                )
                owns_message = (
                    owner is not None
                    and owner["status"] == "streaming"
                    and owner["current_turn_run_id"] == turn_run_id
                )
                row = await conn.fetchrow(
                    """
                    UPDATE reader_ask_turn_runs
                    SET status = $2,
                        final_status = $3,
                        terminal_reason = $4,
                        user_visible_output_json = $5::jsonb,
                        resolved_evidence_json = '[]'::jsonb,
                        reasoning_projection_json = NULL,
                        failed_at = CASE WHEN $2 IN ('failed', 'stale', 'cancelled')
                                         THEN $6 ELSE failed_at END,
                        completed_at = CASE WHEN $2 = 'stale' THEN $6 ELSE completed_at END,
                        updated_at = $6
                    WHERE id = $1 AND status = 'streaming'
                    RETURNING id, status, final_status, terminal_reason,
                              user_visible_output_json, reasoning_projection_json,
                              envelope_fingerprint, execution_version
                    """,
                    turn_run_id,
                    run_status,
                    final_status,
                    terminal_reason,
                    jsonb_param(terminal_dto) if terminal_dto is not None else None,
                    now,
                )
                if row is not None and owns_message:
                    await conn.execute(
                        """
                        UPDATE reader_ask_messages
                        SET status = 'failed',
                            content_md = '',
                            current_turn_run_id = $2,
                            updated_at = $3
                        WHERE id = $1
                          AND status = 'streaming'
                          AND current_turn_run_id = $2
                        """,
                        message_id,
                        turn_run_id,
                        now,
                    )
        if row is None:
            logger.info(
                "terminal_agentic_turn_run skipped: turn_run_id=%s already terminal",
                turn_run_id,
            )
            return {
                "id": str(turn_run_id),
                "status": "already_terminal",
                "final_status": None,
                "terminal_reason": None,
                "user_visible_output_json": None,
                "reasoning_projection_json": None,
                "envelope_fingerprint": None,
                "execution_version": EXECUTION_VERSION_AGENTIC_V2,
            }
        return {
            "id": str(row["id"]),
            "status": row["status"],
            "final_status": row["final_status"],
            "terminal_reason": row["terminal_reason"],
            "user_visible_output_json": row["user_visible_output_json"],
            "reasoning_projection_json": row["reasoning_projection_json"],
            "envelope_fingerprint": row["envelope_fingerprint"],
            "execution_version": row["execution_version"],
        }

    async def reconcile_stale_streaming_turn_run(
        self,
        *,
        turn_run_id: UUID,
        message_id: UUID,
        run_status: str = "cancelled",
    ) -> dict[str, Any]:
        """Reconcile a streaming row whose owner has gone away.

        ASK-TURN-LIFECYCLE R1: used by the route ``finally`` and the
        ``stream_agentic_thread_message`` outer ``finally`` when the
        generator is closed without a typed terminal (client disconnect,
        BFF disconnect, ASGI cancellation, host restart). Always moves
        the row to ``cancelled`` or ``failed`` — NEVER to ``committed``
        (no fabricated success). The typed
        ``RECONCILE_STALE_TERMINAL_REASON`` is recorded so observers can
        distinguish reconciliation from a real provider terminal.

        Idempotent: a row already in a terminal state is left untouched
        and a typed ``already_terminal`` placeholder is returned.
        """
        if run_status not in ("cancelled", "failed"):
            raise ValueError(
                "reconcile_stale_streaming_turn_run: run_status must be "
                "'cancelled' or 'failed'; never 'committed'"
            )
        final_status = "cancelled" if run_status == "cancelled" else "failed"
        return await self.terminal_agentic_turn_run(
            turn_run_id=turn_run_id,
            message_id=message_id,
            run_status=run_status,
            final_status=final_status,
            terminal_reason=RECONCILE_STALE_TERMINAL_REASON,
            terminal_dto=None,
        )

    async def heartbeat_turn_run(self, *, turn_run_id: UUID) -> None:
        """Update ``updated_at`` on a streaming turn_run row.

        ASK-TURN-LIFECYCLE R4-5: proves the owner process is alive during
        long-running turns. The stale-stream reconciler checks
        ``updated_at`` — a row with a recent heartbeat is NOT considered
        stale even if ``started_at`` is old. This is the heartbeat half
        of the owner/lease proof: the route ``finally`` is the lease
        release.

        Safe to call on a row that already transitioned to terminal —
        the ``WHERE status = 'streaming'`` guard makes it a no-op.
        Never raises on missing rows (best-effort observability write).
        """
        pool = self._pool_or_raise()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE reader_ask_turn_runs
                SET updated_at = NOW()
                WHERE id = $1 AND status = 'streaming'
                """,
                turn_run_id,
            )

    async def list_stale_streaming_turn_runs(
        self,
        *,
        older_than_seconds: int = DEFAULT_STALE_STREAM_THRESHOLD_SECONDS,
        now: datetime | None = None,
        limit: int = 200,
        heartbeat_stale_seconds: int = HEARTBEAT_STALE_THRESHOLD_SECONDS,
    ) -> list[dict[str, Any]]:
        """List streaming ``reader_ask_turn_runs`` that are heartbeat-dead.

        ASK-TURN-LIFECYCLE R1/R4-5: the safety-net reconciliation query.
        The in-process ``_StreamLifecycleContext`` hook handles per-request
        cleanup on generator close; this method handles rows whose owner
        process is gone (host restart / crash / leaked rows from prior
        deploys).

        R4-5 owner/heartbeat proof: a row is stale ONLY if BOTH:
          1. ``started_at < cutoff`` — old enough to be considered stale.
          2. ``updated_at < heartbeat_cutoff`` — no recent heartbeat.
        The heartbeat is written by ``heartbeat_turn_run`` during active
        streaming (every ``HEARTBEAT_INTERVAL_SECONDS``). A long-running
        turn with recent heartbeats is NOT stale — its owner is provably
        alive. A row from a crashed process has stale ``updated_at``
        because no heartbeat has been written since the crash.

        On startup (``run_startup_stale_stream_sweep``) all streaming
        rows are stale because the previous process is dead and no
        heartbeats are being written.

        Returns the typed rows for the caller (worker / admin script /
        observability) to inspect before reconciliation. **Does not
        mutate the database** — call ``reconcile_stale_streaming_turn_runs_batch``
        to flip the rows. The caller is responsible for not running this
        against the local DB without explicit owner approval.

        ``limit`` caps the result to avoid unbounded scans on databases
        with many orphans; the caller may invoke again to drain.
        """
        pool = self._pool_or_raise()
        now_dt = now or datetime.now(UTC)
        started_cutoff = now_dt - timedelta(seconds=older_than_seconds)
        heartbeat_cutoff = now_dt - timedelta(seconds=heartbeat_stale_seconds)
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT tr.id AS turn_run_id,
                       tr.message_id,
                       tr.thread_id,
                       tr.user_id,
                       tr.started_at,
                       tr.updated_at,
                       tr.execution_version,
                       tr.envelope_fingerprint
                FROM reader_ask_turn_runs tr
                WHERE tr.status = 'streaming'
                  AND tr.started_at < $1
                  AND tr.updated_at < $2
                ORDER BY tr.started_at ASC
                LIMIT $3
                """,
                started_cutoff,
                heartbeat_cutoff,
                limit,
            )
        return [
            {
                "turn_run_id": str(row["turn_run_id"]),
                "message_id": str(row["message_id"]),
                "thread_id": str(row["thread_id"]),
                "user_id": str(row["user_id"]),
                "started_at": row["started_at"],
                "updated_at": row["updated_at"],
                "execution_version": row["execution_version"],
                "envelope_fingerprint": row["envelope_fingerprint"],
            }
            for row in rows
        ]

    async def reconcile_stale_streaming_turn_runs_batch(
        self,
        *,
        older_than_seconds: int = DEFAULT_STALE_STREAM_THRESHOLD_SECONDS,
        now: datetime | None = None,
        limit: int = 100,
        run_status: str = "cancelled",
    ) -> dict[str, Any]:
        """Reconcile all stale streaming rows older than the threshold.

        ASK-TURN-LIFECYCLE R1: iterates the rows returned by
        ``list_stale_streaming_turn_runs`` and reconciles each to
        ``cancelled`` (default) or ``failed`` using the typed
        ``stale_stream_reconciled`` terminal reason. **Never** fabricates
        ``committed`` — the run_status guard rejects any other value.

        Idempotent: rows already transitioned by a concurrent reconciler
        or by the in-process lifecycle hook return ``already_terminal``
        and are counted separately.

        Returns a typed summary the caller can log / surface to ops:

            {
              "scanned": <int>,
              "reconciled": <int>,
              "already_terminal": <int>,
              "errors": <int>,
              "run_status": "cancelled" | "failed",
              "terminal_reason": "stale_stream_reconciled",
              "cutoff": <iso8601>,
            }

        The caller is responsible for not running this against the local
        DB without explicit owner approval. The method does NOT open a
        single wrapping transaction — each row is reconciled in its own
        transaction so a single bad row does not abort the whole batch.
        """
        if run_status not in ("cancelled", "failed"):
            raise ValueError(
                "reconcile_stale_streaming_turn_runs_batch: run_status "
                "must be 'cancelled' or 'failed'; never 'committed'"
            )

        cutoff_dt = (now or datetime.now(UTC)) - timedelta(seconds=older_than_seconds)
        stale_rows = await self.list_stale_streaming_turn_runs(
            older_than_seconds=older_than_seconds,
            now=now,
            limit=limit,
        )

        reconciled = 0
        already_terminal = 0
        errors = 0
        for row in stale_rows:
            try:
                result = await self.reconcile_stale_streaming_turn_run(
                    turn_run_id=UUID(row["turn_run_id"]),
                    message_id=UUID(row["message_id"]),
                    run_status=run_status,
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "stale_stream_reconcile_batch row failed: "
                    "turn_run_id=%s message_id=%s",
                    row["turn_run_id"],
                    row["message_id"],
                )
                errors += 1
                continue
            if result.get("status") == "already_terminal":
                already_terminal += 1
            else:
                reconciled += 1

        return {
            "scanned": len(stale_rows),
            "reconciled": reconciled,
            "already_terminal": already_terminal,
            "errors": errors,
            "run_status": run_status,
            "terminal_reason": RECONCILE_STALE_TERMINAL_REASON,
            "cutoff": cutoff_dt.isoformat(),
        }

    async def get_turn_run(self, turn_run_id: UUID) -> dict[str, Any] | None:
        pool = self._pool_or_raise()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, status, final_status, terminal_reason,
                       user_visible_output_json, resolved_evidence_json,
                       envelope_fingerprint, execution_version,
                       envelope_snapshot_json
                FROM reader_ask_turn_runs
                WHERE id = $1
                """,
                turn_run_id,
            )
        if row is None:
            return None
        return {
            "id": str(row["id"]),
            "status": row["status"],
            "final_status": row["final_status"],
            "terminal_reason": row["terminal_reason"],
            "user_visible_output_json": row["user_visible_output_json"],
            "resolved_evidence_json": row["resolved_evidence_json"],
            "envelope_fingerprint": row["envelope_fingerprint"],
            "execution_version": row["execution_version"],
            "envelope_snapshot_json": row["envelope_snapshot_json"],
        }

    async def get_message_restricted_evidence_for_navigation(
        self,
        *,
        user_id: UUID,
        reading_record_id: UUID,
        message_id: UUID,
    ) -> dict[str, Any] | None:
        """Load restricted evidence for secure citation navigation.

        Enforces thread ownership via user_id + reading_record_id. Returns
        ``resolved_evidence_json`` and message identity only — never public
        handle leaks through this method's return contract documentation.
        """
        pool = self._pool_or_raise()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT m.id AS message_id,
                       m.thread_id,
                       t.reading_record_id,
                       tr.resolved_evidence_json,
                       tr.final_status,
                       tr.execution_version
                FROM reader_ask_messages m
                JOIN reader_ask_threads t ON t.id = m.thread_id
                LEFT JOIN reader_ask_turn_runs tr
                  ON tr.id = m.current_turn_run_id
                WHERE m.id = $1
                  AND m.role = 'assistant'
                  AND t.user_id = $2
                  AND t.reading_record_id = $3
                  AND t.archived_at IS NULL
                """,
                message_id,
                user_id,
                reading_record_id,
            )
        if row is None:
            return None
        return {
            "message_id": str(row["message_id"]),
            "thread_id": str(row["thread_id"]),
            "reading_record_id": str(row["reading_record_id"]),
            "resolved_evidence_json": row["resolved_evidence_json"],
            "final_status": row["final_status"],
            "execution_version": row["execution_version"],
        }

    async def get_assistant_message_with_preceding_user_message(
        self,
        *,
        thread_id: UUID,
        message_id: UUID,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """Fetch an assistant message and its closest preceding user message.

        Used by the agentic retry path to re-run an assistant turn without
        creating a new user message. Returns ``(assistant_msg, user_msg)``
        or ``(None, None)`` when the assistant message does not exist in
        this thread, is not role='assistant', or no preceding user message
        is found.

        ASK-WEB-G1-R2: the user message dict now carries
        ``metadata_json`` (parsed dict, never ``None``) so the retry path
        can replay the original turn's ``web_search_mode`` without
        re-deciding it from the current UI toggle. Legacy rows with
        ``metadata_json IS NULL`` surface as ``{}`` — the caller treats
        absent ``web_search_mode`` as ``"disabled"`` (fail-closed).

        Ownership of the thread is enforced by the caller via ``get_thread``
        before this method is invoked; this method only reads message rows
        scoped by ``thread_id``.
        """
        pool = self._pool_or_raise()
        async with pool.acquire() as conn:
            assistant_row = await conn.fetchrow(
                """
                SELECT id, thread_id, role, status, content_md, created_at
                FROM reader_ask_messages
                WHERE id = $1
                  AND thread_id = $2
                  AND role = 'assistant'
                """,
                message_id,
                thread_id,
            )
            if assistant_row is None:
                return None, None
            user_row = await conn.fetchrow(
                """
                SELECT id, thread_id, role, status, content_md, created_at,
                       metadata_json
                FROM reader_ask_messages
                WHERE thread_id = $1
                  AND role = 'user'
                  AND created_at < $2
                ORDER BY created_at DESC
                LIMIT 1
                """,
                thread_id,
                assistant_row["created_at"],
            )
        assistant_msg = {
            "id": str(assistant_row["id"]),
            "thread_id": str(assistant_row["thread_id"]),
            "role": assistant_row["role"],
            "status": assistant_row["status"],
            "content_md": assistant_row["content_md"],
        }
        if user_row is None:
            return assistant_msg, None
        # ``metadata_json`` is NULLABLE in the DB schema; normalise to
        # an empty dict so callers can safely ``.get()`` any key. The
        # retry path looks up ``web_search_mode`` here.
        raw_metadata = user_row.get("metadata_json")
        user_metadata: dict[str, Any] = (
            dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
        )
        user_msg = {
            "id": str(user_row["id"]),
            "thread_id": str(user_row["thread_id"]),
            "role": user_row["role"],
            "status": user_row["status"],
            "content_md": user_row["content_md"],
            "metadata_json": user_metadata,
        }
        return assistant_msg, user_msg

    async def reset_assistant_message_for_retry(
        self,
        *,
        message_id: UUID,
    ) -> dict[str, Any]:
        """Reset an assistant message to 'streaming' for an agentic retry.

        Clears ``content_md`` and flips ``status`` back to ``streaming`` so
        the new turn_run can repopulate it. Returns the reset row. Caller
        must have already verified the message belongs to the user's thread
        via ``get_assistant_message_with_preceding_user_message``.
        """
        pool = self._pool_or_raise()
        now = datetime.now(UTC)
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE reader_ask_messages
                SET status = 'streaming',
                    content_md = '',
                    current_turn_run_id = NULL,
                    updated_at = $2
                WHERE id = $1
                RETURNING id, thread_id, role, status, content_md
                """,
                message_id,
                now,
            )
        assert row is not None
        return {
            "id": str(row["id"]),
            "thread_id": str(row["thread_id"]),
            "role": row["role"],
            "status": row["status"],
            "content_md": row["content_md"],
        }
