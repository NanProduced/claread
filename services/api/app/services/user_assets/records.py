"""
Analysis Records Service.

Handles CRUD operations for analysis_records and analysis_results tables.
Splits heavy content from metadata.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from app.database import connection as db_connection

# Fields that reside in analysis_results table
_CONTENT_FIELDS = {"render_scene_json", "page_state_json", "workflow_version", "schema_version"}
# Fields that are JSONB
_JSONB_COLUMNS = {"request_payload_json", "render_scene_json", "page_state_json", "usage_summary_json"}


def _ensure_dict(row: dict | None) -> dict | None:
    """Ensure JSONB columns in a row are dictionaries and synthesize request_payload_json."""
    if row is None:
        return None
    for col in _JSONB_COLUMNS:
        if col in row and isinstance(row[col], str):
            try:
                row[col] = json.loads(row[col])
            except (json.JSONDecodeError, TypeError):
                row[col] = {}

    # Synthesize request_payload_json for backward compatibility
    if "request_payload_json" not in row or not row["request_payload_json"]:
        row["request_payload_json"] = {
            "reading_goal": row.get("reading_goal"),
            "reading_variant": row.get("reading_variant"),
            "source_type": row.get("source_type"),
            "extended": row.get("extended", False),
        }

    return row


async def upsert_record(
    user_id: UUID,
    client_record_id: str,
    source_type: str,
    title: str | None,
    source_text: str,
    source_text_hash: str,
    reading_goal: str | None,
    reading_variant: str | None,
    user_facing_state: str | None,
    analysis_status: str,
    extended: bool = False,
    render_scene_json: dict[str, Any] | None = None,
    page_state_json: dict[str, Any] | None = None,
    workflow_version: str | None = None,
    schema_version: str | None = None,
) -> tuple[UUID, bool, datetime]:
    """
    Upsert an analysis record and its associated result content.
    """
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    async with pool.acquire() as conn:
        async with conn.transaction():
            now = datetime.now(timezone.utc)
            # 1. Upsert Metadata
            record_row = await conn.fetchrow(
                """
                INSERT INTO analysis_records (
                    user_id, client_record_id, source_type, title, source_text,
                    source_text_hash, reading_goal, reading_variant, extended,
                    user_facing_state, analysis_status, created_at, updated_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $12)
                ON CONFLICT (user_id, client_record_id) DO UPDATE SET
                    title            = COALESCE(EXCLUDED.title, analysis_records.title),
                    source_text      = EXCLUDED.source_text,
                    source_text_hash = EXCLUDED.source_text_hash,
                    reading_goal        = EXCLUDED.reading_goal,
                    reading_variant     = EXCLUDED.reading_variant,
                    extended            = EXCLUDED.extended,
                    user_facing_state   = COALESCE(EXCLUDED.user_facing_state, analysis_records.user_facing_state, 'processing'),
                    analysis_status     = EXCLUDED.analysis_status,
                    deleted_at          = NULL,
                    deleted_by          = NULL,
                    updated_at          = $12
                RETURNING id, updated_at, (xmax = 0) AS created
                """,
                user_id, client_record_id, source_type, title, source_text,
                source_text_hash, reading_goal, reading_variant, extended,
                user_facing_state or 'processing', analysis_status, now,
            )
            assert record_row is not None
            record_id = UUID(str(record_row["id"]))

            # 2. Upsert Content if provided
            if render_scene_json is not None:
                await conn.execute(
                    """
                    INSERT INTO analysis_results (
                        record_id, render_scene_json, page_state_json,
                        workflow_version, schema_version, created_at
                    )
                    VALUES ($1, $2::jsonb, $3::jsonb, $4, $5, $6)
                    ON CONFLICT (record_id) DO UPDATE SET
                        render_scene_json = EXCLUDED.render_scene_json,
                        page_state_json   = EXCLUDED.page_state_json,
                        workflow_version  = EXCLUDED.workflow_version,
                        schema_version    = EXCLUDED.schema_version
                    """,
                    record_id,
                    json.dumps(render_scene_json, ensure_ascii=False),
                    json.dumps(page_state_json or {}, ensure_ascii=False),
                    workflow_version,
                    schema_version,
                    now,
                )

            return record_id, bool(record_row["created"]), record_row["updated_at"]


async def get_record_by_id(
    user_id: UUID,
    record_id: UUID,
    include_content: bool = True,
) -> dict | None:
    """Get a single record by id, optionally including results content."""
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    content_join = ""
    content_cols = ""
    if include_content:
        content_join = "LEFT JOIN analysis_results c ON r.id = c.record_id"
        content_cols = ", c.render_scene_json, c.page_state_json, c.workflow_version, c.schema_version"

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            SELECT r.id, r.user_id, r.client_record_id, r.source_type, r.title, r.source_text,
                   r.source_text_hash, r.reading_goal, r.reading_variant, r.extended,
                   r.user_facing_state, r.analysis_status, r.last_opened_at, r.created_at, r.updated_at
                   {content_cols}
            FROM analysis_records r
            {content_join}
            WHERE r.id = $1 AND r.user_id = $2 AND r.deleted_at IS NULL
            """,
            record_id,
            user_id,
        )
        if row is None:
            return None
        return _ensure_dict(dict(row))


