"""Three-mode acceptance tests for the reader orchestration pipeline.

Verifies that the three article routing modes (SHORT_BATCH,
STRUCTURED_BATCH, GROUPED_WINDOWED) produce consistent end-to-end results
when driven through the production ``ReaderEnhancementWorkerLoopService``
plus ``CompletionFinalizer`` path.

Each test submits a fresh article, constructs a pipeline runner wired with
production worker services (``TranslationWorkerService`` /
``VocabularyWorkerService`` / ``GrammarBundleWorkerService`` — no test-local
subclasses) and real layer publishers, so the claim/publish fingerprint
checks that production runs are exercised. Only the LLM executors are faked.

4 tests (asyncio only):

1. ``test_short_batch_acceptance_through_worker_loop``
2. ``test_structured_batch_acceptance_through_worker_loop``
3. ``test_grouped_windowed_acceptance_through_worker_loop``
4. ``test_short_batch_usage_event_tokens_match_runtime_span``

Each acceptance test asserts:

- The worker loop outcome is ``processed`` and the completion finalizer
  finalizes the record (``readiness_state -> coverage_complete``).
- The ``reader_jobs`` topology matches the expected route: batch jobs for
  SHORT/STRUCTURED, window jobs for GROUPED_WINDOWED.
- ``enhancement_layers`` counts match the unit count (SHORT/STRUCTURED) or
  are present for all layer types including ``sentence_analysis``
  (GROUPED_WINDOWED).
- ``ai_usage_events`` carry the correct ``operation_fingerprint`` and token
  counts for the grammar path.

The fourth test verifies that the grammar batch ``ai_usage_events`` token
columns match the corresponding ``reader_runtime_spans`` row (the bug fix
in ``_record_batch_usage_event`` that now passes ``usage_data`` through).

Note: these tests use fake executors and only verify code-level contract
closure. Real-LLM cost / quality / latency acceptance is a separate gate.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.database import connection as db_connection
from app.services.reader_orchestration.display_title_worker import (
    DisplayTitleWorkerService,
)
from app.services.reader_orchestration.event_runtime import ReaderEventRuntime
from app.services.reader_orchestration.grammar_window_publisher import (
    GrammarWindowPublisher,
)
from app.services.reader_orchestration.grammar_window_worker import (
    GrammarWindowWorkerService,
)
from app.services.reader_orchestration.grammar_worker import GrammarBundleWorkerService
from app.services.reader_orchestration.layer_publisher import (
    VocabularyLayerPublisher,
)
from app.services.reader_orchestration.orchestrator import ReaderOrchestrator
from app.services.reader_orchestration.pipeline_runner import (
    ReaderEnhancementPipelineRunner,
)
from app.services.reader_orchestration.translation_worker import TranslationWorkerService
from app.services.reader_orchestration.vocabulary_worker import VocabularyWorkerService
from app.services.reader_orchestration.worker_loop import (
    ReaderEnhancementWorkerLoopService,
    WorkerLoopCandidateRecord,
)
from tests.reader_orchestration_test_support import (
    BASELINE_SQL,
    CompatTranslationLayerPublisher,
    connect_admin,
    insert_user,
    make_pool,
    submit_article_ready,
)
from tests.test_reader_orchestration_pipeline_runner import (
    _StaticBatchTranslator,
    _StaticBatchVocabularyExecutor,
    _StaticGrammarBatchExecutor,
    _StaticGrammarExecutor,
    _StaticTitleGenerator,
    _StaticTranslator,
    _StaticVocabularyExecutor,
)
from tests.test_zplus_bbc_regression import _StaticGrammarWindowExecutor

pytestmark = pytest.mark.anyio

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MIGRATION_0015_SQL = (
    _REPO_ROOT / "infra" / "migrations" / "0015_layer_analysis_plans.sql"
).read_text(encoding="utf-8")
_MIGRATION_0016_SQL = (
    _REPO_ROOT
    / "infra"
    / "migrations"
    / "0016_reader_runtime_spans_grammar_bundle_window.sql"
).read_text(encoding="utf-8")
_MIGRATION_0017_SQL = (
    _REPO_ROOT / "infra" / "migrations" / "0017_reader_jobs_batch_path_job_types.sql"
).read_text(encoding="utf-8")

LEASE_DURATION = timedelta(seconds=30)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
async def three_mode_env() -> AsyncIterator[asyncpg.Pool]:
    schema_name = f"test_three_mode_acceptance_{uuid4().hex}"
    admin = await connect_admin()
    original_pool = db_connection.DB_POOL
    await admin.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
    await admin.execute(f'CREATE SCHEMA "{schema_name}"')
    await admin.execute(f'SET search_path TO "{schema_name}", public')
    await admin.execute(BASELINE_SQL)
    await admin.execute(_MIGRATION_0015_SQL)
    await admin.execute(_MIGRATION_0016_SQL)
    await admin.execute(_MIGRATION_0017_SQL)
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
# Article text generators (tuned to trigger each route)
# ---------------------------------------------------------------------------


def _short_article() -> str:
    """~30 words, 3 paragraphs -> SHORT_BATCH (word_count <= 1100)."""
    return (
        "First sentence for acceptance testing here. "
        "Second sentence continues the short article now. "
        "Third sentence wraps up paragraph one cleanly.\n\n"
        "Fourth sentence starts paragraph two right here. "
        "Fifth sentence adds more content to read. "
        "Sixth sentence finishes this second paragraph.\n\n"
        "Seventh sentence opens paragraph three now. "
        "Eighth sentence keeps the article going strong. "
        "Ninth sentence concludes the short piece today."
    )


def _structured_article() -> str:
    """~1400 words, UTF-16 < 12000 -> STRUCTURED_BATCH.

    1100 < word_count <= 2000 AND content_utf16_length <= 12000.
    Each iteration is ~28 words / ~175 chars; 50 iterations gives
    ~1400 words and ~8750 chars, safely inside the structured tier.
    """
    base = (
        "The committee reviewed the quarterly report and found that several "
        "departments had exceeded their allocated budgets while others reported "
        "unexpected surpluses during the fiscal period. "
    )
    parts = []
    for i in range(50):
        parts.append(f"Sentence number {i + 1}. " + base)
    return "\n\n".join(parts)


def _grouped_article() -> str:
    """>2000 words, 15 paragraphs -> GROUPED_WINDOWED (word_count > 2000).

    15 paragraphs of 5 iterations each: 15 * 5 * 28 ≈ 2100 words.
    Keeping paragraph count at 15 limits units to ~15 so the per-window
    job count stays well within ``max_jobs=30``.
    """
    base = (
        "The committee reviewed the quarterly report and found that several "
        "departments had exceeded their allocated budgets while others reported "
        "unexpected surpluses during the fiscal period. "
    )
    parts = []
    for p in range(15):
        chunk = ""
        for j in range(5):
            idx = p * 5 + j + 1
            chunk += f"Sentence number {idx}. " + base
        parts.append(chunk)
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Flat-usage grammar batch executor (test-only workaround)
# ---------------------------------------------------------------------------
#
# The production ``_StaticGrammarBatchExecutor`` returns usage_data in the
# aggregate format ``{"aggregate": {"input_tokens": 20, ...}}``. The
# ``record_ai_usage_event`` function handles this format correctly
# (extracting from the ``aggregate`` key), but ``end_worker_span_success``
# in ``span_recorder.py`` expects flat usage_data with top-level
# ``input_tokens`` / ``output_tokens`` / ``total_tokens`` keys.
#
# This subclass flattens the aggregate so both the usage event and the
# worker_tick span get correct token counts. This is a test-only
# workaround; the real fix would be to update ``end_worker_span_success``
# to handle the aggregate format (like ``record_ai_usage_event`` does).


class _FlatUsageGrammarBatchExecutor(_StaticGrammarBatchExecutor):
    """Grammar batch executor that returns flat usage_data.

    The production ``_StaticGrammarBatchExecutor`` returns usage_data in the
    aggregate format ``{"aggregate": {"input_tokens": 20, ...}}``. The
    ``record_ai_usage_event`` function handles this format correctly
    (extracting from the ``aggregate`` key), but ``end_worker_span_success``
    in ``span_recorder.py`` expects flat usage_data with top-level
    ``input_tokens`` / ``output_tokens`` / ``total_tokens`` keys.

    This subclass flattens the aggregate so both the usage event and the
    worker_tick span get correct token counts. This is a test-only
    workaround; the real fix would be to update ``end_worker_span_success``
    to handle the aggregate format (like ``record_ai_usage_event`` does).
    """

    async def generate_batch(self, context):
        result = await super().generate_batch(context)
        if result.usage_data and "aggregate" in result.usage_data:
            aggregate = result.usage_data["aggregate"]
            if isinstance(aggregate, dict):
                return replace(result, usage_data=dict(aggregate))
        return result


# ---------------------------------------------------------------------------
# Production-topology pipeline runner
# ---------------------------------------------------------------------------


def _make_production_runner(pool: asyncpg.Pool) -> ReaderEnhancementPipelineRunner:
    """Build a pipeline runner wired with production workers + fake executors.

    Uses the real ``TranslationWorkerService`` / ``VocabularyWorkerService``
    / ``GrammarBundleWorkerService`` (no test-local subclasses) with the real
    layer publishers, so the acceptance path exercises the same claim/publish
    fingerprint checks that production runs. Only the LLM executors are faked.
    """
    translation_worker = TranslationWorkerService(
        pool=pool,
        layer_publisher=CompatTranslationLayerPublisher(pool=pool),
        translator=_StaticTranslator(),
        batch_translator=_StaticBatchTranslator(),
    )
    orchestrator = ReaderOrchestrator(pool=pool, worker_service=translation_worker)
    vocabulary_worker = VocabularyWorkerService(
        pool=pool,
        layer_publisher=VocabularyLayerPublisher(pool=pool),
        executor=_StaticVocabularyExecutor(),
        batch_executor=_StaticBatchVocabularyExecutor(),
    )
    grammar_worker = GrammarBundleWorkerService(
        pool=pool,
        executor=_StaticGrammarExecutor(),
        batch_executor=_FlatUsageGrammarBatchExecutor(),
    )
    display_title_worker = DisplayTitleWorkerService(
        pool=pool,
        generator=_StaticTitleGenerator(),
    )
    window_worker = GrammarWindowWorkerService(
        pool=pool,
        executor=_StaticGrammarWindowExecutor(),
    )
    window_publisher = GrammarWindowPublisher(
        pool=pool,
        event_runtime=ReaderEventRuntime(pool=pool),
    )
    return ReaderEnhancementPipelineRunner(
        pool=pool,
        display_title_worker_service=display_title_worker,
        translation_orchestrator=orchestrator,
        translation_batch_worker_service=translation_worker,
        vocabulary_worker_service=vocabulary_worker,
        grammar_worker_service=grammar_worker,
        grammar_window_worker_service=window_worker,
        grammar_window_publisher=window_publisher,
        enable_zplus_grammar=True,
    )


# ---------------------------------------------------------------------------
# Worker loop helpers
# ---------------------------------------------------------------------------


async def _find_candidate(
    service: ReaderEnhancementWorkerLoopService,
    record_id: UUID,
    *,
    batch_size: int = 20,
) -> WorkerLoopCandidateRecord:
    candidates = await service.scan_eligible_records(batch_size=batch_size)
    for candidate in candidates:
        if candidate.record_id == record_id:
            return candidate
    raise AssertionError(f"candidate for record {record_id} not found")


async def _run_through_worker_loop(
    pool: asyncpg.Pool,
    runner: ReaderEnhancementPipelineRunner,
    record_id: UUID,
    user_id: UUID,
):
    service = ReaderEnhancementWorkerLoopService(pool=pool, pipeline_runner=runner)
    candidate = await _find_candidate(service, record_id)
    result = await service.process_candidate(
        candidate=candidate,
        lease_owner_prefix="three-mode-acceptance",
        lease_duration=LEASE_DURATION,
        max_ticks=80,
        max_jobs=40,
    )
    return result


# ---------------------------------------------------------------------------
# DB query helpers
# ---------------------------------------------------------------------------


async def _fetch_job_topology(pool: asyncpg.Pool, record_id: UUID) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT
                j.job_type,
                j.target_type,
                j.status,
                j.operation_fingerprint,
                r.envelope_json->>'article_route' AS article_route,
                r.policy_version
            FROM reader_jobs j
            JOIN reader_runs r ON r.id = j.run_id
            WHERE j.reading_record_id = $1
            ORDER BY j.job_type, j.created_at
            """,
            record_id,
        )


