"""T3.5 completion state finalizer focused tests.

Covers:
- ``completed_clean`` transition when all enhancement jobs succeeded
- ``completed_with_no_op`` when some analysis windows are ``no_op``
- ``completed_with_failures`` when some analysis windows are ``failed``
- ``max_ticks_reached`` / ``max_jobs_reached`` are finalizable (the
  pipeline runner checks caps AFTER incrementing the processed count, so
  the last succeeding job can land exactly on the budget; durable state
  is the source of truth)
- ``max_jobs_reached`` / ``max_ticks_reached`` coinciding with all-terminal
  durable state finalize to ``coverage_complete`` (P1 regression; the two
  caps are symmetric because both are checked after the processed-count
  increment)
- No finalization when non-terminal jobs remain (``retry_later``)
- Force-fail + ``completed_with_failures`` when all enhancement jobs are
  terminal but analysis windows are stuck ``pending`` / ``running`` (the
  candidate scan would otherwise wedge the record forever)
- No finalization when no enhancement jobs have been bootstrapped
- Worker-loop integration: real chain finalizes to ``coverage_complete``
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.contracts.annotation import compute_text_range_hash, utf16_code_unit_length
from app.database import connection as db_connection
from app.schemas.reader_orchestration import (
    GrammarBundleOutput,
    GrammarNoteItem,
    ReaderTextRangeAnchor,
    SentenceAnalysisChunk,
    SentenceAnalysisItem,
    TranslationBatchGenerationOutput,
    TranslationBatchGroupOutput,
    TranslationBatchUnitOutput,
    TranslationGenerationGroup,
    TranslationLayerGenerationOutput,
    VocabularyHighlightItem,
    VocabularyLayerOutput,
)
from app.services.reader_orchestration.completion_finalizer import (
    COMPLETION_TARGET_READINESS_STATE,
    CompletionFinalizer,
    RECORD_COMPLETION_FINALIZED_EVENT_TYPE,
    should_attempt_finalization,
)
from app.services.reader_orchestration.display_title_worker import (
    DisplayTitleExecutionResult,
    DisplayTitleJobContext,
    DisplayTitleWorkerService,
)
from app.services.reader_orchestration.event_runtime import ReaderEventRuntime
from app.services.reader_orchestration.grammar_worker import (
    GrammarBundleWorkerService,
    GrammarExecutionResult,
    GrammarJobContext,
)
from app.services.reader_orchestration.orchestrator import ReaderOrchestrator
from app.services.reader_orchestration.pipeline_runner import (
    EnhancementBootstrapJobCounts,
    EnhancementBootstrapSummary,
    EnhancementOutcomeCounts,
    EnhancementWorkerTickCounts,
    ReaderEnhancementPipelineRunner,
    ReaderPipelineRunSummary,
)
from app.services.reader_orchestration.repository import ReaderOrchestrationRepository
from app.services.reader_orchestration.translation_worker import (
    TranslationBatchExecutionResult,
    TranslationBatchJobContext,
    TranslationExecutionResult,
    TranslationJobContext,
    TranslationWorkerService,
    build_deterministic_translation_groups,
)
from app.services.reader_orchestration.vocabulary_worker import (
    VocabularyBatchCandidateOutput,
    VocabularyBatchExecutionResult,
    VocabularyBatchJobContext,
    VocabularyBatchUnitCandidateOutput,
    VocabularyExecutionResult,
    VocabularyHighlightCandidateItem,
    VocabularyJobContext,
    VocabularyWorkerService,
)
from app.services.reader_orchestration.worker_loop import (
    ENHANCEMENT_PIPELINE_JOB_TYPES,
    ReaderEnhancementWorkerLoopService,
)
from tests.reader_orchestration_test_support import (
    BASELINE_SQL,
    CompatTranslationLayerPublisher,
    connect_admin,
    insert_user,
    make_pool,
    submit_article_ready,
)

pytestmark = pytest.mark.anyio



@pytest.fixture
async def finalizer_env() -> asyncpg.Pool:
    schema_name = f"test_completion_finalizer_{uuid4().hex}"
    admin = await connect_admin()
    original_pool = db_connection.DB_POOL
    await admin.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
    await admin.execute(f'CREATE SCHEMA "{schema_name}"')
    await admin.execute(f'SET search_path TO "{schema_name}", public')
    await admin.execute(BASELINE_SQL)
    await admin.close()
    pool = await make_pool(schema_name)
    db_connection.DB_POOL = pool
    try:
        yield pool
    finally:
        db_connection.DB_POOL = original_pool
        await pool.close()
        cleanup = await connect_admin()
        try:
            await cleanup.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        finally:
            await cleanup.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_summary(
    *,
    record_id: UUID,
    base_id: UUID,
    stopped_reason: str = "all_workers_no_job",
    stopped_outcome: str | None = None,
    attention_code: str | None = None,
) -> ReaderPipelineRunSummary:
    return ReaderPipelineRunSummary(
        record_id=record_id,
        base_id=base_id,
        expected_generation=1,
        bootstrap=EnhancementBootstrapSummary(
            record_id=record_id,
            base_id=base_id,
            expected_generation=1,
            last_event_sequence=1,
            job_counts=EnhancementBootstrapJobCounts(),
        ),
        bootstrapped_job_counts=EnhancementBootstrapJobCounts(),
        worker_tick_counts=EnhancementWorkerTickCounts(),
        outcome_counts=EnhancementOutcomeCounts(no_job=3),
        total_ticks=3,
        total_jobs=0,
        last_event_sequence=1,
        snapshot_reload_recommended=False,
        stopped_reason=stopped_reason,  # type: ignore[arg-type]
        stopped_outcome=stopped_outcome,  # type: ignore[arg-type]
        attention_code=attention_code,
    )


async def _bootstrap_enhancement_jobs(
    pool: asyncpg.Pool,
    *,
    record_id: UUID,
    user_id: UUID,
) -> None:
    """Use the real pipeline runner's bootstrap to create enhancement jobs.

    This mirrors what the worker loop does on the first tick. We don't
    run the workers — we only need the job rows to exist so the finalizer
    has something to count.
    """
    runner = ReaderEnhancementPipelineRunner(pool=pool, enable_zplus_grammar=False)
    await runner.bootstrap_missing_jobs(
        record_id=record_id,
        user_id=user_id,
    )


async def _mark_all_enhancement_jobs_succeeded(
    pool: asyncpg.Pool,
    *,
    record_id: UUID,
    base_id: UUID,
    expected_generation: int = 1,
) -> int:
    """Mark all enhancement jobs for the record as ``succeeded``.

    Returns the number of rows updated. This bypasses the real worker
    chain — we only need the terminal status for finalizer decision
    testing.
    """
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE reader_jobs
            SET status = 'succeeded',
                updated_at = NOW()
            WHERE reading_record_id = $1
              AND base_id = $2
              AND expected_generation = $3
              AND job_type = ANY($4::text[])
            """,
            record_id,
            base_id,
            expected_generation,
            list(ENHANCEMENT_PIPELINE_JOB_TYPES),
        )
        return int(result.split()[-1]) if result.startswith("UPDATE") else 0


