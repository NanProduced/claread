"""Focused tests for the D4 worker runner hardening.

Covers:
- no job available
- success tick (layer published + parsed_decision written)
- retryable failure (retry_later, no parsed_decision)
- terminal failure (failed_terminal, no parsed_decision)
- active base mismatch (fence rejection at claim, job superseded)
- event sequence (layer_published before parsed_decision_updated)
- drain mode (multiple jobs processed until queue empty)
- orphan diagnostic (no orphan after success tick; orphan detected
  when parsed_decision is missing)
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.database import connection as db_connection
from app.schemas.reader_orchestration import TranslationLayerOutput
from app.services.reader_orchestration.article_ready_service import (
    PlainTextArticleReadySubmitRequest,
)
from app.services.reader_orchestration.event_runtime import ReaderEventRuntime
from app.services.reader_orchestration.orchestrator import (
    OrphanedTranslationDecision,
    ReaderOrchestrator,
)
from app.services.reader_orchestration.translation_worker import (
    TranslationExecutionError,
    TranslationExecutionResult,
    TranslationWorkerService,
)
from app.services.reader_orchestration.worker_runner import (
    TranslationWorkerRunner,
)
from tests.reader_orchestration_test_support import (
    BASELINE_SQL,
    connect_admin,
    insert_user,
    make_pool,
)

pytestmark = pytest.mark.anyio

LEASE_OWNER = "test-runner"
LEASE_DURATION = timedelta(seconds=30)


class _StaticTranslator:
    def __init__(self, output: TranslationLayerOutput) -> None:
        self.output = output
        self.calls: list = []

    async def translate(self, context) -> TranslationExecutionResult:
        self.calls.append(context)
        return TranslationExecutionResult(
            output=self.output,
            usage_data={
                "aggregate": {
                    "input_tokens": 10,
                    "output_tokens": 15,
                    "total_tokens": 25,
                }
            },
            prompt_version="test-runner",
            model_profile="fake-profile",
            model_provider="fake-provider",
            model_name="fake-model",
        )


class _FailingTranslator:
    def __init__(self, error: TranslationExecutionError) -> None:
        self.error = error

    async def translate(self, context) -> TranslationExecutionResult:
        raise self.error


def _translation_output(text: str = "第一句。\n\n第二段。") -> TranslationLayerOutput:
    return TranslationLayerOutput(
        target_language="zh-CN",
        translated_text=text,
        notes=[],
        confidence="normal",
    )


def _retryable_error() -> TranslationExecutionError:
    return TranslationExecutionError(
        "temporary provider timeout",
        retryable=True,
        failure_class="provider",
        failure_code="provider_timeout",
    )


def _terminal_error() -> TranslationExecutionError:
    return TranslationExecutionError(
        "unsupported language pair",
        retryable=False,
        failure_class="policy",
        failure_code="unsupported_language_pair",
    )


@pytest.fixture
async def runner_env() -> asyncpg.Pool:
    schema_name = f"test_reader_runner_{uuid4().hex}"
    admin_conn = await connect_admin()
    original_pool = db_connection.DB_POOL
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)
        pool = await make_pool(schema_name)
        db_connection.DB_POOL = pool
        try:
            yield pool
        finally:
            await pool.close()
    finally:
        db_connection.DB_POOL = original_pool
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


def _make_runner(
    pool: asyncpg.Pool,
    *,
    translator,
) -> TranslationWorkerRunner:
    worker = TranslationWorkerService(pool=pool, translator=translator)
    orchestrator = ReaderOrchestrator(pool=pool, worker_service=worker)
    return TranslationWorkerRunner(orchestrator)


def _plain_text_request(user_id: UUID, *, title: str = "Runner Slice"):
    return PlainTextArticleReadySubmitRequest(
        user_id=user_id,
        plain_text="First sentence.\n\nSecond paragraph for translation.",
        title=title,
        language="en",
    )


async def _submit_and_bootstrap(
    pool: asyncpg.Pool,
    user_id: UUID,
    *,
    title: str = "Runner Slice",
):
    orchestrator = ReaderOrchestrator(pool=pool)
    return await orchestrator.submit_plain_text_and_bootstrap_translation(
        _plain_text_request(user_id, title=title),
    )


async def _count_parsed_decisions(pool: asyncpg.Pool, record_id: UUID) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM parsed_decisions WHERE reading_record_id = $1",
            record_id,
        )


async def _count_published_translation_layers(
    pool: asyncpg.Pool, record_id: UUID
) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM enhancement_layers
            WHERE reading_record_id = $1
              AND layer_type = 'translation'
              AND status = 'published'
            """,
            record_id,
        )


