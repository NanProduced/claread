"""
Feedback Service.

Handles CRUD operations for feedback table.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.database import connection as db_connection

logger = logging.getLogger("app.services.feedback")

FEEDBACK_LIST_LIMIT = 20


async def submit_feedback(
    user_id: UUID,
    feedback_scope: str,
    target_id: str,
    analysis_record_id: UUID | None,
    sentiment: str,
    feedback_type: str,
    annotation_type: str | None,
    content: str | None,
    context_json: dict[str, Any],
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
                    analysis_record_id, sentiment, feedback_type,
                    annotation_type, content, context_json,
                    app_version, created_at, updated_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $11)
                ON CONFLICT (user_id, target_id, feedback_type) DO UPDATE SET
                    sentiment = EXCLUDED.sentiment,
                    content = COALESCE(EXCLUDED.content, feedback.content),
                    context_json = EXCLUDED.context_json,
                    annotation_type = COALESCE(EXCLUDED.annotation_type, feedback.annotation_type),
                    updated_at = $11
                RETURNING id, feedback_scope, target_id, sentiment, feedback_type,
                          status, created_at
                """,
                user_id,
                feedback_scope,
                target_id,
                analysis_record_id,
                sentiment,
                feedback_type,
                annotation_type,
                content,
                json.dumps(context_json),
                app_version,
                now,
            )
            if row is None:
                raise RuntimeError("Failed to upsert feedback")
            logger.info(
                "Feedback %s from user %s (scope=%s, type=%s)",
                row["id"], user_id, feedback_scope, feedback_type,
            )
            return dict(row)


async def list_user_feedback(
    user_id: UUID,
    cursor: str | None = None,
    limit: int = FEEDBACK_LIST_LIMIT,
    feedback_scope: str | None = None,
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

    limit_val = min(limit, 100)
    params.append(limit_val + 1)
    query_limit = f"${len(params)}"

    where_sql = " AND ".join(where_clauses)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id, feedback_scope, feedback_type, sentiment, content,
                   status, reward_points, created_at
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
                COUNT(*) FILTER (WHERE feedback_scope = 'analysis_result') AS result_count,
                COUNT(*) FILTER (WHERE feedback_scope = 'annotation') AS annotation_count,
                COUNT(*) FILTER (WHERE feedback_scope = 'sentence') AS sentence_count,
                COUNT(*) FILTER (WHERE feedback_scope = 'dictionary') AS dictionary_count,
                COUNT(*) FILTER (WHERE feedback_scope = 'app') AS app_count,
                SUM(reward_points) FILTER (WHERE status = 'adopted') AS total_rewarded
            FROM feedback
            """
        )

    return dict(row) if row else {}
