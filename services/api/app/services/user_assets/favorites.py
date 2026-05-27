"""
Favorites Service.

Handles CRUD operations for article-level favorite_records table rows.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException

from app.database import connection as db_connection

ALLOWED_TARGET_TYPES = {"analysis_record", "daily_reader_article"}


def _ensure_payload_dict(row: dict) -> dict:
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
) -> UUID:
    if target_type not in ALLOWED_TARGET_TYPES:
        raise HTTPException(status_code=400, detail="unsupported favorite target_type")
    if target_type == "analysis_record" and analysis_record_id is None:
        raise HTTPException(status_code=400, detail="analysis_record_id is required for analysis_record favorites")

    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO favorite_records
                (user_id, target_type, target_key, analysis_record_id,
                 payload_json, deleted_at, deleted_by, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5::jsonb, NULL, NULL, $6, $6)
            ON CONFLICT (user_id, target_type, target_key) DO UPDATE SET
                analysis_record_id = EXCLUDED.analysis_record_id,
                payload_json = EXCLUDED.payload_json,
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
            datetime.now(UTC),
        )
        assert row is not None
        return UUID(str(row["id"]))


async def list_favorites(user_id: UUID) -> list[dict]:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, user_id, target_type, target_key, analysis_record_id,
                   payload_json, created_at, updated_at
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
    if target_type not in ALLOWED_TARGET_TYPES:
        raise HTTPException(status_code=400, detail="unsupported favorite target_type")

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
              AND target_type = 'analysis_record'
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