async def test_single_tick_no_job_returns_no_job_status(
    runner_env: asyncpg.Pool,
) -> None:
    runner = _make_runner(runner_env, translator=_StaticTranslator(_translation_output()))

    outcome = await runner.run_single_tick(
        lease_owner=LEASE_OWNER,
        lease_duration=LEASE_DURATION,
    )

    assert outcome.status == "no_job"
    assert outcome.tick_result is not None
    assert outcome.tick_result.worker_result is None
    assert outcome.tick_result.parsed_decision_written is False
    assert outcome.error_code is None


async def test_single_tick_success_publishes_layer_and_writes_parsed_decision(
    runner_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(runner_env)
    article = await _submit_and_bootstrap(runner_env, user_id)
    runner = _make_runner(runner_env, translator=_StaticTranslator(_translation_output()))

    outcome = await runner.run_single_tick(
        lease_owner=LEASE_OWNER,
        lease_duration=LEASE_DURATION,
    )

    assert outcome.status == "succeeded"
    assert outcome.tick_result is not None
    assert outcome.tick_result.parsed_decision_written is True
    assert outcome.tick_result.worker_result is not None
    assert outcome.tick_result.worker_result.published_layer is not None

    layer_count = await _count_published_translation_layers(runner_env, article.record_id)
    assert layer_count == 1

    decision_count = await _count_parsed_decisions(runner_env, article.record_id)
    assert decision_count == 1


async def test_single_tick_retryable_failure_returns_retry_later_without_parsed_decision(
    runner_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(runner_env)
    article = await _submit_and_bootstrap(runner_env, user_id)
    runner = _make_runner(runner_env, translator=_FailingTranslator(_retryable_error()))

    outcome = await runner.run_single_tick(
        lease_owner=LEASE_OWNER,
        lease_duration=LEASE_DURATION,
    )

    assert outcome.status == "retry_later"
    assert outcome.tick_result is not None
    assert outcome.tick_result.parsed_decision_written is False
    assert outcome.error_code == "translation_retryable_failure"

    layer_count = await _count_published_translation_layers(runner_env, article.record_id)
    assert layer_count == 0

    decision_count = await _count_parsed_decisions(runner_env, article.record_id)
    assert decision_count == 0

    async with runner_env.acquire() as conn:
        job_status = await conn.fetchval(
            """
            SELECT status FROM reader_jobs
            WHERE reading_record_id = $1 AND job_type = 'translate_unit'
            """,
            article.record_id,
        )
    assert job_status == "retry_later"


async def test_single_tick_terminal_failure_returns_failed_terminal_without_parsed_decision(
    runner_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(runner_env)
    article = await _submit_and_bootstrap(runner_env, user_id)
    runner = _make_runner(runner_env, translator=_FailingTranslator(_terminal_error()))

    outcome = await runner.run_single_tick(
        lease_owner=LEASE_OWNER,
        lease_duration=LEASE_DURATION,
    )

    assert outcome.status == "failed_terminal"
    assert outcome.tick_result is not None
    assert outcome.tick_result.parsed_decision_written is False
    assert outcome.error_code == "translation_terminal_failure"

    layer_count = await _count_published_translation_layers(runner_env, article.record_id)
    assert layer_count == 0

    decision_count = await _count_parsed_decisions(runner_env, article.record_id)
    assert decision_count == 0

    async with runner_env.acquire() as conn:
        job_status = await conn.fetchval(
            """
            SELECT status FROM reader_jobs
            WHERE reading_record_id = $1 AND job_type = 'translate_unit'
            """,
            article.record_id,
        )
    assert job_status == "failed_terminal"


async def test_single_tick_active_base_mismatch_supersedes_job_without_layer(
    runner_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(runner_env)
    article = await _submit_and_bootstrap(runner_env, user_id)
    runner = _make_runner(runner_env, translator=_StaticTranslator(_translation_output()))

    async with runner_env.acquire() as conn:
        await conn.execute(
            "UPDATE reading_records SET active_base_id = NULL WHERE id = $1",
            article.record_id,
        )

    outcome = await runner.run_single_tick(
        lease_owner=LEASE_OWNER,
        lease_duration=LEASE_DURATION,
    )

    assert outcome.status == "no_job"
    assert outcome.tick_result is not None
    assert outcome.tick_result.worker_result is None
    assert outcome.tick_result.parsed_decision_written is False

    async with runner_env.acquire() as conn:
        job_status = await conn.fetchval(
            """
            SELECT status FROM reader_jobs
            WHERE reading_record_id = $1 AND job_type = 'translate_unit'
            """,
            article.record_id,
        )
    assert job_status == "superseded"

    layer_count = await _count_published_translation_layers(runner_env, article.record_id)
    assert layer_count == 0

    decision_count = await _count_parsed_decisions(runner_env, article.record_id)
    assert decision_count == 0


async def test_event_sequence_layer_published_before_parsed_decision_updated(
    runner_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(runner_env)
    article = await _submit_and_bootstrap(runner_env, user_id)
    runner = _make_runner(runner_env, translator=_StaticTranslator(_translation_output()))

    await runner.run_single_tick(
        lease_owner=LEASE_OWNER,
        lease_duration=LEASE_DURATION,
    )

    runtime = ReaderEventRuntime(pool=runner_env)
    poll_result = await runtime.poll_events(
        record_id=article.record_id,
        user_id=user_id,
        after_sequence=0,
        limit=50,
    )

    event_types = [event.event_type for event in poll_result.events]
    assert "article_ready" in event_types
    assert "layer_published" in event_types
    assert "parsed_decision_updated" in event_types

    article_ready_seq = next(
        event.sequence for event in poll_result.events if event.event_type == "article_ready"
    )
    layer_published_seq = next(
        event.sequence for event in poll_result.events if event.event_type == "layer_published"
    )
    parsed_decision_seq = next(
        event.sequence
        for event in poll_result.events
        if event.event_type == "parsed_decision_updated"
    )

    assert article_ready_seq < layer_published_seq < parsed_decision_seq


async def test_drain_processes_multiple_jobs_until_queue_empty(
    runner_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(runner_env)
    article_a = await _submit_and_bootstrap(
        runner_env, user_id, title="Drain Article A"
    )
    article_b = await _submit_and_bootstrap(
        runner_env, user_id, title="Drain Article B"
    )

    runner = _make_runner(runner_env, translator=_StaticTranslator(_translation_output()))

    drain_result = await runner.run_drain(
        lease_owner=LEASE_OWNER,
        lease_duration=LEASE_DURATION,
        max_ticks=5,
    )

    assert drain_result.stopped_reason == "no_job"
    assert drain_result.total_processed == 2
    assert drain_result.total_succeeded == 2
    assert drain_result.total_retry_later == 0
    assert drain_result.total_failed_terminal == 0
    assert drain_result.total_fence_rejected == 0

    layers_a = await _count_published_translation_layers(runner_env, article_a.record_id)
    layers_b = await _count_published_translation_layers(runner_env, article_b.record_id)
    assert layers_a == 1
    assert layers_b == 1

    decisions_a = await _count_parsed_decisions(runner_env, article_a.record_id)
    decisions_b = await _count_parsed_decisions(runner_env, article_b.record_id)
    assert decisions_a == 1
    assert decisions_b == 1


async def test_drain_respects_max_ticks_limit(
    runner_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(runner_env)
    await _submit_and_bootstrap(runner_env, user_id, title="MaxTicks Article")

    runner = _make_runner(runner_env, translator=_StaticTranslator(_translation_output()))

    drain_result = await runner.run_drain(
        lease_owner=LEASE_OWNER,
        lease_duration=LEASE_DURATION,
        max_ticks=1,
    )

    assert drain_result.stopped_reason == "max_ticks_reached"
    assert drain_result.total_processed == 1
    assert drain_result.total_succeeded == 1


async def test_drain_rejects_invalid_max_ticks(
    runner_env: asyncpg.Pool,
) -> None:
    runner = _make_runner(runner_env, translator=_StaticTranslator(_translation_output()))

    with pytest.raises(ValueError, match="max_ticks must be >= 1"):
        await runner.run_drain(
            lease_owner=LEASE_OWNER,
            lease_duration=LEASE_DURATION,
            max_ticks=0,
        )


async def test_orphan_diagnostic_returns_empty_after_successful_tick(
    runner_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(runner_env)
    article = await _submit_and_bootstrap(runner_env, user_id)

    orchestrator = ReaderOrchestrator(pool=runner_env)
    runner = _make_runner(runner_env, translator=_StaticTranslator(_translation_output()))
    await runner.run_single_tick(
        lease_owner=LEASE_OWNER,
        lease_duration=LEASE_DURATION,
    )

    orphans = await orchestrator.diagnose_orphaned_translation_decisions(
        reading_record_id=article.record_id,
    )
    assert orphans == []


async def test_orphan_diagnostic_detects_layer_without_parsed_decision(
    runner_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(runner_env)
    article = await _submit_and_bootstrap(runner_env, user_id)

    runner = _make_runner(runner_env, translator=_StaticTranslator(_translation_output()))
    await runner.run_single_tick(
        lease_owner=LEASE_OWNER,
        lease_duration=LEASE_DURATION,
    )

    async with runner_env.acquire() as conn:
        await conn.execute(
            """
            DELETE FROM parsed_decisions
            WHERE reading_record_id = $1
            """,
            article.record_id,
        )

    orchestrator = ReaderOrchestrator(pool=runner_env)
    orphans = await orchestrator.diagnose_orphaned_translation_decisions(
        reading_record_id=article.record_id,
    )

    assert len(orphans) == 1
    orphan = orphans[0]
    assert isinstance(orphan, OrphanedTranslationDecision)
    assert orphan.reading_record_id == article.record_id
    assert orphan.base_id == article.base_id
    assert orphan.generation == 1
    assert orphan.source_job_id is not None


async def test_orphan_diagnostic_scoped_to_record(
    runner_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(runner_env)
    article_a = await _submit_and_bootstrap(runner_env, user_id, title="Scope A")
    article_b = await _submit_and_bootstrap(runner_env, user_id, title="Scope B")

    runner = _make_runner(runner_env, translator=_StaticTranslator(_translation_output()))
    await runner.run_drain(
        lease_owner=LEASE_OWNER,
        lease_duration=LEASE_DURATION,
        max_ticks=5,
    )

    async with runner_env.acquire() as conn:
        await conn.execute(
            "DELETE FROM parsed_decisions WHERE reading_record_id = $1",
            article_a.record_id,
        )

    orchestrator = ReaderOrchestrator(pool=runner_env)

    orphans_a = await orchestrator.diagnose_orphaned_translation_decisions(
        reading_record_id=article_a.record_id,
    )
    assert len(orphans_a) == 1

    orphans_b = await orchestrator.diagnose_orphaned_translation_decisions(
        reading_record_id=article_b.record_id,
    )
    assert orphans_b == []

    all_orphans = await orchestrator.diagnose_orphaned_translation_decisions()
    assert len(all_orphans) == 1
    assert all_orphans[0].reading_record_id == article_a.record_id


async def test_single_tick_idempotent_replay_does_not_duplicate_layer_or_decision(
    runner_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(runner_env)
    article = await _submit_and_bootstrap(runner_env, user_id)
    runner = _make_runner(runner_env, translator=_StaticTranslator(_translation_output()))

    first = await runner.run_single_tick(
        lease_owner=LEASE_OWNER,
        lease_duration=LEASE_DURATION,
    )
    assert first.status == "succeeded"

    second = await runner.run_single_tick(
        lease_owner=LEASE_OWNER,
        lease_duration=LEASE_DURATION,
    )
    assert second.status == "no_job"

    layer_count = await _count_published_translation_layers(runner_env, article.record_id)
    assert layer_count == 1

    decision_count = await _count_parsed_decisions(runner_env, article.record_id)
    assert decision_count == 1
