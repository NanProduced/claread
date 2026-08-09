from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.config.settings import Settings
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
from app.services.model_execution_journal import (
    ExecutionIdentity,
    prepare_capture_envelope,
)
from app.services.model_execution_journal.service import ModelExecutionJournalService
from app.services.reader_orchestration.article_ready_service import (
    ArticleReadyPersistenceService,
)
from app.services.reader_orchestration.completion_finalizer import (
    COMPLETION_TARGET_READINESS_STATE,
    RECORD_COMPLETION_FINALIZED_EVENT_TYPE,
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
    TranslationBatchExecutionResult,
    TranslationBatchJobContext,
    TranslationExecutionError,
    TranslationExecutionResult,
    TranslationJobContext,
    TranslationWorkerService,
    build_deterministic_translation_groups,
)
from app.services.reader_orchestration.vocabulary_worker import (
    UnconfiguredVocabularyExecutor,
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
    READER_WORKER_USER_ADVISORY_LOCK_NAMESPACE,
    ReaderEnhancementWorkerLoopService,
    WorkerLoopCandidateRecord,
    record_advisory_lock_key,
    user_advisory_lock_key,
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

API_ROOT = Path(__file__).resolve().parents[1]
# T1.1 short-article batch path: migration 0017 adds ``translate_article``
# and ``build_vocabulary_layer_article`` to the ``reader_jobs.job_type``
# CHECK constraint. Required because the default fixture text is well under
# the 6000-char short-article threshold, so bootstrap creates batch jobs.
# T5.3/T5.7: semantic_outline job_type + layer_type + worker_type CHECK.
# Without 0020, pipeline worker_tick spans with worker_type=semantic_outline
# fail CHECK and block real-chain readiness finalization.
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


class _StaticSummaryRunner:
    def __init__(self, summary: ReaderPipelineRunSummary) -> None:
        self.summary = summary

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
        return self.summary


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


class _StaticTitleGenerator:
    async def generate(
        self,
        context: DisplayTitleJobContext,
    ) -> DisplayTitleExecutionResult:
        return DisplayTitleExecutionResult(
            title_zh="循环测试文章标题",
            usage_data={"input_tokens": 1, "output_tokens": 1},
            prompt_version="worker-loop-title",
            model_profile="worker_loop_fake_title",
            model_provider="fake",
            model_name="worker-loop-title",
        )


# T1.1 short-article batch path fakes. The default fixture text is well under
# the 6000-char short-article threshold, so bootstrap creates batch jobs
# (``translate_article`` / ``build_vocabulary_layer_article``) and the pipeline
# runner dispatches them via ``translation_batch`` / ``vocabulary_batch`` worker
# types. Without these fakes the batch worker service falls back to
# ``PydanticAITranslationBatchExecutor`` / ``PydanticAIVocabularyBatchExecutor``
# and attempts a real LLM call. Mirrors the fakes in
# test_reader_orchestration_pipeline_runner.py.
WORD_RE = re.compile(r"[A-Za-z]+")


class _StaticBatchTranslator:
    """Fake batch translator: 1 LLM call → N per-unit translation groups.

    Deterministic-grouping contract: echoes the backend-predefined
    group_ids from :func:`build_deterministic_translation_groups` and
    returns a per-group translated_text, matching the hydrate contract.
    """

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
            prompt_version="worker-loop-translation-batch",
            model_profile="worker_loop_fake_translation_batch",
            model_provider="fake",
            model_name="worker-loop-translation-batch",
        )


class _StaticBatchVocabularyExecutor:
    """Fake batch vocabulary executor: 1 LLM call → N per-unit candidates."""

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
                            brief_explanation="关键词",
                            reason="worker_loop_test_batch",
                        )
                    ],
                )
            )
        return VocabularyBatchExecutionResult(
            output=VocabularyBatchCandidateOutput(units=units_output),
            usage_data={"input_tokens": 1, "output_tokens": 1},
            prompt_version="worker-loop-vocabulary-batch",
            model_profile="worker_loop_fake_vocabulary_batch",
            model_provider="fake",
            model_name="worker-loop-vocabulary-batch",
        )


class _RetryLaterBatchTranslator:
    """T1.1 batch version of _RetryLaterTranslator: always raises a retryable
    translation error so the retry_later hot-loop guard is exercised on the
    batch path.
    """

    async def translate_batch(
        self,
        context: TranslationBatchJobContext,
    ) -> TranslationBatchExecutionResult:
        raise TranslationExecutionError(
            "temporary batch translation outage",
            retryable=True,
            failure_class="provider",
            failure_code="temporary_outage",
        )


