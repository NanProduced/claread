from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException

from app.database import connection as db_connection


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat()


def _message_row_to_dict(row: Any) -> dict[str, Any]:
    metadata = row["metadata_json"] or {}
    hydrated_output = row.get("user_visible_output_json")
    if not isinstance(hydrated_output, dict):
        hydrated_output = None
    visible = hydrated_output or {}
    resolved_intent = visible.get("resolved_intent", metadata.get("resolved_intent"))
    content_md = visible.get("content_md", row["content_md"] or "")
    citations = visible.get("citations", row["citations_json"] or [])
    action_proposals = visible.get("action_proposals", row["action_proposals_json"] or [])
    tool_trace = visible.get("tool_trace", row["tool_trace_json"] or [])
    evidence = visible.get("evidence", metadata.get("evidence") or [])
    trace_summary = visible.get("trace_summary", metadata.get("trace_summary"))
    response_cards = visible.get("response_cards", metadata.get("response_cards") or [])
    resolved_context = visible.get("resolved_context", metadata.get("resolved_context"))
    context_plan = visible.get("context_plan", metadata.get("context_plan"))
    resolved_context_input = visible.get("resolved_context_input", metadata.get("resolved_context_input"))
    run_info = visible.get("run_info", metadata.get("run_info"))
    supplement_candidates = visible.get("supplement_candidates", metadata.get("supplement_candidates") or [])
    persisted_supplements = visible.get("persisted_supplements", metadata.get("persisted_supplements") or [])
    usage_event_id = (
        str(row["current_turn_run_usage_event_id"])
        if row.get("current_turn_run_usage_event_id")
        else str(row["usage_event_id"])
        if row.get("usage_event_id")
        else None
    )
    return {
        "id": str(row["id"]),
        "thread_id": str(row["thread_id"]),
        "role": row["role"],
        "status": row["status"],
        "content_md": content_md,
        "resolved_intent": resolved_intent,
        "context_anchors": row["context_anchors_json"] or [],
        "citations": citations,
        "action_proposals": action_proposals,
        "tool_trace": tool_trace,
        "evidence": evidence,
        "trace_summary": trace_summary,
        "response_cards": response_cards,
        "resolved_context": resolved_context,
        "context_plan": context_plan,
        "resolved_context_input": resolved_context_input,
        "run_info": run_info,
        "run_history": metadata.get("run_history") or [],
        "supplement_candidates": supplement_candidates,
        "persisted_supplements": persisted_supplements,
        "usage_event_id": usage_event_id,
        "current_turn_run_id": str(row["current_turn_run_id"]) if row.get("current_turn_run_id") else None,
        "current_turn_run": _turn_run_row_to_dict(row) if row.get("current_turn_run_id") else None,
        "current_user_visible_output": hydrated_output,
        "current_eval_trace": row.get("current_eval_trace_json") or None,
        "created_at": _iso(row["created_at"]),
        "updated_at": _iso(row["updated_at"]),
    }


def _thread_row_to_dict(row: Any) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "record_id": str(row["record_id"]),
        "title": row["title"],
        "is_default": bool(row["is_default"]),
        "archived_at": _iso(row["archived_at"]),
        "created_at": _iso(row["created_at"]),
        "updated_at": _iso(row["updated_at"]),
        "last_message_at": _iso(row["last_message_at"]),
    }


def _turn_run_row_to_dict(row: Any) -> dict[str, Any]:
    return {
        "id": str(row["current_turn_run_id"]),
        "message_id": str(row["id"]),
        "thread_id": str(row["thread_id"]),
        "user_id": str(row["current_turn_run_user_id"]) if row.get("current_turn_run_user_id") else None,
        "record_id": str(row["current_turn_run_record_id"]) if row.get("current_turn_run_record_id") else None,
        "turn_id": str(row["current_turn_run_turn_id"]) if row.get("current_turn_run_turn_id") else None,
        "run_attempt": int(row["current_turn_run_attempt"]) if row.get("current_turn_run_attempt") is not None else 1,
        "supersedes_run_id": str(row["current_turn_run_supersedes_run_id"])
        if row.get("current_turn_run_supersedes_run_id")
        else None,
        "status": row["current_turn_run_status"],
        "resolved_intent": row.get("current_turn_run_resolved_intent"),
        "user_visible_output_json": row.get("user_visible_output_json"),
        "usage_summary_json": row.get("usage_summary_json"),
        "usage_event_id": str(row["current_turn_run_usage_event_id"]) if row.get("current_turn_run_usage_event_id") else None,
        "started_at": _iso(row["current_turn_run_started_at"]),
        "completed_at": _iso(row["current_turn_run_completed_at"]),
        "failed_at": _iso(row["current_turn_run_failed_at"]),
        "created_at": _iso(row["current_turn_run_created_at"]),
        "updated_at": _iso(row["current_turn_run_updated_at"]),
    }


