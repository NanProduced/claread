"""Tests for ReaderEnhancementPipelineRunner Z+ window dispatch (Task C4).

Design source:
  docs/initiatives/reader-agentic-orchestration/analysis-window-zplus-design.md
  §9 (worker migration): ``grammar_bundle_window`` WorkerType registered in
  ``pipeline_runner._dispatch_worker_attempt`` ahead of legacy
  ``grammar_bundle``.

Coverage:
  1. ``candidates_ready`` → window worker called + publisher called.
  2. ``already_terminal`` → window worker called, publisher NOT called.
  3. ``worker_order`` — grammar_bundle_window runs before legacy
     grammar_bundle when both workers are registered.
  4. Backward compat — when ``_grammar_window_worker`` is not registered,
     ``worker_order`` excludes grammar_bundle_window (legacy 4-worker path).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.database import connection as db_connection
from app.services.reader_orchestration.job_bootstrap import (
    EnhancementBootstrapJobCounts,
    EnhancementBootstrapSummary,
    EnhancementJobBootstrapService,
)
from app.services.reader_orchestration.pipeline_runner import (
    ReaderEnhancementPipelineRunner,
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

LEASE_DURATION = timedelta(seconds=30)


# ---------------------------------------------------------------------------
# Fixture: schema + record + base + Z+ window jobs (no legacy grammar jobs)
# ---------------------------------------------------------------------------


@pytest.fixture
async def test_db_pool_with_window_job_only() -> AsyncIterator[
    tuple[asyncpg.Pool, UUID, UUID, UUID]
]:
    """Submit article + run Z+ bootstrap (creates window jobs only).

    Returns ``(pool, record_id, user_id, base_id)``. Legacy grammar_bundle
    jobs are NOT created — the mock bootstrap in each test returns a 0-count
    summary so legacy workers find no claimable jobs.
    """
    schema_name = f"test_pipeline_window_dispatch_{uuid4().hex}"
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
                title="Pipeline Window Dispatch Slice",
                language="en",
            )
            # Bootstrap Z+ plan + windows + window reader_jobs.
            zplus = ZPlusBootstrapService(pool=pool)
            await zplus.bootstrap_grammar_window_plan(
                record_id=article.record_id,
                base_id=article.base_id,
            )
            yield pool, article.record_id, user_id, article.base_id
        finally:
            await pool.close()
    finally:
        db_connection.DB_POOL = original_pool
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


def _make_mock_bootstrap(
    record_id: UUID,
    base_id: UUID,
    expected_generation: int = 1,
) -> EnhancementJobBootstrapService:
    """Build a mock bootstrap that returns a 0-count summary.

    The pipeline runner uses ``bootstrap.base_id`` /
    ``bootstrap.expected_generation`` to scope subsequent ``claim_next_job``
    calls, so the mock must carry the real base_id / generation. No legacy
    jobs are actually created, so the legacy workers find no claimable jobs.
    """
    mock = AsyncMock(spec=EnhancementJobBootstrapService)
    mock.bootstrap_missing_jobs = AsyncMock(
        return_value=EnhancementBootstrapSummary(
            record_id=record_id,
            base_id=base_id,
            expected_generation=expected_generation,
            last_event_sequence=0,
            job_counts=EnhancementBootstrapJobCounts(),
        )
    )
    return mock


def _make_runner(
    pool: asyncpg.Pool,
    record_id: UUID,
    base_id: UUID,
) -> ReaderEnhancementPipelineRunner:
    """Build a runner with a mock bootstrap (no legacy jobs) + real DB pool.

    ``_grammar_window_worker`` / ``_grammar_window_publisher`` are NOT set
    here — each test overrides them with mocks after construction.
    ``enable_zplus_grammar=False`` 保证构造时不创建 real Z+ worker /
    publisher（避免触达 real LLM）；测试通过 override ``_grammar_window_worker``
    / ``_grammar_window_publisher`` 属性来注入 mock，``run()`` 在运行时
    根据 ``self._grammar_window_worker is not None`` 决定 worker_order。
    """
    return ReaderEnhancementPipelineRunner(
        pool=pool,
        bootstrap_service=_make_mock_bootstrap(record_id, base_id),
        enable_zplus_grammar=False,
    )


# ---------------------------------------------------------------------------
# Test 1: candidates_ready → publisher called
# ---------------------------------------------------------------------------


async def test_pipeline_runner_dispatches_window_job_to_window_worker(
    test_db_pool_with_window_job_only: tuple[asyncpg.Pool, UUID, UUID, UUID],
) -> None:
    """``build_grammar_bundle_window`` job routes to GrammarWindowWorkerService,
    and ``candidates_ready`` triggers GrammarWindowPublisher."""
    pool, record_id, user_id, base_id = test_db_pool_with_window_job_only
    runner = _make_runner(pool, record_id, base_id)

    mock_worker = AsyncMock()
    mock_worker.process_window_job = AsyncMock(
        return_value={
            "status": "candidates_ready",
            "candidates": [],  # empty candidates → publisher still called
        }
    )
    mock_publisher = AsyncMock()

    runner._grammar_window_worker = mock_worker
    runner._grammar_window_publisher = mock_publisher

    await runner.run(
        record_id=record_id,
        user_id=user_id,
        lease_owner="test-window-dispatch",
        lease_duration=LEASE_DURATION,
        max_ticks=10,
        max_jobs=10,
    )

    mock_worker.process_window_job.assert_called()
    mock_publisher.publish_window_grammar_bundle.assert_called()


# ---------------------------------------------------------------------------
# Test 2: already_terminal → publisher NOT called
# ---------------------------------------------------------------------------


async def test_pipeline_runner_skips_already_terminal_window(
    test_db_pool_with_window_job_only: tuple[asyncpg.Pool, UUID, UUID, UUID],
) -> None:
    """When window worker returns ``already_terminal``, publisher is skipped."""
    pool, record_id, user_id, base_id = test_db_pool_with_window_job_only
    runner = _make_runner(pool, record_id, base_id)

    mock_worker = AsyncMock()
    mock_worker.process_window_job = AsyncMock(
        return_value={"status": "already_terminal"}
    )
    mock_publisher = AsyncMock()

    runner._grammar_window_worker = mock_worker
    runner._grammar_window_publisher = mock_publisher

    await runner.run(
        record_id=record_id,
        user_id=user_id,
        lease_owner="test-terminal-skip",
        lease_duration=LEASE_DURATION,
        max_ticks=10,
        max_jobs=10,
    )

    mock_worker.process_window_job.assert_called()
    mock_publisher.publish_window_grammar_bundle.assert_not_called()


# ---------------------------------------------------------------------------
# Test 3: worker_order — grammar_bundle_window before legacy grammar_bundle
# ---------------------------------------------------------------------------


async def test_pipeline_runner_worker_order_grammar_window_before_legacy(
    test_db_pool_with_window_job_only: tuple[asyncpg.Pool, UUID, UUID, UUID],
) -> None:
    """``grammar_bundle_window`` is dispatched before legacy ``grammar_bundle``."""
    pool, record_id, user_id, base_id = test_db_pool_with_window_job_only
    runner = _make_runner(pool, record_id, base_id)

    call_order: list[str] = []

    mock_window_worker = AsyncMock()

    async def _window_side_effect(*, claim: Any) -> dict[str, Any]:
        call_order.append("window")
        return {"status": "already_terminal"}

    mock_window_worker.process_window_job = AsyncMock(
        side_effect=_window_side_effect
    )

    mock_legacy_grammar_service = AsyncMock()

    async def _legacy_side_effect(**kwargs: Any) -> None:
        call_order.append("legacy")
        return None  # no claimable legacy grammar_bundle job → no_job

    mock_legacy_grammar_service.process_next_grammar_job_for_record = AsyncMock(
        side_effect=_legacy_side_effect
    )

    # Publisher is required by the guard in ``_run_grammar_window_attempt``
    # (both worker AND publisher must be registered). Use a no-op mock since
    # ``already_terminal`` never reaches the publisher.
    mock_publisher = AsyncMock()

    runner._grammar_window_worker = mock_window_worker
    runner._grammar_window_publisher = mock_publisher
    # Override the legacy grammar worker service (set in __init__) with the
    # mock so we can observe call order.
    runner._grammar_worker_service = mock_legacy_grammar_service

    await runner.run(
        record_id=record_id,
        user_id=user_id,
        lease_owner="test-worker-order",
        lease_duration=LEASE_DURATION,
        max_ticks=10,
        max_jobs=10,
    )

    # Window worker must have been called at least once (Round 1 claim
    # succeeds for the single window job bootstrapped by ZPlusBootstrapService).
    mock_window_worker.process_window_job.assert_called()
    # Legacy grammar worker is also called each round (returns no_job since
    # no legacy grammar_bundle jobs exist). Verify window was called before
    # any legacy call.
    assert "window" in call_order
    if "legacy" in call_order:
        window_idx = call_order.index("window")
        legacy_idx = call_order.index("legacy")
        assert window_idx < legacy_idx, (
            f"grammar_bundle_window must run before grammar_bundle; "
            f"got order={call_order}"
        )


# ---------------------------------------------------------------------------
# Test 4: backward compat — no window worker registered → 4-worker path
# ---------------------------------------------------------------------------


async def test_pipeline_runner_without_window_worker_excludes_window_dispatch(
    test_db_pool_with_window_job_only: tuple[asyncpg.Pool, UUID, UUID, UUID],
) -> None:
    """When ``_grammar_window_worker`` is None, ``worker_order`` keeps the
    legacy 4-worker tuple so existing deployments / tests are unaffected.

    This preserves backward compatibility: the Z+ path only activates when
    the caller explicitly opts in by registering the window worker.
    """
    pool, record_id, user_id, base_id = test_db_pool_with_window_job_only
    runner = _make_runner(pool, record_id, base_id)

    # _grammar_window_worker / _grammar_window_publisher are None (default).
    summary = await runner.run(
        record_id=record_id,
        user_id=user_id,
        lease_owner="test-no-window-worker",
        lease_duration=LEASE_DURATION,
        max_ticks=10,
        max_jobs=10,
    )

    # grammar_bundle_window tick count stays at 0 (worker_order excluded it).
    assert summary.worker_tick_counts.grammar_bundle_window == 0
    # Pipeline ran without dispatching the window worker, even though window
    # jobs exist in the DB — the Z+ path is opt-in only.