async def get_record_by_client_id(
    user_id: UUID,
    client_record_id: str,
    include_content: bool = True,
) -> dict | None:
    """Get a single record by client_record_id."""
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    content_join = ""
    content_cols = ""
    if include_content:
        content_join = "LEFT JOIN analysis_results c ON r.id = c.record_id"
        content_cols = ", c.render_scene_json, c.page_state_json, c.workflow_version, c.schema_version"

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            SELECT r.id, r.user_id, r.client_record_id, r.source_type, r.title, r.source_text,
                   r.source_text_hash, r.reading_goal, r.reading_variant, r.extended,
                   r.user_facing_state, r.analysis_status, r.last_opened_at, r.created_at, r.updated_at
                   {content_cols}
            FROM analysis_records r
            {content_join}
            WHERE r.client_record_id = $1 AND r.user_id = $2 AND r.deleted_at IS NULL
            """,
            client_record_id,
            user_id,
        )
        if row is None:
            return None
        return _ensure_dict(dict(row))


async def list_records(
    user_id: UUID,
    page: int = 1,
    limit: int = 20,
    include_content: bool = False,
) -> tuple[list[dict], int]:
    """List records for a user with pagination."""
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    offset = (page - 1) * limit
    content_join = ""
    content_cols = ""
    if include_content:
        content_join = "LEFT JOIN analysis_results c ON r.id = c.record_id"
        content_cols = ", c.render_scene_json, c.page_state_json, c.workflow_version, c.schema_version"
    else:
        content_cols = ", '{}'::jsonb AS render_scene_json, '{}'::jsonb AS page_state_json"

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT r.id, r.user_id, r.client_record_id, r.source_type, r.title, r.source_text,
                   r.source_text_hash, r.reading_goal, r.reading_variant, r.extended,
                   r.user_facing_state, r.analysis_status, r.last_opened_at, r.created_at, r.updated_at
                   {content_cols}
            FROM analysis_records r
            {content_join}
            WHERE r.user_id = $1 AND r.deleted_at IS NULL
            ORDER BY r.updated_at DESC
            LIMIT $2 OFFSET $3
            """,
            user_id,
            limit,
            offset,
        )
        total = await conn.fetchval(
            "SELECT COUNT(*) FROM analysis_records WHERE user_id = $1 AND deleted_at IS NULL",
            user_id,
        )
        return [_ensure_dict(dict(row)) for row in rows], int(total)  # type: ignore[misc]


