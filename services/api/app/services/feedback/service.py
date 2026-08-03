"""
Feedback Service.

Handles CRUD operations for feedback table.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.database import connection as db_connection
from app.database.json_compat import jsonb_param

logger = logging.getLogger("app.services.feedback")

FEEDBACK_LIST_LIMIT = 20


async def submit_feedback(
    user_id: UUID,
    feedback_scope: str,
    target_id: str,
    sentiment: str,
    feedback_type: str,
    content: str | None,
    context_json: dict[str, Any],
    context_summary: str | None,
    client_platform: str,
    client_surface: str | None,
    entry_point: str | None,
    app_version: str | None,
) -> dict[str, Any]:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    now = datetime.now(UTC)

    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                INSERT INTO feedback (
                    user_id, feedback_scope, target_id,
                    sentiment, feedback_type,
                    content, context_json,
                    context_summary, app_version, client_platform,
                    client_surface, entry_point, created_at, updated_at
                ) VALUES (
                    $1, $2, $3, $4, $5,
                    $6, $7, $8, $9, $10,
                    $11, $12, $13, $13
                )
                RETURNING id, feedback_scope, target_id, sentiment, feedback_type,
                          client_platform, client_surface, entry_point,
                          context_summary, status, created_at
                """,
                user_id,
                feedback_scope,
                target_id,
                sentiment,
                feedback_type,
                content,
                jsonb_param(context_json),
                context_summary,
                app_version,
                client_platform,
                client_surface,
                entry_point,
                now,
            )
            if row is None:
                raise RuntimeError("Failed to insert feedback")
            logger.info(
                "Feedback %s from user %s (scope=%s, type=%s, platform=%s, surface=%s, entry=%s)",
                row["id"], user_id, feedback_scope, feedback_type, client_platform, client_surface, entry_point,
            )
            return dict(row)


async def list_user_feedback(
    user_id: UUID,
    cursor: str | None = None,
    limit: int = FEEDBACK_LIST_LIMIT,
    feedback_scope: str | None = None,
    client_platform: str | None = None,
    client_surface: str | None = None,
    status: str | None = None,
) -> tuple[list[dict], str | None, bool]:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    params: list[Any] = [user_id]
    where_clauses = ["user_id = $1"]

    if cursor:
        params.append(UUID(cursor))
        where_clauses.append(f"id < ${len(params)}")

    if feedback_scope:
        params.append(feedback_scope)
        where_clauses.append(f"feedback_scope = ${len(params)}")

    if client_platform:
        params.append(client_platform)
        where_clauses.append(f"client_platform = ${len(params)}")

    if client_surface:
        params.append(client_surface)
        where_clauses.append(f"client_surface = ${len(params)}")

    if status:
        params.append(status)
        where_clauses.append(f"status = ${len(params)}")

    limit_val = min(limit, 100)
    params.append(limit_val + 1)
    query_limit = f"${len(params)}"

    where_sql = " AND ".join(where_clauses)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id, feedback_scope, feedback_type, sentiment, content,
                   context_summary, client_platform, client_surface,
                   entry_point, admin_note, status, reward_points, created_at
            FROM feedback
            WHERE {where_sql}
            ORDER BY created_at DESC
            LIMIT {query_limit}
            """,
            *params,
        )

    items = [dict(r) for r in rows[:limit_val]]
    has_more = len(rows) > limit_val
    next_cursor = str(items[-1]["id"]) if items and has_more else None

    return items, next_cursor, has_more


async def delete_feedback(
    user_id: UUID,
    feedback_id: UUID,
) -> bool:
    pool = db_connection.DB_POOL
    if pool is None:
        return False

    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            DELETE FROM feedback
            WHERE id = $1 AND user_id = $2 AND status = 'pending'
            """,
            feedback_id,
            user_id,
        )
        deleted = result == "DELETE 1"
        if deleted:
            logger.info("Feedback %s deleted by user %s", feedback_id, user_id)
        return deleted


async def update_feedback_status(
    feedback_id: UUID,
    status: str,
    admin_note: str | None,
    reviewed_by: UUID | None,
) -> dict[str, Any] | None:
    pool = db_connection.DB_POOL
    if pool is None:
        return None

    now = datetime.now(UTC)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE feedback
            SET status = $2,
                admin_note = COALESCE($3, feedback.admin_note),
                reviewed_by = $4,
                reviewed_at = $5,
                updated_at = $5
            WHERE id = $1
            RETURNING id, user_id, feedback_scope, status, reward_points
            """,
            feedback_id,
            status,
            admin_note,
            reviewed_by,
            now,
        )
        if row:
            logger.info(
                "Feedback %s status -> %s by admin %s",
                feedback_id, status, reviewed_by,
            )
        return dict(row) if row else None


async def reward_feedback(
    feedback_id: UUID,
    points: int,
) -> dict[str, Any] | None:
    pool = db_connection.DB_POOL
    if pool is None:
        return None

    now = datetime.now(UTC)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE feedback
            SET status = 'adopted',
                reward_points = $2,
                reward_granted_at = $3,
                updated_at = $3
            WHERE id = $1 AND status != 'adopted'
            RETURNING id, user_id, reward_points, reward_granted_at
            """,
            feedback_id,
            points,
            now,
        )
        if row:
            logger.info(
                "Feedback %s rewarded %d points for user %s",
                feedback_id, points, row["user_id"],
            )
        return dict(row) if row else None


async def get_feedback_stats() -> dict[str, Any]:
    pool = db_connection.DB_POOL
    if pool is None:
        return {}

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE status = 'pending') AS pending,
                COUNT(*) FILTER (WHERE status = 'adopted') AS adopted,
                COUNT(*) FILTER (WHERE status = 'resolved') AS resolved,
                COUNT(*) FILTER (WHERE status = 'dismissed') AS dismissed,
                COUNT(*) FILTER (WHERE feedback_scope = 'sentence') AS sentence_count,
                COUNT(*) FILTER (WHERE feedback_scope = 'dictionary') AS dictionary_count,
                COUNT(*) FILTER (WHERE feedback_scope = 'app') AS app_count,
                SUM(reward_points) FILTER (WHERE status = 'adopted') AS total_rewarded
            FROM feedback
            """
        )

    return dict(row) if row else {}
