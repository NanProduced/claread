"""Tests for Z+ vs legacy bootstrap routing in ``bootstrap_missing_jobs``.

Design source:
  docs/initiatives/reader-agentic-orchestration/analysis-window-zplus-design.md
  §9 worker migration (bootstrap routing)

Routing contract (P1-1 修正后):
    - 默认走 Z+ 路径 (调用 ``ZPlusBootstrapService.bootstrap_grammar_window_plan``)，
      无论 record 是否已有 Z+ plan。``ZPlusBootstrapService`` 内部幂等。
    - ``force_legacy_grammar=True`` 时回退到 legacy per-unit 路径
      (现有 ``_bootstrap_grammar_jobs``)。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.database import connection as db_connection
from app.services.reader_orchestration.job_bootstrap import (
    EnhancementJobBootstrapService,
)
from app.services.reader_orchestration.zplus_bootstrap import (
    ZPlusBootstrapService,
)
from tests.reader_orchestration_test_support import (
    BASELINE_SQL,
    connect_admin,
    insert_user,
    make_pool,
    submit_article_ready,
)

pytestmark = pytest.mark.anyio

REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATION_0015_SQL = (
    REPO_ROOT / "infra" / "migrations" / "0015_layer_analysis_plans.sql"
).read_text(encoding="utf-8")

ZPLUS_ARTICLE_TEXT = (
    "Not only did the team revise the plan, but they also clarified the timeline. "
    "Everyone understood the tradeoff.\n\n"
    "The committee, which had spent six months reviewing export data, "
    "labor surveys, and municipal tax receipts that rarely lined up neatly, "
    "claimed that the recovery was broad enough to justify ending the emergency "
    "grant program.\n\n"
    "Several shop owners warned that the headline numbers hid a "
    "more fragile street-level reality, because customers were still delaying "
    "purchases whenever wages, school fees, and transport costs rose in the same "
    "week."
)


async def _build_test_schema_and_pool() -> tuple[asyncpg.Pool, str]:
    """Create a fresh test schema with baseline + migration 0015, return
    (pool, schema_name). Caller is responsible for cleanup.
    """
    schema_name = f"test_bootstrap_routing_{uuid4().hex}"
    admin_conn = await connect_admin()
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)
        await admin_conn.execute(MIGRATION_0015_SQL)
    finally:
        await admin_conn.close()
    pool = await make_pool(schema_name)
    return pool, schema_name


async def _drop_schema(schema_name: str) -> None:
    admin_conn = await connect_admin()
    try:
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
    finally:
        await admin_conn.close()


@pytest.fixture
async def test_db_pool_with_zplus_plan() -> AsyncIterator[
    tuple[asyncpg.Pool, UUID, UUID, UUID]
]:
    """Create schema, submit article, AND pre-create a Z+ plan + windows + jobs.

    Returns (pool, record_id, base_id, user_id).
    """
    pool, schema_name = await _build_test_schema_and_pool()
    original_pool = db_connection.DB_POOL
    db_connection.DB_POOL = pool
    try:
        user_id = await insert_user(pool)
        article = await submit_article_ready(
            pool,
            user_id=user_id,
            plain_text=ZPLUS_ARTICLE_TEXT,
            title="Bootstrap Routing ZPlus",
            language="en",
        )
        # Pre-create the Z+ plan + windows + jobs.
        zplus_service = ZPlusBootstrapService(pool=pool)
        await zplus_service.bootstrap_grammar_window_plan(
            record_id=article.record_id,
            base_id=article.base_id,
        )
        yield pool, article.record_id, article.base_id, user_id
    finally:
        db_connection.DB_POOL = original_pool
        await pool.close()
        await _drop_schema(schema_name)


@pytest.fixture
async def test_db_pool_without_plan() -> AsyncIterator[
    tuple[asyncpg.Pool, UUID, UUID, UUID]
]:
    """Create schema + submit article, but do NOT create a Z+ plan.

    Returns (pool, record_id, base_id, user_id).
    """
    pool, schema_name = await _build_test_schema_and_pool()
    original_pool = db_connection.DB_POOL
    db_connection.DB_POOL = pool
    try:
        user_id = await insert_user(pool)
        article = await submit_article_ready(
            pool,
            user_id=user_id,
            plain_text=ZPLUS_ARTICLE_TEXT,
            title="Bootstrap Routing Legacy",
            language="en",
        )
        yield pool, article.record_id, article.base_id, user_id
    finally:
        db_connection.DB_POOL = original_pool
        await pool.close()
        await _drop_schema(schema_name)


async def test_bootstrap_uses_zplus_path_when_plan_exists(
    test_db_pool_with_zplus_plan: tuple[asyncpg.Pool, UUID, UUID, UUID],
) -> None:
    """record 已有 Z+ plan 时，grammar bootstrap 走 Z+ 路径（幂等复用）。"""
    pool, record_id, base_id, user_id = test_db_pool_with_zplus_plan
    service = EnhancementJobBootstrapService(pool=pool)
    await service.bootstrap_missing_jobs(record_id=record_id, user_id=user_id)

    async with pool.acquire() as conn:
        window_jobs = await conn.fetch(
            "SELECT * FROM reader_jobs WHERE job_type = 'build_grammar_bundle_window'"
        )
        legacy_jobs = await conn.fetch(
            "SELECT * FROM reader_jobs "
            "WHERE job_type = 'build_grammar_bundle' AND target_type = 'unit'"
        )
        assert len(window_jobs) > 0
        assert len(legacy_jobs) == 0


async def test_bootstrap_uses_zplus_path_by_default(
    test_db_pool_without_plan: tuple[asyncpg.Pool, UUID, UUID, UUID],
) -> None:
    """P1-1: 默认走 Z+ 路径，无需 pre-create plan。

    ``bootstrap_missing_jobs`` 不传 ``force_legacy_grammar`` 时默认走 Z+，
    由 ``ZPlusBootstrapService.bootstrap_grammar_window_plan`` 创建 plan +
    windows + window jobs。
    """
    pool, record_id, base_id, user_id = test_db_pool_without_plan
    service = EnhancementJobBootstrapService(pool=pool)
    await service.bootstrap_missing_jobs(record_id=record_id, user_id=user_id)

    async with pool.acquire() as conn:
        window_jobs = await conn.fetch(
            "SELECT * FROM reader_jobs WHERE job_type = 'build_grammar_bundle_window'"
        )
        legacy_jobs = await conn.fetch(
            "SELECT * FROM reader_jobs "
            "WHERE job_type = 'build_grammar_bundle' AND target_type = 'unit'"
        )
        assert len(window_jobs) > 0
        assert len(legacy_jobs) == 0


async def test_bootstrap_uses_legacy_path_when_forced(
    test_db_pool_without_plan: tuple[asyncpg.Pool, UUID, UUID, UUID],
) -> None:
    """``force_legacy_grammar=True`` 时回退到 legacy per-unit 路径。"""
    pool, record_id, base_id, user_id = test_db_pool_without_plan
    service = EnhancementJobBootstrapService(pool=pool)
    await service.bootstrap_missing_jobs(
        record_id=record_id,
        user_id=user_id,
        force_legacy_grammar=True,
    )

    async with pool.acquire() as conn:
        window_jobs = await conn.fetch(
            "SELECT * FROM reader_jobs WHERE job_type = 'build_grammar_bundle_window'"
        )
        legacy_jobs = await conn.fetch(
            "SELECT * FROM reader_jobs "
            "WHERE job_type = 'build_grammar_bundle' AND target_type = 'unit'"
        )
        assert len(window_jobs) == 0
        assert len(legacy_jobs) > 0


async def test_bootstrap_zplus_idempotent(
    test_db_pool_with_zplus_plan: tuple[asyncpg.Pool, UUID, UUID, UUID],
) -> None:
    """Z+ 路径重复调用不重复创建 plan/windows/jobs。"""
    pool, record_id, base_id, user_id = test_db_pool_with_zplus_plan
    service = EnhancementJobBootstrapService(pool=pool)

    await service.bootstrap_missing_jobs(record_id=record_id, user_id=user_id)

    async with pool.acquire() as conn:
        jobs_after_first = await conn.fetchval(
            "SELECT count(*) FROM reader_jobs "
            "WHERE job_type = 'build_grammar_bundle_window'"
        )

    await service.bootstrap_missing_jobs(record_id=record_id, user_id=user_id)

    async with pool.acquire() as conn:
        jobs_after_second = await conn.fetchval(
            "SELECT count(*) FROM reader_jobs "
            "WHERE job_type = 'build_grammar_bundle_window'"
        )

    assert jobs_after_first == jobs_after_second
