"""Persistence seam for agentic Reading Record Ask turns.

Owns SQL against ``reader_ask_*`` tables for the agentic lane only.
Does not import ``app.services.reader_ask.repository``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.database import connection as db_connection
from app.database.json_compat import jsonb_param
from app.schemas.reader_record_ask_stream import EXECUTION_VERSION_AGENTIC_V2


class ReaderRecordAskRepository:
    """DB access for agentic message / turn-run rows."""

    def __init__(self, *, pool: Any | None = None) -> None:
        self._pool = pool

    def _pool_or_raise(self) -> Any:
        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("Database pool not initialized")
        return pool

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
        pool = self._pool_or_raise()
        now = datetime.now(UTC)
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
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
                    WHERE id = $1
                    RETURNING id, status, final_status, user_visible_output_json,
                              resolved_evidence_json, reasoning_projection_json,
                              envelope_fingerprint, execution_version
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
                await conn.execute(
                    """
                    UPDATE reader_ask_messages
                    SET status = 'completed',
                        content_md = $2,
                        current_turn_run_id = $3,
                        updated_at = $4
                    WHERE id = $1
                    """,
                    message_id,
                    answer_text,
                    turn_run_id,
                    now,
                )
        assert row is not None
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
        """
        pool = self._pool_or_raise()
        now = datetime.now(UTC)
        async with pool.acquire() as conn:
            async with conn.transaction():
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
                    WHERE id = $1
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
                await conn.execute(
                    """
                    UPDATE reader_ask_messages
                    SET status = 'failed',
                        content_md = '',
                        current_turn_run_id = $2,
                        updated_at = $3
                    WHERE id = $1
                    """,
                    message_id,
                    turn_run_id,
                    now,
                )
        assert row is not None
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
                SELECT id, thread_id, role, status, content_md, created_at
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
        user_msg = {
            "id": str(user_row["id"]),
            "thread_id": str(user_row["thread_id"]),
            "role": user_row["role"],
            "status": user_row["status"],
            "content_md": user_row["content_md"],
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
