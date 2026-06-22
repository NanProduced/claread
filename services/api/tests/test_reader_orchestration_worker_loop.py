from __future__ import annotations

from datetime import timedelta
from pathlib import Path
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
    TranslationLayerOutput,
    VocabularyHighlightItem,
    VocabularyLayerOutput,
)
from app.services.reader_orchestration.grammar_worker import (
    GrammarBundleWorkerService,
    GrammarExecutionResult,
    GrammarJobContext,
)
from app.services.reader_orchestration.job_bootstrap import (
    EnhancementBootstrapJobCounts,
    EnhancementBootstrapSummary,
    EnhancementJobBootstrapService,
)
from app.services.reader_orchestration.orchestrator import ReaderOrchestrator
from app.services.reader_orchestration.pipeline_runner import (
    EnhancementOutcomeCounts,
    EnhancementWorkerTickCounts,
    ReaderEnhancementPipelineRunner,
    ReaderPipelineRunSummary,
)
from app.services.reader_orchestration.translation_worker import (
    TranslationExecutionError,
    TranslationExecutionResult,
    TranslationJobContext,
    TranslationWorkerService,
)
from app.services.reader_orchestration.vocabulary_worker import (
    UnconfiguredVocabularyExecutor,
    VocabularyExecutionResult,
    VocabularyJobContext,
    VocabularyWorkerService,
)
from app.services.reader_orchestration.worker_loop import (
    READER_WORKER_USER_ADVISORY_LOCK_NAMESPACE,
    ReaderEnhancementWorkerLoopService,
    WorkerLoopCandidateRecord,
    record_advisory_lock_key,
    user_advisory_lock_key,
)
from tests.reader_orchestration_test_support import (
    BASELINE_SQL,
    connect_admin,
    insert_user,
    make_pool,
    submit_article_ready,
)

pytestmark = pytest.mark.anyio

API_ROOT = Path(__file__).resolve().parents[1]
LEASE_DURATION = timedelta(seconds=30)


class _CapturingRunner:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def run(
        self,
        *,
        record_id: UUID,
        user_id: UUID,
        lease_owner: str,
        lease_duration: timedelta,
        max_ticks: int,
        max_jobs: int,
    ) -> ReaderPipelineRunSummary:
        self.calls.append(
            {
                "record_id": record_id,
                "user_id": user_id,
                "lease_owner": lease_owner,
                "lease_duration": lease_duration,
                "max_ticks": max_ticks,
                "max_jobs": max_jobs,
            }
        )
        return ReaderPipelineRunSummary(
            record_id=record_id,
            base_id=UUID("00000000-0000-0000-0000-000000000001"),
            expected_generation=1,
            bootstrap=EnhancementBootstrapSummary(
                record_id=record_id,
                base_id=UUID("00000000-0000-0000-0000-000000000001"),
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
            stopped_reason="all_workers_no_job",
        )


class _StaticTranslator:
    async def translate(
        self,
        context: TranslationJobContext,
    ) -> TranslationExecutionResult:
        return TranslationExecutionResult(
            output=TranslationLayerOutput(
                target_language="zh-CN",
                translated_text=f"译文：{context.source_text}",
                notes=[],
                confidence="normal",
            ),
            usage_data={"input_tokens": 1, "output_tokens": 1},
            prompt_version="worker-loop-translation",
            model_profile="worker_loop_fake_translation",
            model_provider="fake",
            model_name="worker-loop-translation",
        )


class _RetryLaterTranslator:
    async def translate(
        self,
        context: TranslationJobContext,
    ) -> TranslationExecutionResult:
        raise TranslationExecutionError(
            "temporary translation outage",
            retryable=True,
            failure_class="provider",
            failure_code="temporary_outage",
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
                        brief_explanation="worker loop vocab",
                        reason="worker_loop_test",
                    )
                ]
            ),
            usage_data={"input_tokens": 1, "output_tokens": 1},
            prompt_version="worker-loop-vocabulary",
            model_profile="worker_loop_fake_vocabulary",
            model_provider="fake",
            model_name="worker-loop-vocabulary",
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
                        grammar_point="worker loop grammar",
                        pattern="SVO",
                        note="Deterministic grammar note for worker loop test.",
                    )
                ],
                sentence_analyses=[
                    SentenceAnalysisItem(
                        anchor=sentence_anchor,
                        label="main clause",
                        analysis="Deterministic sentence analysis for worker loop test.",
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
            prompt_version="worker-loop-grammar",
            model_profile="worker_loop_fake_grammar",
            model_provider="fake",
            model_name="worker-loop-grammar",
        )


@pytest.fixture
async def worker_loop_env() -> asyncpg.Pool:
    schema_name = f"test_reader_worker_loop_{uuid4().hex}"
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


def _make_runner(
    pool: asyncpg.Pool,
    *,
    translator: object | None = None,
    vocabulary_executor: object | None = None,
    grammar_executor: object | None = None,
) -> ReaderEnhancementPipelineRunner:
    translation_orchestrator = None
    if translator is not None:
        translation_orchestrator = ReaderOrchestrator(
            pool=pool,
            worker_service=TranslationWorkerService(pool=pool, translator=translator),
        )
    vocabulary_worker = None
    if vocabulary_executor is not None:
        vocabulary_worker = VocabularyWorkerService(pool=pool, executor=vocabulary_executor)
    grammar_worker = None
    if grammar_executor is not None:
        grammar_worker = GrammarBundleWorkerService(pool=pool, executor=grammar_executor)
    return ReaderEnhancementPipelineRunner(
        pool=pool,
        translation_orchestrator=translation_orchestrator,
        vocabulary_worker_service=vocabulary_worker,
        grammar_worker_service=grammar_worker,
    )


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


async def _count_jobs(
    pool: asyncpg.Pool,
    record_id: UUID,
    *,
    status: str | None = None,
) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM reader_jobs
            WHERE reading_record_id = $1
              AND ($2::text IS NULL OR status = $2)
            """,
            record_id,
            status,
        )


async def _count_layers(
    pool: asyncpg.Pool,
    record_id: UUID,
    layer_type: str,
) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM enhancement_layers
            WHERE reading_record_id = $1
              AND layer_type = $2
              AND status = 'published'
            """,
            record_id,
            layer_type,
        )