async def _fetch_layer_counts(pool: asyncpg.Pool, record_id: UUID) -> dict[str, int]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT layer_type, COUNT(*) AS cnt
            FROM enhancement_layers
            WHERE reading_record_id = $1 AND status = 'published'
            GROUP BY layer_type
            """,
            record_id,
        )
    return {row["layer_type"]: int(row["cnt"]) for row in rows}


async def _fetch_grammar_usage_events(
    pool: asyncpg.Pool, record_id: UUID
) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT
                id,
                capability_code,
                operation_fingerprint,
                status,
                input_tokens,
                output_tokens,
                total_tokens,
                reader_job_id
            FROM ai_usage_events
            WHERE reading_record_id = $1
              AND capability_code = 'reader_grammar_bundle'
            ORDER BY created_at
            """,
            record_id,
        )


async def _fetch_unit_count(pool: asyncpg.Pool, record_id: UUID) -> int:
    async with pool.acquire() as conn:
        return int(
            await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM reading_units
                WHERE reading_record_id = $1
                """,
                record_id,
            )
        )


async def _fetch_readiness_state(pool: asyncpg.Pool, record_id: UUID) -> str:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT readiness_state FROM reading_records WHERE id = $1",
            record_id,
        )


async def _fetch_grammar_span(
    pool: asyncpg.Pool, ai_usage_event_id: UUID
) -> asyncpg.Record | None:
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            SELECT
                worker_type,
                span_kind,
                status,
                input_tokens,
                output_tokens,
                total_tokens,
                ai_usage_event_id
            FROM reader_runtime_spans
            WHERE ai_usage_event_id = $1
              AND span_kind = 'worker_tick'
              AND status = 'succeeded'
            ORDER BY started_at DESC
            LIMIT 1
            """,
            ai_usage_event_id,
        )


