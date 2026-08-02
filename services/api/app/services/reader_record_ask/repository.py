"""Persistence seam for agentic Reading Record Ask turns.

Owns SQL against ``reader_ask_*`` tables for the agentic lane only.
The repository owns the v2 persistence seam and does not depend on a legacy
Ask repository.
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
from app.services.reader_record_ask.history_projection import (
    is_agentic_execution_version,
    project_agentic_history_message,
    quarantine_untrusted_agentic_claim,
)

logger = logging.getLogger(__name__)

# ASK-TURN-LIFECYCLE R1: typed terminal reason for stale-stream
# reconciliation. Used when the host detects a streaming run/message
# whose owner has gone away (client disconnect, BFF disconnect,
# generator close without typed terminal, host restart). Never used
# to fabricate a ``committed`` row — only ``failed`` or ``cancelled``.
RECONCILE_STALE_TERMINAL_REASON = "stale_stream_reconciled"


class SubmissionIdempotencyUnavailable(RuntimeError):
    """Raised when reader_ask_client_submissions is missing (0026 not applied)."""

    def __init__(self, *, cause: BaseException | None = None) -> None:
        super().__init__(
            "submission_idempotency_unavailable: migration 0026 not applied"
        )
        self.cause = cause

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


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.astimezone(UTC).isoformat()


def _thread_row_to_dict(row: Any) -> dict[str, Any]:
    reading_record_id = row.get("reading_record_id")
    analysis_record_id = row.get("analysis_record_id")
    record_id = reading_record_id or analysis_record_id
    return {
        "id": str(row["id"]),
        "user_id": str(row["user_id"]) if row.get("user_id") is not None else None,
        "record_id": str(record_id) if record_id is not None else None,
        "record_scope": (
            "reading_record" if reading_record_id is not None
            else "analysis" if analysis_record_id is not None
            else None
        ),
        "analysis_record_id": str(analysis_record_id) if analysis_record_id is not None else None,
        "reading_record_id": str(reading_record_id) if reading_record_id is not None else None,
        "title": row["title"],
        "is_default": bool(row["is_default"]),
        "selected_model_key": row.get("selected_model_key"),
        "archived_at": _iso(row.get("archived_at")),
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
        "last_message_at": _iso(row.get("last_message_at")),
    }


def _turn_run_for_history(row: Any) -> dict[str, Any] | None:
    run_id = row.get("turn_run_id")
    if run_id is None:
        return None
    return {
        "id": str(run_id),
        "message_id": str(row["id"]),
        "thread_id": str(row["thread_id"]),
        "user_id": (
            str(row["turn_run_user_id"])
            if row.get("turn_run_user_id") is not None
            else None
        ),
        "reading_record_id": str(row["turn_run_reading_record_id"])
        if row.get("turn_run_reading_record_id") is not None else None,
        "status": row.get("turn_run_status"),
        "final_status": row.get("turn_run_final_status"),
        "terminal_reason": row.get("turn_run_terminal_reason"),
        "execution_version": row.get("turn_run_execution_version"),
        "user_visible_output_json": row.get("user_visible_output_json"),
        "resolved_evidence_json": row.get("turn_run_resolved_evidence_json"),
        "reasoning_projection_json": row.get("turn_run_reasoning_projection_json"),
        "envelope_fingerprint": row.get("turn_run_envelope_fingerprint"),
        "usage_event_id": str(row["turn_run_usage_event_id"])
        if row.get("turn_run_usage_event_id") is not None else None,
        "started_at": _iso(row.get("turn_run_started_at")),
        "completed_at": _iso(row.get("turn_run_completed_at")),
        "failed_at": _iso(row.get("turn_run_failed_at")),
        "created_at": _iso(row.get("turn_run_created_at")),
        "updated_at": _iso(row.get("turn_run_updated_at")),
    }


def _message_row_to_history(row: Any) -> dict[str, Any]:
    metadata = row.get("metadata_json") if isinstance(row.get("metadata_json"), dict) else {}
    turn_run = _turn_run_for_history(row)
    execution_version = row.get("turn_run_execution_version")
    base = {
        "id": str(row["id"]),
        "thread_id": str(row["thread_id"]),
        "role": row["role"],
        "status": row["status"],
        "content_md": row["content_md"] or "",
        "context_anchors": row.get("context_anchors_json") or [],
        "citations": row.get("citations_json") or [],
        "action_proposals": row.get("action_proposals_json") or [],
        "tool_trace": row.get("tool_trace_json") or [],
        "metadata_json": metadata,
        "usage_event_id": (
            str(row["usage_event_id"])
            if row.get("usage_event_id") is not None
            else None
        ),
        "current_turn_run_id": str(row["message_current_turn_run_id"])
        if row.get("message_current_turn_run_id") is not None else None,
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }
    if is_agentic_execution_version(execution_version):
        return project_agentic_history_message(
            message_id=base["id"],
            thread_id=base["thread_id"],
            role=base["role"],
            row_status=base["status"],
            row_content_md=base["content_md"],
            created_at=base["created_at"],
            updated_at=base["updated_at"],
            context_anchors=base["context_anchors"],
            usage_event_id=base["usage_event_id"],
            current_turn_run_id=base["current_turn_run_id"],
            current_turn_run=turn_run,
            user_visible_output_json=row.get("user_visible_output_json"),
            resolved_evidence_json=row.get("turn_run_resolved_evidence_json"),
            final_status=row.get("turn_run_final_status"),
            turn_run_status=row.get("turn_run_status"),
        )
    if base["role"] == "assistant":
        return quarantine_untrusted_agentic_claim(
            message_id=base["id"],
            thread_id=base["thread_id"],
            role=base["role"],
            created_at=base["created_at"],
            updated_at=base["updated_at"],
            context_anchors=base["context_anchors"],
            usage_event_id=base["usage_event_id"],
            current_turn_run_id=base["current_turn_run_id"],
            current_turn_run=turn_run,
        )
    base.pop("metadata_json", None)
    return base


_MESSAGE_HISTORY_SELECT = """
SELECT m.id, m.thread_id, m.role, m.status, m.content_md,
       m.context_anchors_json, m.citations_json, m.action_proposals_json,
       m.tool_trace_json, m.metadata_json, m.current_turn_run_id AS message_current_turn_run_id,
       m.usage_event_id, m.created_at, m.updated_at,
       tr.id AS turn_run_id, tr.user_id AS turn_run_user_id,
       tr.reading_record_id AS turn_run_reading_record_id,
       tr.status AS turn_run_status, tr.final_status AS turn_run_final_status,
       tr.terminal_reason AS turn_run_terminal_reason,
       tr.execution_version AS turn_run_execution_version,
       tr.user_visible_output_json, tr.resolved_evidence_json AS turn_run_resolved_evidence_json,
       tr.reasoning_projection_json AS turn_run_reasoning_projection_json,
       tr.envelope_fingerprint AS turn_run_envelope_fingerprint,
       tr.usage_event_id AS turn_run_usage_event_id,
       tr.started_at AS turn_run_started_at, tr.completed_at AS turn_run_completed_at,
       tr.failed_at AS turn_run_failed_at, tr.created_at AS turn_run_created_at,
       tr.updated_at AS turn_run_updated_at