async def test_scan_eligible_records_excludes_coverage_complete(
    worker_loop_env: asyncpg.Pool,
) -> None:
    service = ReaderEnhancementWorkerLoopService(pool=worker_loop_env)
    user_id = await insert_user(worker_loop_env)
    article_ready = await submit_article_ready(
        worker_loop_env,
        user_id=user_id,
        title="Article Ready",
    )
    initial_ready = await submit_article_ready(
        worker_loop_env,
        user_id=user_id,
        title="Initial Ready",
    )
    coverage_complete = await submit_article_ready(
        worker_loop_env,
        user_id=user_id,
        title="Coverage Complete",
    )

    async with worker_loop_env.acquire() as conn:
        await conn.execute(
            """
            UPDATE reading_records
            SET readiness_state = 'initial_enhancement_ready'
            WHERE id = $1
            """,
            initial_ready.record_id,
        )
        await conn.execute(
            """
            UPDATE reading_records
            SET readiness_state = 'coverage_complete'
            WHERE id = $1
            """,
            coverage_complete.record_id,
        )

    candidates = await service.scan_eligible_records(batch_size=20)
    candidate_ids = {candidate.record_id for candidate in candidates}

    assert article_ready.record_id in candidate_ids
    assert initial_ready.record_id in candidate_ids
    assert coverage_complete.record_id not in candidate_ids


async def test_scan_eligible_records_requires_active_base_status_and_presence(
    worker_loop_env: asyncpg.Pool,
) -> None:
    service = ReaderEnhancementWorkerLoopService(pool=worker_loop_env)
    user_id = await insert_user(worker_loop_env)
    valid = await submit_article_ready(worker_loop_env, user_id=user_id, title="Valid")
    inactive_base = await submit_article_ready(worker_loop_env, user_id=user_id, title="Inactive")
    missing_base = await submit_article_ready(worker_loop_env, user_id=user_id, title="Missing")

    async with worker_loop_env.acquire() as conn:
        await conn.execute(
            "UPDATE reading_bases SET status = 'superseded' WHERE id = $1",
            inactive_base.base_id,
        )
        await conn.execute(
            "UPDATE reading_records SET active_base_id = NULL WHERE id = $1",
            missing_base.record_id,
        )

    candidates = await service.scan_eligible_records(batch_size=20)
    candidate_ids = {candidate.record_id for candidate in candidates}

    assert valid.record_id in candidate_ids
    assert inactive_base.record_id not in candidate_ids
    assert missing_base.record_id not in candidate_ids


