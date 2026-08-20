"""Review audit columns: baseline presence + incremental up/down backfill."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from tests.test_reader_orchestration_schema_baseline import DATABASE_URL

pytestmark = pytest.mark.anyio

REPO_ROOT = Path(__file__).resolve().parents[3]
BASELINE_SQL = (REPO_ROOT / "infra" / "migrations" / "0001_initial.sql").read_text(
    encoding="utf-8"
)
UP_SQL = (
    REPO_ROOT / "infra" / "scripts" / "alter_daily_readers_review_audit.sql"
).read_text(encoding="utf-8")
DOWN_SQL = (
    REPO_ROOT / "infra" / "scripts" / "alter_daily_readers_review_audit_down.sql"
).read_text(encoding="utf-8")


def test_baseline_declares_review_audit_columns() -> None:
    assert "review_status text DEFAULT 'pending'::text NOT NULL" in BASELINE_SQL
    assert "reviewed_by text" in BASELINE_SQL
    assert "reviewed_at timestamp with time zone" in BASELINE_SQL
    assert "daily_readers_review_status_check" in BASELINE_SQL
    assert "DEPRECATED: 历史占位字段" in BASELINE_SQL


def test_migrations_dir_still_single_baseline() -> None:
    names = sorted(
        path.name for path in (REPO_ROOT / "infra" / "migrations").iterdir()
    )
    assert names == ["0001_initial.sql"]


async def test_alter_script_backfills_published_and_rolls_back() -> None:
    schema_name = f"test_dr_review_audit_{uuid4().hex}"
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await conn.execute(f'SET search_path TO "{schema_name}", public')
        await conn.execute(
            """
            CREATE TABLE daily_readers (
                id text PRIMARY KEY,
                status text NOT NULL,
                published_at timestamp with time zone,
                content_sec_check jsonb DEFAULT '{}'::jsonb NOT NULL
            )
            """
        )
        await conn.execute(
            """
            INSERT INTO daily_readers (id, status, published_at) VALUES
            ('daily_old_1', 'published', TIMESTAMPTZ '2026-08-01 00:00:00+00'),
            ('daily_old_2', 'published', TIMESTAMPTZ '2026-08-02 00:00:00+00'),
            ('daily_old_3', 'published', NULL),
            ('daily_draft_1', 'draft', NULL)
            """
        )

        await conn.execute(UP_SQL)

        published = await conn.fetch(
            """
            SELECT id, review_status, reviewed_by, reviewed_at
            FROM daily_readers
            WHERE status = 'published'
            ORDER BY id
            """
        )
        assert len(published) == 3
        for row in published:
            assert row["review_status"] == "approved"
            assert row["reviewed_by"] == "legacy"
            assert row["reviewed_at"] is not None

        draft = await conn.fetchrow(
            "SELECT review_status, reviewed_by FROM daily_readers WHERE id = 'daily_draft_1'"
        )
        assert draft["review_status"] == "pending"
        assert draft["reviewed_by"] is None

        comment = await conn.fetchval(
            """
            SELECT col_description('daily_readers'::regclass, attnum)
            FROM pg_attribute
            WHERE attrelid = 'daily_readers'::regclass
              AND attname = 'content_sec_check'
            """
        )
        assert comment is not None
        assert "DEPRECATED" in comment

        await conn.execute(DOWN_SQL)

        columns = await conn.fetch(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = $1 AND table_name = 'daily_readers'
            """,
            schema_name,
        )
        names = {row["column_name"] for row in columns}
        assert "review_status" not in names
        assert "reviewed_by" not in names
        assert "reviewed_at" not in names
        assert "content_sec_check" in names
    finally:
        await conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await conn.close()


@pytest.fixture
def anyio_backend():
    return "asyncio"