FROM reader_ask_messages m
LEFT JOIN reader_ask_turn_runs tr ON tr.id = m.current_turn_run_id
"""


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
        reading_record_id: UUID | None = None,
    ) -> dict[str, Any] | None:
        pool = self._pool_or_raise()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, user_id, analysis_record_id, reading_record_id, title,
                       is_default, selected_model_key, archived_at, created_at,
                       updated_at, last_message_at
                FROM reader_ask_threads
                WHERE id = $1
                  AND user_id = $2
                  AND ($3::uuid IS NULL OR reading_record_id = $3)
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
            "record_id": str(row["reading_record_id"] or row["analysis_record_id"]),
            "record_scope": (
                "reading_record"
                if row["reading_record_id"] is not None
                else "analysis"
                if row["analysis_record_id"] is not None
                else None
            ),
            "analysis_record_id": (
                str(row["analysis_record_id"])
                if row["analysis_record_id"] is not None
                else None
            ),
            "reading_record_id": (
                str(row["reading_record_id"])
                if row["reading_record_id"] is not None
                else None
            ),
            "title": row["title"],
            "is_default": bool(row["is_default"]),
            "selected_model_key": row["selected_model_key"],
            "archived_at": _iso(row["archived_at"]),
            "created_at": _iso(row["created_at"]),
            "updated_at": _iso(row["updated_at"]),
            "last_message_at": _iso(row["last_message_at"]),
        }

    async def list_reading_record_threads(
        self,
        *,
        user_id: UUID,
        reading_record_id: UUID,
    ) -> list[dict[str, Any]]:
        pool = self._pool_or_raise()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, user_id, analysis_record_id, reading_record_id, title,
                       is_default, selected_model_key, archived_at, created_at,
                       updated_at, last_message_at
                FROM reader_ask_threads
                WHERE user_id = $1
                  AND reading_record_id = $2
                  AND archived_at IS NULL
                ORDER BY is_default DESC,
                         COALESCE(last_message_at, created_at) DESC,
                         created_at DESC
                """,
                user_id,
                reading_record_id,
            )
        return [_thread_row_to_dict(row) for row in rows]

    async def get_or_create_default_reading_record_thread(
        self,
        *,
        user_id: UUID,
        reading_record_id: UUID,
        title: str | None = None,
        selected_model_key: str | None = None,
    ) -> dict[str, Any]:
        pool = self._pool_or_raise()
        now = datetime.now(UTC)
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO reader_ask_threads (
                    user_id, analysis_record_id, reading_record_id, title,
                    selected_model_key, is_default, created_at, updated_at
                )
                VALUES ($1, NULL, $2, $3, $4, TRUE, $5, $5)
                ON CONFLICT (user_id, reading_record_id)
                WHERE is_default = TRUE
                  AND archived_at IS NULL
                  AND reading_record_id IS NOT NULL
                DO UPDATE SET
                    title = COALESCE(reader_ask_threads.title, EXCLUDED.title),
                    selected_model_key = COALESCE(
                        EXCLUDED.selected_model_key,
                        reader_ask_threads.selected_model_key
                    ),
                    updated_at = EXCLUDED.updated_at
                RETURNING id, user_id, analysis_record_id, reading_record_id, title,
                          is_default, selected_model_key, archived_at, created_at,
                          updated_at, last_message_at
                """,
                user_id,
                reading_record_id,
                title,
                selected_model_key,
                now,
            )
        assert row is not None
        return _thread_row_to_dict(row)

    async def update_thread_selected_model(
        self,
        *,
        user_id: UUID,
        thread_id: UUID,
        selected_model_key: str | None,
    ) -> dict[str, Any] | None:
        pool = self._pool_or_raise()
        now = datetime.now(UTC)
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE reader_ask_threads
                SET selected_model_key = $3, updated_at = $4
                WHERE id = $1 AND user_id = $2 AND archived_at IS NULL
                RETURNING id, user_id, analysis_record_id, reading_record_id, title,
                          is_default, selected_model_key, archived_at, created_at,
                          updated_at, last_message_at
                """,
                thread_id,
                user_id,
                selected_model_key,
                now,
            )
        return _thread_row_to_dict(row) if row is not None else None

    async def archive_thread(
        self,
        *,
        user_id: UUID,
        thread_id: UUID,
    ) -> dict[str, Any] | None:
        pool = self._pool_or_raise()
        now = datetime.now(UTC)
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE reader_ask_threads
                SET archived_at = $3, updated_at = $3
                WHERE id = $1 AND user_id = $2 AND archived_at IS NULL
                RETURNING id, user_id, analysis_record_id, reading_record_id, title,
                          is_default, selected_model_key, archived_at, created_at,
                          updated_at, last_message_at
                """,
                thread_id,
                user_id,
                now,
            )
        return _thread_row_to_dict(row) if row is not None else None

    async def list_messages(
        self,
        *,
        thread_id: UUID,
        limit: int | None = 100,
    ) -> list[dict[str, Any]]:
        pool = self._pool_or_raise()
        async with pool.acquire() as conn:
            if limit is None:
                rows = await conn.fetch(
                    _MESSAGE_HISTORY_SELECT
                    + " WHERE m.thread_id = $1 ORDER BY m.created_at ASC",
                    thread_id,
                )
            else:
                rows = await conn.fetch(
                    _MESSAGE_HISTORY_SELECT
                    + " WHERE m.thread_id = $1 ORDER BY m.created_at ASC LIMIT $2",
                    thread_id,
                    limit,
                )
        return [_message_row_to_history(row) for row in rows]

    async def get_message(self, *, message_id: UUID) -> dict[str, Any] | None:
        pool = self._pool_or_raise()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                _MESSAGE_HISTORY_SELECT + " WHERE m.id = $1",
                message_id,
            )
        return _message_row_to_history(row) if row is not None else None

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
        run_attempt: int = 1,
        supersedes_run_id: UUID | None = None,
    ) -> dict[str, Any]:
        pool = self._pool_or_raise()
        now = datetime.now(UTC)
        async with pool.acquire() as conn:
            async with conn.transaction():
                # Concurrent regenerate guard: refuse a second streaming
                # claim when another turn_run already owns this message.
                if supersedes_run_id is not None:
                    active = await conn.fetchval(
                        """
                        SELECT 1
                        FROM reader_ask_turn_runs
                        WHERE message_id = $1
                          AND status = 'streaming'
                        LIMIT 1
                        """,
                        message_id,
                    )
                    if active is not None:
                        raise RuntimeError(
                            "agentic turn run already streaming for message"
                        )
                row = await conn.fetchrow(
                    """
                    INSERT INTO reader_ask_turn_runs (
                        message_id, thread_id, user_id, analysis_record_id,
                        reading_record_id, base_id, generation, turn_id,
                        run_attempt, supersedes_run_id, status, execution_version,
                        envelope_fingerprint, envelope_snapshot_json,
                        started_at, created_at, updated_at
                    )
                    VALUES (
                        $1, $2, $3, NULL,
                        $4, $5, $6, $7,
                        $8, $9, $10, $11,
                        $12, $13::jsonb,
                        $14, $14, $14
                    )
                    RETURNING id, status, execution_version, envelope_fingerprint,
                              run_attempt, supersedes_run_id
                    """,
                    message_id,
                    thread_id,
                    user_id,
                    reading_record_id,
                    base_id,
                    generation,
                    turn_id,
                    run_attempt,
                    supersedes_run_id,
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
            "run_attempt": row.get("run_attempt"),
            "supersedes_run_id": (
                str(row["supersedes_run_id"])
                if row.get("supersedes_run_id") is not None
                else None
            ),
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
                    meta_row = await conn.fetchrow(
                        """
                        SELECT metadata_json
                        FROM reader_ask_messages
                        WHERE id = $1
                        """,
                        message_id,
                    )
                    raw_meta = (
                        meta_row.get("metadata_json") if meta_row else None
                    )
                    meta: dict[str, Any] = (
                        dict(raw_meta) if isinstance(raw_meta, dict) else {}
                    )
                    meta.pop("retry_fallback", None)
                    await conn.execute(
                        """
                        UPDATE reader_ask_messages
                        SET status = 'completed',
                            content_md = $2,
                            current_turn_run_id = $3,
                            metadata_json = $5::jsonb,
                            updated_at = $4
                        WHERE id = $1
                          AND status = 'streaming'
                          AND current_turn_run_id = $3
                        """,
                        message_id,
                        answer_text,
                        turn_run_id,
                        now,
                        jsonb_param(meta),
                    )
                    await conn.execute(
                        """
                        UPDATE reader_ask_client_submissions
                        SET status = 'completed',
                            lease_expires_at = NULL,
                            updated_at = $2
                        WHERE assistant_message_id = $1
                          AND status IN ('claimed', 'streaming')
                        """,
                        message_id,
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
                    # ASK-RETRY-CONTRACT-R4: if regenerate stored a fallback
                    # canonical answer, restore it instead of blanking.
                    meta_row = await conn.fetchrow(
                        """
                        SELECT metadata_json, content_md
                        FROM reader_ask_messages
                        WHERE id = $1
                        """,
                        message_id,
                    )
                    raw_meta = (
                        meta_row.get("metadata_json") if meta_row else None
                    )
                    meta: dict[str, Any] = (
                        dict(raw_meta) if isinstance(raw_meta, dict) else {}
                    )
                    fallback = meta.get("retry_fallback")
                    if isinstance(fallback, dict) and (
                        fallback.get("content_md") or ""
                    ).strip():
                        restore_status = fallback.get("status") or "completed"
                        if restore_status not in {
                            "completed",
                            "interrupted",
                            "failed",
                        }:
                            restore_status = "completed"
                        fallback_run = fallback.get("current_turn_run_id")
                        meta.pop("retry_fallback", None)
                        await conn.execute(
                            """
                            UPDATE reader_ask_messages
                            SET status = $2,
                                content_md = $3,
                                current_turn_run_id = $4,
                                metadata_json = $5::jsonb,
                                updated_at = $6
                            WHERE id = $1
                              AND status = 'streaming'
                              AND current_turn_run_id = $7
                            """,
                            message_id,
                            restore_status,
                            str(fallback.get("content_md") or ""),
                            (
                                UUID(str(fallback_run))
                                if fallback_run
                                else turn_run_id
                            ),
                            jsonb_param(meta),
                            now,
                            turn_run_id,
                        )
                    else:
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
                # Always sync client submission terminal when we know the message.
                await conn.execute(
                    """
                    UPDATE reader_ask_client_submissions
                    SET status = $2,
                        lease_expires_at = NULL,
                        updated_at = $3
                    WHERE assistant_message_id = $1
                      AND status IN ('claimed', 'streaming')
                    """,
                    message_id,
                    (
                        "cancelled"
                        if final_status == "cancelled"
                        else "failed"
                    ),
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

        ASK-RETRY-CONTRACT-R3: assistant dict also carries
        ``metadata_json`` and the current turn_run ``execution_version``
        (when present) so retry preflight can resolve the immutable lane
        without reading the live feature flag.

        Ownership of the thread is enforced by the caller via ``get_thread``
        before this method is invoked; this method only reads message rows
        scoped by ``thread_id``.
        """
        pool = self._pool_or_raise()
        async with pool.acquire() as conn:
            assistant_row = await conn.fetchrow(
                """
                SELECT m.id, m.thread_id, m.role, m.status, m.content_md,
                       m.created_at, m.metadata_json, m.current_turn_run_id,
                       tr.execution_version AS turn_run_execution_version,
                       tr.id AS turn_run_id,
                       tr.run_attempt AS turn_run_attempt
                FROM reader_ask_messages m
                LEFT JOIN reader_ask_turn_runs tr
                  ON tr.id = m.current_turn_run_id
                WHERE m.id = $1
                  AND m.thread_id = $2
                  AND m.role = 'assistant'
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
        raw_assistant_metadata = assistant_row.get("metadata_json")
        assistant_metadata: dict[str, Any] = (
            dict(raw_assistant_metadata)
            if isinstance(raw_assistant_metadata, dict)
            else {}
        )
        assistant_msg = {
            "id": str(assistant_row["id"]),
            "thread_id": str(assistant_row["thread_id"]),
            "role": assistant_row["role"],
            "status": assistant_row["status"],
            "content_md": assistant_row["content_md"],
            "metadata_json": assistant_metadata,
            "turn_run_execution_version": assistant_row.get(
                "turn_run_execution_version"
            ),
            "turn_run_id": (
                str(assistant_row["turn_run_id"])
                if assistant_row.get("turn_run_id") is not None
                else None
            ),
            "turn_run_attempt": assistant_row.get("turn_run_attempt"),
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

    async def get_message_status(
        self,
        *,
        message_id: UUID,
    ) -> str | None:
        """Load one message status without crossing into the legacy repository."""
        pool = self._pool_or_raise()
        async with pool.acquire() as conn:
            status = await conn.fetchval(
                """
                SELECT status
                FROM reader_ask_messages
                WHERE id = $1
                """,
                message_id,
            )
        return str(status) if status is not None else None

    async def reset_assistant_message_for_retry(
        self,
        *,
        message_id: UUID,
    ) -> dict[str, Any]:
        """Begin regenerate without discarding the prior canonical answer.

        ASK-RETRY-CONTRACT-R4:
        - Keeps ``content_md`` as the visible fallback until a new run
          successfully completes.
        - Snapshots prior status / content / current_turn_run_id into
          ``metadata_json.retry_fallback`` for failure restore.
        - Does **not** clear ``current_turn_run_id`` until a new streaming
          run is claimed (caller creates the new run next). A concurrent
          second regenerate is refused via FOR UPDATE + active streaming
          turn_run check on create.
        """
        pool = self._pool_or_raise()
        now = datetime.now(UTC)
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    SELECT id, thread_id, role, status, content_md,
                           current_turn_run_id, metadata_json
                    FROM reader_ask_messages
                    WHERE id = $1
                    FOR UPDATE
                    """,
                    message_id,
                )
                if row is None:
                    raise RuntimeError("assistant message not found for retry")
                # Refuse if another regenerate already has a streaming run.
                active = await conn.fetchval(
                    """
                    SELECT 1
                    FROM reader_ask_turn_runs
                    WHERE message_id = $1
                      AND status = 'streaming'
                    LIMIT 1
                    """,
                    message_id,
                )
                if active is not None and row["status"] == "streaming":
                    raise RuntimeError(
                        "agentic turn run already streaming for message"
                    )
                raw_meta = row.get("metadata_json")
                meta: dict[str, Any] = (
                    dict(raw_meta) if isinstance(raw_meta, dict) else {}
                )
                meta["retry_fallback"] = {
                    "status": row["status"],
                    "content_md": row["content_md"] or "",
                    "current_turn_run_id": (
                        str(row["current_turn_run_id"])
                        if row["current_turn_run_id"] is not None
                        else None
                    ),
                }
                updated = await conn.fetchrow(
                    """
                    UPDATE reader_ask_messages
                    SET status = 'streaming',
                        metadata_json = $2::jsonb,
                        updated_at = $3
                    WHERE id = $1
                    RETURNING id, thread_id, role, status, content_md,
                              current_turn_run_id
                    """,
                    message_id,
                    jsonb_param(meta),
                    now,
                )
        assert updated is not None
        return {
            "id": str(updated["id"]),
            "thread_id": str(updated["thread_id"]),
            "role": updated["role"],
            "status": updated["status"],
            "content_md": updated["content_md"],
            "current_turn_run_id": (
                str(updated["current_turn_run_id"])
                if updated["current_turn_run_id"] is not None
                else None
            ),
            "retry_fallback": meta.get("retry_fallback"),
        }

    def _raise_if_table_missing(self, exc: BaseException) -> None:
        """Map missing-table errors to typed SubmissionIdempotencyUnavailable."""
        msg = str(exc).lower()
        sqlstate = getattr(exc, "sqlstate", None)
        # asyncpg UndefinedTableError sqlstate 42P01
        if sqlstate == "42P01" or (
            "reader_ask_client_submissions" in msg
            and ("does not exist" in msg or "undefined" in msg)
        ):
            raise SubmissionIdempotencyUnavailable(cause=exc) from exc

    async def ensure_submission_message_pair(
        self,
        *,
        thread_id: UUID,
        user_id: UUID,
        client_submission_id: UUID,
        content_md: str,
        user_metadata: dict[str, Any],
        assistant_metadata: dict[str, Any],
        orphan_lease_seconds: int = 60,
    ) -> dict[str, Any]:
        """R5: atomic claim + user/assistant pair + bind in one transaction.

        Model invocation must happen ONLY after this method returns with
        ``may_create_model=True``. Duplicate keys return existing pair
        without creating messages or allowing a second model call.

        Orphan reclaim: only for ``claimed`` rows with **no** bound
        messages and expired lease — never deletes a row that already
        has a message pair. Reclaim bumps ``claim_generation`` (CAS).
        """
        pool = self._pool_or_raise()
        now = datetime.now(UTC)
        lease = now + timedelta(seconds=orphan_lease_seconds)

        def _msg_dict(row: Any) -> dict[str, Any]:
            return {
                "id": str(row["id"]),
                "thread_id": str(row["thread_id"]),
                "role": row["role"],
                "status": row["status"],
                "content_md": row["content_md"] or "",
            }

        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    existing = await conn.fetchrow(
                        """
                        SELECT thread_id, client_submission_id, user_id,
                               user_message_id, assistant_message_id, status,
                               claim_generation, lease_expires_at
                        FROM reader_ask_client_submissions
                        WHERE thread_id = $1
                          AND client_submission_id = $2
                        FOR UPDATE
                        """,
                        thread_id,
                        client_submission_id,
                    )

                    if existing is not None:
                        has_pair = (
                            existing["user_message_id"] is not None
                            and existing["assistant_message_id"] is not None
                        )
                        if has_pair:
                            return {
                                "may_create_model": False,
                                "status": existing["status"],
                                "claim_generation": int(
                                    existing["claim_generation"] or 1
                                ),
                                "user_message_id": str(
                                    existing["user_message_id"]
                                ),
                                "assistant_message_id": str(
                                    existing["assistant_message_id"]
                                ),
                                "user_message": None,
                                "assistant_message": None,
                                "terminal_code": f"submission_{existing['status']}",
                            }

                        # claimed without pair
                        lease_at = existing.get("lease_expires_at")
                        lease_expired = lease_at is None or lease_at <= now
                        if (
                            existing["status"] == "claimed"
                            and not has_pair
                            and lease_expired
                        ):
                            # Reclaim: bump generation; do NOT delete if
                            # messages somehow appear mid-way (re-check).
                            old_gen = int(existing["claim_generation"] or 1)
                            reclaimed = await conn.fetchrow(
                                """
                                UPDATE reader_ask_client_submissions
                                SET claim_generation = $3,
                                    lease_expires_at = $4,
                                    user_id = $5,
                                    updated_at = $6
                                WHERE thread_id = $1
                                  AND client_submission_id = $2
                                  AND status = 'claimed'
                                  AND user_message_id IS NULL
                                  AND assistant_message_id IS NULL
                                  AND claim_generation = $7
                                RETURNING claim_generation
                                """,
                                thread_id,
                                client_submission_id,
                                old_gen + 1,
                                lease,
                                user_id,
                                now,
                                old_gen,
                            )
                            if reclaimed is None:
                                return {
                                    "may_create_model": False,
                                    "status": "claimed",
                                    "claim_generation": old_gen,
                                    "user_message_id": None,
                                    "assistant_message_id": None,
                                    "terminal_code": "submission_in_progress",
                                }
                            claim_gen = int(reclaimed["claim_generation"])
                        elif existing["status"] == "claimed" and not has_pair:
                            return {
                                "may_create_model": False,
                                "status": "claimed",
                                "claim_generation": int(
                                    existing["claim_generation"] or 1
                                ),
                                "user_message_id": None,
                                "assistant_message_id": None,
                                "terminal_code": "submission_in_progress",
                            }
                        else:
                            # streaming without pair should not happen; fail closed
                            return {
                                "may_create_model": False,
                                "status": existing["status"],
                                "claim_generation": int(
                                    existing["claim_generation"] or 1
                                ),
                                "user_message_id": (
                                    str(existing["user_message_id"])
                                    if existing["user_message_id"]
                                    else None
                                ),
                                "assistant_message_id": (
                                    str(existing["assistant_message_id"])
                                    if existing["assistant_message_id"]
                                    else None
                                ),
                                "terminal_code": "submission_in_progress",
                            }
                    else:
                        # R6: INSERT ON CONFLICT DO NOTHING — never catch
                        # UniqueViolation and continue in the same txn
                        # (Postgres aborts the transaction on 23505).
                        try:
                            inserted = await conn.fetchrow(
                                """
                                INSERT INTO reader_ask_client_submissions (
                                    thread_id, client_submission_id, user_id,
                                    status, claim_generation, lease_expires_at,
                                    created_at, updated_at
                                )
                                VALUES ($1, $2, $3, 'claimed', 1, $4, $5, $5)
                                ON CONFLICT (thread_id, client_submission_id)
                                DO NOTHING
                                RETURNING claim_generation
                                """,
                                thread_id,
                                client_submission_id,
                                user_id,
                                lease,
                                now,
                            )
                        except Exception as exc:
                            self._raise_if_table_missing(exc)
                            raise
                        if inserted is None:
                            # Concurrent winner owns the row — lock and read.
                            raced = await conn.fetchrow(
                                """
                                SELECT status, claim_generation,
                                       user_message_id, assistant_message_id
                                FROM reader_ask_client_submissions
                                WHERE thread_id = $1
                                  AND client_submission_id = $2
                                FOR UPDATE
                                """,
                                thread_id,
                                client_submission_id,
                            )
                            if raced is None:
                                raise RuntimeError(
                                    "submission conflict row vanished"
                                )
                            if (
                                raced["user_message_id"] is not None
                                and raced["assistant_message_id"] is not None
                            ):
                                return {
                                    "may_create_model": False,
                                    "status": raced["status"],
                                    "claim_generation": int(
                                        raced["claim_generation"] or 1
                                    ),
                                    "user_message_id": str(
                                        raced["user_message_id"]
                                    ),
                                    "assistant_message_id": str(
                                        raced["assistant_message_id"]
                                    ),
                                    "terminal_code": f"submission_{raced['status']}",
                                }
                            return {
                                "may_create_model": False,
                                "status": raced["status"],
                                "claim_generation": int(
                                    raced["claim_generation"] or 1
                                ),
                                "terminal_code": "submission_in_progress",
                            }
                        claim_gen = int(inserted["claim_generation"])

                    # Create user + assistant + bind in same transaction.
                    user_row = await conn.fetchrow(
                        """
                        INSERT INTO reader_ask_messages (
                            thread_id, role, status, content_md,
                            context_anchors_json, citations_json,
                            action_proposals_json, tool_trace_json,
                            metadata_json, created_at, updated_at
                        )
                        VALUES (
                            $1, 'user', 'completed', $2,
                            '[]'::jsonb, '[]'::jsonb, '[]'::jsonb, '[]'::jsonb,
                            $3::jsonb, $4, $4
                        )
                        RETURNING id, thread_id, role, status, content_md
                        """,
                        thread_id,
                        content_md,
                        jsonb_param(user_metadata),
                        now,
                    )
                    asst_row = await conn.fetchrow(
                        """
                        INSERT INTO reader_ask_messages (
                            thread_id, role, status, content_md,
                            context_anchors_json, citations_json,
                            action_proposals_json, tool_trace_json,
                            metadata_json, created_at, updated_at
                        )
                        VALUES (
                            $1, 'assistant', 'streaming', '',
                            '[]'::jsonb, '[]'::jsonb, '[]'::jsonb, '[]'::jsonb,
                            $2::jsonb, $3, $3
                        )
                        RETURNING id, thread_id, role, status, content_md
                        """,
                        thread_id,
                        jsonb_param(assistant_metadata),
                        now,
                    )
                    assert user_row is not None and asst_row is not None
                    bound = await conn.fetchrow(
                        """
                        UPDATE reader_ask_client_submissions
                        SET user_message_id = $3,
                            assistant_message_id = $4,
                            status = 'streaming',
                            lease_expires_at = NULL,
                            updated_at = $5
                        WHERE thread_id = $1
                          AND client_submission_id = $2
                          AND claim_generation = $6
                          AND user_message_id IS NULL
                          AND assistant_message_id IS NULL
                        RETURNING claim_generation, status
                        """,
                        thread_id,
                        client_submission_id,
                        user_row["id"],
                        asst_row["id"],
                        now,
                        claim_gen,
                    )
                    if bound is None:
                        # Stale generation — another reclaim won. Do not
                        # leave unbound messages dangling as submission
                        # ownership; fail closed for this owner.
                        raise RuntimeError(
                            "submission bind CAS failed (stale claim_generation)"
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
                    user_msg = _msg_dict(user_row)
                    asst_msg = _msg_dict(asst_row)
                    return {
                        "may_create_model": True,
                        "status": "streaming",
                        "claim_generation": claim_gen,
                        "user_message_id": user_msg["id"],
                        "assistant_message_id": asst_msg["id"],
                        "user_message": user_msg,
                        "assistant_message": asst_msg,
                        "terminal_code": None,
                    }
        except SubmissionIdempotencyUnavailable:
            raise
        except Exception as exc:
            self._raise_if_table_missing(exc)
            raise

    async def mark_client_submission_terminal(
        self,
        *,
        status: str,
        thread_id: UUID | None = None,
        client_submission_id: UUID | None = None,
        assistant_message_id: UUID | None = None,
        claim_generation: int | None = None,
    ) -> int:
        """Mark submission terminal. Returns affected row count (CAS)."""
        if status not in {"completed", "failed", "cancelled"}:
            raise ValueError(f"invalid submission terminal status: {status}")
        pool = self._pool_or_raise()
        now = datetime.now(UTC)
        try:
            async with pool.acquire() as conn:
                if (
                    thread_id is not None
                    and client_submission_id is not None
                ):
                    if claim_generation is not None:
                        result = await conn.execute(
                            """
                            UPDATE reader_ask_client_submissions
                            SET status = $3,
                                lease_expires_at = NULL,
                                updated_at = $4
                            WHERE thread_id = $1
                              AND client_submission_id = $2
                              AND claim_generation = $5
                              AND status IN ('claimed', 'streaming')
                            """,
                            thread_id,
                            client_submission_id,
                            status,
                            now,
                            claim_generation,
                        )
                    else:
                        result = await conn.execute(
                            """
                            UPDATE reader_ask_client_submissions
                            SET status = $3,
                                lease_expires_at = NULL,
                                updated_at = $4
                            WHERE thread_id = $1
                              AND client_submission_id = $2
                              AND status IN ('claimed', 'streaming')
                            """,
                            thread_id,
                            client_submission_id,
                            status,
                            now,
                        )
                elif assistant_message_id is not None:
                    result = await conn.execute(
                        """
                        UPDATE reader_ask_client_submissions
                        SET status = $2,
                            lease_expires_at = NULL,
                            updated_at = $3
                        WHERE assistant_message_id = $1
                          AND status IN ('claimed', 'streaming')
                        """,
                        assistant_message_id,
                        status,
                        now,
                    )
                else:
                    return 0
                # asyncpg returns "UPDATE N"
                try:
                    return int(str(result).split()[-1])
                except (ValueError, IndexError):
                    return 0
        except Exception as exc:
            self._raise_if_table_missing(exc)
            raise

    async def get_client_submission(
        self,
        *,
        thread_id: UUID,
        client_submission_id: UUID,
    ) -> dict[str, Any] | None:
        pool = self._pool_or_raise()
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT thread_id, client_submission_id, user_id,
                           user_message_id, assistant_message_id, status,
                           claim_generation
                    FROM reader_ask_client_submissions
                    WHERE thread_id = $1
                      AND client_submission_id = $2
                    """,
                    thread_id,
                    client_submission_id,
                )
        except Exception as exc:
            self._raise_if_table_missing(exc)
            raise
        if row is None:
            return None
        return {
            "thread_id": str(row["thread_id"]),
            "client_submission_id": str(row["client_submission_id"]),
            "user_message_id": (
                str(row["user_message_id"])
                if row["user_message_id"] is not None
                else None
            ),
            "assistant_message_id": (
                str(row["assistant_message_id"])
                if row["assistant_message_id"] is not None
                else None
            ),
            "status": row["status"],
            "claim_generation": (
                int(row["claim_generation"])
                if row.get("claim_generation") is not None
                else 1
            ),
        }

    # Backward-compatible aliases
    async def claim_client_submission(self, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError(
            "claim_client_submission is removed; use ensure_submission_message_pair"
        )

    async def bind_client_submission_messages(self, **kwargs: Any) -> None:
        raise RuntimeError(
            "bind_client_submission_messages is removed; bind is atomic in ensure"
        )


# Module-level thread seam retained for the RR thread service. These thin
# wrappers keep all SQL in the RR repository while allowing the production
# RR package to avoid importing ``ask_runtime`` or the legacy repository.
def _module_repository() -> ReaderRecordAskRepository:
    return ReaderRecordAskRepository()


async def get_thread(user_id: UUID, thread_id: UUID) -> dict[str, Any] | None:
    return await _module_repository().get_thread(user_id=user_id, thread_id=thread_id)


async def list_reading_record_threads(
    user_id: UUID,
    reading_record_id: UUID,
) -> list[dict[str, Any]]:
    return await _module_repository().list_reading_record_threads(
        user_id=user_id,
        reading_record_id=reading_record_id,
    )


async def get_or_create_default_thread_for_reading_record(
    user_id: UUID,
    reading_record_id: UUID,
    *,
    title: str | None = None,
    selected_model_key: str | None = None,
) -> dict[str, Any]:
    return await _module_repository().get_or_create_default_reading_record_thread(
        user_id=user_id,
        reading_record_id=reading_record_id,
        title=title,
        selected_model_key=selected_model_key,
    )


async def update_thread_selected_model(
    user_id: UUID,
    thread_id: UUID,
    *,
    selected_model_key: str | None,
) -> dict[str, Any] | None:
    return await _module_repository().update_thread_selected_model(
        user_id=user_id,
        thread_id=thread_id,
        selected_model_key=selected_model_key,
    )


async def archive_thread(user_id: UUID, thread_id: UUID) -> dict[str, Any] | None:
    return await _module_repository().archive_thread(
        user_id=user_id,
        thread_id=thread_id,
    )


async def list_messages(
    thread_id: UUID,
    *,
    limit: int | None = 100,
) -> list[dict[str, Any]]:
    return await _module_repository().list_messages(thread_id=thread_id, limit=limit)


async def ensure_record_access(user_id: UUID, record_id: UUID) -> dict[str, Any]:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, title
            FROM analysis_records
            WHERE id = $1 AND user_id = $2 AND deleted_at IS NULL
            """,
            record_id,
            user_id,
        )
    if row is None:
        raise RuntimeError("Reading Record not found")
    return {"id": str(row["id"]), "title": row["title"]}


