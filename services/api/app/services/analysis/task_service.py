"""
Analysis Task Service.

Handles task creation (with single-active-task control),
status queries, and record+task lifecycle management.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from app.database import connection as db_connection

logger = logging.getLogger(__name__)


def compute_source_text_hash(text: str) -> str:
    """Simple hash used for analysis_records.source_text_hash."""
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]


class TaskSubmitResult:
    """Result of submit_task."""

    __slots__ = ("task_id", "record_id", "client_record_id", "status", "created")

    def __init__(
        self,
        task_id: UUID,
        record_id: UUID,
        client_record_id: str,
        status: str,
        created: bool,
    ) -> None:
        self.task_id = task_id
        self.record_id = record_id
        self.client_record_id = client_record_id
        self.status = status
        self.created = created


class TaskExecutionPayload:
    """Claimed task payload needed by the worker to execute analysis."""

    __slots__ = (
        "task_id",
        "record_id",
        "user_id",
        "text",
        "reading_goal",
        "reading_variant",
        "source_type",
        "extended",
        "worker_token",
    )

    def __init__(
        self,
        *,
        task_id: UUID,
        record_id: UUID,
        user_id: UUID,
        text: str,
        reading_goal: str,
        reading_variant: str,
        source_type: str,
        extended: bool,
        worker_token: str,
    ) -> None:
        self.task_id = task_id
        self.record_id = record_id
        self.user_id = user_id
        self.text = text
        self.reading_goal = reading_goal
        self.reading_variant = reading_variant
        self.source_type = source_type
        self.extended = extended
        self.worker_token = worker_token


class ActiveTaskConflict(Exception):
    """Raised when user already has an active task."""

    def __init__(self, task_id: UUID, record_id: UUID, status: str) -> None:
        self.task_id = task_id
        self.record_id = record_id
        self.status = status
        super().__init__(f"Active task exists: {task_id} ({status})")


async def submit_task(
    *,
    user_id: UUID,
    text: str,
    reading_goal: str,
    reading_variant: str,
    source_type: str,
    extended: bool,
    client_record_id: str | None = None,
) -> TaskSubmitResult:
    """
    Submit an analysis task with single-active-task control.
    """
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    source_text_hash = compute_source_text_hash(text)
    now = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        async with conn.transaction():
            # 1. Single active task check
            active = await conn.fetchrow(
                """
                SELECT t.id AS task_id, t.analysis_record_id AS record_id, t.status
                FROM analysis_tasks t
                WHERE t.user_id = $1
                  AND t.status IN ('queued', 'running', 'finalizing')
                """,
                user_id,
            )
            if active is not None:
                raise ActiveTaskConflict(
                    task_id=active["task_id"],
                    record_id=active["record_id"],
                    status=active["status"],
                )

            # 2. Create analysis_record (minimal metadata)
            final_client_record_id = client_record_id or f"task-{uuid4()}"
            record_row = await conn.fetchrow(
                """
                INSERT INTO analysis_records (
                    user_id, client_record_id, source_type,
                    source_text, source_text_hash,
                    reading_goal, reading_variant, extended,
                    analysis_status, user_facing_state, created_at, updated_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'queued', 'processing', $9, $9)
                RETURNING id
                """,
                user_id,
                final_client_record_id,
                source_type,
                text,
                source_text_hash,
                reading_goal,
                reading_variant,
                extended,
                now,
            )
            record_id = record_row["id"]

            # 3. Create analysis_task
            task_row = await conn.fetchrow(
                """
                INSERT INTO analysis_tasks (
                    user_id, analysis_record_id, status, queued_at,
                    created_at, updated_at
                )
                VALUES ($1, $2, 'queued', $3, $3, $3)
                RETURNING id
                """,
                user_id,
                record_id,
                now,
            )
            task_id = task_row["id"]

            # 4. Insert task_submitted event
            await conn.execute(
                """
                INSERT INTO analysis_task_events (task_id, event_type, event_payload_json, created_at)
                VALUES ($1, 'task_submitted', $2, $3)
                """,
                task_id,
                json.dumps({}),
                now,
            )

            return TaskSubmitResult(
                task_id=task_id,
                record_id=record_id,
                client_record_id=final_client_record_id,
                status="queued",
                created=True,
            )


async def cancel_new_task(task_id: UUID, record_id: UUID) -> None:
    """
    Cancel a just-created task.
    """
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    now = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                UPDATE analysis_tasks
                SET status = 'cancelled',
                    failure_code = 'insufficient_credits',
                    failure_message = 'Task cancelled: daily credits exhausted.',
                    finished_at = $2,
                    updated_at = $2
                WHERE id = $1 AND status = 'queued'
                """,
                task_id,
                now,
            )
            await conn.execute(
                """
                UPDATE analysis_records
                SET analysis_status = 'cancelled', updated_at = $2
                WHERE id = $1
                """,
                record_id,
                now,
            )
            await conn.execute(
                """
                INSERT INTO analysis_task_events
                    (task_id, event_type, event_payload_json, created_at)
                VALUES ($1, 'task_cancelled', '{"reason": "insufficient_credits"}', $2)
                """,
                task_id,
                now,
            )

