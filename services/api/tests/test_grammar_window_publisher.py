"""Tests for GrammarWindowPublisher: multi-unit publish transaction.

Design source:
  docs/initiatives/reader-agentic-orchestration/analysis-window-zplus-design.md
  §3.3 (unit-scoped publish) + §8.4 (publish transaction) + §8.5 (lock coverage)
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.database import connection as db_connection
from app.services.reader_orchestration.grammar_window_publisher import (
    GrammarWindowPublisher,
    PublishedWindowResult,
)
from app.services.reader_orchestration.window_selector import CandidateItem
from app.services.reader_orchestration.zplus_bootstrap import ZPlusBootstrapService
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

ARTICLE_TEXT = (
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


@dataclass
class _TestEnv:
    pool: asyncpg.Pool
    admin_conn: asyncpg.Connection
    schema_name: str
    original_pool: asyncpg.Pool | None
    plan_id: UUID
    window_id: UUID
    job_id: UUID
    target_unit_ids: list[str]
    target_anchor_ids: list[str]


async def _setup_test_env() -> _TestEnv:
    schema_name = f"test_grammar_window_pub_{uuid4().hex}"
    admin_conn = await connect_admin()
    original_pool = db_connection.DB_POOL
    await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
    await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
    await admin_conn.execute(BASELINE_SQL)
    await admin_conn.execute(MIGRATION_0015_SQL)
    pool = await make_pool(schema_name)
    db_connection.DB_POOL = pool

    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool,
        user_id=user_id,
        plain_text=ARTICLE_TEXT,
        title="Grammar Window Pub Test",
        language="en",
    )
    service = ZPlusBootstrapService(pool=pool)
    result = await service.bootstrap_grammar_window_plan(
        record_id=article.record_id, base_id=article.base_id,
    )

    async with pool.acquire() as conn:
        window = await conn.fetchrow(
            """
            SELECT id, job_id, target_unit_ids, target_anchor_ids
            FROM analysis_windows
            WHERE plan_id = $1
            ORDER BY window_index
            LIMIT 1
            """,
            result.plan_id,
        )

    return _TestEnv(
        pool=pool,
        admin_conn=admin_conn,
        schema_name=schema_name,
        original_pool=original_pool,
        plan_id=result.plan_id,
        window_id=window["id"],
        job_id=window["job_id"],
        target_unit_ids=list(window["target_unit_ids"]),
        target_anchor_ids=list(window["target_anchor_ids"]),
    )


async def _cleanup_test_env(env: _TestEnv) -> None:
    await env.pool.close()
    db_connection.DB_POOL = env.original_pool
    await env.admin_conn.execute(f'DROP SCHEMA IF EXISTS "{env.schema_name}" CASCADE')
    await env.admin_conn.close()


async def _claim_job(pool: asyncpg.Pool, job_id: UUID) -> UUID:
    lease_token = uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE reader_jobs
            SET status = 'claimed',
                lease_owner = 'test-worker',
                lease_token = $2,
                lease_expires_at = NOW() + INTERVAL '1 hour',
                claimed_at = NOW(),
                attempt_count = COALESCE(attempt_count, 0) + 1,
                updated_at = NOW()
            WHERE id = $1
            """,
            job_id,
            lease_token,
        )
    return lease_token


def _make_candidates(
    target_unit_ids: list[str], target_anchor_ids: list[str]
) -> list[CandidateItem]:
    if not target_unit_ids or not target_anchor_ids:
        return []
    candidates: list[CandidateItem] = []
    # Create grammar_note candidates (one per unit, up to 2, distinct anchors)
    for i, unit_id in enumerate(target_unit_ids[:2]):
        if i >= len(target_anchor_ids):
            break
        candidates.append(
            CandidateItem(
                item_type="grammar_note",
                anchor_segment_id=target_anchor_ids[i],
                spans=[{"unit_id": unit_id}],
                semantic_dedup_key=f"grammar-dedup-{i}",
                pattern_key=f"grammar-pattern-{i}",
                quality_score=0.8 - i * 0.1,
            )
        )
    # Create sentence_analysis candidate for first unit
    candidates.append(
        CandidateItem(
            item_type="sentence_analysis",
            anchor_segment_id=target_anchor_ids[0],
            spans=[{"unit_id": target_unit_ids[0]}],
            semantic_dedup_key="sentence-dedup-0",
            pattern_key=None,
            quality_score=0.9,
        )
    )
    return candidates