def _eval_trace_row_to_dict(row: Any) -> dict[str, Any] | None:
    turn_run_id = row.get("eval_trace_turn_run_id")
    if turn_run_id is None:
        return None
    return {
        "turn_run_id": str(turn_run_id),
        "trace_schema_version": row["trace_schema_version"],
        "planning_snapshot_json": row["planning_snapshot_json"] or {},
        "capability_trace_json": row["capability_trace_json"] or {},
        "action_audit_json": row["action_audit_json"] or [],
        "supplement_audit_json": row["supplement_audit_json"] or [],
        "metrics_json": row["metrics_json"] or {},
        "created_at": _iso(row["eval_trace_created_at"]),
        "updated_at": _iso(row["eval_trace_updated_at"]),
    }


_MESSAGE_SELECT = """
SELECT m.id, m.thread_id, m.role, m.status, m.content_md,
       m.context_anchors_json, m.citations_json, m.action_proposals_json, m.tool_trace_json, m.metadata_json,
       m.current_turn_run_id, m.usage_event_id, m.created_at, m.updated_at,
       tr.user_id AS current_turn_run_user_id,
       tr.record_id AS current_turn_run_record_id,
       tr.turn_id AS current_turn_run_turn_id,
       tr.run_attempt AS current_turn_run_attempt,
       tr.supersedes_run_id AS current_turn_run_supersedes_run_id,
       tr.status AS current_turn_run_status,
       tr.resolved_intent AS current_turn_run_resolved_intent,
       tr.user_visible_output_json,
       tr.usage_summary_json,
       tr.usage_event_id AS current_turn_run_usage_event_id,
       tr.started_at AS current_turn_run_started_at,
       tr.completed_at AS current_turn_run_completed_at,
       tr.failed_at AS current_turn_run_failed_at,
       tr.created_at AS current_turn_run_created_at,
       tr.updated_at AS current_turn_run_updated_at,
       et.turn_run_id AS eval_trace_turn_run_id,
       et.trace_schema_version,
       et.planning_snapshot_json,
       et.capability_trace_json,
       et.action_audit_json,
       et.supplement_audit_json,
       et.metrics_json,
       et.created_at AS eval_trace_created_at,
       et.updated_at AS eval_trace_updated_at
FROM reader_ask_messages m
LEFT JOIN reader_ask_turn_runs tr ON tr.id = m.current_turn_run_id
LEFT JOIN reader_ask_eval_traces et ON et.turn_run_id = tr.id
"""


async def ensure_record_access(user_id: UUID, record_id: UUID) -> dict[str, Any]:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, title, source_text
            FROM analysis_records
            WHERE id = $1 AND user_id = $2 AND deleted_at IS NULL
            """,
            record_id,
            user_id,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="Analysis record not found")
    return {
        "id": str(row["id"]),
        "title": row["title"],
        "source_text": row["source_text"] or "",
    }


async def search_records_by_title(
    user_id: UUID,
    *,
    query: str,
    exclude_record_id: UUID | None = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    normalized_query = query.strip()
    if not normalized_query:
        return []

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, title, updated_at
            FROM analysis_records
            WHERE user_id = $1
              AND deleted_at IS NULL
              AND ($2::uuid IS NULL OR id <> $2)
              AND title IS NOT NULL
              AND title ILIKE $3
            ORDER BY updated_at DESC
            LIMIT $4
            """,
            user_id,
            exclude_record_id,
            f"%{normalized_query}%",
            limit,
        )
    return [
        {
            "id": str(row["id"]),
            "title": row["title"],
            "updated_at": _iso(row["updated_at"]),
        }
        for row in rows
    ]