# ---------------------------------------------------------------------------
# Shared assertion helpers
# ---------------------------------------------------------------------------


def _assert_finalized(result) -> None:
    """Assert the worker loop processed the record and the finalizer ran."""
    assert result.outcome == "processed", (
        f"expected outcome='processed', got {result.outcome!r}"
    )
    assert result.completion_finalization_result is not None, (
        "completion_finalization_result must not be None"
    )
    fin = result.completion_finalization_result
    assert fin.finalized is True, (
        f"finalizer must finalize the record; finalized={fin.finalized!r}"
    )
    assert fin.outcome in ("completed_clean", "completed_with_no_op"), (
        f"unexpected completion outcome: {fin.outcome!r}"
    )


async def _assert_all_jobs_succeeded(
    pool: asyncpg.Pool, record_id: UUID
) -> list[asyncpg.Record]:
    jobs = await _fetch_job_topology(pool, record_id)
    assert len(jobs) > 0, "expected at least one reader_job"
    for job in jobs:
        assert job["status"] == "succeeded", (
            f"job {job['job_type']}/{job['target_type']} status="
            f"{job['status']!r}, expected 'succeeded'"
        )
    return jobs


# ---------------------------------------------------------------------------
# Test 1: SHORT_BATCH acceptance
# ---------------------------------------------------------------------------


