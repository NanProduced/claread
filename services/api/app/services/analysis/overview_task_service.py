from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from app.database import connection as db_connection
from app.database.json_compat import ensure_json_object, jsonb_param
from app.schemas.internal.overview_hint import StoredOverviewHint

logger = logging.getLogger(__name__)


class OverviewTaskExecutionPayload:
    __slots__ = (
        "task_id",
        "record_id",
        "user_id",
        "text",
        "source_text_hash",
        "reading_goal",
        "reading_variant",
        "render_scene_json",
        "workflow_version",
        "schema_version",
        "worker_token",
    )

    def __init__(
        self,
        *,
        task_id: UUID,
        record_id: UUID,
        user_id: UUID,
        text: str,
        source_text_hash: str,
        reading_goal: str,
        reading_variant: str,
        render_scene_json: dict[str, Any],
        workflow_version: str | None,
        schema_version: str | None,
        worker_token: str,
    ) -> None:
        self.task_id = task_id
        self.record_id = record_id
        self.user_id = user_id
        self.text = text
        self.source_text_hash = source_text_hash
        self.reading_goal = reading_goal
        self.reading_variant = reading_variant
        self.render_scene_json = render_scene_json
        self.workflow_version = workflow_version
        self.schema_version = schema_version
        self.worker_token = worker_token


def _ensure_page_state_dict(payload: Any) -> dict[str, Any]:
    return ensure_json_object(payload)


def _merge_overview_hint(
    page_state_json: dict[str, Any] | None,
    hint: StoredOverviewHint,
) -> dict[str, Any]:
    page_state = dict(page_state_json or {})
    derived = page_state.get("derived")
    if not isinstance(derived, dict):
        derived = {}
    derived["overview_hint"] = hint.model_dump(mode="json", exclude_none=True)
    page_state["derived"] = derived
    return page_state


async def update_record_overview_hint(
    *,
    record_id: UUID,
    hint: StoredOverviewHint,
) -> None:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT page_state_json
                FROM analysis_results
                WHERE record_id = $1
                FOR UPDATE
                """,
                record_id,
            )
            existing_page_state = _ensure_page_state_dict(row["page_state_json"]) if row else {}
            next_page_state = _merge_overview_hint(existing_page_state, hint)
            await conn.execute(
                """
                INSERT INTO analysis_results (record_id, page_state_json)
                VALUES ($1, $2::jsonb)
                ON CONFLICT (record_id) DO UPDATE SET
                    page_state_json = EXCLUDED.page_state_json
                """,
                record_id,
                jsonb_param(next_page_state),
            )


async def enqueue_overview_task_if_needed(
    *,
    user_id: UUID,
    record_id: UUID,
    source_text_hash: str,
    reading_goal: str | None,
    reading_variant: str | None,
    workflow_version: str | None,
    schema_version: str | None,
) -> UUID | None:
    if reading_goal == "academic":
        return None

    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    now = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT c.page_state_json
                FROM analysis_records r
                LEFT JOIN analysis_results c ON c.record_id = r.id
                WHERE r.id = $1 AND r.user_id = $2 AND r.deleted_at IS NULL
                FOR UPDATE OF r
                """,
                record_id,
                user_id,
            )
            if row is None:
                return None

            page_state_json = _ensure_page_state_dict(row["page_state_json"])
            existing_hint_raw = (
                page_state_json.get("derived", {}).get("overview_hint")
                if isinstance(page_state_json.get("derived"), dict)
                else None
            )
            existing_hint = (
                StoredOverviewHint.model_validate(existing_hint_raw)
                if isinstance(existing_hint_raw, dict)
                else None
            )
            is_fresh = (
                existing_hint is not None
                and existing_hint.status in {"ready", "pending"}
                and existing_hint.source_text_hash == source_text_hash
                and existing_hint.workflow_version == workflow_version
                and existing_hint.schema_version == schema_version
            )
            if is_fresh:
                return None

            active_row = await conn.fetchrow(
                """
                SELECT id
                FROM analysis_overview_tasks
                WHERE analysis_record_id = $1
                  AND status IN ('queued', 'running', 'finalizing')
                LIMIT 1
                """,
                record_id,
            )
            if active_row is not None:
                pending_hint = StoredOverviewHint(
                    status="pending",
                    source="learning_overview_hint_agent",
                    source_text_hash=source_text_hash,
                    workflow_version=workflow_version,
                    schema_version=schema_version,
                    updated_at=now.isoformat(),
                    task_id=str(active_row["id"]),
                )
                next_page_state = _merge_overview_hint(page_state_json, pending_hint)
                await conn.execute(
                    """
                    UPDATE analysis_results
                    SET page_state_json = $2::jsonb
                    WHERE record_id = $1
                    """,
                    record_id,
                    jsonb_param(next_page_state),
                )
                return UUID(str(active_row["id"]))

            task_id = uuid4()
            pending_hint = StoredOverviewHint(
                status="pending",
                source="learning_overview_hint_agent",
                source_text_hash=source_text_hash,
                workflow_version=workflow_version,
                schema_version=schema_version,
                updated_at=now.isoformat(),
                task_id=str(task_id),
            )
            next_page_state = _merge_overview_hint(page_state_json, pending_hint)

            await conn.execute(
                """
                UPDATE analysis_results
                SET page_state_json = $2::jsonb
                WHERE record_id = $1
                """,
                record_id,
                jsonb_param(next_page_state),
            )
            await conn.execute(
                """
                INSERT INTO analysis_overview_tasks (
                    id, user_id, analysis_record_id, status, queued_at, created_at, updated_at
                )
                VALUES ($1, $2, $3, 'queued', $4, $4, $4)
                """,
                task_id,
                user_id,
                record_id,
                now,
            )
            await conn.execute(
                """
                INSERT INTO analysis_overview_task_events (task_id, event_type, event_payload_json, created_at)
                VALUES ($1, 'task_submitted', $2::jsonb, $3)
                """,
                task_id,
                jsonb_param(
                    {
                        "source_text_hash": source_text_hash,
                        "workflow_version": workflow_version,
                        "schema_version": schema_version,
                    }
                ),
                now,
            )
            return task_id