async def test_process_candidate_skips_when_record_lock_is_unavailable(
    worker_loop_env: asyncpg.Pool,
) -> None:
    runner = _CapturingRunner()
    service = ReaderEnhancementWorkerLoopService(
        pool=worker_loop_env,
        pipeline_runner=runner,  # type: ignore[arg-type]
    )
    user_id = await insert_user(worker_loop_env)
    article = await submit_article_ready(worker_loop_env, user_id=user_id, title="Lock")
    candidate = await _find_candidate(service, article.record_id)
    lock_key = record_advisory_lock_key(article.record_id)

    async with worker_loop_env.acquire() as conn:
        await conn.execute("SELECT pg_advisory_lock($1)", lock_key)
        try:
            result = await service.process_candidate(
                candidate=candidate,
                lease_owner_prefix="worker-loop-test",
                max_ticks=6,
                max_jobs=6,
            )
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", lock_key)

    assert result.outcome == "lock_unavailable"
    assert result.pipeline_summary is None
    assert runner.calls == []


async def test_process_candidate_skips_when_user_lock_is_unavailable(
    worker_loop_env: asyncpg.Pool,
) -> None:
    runner = _CapturingRunner()
    service = ReaderEnhancementWorkerLoopService(
        pool=worker_loop_env,
        pipeline_runner=runner,  # type: ignore[arg-type]
    )
    user_id = await insert_user(worker_loop_env)
    article = await submit_article_ready(
        worker_loop_env,
        user_id=user_id,
        title="User Lock",
    )
    candidate = await _find_candidate(service, article.record_id)
    user_lock_key = user_advisory_lock_key(user_id)

    async with worker_loop_env.acquire() as conn:
        await conn.execute(
            "SELECT pg_advisory_lock($1, $2)",
            READER_WORKER_USER_ADVISORY_LOCK_NAMESPACE,
            user_lock_key,
        )
        try:
            result = await service.process_candidate(
                candidate=candidate,
                lease_owner_prefix="worker-loop-test",
                max_ticks=6,
                max_jobs=6,
            )
        finally:
            await conn.execute(
                "SELECT pg_advisory_unlock($1, $2)",
                READER_WORKER_USER_ADVISORY_LOCK_NAMESPACE,
                user_lock_key,
            )

    assert result.outcome == "lock_unavailable"
    assert result.pipeline_summary is None
    assert runner.calls == []