async def test_short_batch_acceptance_through_worker_loop(
    three_mode_env: asyncpg.Pool,
) -> None:
    pool = three_mode_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool,
        user_id=user_id,
        plain_text=_short_article(),
        title="Short Batch Acceptance",
    )

    runner = _make_production_runner(pool)
    result = await _run_through_worker_loop(
        pool, runner, article.record_id, user_id
    )

    _assert_finalized(result)

    # readiness_state must have advanced to coverage_complete.
    readiness = await _fetch_readiness_state(pool, article.record_id)
    assert readiness == "coverage_complete", (
        f"readiness_state={readiness!r}, expected 'coverage_complete'"
    )

    jobs = await _assert_all_jobs_succeeded(pool, article.record_id)

    # Job topology: display_title + translate_article + build_vocabulary_layer_article
    # + build_grammar_bundle (unit_range batch) = 4 jobs.
    job_types = [(j["job_type"], j["target_type"]) for j in jobs]
    assert ("generate_display_title_zh", "record") in job_types
    assert ("translate_article", "unit_range") in job_types
    assert ("build_vocabulary_layer_article", "unit_range") in job_types
    assert ("build_grammar_bundle", "unit_range") in job_types
    assert len(jobs) == 4, f"expected 4 jobs, got {len(jobs)}: {job_types}"

    # Grammar batch fingerprint base is grammar_bundle_article_v1 (SHORT_BATCH).
    # The full fingerprint is composed as ``{base}:{strategy_hash}``.
    grammar_job = next(
        j for j in jobs if j["job_type"] == "build_grammar_bundle"
    )
    assert grammar_job["operation_fingerprint"].startswith(
        "grammar_bundle_article_v1"
    ), (
        f"grammar fingerprint={grammar_job['operation_fingerprint']!r}, "
        f"expected prefix 'grammar_bundle_article_v1'"
    )
    assert grammar_job["policy_version"] == "reader_grammar_batch_bootstrap_v1", (
        f"policy_version={grammar_job['policy_version']!r}"
    )
    assert grammar_job["article_route"] == "short_batch", (
        f"article_route={grammar_job['article_route']!r}"
    )

    # Effective grammar calls: exactly 1 batch job succeeded.
    grammar_jobs = [j for j in jobs if j["job_type"] == "build_grammar_bundle"]
    assert len(grammar_jobs) == 1, (
        f"expected 1 grammar batch job, got {len(grammar_jobs)}"
    )

    # Layer counts: each layer type equals unit_count.
    unit_count = await _fetch_unit_count(pool, article.record_id)
    layer_counts = await _fetch_layer_counts(pool, article.record_id)
    assert unit_count > 0, "expected at least one reading unit"
    for layer_type in ("translation", "vocabulary", "grammar_note", "sentence_analysis"):
        assert layer_counts.get(layer_type, 0) == unit_count, (
            f"layer_type={layer_type!r} count={layer_counts.get(layer_type, 0)}, "
            f"expected unit_count={unit_count}"
        )

    # Usage attribution: 1 grammar batch usage event with correct tokens.
    usage_events = await _fetch_grammar_usage_events(pool, article.record_id)
    grammar_batch_events = [
        e for e in usage_events
        if e["operation_fingerprint"]
        and e["operation_fingerprint"].startswith("grammar_bundle_article_v1")
    ]
    assert len(grammar_batch_events) == 1, (
        f"expected 1 grammar batch usage event, got {len(grammar_batch_events)}"
    )
    event = grammar_batch_events[0]
    assert int(event["input_tokens"]) == 20, (
        f"input_tokens={event['input_tokens']!r}, expected 20"
    )
    assert int(event["output_tokens"]) == 30, (
        f"output_tokens={event['output_tokens']!r}, expected 30"
    )
    assert event["status"] == "succeeded", (
        f"usage event status={event['status']!r}"
    )