async def get_task_status(
    user_id: UUID,
    task_id: UUID,
) -> dict[str, Any] | None:
    """Get task status."""
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                t.id AS task_id,
                t.analysis_record_id AS record_id,
                r.client_record_id,
                t.status,
                t.failure_code,
                t.failure_message,
                t.quota_cost_points,
                t.queued_at,
                t.started_at,
                t.finished_at,
                t.created_at,
                t.updated_at
            FROM analysis_tasks t
            LEFT JOIN analysis_records r ON t.analysis_record_id = r.id
            WHERE t.id = $1 AND t.user_id = $2
            """,
            task_id,
            user_id,
        )
        return dict(row) if row else None


async def get_active_task(user_id: UUID) -> dict[str, Any] | None:
    """Get the currently active task for the user."""
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                t.id AS task_id,
                t.analysis_record_id AS record_id,
                r.client_record_id,
                t.status,
                t.failure_code,
                t.failure_message,
                t.quota_cost_points,
                t.queued_at,
                t.started_at,
                t.finished_at,
                t.created_at,
                t.updated_at
            FROM analysis_tasks t
            LEFT JOIN analysis_records r ON t.analysis_record_id = r.id
            WHERE t.user_id = $1
              AND t.status IN ('queued', 'running', 'finalizing')
            ORDER BY t.created_at DESC
            LIMIT 1
            """,
            user_id,
        )
        return dict(row) if row else None


async def update_task_status(
    task_id: UUID,
    *,
    status: str,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    failure_code: str | None = None,
    failure_message: str | None = None,
    usage_summary_json: dict[str, Any] | None = None,
    quota_cost_points: int | None = None,
    worker_token: str | None = None,
) -> None:
    """Update task status and optional fields."""
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    sets = ["status = $2", "updated_at = $3"]
    params: list[Any] = [task_id, status, datetime.now(timezone.utc)]
    idx = 4

    _JSONB_FIELDS = {"usage_summary_json"}

    for field_name, value in [
        ("started_at", started_at),
        ("finished_at", finished_at),
        ("failure_code", failure_code),
        ("failure_message", failure_message),
        ("usage_summary_json", usage_summary_json),
        ("quota_cost_points", quota_cost_points),
        ("worker_token", worker_token),
    ]:
        if value is not None:
            if field_name in _JSONB_FIELDS and isinstance(value, dict):
                sets.append(f"{field_name} = ${idx}::jsonb")
                params.append(json.dumps(value, ensure_ascii=False))
            else:
                sets.append(f"{field_name} = ${idx}")
                params.append(value)
            idx += 1

    sql = f"UPDATE analysis_tasks SET {', '.join(sets)} WHERE id = $1"

    async with pool.acquire() as conn:
        await conn.execute(sql, *params)


async def touch_task_heartbeat(task_id: UUID, worker_token: str) -> None:
    """Refresh updated_at for a running task."""
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE analysis_tasks
            SET updated_at = $3
            WHERE id = $1
              AND worker_token = $2
              AND status IN ('running', 'finalizing')
            """,
            task_id,
            worker_token,
            datetime.now(timezone.utc),
        )


async def insert_task_event(
    task_id: UUID,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """Insert a task event."""
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO analysis_task_events (task_id, event_type, event_payload_json, created_at)
            VALUES ($1, $2, $3::jsonb, $4)
            """,
            task_id,
            event_type,
            json.dumps(payload or {}),
            datetime.now(timezone.utc),
        )