@pytest.fixture
async def test_db_pool_with_window_and_candidates() -> AsyncIterator[
    tuple[asyncpg.Pool, UUID, UUID, UUID, UUID, list[CandidateItem]]
]:
    """Window status='running', job status='claimed', with test candidates."""
    env = await _setup_test_env()
    try:
        async with env.pool.acquire() as conn:
            await conn.execute(
                "UPDATE analysis_windows SET status = 'running' WHERE id = $1",
                env.window_id,
            )
        lease_token = await _claim_job(env.pool, env.job_id)
        candidates = _make_candidates(env.target_unit_ids, env.target_anchor_ids)
        yield (
            env.pool,
            env.job_id,
            lease_token,
            env.plan_id,
            env.window_id,
            candidates,
        )
    finally:
        await _cleanup_test_env(env)


@pytest.fixture
async def test_db_pool_with_pending_window() -> AsyncIterator[
    tuple[asyncpg.Pool, UUID, UUID, UUID, UUID]
]:
    """Window status='pending' (not 'running'); job claimed."""
    env = await _setup_test_env()
    try:
        # Leave window status as 'pending' (default from bootstrap)
        lease_token = await _claim_job(env.pool, env.job_id)
        yield (env.pool, env.job_id, lease_token, env.plan_id, env.window_id)
    finally:
        await _cleanup_test_env(env)


@pytest.fixture
async def test_db_pool_with_queued_job_window() -> AsyncIterator[
    tuple[asyncpg.Pool, UUID, UUID, UUID, UUID]
]:
    """Window status='running'; job status='queued' (not 'claimed')."""
    env = await _setup_test_env()
    try:
        async with env.pool.acquire() as conn:
            await conn.execute(
                "UPDATE analysis_windows SET status = 'running' WHERE id = $1",
                env.window_id,
            )
        # Leave job status as 'queued' (default from bootstrap)
        lease_token = uuid4()  # token won't be checked (job status check fails first)
        yield (env.pool, env.job_id, lease_token, env.plan_id, env.window_id)
    finally:
        await _cleanup_test_env(env)


@pytest.fixture
async def test_db_pool_with_window_no_candidates() -> AsyncIterator[
    tuple[asyncpg.Pool, UUID, UUID, UUID, UUID]
]:
    """Window status='running'; job claimed; empty candidates list."""
    env = await _setup_test_env()
    try:
        async with env.pool.acquire() as conn:
            await conn.execute(
                "UPDATE analysis_windows SET status = 'running' WHERE id = $1",
                env.window_id,
            )
        lease_token = await _claim_job(env.pool, env.job_id)
        yield (env.pool, env.job_id, lease_token, env.plan_id, env.window_id)
    finally:
        await _cleanup_test_env(env)


async def test_publish_window_publishes_multiple_units_in_one_transaction(
    test_db_pool_with_window_and_candidates: tuple[
        asyncpg.Pool, UUID, UUID, UUID, UUID, list[CandidateItem]
    ],
) -> None:
    """§3.3 One window transaction publishes multiple unit-targeted layers."""
    pool, job_id, lease_token, plan_id, window_id, candidates = (
        test_db_pool_with_window_and_candidates
    )
    publisher = GrammarWindowPublisher(pool=pool)
    result = await publisher.publish_window_grammar_bundle(
        job_id=job_id,
        lease_token=lease_token,
        plan_id=plan_id,
        window_id=window_id,
        candidates=candidates,
    )
    assert result.accepted_count > 0
    assert len(result.grammar_note_layer_ids) >= 1
    assert result.skipped is False
    assert isinstance(result, PublishedWindowResult)

    # Verify each accepted layer has target_scope='unit' and status='published'
    async with pool.acquire() as conn:
        for layer_id in result.grammar_note_layer_ids:
            layer = await conn.fetchrow(
                "SELECT target_scope, target_key, status FROM enhancement_layers WHERE id = $1",
                layer_id,
            )
            assert layer is not None
            assert layer["target_scope"] == "unit"
            assert layer["status"] == "published"


async def test_publish_window_skips_when_window_status_not_running(
    test_db_pool_with_pending_window: tuple[asyncpg.Pool, UUID, UUID, UUID, UUID],
) -> None:
    """Window status != 'running' → publish skipped."""
    pool, job_id, lease_token, plan_id, window_id = test_db_pool_with_pending_window
    publisher = GrammarWindowPublisher(pool=pool)
    result = await publisher.publish_window_grammar_bundle(
        job_id=job_id,
        lease_token=lease_token,
        plan_id=plan_id,
        window_id=window_id,
        candidates=[],
    )
    assert result.skipped is True
    assert result.accepted_count == 0
    assert result.grammar_note_layer_ids == ()