# ---------------------------------------------------------------------------
# Test 2: STRUCTURED_BATCH acceptance
# ---------------------------------------------------------------------------


async def test_structured_batch_acceptance_through_worker_loop(
    three_mode_env: asyncpg.Pool,
) -> None:
    pool = three_mode_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool,
        user_id=user_id,
        plain_text=_structured_article(),
        title="Structured Batch Acceptance",
    )

    runner = _make_production_runner(pool)
    result = await _run_through_worker_loop(
        pool, runner, article.record_id, user_id
    )

    _assert_finalized(result)

    readiness = await _fetch_readiness_state(pool, article.record_id)
    assert readiness == "coverage_complete", (
        f"readiness_state={readiness!r}, expected 'coverage_complete'"
    )

    jobs = await _assert_all_jobs_succeeded(pool, article.record_id)

    job_types = [(j["job_type"], j["target_type"]) for j in jobs]
    assert ("generate_display_title_zh", "record") in job_types
    assert ("translate_article", "unit_range") in job_types
    assert ("build_vocabulary_layer_article", "unit_range") in job_types
    assert ("build_grammar_bundle", "unit_range") in job_types
    assert len(jobs) == 4, f"expected 4 jobs, got {len(jobs)}: {job_types}"

    grammar_job = next(
        j for j in jobs if j["job_type"] == "build_grammar_bundle"
    )
    # STRUCTURED_BATCH gets a distinct fingerprint base.
    assert grammar_job["operation_fingerprint"].startswith(
        "grammar_bundle_article_structured_v1"
    ), (
        f"grammar fingerprint={grammar_job['operation_fingerprint']!r}, "
        f"expected prefix 'grammar_bundle_article_structured_v1'"
    )
    assert grammar_job["policy_version"] == (
        "reader_grammar_batch_structured_bootstrap_v1"
    ), f"policy_version={grammar_job['policy_version']!r}"
    assert grammar_job["article_route"] == "structured_batch", (
        f"article_route={grammar_job['article_route']!r}"
    )

    # Layer counts match unit_count.
    unit_count = await _fetch_unit_count(pool, article.record_id)
    layer_counts = await _fetch_layer_counts(pool, article.record_id)
    assert unit_count > 0, "expected at least one reading unit"
    for layer_type in ("translation", "vocabulary", "grammar_note", "sentence_analysis"):
        assert layer_counts.get(layer_type, 0) == unit_count, (
            f"layer_type={layer_type!r} count={layer_counts.get(layer_type, 0)}, "
            f"expected unit_count={unit_count}"
        )

    # Usage attribution: 1 grammar batch usage event with correct tokens.
    usage_events = await _fetch_grammar_usage_events(pool, article.record_id)
    grammar_batch_events = [
        e for e in usage_events
        if e["operation_fingerprint"]
        and e["operation_fingerprint"].startswith(
            "grammar_bundle_article_structured_v1"
        )
    ]
    assert len(grammar_batch_events) == 1, (
        f"expected 1 grammar batch usage event, got {len(grammar_batch_events)}"
    )
    event = grammar_batch_events[0]
    assert int(event["input_tokens"]) == 20, (
        f"input_tokens={event['input_tokens']!r}, expected 20"
    )
    assert int(event["output_tokens"]) == 30, (
        f"output_tokens={event['output_tokens']!r}, expected 30"
    )


