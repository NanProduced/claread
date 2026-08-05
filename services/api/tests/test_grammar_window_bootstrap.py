"""Tests for GrammarWindowBootstrapService: bootstrap plan + windows + reader_jobs.

Design source:
  docs/initiatives/reader-agentic-orchestration/modules/enhancement-layers-and-parsed.md
  §3.2 (Window Job contract) + §4.1 (layer_analysis_plans) + §7.3 (budget caps)
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.database import connection as db_connection
from app.services.reader_orchestration.grammar_window_bootstrap import (
    GRAMMAR_WINDOW_JOB_TYPE,
    GRAMMAR_WINDOW_OPERATION_FINGERPRINT,
    GrammarWindowBootstrapResult,
    GrammarWindowBootstrapService,
)
from tests.reader_orchestration_test_support import (
    BASELINE_SQL,
    connect_admin,
    insert_user,
    make_pool,
    submit_article_ready,
)

pytestmark = pytest.mark.anyio


GRAMMAR_WINDOW_ARTICLE_TEXT = (
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


@pytest.fixture
async def test_db_pool_with_record_and_base() -> AsyncIterator[
    tuple[asyncpg.Pool, UUID, UUID]
]:
    """Create a test schema with baseline + migration 0015, submit an article,
    and return (pool, record_id, base_id).
    """
    schema_name = f"test_grammar_window_bootstrap_{uuid4().hex}"
    admin_conn = await connect_admin()
    original_pool = db_connection.DB_POOL
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)
        pool = await make_pool(schema_name)
        db_connection.DB_POOL = pool
        try:
            user_id = await insert_user(pool)
            article = await submit_article_ready(
                pool,
                user_id=user_id,
                plain_text=GRAMMAR_WINDOW_ARTICLE_TEXT,
                title="grammar-window Bootstrap Slice",
                language="en",
            )
            yield pool, article.record_id, article.base_id
        finally:
            await pool.close()
    finally:
        db_connection.DB_POOL = original_pool
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


async def test_bootstrap_creates_plan_windows_and_jobs(
    test_db_pool_with_record_and_base: tuple[asyncpg.Pool, UUID, UUID],
) -> None:
    """grammar-window bootstrap creates 1 plan + N windows + N reader_jobs."""
    pool, record_id, base_id = test_db_pool_with_record_and_base
    service = GrammarWindowBootstrapService(pool=pool)
    result = await service.bootstrap_grammar_window_plan(
        record_id=record_id, base_id=base_id
    )
    assert result.plan_id is not None
    assert isinstance(result.plan_id, UUID)
    assert len(result.windows) >= 1
    assert len(result.job_ids) == len(result.windows)

    # Verify DB state.
    async with pool.acquire() as conn:
        plan = await conn.fetchrow(
            "SELECT * FROM layer_analysis_plans WHERE id = $1",
            result.plan_id,
        )
        assert plan is not None
        assert plan["layer_type"] == "grammar_bundle"
        assert plan["status"] == "active"
        assert plan["policy_version"] == "zplus_grammar_bundle_v1"

        windows = await conn.fetch(
            "SELECT * FROM analysis_windows WHERE plan_id = $1 ORDER BY window_index",
            result.plan_id,
        )
        assert len(windows) == len(result.windows)
        for w in windows:
            assert w["status"] == "pending"
            assert w["job_id"] is not None

        # Verify reader_jobs.
        for job_id in result.job_ids:
            job = await conn.fetchrow(
                "SELECT * FROM reader_jobs WHERE id = $1",
                job_id,
            )
            assert job is not None
            assert job["job_type"] == GRAMMAR_WINDOW_JOB_TYPE
            assert job["target_type"] == "unit_range"
            job_input = job["input_json"]
            assert job_input["semantic_contract_version"] is None
            assert (
                job_input["automatic_layer_policy_resolver_version"] == "legacy_open"
            )
            assert job_input["automatic_layer_name"] == "grammar_note"
            assert job_input["semantic_policy_mode"] == "enforce"
            assert job["operation_fingerprint"] == (
                f"{GRAMMAR_WINDOW_OPERATION_FINGERPRINT}:"
                f"{job_input['strategy_hash']}:"
                "sem:legacy:legacy_open:mode:enforce"
            )
            assert job["status"] == "queued"
            assert job_input["plan_id"] == str(result.plan_id)


async def test_bootstrap_idempotent_skips_existing_active_plan(
    test_db_pool_with_record_and_base: tuple[asyncpg.Pool, UUID, UUID],
) -> None:
    """Same record/base with existing active plan does not re-create."""
    pool, record_id, base_id = test_db_pool_with_record_and_base
    service = GrammarWindowBootstrapService(pool=pool)
    result1 = await service.bootstrap_grammar_window_plan(
        record_id=record_id, base_id=base_id
    )
    result2 = await service.bootstrap_grammar_window_plan(
        record_id=record_id, base_id=base_id
    )
    assert result1.plan_id == result2.plan_id  # reuse existing plan
    assert len(result2.windows) == len(result1.windows)
    assert set(result2.job_ids) == set(result1.job_ids)


async def test_bootstrap_budget_total_uses_section_7_3_formula(
    test_db_pool_with_record_and_base: tuple[asyncpg.Pool, UUID, UUID],
) -> None:
    """§7.3 budget caps: grammar_note = min(ceil(chars/1000)*2, 18),
    sentence = min(max(round(chars/2000), 1), 5).
    """
    pool, record_id, base_id = test_db_pool_with_record_and_base
    service = GrammarWindowBootstrapService(pool=pool)
    result = await service.bootstrap_grammar_window_plan(
        record_id=record_id, base_id=base_id
    )
    async with pool.acquire() as conn:
        plan = await conn.fetchrow(
            "SELECT budget_total FROM layer_analysis_plans WHERE id = $1",
            result.plan_id,
        )
        budget = (
            plan["budget_total"]
            if isinstance(plan["budget_total"], dict)
            else json.loads(plan["budget_total"])
        )
        assert "grammar_note" in budget
        assert "sentence_analysis" in budget
        assert budget["grammar_note"]["count"] <= 18
        assert budget["sentence_analysis"]["count"] <= 5
        assert budget["sentence_analysis"]["count"] >= 1


async def test_bootstrap_result_type_is_grammar_window_bootstrap_result(
    test_db_pool_with_record_and_base: tuple[asyncpg.Pool, UUID, UUID],
) -> None:
    """Bootstrap returns GrammarWindowBootstrapResult with correct field types."""
    pool, record_id, base_id = test_db_pool_with_record_and_base
    service = GrammarWindowBootstrapService(pool=pool)
    result = await service.bootstrap_grammar_window_plan(
        record_id=record_id, base_id=base_id
    )
    assert isinstance(result, GrammarWindowBootstrapResult)
    assert isinstance(result.plan_id, UUID)
    assert isinstance(result.windows, tuple)
    assert isinstance(result.job_ids, tuple)
    for job_id in result.job_ids:
        assert isinstance(job_id, UUID)


async def test_bootstrap_window_input_json_contains_window_contract_fields(
    test_db_pool_with_record_and_base: tuple[asyncpg.Pool, UUID, UUID],
) -> None:
    """§3.2 input_json must contain plan_id, window_id, window_index,
    target_unit_ids, target_anchor_ids.
    """
    pool, record_id, base_id = test_db_pool_with_record_and_base
    service = GrammarWindowBootstrapService(pool=pool)
    result = await service.bootstrap_grammar_window_plan(
        record_id=record_id, base_id=base_id
    )
    async with pool.acquire() as conn:
        for job_id in result.job_ids:
            job = await conn.fetchrow(
                "SELECT input_json, target_key FROM reader_jobs WHERE id = $1",
                job_id,
            )
            input_json = job["input_json"]
            assert "plan_id" in input_json
            assert "window_id" in input_json
            assert "window_index" in input_json
            assert "target_unit_ids" in input_json
            assert "target_anchor_ids" in input_json
            # target_key = window_id (UUID string)
            assert job["target_key"] == input_json["window_id"]


async def test_bootstrap_propagates_trace_id_into_window_run_envelope(
    test_db_pool_with_record_and_base: tuple[asyncpg.Pool, UUID, UUID],
) -> None:
    """Requirement 5: ``bootstrap_grammar_window_plan(trace_id=...)`` writes
    the same ``trace_id`` into every window ``reader_runs.envelope_json`` so
    downstream workers can propagate it into ``reader_runtime_spans``.

    When called without ``trace_id``, the service generates a fresh UUID so
    window runs always carry a trace_id (no NULL envelope trace_id for grammar-window
    runs).
    """
    pool, record_id, base_id = test_db_pool_with_record_and_base
    service = GrammarWindowBootstrapService(pool=pool)
    shared_trace_id = uuid4()
    result = await service.bootstrap_grammar_window_plan(
        record_id=record_id, base_id=base_id, trace_id=shared_trace_id
    )

    async with pool.acquire() as conn:
        for job_id in result.job_ids:
            row = await conn.fetchrow(
                """
                SELECT run.envelope_json
                FROM reader_jobs job
                JOIN reader_runs run ON run.id = job.run_id
                WHERE job.id = $1
                """,
                job_id,
            )
            assert row is not None, f"reader_run for job {job_id} not found"
            envelope = row["envelope_json"]
            if isinstance(envelope, str):
                envelope = json.loads(envelope)
            assert envelope is not None, "envelope_json must not be NULL"
            assert str(envelope.get("trace_id")) == str(shared_trace_id), (
                f"window run envelope trace_id must equal shared trace_id; "
                f"got {envelope.get('trace_id')!r}"
            )


async def test_bootstrap_without_trace_id_still_writes_envelope_trace_id(
    test_db_pool_with_record_and_base: tuple[asyncpg.Pool, UUID, UUID],
) -> None:
    """Requirement 5: when ``trace_id`` is ``None``, the service generates a
    fresh UUID so window runs always carry a trace_id (defensive).
    """
    pool, record_id, base_id = test_db_pool_with_record_and_base
    service = GrammarWindowBootstrapService(pool=pool)
    result = await service.bootstrap_grammar_window_plan(
        record_id=record_id, base_id=base_id, trace_id=None
    )

    async with pool.acquire() as conn:
        for job_id in result.job_ids:
            row = await conn.fetchrow(
                """
                SELECT run.envelope_json
                FROM reader_jobs job
                JOIN reader_runs run ON run.id = job.run_id
                WHERE job.id = $1
                """,
                job_id,
            )
            assert row is not None
            envelope = row["envelope_json"]
            if isinstance(envelope, str):
                envelope = json.loads(envelope)
            assert envelope is not None, "envelope_json must not be NULL"
            trace_id_str = envelope.get("trace_id")
            assert trace_id_str, (
                f"window run envelope must carry a generated trace_id when "
                f"caller did not pass one; got {trace_id_str!r}"
            )
            # Must be a valid UUID
            UUID(str(trace_id_str))