async def list_threads(user_id: UUID, record_id: UUID) -> list[dict[str, Any]]:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, record_id, title, is_default, archived_at, created_at, updated_at, last_message_at
            FROM reader_ask_threads
            WHERE user_id = $1 AND record_id = $2 AND archived_at IS NULL
            ORDER BY is_default DESC, COALESCE(last_message_at, created_at) DESC, created_at DESC
            """,
            user_id,
            record_id,
        )
    return [_thread_row_to_dict(row) for row in rows]


async def get_thread(user_id: UUID, thread_id: UUID) -> dict[str, Any] | None:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, record_id, title, is_default, archived_at, created_at, updated_at, last_message_at
            FROM reader_ask_threads
            WHERE id = $1 AND user_id = $2 AND archived_at IS NULL
            """,
            thread_id,
            user_id,
        )
    return _thread_row_to_dict(row) if row else None


async def get_or_create_default_thread(
    user_id: UUID,
    record_id: UUID,
    *,
    title: str | None = None,
) -> dict[str, Any]:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    now = datetime.now(UTC)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO reader_ask_threads (user_id, record_id, title, is_default, created_at, updated_at)
            VALUES ($1, $2, $3, TRUE, $4, $4)
            ON CONFLICT (user_id, record_id)
            WHERE is_default = TRUE AND archived_at IS NULL
            DO UPDATE SET
                title = COALESCE(reader_ask_threads.title, EXCLUDED.title),
                updated_at = EXCLUDED.updated_at
            RETURNING id, record_id, title, is_default, archived_at, created_at, updated_at, last_message_at
            """,
            user_id,
            record_id,
            title,
            now,
        )
    assert row is not None
    return _thread_row_to_dict(row)


async def create_new_chat_thread(
    user_id: UUID,
    record_id: UUID,
    *,
    title: str | None = None,
) -> dict[str, Any]:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    now = datetime.now(UTC)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO reader_ask_threads (user_id, record_id, title, is_default, created_at, updated_at)
            VALUES ($1, $2, $3, FALSE, $4, $4)
            RETURNING id, record_id, title, is_default, archived_at, created_at, updated_at, last_message_at
            """,
            user_id,
            record_id,
            title,
            now,
        )
    assert row is not None
    return _thread_row_to_dict(row)


async def archive_thread(user_id: UUID, thread_id: UUID) -> dict[str, Any] | None:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    now = datetime.now(UTC)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE reader_ask_threads
            SET archived_at = $3, updated_at = $3
            WHERE id = $1 AND user_id = $2 AND archived_at IS NULL
            RETURNING id, record_id, title, is_default, archived_at, created_at, updated_at, last_message_at
            """,
            thread_id,
            user_id,
            now,
        )
    return _thread_row_to_dict(row) if row else None


async def list_messages(thread_id: UUID, *, limit: int | None = 50) -> list[dict[str, Any]]:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    async with pool.acquire() as conn:
        if limit is None:
            rows = await conn.fetch(
                _MESSAGE_SELECT
                + """
                WHERE m.thread_id = $1
                ORDER BY m.created_at ASC
                """,
                thread_id,
            )
        else:
            rows = await conn.fetch(
                _MESSAGE_SELECT
                + """
                WHERE m.thread_id = $1
                ORDER BY m.created_at ASC
                LIMIT $2
                """,
                thread_id,
                limit,
            )
    return [_message_row_to_dict(row) for row in rows]


async def get_message(message_id: UUID) -> dict[str, Any] | None:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            _MESSAGE_SELECT
            + """
            WHERE m.id = $1
            """,
            message_id,
        )
    return _message_row_to_dict(row) if row else None


async def create_message(
    *,
    thread_id: UUID,
    role: str,
    status: str,
    content_md: str,
    context_anchors: list[dict[str, Any]] | None = None,
    citations: list[dict[str, Any]] | None = None,
    action_proposals: list[dict[str, Any]] | None = None,
    tool_trace: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
    usage_event_id: UUID | None = None,
    current_turn_run_id: UUID | None = None,
) -> dict[str, Any]:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    now = datetime.now(UTC)
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                INSERT INTO reader_ask_messages (
                    thread_id, role, status, content_md,
                    context_anchors_json, citations_json, action_proposals_json, tool_trace_json,
                    metadata_json, usage_event_id, current_turn_run_id, created_at, updated_at
                )
                VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7::jsonb, $8::jsonb, $9::jsonb, $10, $11, $12, $12)
                RETURNING id
                """,
                thread_id,
                role,
                status,
                content_md,
                context_anchors or [],
                citations or [],
                action_proposals or [],
                tool_trace or [],
                metadata or {},
                usage_event_id,
                current_turn_run_id,
                now,
            )
            assert row is not None
            await conn.execute(
                """
                UPDATE reader_ask_threads
                SET last_message_at = $2, updated_at = $2
                WHERE id = $1
                """,
                thread_id,
                now,
            )
    return await get_message(UUID(str(row["id"])))