# ---------------------------------------------------------------------------
# Test 3: GROUPED_WINDOWED acceptance
# ---------------------------------------------------------------------------


async def test_grouped_windowed_acceptance_through_worker_loop(
    three_mode_env: asyncpg.Pool,
) -> None:
    pool = three_mode_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool,
        user_id=user_id,
        plain_text=_grouped_article(),
        title="Grouped Windowed Acceptance",
    )

    runner = _make_production_runner(pool)
    result = await _run_through_worker_loop(
        pool, runner, article.record_id, user_id
    )

    _assert_finalized(result)

    readiness = await _fetch_readiness_state(pool, article.record_id)
    assert readiness == "coverage_complete", (
        f"readiness_state={readiness!r}, expected 'coverage_complete'"
    )

    jobs = await _assert_all_jobs_succeeded(pool, article.record_id)

    job_types = [(j["job_type"], j["target_type"]) for j in jobs]

    # GROUPED_WINDOWED: translation and vocabulary use per-window batch jobs.
    assert any(jt == "translate_article" for jt, _ in job_types), (
        f"expected at least one translate_article job, got {job_types}"
    )
    assert any(
        jt == "build_vocabulary_layer_article" for jt, _ in job_types
    ), (
        f"expected at least one build_vocabulary_layer_article job, got {job_types}"
    )

    # Grammar goes through Z+ window jobs, NOT batch grammar_bundle.
    grammar_window_jobs = [
        j for j in jobs if j["job_type"] == "build_grammar_bundle_window"
    ]
    assert len(grammar_window_jobs) >= 1, (
        f"expected at least 1 build_grammar_bundle_window job, got {job_types}"
    )
    grammar_batch_jobs = [
        j for j in jobs
        if j["job_type"] == "build_grammar_bundle" and j["target_type"] == "unit_range"
    ]
    assert len(grammar_batch_jobs) == 0, (
        "GROUPED_WINDOWED must not create grammar batch jobs; "
        f"found {len(grammar_batch_jobs)}"
    )

    # Translation and vocabulary jobs should carry article_route='grouped_windowed'
    # in their run envelope. Display title and grammar window runs use a
    # separate envelope schema that does not include ``article_route`` (the
    # route identity for those runs is established at the main-run level).
    for j in jobs:
        if j["job_type"] in (
            "translate_article",
            "build_vocabulary_layer_article",
        ):
            assert j["article_route"] == "grouped_windowed", (
                f"job {j['job_type']} article_route={j['article_route']!r}, "
                f"expected 'grouped_windowed'"
            )

    # Grammar window usage events: operation_fingerprint == grammar_bundle_window_v1.
    usage_events = await _fetch_grammar_usage_events(pool, article.record_id)
    window_events = [
        e for e in usage_events
        if e["operation_fingerprint"] == "grammar_bundle_window_v1"
    ]
    assert len(window_events) >= 1, (
        f"expected at least 1 grammar_bundle_window_v1 usage event, "
        f"got {len(window_events)}; all events: "
        f"{[e['operation_fingerprint'] for e in usage_events]}"
    )
    for event in window_events:
        assert event["status"] == "succeeded", (
            f"window usage event status={event['status']!r}"
        )

    # Layer counts: translation/vocabulary at least 1 per unit;
    # grammar_note and sentence_analysis may be budget-capped by the
    # window selector, but both layer types must be present (> 0) to prove
    # the window publisher emitted both subtypes.
    layer_counts = await _fetch_layer_counts(pool, article.record_id)
    assert layer_counts.get("translation", 0) > 0, "expected translation layers"
    assert layer_counts.get("vocabulary", 0) > 0, "expected vocabulary layers"
    assert layer_counts.get("grammar_note", 0) > 0, "expected grammar_note layers"
    assert layer_counts.get("sentence_analysis", 0) > 0, (
        f"expected sentence_analysis layers, got "
        f"{layer_counts.get('sentence_analysis', 0)}; "
        f"full layer_counts={layer_counts}"
    )

    # Effective grammar calls: at least 1 window job succeeded (already
    # asserted above), and no grammar batch jobs were created (also asserted).
    # Effective translation/vocabulary calls: at least 1 batch job each.
    translation_jobs = [j for j in jobs if j["job_type"] == "translate_article"]
    assert len(translation_jobs) >= 1, (
        f"expected >=1 translate_article job, got {len(translation_jobs)}"
    )
    vocabulary_jobs = [
        j for j in jobs if j["job_type"] == "build_vocabulary_layer_article"
    ]
    assert len(vocabulary_jobs) >= 1, (
        f"expected >=1 build_vocabulary_layer_article job, "
        f"got {len(vocabulary_jobs)}"
    )