async def _mark_some_jobs_retry_later(
    pool: asyncpg.Pool,
    *,
    record_id: UUID,
    base_id: UUID,
    expected_generation: int = 1,
    job_type: str = "translate_article",
) -> int:
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE reader_jobs
            SET status = 'retry_later',
                available_at = NOW() + INTERVAL '1 hour',
                updated_at = NOW()
            WHERE reading_record_id = $1
              AND base_id = $2
              AND expected_generation = $3
              AND job_type = $4
            """,
            record_id,
            base_id,
            expected_generation,
            job_type,
        )
        return int(result.split()[-1]) if result.startswith("UPDATE") else 0


async def _insert_grammar_analysis_plan(
    pool: asyncpg.Pool,
    *,
    record_id: UUID,
    base_id: UUID,
    expected_generation: int = 1,
) -> UUID:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            INSERT INTO layer_analysis_plans (
                reading_record_id, base_id, layer_type,
                policy_version, generation, budget_total, status
            )
            VALUES ($1, $2, 'grammar_bundle', 't35_test_v1', $3,
                    '{"grammar_note":{"max_items":5},"sentence_analysis":{"max_items":5}}'::jsonb,
                    'active')
            RETURNING id
            """,
            record_id,
            base_id,
            expected_generation,
        )


async def _insert_analysis_window(
    pool: asyncpg.Pool,
    *,
    plan_id: UUID,
    window_index: int,
    status: str,
) -> UUID:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            INSERT INTO analysis_windows (
                plan_id, window_index,
                target_anchor_ids,
                context_anchor_prev, context_anchor_next,
                target_unit_ids, target_block_ids,
                char_count, anchor_count,
                window_budget, status
            )
            VALUES (
                $1, $2,
                '[]'::jsonb,
                '[]'::jsonb, '[]'::jsonb,
                '[]'::jsonb, '[]'::jsonb,
                100, 2,
                '{"grammar_note":{"max_items":5}}'::jsonb, $3
            )
            RETURNING id
            """,
            plan_id,
            window_index,
            status,
        )


async def _load_readiness_state(
    pool: asyncpg.Pool,
    record_id: UUID,
) -> str:
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            "SELECT readiness_state FROM reading_records WHERE id = $1",
            record_id,
        )
    if value is None:
        raise AssertionError(f"readiness_state for record {record_id} not found")
    return str(value)


async def _load_completion_events(
    pool: asyncpg.Pool,
    *,
    record_id: UUID,
    user_id: UUID,
    after_sequence: int,
) -> list:
    """Return ``record_state_changed`` events whose payload field is
    ``readiness_state`` (i.e. finalizer-emitted).
    """
    result = await ReaderEventRuntime(pool=pool).poll_events(
        record_id=record_id,
        user_id=user_id,
        after_sequence=after_sequence,
        limit=20,
    )
    return [
        event
        for event in result.events
        if event.event_type == RECORD_COMPLETION_FINALIZED_EVENT_TYPE
        and event.payload_json.get("field") == "readiness_state"
    ]


async def _load_analysis_window_statuses(
    pool: asyncpg.Pool,
    *,
    record_id: UUID,
) -> list[tuple[UUID, str, dict]]:
    """Return ``(window_id, status, coverage)`` rows for the record's
    analysis windows, ordered by window_index.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT aw.id, aw.status, aw.coverage
            FROM analysis_windows aw
            JOIN layer_analysis_plans plan ON plan.id = aw.plan_id
            WHERE plan.reading_record_id = $1
            ORDER BY aw.window_index ASC
            """,
            record_id,
        )
    return [
        (UUID(str(row["id"])), str(row["status"]), dict(row["coverage"] or {}))
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Tests: successful finalization
# ---------------------------------------------------------------------------


async def test_finalizer_transitions_to_coverage_complete_when_all_jobs_succeeded(
    finalizer_env: asyncpg.Pool,
) -> None:
    """All enhancement jobs ``succeeded`` + no analysis windows →
    ``readiness_state`` transitions to ``coverage_complete`` with
    ``completed_clean`` outcome."""
    user_id = await insert_user(finalizer_env)
    article = await submit_article_ready(
        finalizer_env,
        user_id=user_id,
        title="Clean Completion",
    )
    await _bootstrap_enhancement_jobs(
        finalizer_env,
        record_id=article.record_id,
        user_id=user_id,
    )
    updated_count = await _mark_all_enhancement_jobs_succeeded(
        finalizer_env,
        record_id=article.record_id,
        base_id=article.base_id,
    )
    assert updated_count > 0, "bootstrap should have created enhancement jobs"

    summary = _make_summary(
        record_id=article.record_id,
        base_id=article.base_id,
    )
    finalizer = CompletionFinalizer()
    event_runtime = ReaderEventRuntime(pool=finalizer_env)
    repository = ReaderOrchestrationRepository(pool=finalizer_env)

    async with finalizer_env.acquire() as conn:
        async with conn.transaction():
            result = await finalizer.finalize_completion_state(
                conn,
                record_id=article.record_id,
                base_id=article.base_id,
                expected_generation=1,
                summary=summary,
                enhancement_job_types=ENHANCEMENT_PIPELINE_JOB_TYPES,
                event_runtime=event_runtime,
                updated_at=datetime.now(UTC),
            )

    assert result.finalized is True
    assert result.outcome == "completed_clean"
    assert result.readiness_state_updated is True
    assert result.skip_reason is None
    assert await _load_readiness_state(finalizer_env, article.record_id) == (
        COMPLETION_TARGET_READINESS_STATE
    )

    events = await _load_completion_events(
        finalizer_env,
        record_id=article.record_id,
        user_id=user_id,
        after_sequence=article.article_ready_sequence,
    )
    assert len(events) == 1
    event = events[0]
    assert event.payload_json["field"] == "readiness_state"
    assert event.payload_json["previous_value"] == "article_ready"
    assert event.payload_json["next_value"] == "coverage_complete"
    assert event.payload_json["completion_outcome"] == "completed_clean"
    assert event.payload_json["stopped_reason"] == "all_workers_no_job"


async def test_finalizer_transitions_to_coverage_complete_with_partial_no_op_windows(
    finalizer_env: asyncpg.Pool,
) -> None:
    """All jobs ``succeeded`` + one ``completed`` and one ``no_op`` analysis
    window → ``completed_with_no_op`` outcome."""
    user_id = await insert_user(finalizer_env)
    article = await submit_article_ready(
        finalizer_env,
        user_id=user_id,
        title="No-Op Window",
    )
    await _bootstrap_enhancement_jobs(
        finalizer_env,
        record_id=article.record_id,
        user_id=user_id,
    )
    await _mark_all_enhancement_jobs_succeeded(
        finalizer_env,
        record_id=article.record_id,
        base_id=article.base_id,
    )
    plan_id = await _insert_grammar_analysis_plan(
        finalizer_env,
        record_id=article.record_id,
        base_id=article.base_id,
    )
    await _insert_analysis_window(
        finalizer_env,
        plan_id=plan_id,
        window_index=0,
        status="completed",
    )
    await _insert_analysis_window(
        finalizer_env,
        plan_id=plan_id,
        window_index=1,
        status="no_op",
    )

    summary = _make_summary(
        record_id=article.record_id,
        base_id=article.base_id,
    )
    finalizer = CompletionFinalizer()
    event_runtime = ReaderEventRuntime(pool=finalizer_env)

    async with finalizer_env.acquire() as conn:
        async with conn.transaction():
            result = await finalizer.finalize_completion_state(
                conn,
                record_id=article.record_id,
                base_id=article.base_id,
                expected_generation=1,
                summary=summary,
                enhancement_job_types=ENHANCEMENT_PIPELINE_JOB_TYPES,
                event_runtime=event_runtime,
                updated_at=datetime.now(UTC),
            )

    assert result.finalized is True
    assert result.outcome == "completed_with_no_op"
    assert result.window_status_counts is not None
    assert result.window_status_counts["completed"] == 1
    assert result.window_status_counts["no_op"] == 1
    assert await _load_readiness_state(finalizer_env, article.record_id) == (
        COMPLETION_TARGET_READINESS_STATE
    )

    events = await _load_completion_events(
        finalizer_env,
        record_id=article.record_id,
        user_id=user_id,
        after_sequence=article.article_ready_sequence,
    )
    assert len(events) == 1
    assert events[0].payload_json["completion_outcome"] == "completed_with_no_op"


async def test_finalizer_transitions_to_coverage_complete_with_failed_windows(
    finalizer_env: asyncpg.Pool,
) -> None:
    """All jobs ``succeeded`` + one ``failed`` analysis window →
    ``completed_with_failures`` outcome (v1 does NOT block coverage)."""
    user_id = await insert_user(finalizer_env)
    article = await submit_article_ready(
        finalizer_env,
        user_id=user_id,
        title="Failed Window",
    )
    await _bootstrap_enhancement_jobs(
        finalizer_env,
        record_id=article.record_id,
        user_id=user_id,
    )
    await _mark_all_enhancement_jobs_succeeded(
        finalizer_env,
        record_id=article.record_id,
        base_id=article.base_id,
    )
    plan_id = await _insert_grammar_analysis_plan(
        finalizer_env,
        record_id=article.record_id,
        base_id=article.base_id,
    )
    await _insert_analysis_window(
        finalizer_env,
        plan_id=plan_id,
        window_index=0,
        status="completed",
    )
    await _insert_analysis_window(
        finalizer_env,
        plan_id=plan_id,
        window_index=1,
        status="failed",
    )

    summary = _make_summary(
        record_id=article.record_id,
        base_id=article.base_id,
    )
    finalizer = CompletionFinalizer()
    event_runtime = ReaderEventRuntime(pool=finalizer_env)

    async with finalizer_env.acquire() as conn:
        async with conn.transaction():
            result = await finalizer.finalize_completion_state(
                conn,
                record_id=article.record_id,
                base_id=article.base_id,
                expected_generation=1,
                summary=summary,
                enhancement_job_types=ENHANCEMENT_PIPELINE_JOB_TYPES,
                event_runtime=event_runtime,
                updated_at=datetime.now(UTC),
            )

    assert result.finalized is True
    assert result.outcome == "completed_with_failures"
    assert result.window_status_counts is not None
    assert result.window_status_counts["failed"] == 1
    assert await _load_readiness_state(finalizer_env, article.record_id) == (
        COMPLETION_TARGET_READINESS_STATE
    )

    events = await _load_completion_events(
        finalizer_env,
        record_id=article.record_id,
        user_id=user_id,
        after_sequence=article.article_ready_sequence,
    )
    assert len(events) == 1
    assert events[0].payload_json["completion_outcome"] == "completed_with_failures"


# ---------------------------------------------------------------------------
# Tests: budget caps are finalizable (durable state is the source of truth)
# ---------------------------------------------------------------------------


async def test_should_attempt_finalization_returns_true_for_max_ticks(
    finalizer_env: asyncpg.Pool,
) -> None:
    """``max_ticks_reached`` must still attempt finalization. The pipeline
    runner checks the tick cap AFTER incrementing the processed count, so
    the last succeeding job can land exactly on the budget. The
    durable-state guards (not ``stopped_reason``) decide whether the work
    is actually finished."""
    user_id = await insert_user(finalizer_env)
    article = await submit_article_ready(
        finalizer_env,
        user_id=user_id,
        title="Max Ticks",
    )
    summary = _make_summary(
        record_id=article.record_id,
        base_id=article.base_id,
        stopped_reason="max_ticks_reached",
    )
    assert should_attempt_finalization(summary) is True


async def test_should_attempt_finalization_returns_true_for_max_jobs(
    finalizer_env: asyncpg.Pool,
) -> None:
    """``max_jobs_reached`` must still attempt finalization. Same
    rationale as ``max_ticks_reached`` — the cap is checked after the
    processed-count increment, so a just-finished record can report
    ``max_jobs_reached`` even though all work is terminal."""
    user_id = await insert_user(finalizer_env)
    article = await submit_article_ready(
        finalizer_env,
        user_id=user_id,
        title="Max Jobs",
    )
    summary = _make_summary(
        record_id=article.record_id,
        base_id=article.base_id,
        stopped_reason="max_jobs_reached",
    )
    assert should_attempt_finalization(summary) is True


async def test_should_attempt_finalization_returns_false_for_attention_required(
    finalizer_env: asyncpg.Pool,
) -> None:
    """``attention_required`` must not trigger finalization (the
    product_state decision path owns this case)."""
    user_id = await insert_user(finalizer_env)
    article = await submit_article_ready(
        finalizer_env,
        user_id=user_id,
        title="Attention",
    )
    summary = _make_summary(
        record_id=article.record_id,
        base_id=article.base_id,
        stopped_reason="attention_required",
        stopped_outcome="retry_later",
    )
    assert should_attempt_finalization(summary) is False


async def test_finalizer_finalizes_when_max_jobs_coincides_with_all_terminal(
    finalizer_env: asyncpg.Pool,
) -> None:
    """When ``max_jobs_reached`` is reported but every enhancement job is
    already terminal (the last succeeding job landed exactly on the
    budget), the finalizer must transition to ``coverage_complete``.

    This is the core P1 regression: previously the finalizer blanket-
    rejected ``max_jobs_reached`` and the record was wedged because the
    candidate scan never re-picks a record with all-terminal jobs.
    """
    user_id = await insert_user(finalizer_env)
    article = await submit_article_ready(
        finalizer_env,
        user_id=user_id,
        title="Max Jobs At Completion",
    )
    await _bootstrap_enhancement_jobs(
        finalizer_env,
        record_id=article.record_id,
        user_id=user_id,
    )
    await _mark_all_enhancement_jobs_succeeded(
        finalizer_env,
        record_id=article.record_id,
        base_id=article.base_id,
    )

    summary = _make_summary(
        record_id=article.record_id,
        base_id=article.base_id,
        stopped_reason="max_jobs_reached",
    )
    finalizer = CompletionFinalizer()
    event_runtime = ReaderEventRuntime(pool=finalizer_env)

    async with finalizer_env.acquire() as conn:
        async with conn.transaction():
            result = await finalizer.finalize_completion_state(
                conn,
                record_id=article.record_id,
                base_id=article.base_id,
                expected_generation=1,
                summary=summary,
                enhancement_job_types=ENHANCEMENT_PIPELINE_JOB_TYPES,
                event_runtime=event_runtime,
                updated_at=datetime.now(UTC),
            )

    assert result.finalized is True
    assert result.outcome == "completed_clean"
    assert result.force_failed_window_count == 0
    assert await _load_readiness_state(finalizer_env, article.record_id) == (
        COMPLETION_TARGET_READINESS_STATE
    )

    events = await _load_completion_events(
        finalizer_env,
        record_id=article.record_id,
        user_id=user_id,
        after_sequence=article.article_ready_sequence,
    )
    assert len(events) == 1
    assert events[0].payload_json["completion_outcome"] == "completed_clean"
    assert events[0].payload_json["stopped_reason"] == "max_jobs_reached"


async def test_finalizer_finalizes_when_max_ticks_coincides_with_all_terminal(
    finalizer_env: asyncpg.Pool,
) -> None:
    """Symmetric to the ``max_jobs_reached`` case: when ``max_ticks_reached``
    is reported but every enhancement job is already terminal (the last
    succeeding tick landed exactly on the tick budget), the finalizer must
    transition to ``coverage_complete``.

    The pipeline runner checks ``max_ticks`` AFTER incrementing the
    processed-tick count, so a just-finished record can report
    ``max_ticks_reached`` even though all work is terminal. The durable-
    state guards — not ``stopped_reason`` — decide whether the work is
    actually finished.
    """
    user_id = await insert_user(finalizer_env)
    article = await submit_article_ready(
        finalizer_env,
        user_id=user_id,
        title="Max Ticks At Completion",
    )
    await _bootstrap_enhancement_jobs(
        finalizer_env,
        record_id=article.record_id,
        user_id=user_id,
    )
    await _mark_all_enhancement_jobs_succeeded(
        finalizer_env,
        record_id=article.record_id,
        base_id=article.base_id,
    )

    summary = _make_summary(
        record_id=article.record_id,
        base_id=article.base_id,
        stopped_reason="max_ticks_reached",
    )
    finalizer = CompletionFinalizer()
    event_runtime = ReaderEventRuntime(pool=finalizer_env)

    async with finalizer_env.acquire() as conn:
        async with conn.transaction():
            result = await finalizer.finalize_completion_state(
                conn,
                record_id=article.record_id,
                base_id=article.base_id,
                expected_generation=1,
                summary=summary,
                enhancement_job_types=ENHANCEMENT_PIPELINE_JOB_TYPES,
                event_runtime=event_runtime,
                updated_at=datetime.now(UTC),
            )

    assert result.finalized is True
    assert result.outcome == "completed_clean"
    assert result.force_failed_window_count == 0
    assert await _load_readiness_state(finalizer_env, article.record_id) == (
        COMPLETION_TARGET_READINESS_STATE
    )

    events = await _load_completion_events(
        finalizer_env,
        record_id=article.record_id,
        user_id=user_id,
        after_sequence=article.article_ready_sequence,
    )
    assert len(events) == 1
    assert events[0].payload_json["completion_outcome"] == "completed_clean"
    assert events[0].payload_json["stopped_reason"] == "max_ticks_reached"


# ---------------------------------------------------------------------------
# Tests: no false closure when work is still in-flight
# ---------------------------------------------------------------------------


async def test_finalizer_does_not_finalize_when_retry_later_jobs_present(
    finalizer_env: asyncpg.Pool,
) -> None:
    """A ``retry_later`` job means work is still pending — the finalizer
    must skip and leave ``readiness_state`` unchanged."""
    user_id = await insert_user(finalizer_env)
    article = await submit_article_ready(
        finalizer_env,
        user_id=user_id,
        title="Retry Later",
    )
    await _bootstrap_enhancement_jobs(
        finalizer_env,
        record_id=article.record_id,
        user_id=user_id,
    )
    await _mark_some_jobs_retry_later(
        finalizer_env,
        record_id=article.record_id,
        base_id=article.base_id,
        job_type="translate_article",
    )

    summary = _make_summary(
        record_id=article.record_id,
        base_id=article.base_id,
    )
    finalizer = CompletionFinalizer()
    event_runtime = ReaderEventRuntime(pool=finalizer_env)

    async with finalizer_env.acquire() as conn:
        async with conn.transaction():
            result = await finalizer.finalize_completion_state(
                conn,
                record_id=article.record_id,
                base_id=article.base_id,
                expected_generation=1,
                summary=summary,
                enhancement_job_types=ENHANCEMENT_PIPELINE_JOB_TYPES,
                event_runtime=event_runtime,
                updated_at=datetime.now(UTC),
            )

    assert result.finalized is False
    assert result.skip_reason == "non_terminal_jobs_present"
    assert result.job_status_counts is not None
    assert result.job_status_counts["retry_later"] >= 1
    assert await _load_readiness_state(finalizer_env, article.record_id) == (
        "article_ready"
    )

    events = await _load_completion_events(
        finalizer_env,
        record_id=article.record_id,
        user_id=user_id,
        after_sequence=article.article_ready_sequence,
    )
    assert len(events) == 0


async def test_finalizer_force_fails_stuck_windows_and_finalizes_with_failures(
    finalizer_env: asyncpg.Pool,
) -> None:
    """When all enhancement jobs are terminal but analysis windows are
    still ``pending`` / ``running``, the windows are stuck (the pipeline
    already exhausted every worker under the per-record advisory lock).
    The finalizer must NOT skip — that would wedge the record forever
    because the candidate scan only re-picks records with runnable jobs.

    Instead the finalizer force-fails the stuck windows to ``failed``
    (writing failure metadata into ``coverage.diagnostics``) and
    finalizes as ``completed_with_failures``. This mirrors the v1 design
    that grammar-window issues do not block ``coverage_complete``.
    """
    user_id = await insert_user(finalizer_env)
    article = await submit_article_ready(
        finalizer_env,
        user_id=user_id,
        title="Stuck Window",
    )
    await _bootstrap_enhancement_jobs(
        finalizer_env,
        record_id=article.record_id,
        user_id=user_id,
    )
    await _mark_all_enhancement_jobs_succeeded(
        finalizer_env,
        record_id=article.record_id,
        base_id=article.base_id,
    )
    plan_id = await _insert_grammar_analysis_plan(
        finalizer_env,
        record_id=article.record_id,
        base_id=article.base_id,
    )
    # One pending window (worker not registered) + one running window
    # (stuck lease). Both must be force-failed.
    await _insert_analysis_window(
        finalizer_env,
        plan_id=plan_id,
        window_index=0,
        status="pending",
    )
    await _insert_analysis_window(
        finalizer_env,
        plan_id=plan_id,
        window_index=1,
        status="running",
    )

    summary = _make_summary(
        record_id=article.record_id,
        base_id=article.base_id,
    )
    finalizer = CompletionFinalizer()
    event_runtime = ReaderEventRuntime(pool=finalizer_env)

    async with finalizer_env.acquire() as conn:
        async with conn.transaction():
            result = await finalizer.finalize_completion_state(
                conn,
                record_id=article.record_id,
                base_id=article.base_id,
                expected_generation=1,
                summary=summary,
                enhancement_job_types=ENHANCEMENT_PIPELINE_JOB_TYPES,
                event_runtime=event_runtime,
                updated_at=datetime.now(UTC),
            )

    assert result.finalized is True
    assert result.outcome == "completed_with_failures"
    assert result.force_failed_window_count == 2
    assert result.window_status_counts is not None
    assert result.window_status_counts["failed"] == 2
    assert result.window_status_counts["pending"] == 0
    assert result.window_status_counts["running"] == 0
    assert await _load_readiness_state(finalizer_env, article.record_id) == (
        COMPLETION_TARGET_READINESS_STATE
    )

    # Durable state must be truthful: both windows are now ``failed`` with
    # finalizer-attributed diagnostics in coverage.
    window_rows = await _load_analysis_window_statuses(
        finalizer_env,
        record_id=article.record_id,
    )
    assert len(window_rows) == 2
    for _window_id, status, coverage in window_rows:
        assert status == "failed"
        diagnostics = coverage.get("diagnostics", {})
        assert diagnostics.get("failure_code") == "finalizer_forced_window_failure"
        assert diagnostics.get("forced_by") == "completion_finalizer"

    events = await _load_completion_events(
        finalizer_env,
        record_id=article.record_id,
        user_id=user_id,
        after_sequence=article.article_ready_sequence,
    )
    assert len(events) == 1
    assert events[0].payload_json["completion_outcome"] == "completed_with_failures"
    assert events[0].payload_json["force_failed_window_count"] == 2


async def test_finalizer_does_not_finalize_when_no_tracked_jobs(
    finalizer_env: asyncpg.Pool,
) -> None:
    """A record with zero tracked enhancement jobs has not been
    bootstrapped — the finalizer must skip to let the next worker tick
    retry bootstrap."""
    user_id = await insert_user(finalizer_env)
    article = await submit_article_ready(
        finalizer_env,
        user_id=user_id,
        title="No Bootstrap",
    )
    # Deliberately do NOT bootstrap enhancement jobs.

    summary = _make_summary(
        record_id=article.record_id,
        base_id=article.base_id,
    )
    finalizer = CompletionFinalizer()
    event_runtime = ReaderEventRuntime(pool=finalizer_env)

    async with finalizer_env.acquire() as conn:
        async with conn.transaction():
            result = await finalizer.finalize_completion_state(
                conn,
                record_id=article.record_id,
                base_id=article.base_id,
                expected_generation=1,
                summary=summary,
                enhancement_job_types=ENHANCEMENT_PIPELINE_JOB_TYPES,
                event_runtime=event_runtime,
                updated_at=datetime.now(UTC),
            )

    assert result.finalized is False
    assert result.skip_reason == "no_tracked_enhancement_jobs"
    assert await _load_readiness_state(finalizer_env, article.record_id) == (
        "article_ready"
    )


# ---------------------------------------------------------------------------
# Test: worker-loop integration — real chain finalizes to coverage_complete
# ---------------------------------------------------------------------------


WORD_RE = re.compile(r"[A-Za-z]+")


class _StaticTranslator:
    async def translate(
        self,
        context: TranslationJobContext,
    ) -> TranslationExecutionResult:
        return TranslationExecutionResult(
            output=TranslationLayerGenerationOutput(
                groups=[
                    TranslationGenerationGroup(
                        anchor_segment_ids=[
                            anchor_segment.anchor_segment_id
                            for anchor_segment in context.anchor_segments
                        ],
                        translated_text=f"译文：{context.source_text}",
                    )
                ]
            ),
            usage_data={"input_tokens": 1, "output_tokens": 1},
            prompt_version="t35-finalizer-test",
            model_profile="t35_fake_translation",
            model_provider="fake",
            model_name="t35-translation",
        )


class _StaticBatchTranslator:
    """Fake batch translator using ``build_deterministic_translation_groups``
    to echo backend-predefined group_ids, matching the hydrate contract."""

    async def translate_batch(
        self,
        context: TranslationBatchJobContext,
    ) -> TranslationBatchExecutionResult:
        units_output = [
            TranslationBatchUnitOutput(
                unit_id=unit.unit_id,
                groups=[
                    TranslationBatchGroupOutput(
                        group_id=group.group_id,
                        translated_text=f"译文：{group.source_text}",
                    )
                    for group in build_deterministic_translation_groups(unit)
                ],
            )
            for unit in context.units
        ]
        return TranslationBatchExecutionResult(
            output=TranslationBatchGenerationOutput(units=units_output),
            usage_data={"input_tokens": 1, "output_tokens": 1},
            prompt_version="t35-finalizer-test-batch",
            model_profile="t35_fake_translation_batch",
            model_provider="fake",
            model_name="t35-translation-batch",
        )


class _StaticVocabularyExecutor:
    async def generate(
        self,
        context: VocabularyJobContext,
    ) -> VocabularyExecutionResult:
        anchor_segment = context.anchor_segments[0]
        selected_text = anchor_segment.text.split()[0]
        start_offset = anchor_segment.unit_start_utf16
        anchor = ReaderTextRangeAnchor(
            base_id=str(context.base_id),
            unit_id=context.unit_id,
            anchor_segment_id=anchor_segment.anchor_segment_id,
            sentence_id=anchor_segment.sentence_id,
            segment_type=anchor_segment.segment_type,
            start_offset=start_offset,
            end_offset=start_offset + utf16_code_unit_length(selected_text),
            selected_text=selected_text,
            text_hash=compute_text_range_hash(selected_text),
        )
        return VocabularyExecutionResult(
            output=VocabularyLayerOutput(
                items=[
                    VocabularyHighlightItem(
                        anchor=anchor,
                        headword=selected_text.lower(),
                        brief_explanation="t35 vocab",
                        reason="t35_test",
                    )
                ]
            ),
            usage_data={"input_tokens": 1, "output_tokens": 1},
            prompt_version="t35-finalizer-test-vocab",
            model_profile="t35_fake_vocab",
            model_provider="fake",
            model_name="t35-vocab",
        )


class _StaticBatchVocabularyExecutor:
    async def generate_batch(
        self,
        context: VocabularyBatchJobContext,
    ) -> VocabularyBatchExecutionResult:
        units_output: list[VocabularyBatchUnitCandidateOutput] = []
        for unit in context.units:
            if not unit.anchor_segments:
                units_output.append(
                    VocabularyBatchUnitCandidateOutput(unit_id=unit.unit_id, items=[])
                )
                continue
            anchor_segment = unit.anchor_segments[0]
            word_match = WORD_RE.search(anchor_segment.text)
            if word_match is None:
                units_output.append(
                    VocabularyBatchUnitCandidateOutput(unit_id=unit.unit_id, items=[])
                )
                continue
            selected_text = word_match.group(0)
            units_output.append(
                VocabularyBatchUnitCandidateOutput(
                    unit_id=unit.unit_id,
                    items=[
                        VocabularyHighlightCandidateItem(
                            anchor_segment_id=anchor_segment.anchor_segment_id,
                            selected_text=selected_text,
                            headword=selected_text.lower(),
                            brief_explanation="t35 batch vocab",
                            reason="t35_batch_test",
                        )
                    ],
                )
            )
        return VocabularyBatchExecutionResult(
            output=VocabularyBatchCandidateOutput(units=units_output),
            usage_data={"input_tokens": 1, "output_tokens": 1},
            prompt_version="t35-finalizer-test-vocab-batch",
            model_profile="t35_fake_vocab_batch",
            model_provider="fake",
            model_name="t35-vocab-batch",
        )


class _StaticGrammarExecutor:
    async def generate(
        self,
        context: GrammarJobContext,
    ) -> GrammarExecutionResult:
        anchor_segment = context.anchor_segments[0]
        selected_text = anchor_segment.text.split()[0]
        word_anchor = ReaderTextRangeAnchor(
            base_id=str(context.base_id),
            unit_id=context.unit_id,
            anchor_segment_id=anchor_segment.anchor_segment_id,
            sentence_id=anchor_segment.sentence_id,
            segment_type=anchor_segment.segment_type,
            start_offset=anchor_segment.unit_start_utf16,
            end_offset=anchor_segment.unit_start_utf16
            + utf16_code_unit_length(selected_text),
            selected_text=selected_text,
            text_hash=compute_text_range_hash(selected_text),
        )
        sentence_anchor = ReaderTextRangeAnchor(
            base_id=str(context.base_id),
            unit_id=context.unit_id,
            anchor_segment_id=anchor_segment.anchor_segment_id,
            sentence_id=anchor_segment.sentence_id,
            segment_type=anchor_segment.segment_type,
            start_offset=anchor_segment.unit_start_utf16,
            end_offset=anchor_segment.unit_end_utf16,
            selected_text=anchor_segment.text,
            text_hash=compute_text_range_hash(anchor_segment.text),
        )
        return GrammarExecutionResult(
            output=GrammarBundleOutput(
                grammar_notes=[
                    GrammarNoteItem(
                        spans=[word_anchor],
                        grammar_point="t35 grammar point",
                        pattern="SVO",
                        note="t35 test grammar note",
                    )
                ],
                sentence_analyses=[
                    SentenceAnalysisItem(
                        anchor=sentence_anchor,
                        label="main clause",
                        analysis="t35 test sentence analysis",
                        chunks=[
                            SentenceAnalysisChunk(
                                order=1,
                                label="clause",
                                text=anchor_segment.text,
                            )
                        ],
                    )
                ],
            ),
            usage_data={"input_tokens": 1, "output_tokens": 1},
            prompt_version="t35-finalizer-test-grammar",
            model_profile="t35_fake_grammar",
            model_provider="fake",
            model_name="t35-grammar",
        )


class _StaticTitleGenerator:
    async def generate(
        self,
        context: DisplayTitleJobContext,
    ) -> DisplayTitleExecutionResult:
        return DisplayTitleExecutionResult(
            title_zh="T35 测试标题",
            usage_data={"input_tokens": 1, "output_tokens": 1},
            prompt_version="t35-finalizer-test-title",
            model_profile="t35_fake_title",
            model_provider="fake",
            model_name="t35-title",
        )


def _make_real_chain_runner(pool: asyncpg.Pool) -> ReaderEnhancementPipelineRunner:
    translation_worker = TranslationWorkerService(
        pool=pool,
        layer_publisher=CompatTranslationLayerPublisher(pool=pool),
        translator=_StaticTranslator(),
        batch_translator=_StaticBatchTranslator(),
    )
    vocabulary_worker = VocabularyWorkerService(
        pool=pool,
        executor=_StaticVocabularyExecutor(),
        batch_executor=_StaticBatchVocabularyExecutor(),
    )
    grammar_worker = GrammarBundleWorkerService(
        pool=pool,
        executor=_StaticGrammarExecutor(),
    )
    display_title_worker = DisplayTitleWorkerService(
        pool=pool,
        generator=_StaticTitleGenerator(),
    )
    return ReaderEnhancementPipelineRunner(
        pool=pool,
        display_title_worker_service=display_title_worker,
        translation_orchestrator=ReaderOrchestrator(pool=pool, worker_service=translation_worker),
        translation_batch_worker_service=translation_worker,
        vocabulary_worker_service=vocabulary_worker,
        grammar_worker_service=grammar_worker,
        enable_zplus_grammar=False,
    )


async def test_worker_loop_finalizes_to_coverage_complete_on_real_chain(
    finalizer_env: asyncpg.Pool,
) -> None:
    """End-to-end: the worker loop runs the full enhancement chain with
    fake executors, then the finalizer transitions the record to
    ``coverage_complete``. Verifies the worker_loop integration path."""
    user_id = await insert_user(finalizer_env)
    article = await submit_article_ready(
        finalizer_env,
        user_id=user_id,
        title="Worker Loop Finalize",
    )
    runner = _make_real_chain_runner(finalizer_env)
    service = ReaderEnhancementWorkerLoopService(
        pool=finalizer_env,
        pipeline_runner=runner,
    )
    candidate = await _find_candidate(service, article.record_id)

    result = await service.process_candidate(
        candidate=candidate,
        lease_owner_prefix="t35-finalizer-integration",
        max_ticks=24,
        max_jobs=24,
    )

    assert result.outcome == "processed"
    assert result.pipeline_summary is not None
    assert result.pipeline_summary.stopped_reason == "all_workers_no_job"
    assert result.completion_finalization_result is not None
    assert result.completion_finalization_result.finalized is True
    assert result.completion_finalization_result.outcome == "completed_clean"

    assert await _load_readiness_state(finalizer_env, article.record_id) == (
        COMPLETION_TARGET_READINESS_STATE
    )

    # Record must no longer be in the scan candidate pool.
    candidates_after = await service.scan_eligible_records(batch_size=20)
    assert article.record_id not in {c.record_id for c in candidates_after}


async def _find_candidate(
    service: ReaderEnhancementWorkerLoopService,
    record_id: UUID,
    *,
    batch_size: int = 20,
):
    candidates = await service.scan_eligible_records(batch_size=batch_size)
    for candidate in candidates:
        if candidate.record_id == record_id:
            return candidate
    raise AssertionError(f"candidate for record {record_id} not found")
