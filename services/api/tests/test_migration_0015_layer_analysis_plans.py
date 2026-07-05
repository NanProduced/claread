"""Migration test for 0015_layer_analysis_plans.sql.

Verifies that the Z+ Analysis Window migration creates the
``layer_analysis_plans`` and ``analysis_windows`` tables, registers the
``build_grammar_bundle_window`` job type in ``reader_jobs``, and installs the
partial unique index that fences concurrent active plans.

See docs/initiatives/reader-agentic-orchestration/analysis-window-zplus-design.md §4.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from app.database.connection import init_connection
from tests.test_reader_orchestration_schema_baseline import BASELINE_SQL, DATABASE_URL

pytestmark = pytest.mark.anyio

REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATION_0015_SQL = (
    REPO_ROOT / "infra" / "migrations" / "0015_layer_analysis_plans.sql"
).read_text(encoding="utf-8")


async def _connect_admin() -> asyncpg.Connection:
    return await asyncpg.connect(DATABASE_URL)


async def _make_pool(schema_name: str) -> asyncpg.Pool:
    async def _init_conn(conn: asyncpg.Connection) -> None:
        await init_connection(conn)

    async def _setup_conn(conn: asyncpg.Connection) -> None:
        await conn.execute(f'SET search_path TO "{schema_name}", public')

    return await asyncpg.create_pool(
        DATABASE_URL,
        min_size=1,
        max_size=2,
        init=_init_conn,
        setup=_setup_conn,
    )


@pytest.fixture
async def test_db_pool() -> AsyncIterator[asyncpg.Pool]:
    schema_name = f"test_migration_0015_{uuid4().hex}"
    admin_conn = await _connect_admin()
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)
        await admin_conn.execute(MIGRATION_0015_SQL)
        pool = await _make_pool(schema_name)
        try:
            yield pool
        finally:
            await pool.close()
    finally:
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


async def test_migration_creates_layer_analysis_plans_table(test_db_pool: asyncpg.Pool) -> None:
    async with test_db_pool.acquire() as conn:
        exists = await conn.fetchval("""
            SELECT EXISTS (
              SELECT 1 FROM information_schema.tables
              WHERE table_schema = current_schema()
                AND table_name = 'layer_analysis_plans'
            )
        """)
        assert exists is True


async def test_layer_analysis_plans_status_check_constraint(test_db_pool: asyncpg.Pool) -> None:
    async with test_db_pool.acquire() as conn:
        constraint_check = await conn.fetchval("""
            SELECT pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conname = 'layer_analysis_plans_status_check'
              AND conrelid = 'layer_analysis_plans'::regclass
        """)
        assert constraint_check is not None
        assert 'planning' in constraint_check
        assert 'active' in constraint_check
        assert 'completed' in constraint_check
        assert 'completed_with_failures' in constraint_check
        assert 'superseded' in constraint_check


async def test_migration_creates_analysis_windows_table(test_db_pool: asyncpg.Pool) -> None:
    async with test_db_pool.acquire() as conn:
        exists = await conn.fetchval("""
            SELECT EXISTS (
              SELECT 1 FROM information_schema.tables
              WHERE table_schema = current_schema()
                AND table_name = 'analysis_windows'
            )
        """)
        assert exists is True


async def test_analysis_windows_status_check_constraint(test_db_pool: asyncpg.Pool) -> None:
    async with test_db_pool.acquire() as conn:
        constraint_check = await conn.fetchval("""
            SELECT pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conname = 'analysis_windows_status_check'
              AND conrelid = 'analysis_windows'::regclass
        """)
        assert constraint_check is not None
        assert 'pending' in constraint_check
        assert 'running' in constraint_check
        assert 'completed' in constraint_check
        assert 'no_op' in constraint_check
        assert 'failed' in constraint_check


async def test_migration_adds_build_grammar_bundle_window_job_type(
    test_db_pool: asyncpg.Pool,
) -> None:
    async with test_db_pool.acquire() as conn:
        allowed = await conn.fetchval("""
            SELECT pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conname = 'reader_jobs_job_type_check'
              AND conrelid = 'reader_jobs'::regclass
        """)
        assert allowed is not None
        assert 'build_grammar_bundle_window' in allowed


async def test_partial_unique_index_active_plan(test_db_pool: asyncpg.Pool) -> None:
    """同 record/base/layer 只能有一个 active plan"""
    async with test_db_pool.acquire() as conn:
        idx_def = await conn.fetchval("""
            SELECT indexdef FROM pg_indexes
            WHERE schemaname = current_schema()
              AND indexname = 'uq_layer_analysis_plans_active'
        """)
        assert idx_def is not None
        assert 'WHERE' in idx_def
        assert 'planning' in idx_def
        assert 'active' in idx_def