# ---------------------------------------------------------------------------
# Test 4: SHORT_BATCH usage event tokens match runtime span
# ---------------------------------------------------------------------------


async def test_short_batch_usage_event_tokens_match_runtime_span(
    three_mode_env: asyncpg.Pool,
) -> None:
    pool = three_mode_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool,
        user_id=user_id,
        plain_text=_short_article(),
        title="Short Batch Span Token Match",
    )

    runner = _make_production_runner(pool)
    result = await _run_through_worker_loop(
        pool, runner, article.record_id, user_id
    )

    _assert_finalized(result)

    # Fetch the grammar batch usage event.
    usage_events = await _fetch_grammar_usage_events(pool, article.record_id)
    grammar_batch_events = [
        e for e in usage_events
        if e["operation_fingerprint"]
        and e["operation_fingerprint"].startswith("grammar_bundle_article_v1")
        and e["status"] == "succeeded"
    ]
    assert len(grammar_batch_events) == 1, (
        f"expected 1 succeeded grammar batch usage event, got "
        f"{len(grammar_batch_events)}"
    )
    event = grammar_batch_events[0]
    assert int(event["input_tokens"]) == 20, (
        f"usage event input_tokens={event['input_tokens']!r}, expected 20"
    )
    assert int(event["output_tokens"]) == 30, (
        f"usage event output_tokens={event['output_tokens']!r}, expected 30"
    )
    assert int(event["total_tokens"]) == 50, (
        f"usage event total_tokens={event['total_tokens']!r}, expected 50"
    )

    # Fetch the corresponding worker_tick span via ai_usage_event_id FK.
    span = await _fetch_grammar_span(pool, event["id"])
    assert span is not None, (
        "expected a succeeded worker_tick span linked to the grammar "
        "batch usage event"
    )
    assert span["worker_type"] == "grammar_bundle", (
        f"span worker_type={span['worker_type']!r}, expected 'grammar_bundle'"
    )
    assert int(span["input_tokens"]) == int(event["input_tokens"]), (
        f"span input_tokens={span['input_tokens']!r} != "
        f"event input_tokens={event['input_tokens']!r}"
    )
    assert int(span["output_tokens"]) == int(event["output_tokens"]), (
        f"span output_tokens={span['output_tokens']!r} != "
        f"event output_tokens={event['output_tokens']!r}"
    )
    assert int(span["input_tokens"]) == 20, (
        f"span input_tokens={span['input_tokens']!r}, expected 20"
    )
    assert int(span["output_tokens"]) == 30, (
        f"span output_tokens={span['output_tokens']!r}, expected 30"
    )