async def test_process_candidate_is_record_scoped_and_does_not_consume_other_record_queue(
    worker_loop_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(worker_loop_env)
    record_b = await submit_article_ready(
        worker_loop_env,
        user_id=user_id,
        title="Other Record",
    )
    await EnhancementJobBootstrapService(pool=worker_loop_env).bootstrap_missing_jobs(
        record_id=record_b.record_id,
        user_id=user_id,
    )
    other_queued_jobs_before = await _count_jobs(
        worker_loop_env,
        record_b.record_id,
        status="queued",
    )
    assert other_queued_jobs_before > 0

    record_a = await submit_article_ready(
        worker_loop_env,
        user_id=user_id,
        title="Target Record",
        plain_text="Single sentence only.",
    )
    runner = _make_runner(
        worker_loop_env,
        translator=_StaticTranslator(),
        vocabulary_executor=_StaticVocabularyExecutor(),
        grammar_executor=_StaticGrammarExecutor(),
    )
    service = ReaderEnhancementWorkerLoopService(
        pool=worker_loop_env,
        pipeline_runner=runner,
    )
    candidate = await _find_candidate(service, record_a.record_id)

    result = await service.process_candidate(
        candidate=candidate,
        lease_owner_prefix="worker-loop-scope",
        max_ticks=12,
        max_jobs=12,
    )

    assert result.outcome == "processed"
    assert result.pipeline_summary is not None
    assert result.pipeline_summary.record_id == record_a.record_id
    assert await _count_layers(worker_loop_env, record_a.record_id, "translation") == 1
    assert await _count_layers(worker_loop_env, record_b.record_id, "translation") == 0
    assert (
        await _count_jobs(worker_loop_env, record_b.record_id, status="queued")
        == other_queued_jobs_before
    )


async def test_process_candidate_forwards_custom_lease_duration_to_pipeline_runner(
    worker_loop_env: asyncpg.Pool,
) -> None:
    runner = _CapturingRunner()
    service = ReaderEnhancementWorkerLoopService(
        pool=worker_loop_env,
        pipeline_runner=runner,  # type: ignore[arg-type]
    )
    user_id = await insert_user(worker_loop_env)
    article = await submit_article_ready(
        worker_loop_env,
        user_id=user_id,
        title="Lease Duration",
    )
    candidate = await _find_candidate(service, article.record_id)
    lease_duration = timedelta(seconds=120)

    result = await service.process_candidate(
        candidate=candidate,
        lease_owner_prefix="worker-loop-lease",
        lease_duration=lease_duration,
        max_ticks=6,
        max_jobs=6,
    )

    assert result.outcome == "processed"
    assert result.pipeline_summary is not None
    assert len(runner.calls) == 1
    assert runner.calls[0]["lease_duration"] == lease_duration


async def test_retry_later_records_are_not_hot_looped_until_available(
    worker_loop_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(worker_loop_env)
    article = await submit_article_ready(
        worker_loop_env,
        user_id=user_id,
        title="Retry Later",
    )
    runner = _make_runner(worker_loop_env, translator=_RetryLaterTranslator())
    service = ReaderEnhancementWorkerLoopService(
        pool=worker_loop_env,
        pipeline_runner=runner,
    )
    candidate = await _find_candidate(service, article.record_id)

    result = await service.process_candidate(
        candidate=candidate,
        lease_owner_prefix="worker-loop-retry",
        max_ticks=6,
        max_jobs=6,
    )

    assert result.pipeline_summary is not None
    assert result.pipeline_summary.stopped_reason == "attention_required"
    assert result.pipeline_summary.stopped_outcome == "retry_later"

    candidates_after_retry = await service.scan_eligible_records(batch_size=20)
    candidate_ids_after_retry = {
        record.record_id for record in candidates_after_retry
    }
    async with worker_loop_env.acquire() as conn:
        await conn.execute(
            """
            UPDATE reader_jobs
            SET status = 'cancelled'
            WHERE reading_record_id = $1
              AND status = 'queued'
            """,
            article.record_id,
        )
    candidates_after_retry = await service.scan_eligible_records(batch_size=20)
    candidate_ids_after_retry = {
        record.record_id for record in candidates_after_retry
    }
    assert article.record_id not in candidate_ids_after_retry

    async with worker_loop_env.acquire() as conn:
        await conn.execute(
            """
            UPDATE reader_jobs
            SET available_at = NOW() - INTERVAL '1 second'
            WHERE reading_record_id = $1
              AND status = 'retry_later'
            """,
            article.record_id,
        )

    candidates_after_due = await service.scan_eligible_records(batch_size=20)
    candidate_ids_after_due = {record.record_id for record in candidates_after_due}
    assert article.record_id in candidate_ids_after_due


async def test_worker_loop_preserves_fail_closed_when_real_executor_is_unconfigured(
    worker_loop_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(worker_loop_env)
    article = await submit_article_ready(
        worker_loop_env,
        user_id=user_id,
        title="Fail Closed",
    )
    runner = _make_runner(
        worker_loop_env,
        translator=_StaticTranslator(),
        vocabulary_executor=UnconfiguredVocabularyExecutor(),
    )
    service = ReaderEnhancementWorkerLoopService(
        pool=worker_loop_env,
        pipeline_runner=runner,
    )
    candidate = await _find_candidate(service, article.record_id)

    result = await service.process_candidate(
        candidate=candidate,
        lease_owner_prefix="worker-loop-fail-closed",
        max_ticks=6,
        max_jobs=6,
    )

    assert result.pipeline_summary is not None
    assert result.pipeline_summary.stopped_reason == "attention_required"
    assert result.pipeline_summary.stopped_worker_type == "vocabulary"
    assert result.pipeline_summary.stopped_outcome == "failed_terminal"
    assert result.pipeline_summary.attention_code == "vocabulary_executor_unconfigured"
    assert await _count_layers(worker_loop_env, article.record_id, "translation") == 1
    assert await _count_layers(worker_loop_env, article.record_id, "vocabulary") == 0


def test_worker_loop_module_does_not_reference_render_scene_or_projection_ops() -> None:
    worker_loop_path = (
        API_ROOT / "app" / "services" / "reader_orchestration" / "worker_loop.py"
    )
    script_path = API_ROOT / "scripts" / "run_reader_enhancement_worker.py"
    worker_text = worker_loop_path.read_text(encoding="utf-8")
    script_text = script_path.read_text(encoding="utf-8")

    for text in (worker_text, script_text):
        assert "render_scene_json" not in text
        assert "projection_ops" not in text
        assert "raw_plate" not in text
        assert "slate_path" not in text