async def claim_next_queued_task(worker_token: str) -> TaskExecutionPayload | None:
    """Atomically claim the next queued task."""
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    now = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        async with conn.transaction():
            next_row = await conn.fetchrow(
                """
                SELECT id
                FROM analysis_tasks
                WHERE status = 'queued'
                ORDER BY queued_at ASC
                LIMIT 1
                FOR UPDATE SKIP LOCKED
                """
            )
            if next_row is None:
                return None

            row = await conn.fetchrow(
                """
                UPDATE analysis_tasks t
                SET status = 'running',
                    started_at = COALESCE(t.started_at, $3),
                    worker_token = $1,
                    updated_at = $3
                FROM analysis_records r
                WHERE t.id = $2
                  AND t.analysis_record_id = r.id
                  AND t.status = 'queued'
                RETURNING
                    t.id AS task_id,
                    t.analysis_record_id AS record_id,
                    t.user_id AS user_id,
                    r.source_text AS text,
                    r.reading_goal AS reading_goal,
                    r.reading_variant AS reading_variant,
                    r.source_type AS source_type,
                    r.extended AS extended
                """,
                worker_token,
                next_row["id"],
                now,
            )
        if row is None:
            return None

        return TaskExecutionPayload(
            task_id=row["task_id"],
            record_id=row["record_id"],
            user_id=row["user_id"],
            text=row["text"],
            reading_goal=row["reading_goal"],
            reading_variant=row["reading_variant"],
            source_type=row["source_type"],
            extended=row["extended"],
            worker_token=worker_token,
        )


async def requeue_stale_tasks(
    *,
    queued_before: datetime,
    active_before: datetime,
) -> int:
    """Requeue stale tasks."""
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    now = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """
                SELECT t.id AS task_id, t.analysis_record_id AS record_id, t.status,
                       r.analysis_status AS record_status
                FROM analysis_tasks t
                JOIN analysis_records r ON t.analysis_record_id = r.id
                WHERE (t.status = 'queued' AND t.queued_at < $1)
                   OR (t.status IN ('running', 'finalizing') AND t.updated_at < $2)
                FOR UPDATE OF t
                """,
                queued_before,
                active_before,
            )

            if not rows:
                return 0

            requeue_task_ids: list = []
            requeue_record_ids: list = []
            succeed_task_ids: list = []
            succeed_record_ids: list = []

            for row in rows:
                if row["record_status"] in ("ready", "partial"):
                    succeed_task_ids.append(row["task_id"])
                    succeed_record_ids.append(row["record_id"])
                else:
                    requeue_task_ids.append(row["task_id"])
                    requeue_record_ids.append(row["record_id"])

            if succeed_task_ids:
                await conn.execute(
                    """
                    UPDATE analysis_tasks
                    SET status = 'succeeded',
                        finished_at = $2,
                        updated_at = $2
                    WHERE id = ANY($1::uuid[])
                    """,
                    succeed_task_ids,
                    now,
                )
                for tid in succeed_task_ids:
                    await conn.execute(
                        """
                        INSERT INTO analysis_task_events
                            (task_id, event_type, event_payload_json, created_at)
                        VALUES ($1, 'task_recovered_succeeded', $2, $3)
                        """,
                        tid,
                        json.dumps({"reason": "record_already_ready"}),
                        now,
                    )

            if requeue_task_ids:
                await conn.execute(
                    """
                    UPDATE analysis_tasks
                    SET status = 'queued',
                        worker_token = NULL,
                        queued_at = $2,
                        started_at = NULL,
                        finished_at = NULL,
                        failure_code = NULL,
                        failure_message = NULL,
                        updated_at = $2
                    WHERE id = ANY($1::uuid[])
                    """,
                    requeue_task_ids,
                    now,
                )
                await conn.execute(
                    """
                    UPDATE analysis_records
                    SET analysis_status = 'queued',
                        updated_at = $2
                    WHERE id = ANY($1::uuid[])
                    """,
                    requeue_record_ids,
                    now,
                )
                for row in rows:
                    if row["task_id"] in requeue_task_ids:
                        await conn.execute(
                            """
                            INSERT INTO analysis_task_events
                                (task_id, event_type, event_payload_json, created_at)
                            VALUES ($1, 'task_requeued', $2, $3)
                            """,
                            row["task_id"],
                            json.dumps(
                                {
                                    "reason": "server_restart",
                                    "previous_status": row["status"],
                                }
                            ),
                            now,
                        )

            return len(requeue_task_ids) + len(succeed_task_ids)