async def update_message(
    *,
    message_id: UUID,
    status: str,
    content_md: str,
    context_anchors: list[dict[str, Any]] | None = None,
    citations: list[dict[str, Any]] | None = None,
    action_proposals: list[dict[str, Any]] | None = None,
    tool_trace: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
    usage_event_id: UUID | None = None,
    current_turn_run_id: UUID | None = None,
) -> dict[str, Any]:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    now = datetime.now(UTC)
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                UPDATE reader_ask_messages
                SET status = $2,
                    content_md = $3,
                    context_anchors_json = $4::jsonb,
                    citations_json = $5::jsonb,
                    action_proposals_json = $6::jsonb,
                    tool_trace_json = $7::jsonb,
                    metadata_json = $8::jsonb,
                    usage_event_id = $9,
                    current_turn_run_id = COALESCE($10, current_turn_run_id),
                    updated_at = $11
                WHERE id = $1
                RETURNING id, thread_id
                """,
                message_id,
                status,
                content_md,
                context_anchors or [],
                citations or [],
                action_proposals or [],
                tool_trace or [],
                metadata or {},
                usage_event_id,
                current_turn_run_id,
                now,
            )
            if row is not None:
                await conn.execute(
                    """
                    UPDATE reader_ask_threads
                    SET updated_at = $2,
                        last_message_at = GREATEST(COALESCE(last_message_at, $2), $2)
                    WHERE id = $1
                    """,
                    row["thread_id"],
                    now,
                )
    if row is None:
        raise HTTPException(status_code=404, detail="Reader ask message not found")
    return await get_message(message_id)


async def find_action_proposal(
    *,
    user_id: UUID,
    thread_id: UUID,
    action_id: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    thread = await get_thread(user_id, thread_id)
    if thread is None:
        return None, None

    messages = await list_messages(thread_id, limit=None)
    for message in reversed(messages):
        for proposal in message["action_proposals"]:
            if proposal.get("id") == action_id:
                return message, proposal
    return None, None


async def create_turn_run(
    *,
    message_id: UUID,
    thread_id: UUID,
    user_id: UUID,
    record_id: UUID,
    turn_id: UUID,
    run_attempt: int,
    supersedes_run_id: UUID | None,
    status: str,
    resolved_intent: str | None,
) -> dict[str, Any]:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    now = datetime.now(UTC)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO reader_ask_turn_runs (
                message_id, thread_id, user_id, record_id, turn_id,
                run_attempt, supersedes_run_id, status, resolved_intent,
                started_at, created_at, updated_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $10, $10)
            RETURNING id, message_id, thread_id, user_id, record_id, turn_id, run_attempt,
                      supersedes_run_id, status, resolved_intent, user_visible_output_json,
                      usage_summary_json, usage_event_id, started_at, completed_at, failed_at,
                      created_at, updated_at
            """,
            message_id,
            thread_id,
            user_id,
            record_id,
            turn_id,
            run_attempt,
            supersedes_run_id,
            status,
            resolved_intent,
            now,
        )
    assert row is not None
    return {
        "id": str(row["id"]),
        "message_id": str(row["message_id"]),
        "thread_id": str(row["thread_id"]),
        "user_id": str(row["user_id"]),
        "record_id": str(row["record_id"]),
        "turn_id": str(row["turn_id"]),
        "run_attempt": int(row["run_attempt"]),
        "supersedes_run_id": str(row["supersedes_run_id"]) if row.get("supersedes_run_id") else None,
        "status": row["status"],
        "resolved_intent": row["resolved_intent"],
        "user_visible_output_json": row["user_visible_output_json"],
        "usage_summary_json": row["usage_summary_json"],
        "usage_event_id": str(row["usage_event_id"]) if row.get("usage_event_id") else None,
        "started_at": _iso(row["started_at"]),
        "completed_at": _iso(row["completed_at"]),
        "failed_at": _iso(row["failed_at"]),
        "created_at": _iso(row["created_at"]),
        "updated_at": _iso(row["updated_at"]),
    }