async def update_task_status(
    task_id: UUID,
    *,
    status: str,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    failure_code: str | None = None,
    failure_message: str | None = None,
    usage_summary_json: dict[str, Any] | None = None,
    worker_token: str | None = None,
) -> None:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    sets = ["status = $2", "updated_at = $3"]
    params: list[Any] = [task_id, status, datetime.now(timezone.utc)]
    idx = 4
    json_fields = {"usage_summary_json"}

    for field_name, value in [
        ("started_at", started_at),
        ("finished_at", finished_at),
        ("failure_code", failure_code),
        ("failure_message", failure_message),
        ("usage_summary_json", usage_summary_json),
        ("worker_token", worker_token),
    ]:
        if value is None:
            continue
        if field_name in json_fields and isinstance(value, dict):
            sets.append(f"{field_name} = ${idx}::jsonb")
            params.append(jsonb_param(value))
        else:
            sets.append(f"{field_name} = ${idx}")
            params.append(value)
        idx += 1

    async with pool.acquire() as conn:
        await conn.execute(
            f"UPDATE analysis_overview_tasks SET {', '.join(sets)} WHERE id = $1",
            *params,
        )


async def insert_task_event(
    task_id: UUID,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> None:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO analysis_overview_task_events (task_id, event_type, event_payload_json, created_at)
            VALUES ($1, $2, $3::jsonb, $4)
            """,
            task_id,
            event_type,
            jsonb_param(payload or {}),
            datetime.now(timezone.utc),
        )


async def touch_task_heartbeat(task_id: UUID, worker_token: str) -> None:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE analysis_overview_tasks
            SET updated_at = $3
            WHERE id = $1
              AND worker_token = $2
              AND status IN ('running', 'finalizing')
            """,
            task_id,
            worker_token,
            datetime.now(timezone.utc),
        )


async def claim_next_queued_task(worker_token: str) -> OverviewTaskExecutionPayload | None:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    now = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        async with conn.transaction():
            next_row = await conn.fetchrow(
                """
                SELECT id
                FROM analysis_overview_tasks
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
                UPDATE analysis_overview_tasks t
                SET status = 'running',
                    started_at = COALESCE(t.started_at, $3),
                    worker_token = $1,
                    updated_at = $3
                FROM analysis_records r
                LEFT JOIN analysis_results c ON c.record_id = r.id
                WHERE t.id = $2
                  AND t.analysis_record_id = r.id
                  AND t.status = 'queued'
                RETURNING
                    t.id AS task_id,
                    t.analysis_record_id AS record_id,
                    t.user_id AS user_id,
                    r.source_text AS text,
                    r.source_text_hash AS source_text_hash,
                    r.reading_goal AS reading_goal,
                    r.reading_variant AS reading_variant,
                    c.render_scene_json AS render_scene_json,
                    c.workflow_version AS workflow_version,
                    c.schema_version AS schema_version
                """,
                worker_token,
                next_row["id"],
                now,
            )
        if row is None:
            return None
        render_scene_json = _ensure_page_state_dict(row["render_scene_json"])
        return OverviewTaskExecutionPayload(
            task_id=row["task_id"],
            record_id=row["record_id"],
            user_id=row["user_id"],
            text=row["text"] or "",
            source_text_hash=row["source_text_hash"] or "",
            reading_goal=row["reading_goal"] or "daily_reading",
            reading_variant=row["reading_variant"] or "intermediate_reading",
            render_scene_json=render_scene_json,
            workflow_version=row["workflow_version"],
            schema_version=row["schema_version"],
            worker_token=worker_token,
        )


async def requeue_stale_tasks(*, queued_before: datetime, active_before: datetime) -> int:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    now = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """
                SELECT id, status
                FROM analysis_overview_tasks
                WHERE (status = 'queued' AND queued_at < $1)
                   OR (status IN ('running', 'finalizing') AND updated_at < $2)
                FOR UPDATE
                """,
                queued_before,
                active_before,
            )
            if not rows:
                return 0
            task_ids = [row["id"] for row in rows]
            await conn.execute(
                """
                UPDATE analysis_overview_tasks
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
                task_ids,
                now,
            )
            for row in rows:
                await conn.execute(
                    """
                    INSERT INTO analysis_overview_task_events (task_id, event_type, event_payload_json, created_at)
                    VALUES ($1, 'task_requeued', $2::jsonb, $3)
                    """,
                    row["id"],
                    jsonb_param({"reason": "server_restart", "previous_status": row["status"]}),
                    now,
                )
            return len(task_ids)