async def test_publish_window_rejects_when_job_status_not_claimed(
    test_db_pool_with_queued_job_window: tuple[asyncpg.Pool, UUID, UUID, UUID, UUID],
) -> None:
    """Job status != 'claimed' → IllegalTransitionError."""
    pool, job_id, lease_token, plan_id, window_id = test_db_pool_with_queued_job_window
    publisher = GrammarWindowPublisher(pool=pool)
    from app.services.reader_orchestration.job_runtime import IllegalTransitionError

    with pytest.raises(IllegalTransitionError):
        await publisher.publish_window_grammar_bundle(
            job_id=job_id,
            lease_token=lease_token,
            plan_id=plan_id,
            window_id=window_id,
            candidates=[],
        )


async def test_publish_window_updates_ledger_after_publish(
    test_db_pool_with_window_and_candidates: tuple[
        asyncpg.Pool, UUID, UUID, UUID, UUID, list[CandidateItem]
    ],
) -> None:
    """Publish updates ledger: budget_used / covered_window_ids."""
    pool, job_id, lease_token, plan_id, window_id, candidates = (
        test_db_pool_with_window_and_candidates
    )
    publisher = GrammarWindowPublisher(pool=pool)
    result = await publisher.publish_window_grammar_bundle(
        job_id=job_id,
        lease_token=lease_token,
        plan_id=plan_id,
        window_id=window_id,
        candidates=candidates,
    )

    async with pool.acquire() as conn:
        plan = await conn.fetchrow(
            "SELECT * FROM layer_analysis_plans WHERE id = $1",
            plan_id,
        )
        budget_used = (
            plan["budget_used"]
            if isinstance(plan["budget_used"], dict)
            else json.loads(plan["budget_used"])
        )
        grammar_used = budget_used.get("grammar_note", {}).get("count", 0)
        assert grammar_used > 0

        covered = (
            plan["covered_window_ids"]
            if isinstance(plan["covered_window_ids"], list)
            else json.loads(plan["covered_window_ids"])
        )
        assert str(window_id) in covered or window_id in covered


async def test_publish_window_marks_window_completed_after_publish(
    test_db_pool_with_window_and_candidates: tuple[
        asyncpg.Pool, UUID, UUID, UUID, UUID, list[CandidateItem]
    ],
) -> None:
    """Publish success → window.status='completed', completed_at set."""
    pool, job_id, lease_token, plan_id, window_id, candidates = (
        test_db_pool_with_window_and_candidates
    )
    publisher = GrammarWindowPublisher(pool=pool)
    await publisher.publish_window_grammar_bundle(
        job_id=job_id,
        lease_token=lease_token,
        plan_id=plan_id,
        window_id=window_id,
        candidates=candidates,
    )
    async with pool.acquire() as conn:
        window = await conn.fetchrow(
            "SELECT status, completed_at FROM analysis_windows WHERE id = $1",
            window_id,
        )
        assert window is not None
        assert window["status"] == "completed"
        assert window["completed_at"] is not None


async def test_publish_window_marks_window_no_op_when_no_accepted(
    test_db_pool_with_window_no_candidates: tuple[
        asyncpg.Pool, UUID, UUID, UUID, UUID
    ],
) -> None:
    """All candidates rejected / empty → window.status='no_op'."""
    pool, job_id, lease_token, plan_id, window_id = test_db_pool_with_window_no_candidates
    publisher = GrammarWindowPublisher(pool=pool)
    result = await publisher.publish_window_grammar_bundle(
        job_id=job_id,
        lease_token=lease_token,
        plan_id=plan_id,
        window_id=window_id,
        candidates=[],
    )
    assert result.accepted_count == 0

    async with pool.acquire() as conn:
        window = await conn.fetchrow(
            "SELECT status FROM analysis_windows WHERE id = $1",
            window_id,
        )
        assert window is not None
        assert window["status"] == "no_op"


async def test_publish_window_marks_job_succeeded(
    test_db_pool_with_window_and_candidates: tuple[
        asyncpg.Pool, UUID, UUID, UUID, UUID, list[CandidateItem]
    ],
) -> None:
    """Publish → reader_jobs.status='succeeded'."""
    pool, job_id, lease_token, plan_id, window_id, candidates = (
        test_db_pool_with_window_and_candidates
    )
    publisher = GrammarWindowPublisher(pool=pool)
    await publisher.publish_window_grammar_bundle(
        job_id=job_id,
        lease_token=lease_token,
        plan_id=plan_id,
        window_id=window_id,
        candidates=candidates,
    )
    async with pool.acquire() as conn:
        job = await conn.fetchrow(
            "SELECT status, rationale_code FROM reader_jobs WHERE id = $1",
            job_id,
        )
        assert job is not None
        assert job["status"] == "succeeded"
