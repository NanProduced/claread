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

ALLOWED_TARGET_TYPES = {"daily_reader_article"}


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
    payload_json: dict[str, Any],
) -> UUID:
    if target_type not in ALLOWED_TARGET_TYPES:
        raise HTTPException(status_code=400, detail="unsupported favorite target_type")

    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO favorite_records
                (user_id, target_type, target_key,
                 payload_json, deleted_at, deleted_by, created_at, updated_at)
            VALUES ($1, $2, $3, $4::jsonb, NULL, NULL, $5, $5)
            ON CONFLICT (user_id, target_type, target_key) DO UPDATE SET
                payload_json = EXCLUDED.payload_json,
                deleted_at = NULL,
                deleted_by = NULL,
                updated_at = EXCLUDED.updated_at
            RETURNING id
            """,
            user_id,
            target_type,
            target_key,
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
            SELECT id, user_id, target_type, target_key,
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
