"""User profile service.

Handles user profile retrieval and updates.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from app.database import connection as db_connection


async def get_user_profile(user_id: UUID) -> dict | None:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, display_name, avatar_url, cumulative_article_count, settings_json
            FROM users WHERE id = $1
            """,
            user_id,
        )

    if row is None:
        return None

    return {
        "user_id": str(row["id"]),
        "nickname": row["display_name"] or "",
        "avatar_url": row["avatar_url"] or "",
        "cumulative_article_count": row["cumulative_article_count"] or 0,
        "settings": json.loads(row["settings_json"]) if row["settings_json"] else {},
    }


async def update_user_profile(
    user_id: UUID,
    nickname: str | None = None,
    avatar_url: str | None = None,
    settings: dict[str, Any] | None = None,
) -> list[str]:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    async with pool.acquire() as conn:
        async with conn.transaction():
            if settings is not None:
                current_settings_raw = await conn.fetchval(
                    "SELECT settings_json FROM users WHERE id = $1", user_id
                )
                current_settings = json.loads(current_settings_raw) if current_settings_raw else {}
                current_settings.update(settings)
                new_settings_json = json.dumps(current_settings, ensure_ascii=False)
            else:
                new_settings_json = None

            updates: dict[str, Any] = {}
            if nickname is not None:
                updates["display_name"] = nickname
            if avatar_url is not None:
                updates["avatar_url"] = avatar_url
            if new_settings_json is not None:
                updates["settings_json"] = new_settings_json

            if not updates:
                return []

            set_clauses = ", ".join(f"{k} = ${i+2}" for i, k in enumerate(updates.keys()))
            values = list(updates.values())

            await conn.execute(
                f"UPDATE users SET {set_clauses} WHERE id = $1",
                user_id,
                *values,
            )

    return list(updates.keys())