async def get_turn_run(turn_run_id: UUID) -> dict[str, Any] | None:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, message_id, thread_id, user_id, record_id, turn_id, run_attempt,
                   supersedes_run_id, status, resolved_intent, user_visible_output_json,
                   usage_summary_json, usage_event_id, started_at, completed_at, failed_at,
                   created_at, updated_at
            FROM reader_ask_turn_runs
            WHERE id = $1
            """,
            turn_run_id,
        )
    if row is None:
        return None
    return {
        "id": str(row["id"]),
        "message_id": str(row["message_id"]),
        "thread_id": str(row["thread_id"]),
        "user_id": str(row["user_id"]),
        "record_id": str(row["record_id"]),
        "turn_id": str(row["turn_id"]),
        "run_attempt": int(row["run_attempt"]),
        "supersedes_run_id": str(row["supersedes_run_id"]) if row.get("supersedes_run_id") else None,
        "status": row["status"],
        "resolved_intent": row["resolved_intent"],
        "user_visible_output_json": row["user_visible_output_json"],
        "usage_summary_json": row["usage_summary_json"],
        "usage_event_id": str(row["usage_event_id"]) if row.get("usage_event_id") else None,
        "started_at": _iso(row["started_at"]),
        "completed_at": _iso(row["completed_at"]),
        "failed_at": _iso(row["failed_at"]),
        "created_at": _iso(row["created_at"]),
        "updated_at": _iso(row["updated_at"]),
    }


async def update_turn_run(
    *,
    turn_run_id: UUID,
    status: str,
    resolved_intent: str | None = None,
    user_visible_output_json: dict[str, Any] | None = None,
    usage_summary_json: dict[str, Any] | None = None,
    usage_event_id: UUID | None = None,
    completed_at: datetime | None = None,
    failed_at: datetime | None = None,
) -> dict[str, Any]:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    now = datetime.now(UTC)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE reader_ask_turn_runs
            SET status = $2,
                resolved_intent = COALESCE($3, resolved_intent),
                user_visible_output_json = COALESCE($4::jsonb, user_visible_output_json),
                usage_summary_json = COALESCE($5::jsonb, usage_summary_json),
                usage_event_id = COALESCE($6, usage_event_id),
                completed_at = COALESCE($7, completed_at),
                failed_at = COALESCE($8, failed_at),
                updated_at = $9
            WHERE id = $1
            RETURNING id, message_id, thread_id, user_id, record_id, turn_id, run_attempt,
                      supersedes_run_id, status, resolved_intent, user_visible_output_json,
                      usage_summary_json, usage_event_id, started_at, completed_at, failed_at,
                      created_at, updated_at
            """,
            turn_run_id,
            status,
            resolved_intent,
            user_visible_output_json,
            usage_summary_json,
            usage_event_id,
            completed_at,
            failed_at,
            now,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="Reader ask turn run not found")
    return {
        "id": str(row["id"]),
        "message_id": str(row["message_id"]),
        "thread_id": str(row["thread_id"]),
        "user_id": str(row["user_id"]),
        "record_id": str(row["record_id"]),
        "turn_id": str(row["turn_id"]),
        "run_attempt": int(row["run_attempt"]),
        "supersedes_run_id": str(row["supersedes_run_id"]) if row.get("supersedes_run_id") else None,
        "status": row["status"],
        "resolved_intent": row["resolved_intent"],
        "user_visible_output_json": row["user_visible_output_json"],
        "usage_summary_json": row["usage_summary_json"],
        "usage_event_id": str(row["usage_event_id"]) if row.get("usage_event_id") else None,
        "started_at": _iso(row["started_at"]),
        "completed_at": _iso(row["completed_at"]),
        "failed_at": _iso(row["failed_at"]),
        "created_at": _iso(row["created_at"]),
        "updated_at": _iso(row["updated_at"]),
    }