async def list_threads(user_id: UUID, record_id: UUID) -> list[dict[str, Any]]:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, user_id, analysis_record_id, reading_record_id, title,
                   is_default, selected_model_key, archived_at, created_at,
                   updated_at, last_message_at
            FROM reader_ask_threads
            WHERE user_id = $1 AND analysis_record_id = $2 AND archived_at IS NULL
            ORDER BY is_default DESC, COALESCE(last_message_at, created_at) DESC,
                     created_at DESC
            """,
            user_id,
            record_id,
        )
    return [_thread_row_to_dict(row) for row in rows]


async def get_or_create_default_thread(
    user_id: UUID,
    record_id: UUID,
    *,
    title: str | None = None,
    selected_model_key: str | None = None,
) -> dict[str, Any]:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")
    now = datetime.now(UTC)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO reader_ask_threads (
                user_id, analysis_record_id, title, selected_model_key,
                is_default, created_at, updated_at
            )
            VALUES ($1, $2, $3, $4, TRUE, $5, $5)
            ON CONFLICT (user_id, analysis_record_id)
            WHERE is_default = TRUE AND archived_at IS NULL
            DO UPDATE SET
                title = COALESCE(reader_ask_threads.title, EXCLUDED.title),
                selected_model_key = COALESCE(
                    EXCLUDED.selected_model_key,
                    reader_ask_threads.selected_model_key
                ),
                updated_at = EXCLUDED.updated_at
            RETURNING id, user_id, analysis_record_id, reading_record_id, title,
                      is_default, selected_model_key, archived_at, created_at,
                      updated_at, last_message_at
            """,
            user_id,
            record_id,
            title,
            selected_model_key,
            now,
        )
    assert row is not None
    return _thread_row_to_dict(row)