async def update_record(
    user_id: UUID,
    record_id: UUID,
    **fields: Any,
) -> dict | None:
    """Partial update across both records and results tables."""
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    allowed_metadata = {"title", "user_facing_state", "analysis_status", "last_opened_at", "extended"}
    allowed_content = {"render_scene_json", "page_state_json", "workflow_version", "schema_version"}

    metadata_updates = {k: v for k, v in fields.items() if k in allowed_metadata and v is not None}
    content_updates = {k: v for k, v in fields.items() if k in allowed_content and v is not None}

    if not metadata_updates and not content_updates:
        return await get_record_by_id(user_id, record_id)

    now = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        async with conn.transaction():
            if metadata_updates:
                metadata_updates["updated_at"] = now
                set_parts = []
                params = []
                for i, (k, v) in enumerate(metadata_updates.items()):
                    set_parts.append(f"{k} = ${i + 1}")
                    params.append(v)
                params.extend([record_id, user_id])
                await conn.execute(
                    f"UPDATE analysis_records SET {', '.join(set_parts)} "
                    f"WHERE id = ${len(params) - 1} AND user_id = ${len(params)} AND deleted_at IS NULL",
                    *params
                )

            if content_updates:
                set_parts = []
                params = [record_id]
                for i, (k, v) in enumerate(content_updates.items()):
                    if k in _JSONB_COLUMNS:
                        set_parts.append(f"{k} = ${i + 2}::jsonb")
                        params.append(json.dumps(v, ensure_ascii=False))
                    else:
                        set_parts.append(f"{k} = ${i + 2}")
                        params.append(v)

                await conn.execute(
                    f"""
                    INSERT INTO analysis_results (record_id, {', '.join(content_updates.keys())})
                    VALUES ($1, {', '.join([f'${i+2}' + ('::jsonb' if k in _JSONB_COLUMNS else '') for i, k in enumerate(content_updates.keys())])})
                    ON CONFLICT (record_id) DO UPDATE SET {', '.join(set_parts)}
                    """,
                    *params
                )

    return await get_record_by_id(user_id, record_id)


async def increment_user_reading_count(user_id: UUID) -> bool:
    """
    Increment the user's cumulative article count and update last active time.
    Atomic operation.
    """
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    now = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE users
            SET cumulative_article_count = cumulative_article_count + 1,
                last_active_at = $2
            WHERE id = $1
            """,
            user_id,
            now,
        )
    return "UPDATE 1" in result


async def delete_record(user_id: UUID, record_id: UUID) -> str:
    """Soft-delete a record. Results will stay linked but analysis_records marks as deleted.

    Returns:
        "deleted"  — record was live and has been soft-deleted now
        "already"  — record was already in deleted state (idempotent success)
        "missing"  — no such record belongs to this user
    """
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT deleted_at FROM analysis_records
                WHERE id = $1 AND user_id = $2
                """,
                record_id,
                user_id,
            )
            if row is None:
                return "missing"
            if row["deleted_at"] is not None:
                return "already"

            now = datetime.now(timezone.utc)
            await conn.execute(
                """
                UPDATE analysis_records
                SET deleted_at = $3,
                    deleted_by = $2,
                    analysis_status = 'deleted',
                    updated_at = $3
                WHERE id = $1 AND user_id = $2 AND deleted_at IS NULL
                """,
                record_id,
                user_id,
                now,
            )
            await conn.execute(
                """
                UPDATE favorite_records
                SET deleted_at = $3,
                    deleted_by = $2,
                    updated_at = $3
                WHERE analysis_record_id = $1
                  AND user_id = $2
                  AND deleted_at IS NULL
                """,
                record_id,
                user_id,
                now,
            )
            await conn.execute(
                """
                UPDATE user_annotations
                SET deleted_at = $3,
                    deleted_by = $2,
                    updated_at = $3
                WHERE analysis_record_id = $1
                  AND user_id = $2
                  AND deleted_at IS NULL
                """,
                record_id,
                user_id,
                now,
            )
    return "deleted"