async def list_turn_runs_for_message(message_id: UUID) -> list[dict[str, Any]]:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, message_id, thread_id, user_id, record_id, turn_id, run_attempt,
                   supersedes_run_id, status, resolved_intent, user_visible_output_json,
                   usage_summary_json, usage_event_id, started_at, completed_at, failed_at,
                   created_at, updated_at
            FROM reader_ask_turn_runs
            WHERE message_id = $1
            ORDER BY run_attempt ASC, created_at ASC
            """,
            message_id,
        )
    return [
        {
            "id": str(row["id"]),
            "message_id": str(row["message_id"]),
            "thread_id": str(row["thread_id"]),
            "user_id": str(row["user_id"]),
            "record_id": str(row["record_id"]),
            "turn_id": str(row["turn_id"]),
            "run_attempt": int(row["run_attempt"]),
            "supersedes_run_id": str(row["supersedes_run_id"]) if row.get("supersedes_run_id") else None,
            "status": row["status"],
            "resolved_intent": row["resolved_intent"],
            "user_visible_output_json": row["user_visible_output_json"],
            "usage_summary_json": row["usage_summary_json"],
            "usage_event_id": str(row["usage_event_id"]) if row.get("usage_event_id") else None,
            "started_at": _iso(row["started_at"]),
            "completed_at": _iso(row["completed_at"]),
            "failed_at": _iso(row["failed_at"]),
            "created_at": _iso(row["created_at"]),
            "updated_at": _iso(row["updated_at"]),
        }
        for row in rows
    ]


async def get_eval_trace(turn_run_id: UUID) -> dict[str, Any] | None:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT turn_run_id, trace_schema_version, planning_snapshot_json, capability_trace_json,
                   action_audit_json, supplement_audit_json, metrics_json, created_at, updated_at
            FROM reader_ask_eval_traces
            WHERE turn_run_id = $1
            """,
            turn_run_id,
        )
    if row is None:
        return None
    return {
        "turn_run_id": str(row["turn_run_id"]),
        "trace_schema_version": row["trace_schema_version"],
        "planning_snapshot_json": row["planning_snapshot_json"] or {},
        "capability_trace_json": row["capability_trace_json"] or {},
        "action_audit_json": row["action_audit_json"] or [],
        "supplement_audit_json": row["supplement_audit_json"] or [],
        "metrics_json": row["metrics_json"] or {},
        "created_at": _iso(row["created_at"]),
        "updated_at": _iso(row["updated_at"]),
    }


async def upsert_eval_trace(
    *,
    turn_run_id: UUID,
    trace_schema_version: str,
    planning_snapshot_json: dict[str, Any] | None = None,
    capability_trace_json: dict[str, Any] | None = None,
    action_audit_json: list[dict[str, Any]] | None = None,
    supplement_audit_json: list[dict[str, Any]] | None = None,
    metrics_json: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    now = datetime.now(UTC)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO reader_ask_eval_traces (
                turn_run_id, trace_schema_version, planning_snapshot_json, capability_trace_json,
                action_audit_json, supplement_audit_json, metrics_json, created_at, updated_at
            )
            VALUES ($1, $2, $3::jsonb, $4::jsonb, $5::jsonb, $6::jsonb, $7::jsonb, $8, $8)
            ON CONFLICT (turn_run_id)
            DO UPDATE SET
                trace_schema_version = EXCLUDED.trace_schema_version,
                planning_snapshot_json = EXCLUDED.planning_snapshot_json,
                capability_trace_json = EXCLUDED.capability_trace_json,
                action_audit_json = EXCLUDED.action_audit_json,
                supplement_audit_json = EXCLUDED.supplement_audit_json,
                metrics_json = EXCLUDED.metrics_json,
                updated_at = EXCLUDED.updated_at
            RETURNING turn_run_id, trace_schema_version, planning_snapshot_json, capability_trace_json,
                      action_audit_json, supplement_audit_json, metrics_json, created_at, updated_at
            """,
            turn_run_id,
            trace_schema_version,
            planning_snapshot_json or {},
            capability_trace_json or {},
            action_audit_json or [],
            supplement_audit_json or [],
            metrics_json or {},
            now,
        )
    assert row is not None
    return {
        "turn_run_id": str(row["turn_run_id"]),
        "trace_schema_version": row["trace_schema_version"],
        "planning_snapshot_json": row["planning_snapshot_json"] or {},
        "capability_trace_json": row["capability_trace_json"] or {},
        "action_audit_json": row["action_audit_json"] or [],
        "supplement_audit_json": row["supplement_audit_json"] or [],
        "metrics_json": row["metrics_json"] or {},
        "created_at": _iso(row["created_at"]),
        "updated_at": _iso(row["updated_at"]),
    }
