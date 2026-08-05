"""DATA-SCHEMA-BASELINE D2 fresh-init gate: reading_record favorite chain.

Proves against the real local database (fresh volume initialized only from
``infra/migrations/0001_initial.sql``) that the exited favorite contract
works end to end for the ``reading_record`` target:

- the API DTO accepts exactly ``daily_reader_article | reading_record``;
- the service can create, list and soft-delete a reading_record favorite
  against the fresh baseline (target_type CHECK accepts it).
"""

from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest
from pydantic import ValidationError

from app.database import connection as db_connection
from app.database.connection import init_connection
from app.schemas.user_assets.favorites import FavoriteCreateRequest
from app.services.user_assets import favorites as fav_svc

pytestmark = pytest.mark.asyncio

API_ROOT = Path(__file__).resolve().parents[1]


def _load_database_url() -> str:
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return database_url
    env_path = API_ROOT / ".env"
    if env_path.exists():
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or not line.startswith("DATABASE_URL="):
                continue
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return "postgresql://claread:claread_dev@127.0.0.1:5432/claread"


def test_favorite_target_union_is_exact() -> None:
    ok = FavoriteCreateRequest(target_type="reading_record", target_key=str(uuid4()))
    assert ok.target_type == "reading_record"
    ok_daily = FavoriteCreateRequest(
        target_type="daily_reader_article", target_key="daily_reader_article:2026-05-21"
    )
    assert ok_daily.target_type == "daily_reader_article"
    with pytest.raises(ValidationError):
        FavoriteCreateRequest(target_type="analysis_record", target_key="x")  # type: ignore[arg-type]


async def test_reading_record_favorite_create_list_delete_real_db() -> None:
    pool = await asyncpg.create_pool(
        _load_database_url(), min_size=1, max_size=2, init=init_connection
    )
    user_id: UUID | None = None
    previous_pool = db_connection.DB_POOL
    db_connection.DB_POOL = pool
    try:
        record_id = uuid4()
        async with pool.acquire() as conn:
            user_id = await conn.fetchval("INSERT INTO users DEFAULT VALUES RETURNING id")

        favorite_id = await fav_svc.add_favorite(
            user_id=user_id,
            target_type="reading_record",
            target_key=str(record_id),
            payload_json={"source": "d2_fresh_gate"},
        )
        assert isinstance(favorite_id, UUID)

        rows = await fav_svc.list_favorites(user_id=user_id)
        matched = [
            row
            for row in rows
            if row["target_type"] == "reading_record" and row["target_key"] == str(record_id)
        ]
        assert len(matched) == 1
        assert matched[0]["id"] == favorite_id

        deleted = await fav_svc.remove_favorite(
            user_id=user_id,
            target_type="reading_record",
            target_key=str(record_id),
        )
        assert deleted is True

        rows_after = await fav_svc.list_favorites(user_id=user_id)
        assert all(
            not (row["target_type"] == "reading_record" and row["target_key"] == str(record_id))
            for row in rows_after
        )
    finally:
        if user_id is not None:
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM users WHERE id = $1", user_id)
        db_connection.DB_POOL = previous_pool
        await pool.close()
