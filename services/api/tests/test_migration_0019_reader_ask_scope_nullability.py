"""Integration coverage for the Reading Record Ask dual-scope repair."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from tests.test_reader_orchestration_schema_baseline import (
    BASELINE_SQL,
    DATABASE_URL,
    _insert_reading_record,
    _insert_user,
)

pytestmark = pytest.mark.anyio

REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATION_0019_SQL = (
    REPO_ROOT
    / "infra"
    / "migrations"
    / "0019_reader_ask_dual_scope_nullable_analysis_record_id.sql"
).read_text(encoding="utf-8")


@pytest.fixture
async def migrated_connection() -> AsyncIterator[asyncpg.Connection]:
    schema_name = f"test_migration_0019_{uuid4().hex}"
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await conn.execute(f'SET search_path TO "{schema_name}", public')
        await conn.execute(BASELINE_SQL)
        await conn.execute(MIGRATION_0019_SQL)
        yield conn
    finally:
        await conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await conn.close()


async def test_reading_record_thread_scope_can_persist_without_analysis_record(
    migrated_connection: asyncpg.Connection,
) -> None:
    user_id = await _insert_user(migrated_connection)
    reading_record_id = await _insert_reading_record(migrated_connection, user_id)

    thread_id = await migrated_connection.fetchval(
        """
        INSERT INTO reader_ask_threads (
            user_id, analysis_record_id, reading_record_id, title, is_default
        )
        VALUES ($1, NULL, $2, 'Reading Record Ask', TRUE)
        RETURNING id
        """,
        user_id,
        reading_record_id,
    )

    assert thread_id is not None


async def test_turn_run_analysis_scope_is_nullable(
    migrated_connection: asyncpg.Connection,
) -> None:
    is_nullable = await migrated_connection.fetchval(
        """
        SELECT is_nullable
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'reader_ask_turn_runs'
          AND column_name = 'analysis_record_id'
        """
    )

    assert is_nullable == "YES"
