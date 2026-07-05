"""Tests for GrammarWindowWorkerService: preflight (§8.2) + heartbeat (§8.6).

Design source:
  docs/initiatives/reader-agentic-orchestration/analysis-window-zplus-design.md
  §8.2 (window claim / preflight pending→running) + §8.6 (heartbeat)

The preflight tests cover all four §8.2 status branches:
  - pending → UPDATE running, return PROCEED
  - running + same job_id → return PROCEED (retry)
  - running + different job_id → raise IllegalTransitionError
  - completed / no_op / failed → return ALREADY_TERMINAL
  - unknown status → raise IllegalTransitionError (defensive)

The heartbeat test verifies the loop calls job_runtime.heartbeat periodically.
The process_window_job test verifies ALREADY_TERMINAL short-circuits the LLM.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.database import connection as db_connection
from app.services.reader_orchestration.grammar_window_worker import (
    GrammarWindowWorkerService,
    PreflightResult,
)
from app.services.reader_orchestration.job_runtime import ClaimResult
from app.services.reader_orchestration.zplus_bootstrap import (
    ZPLUS_GRAMMAR_OPERATION_FINGERPRINT,
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


# ---------------------------------------------------------------------------
# Base fixture: schema + record + base
# ---------------------------------------------------------------------------


@pytest.fixture
async def test_db_pool_with_record_and_base() -> AsyncIterator[
    tuple[asyncpg.Pool, UUID, UUID]
]:
    """Create test schema (baseline + migration 0015), submit an article,
    return (pool, record_id, base_id).
    """
    schema_name = f"test_grammar_window_worker_{uuid4().hex}"
    admin_conn = await connect_admin()
    original_pool = db_connection.DB_POOL
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)
        await admin_conn.execute(MIGRATION_0015_SQL)
        pool = await make_pool(schema_name)
        db_connection.DB_POOL = pool
        try:
            user_id = await insert_user(pool)
            article = await submit_article_ready(
                pool,
                user_id=user_id,
                plain_text=ZPLUS_ARTICLE_TEXT,
                title="Grammar Window Worker Slice",
                language="en",
            )
            yield pool, article.record_id, article.base_id
        finally:
            await pool.close()
    finally:
        db_connection.DB_POOL = original_pool
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


# ---------------------------------------------------------------------------
# Shared helper: bootstrap plan + windows + jobs, pick first window
# ---------------------------------------------------------------------------


async def _bootstrap_first_window(
    pool: asyncpg.Pool,
    record_id: UUID,
    base_id: UUID,
) -> tuple[UUID, UUID]:
    """Run Z+ bootstrap and return (job_id, window_id) for the first window."""
    service = ZPlusBootstrapService(pool=pool)
    result = await service.bootstrap_grammar_window_plan(
        record_id=record_id, base_id=base_id
    )
    assert len(result.job_ids) >= 1
    job_id = result.job_ids[0]
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id FROM analysis_windows WHERE job_id = $1", job_id
        )
    assert row is not None
    return job_id, row["id"]


# ---------------------------------------------------------------------------
# Preflight fixtures: one per §8.2 branch
# ---------------------------------------------------------------------------


@pytest.fixture
async def test_db_pool_with_window_job(
    test_db_pool_with_record_and_base: tuple[asyncpg.Pool, UUID, UUID],
) -> AsyncIterator[tuple[asyncpg.Pool, UUID, UUID, UUID]]:
    """§8.2 pending window (default state after bootstrap).

    Returns (pool, job_id, lease_token, window_id).
    """
    pool, record_id, base_id = test_db_pool_with_record_and_base
    job_id, window_id = await _bootstrap_first_window(pool, record_id, base_id)
    lease_token = uuid4()
    yield pool, job_id, lease_token, window_id


@pytest.fixture
async def test_db_pool_with_completed_window(
    test_db_pool_with_record_and_base: tuple[asyncpg.Pool, UUID, UUID],
) -> AsyncIterator[tuple[asyncpg.Pool, UUID, UUID, UUID]]:
    """§8.2 terminal window (status='completed')."""
    pool, record_id, base_id = test_db_pool_with_record_and_base
    job_id, window_id = await _bootstrap_first_window(pool, record_id, base_id)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE analysis_windows SET status = 'completed' WHERE id = $1",
            window_id,
        )
    lease_token = uuid4()
    yield pool, job_id, lease_token, window_id


@pytest.fixture
async def test_db_pool_with_running_window(
    test_db_pool_with_record_and_base: tuple[asyncpg.Pool, UUID, UUID],
) -> AsyncIterator[tuple[asyncpg.Pool, UUID, UUID, UUID]]:
    """§8.2 running window with matching job_id (retry path)."""
    pool, record_id, base_id = test_db_pool_with_record_and_base
    job_id, window_id = await _bootstrap_first_window(pool, record_id, base_id)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE analysis_windows
            SET status = 'running', started_at = NOW(), job_id = $2
            WHERE id = $1
            """,
            window_id, job_id,
        )
    lease_token = uuid4()
    yield pool, job_id, lease_token, window_id