class _UnconfiguredVocabularyBatchExecutor:
    """T1.1 batch version of UnconfiguredVocabularyExecutor: always raises
    vocabulary_executor_unconfigured so the fail-closed path is exercised on
    the batch path.
    """

    async def generate_batch(
        self,
        context: VocabularyBatchJobContext,
    ) -> VocabularyBatchExecutionResult:
        from app.services.reader_orchestration.vocabulary_worker import (
            VocabularyExecutionError,
        )

        raise VocabularyExecutionError(
            "vocabulary batch executor is not configured",
            retryable=False,
            failure_class="configuration",
            failure_code="vocabulary_executor_unconfigured",
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
    title_generator: object | None = None,
    translator: object | None = None,
    vocabulary_executor: object | None = None,
    grammar_executor: object | None = None,
    batch_translator: object | None = None,
    batch_vocabulary_executor: object | None = None,
) -> ReaderEnhancementPipelineRunner:
    # T1.1 short-article batch path: the default fixture text is well under
    # the 6000-char threshold, so bootstrap creates batch jobs and the runner
    # dispatches them via translation_batch / vocabulary_batch worker types.
    # We must inject fake batch executors alongside the per-unit fakes,
    # otherwise the batch worker service falls back to PydanticAI*BatchExecutor
    # and attempts a real LLM call. Mirrors _make_runner in
    # test_reader_orchestration_pipeline_runner.py.
    translation_worker = None
    if translator is not None:
        translation_worker = TranslationWorkerService(
            pool=pool,
            layer_publisher=CompatTranslationLayerPublisher(pool=pool),
            translator=translator,
            batch_translator=batch_translator or _StaticBatchTranslator(),
        )
    translation_orchestrator = (
        ReaderOrchestrator(pool=pool, worker_service=translation_worker)
        if translation_worker is not None
        else None
    )
    vocabulary_worker = None
    if vocabulary_executor is not None:
        vocabulary_worker = VocabularyWorkerService(
            pool=pool,
            executor=vocabulary_executor,
            batch_executor=batch_vocabulary_executor
            or _StaticBatchVocabularyExecutor(),
        )
    grammar_worker = None
    if grammar_executor is not None:
        grammar_worker = GrammarBundleWorkerService(pool=pool, executor=grammar_executor)
    display_title_worker = DisplayTitleWorkerService(
        pool=pool,
        generator=title_generator or _StaticTitleGenerator(),
    )
    return ReaderEnhancementPipelineRunner(
        pool=pool,
        display_title_worker_service=display_title_worker,
        translation_orchestrator=translation_orchestrator,
        translation_batch_worker_service=translation_worker,
        vocabulary_worker_service=vocabulary_worker,
        grammar_worker_service=grammar_worker,
        enable_grammar_window=False,
        # _env_file=None: offline tests must not pick up real LLM /
        # embedding / vector credentials from the local .env file.
        # This keeps semantic_outline_generation_enabled=False (default)
        # so the PydanticAISemanticOutlineGenerator is never constructed
        # and no real LLM call is attempted during the test.
        settings=Settings(_env_file=None),
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


async def _load_product_state(pool: asyncpg.Pool, record_id: UUID) -> str:
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            """
            SELECT product_state
            FROM reading_records
            WHERE id = $1
            """,
            record_id,
        )
    if value is None:
        raise AssertionError(f"product_state for record {record_id} not found")
    return str(value)


async def _poll_events_after(
    pool: asyncpg.Pool,
    *,
    record_id: UUID,
    user_id: UUID,
    after_sequence: int,
):
    result = await ReaderEventRuntime(pool=pool).poll_events(
        record_id=record_id,
        user_id=user_id,
        after_sequence=after_sequence,
        limit=20,
    )
    return result.events


async def _load_snapshot_product_state(
    pool: asyncpg.Pool,
    *,
    record_id: UUID,
    user_id: UUID,
) -> str:
    snapshot = await _load_snapshot(
        pool,
        record_id=record_id,
        user_id=user_id,
    )
    return snapshot.record.product_state


async def _load_snapshot(
    pool: asyncpg.Pool,
    *,
    record_id: UUID,
    user_id: UUID,
):
    snapshot = await ArticleReadyPersistenceService(pool=pool).load_snapshot(
        record_id=record_id,
        user_id=user_id,
    )
    return snapshot


