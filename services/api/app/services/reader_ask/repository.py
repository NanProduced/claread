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
    return {
        "id": str(row["id"]),
        "thread_id": str(row["thread_id"]),
        "role": row["role"],
        "status": row["status"],
        "content_md": row["content_md"] or "",
        "task_mode": metadata.get("task_mode"),
        "context_anchors": row["context_anchors_json"] or [],
        "citations": row["citations_json"] or [],
        "action_proposals": row["action_proposals_json"] or [],
        "tool_trace": row["tool_trace_json"] or [],
        "response_cards": metadata.get("response_cards") or [],
        "resolved_context": metadata.get("resolved_context"),
        "usage_event_id": str(row["usage_event_id"]) if row.get("usage_event_id") else None,
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


async def list_messages(thread_id: UUID, *, limit: int | None = 50) -> list[dict[str, Any]]:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    async with pool.acquire() as conn:
        if limit is None:
            rows = await conn.fetch(
                """
                SELECT id, thread_id, role, status, content_md,
                       context_anchors_json, citations_json, action_proposals_json, tool_trace_json, metadata_json,
                       usage_event_id, created_at, updated_at
                FROM reader_ask_messages
                WHERE thread_id = $1
                ORDER BY created_at ASC
                """,
                thread_id,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT id, thread_id, role, status, content_md,
                       context_anchors_json, citations_json, action_proposals_json, tool_trace_json, metadata_json,
                       usage_event_id, created_at, updated_at
                FROM reader_ask_messages
                WHERE thread_id = $1
                ORDER BY created_at ASC
                LIMIT $2
                """,
                thread_id,
                limit,
            )
    return [_message_row_to_dict(row) for row in rows]


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
                    metadata_json, usage_event_id, created_at, updated_at
                )
                VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7::jsonb, $8::jsonb, $9::jsonb, $10, $11, $11)
                RETURNING id, thread_id, role, status, content_md,
                          context_anchors_json, citations_json, action_proposals_json, tool_trace_json, metadata_json,
                          usage_event_id, created_at, updated_at
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
    return _message_row_to_dict(row)


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
) -> dict[str, Any]:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    async with pool.acquire() as conn:
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
                usage_event_id = $9
            WHERE id = $1
            RETURNING id, thread_id, role, status, content_md,
                      context_anchors_json, citations_json, action_proposals_json, tool_trace_json, metadata_json,
                      usage_event_id, created_at, updated_at
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
        )
    if row is None:
        raise HTTPException(status_code=404, detail="Reader ask message not found")
    return _message_row_to_dict(row)


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