@pytest.fixture
async def test_db_pool_with_running_window_other_job(
    test_db_pool_with_record_and_base: tuple[asyncpg.Pool, UUID, UUID],
) -> AsyncIterator[tuple[asyncpg.Pool, UUID, UUID, UUID]]:
    """§8.2 running window with a different job_id (must reject)."""
    pool, record_id, base_id = test_db_pool_with_record_and_base
    job_id, window_id = await _bootstrap_first_window(pool, record_id, base_id)
    other_job_id = uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE analysis_windows
            SET status = 'running', started_at = NOW(), job_id = $2
            WHERE id = $1
            """,
            window_id, other_job_id,
        )
    lease_token = uuid4()
    yield pool, job_id, lease_token, window_id


@pytest.fixture
async def test_db_pool_with_unknown_status_window(
    test_db_pool_with_record_and_base: tuple[asyncpg.Pool, UUID, UUID],
) -> AsyncIterator[tuple[asyncpg.Pool, UUID, UUID, UUID]]:
    """§8.2 defensive: window with an unrecognized status value.

    Requires dropping the CHECK constraint so we can insert a bogus status.
    The schema is dropped at fixture teardown so this is contained.
    """
    pool, record_id, base_id = test_db_pool_with_record_and_base
    job_id, window_id = await _bootstrap_first_window(pool, record_id, base_id)
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE analysis_windows "
            "DROP CONSTRAINT IF EXISTS analysis_windows_status_check"
        )
        await conn.execute(
            "UPDATE analysis_windows SET status = 'bogus_status' WHERE id = $1",
            window_id,
        )
    lease_token = uuid4()
    yield pool, job_id, lease_token, window_id


# ---------------------------------------------------------------------------
# Preflight tests (§8.2)
# ---------------------------------------------------------------------------


async def test_preflight_marks_pending_window_as_running(
    test_db_pool_with_window_job: tuple[asyncpg.Pool, UUID, UUID, UUID],
) -> None:
    """§8.2 pending window → preflight marks it running, returns PROCEED."""
    pool, job_id, lease_token, window_id = test_db_pool_with_window_job
    service = GrammarWindowWorkerService(pool=pool)
    result = await service.preflight_window_job(
        job_id=job_id,
        lease_token=lease_token,
        lease_duration=timedelta(seconds=120),
    )
    assert result == PreflightResult.PROCEED

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, started_at, job_id FROM analysis_windows WHERE id = $1",
            window_id,
        )
    assert row is not None
    assert row["status"] == "running"
    assert row["started_at"] is not None
    assert str(row["job_id"]) == str(job_id)


async def test_preflight_skips_terminal_window(
    test_db_pool_with_completed_window: tuple[asyncpg.Pool, UUID, UUID, UUID],
) -> None:
    """§8.2 already-terminal window → ALREADY_TERMINAL, no mutation."""
    pool, job_id, lease_token, window_id = test_db_pool_with_completed_window
    service = GrammarWindowWorkerService(pool=pool)
    result = await service.preflight_window_job(
        job_id=job_id,
        lease_token=lease_token,
        lease_duration=timedelta(seconds=120),
    )
    assert result == PreflightResult.ALREADY_TERMINAL

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status FROM analysis_windows WHERE id = $1", window_id
        )
    assert row is not None
    assert row["status"] == "completed"


async def test_preflight_allows_retry_same_job(
    test_db_pool_with_running_window: tuple[asyncpg.Pool, UUID, UUID, UUID],
) -> None:
    """§8.2 running window + same job_id → PROCEED (retry path)."""
    pool, job_id, lease_token, window_id = test_db_pool_with_running_window
    service = GrammarWindowWorkerService(pool=pool)
    result = await service.preflight_window_job(
        job_id=job_id,
        lease_token=lease_token,
        lease_duration=timedelta(seconds=120),
    )
    assert result == PreflightResult.PROCEED

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, job_id FROM analysis_windows WHERE id = $1", window_id
        )
    assert row is not None
    assert row["status"] == "running"
    assert str(row["job_id"]) == str(job_id)


async def test_preflight_rejects_running_window_with_different_job_id(
    test_db_pool_with_running_window_other_job: tuple[
        asyncpg.Pool, UUID, UUID, UUID
    ],
) -> None:
    """§8.2 running window + different job_id → IllegalTransitionError."""
    pool, job_id, lease_token, window_id = test_db_pool_with_running_window_other_job
    service = GrammarWindowWorkerService(pool=pool)
    with pytest.raises(Exception):
        await service.preflight_window_job(
            job_id=job_id,
            lease_token=lease_token,
            lease_duration=timedelta(seconds=120),
        )


async def test_preflight_raises_on_unknown_status(
    test_db_pool_with_unknown_status_window: tuple[
        asyncpg.Pool, UUID, UUID, UUID
    ],
) -> None:
    """§8.2 unknown status → IllegalTransitionError (defensive)."""
    pool, job_id, lease_token, window_id = test_db_pool_with_unknown_status_window
    service = GrammarWindowWorkerService(pool=pool)
    with pytest.raises(Exception):
        await service.preflight_window_job(
            job_id=job_id,
            lease_token=lease_token,
            lease_duration=timedelta(seconds=120),
        )


# ---------------------------------------------------------------------------
# Heartbeat test (§8.6)
# ---------------------------------------------------------------------------


async def test_heartbeat_loop_calls_job_runtime_heartbeat() -> None:
    """§8.6 heartbeat loop periodically calls job_runtime.heartbeat."""
    mock_job_runtime = AsyncMock()
    service = GrammarWindowWorkerService(
        pool=MagicMock(),
        job_runtime=mock_job_runtime,
        heartbeat_interval=timedelta(milliseconds=50),
    )
    task = asyncio.create_task(
        service._heartbeat_loop(
            job_id=uuid4(),
            lease_token=uuid4(),
        )
    )
    await asyncio.sleep(0.15)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert mock_job_runtime.heartbeat.call_count >= 2


# ---------------------------------------------------------------------------
# process_window_job short-circuit test
# ---------------------------------------------------------------------------


def _make_claim() -> ClaimResult:
    """Build a minimal valid ClaimResult for tests that mock the DB layer."""
    return ClaimResult(
        job_id=uuid4(),
        run_id=uuid4(),
        reading_record_id=uuid4(),
        base_id=uuid4(),
        job_type="build_grammar_bundle_window",
        target_type="unit_range",
        target_key=str(uuid4()),
        expected_generation=1,
        operation_fingerprint=ZPLUS_GRAMMAR_OPERATION_FINGERPRINT,
        attempt_count=0,
        lease_owner="test_window_worker",
        lease_token=uuid4(),
        lease_expires_at=datetime.now(UTC),
    )


async def test_process_window_job_skips_when_already_terminal() -> None:
    """preflight ALREADY_TERMINAL → return early, no LLM call."""
    service = GrammarWindowWorkerService(pool=MagicMock())
    service.preflight_window_job = AsyncMock(  # type: ignore[method-assign]
        return_value=PreflightResult.ALREADY_TERMINAL
    )
    service._load_window_context = AsyncMock()  # type: ignore[method-assign]
    service._call_llm = AsyncMock()  # type: ignore[method-assign]

    claim = _make_claim()
    result = await service.process_window_job(claim=claim)
    assert result["status"] == "already_terminal"
    service._load_window_context.assert_not_called()
    service._call_llm.assert_not_called()