def _find_progress_layer(
    snapshot,
    *,
    capability: str,
    status: str | None = None,
    layer_type: str | None = None,
    job_type: str | None = None,
):
    for layer in snapshot.enhancement_progress.layers:
        if layer.capability != capability:
            continue
        if status is not None and layer.status != status:
            continue
        if layer_type is not None and layer.layer_type != layer_type:
            continue
        if job_type is not None and layer.job_type != job_type:
            continue
        return layer
    raise AssertionError(
        "progress layer not found for "
        f"capability={capability!r}, status={status!r}, "
        f"layer_type={layer_type!r}, job_type={job_type!r}"
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


async def test_scan_selects_system_paused_captured_grammar_batch(
    worker_loop_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(worker_loop_env)
    article = await submit_article_ready(
        worker_loop_env,
        user_id=user_id,
        title="Captured Grammar Resume",
    )
    await EnhancementJobBootstrapService(
        pool=worker_loop_env
    ).bootstrap_missing_jobs(
        record_id=article.record_id,
        user_id=user_id,
    )

    async with worker_loop_env.acquire() as conn:
        job = await conn.fetchrow(
            """
            SELECT id, run_id
            FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = 'build_grammar_bundle'
              AND target_type = 'unit_range'
            """,
            article.record_id,
        )
        assert job is not None
        await conn.execute(
            "DELETE FROM reader_jobs WHERE reading_record_id = $1 AND id <> $2",
            article.record_id,
            job["id"],
        )
        await conn.execute(
            """
            UPDATE reader_jobs
            SET status = 'paused',
                attempt_count = 1,
                pause_owner = 'system',
                rationale_code = 'model_execution_captured_resume_required',
                failure_class = 'model_execution',
                failure_code = 'post_provider_resume_required'
            WHERE id = $1
            """,
            job["id"],
        )

    identity = ExecutionIdentity(
        invocation_key=(
            f"reader:reader_grammar_bundle:{job['id']}:1:1"
        ),
        reader_job_id=job["id"],
        reader_run_id=job["run_id"],
        attempt_ordinal=1,
        execution_slot=1,
    )
    journal = ModelExecutionJournalService(pool=worker_loop_env)
    await journal.begin_execution(
        identity=identity,
        invocation_kind="reader.grammar_batch",
    )
    await journal.capture_execution(
        identity=identity,
        prepared=prepare_capture_envelope(
            invocation_kind="reader.grammar_batch",
            resume_payload_kind="reader.grammar_batch.result",
            resume_payload_schema_version=1,
            usage_event_draft_schema_version=1,
            normalized_payload={"outputs": [], "diagnostics": None},
            usage_event_draft={
                "usage_scope": "system_internal",
                "capability_code": "reader_grammar_bundle",
                "billing_mode": "internal_only",
                "status": "model_call_completed",
                "user_id": str(user_id),
                "reading_record_id": str(article.record_id),
                "reader_run_id": str(job["run_id"]),
                "reader_job_id": str(job["id"]),
                "model_route": "reader_layer_grammar_bundle",
                "model_provider": "fake-provider",
                "model_name": "fake-model",
            },
        ),
    )

    candidates = await ReaderEnhancementWorkerLoopService(
        pool=worker_loop_env
    ).scan_eligible_records(batch_size=20)
    candidate = next(
        item for item in candidates if item.record_id == article.record_id
    )
    assert candidate.runnable_job_count == 1


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
    assert await _load_product_state(worker_loop_env, article.record_id) == "readable_enhancing"
    events_after_retry = await _poll_events_after(
        worker_loop_env,
        record_id=article.record_id,
        user_id=user_id,
        after_sequence=article.article_ready_sequence,
    )
    assert [
        event
        for event in events_after_retry
        if event.event_type != "record_state_changed"
    ] == []


async def test_worker_loop_real_chain_updates_snapshot_progress_and_emits_reload_events(
    worker_loop_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(worker_loop_env)
    article = await submit_article_ready(
        worker_loop_env,
        user_id=user_id,
        title="Real Chain Smoke",
    )
    await ReaderEnhancementPipelineRunner(
        pool=worker_loop_env,
        enable_grammar_window=False,
        # _env_file=None: prevent .env leakage into the offline test
        # (see _make_runner for the full rationale).
        settings=Settings(_env_file=None),
    ).bootstrap_missing_jobs(
        record_id=article.record_id,
        user_id=user_id,
    )
    initial_snapshot = await _load_snapshot(
        worker_loop_env,
        record_id=article.record_id,
        user_id=user_id,
    )
    initial_translation = _find_progress_layer(
        initial_snapshot,
        capability="translation",
        status="queued",
        # T1.1: 短文走 batch 路径，bootstrap 创建 translate_article 而非
        # translate_unit job
        job_type="translate_article",
    )

    assert initial_snapshot.record.readiness_state == "article_ready"
    assert initial_snapshot.enhancement_progress.overall_status == "readable_enhancing"
    assert initial_translation.job_status == "queued"

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
    candidate = await _find_candidate(service, article.record_id)

    # T5.7: pipeline worker order includes non-budget semantic_outline ticks
    # (usually no_job under default eligibility=false). Allow enough ticks so
    # per-unit grammar jobs still finish after batch translation/vocabulary.
    result = await service.process_candidate(
        candidate=candidate,
        lease_owner_prefix="worker-loop-real-chain",
        max_ticks=24,
        max_jobs=24,
    )

    assert result.outcome == "processed"
    assert result.pipeline_summary is not None
    assert result.pipeline_summary.record_id == article.record_id
    assert await _count_layers(worker_loop_env, article.record_id, "translation") >= 1
    assert await _count_layers(worker_loop_env, article.record_id, "vocabulary") >= 1
    assert await _count_layers(worker_loop_env, article.record_id, "grammar_note") >= 1
    assert await _count_layers(worker_loop_env, article.record_id, "sentence_analysis") >= 1

    events = await _poll_events_after(
        worker_loop_env,
        record_id=article.record_id,
        user_id=user_id,
        after_sequence=article.article_ready_sequence,
    )
    event_types = [event.event_type for event in events]
    assert "layer_published" in event_types

    reloaded_snapshot = await _load_snapshot(
        worker_loop_env,
        record_id=article.record_id,
        user_id=user_id,
    )
    # P1: ``max_ticks_reached`` / ``max_jobs_reached`` are now finalizable.
    # The pipeline runner checks caps AFTER incrementing the processed count,
    # so the last succeeding job can land exactly on the budget. When all
    # enhancement jobs are terminal, the finalizer transitions
    # ``readiness_state -> coverage_complete`` and publishes a
    # ``record_completion_finalized`` event AFTER the pipeline summary's
    # ``last_event_sequence`` was captured. The reloaded snapshot therefore
    # reflects the finalizer's event sequence, not the pipeline's.
    if (
        result.completion_finalization_result is not None
        and result.completion_finalization_result.finalized
    ):
        assert (
            reloaded_snapshot.last_event_sequence
            == result.completion_finalization_result.event_sequence
        )
        assert reloaded_snapshot.record.readiness_state == "coverage_complete"
        assert result.completion_finalization_result.outcome == "completed_clean"
    else:
        assert (
            reloaded_snapshot.last_event_sequence
            == result.pipeline_summary.last_event_sequence
        )
    assert reloaded_snapshot.record.product_state == "readable_enhancing"
    assert reloaded_snapshot.enhancement_progress.overall_status == "ready"
    assert _find_progress_layer(
        reloaded_snapshot,
        capability="translation",
        status="succeeded",
        layer_type="translation",
    )
    assert _find_progress_layer(
        reloaded_snapshot,
        capability="vocabulary",
        status="succeeded",
        layer_type="vocabulary",
    )
    assert _find_progress_layer(
        reloaded_snapshot,
        capability="grammar",
        status="succeeded",
    )


# ---------------------------------------------------------------------------
# T3.5 worker-loop closed-loop: stuck analysis windows -> force-fail ->
# completed_with_failures.
#
# When all enhancement jobs are terminal but analysis windows remain
# pending/running (e.g. the grammar-window grammar window worker is not registered in
# this deployment, or a window lease is stuck), the candidate scan would
# never re-pick the record (it only re-picks records with runnable jobs).
# The finalizer force-fails the stuck windows and finalizes as
# ``completed_with_failures`` so the record is not wedged forever. This
# test exercises the full worker_loop -> pipeline_runner -> finalizer
# integration path, not just the finalizer in isolation.
# ---------------------------------------------------------------------------


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
            VALUES ($1, $2, 'grammar_bundle', 't35_worker_loop_v1', $3,
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


async def _load_analysis_window_statuses(
    pool: asyncpg.Pool,
    *,
    record_id: UUID,
) -> list[tuple[UUID, str, dict]]:
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


async def test_worker_loop_force_fails_stuck_windows_and_finalizes_with_failures(
    worker_loop_env: asyncpg.Pool,
) -> None:
    """Worker-loop closed-loop: pipeline drains the last enhancement jobs to
    success while analysis windows remain stuck ``pending`` / ``running``
    (e.g. the grammar-window grammar window worker is not registered in this
    deployment, or a window lease is stuck) -> finalizer force-fails the
    windows -> record transitions to ``coverage_complete`` with
    ``completed_with_failures``.

    The candidate scan only re-picks records with runnable jobs. Once the
    pipeline drains the last enhancement job, the record has
    ``runnable_job_count = 0`` and ``tracked_job_count > 0`` — it would
    never be re-scanned. Leaving the windows pending would therefore wedge
    the record in ``article_ready`` forever. The finalizer runs inside the
    same ``process_candidate`` call (the pipeline returned
    ``all_workers_no_job``), force-fails the stuck windows so the durable
    state is truthful, and finalizes as ``completed_with_failures``.

    This exercises the full worker_loop -> pipeline_runner -> finalizer
    integration path, not just the finalizer in isolation.
    """
    user_id = await insert_user(worker_loop_env)
    article = await submit_article_ready(
        worker_loop_env,
        user_id=user_id,
        title="Worker Loop Stuck Windows",
    )
    # Insert a grammar analysis plan with two stuck windows: one pending
    # (worker not registered) and one running (stuck lease). The
    # enable_grammar_window=False runner never touches these windows; the
    # per-unit ``build_grammar_bundle`` worker is separate from the
    # analysis-window path.
    plan_id = await _insert_grammar_analysis_plan(
        worker_loop_env,
        record_id=article.record_id,
        base_id=article.base_id,
    )
    await _insert_analysis_window(
        worker_loop_env,
        plan_id=plan_id,
        window_index=0,
        status="pending",
    )
    await _insert_analysis_window(
        worker_loop_env,
        plan_id=plan_id,
        window_index=1,
        status="running",
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
    # The record is picked because ``tracked_job_count = 0`` (no
    # enhancement jobs yet). The pipeline runner bootstraps + drains all
    # enhancement jobs to success in this single call, then returns
    # ``all_workers_no_job``.
    candidate = await _find_candidate(service, article.record_id)

    result = await service.process_candidate(
        candidate=candidate,
        lease_owner_prefix="worker-loop-stuck-windows",
        max_ticks=24,
        max_jobs=24,
    )

    assert result.outcome == "processed"
    assert result.pipeline_summary is not None
    assert result.pipeline_summary.stopped_reason == "all_workers_no_job"
    assert result.completion_finalization_result is not None
    assert result.completion_finalization_result.finalized is True
    assert (
        result.completion_finalization_result.outcome
        == "completed_with_failures"
    )
    assert result.completion_finalization_result.force_failed_window_count == 2

    # Durable state: readiness advanced to coverage_complete.
    assert await _load_readiness_state(
        worker_loop_env, article.record_id
    ) == COMPLETION_TARGET_READINESS_STATE

    # Durable state: both stuck windows are now ``failed`` with
    # finalizer-attributed diagnostics in coverage.
    window_rows = await _load_analysis_window_statuses(
        worker_loop_env,
        record_id=article.record_id,
    )
    assert len(window_rows) == 2
    for _window_id, status, coverage in window_rows:
        assert status == "failed"
        diagnostics = coverage.get("diagnostics", {})
        assert diagnostics.get("failure_code") == "finalizer_forced_window_failure"
        assert diagnostics.get("forced_by") == "completion_finalizer"

    # A ``record_state_changed`` (readiness_state) event was emitted by
    # the finalizer, distinct from any pipeline-level events.
    events = await _poll_events_after(
        worker_loop_env,
        record_id=article.record_id,
        user_id=user_id,
        after_sequence=article.article_ready_sequence,
    )
    completion_events = [
        event
        for event in events
        if event.event_type == RECORD_COMPLETION_FINALIZED_EVENT_TYPE
        and event.payload_json.get("field") == "readiness_state"
    ]
    assert len(completion_events) == 1
    assert completion_events[0].payload_json["completion_outcome"] == (
        "completed_with_failures"
    )
    assert completion_events[0].payload_json["force_failed_window_count"] == 2

    # Record must no longer be in the scan candidate pool — it advanced
    # past the eligible readiness states.
    candidates_after = await service.scan_eligible_records(batch_size=20)
    assert article.record_id not in {c.record_id for c in candidates_after}


async def test_retry_later_records_are_not_hot_looped_until_available(
    worker_loop_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(worker_loop_env)
    article = await submit_article_ready(
        worker_loop_env,
        user_id=user_id,
        title="Retry Later",
    )
    runner = _make_runner(
        worker_loop_env,
        translator=_RetryLaterTranslator(),
        batch_translator=_RetryLaterBatchTranslator(),
    )
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
    assert await _load_product_state(worker_loop_env, article.record_id) == "readable_enhancing"
    events_after_retry = await _poll_events_after(
        worker_loop_env,
        record_id=article.record_id,
        user_id=user_id,
        after_sequence=article.article_ready_sequence,
    )
    assert [
        event
        for event in events_after_retry
        if event.event_type != "record_state_changed"
    ] == []

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
        # T1.1: 短文走 batch 路径，需要用 batch unconfigured executor
        # 触发 fail-closed
        batch_vocabulary_executor=_UnconfiguredVocabularyBatchExecutor(),
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
    # T1.1: 短文走 batch 路径，fail-closed 来自 vocabulary_batch worker
    assert result.pipeline_summary.stopped_worker_type == "vocabulary_batch"
    assert result.pipeline_summary.stopped_outcome == "failed_terminal"
    assert result.pipeline_summary.attention_code == "vocabulary_executor_unconfigured"
    # T1.1: 短文走 batch 路径，batch publisher 按单元拆分发布 translation layers。
    # 默认 fixture 文本有 2 段 → 2 个 translation layer。
    assert await _count_layers(worker_loop_env, article.record_id, "translation") >= 1
    assert await _count_layers(worker_loop_env, article.record_id, "vocabulary") == 0
    assert await _load_product_state(worker_loop_env, article.record_id) == "failed"
    assert (
        await _load_snapshot_product_state(
            worker_loop_env,
            record_id=article.record_id,
            user_id=user_id,
        )
        == "failed"
    )
    events = await _poll_events_after(
        worker_loop_env,
        record_id=article.record_id,
        user_id=user_id,
        after_sequence=article.article_ready_sequence,
    )
    product_state_events = [
        event for event in events if event.event_type == "record_product_state_updated"
    ]
    assert len(product_state_events) == 1
    event = product_state_events[0]
    assert event.event_type == "record_product_state_updated"
    assert event.payload_json == {
        "product_state": "failed",
        "reason_code": "vocabulary_executor_unconfigured",
        "user_visible": False,
        "attention_code": "vocabulary_executor_unconfigured",
        "stopped_reason": "attention_required",
        "stopped_outcome": "failed_terminal",
    }


# ---------------------------------------------------------------------------
# T2: article_rag_index_build must NOT block enhancement pipeline bootstrap.
#
# The candidate scan in worker_loop.py counts only enhancement job types
# (ENHANCEMENT_PIPELINE_JOB_TYPES) when deciding tracked_job_count /
# runnable_job_count. A record whose only job is article_rag_index_build
# (any status) must still appear in the candidate set so the pipeline
# runner can bootstrap display_title / translation / vocabulary / grammar.
# ---------------------------------------------------------------------------


async def _insert_article_rag_index_build_job(
    pool: asyncpg.Pool,
    *,
    record_id: UUID,
    user_id: UUID,
    base_id: UUID,
    status: str = "succeeded",
    expected_generation: int = 1,
) -> UUID:
    """Insert a reader_runs + reader_jobs row for an article_rag_index_build
    job with the given status. Mirrors what ArticleRagIndexBootstrapService
    would produce, but without the full plan/index-run machinery — we only
    need the job row to be present in the table for the scan query's
    LEFT JOIN to see it.

    Note: ``reader_runs.status`` and ``reader_jobs.status`` have different
    CHECK constraints. ``reader_runs.status`` uses ``completed`` (not
    ``succeeded``), while ``reader_jobs.status`` uses ``succeeded``. We map
    the caller-friendly ``status`` argument to the correct value for each
    table.
    """
    # Map reader_jobs status to reader_runs status.
    run_status_map = {
        "succeeded": "completed",
        "queued": "queued",
        "claimed": "running",
        "failed_terminal": "failed_terminal",
    }
    run_status = run_status_map.get(status, status)

    async with pool.acquire() as conn:
        async with conn.transaction():
            run_id = await conn.fetchval(
                """
                INSERT INTO reader_runs (
                    reading_record_id,
                    user_id,
                    run_type,
                    status,
                    record_generation,
                    envelope_json,
                    policy_version,
                    trigger_kind
                )
                VALUES (
                    $1, $2, 'article_rag_index_build', $3, $4,
                    '{}'::jsonb, 'article_rag_index_bootstrap_v1', 'system'
                )
                RETURNING id
                """,
                record_id,
                user_id,
                run_status,
                expected_generation,
            )
            job_id = await conn.fetchval(
                """
                INSERT INTO reader_jobs (
                    reading_record_id,
                    base_id,
                    run_id,
                    user_id,
                    job_type,
                    target_type,
                    target_key,
                    status,
                    expected_generation,
                    operation_fingerprint,
                    idempotency_key
                )
                VALUES (
                    $1, $2, $3, $4, 'article_rag_index_build',
                    'record', $5, $6, $7,
                    'article_rag_index_build_v1', 'rag_index_build_1'
                )
                RETURNING id
                """,
                record_id,
                base_id,
                run_id,
                user_id,
                str(record_id),
                status,
                expected_generation,
            )
            return job_id


async def _count_jobs_by_type(
    pool: asyncpg.Pool,
    record_id: UUID,
    job_type: str,
) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = $2
            """,
            record_id,
            job_type,
        )


async def test_scan_selects_record_with_only_succeeded_rag_job(
    worker_loop_env: asyncpg.Pool,
) -> None:
    """A record whose only job is a succeeded article_rag_index_build must
    still appear in the candidate set — the RAG job must not count as
    tracked enhancement work.
    """
    service = ReaderEnhancementWorkerLoopService(pool=worker_loop_env)
    user_id = await insert_user(worker_loop_env)
    article = await submit_article_ready(
        worker_loop_env,
        user_id=user_id,
        title="RAG Only Succeeded",
    )

    # Insert a succeeded RAG job — this is the scenario the bug produced:
    # article_ready flow creates the RAG job, it succeeds, and then the
    # record was stuck because the old scan saw tracked_job_count=1.
    await _insert_article_rag_index_build_job(
        worker_loop_env,
        record_id=article.record_id,
        user_id=user_id,
        base_id=article.base_id,
        status="succeeded",
    )

    candidates = await service.scan_eligible_records(batch_size=20)
    candidate_ids = {candidate.record_id for candidate in candidates}

    assert article.record_id in candidate_ids, (
        "record with only a succeeded article_rag_index_build job must "
        "still be selected for enhancement bootstrap"
    )


async def test_scan_selects_record_with_queued_rag_job(
    worker_loop_env: asyncpg.Pool,
) -> None:
    """A record whose only job is a queued/running article_rag_index_build
    must also not be blocked from enhancement bootstrap.
    """
    service = ReaderEnhancementWorkerLoopService(pool=worker_loop_env)
    user_id = await insert_user(worker_loop_env)
    article = await submit_article_ready(
        worker_loop_env,
        user_id=user_id,
        title="RAG Queued",
    )

    await _insert_article_rag_index_build_job(
        worker_loop_env,
        record_id=article.record_id,
        user_id=user_id,
        base_id=article.base_id,
        status="queued",
    )

    candidates = await service.scan_eligible_records(batch_size=20)
    candidate_ids = {candidate.record_id for candidate in candidates}

    assert article.record_id in candidate_ids, (
        "record with only a queued article_rag_index_build job must "
        "still be selected for enhancement bootstrap"
    )


async def test_pipeline_bootstraps_enhancement_jobs_when_only_rag_job_exists(
    worker_loop_env: asyncpg.Pool,
) -> None:
    """End-to-end: a record with a succeeded RAG job enters the pipeline
    and bootstraps display_title / translation / vocabulary / grammar jobs.
    """
    user_id = await insert_user(worker_loop_env)
    article = await submit_article_ready(
        worker_loop_env,
        user_id=user_id,
        title="RAG Then Enhancement",
    )

    await _insert_article_rag_index_build_job(
        worker_loop_env,
        record_id=article.record_id,
        user_id=user_id,
        base_id=article.base_id,
        status="succeeded",
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

    candidate = await _find_candidate(service, article.record_id)
    result = await service.process_candidate(
        candidate=candidate,
        lease_owner_prefix="worker-loop-rag-test",
        max_ticks=24,
        max_jobs=24,
    )

    assert result.outcome == "processed"
    assert result.pipeline_summary is not None

    # The RAG job is still there (not consumed by the enhancement pipeline).
    assert await _count_jobs_by_type(
        worker_loop_env, article.record_id, "article_rag_index_build"
    ) == 1

    # Enhancement jobs were bootstrapped and executed. The short article text
    # uses the batch path (translate_article / build_vocabulary_layer_article)
    # because the default fixture text is well under 6000 chars.
    assert await _count_jobs_by_type(
        worker_loop_env, article.record_id, "generate_display_title_zh"
    ) >= 1
    assert (
        await _count_jobs_by_type(
            worker_loop_env,
            article.record_id,
            "translate_article",
        )
        + await _count_jobs_by_type(
            worker_loop_env,
            article.record_id,
            "translate_unit",
        )
        >= 1
    )
    assert (
        await _count_jobs_by_type(
            worker_loop_env,
            article.record_id,
            "build_vocabulary_layer_article",
        )
        + await _count_jobs_by_type(
            worker_loop_env,
            article.record_id,
            "build_vocabulary_layer",
        )
        >= 1
    )

    # Enhancement layers were published.
    assert await _count_layers(worker_loop_env, article.record_id, "translation") >= 1
    assert await _count_layers(worker_loop_env, article.record_id, "vocabulary") >= 1


async def test_scan_excludes_record_with_completed_enhancement_and_rag_jobs(
    worker_loop_env: asyncpg.Pool,
) -> None:
    """A record that already has enhancement jobs (all succeeded, no
    runnable) AND a RAG job should NOT be re-selected — the enhancement
    pipeline has no work to do. This verifies the fix does not cause
    redundant empty-cycling on records that are genuinely done.
    """
    service = ReaderEnhancementWorkerLoopService(pool=worker_loop_env)
    user_id = await insert_user(worker_loop_env)
    article = await submit_article_ready(
        worker_loop_env,
        user_id=user_id,
        title="Enhancement Done",
    )

    # Bootstrap enhancement jobs, then mark them all succeeded so there are
    # no runnable jobs.
    await EnhancementJobBootstrapService(pool=worker_loop_env).bootstrap_missing_jobs(
        record_id=article.record_id,
        user_id=user_id,
    )
    async with worker_loop_env.acquire() as conn:
        await conn.execute(
            """
            UPDATE reader_jobs
            SET status = 'succeeded'
            WHERE reading_record_id = $1
              AND job_type != 'article_rag_index_build'
            """,
            article.record_id,
        )

    # Also insert a succeeded RAG job.
    await _insert_article_rag_index_build_job(
        worker_loop_env,
        record_id=article.record_id,
        user_id=user_id,
        base_id=article.base_id,
        status="succeeded",
    )

    candidates = await service.scan_eligible_records(batch_size=20)
    candidate_ids = {candidate.record_id for candidate in candidates}

    assert article.record_id not in candidate_ids, (
        "record with all enhancement jobs succeeded (no runnable) should "
        "not be re-selected even if a RAG job also exists"
    )


async def test_worker_loop_maps_user_actionable_terminal_failure_to_action_required(
    worker_loop_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(worker_loop_env)
    article = await submit_article_ready(
        worker_loop_env,
        user_id=user_id,
        title="Action Required",
    )
    summary = ReaderPipelineRunSummary(
        record_id=article.record_id,
        base_id=article.base_id,
        expected_generation=1,
        bootstrap=EnhancementBootstrapSummary(
            record_id=article.record_id,
            base_id=article.base_id,
            expected_generation=1,
            last_event_sequence=article.article_ready_sequence,
            job_counts=EnhancementBootstrapJobCounts(),
        ),
        bootstrapped_job_counts=EnhancementBootstrapJobCounts(),
        worker_tick_counts=EnhancementWorkerTickCounts(vocabulary=1),
        outcome_counts=EnhancementOutcomeCounts(failed_terminal=1),
        total_ticks=1,
        total_jobs=1,
        last_event_sequence=article.article_ready_sequence,
        snapshot_reload_recommended=False,
        stopped_reason="attention_required",
        stopped_worker_type="vocabulary",
        stopped_outcome="failed_terminal",
        attention_code="reader_user_confirmation_required",
    )
    runner = _StaticSummaryRunner(summary)
    service = ReaderEnhancementWorkerLoopService(
        pool=worker_loop_env,
        pipeline_runner=runner,  # type: ignore[arg-type]
    )
    candidate = await _find_candidate(service, article.record_id)

    result = await service.process_candidate(
        candidate=candidate,
        lease_owner_prefix="worker-loop-action-required",
        max_ticks=6,
        max_jobs=6,
    )

    assert result.outcome == "processed"
    assert result.pipeline_summary is not None
    assert result.pipeline_summary.attention_code == "reader_user_confirmation_required"
    assert await _load_product_state(worker_loop_env, article.record_id) == "action_required"
    assert (
        await _load_snapshot_product_state(
            worker_loop_env,
            record_id=article.record_id,
            user_id=user_id,
        )
        == "action_required"
    )
    events = await _poll_events_after(
        worker_loop_env,
        record_id=article.record_id,
        user_id=user_id,
        after_sequence=article.article_ready_sequence,
    )
    assert len(events) == 1
    event = events[0]
    assert event.event_type == "record_product_state_updated"
    assert event.payload_json == {
        "product_state": "action_required",
        "reason_code": "reader_user_confirmation_required",
        "user_visible": True,
        "attention_code": "reader_user_confirmation_required",
        "stopped_reason": "attention_required",
        "stopped_outcome": "failed_terminal",
    }


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
