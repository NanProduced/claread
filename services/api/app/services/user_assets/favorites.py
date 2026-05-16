"""
Favorites Service.

Handles CRUD operations for favorite_records table.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException

from app.database import connection as db_connection
from app.services.text_anchors import (
    load_render_scene,
    validate_multi_text_against_render_scene,
    validate_text_range_against_render_scene,
)


def _ensure_payload_dict(row: dict) -> dict:
    """Normalize asyncpg JSONB payloads across real DB and tests."""
    payload = row.get("payload_json")
    if isinstance(payload, str):
        try:
            row["payload_json"] = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            row["payload_json"] = {}
    elif payload is None:
        row["payload_json"] = {}
    return row


async def add_favorite(
    user_id: UUID,
    target_type: str,
    target_key: str,
    analysis_record_id: UUID | None,
    payload_json: dict[str, Any],
    note: str | None,
) -> UUID:
    """
    Add a favorite.

    If the same target was removed before, this call revives it by clearing deleted_at/deleted_by.

    Returns:
        id of the favorite record
    """
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    async with pool.acquire() as conn:
        if target_type in {"text_range", "multi_text"}:
            if analysis_record_id is None:
                raise HTTPException(status_code=400, detail="analysis_record_id is required for text anchors")
            render_scene = await load_render_scene(conn, user_id, analysis_record_id)
            if target_type == "multi_text":
                segments = payload_json.get("segments")
                if not isinstance(segments, list):
                    raise HTTPException(status_code=400, detail="payload_json.segments is required for multi_text favorites")
                validate_multi_text_against_render_scene(render_scene, segments)
            else:
                validate_text_range_against_render_scene(render_scene, payload_json)

        row = await conn.fetchrow(
            """
            INSERT INTO favorite_records
                (user_id, target_type, target_key, analysis_record_id,
                 payload_json, note, deleted_at, deleted_by, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6, NULL, NULL, $7, $7)
            ON CONFLICT (user_id, target_type, target_key) DO UPDATE SET
                analysis_record_id = EXCLUDED.analysis_record_id,
                payload_json = EXCLUDED.payload_json,
                note = EXCLUDED.note,
                deleted_at = NULL,
                deleted_by = NULL,
                updated_at = EXCLUDED.updated_at
            RETURNING id
            """,
            user_id,
            target_type,
            target_key,
            analysis_record_id,
            payload_json,
            note,
            datetime.now(UTC),
        )
        assert row is not None
        return UUID(str(row["id"]))


async def list_favorites(
    user_id: UUID,
) -> list[dict]:
    """List all favorites for a user."""
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, user_id, target_type, target_key, analysis_record_id,
                   payload_json, note, created_at, updated_at
            FROM favorite_records
            WHERE user_id = $1 AND deleted_at IS NULL
            ORDER BY created_at DESC
            """,
            user_id,
        )
        return [_ensure_payload_dict(dict(row)) for row in rows]


async def remove_favorite(
    user_id: UUID,
    target_type: str,
    target_key: str,
) -> bool:
    """Soft-delete a favorite by target. Returns True if affected."""
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    async with pool.acquire() as conn:
        now = datetime.now(UTC)
        result = await conn.execute(
            """
            UPDATE favorite_records
            SET deleted_at = $4,
                deleted_by = $1,
                updated_at = $4
            WHERE user_id = $1
              AND target_type = $2
              AND target_key = $3
              AND deleted_at IS NULL
            """,
            user_id,
            target_type,
            target_key,
            now,
        )
    return "UPDATE 1" in result


async def remove_favorite_by_analysis_record(
    user_id: UUID,
    analysis_record_id: UUID,
) -> int:
    """Soft-delete favorites by analysis_record_id. Returns affected count."""
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    async with pool.acquire() as conn:
        now = datetime.now(UTC)
        result = await conn.execute(
            """
            UPDATE favorite_records
            SET deleted_at = $3,
                deleted_by = $1,
                updated_at = $3
            WHERE user_id = $1
              AND analysis_record_id = $2
              AND deleted_at IS NULL
            """,
            user_id,
            analysis_record_id,
            now,
        )
        if "UPDATE " in result:
            return int(result.split()[-1])
        return 0
