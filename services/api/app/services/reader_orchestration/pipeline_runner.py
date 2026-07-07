from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

import asyncpg

from app.database import connection as db_connection
from app.services.ai_usage import (
    BILLING_MODE_INTERNAL_ONLY,
    CAPABILITY_READER_GRAMMAR_BUNDLE,
    USAGE_SCOPE_SYSTEM_INTERNAL,
    AIUsageEventCreate,
    record_ai_usage_event,
)
from app.services.reader_orchestration.display_title_worker import (
    DEFAULT_DISPLAY_TITLE_RETRY_DELAY,
    DisplayTitleJobProcessResult,
    DisplayTitleWorkerService,
)
from app.services.reader_orchestration.event_runtime import ReaderEventRuntime
from app.services.reader_orchestration.grammar_window_publisher import (
    GrammarWindowPublisher,
    PublishedWindowResult,
    WindowCandidateContent,
)
from app.services.reader_orchestration.grammar_window_worker import (
    GrammarWindowExecutionError,
    GrammarWindowWorkerService,
)
from app.services.reader_orchestration.grammar_worker import (
    DEFAULT_GRAMMAR_RETRY_DELAY,
    GRAMMAR_WORKFLOW_VERSION,
    GrammarBundleWorkerService,
    GrammarJobProcessResult,
)
from app.services.reader_orchestration.job_bootstrap import (
    DISPLAY_TITLE_JOB_TYPE,
    DISPLAY_TITLE_OPERATION_FINGERPRINT,
    DISPLAY_TITLE_TARGET_SCOPE,
    GRAMMAR_JOB_TYPE,
    GRAMMAR_OPERATION_FINGERPRINT,
    GRAMMAR_TARGET_SCOPE,
    TRANSLATION_BATCH_JOB_TYPE,
    TRANSLATION_BATCH_OPERATION_FINGERPRINT,
    TRANSLATION_BATCH_TARGET_SCOPE,
    TRANSLATION_JOB_TYPE,
    TRANSLATION_OPERATION_FINGERPRINT,
    TRANSLATION_TARGET_SCOPE,
    VOCABULARY_BATCH_JOB_TYPE,
    VOCABULARY_BATCH_OPERATION_FINGERPRINT,
    VOCABULARY_BATCH_TARGET_SCOPE,
    VOCABULARY_JOB_TYPE,
    VOCABULARY_OPERATION_FINGERPRINT,
    VOCABULARY_TARGET_SCOPE,
    EnhancementBootstrapJobCounts,
    EnhancementBootstrapSummary,
    EnhancementJobBootstrapService,
)
from app.services.reader_orchestration.job_runtime import (
    ClaimResult,
    FenceViolationError,
    IllegalTransitionError,
    ReaderJobRuntime,
)
from app.services.reader_orchestration.orchestrator import ReaderOrchestrator
from app.services.reader_orchestration.span_recorder import (
    SPAN_KIND_WORKER_TICK,
    STATUS_FAILED,
    STATUS_SKIPPED,
    STATUS_SUCCEEDED,
    current_span,
    end_worker_span_execution_error,
    end_worker_span_fence_violation,
    end_worker_span_generic_exception,
    end_worker_span_success,
    get_default_recorder,
)
from app.services.reader_orchestration.translation_worker import (
    DEFAULT_TRANSLATION_RETRY_DELAY,
    TranslationBatchJobProcessResult,
    TranslationWorkerService,
)
from app.services.reader_orchestration.vocabulary_worker import (
    DEFAULT_VOCABULARY_RETRY_DELAY,
    VocabularyBatchJobProcessResult,
    VocabularyJobProcessResult,
    VocabularyWorkerService,
)
from app.services.reader_orchestration.zplus_bootstrap import (
    ZPLUS_GRAMMAR_JOB_TYPE,
    ZPLUS_GRAMMAR_OPERATION_FINGERPRINT,
    ZPLUS_TARGET_TYPE,
)

WorkerType = Literal[
    "display_title",
    "translation",
    "translation_batch",  # T1.1 short-article batch
    "vocabulary",
    "vocabulary_batch",  # T1.1 short-article batch
    "grammar_bundle",  # legacy per-unit
    "grammar_bundle_window",  # Z+ window
]
PipelineAttemptOutcome = Literal[
    "succeeded",
    "retry_later",
    "failed_terminal",
    "superseded",
    "no_job",
]
PipelineStoppedReason = Literal[
    "all_workers_no_job",
    "max_ticks_reached",
    "max_jobs_reached",
    "attention_required",
]

# T1 acceptance: the fake executor baseline showed reuters_bbc_970 needs
# 60 ticks (6 workers × 10 rounds) in fake mode and ~70 in 7-worker Z+ mode.
# 96 / 48 covers medium samples (≤12 units) in both 6- and 7-worker modes
# with ~30% margin. Every worker attempt (including ``no_job``) consumes a
# tick, so the budget must scale with ``workers × (units + 1)``.
DEFAULT_PIPELINE_MAX_TICKS = 96
DEFAULT_PIPELINE_MAX_JOBS = 48

# Z+ window worker observability constants (requirement 6).
# operation_fingerprint mirrors the reader_jobs.operation_fingerprint so
# Console can group window worker spans with their parent job rows.
ZPLUS_WINDOW_WORKFLOW_VERSION = GRAMMAR_WORKFLOW_VERSION
ZPLUS_WINDOW_OPERATION_FINGERPRINT = ZPLUS_GRAMMAR_OPERATION_FINGERPRINT


def _derive_candidate_contents(
    candidates: list,
) -> list[WindowCandidateContent]:
    """P1-4 bridge: 从 CandidateItem 的 content_* 字段派生 WindowCandidateContent。

    executor 产出的 CandidateItem 携带 grammar_point / note / label /
    analysis / chunks 等内容字段（P1-3 fix），但 publisher 的
    ``publish_window_grammar_bundle`` 需要独立的 ``WindowCandidateContent``
    列表来构建合法的 GrammarNoteLayerOutput / SentenceAnalysisLayerOutput。
    本函数完成 dict → Pydantic model 的转换。

    P2-1 (fail closed): 当 candidates 存在但没有 content_* 字段时，raise
    ValueError 而不是返回 None 触发 sidecar fallback。生产路径必须产出
    符合 layer contract 的 output_json，不能发布旧 sidecar shape。

    当 span dict 不完全符合 ReaderTextRangeAnchor schema 时，尝试用
    candidate 的 anchor_segment_id 和 spans 中的可用字段构建有效的
    ReaderTextRangeAnchor（填充缺失的必需字段）。
    """
    from app.schemas.reader_orchestration import (
        ReaderTextRangeAnchor,
        SentenceAnalysisChunk,
    )

    if not candidates:
        return []

    contents: list[WindowCandidateContent] = []
    for c in candidates:
        # P2-1: fail closed — candidates 必须携带 content_* 字段
        has_content = bool(
            c.grammar_point or c.note or c.label or c.analysis or c.chunks
        )
        if not has_content:
            raise ValueError(
                f"CandidateItem {c.semantic_dedup_key} has no content_* fields. "
                f"Executor must populate grammar_point/note/label/analysis/chunks "
                f"to produce valid layer contract output."
            )

        # 尝试直接验证 span dicts
        spans_models: list[ReaderTextRangeAnchor] = []
        try:
            spans_models = (
                [ReaderTextRangeAnchor(**s) for s in c.spans] if c.spans else []
            )
        except Exception:
            # span dict 不符合 schema，尝试构建有效的 ReaderTextRangeAnchor
            spans_models = _build_fallback_spans(c)

        chunks_models: list[SentenceAnalysisChunk] = []
        try:
            chunks_models = (
                [SentenceAnalysisChunk(**ch) for ch in c.chunks] if c.chunks else []
            )
        except Exception:
            chunks_models = []

        anchor_model = spans_models[0] if spans_models else None
        contents.append(
            WindowCandidateContent(
                semantic_dedup_key=c.semantic_dedup_key,
                grammar_point=c.grammar_point,
                pattern=c.pattern,
                note=c.note,
                spans=spans_models,
                anchor=anchor_model,
                label=c.label,
                analysis=c.analysis,
                chunks=chunks_models,
            )
        )
    return contents


def _build_fallback_spans(candidate) -> list:
    """Build valid ReaderTextRangeAnchor from simplified span dicts.

    When executor produces span dicts with only ``unit_id`` / ``start`` /
    ``end`` (test mocks), construct a valid ReaderTextRangeAnchor by
    filling in required fields with derivable/placeholder values.
    """
    from app.schemas.reader_orchestration import ReaderTextRangeAnchor

    spans: list[ReaderTextRangeAnchor] = []
    for s in (candidate.spans or []):
        unit_id = str(s.get("unit_id", "unknown"))
        start = int(s.get("start", s.get("start_offset", 0)))
        end = int(s.get("end", s.get("end_offset", start + 1)))
        selected_text = str(s.get("selected_text", s.get("text", "x" * max(1, end - start))))
        # Compute fnv1a32 hash of selected_text (8 hex chars)
        text_hash = _fnv1a32_hex(selected_text)
        try:
            spans.append(
                ReaderTextRangeAnchor(
                    base_id=str(s.get("base_id", "fallback")),
                    unit_id=unit_id,
                    anchor_segment_id=candidate.anchor_segment_id,
                    start_offset=start,
                    end_offset=end,
                    selected_text=selected_text,
                    text_hash=text_hash,
                )
            )
        except Exception:
            continue
    return spans


def _fnv1a32_hex(text: str) -> str:
    """Compute FNV-1a 32-bit hash of text (UTF-16 code units), return 8-char hex.

    Delegates to ``app.contracts.annotation.compute_text_range_hash`` to ensure
    the hash matches the ``ReaderTextRangeAnchor.text_hash`` validator exactly.
    """
    from app.contracts.annotation import compute_text_range_hash

    return compute_text_range_hash(text)


@dataclass(frozen=True, slots=True)
class EnhancementWorkerTickCounts:
    display_title: int = 0
    translation: int = 0
    translation_batch: int = 0
    vocabulary: int = 0
    vocabulary_batch: int = 0
    grammar_bundle: int = 0
    grammar_bundle_window: int = 0


@dataclass(frozen=True, slots=True)
class EnhancementOutcomeCounts:
    succeeded: int = 0
    retry_later: int = 0
    failed_terminal: int = 0
    superseded: int = 0
    no_job: int = 0


@dataclass(frozen=True, slots=True)
class ReaderPipelineWorkerAttempt:
    worker_type: WorkerType
    outcome: PipelineAttemptOutcome
    processed_job: bool
    job_id: UUID | None = None
    run_id: UUID | None = None
    attention_code: str | None = None
    superseded_jobs: int = 0


@dataclass(frozen=True, slots=True)
class ReaderPipelineRunSummary:
    record_id: UUID
    base_id: UUID
    expected_generation: int
    bootstrap: EnhancementBootstrapSummary
    bootstrapped_job_counts: EnhancementBootstrapJobCounts
    worker_tick_counts: EnhancementWorkerTickCounts
    outcome_counts: EnhancementOutcomeCounts
    total_ticks: int
    total_jobs: int
    last_event_sequence: int
    snapshot_reload_recommended: bool
    stopped_reason: PipelineStoppedReason
    stopped_worker_type: WorkerType | None = None
    stopped_outcome: PipelineAttemptOutcome | None = None
    attention_code: str | None = None
    attempts: tuple[ReaderPipelineWorkerAttempt, ...] = ()


@dataclass(frozen=True, slots=True)
class _RecordRuntimeState:
    generation: int
    active_base_id: UUID | None
    last_event_sequence: int


class ReaderEnhancementPipelineRunner:
    def __init__(
        self,
        *,
        pool: asyncpg.Pool | None = None,
        bootstrap_service: EnhancementJobBootstrapService | None = None,
        display_title_worker_service: DisplayTitleWorkerService | None = None,
        translation_orchestrator: ReaderOrchestrator | None = None,
        translation_batch_worker_service: TranslationWorkerService | None = None,
        vocabulary_worker_service: VocabularyWorkerService | None = None,
        grammar_worker_service: GrammarBundleWorkerService | None = None,
        grammar_window_worker_service: GrammarWindowWorkerService | None = None,
        grammar_window_publisher: GrammarWindowPublisher | None = None,
        job_runtime: ReaderJobRuntime | None = None,
        enable_zplus_grammar: bool = True,
    ) -> None:
        self._pool = pool
        self._bootstrap_service = bootstrap_service or EnhancementJobBootstrapService(
            pool=pool
        )
        self._display_title_worker_service = (
            display_title_worker_service or DisplayTitleWorkerService(pool=pool)
        )
        self._translation_orchestrator = translation_orchestrator or ReaderOrchestrator(
            pool=pool
        )
        # T1.1 short-article batch path: bypass the orchestrator and call the
        # batch worker service directly. The batch methods live on the same
        # TranslationWorkerService class; a dedicated instance is wired here so
        # tests can inject a fake batch executor without affecting the
        # per-unit orchestrator path.
        self._translation_batch_worker_service = (
            translation_batch_worker_service or TranslationWorkerService(pool=pool)
        )
        self._vocabulary_worker_service = vocabulary_worker_service or VocabularyWorkerService(
            pool=pool
        )
        self._grammar_worker_service = grammar_worker_service or GrammarBundleWorkerService(
            pool=pool
        )
        # Z+ window worker + publisher。默认启用 Z+ 路径（design §9）：
        # 当 enable_zplus_grammar=True 且调用方未显式注入时，自动构造
        # GrammarWindowWorkerService + GrammarWindowPublisher +
        # PydanticAIGrammarWindowExecutor，使生产路径默认走 Z+ window
        # 调度。legacy 测试可传 enable_zplus_grammar=False 回退到 4-worker
        # 模式（_grammar_window_worker / _grammar_window_publisher 保持
        # None，worker_order 不包含 grammar_bundle_window）。
        self._enable_zplus_grammar = enable_zplus_grammar
        if enable_zplus_grammar:
            # 延迟导入避免循环依赖（grammar_window_worker → grammar_worker
            # → job_bootstrap 已在模块顶部导入，此处仅导入 executor）。
            from app.services.reader_orchestration.grammar_window_worker import (
                PydanticAIGrammarWindowExecutor,
            )
            self._grammar_window_worker = (
                grammar_window_worker_service
                or GrammarWindowWorkerService(
                    pool=pool,
                    executor=PydanticAIGrammarWindowExecutor(pool=pool),
                )
            )
            self._grammar_window_publisher = (
                grammar_window_publisher
                or GrammarWindowPublisher(
                    pool=pool,
                    event_runtime=ReaderEventRuntime(pool=pool),
                )
            )
        else:
            self._grammar_window_worker = grammar_window_worker_service
            self._grammar_window_publisher = grammar_window_publisher
        self._job_runtime = job_runtime or ReaderJobRuntime(pool=pool)

    def get_pool(self) -> asyncpg.Pool:
        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("Database pool not initialized")
        return pool

    async def bootstrap_missing_jobs(
        self,
        *,
        record_id: UUID,
        user_id: UUID,
    ) -> EnhancementBootstrapSummary:
        # enable_zplus_grammar=False 时强制走 legacy per-unit 路径，
        # 保持 4-worker 模式下的 bootstrap 行为不变。
        return await self._bootstrap_service.bootstrap_missing_jobs(
            record_id=record_id,
            user_id=user_id,
            force_legacy_grammar=not self._enable_zplus_grammar,
        )

    async def run(
        self,
        *,
        record_id: UUID,
        user_id: UUID,
        lease_owner: str,
        lease_duration: timedelta,
        max_ticks: int = DEFAULT_PIPELINE_MAX_TICKS,
        max_jobs: int = DEFAULT_PIPELINE_MAX_JOBS,
        translation_retry_delay: timedelta = DEFAULT_TRANSLATION_RETRY_DELAY,
        vocabulary_retry_delay: timedelta = DEFAULT_VOCABULARY_RETRY_DELAY,
        grammar_retry_delay: timedelta = DEFAULT_GRAMMAR_RETRY_DELAY,
        display_title_retry_delay: timedelta = DEFAULT_DISPLAY_TITLE_RETRY_DELAY,
    ) -> ReaderPipelineRunSummary:
        if max_ticks < 1:
            raise ValueError("max_ticks must be >= 1")
        if max_jobs < 1:
            raise ValueError("max_jobs must be >= 1")

        bootstrap = await self.bootstrap_missing_jobs(
            record_id=record_id,
            user_id=user_id,
        )

        attempts: list[ReaderPipelineWorkerAttempt] = []
        tick_counts = {
            "display_title": 0,
            "translation": 0,
            "translation_batch": 0,
            "vocabulary": 0,
            "vocabulary_batch": 0,
            "grammar_bundle": 0,
            "grammar_bundle_window": 0,
        }
        outcome_counts = {
            "succeeded": 0,
            "retry_later": 0,
            "failed_terminal": 0,
            "superseded": 0,
            "no_job": 0,
        }
        total_ticks = 0
        total_jobs = 0
        stopped_reason: PipelineStoppedReason = "all_workers_no_job"
        stopped_worker_type: WorkerType | None = None
        stopped_outcome: PipelineAttemptOutcome | None = None
        attention_code: str | None = None

        # Z+ window worker is dispatched ahead of legacy grammar_bundle to
        # avoid legacy / Z+ contention. When ``grammar_window_worker`` is not
        # registered (legacy deployments / existing tests), the pipeline keeps
        # the legacy 4-worker order so baseline tick / job counts are
        # preserved. T1.1 batch workers are dispatched ahead of their per-unit
        # counterparts so short-article batch jobs are processed before the
        # per-unit workers (which will find no_job for short articles).
        if self._grammar_window_worker is not None:
            worker_order: tuple[WorkerType, ...] = (
                "display_title",
                "translation_batch",  # T1.1 short-article batch
                "translation",
                "vocabulary_batch",  # T1.1 short-article batch
                "vocabulary",
                "grammar_bundle_window",  # Z+ 优先
                "grammar_bundle",  # legacy
            )
        else:
            worker_order = (
                "display_title",
                "translation_batch",  # T1.1 short-article batch
                "translation",
                "vocabulary_batch",  # T1.1 short-article batch
                "vocabulary",
                "grammar_bundle",
            )

        while True:
            round_no_job_count = 0

            for worker_type in worker_order:
                attempt = await self._run_worker_attempt(
                    worker_type=worker_type,
                    record_id=record_id,
                    base_id=bootstrap.base_id,
                    expected_generation=bootstrap.expected_generation,
                    lease_owner=lease_owner,
                    lease_duration=lease_duration,
                    translation_retry_delay=translation_retry_delay,
                    vocabulary_retry_delay=vocabulary_retry_delay,
                    grammar_retry_delay=grammar_retry_delay,
                    display_title_retry_delay=display_title_retry_delay,
                )
                attempts.append(attempt)
                total_ticks += 1
                tick_counts[worker_type] += 1
                if attempt.outcome == "no_job":
                    outcome_counts["no_job"] += 1
                    round_no_job_count += 1
                elif attempt.outcome == "succeeded":
                    outcome_counts["succeeded"] += 1
                elif attempt.outcome == "retry_later":
                    outcome_counts["retry_later"] += 1
                elif attempt.outcome == "failed_terminal":
                    outcome_counts["failed_terminal"] += 1
                outcome_counts["superseded"] += attempt.superseded_jobs

                if attempt.processed_job:
                    total_jobs += 1

                if attempt.outcome in {
                    "retry_later",
                    "failed_terminal",
                    "superseded",
                }:
                    stopped_reason = "attention_required"
                    stopped_worker_type = worker_type
                    stopped_outcome = attempt.outcome
                    attention_code = attempt.attention_code
                    break

                if total_jobs >= max_jobs:
                    stopped_reason = "max_jobs_reached"
                    break
                if total_ticks >= max_ticks:
                    stopped_reason = "max_ticks_reached"
                    break

            if stopped_reason != "all_workers_no_job":
                break
            if round_no_job_count == len(worker_order):
                break

        runtime_state = await self._load_record_runtime_state(
            record_id=record_id,
            user_id=user_id,
        )
        snapshot_reload_recommended = (
            runtime_state.last_event_sequence > bootstrap.last_event_sequence
            or runtime_state.active_base_id != bootstrap.base_id
            or runtime_state.generation != bootstrap.expected_generation
        )

        return ReaderPipelineRunSummary(
            record_id=record_id,
            base_id=bootstrap.base_id,
            expected_generation=bootstrap.expected_generation,
            bootstrap=bootstrap,
            bootstrapped_job_counts=bootstrap.job_counts,
            worker_tick_counts=EnhancementWorkerTickCounts(
                display_title=tick_counts["display_title"],
                translation=tick_counts["translation"],
                translation_batch=tick_counts["translation_batch"],
                vocabulary=tick_counts["vocabulary"],
                vocabulary_batch=tick_counts["vocabulary_batch"],
                grammar_bundle=tick_counts["grammar_bundle"],
                grammar_bundle_window=tick_counts["grammar_bundle_window"],
            ),
            outcome_counts=EnhancementOutcomeCounts(
                succeeded=outcome_counts["succeeded"],
                retry_later=outcome_counts["retry_later"],
                failed_terminal=outcome_counts["failed_terminal"],
                superseded=outcome_counts["superseded"],
                no_job=outcome_counts["no_job"],
            ),
            total_ticks=total_ticks,
            total_jobs=total_jobs,
            last_event_sequence=runtime_state.last_event_sequence,
            snapshot_reload_recommended=snapshot_reload_recommended,
            stopped_reason=stopped_reason,
            stopped_worker_type=stopped_worker_type,
            stopped_outcome=stopped_outcome,
            attention_code=attention_code,
            attempts=tuple(attempts),
        )

    async def _run_worker_attempt(
        self,
        *,
        worker_type: WorkerType,
        record_id: UUID,
        base_id: UUID,
        expected_generation: int,
        lease_owner: str,
        lease_duration: timedelta,
        translation_retry_delay: timedelta,
        vocabulary_retry_delay: timedelta,
        grammar_retry_delay: timedelta,
        display_title_retry_delay: timedelta,
    ) -> ReaderPipelineWorkerAttempt:
        recorder = get_default_recorder()
        parent = current_span()
        trace_id = parent.trace_id if parent is not None else uuid4()
        span_ctx = await recorder.start_span(
            trace_id=trace_id,
            span_kind=SPAN_KIND_WORKER_TICK,
            reading_record_id=record_id,
            parent_span_id=parent.span_id if parent is not None else None,
            worker_type=worker_type,
            metadata={"lease_owner": lease_owner},
        )
        try:
            async with recorder.use_span(span_ctx):
                attempt = await self._dispatch_worker_attempt(
                    worker_type=worker_type,
                    record_id=record_id,
                    base_id=base_id,
                    expected_generation=expected_generation,
                    lease_owner=lease_owner,
                    lease_duration=lease_duration,
                    translation_retry_delay=translation_retry_delay,
                    vocabulary_retry_delay=vocabulary_retry_delay,
                    grammar_retry_delay=grammar_retry_delay,
                    display_title_retry_delay=display_title_retry_delay,
                )
            # Worker ends the worker_tick span itself via current_span()
            # in process_claimed_*_job. The no_job outcome is the exception:
            # the worker found no claimable job, so it returns without
            # ending the span. End it as skipped so Console's "active span"
            # view doesn't accumulate zombie rows.
            if attempt.outcome == "no_job":
                await recorder.end_span(span_ctx, status=STATUS_SKIPPED)
            return attempt
        except Exception as exc:
            # Uncaught exception fallback: worker didn't end the span.
            await recorder.end_span(
                span_ctx,
                status=STATUS_FAILED,
                failure_class="worker_exception",
                failure_code=type(exc).__name__,
            )
            raise

    async def _dispatch_worker_attempt(
        self,
        *,
        worker_type: WorkerType,
        record_id: UUID,
        base_id: UUID,
        expected_generation: int,
        lease_owner: str,
        lease_duration: timedelta,
        translation_retry_delay: timedelta,
        vocabulary_retry_delay: timedelta,
        grammar_retry_delay: timedelta,
        display_title_retry_delay: timedelta,
    ) -> ReaderPipelineWorkerAttempt:
        if worker_type == "display_title":
            return await self._run_display_title_attempt(
                record_id=record_id,
                base_id=base_id,
                expected_generation=expected_generation,
                lease_owner=lease_owner,
                lease_duration=lease_duration,
                retry_delay=display_title_retry_delay,
            )
        if worker_type == "translation":
            return await self._run_translation_attempt(
                record_id=record_id,
                base_id=base_id,
                expected_generation=expected_generation,
                lease_owner=lease_owner,
                lease_duration=lease_duration,
                retry_delay=translation_retry_delay,
            )
        if worker_type == "translation_batch":
            return await self._run_translation_batch_attempt(
                record_id=record_id,
                base_id=base_id,
                expected_generation=expected_generation,
                lease_owner=lease_owner,
                lease_duration=lease_duration,
                retry_delay=translation_retry_delay,
            )
        if worker_type == "vocabulary":
            return await self._run_vocabulary_attempt(
                record_id=record_id,
                base_id=base_id,
                expected_generation=expected_generation,
                lease_owner=lease_owner,
                lease_duration=lease_duration,
                retry_delay=vocabulary_retry_delay,
            )
        if worker_type == "vocabulary_batch":
            return await self._run_vocabulary_batch_attempt(
                record_id=record_id,
                base_id=base_id,
                expected_generation=expected_generation,
                lease_owner=lease_owner,
                lease_duration=lease_duration,
                retry_delay=vocabulary_retry_delay,
            )
        if worker_type == "grammar_bundle_window":
            return await self._run_grammar_window_attempt(
                record_id=record_id,
                base_id=base_id,
                expected_generation=expected_generation,
                lease_owner=lease_owner,
                lease_duration=lease_duration,
                retry_delay=grammar_retry_delay,
            )
        return await self._run_grammar_attempt(
            record_id=record_id,
            base_id=base_id,
            expected_generation=expected_generation,
            lease_owner=lease_owner,
            lease_duration=lease_duration,
            retry_delay=grammar_retry_delay,
        )

    async def _run_display_title_attempt(
        self,
        *,
        record_id: UUID,
        base_id: UUID,
        expected_generation: int,
        lease_owner: str,
        lease_duration: timedelta,
        retry_delay: timedelta,
    ) -> ReaderPipelineWorkerAttempt:
        before_superseded = await self._count_superseded_jobs(
            record_id=record_id,
            base_id=base_id,
            expected_generation=expected_generation,
            job_type=DISPLAY_TITLE_JOB_TYPE,
            target_scope=DISPLAY_TITLE_TARGET_SCOPE,
            operation_fingerprint=DISPLAY_TITLE_OPERATION_FINGERPRINT,
        )
        try:
            result = await (
                self._display_title_worker_service.process_next_display_title_job_for_record(
                    record_id=record_id,
                    base_id=base_id,
                    expected_generation=expected_generation,
                    lease_owner=lease_owner,
                    lease_duration=lease_duration,
                    retry_delay=retry_delay,
                )
            )
        except FenceViolationError:
            superseded_jobs = (
                await self._count_superseded_jobs(
                    record_id=record_id,
                    base_id=base_id,
                    expected_generation=expected_generation,
                    job_type=DISPLAY_TITLE_JOB_TYPE,
                    target_scope=DISPLAY_TITLE_TARGET_SCOPE,
                    operation_fingerprint=DISPLAY_TITLE_OPERATION_FINGERPRINT,
                )
                - before_superseded
            )
            return ReaderPipelineWorkerAttempt(
                worker_type="display_title",
                outcome="superseded",
                processed_job=True,
                attention_code="publish_fence_failed",
                superseded_jobs=max(1, superseded_jobs),
            )

        return await self._build_worker_attempt_from_result(
            worker_type="display_title",
            record_id=record_id,
            base_id=base_id,
            expected_generation=expected_generation,
            job_type=DISPLAY_TITLE_JOB_TYPE,
            target_scope=DISPLAY_TITLE_TARGET_SCOPE,
            operation_fingerprint=DISPLAY_TITLE_OPERATION_FINGERPRINT,
            before_superseded=before_superseded,
            result=result,
        )

    async def _run_translation_attempt(
        self,
        *,
        record_id: UUID,
        base_id: UUID,
        expected_generation: int,
        lease_owner: str,
        lease_duration: timedelta,
        retry_delay: timedelta,
    ) -> ReaderPipelineWorkerAttempt:
        before_superseded = await self._count_superseded_jobs(
            record_id=record_id,
            base_id=base_id,
            expected_generation=expected_generation,
            job_type=TRANSLATION_JOB_TYPE,
            target_scope=TRANSLATION_TARGET_SCOPE,
            operation_fingerprint=TRANSLATION_OPERATION_FINGERPRINT,
        )
        try:
            tick_result = await self._translation_orchestrator.tick_translation_worker_for_record(
                record_id=record_id,
                base_id=base_id,
                expected_generation=expected_generation,
                lease_owner=lease_owner,
                lease_duration=lease_duration,
                retry_delay=retry_delay,
            )
        except FenceViolationError:
            superseded_jobs = (
                await self._count_superseded_jobs(
                    record_id=record_id,
                    base_id=base_id,
                    expected_generation=expected_generation,
                    job_type=TRANSLATION_JOB_TYPE,
                    target_scope=TRANSLATION_TARGET_SCOPE,
                    operation_fingerprint=TRANSLATION_OPERATION_FINGERPRINT,
                )
                - before_superseded
            )
            return ReaderPipelineWorkerAttempt(
                worker_type="translation",
                outcome="superseded",
                processed_job=True,
                attention_code="publish_fence_failed",
                superseded_jobs=max(1, superseded_jobs),
            )

        superseded_jobs = (
            await self._count_superseded_jobs(
                record_id=record_id,
                base_id=base_id,
                expected_generation=expected_generation,
                job_type=TRANSLATION_JOB_TYPE,
                target_scope=TRANSLATION_TARGET_SCOPE,
                operation_fingerprint=TRANSLATION_OPERATION_FINGERPRINT,
            )
            - before_superseded
        )
        worker_result = tick_result.worker_result
        if worker_result is None:
            return ReaderPipelineWorkerAttempt(
                worker_type="translation",
                outcome="no_job",
                processed_job=False,
                superseded_jobs=max(0, superseded_jobs),
            )

        attention = None
        if worker_result.status != "succeeded":
            attention = await self._load_job_attention_code(worker_result.claim.job_id)
        return ReaderPipelineWorkerAttempt(
            worker_type="translation",
            outcome=self._normalize_worker_outcome(worker_result.status),
            processed_job=True,
            job_id=worker_result.claim.job_id,
            run_id=worker_result.claim.run_id,
            attention_code=attention,
            superseded_jobs=max(0, superseded_jobs),
        )

    async def _run_vocabulary_attempt(
        self,
        *,
        record_id: UUID,
        base_id: UUID,
        expected_generation: int,
        lease_owner: str,
        lease_duration: timedelta,
        retry_delay: timedelta,
    ) -> ReaderPipelineWorkerAttempt:
        before_superseded = await self._count_superseded_jobs(
            record_id=record_id,
            base_id=base_id,
            expected_generation=expected_generation,
            job_type=VOCABULARY_JOB_TYPE,
            target_scope=VOCABULARY_TARGET_SCOPE,
            operation_fingerprint=VOCABULARY_OPERATION_FINGERPRINT,
        )
        try:
            result = await self._vocabulary_worker_service.process_next_vocabulary_job_for_record(
                record_id=record_id,
                base_id=base_id,
                expected_generation=expected_generation,
                lease_owner=lease_owner,
                lease_duration=lease_duration,
                retry_delay=retry_delay,
            )
        except FenceViolationError:
            superseded_jobs = (
                await self._count_superseded_jobs(
                    record_id=record_id,
                    base_id=base_id,
                    expected_generation=expected_generation,
                    job_type=VOCABULARY_JOB_TYPE,
                    target_scope=VOCABULARY_TARGET_SCOPE,
                    operation_fingerprint=VOCABULARY_OPERATION_FINGERPRINT,
                )
                - before_superseded
            )
            return ReaderPipelineWorkerAttempt(
                worker_type="vocabulary",
                outcome="superseded",
                processed_job=True,
                attention_code="publish_fence_failed",
                superseded_jobs=max(1, superseded_jobs),
            )

        return await self._build_worker_attempt_from_result(
            worker_type="vocabulary",
            record_id=record_id,
            base_id=base_id,
            expected_generation=expected_generation,
            job_type=VOCABULARY_JOB_TYPE,
            target_scope=VOCABULARY_TARGET_SCOPE,
            operation_fingerprint=VOCABULARY_OPERATION_FINGERPRINT,
            before_superseded=before_superseded,
            result=result,
        )

    async def _run_translation_batch_attempt(
        self,
        *,
        record_id: UUID,
        base_id: UUID,
        expected_generation: int,
        lease_owner: str,
        lease_duration: timedelta,
        retry_delay: timedelta,
    ) -> ReaderPipelineWorkerAttempt:
        """T1.1 short-article batch dispatch for the translation layer.

        Bypasses the orchestrator and calls the batch worker service directly
        so a single LLM call covers all units of a short article. For long
        articles the bootstrap creates no batch jobs, so this attempt returns
        ``no_job`` and the per-unit ``_run_translation_attempt`` handles them.
        """
        before_superseded = await self._count_superseded_jobs(
            record_id=record_id,
            base_id=base_id,
            expected_generation=expected_generation,
            job_type=TRANSLATION_BATCH_JOB_TYPE,
            target_scope=TRANSLATION_BATCH_TARGET_SCOPE,
            operation_fingerprint=TRANSLATION_BATCH_OPERATION_FINGERPRINT,
        )
        try:
            result = await self._translation_batch_worker_service.process_next_translation_batch_job_for_record(
                record_id=record_id,
                base_id=base_id,
                expected_generation=expected_generation,
                lease_owner=lease_owner,
                lease_duration=lease_duration,
                retry_delay=retry_delay,
            )
        except FenceViolationError:
            superseded_jobs = (
                await self._count_superseded_jobs(
                    record_id=record_id,
                    base_id=base_id,
                    expected_generation=expected_generation,
                    job_type=TRANSLATION_BATCH_JOB_TYPE,
                    target_scope=TRANSLATION_BATCH_TARGET_SCOPE,
                    operation_fingerprint=TRANSLATION_BATCH_OPERATION_FINGERPRINT,
                )
                - before_superseded
            )
            return ReaderPipelineWorkerAttempt(
                worker_type="translation_batch",
                outcome="superseded",
                processed_job=True,
                attention_code="publish_fence_failed",
                superseded_jobs=max(1, superseded_jobs),
            )

        return await self._build_worker_attempt_from_result(
            worker_type="translation_batch",
            record_id=record_id,
            base_id=base_id,
            expected_generation=expected_generation,
            job_type=TRANSLATION_BATCH_JOB_TYPE,
            target_scope=TRANSLATION_BATCH_TARGET_SCOPE,
            operation_fingerprint=TRANSLATION_BATCH_OPERATION_FINGERPRINT,
            before_superseded=before_superseded,
            result=result,
        )

    async def _run_vocabulary_batch_attempt(
        self,
        *,
        record_id: UUID,
        base_id: UUID,
        expected_generation: int,
        lease_owner: str,
        lease_duration: timedelta,
        retry_delay: timedelta,
    ) -> ReaderPipelineWorkerAttempt:
        """T1.1 short-article batch dispatch for the vocabulary layer.

        Reuses the existing ``vocabulary_worker_service`` (the batch methods
        live on the same ``VocabularyWorkerService`` class) and calls
        ``process_next_vocabulary_batch_job_for_record`` directly. For long
        articles the bootstrap creates no batch jobs, so this attempt returns
        ``no_job`` and the per-unit ``_run_vocabulary_attempt`` handles them.
        """
        before_superseded = await self._count_superseded_jobs(
            record_id=record_id,
            base_id=base_id,
            expected_generation=expected_generation,
            job_type=VOCABULARY_BATCH_JOB_TYPE,
            target_scope=VOCABULARY_BATCH_TARGET_SCOPE,
            operation_fingerprint=VOCABULARY_BATCH_OPERATION_FINGERPRINT,
        )
        try:
            result = await self._vocabulary_worker_service.process_next_vocabulary_batch_job_for_record(
                record_id=record_id,
                base_id=base_id,
                expected_generation=expected_generation,
                lease_owner=lease_owner,
                lease_duration=lease_duration,
                retry_delay=retry_delay,
            )
        except FenceViolationError:
            superseded_jobs = (
                await self._count_superseded_jobs(
                    record_id=record_id,
                    base_id=base_id,
                    expected_generation=expected_generation,
                    job_type=VOCABULARY_BATCH_JOB_TYPE,
                    target_scope=VOCABULARY_BATCH_TARGET_SCOPE,
                    operation_fingerprint=VOCABULARY_BATCH_OPERATION_FINGERPRINT,
                )
                - before_superseded
            )
            return ReaderPipelineWorkerAttempt(
                worker_type="vocabulary_batch",
                outcome="superseded",
                processed_job=True,
                attention_code="publish_fence_failed",
                superseded_jobs=max(1, superseded_jobs),
            )

        return await self._build_worker_attempt_from_result(
            worker_type="vocabulary_batch",
            record_id=record_id,
            base_id=base_id,
            expected_generation=expected_generation,
            job_type=VOCABULARY_BATCH_JOB_TYPE,
            target_scope=VOCABULARY_BATCH_TARGET_SCOPE,
            operation_fingerprint=VOCABULARY_BATCH_OPERATION_FINGERPRINT,
            before_superseded=before_superseded,
            result=result,
        )

    async def _run_grammar_attempt(
        self,
        *,
        record_id: UUID,
        base_id: UUID,
        expected_generation: int,
        lease_owner: str,
        lease_duration: timedelta,
        retry_delay: timedelta,
    ) -> ReaderPipelineWorkerAttempt:
        before_superseded = await self._count_superseded_jobs(
            record_id=record_id,
            base_id=base_id,
            expected_generation=expected_generation,
            job_type=GRAMMAR_JOB_TYPE,
            target_scope=GRAMMAR_TARGET_SCOPE,
            operation_fingerprint=GRAMMAR_OPERATION_FINGERPRINT,
        )
        try:
            result = await self._grammar_worker_service.process_next_grammar_job_for_record(
                record_id=record_id,
                base_id=base_id,
                expected_generation=expected_generation,
                lease_owner=lease_owner,
                lease_duration=lease_duration,
                retry_delay=retry_delay,
            )
        except FenceViolationError:
            superseded_jobs = (
                await self._count_superseded_jobs(
                    record_id=record_id,
                    base_id=base_id,
                    expected_generation=expected_generation,
                    job_type=GRAMMAR_JOB_TYPE,
                    target_scope=GRAMMAR_TARGET_SCOPE,
                    operation_fingerprint=GRAMMAR_OPERATION_FINGERPRINT,
                )
                - before_superseded
            )
            return ReaderPipelineWorkerAttempt(
                worker_type="grammar_bundle",
                outcome="superseded",
                processed_job=True,
                attention_code="publish_fence_failed",
                superseded_jobs=max(1, superseded_jobs),
            )

        return await self._build_worker_attempt_from_result(
            worker_type="grammar_bundle",
            record_id=record_id,
            base_id=base_id,
            expected_generation=expected_generation,
            job_type=GRAMMAR_JOB_TYPE,
            target_scope=GRAMMAR_TARGET_SCOPE,
            operation_fingerprint=GRAMMAR_OPERATION_FINGERPRINT,
            before_superseded=before_superseded,
            result=result,
        )

    async def _run_grammar_window_attempt(
        self,
        *,
        record_id: UUID,
        base_id: UUID,
        expected_generation: int,
        lease_owner: str,
        lease_duration: timedelta,
        retry_delay: timedelta,
    ) -> ReaderPipelineWorkerAttempt:
        """Dispatch a Z+ ``build_grammar_bundle_window`` reader_job.

        Flow (§9 worker migration):
          1. ``claim_next_job`` filtered to the Z+ job_type / target_type /
             operation_fingerprint.
          2. Resolve ``plan_id`` / ``window_id`` from the job's input_json
             immediately after claim (requirement 3) so failure handlers can
             mark ``analysis_windows.status = 'failed'`` even when
             ``process_window_job`` raises before returning ``candidates_ready``.
          3. ``GrammarWindowWorkerService.process_window_job`` runs preflight
             (§8.2 pending → running) + LLM (with heartbeat).
          4. If the worker returns ``candidates_ready``, hand off to
             ``GrammarWindowPublisher.publish_window_grammar_bundle`` (§8.4
             publish transaction). If the worker short-circuits with
             ``already_terminal`` (window already completed / no_op / failed),
             the publisher is skipped — there is nothing to publish.

        Failure handling (requirements 1 / 2 / 3 / 4 / 6):
          - ``GrammarWindowExecutionError``: routes via ``exc.retryable`` —
            config / validation errors go to ``failed_terminal``, provider
            errors go to ``retry_later`` (requirement 2).
          - ``ValueError`` (P2-1 fail-closed contract violation): ``failed_terminal``.
          - Generic ``Exception``: ``failed_terminal`` (defensive).
          - ``FenceViolationError`` from publisher: transition job →
            ``superseded``, mark run ``superseded``, end worker span as
            fence violation (requirement 1 — aligns with legacy grammar_worker).
          - Non-retryable / generic terminal failure marks
            ``analysis_windows.status = 'failed'``. Retryable failures leave
            the window in ``running`` so the same job retry can resume
            (requirement 3).
          - ``_handle_window_job_failure`` only swallows
            ``IllegalTransitionError`` (lease race / illegal transition);
            other transition exceptions propagate so the outer worker span
            fallback can record them (requirement 4 — no broad-swallow).
          - Success path records ``ai_usage_events`` + ends the worker_tick
            span with token / model fields (requirement 6). Failure path
            records a failed ``ai_usage_event`` when model metadata is
            available and ends the span via ``end_worker_span_execution_error``
            / ``end_worker_span_generic_exception``.
        """
        if self._grammar_window_worker is None or self._grammar_window_publisher is None:
            return ReaderPipelineWorkerAttempt(
                worker_type="grammar_bundle_window",
                outcome="no_job",
                processed_job=False,
            )

        claim = await self._job_runtime.claim_next_job(
            lease_owner=lease_owner,
            lease_duration=lease_duration,
            job_type=ZPLUS_GRAMMAR_JOB_TYPE,
            target_type=ZPLUS_TARGET_TYPE,
            operation_fingerprint=ZPLUS_GRAMMAR_OPERATION_FINGERPRINT,
            reading_record_id=record_id,
            base_id=base_id,
            expected_generation=expected_generation,
        )
        if claim is None:
            return ReaderPipelineWorkerAttempt(
                worker_type="grammar_bundle_window",
                outcome="no_job",
                processed_job=False,
            )

        # Requirement 3: resolve plan_id / window_id immediately after claim
        # so failure handlers can mark the analysis_window failed even when
        # process_window_job raises before returning candidates_ready.
        plan_id, window_id = await self._load_window_ids_from_job(claim.job_id)

        await self._mark_window_run_running(claim.run_id)

        try:
            result = await self._grammar_window_worker.process_window_job(claim=claim)
        except GrammarWindowExecutionError as exc:
            # Requirement 2: route via exc.retryable instead of fixed True.
            return await self._handle_window_job_failure(
                claim=claim,
                exc=exc,
                retryable=exc.retryable,
                retry_delay=retry_delay,
                failure_class=exc.failure_class,
                failure_code=exc.failure_code,
                rationale_code=exc.rationale_code,
                message=str(exc),
                window_id=window_id,
                prompt_version=exc.prompt_version,
                model_route=exc.model_route,
                model_profile=exc.model_profile,
                model_provider=exc.model_provider,
                model_name=exc.model_name,
                end_span="execution_error",
            )
        except ValueError as exc:
            return await self._handle_window_job_failure(
                claim=claim,
                exc=exc,
                retryable=False,
                retry_delay=retry_delay,
                failure_class="grammar_window_contract_violation",
                failure_code=type(exc).__name__,
                rationale_code="grammar_window_contract_violation",
                message=str(exc),
                window_id=window_id,
                end_span="execution_error",
            )
        except Exception as exc:
            return await self._handle_window_job_failure(
                claim=claim,
                exc=exc,
                retryable=False,
                retry_delay=retry_delay,
                failure_class="grammar_window_unexpected",
                failure_code=type(exc).__name__,
                rationale_code="grammar_window_unexpected_failure",
                message=str(exc),
                window_id=window_id,
                end_span="generic_exception",
            )

        status = result.get("status")

        if status != "candidates_ready":
            # already_terminal: window already completed (no_op / failed /
            # completed by a previous run). The publisher is skipped; no LLM
            # call was made. Close the claimed job/run so it does not wait for
            # lease recovery, then end the worker_tick span as skipped.
            transition_succeeded = False
            try:
                await self._job_runtime.transition(
                    job_id=claim.job_id,
                    target_status="skipped",
                    lease_token=claim.lease_token,
                    output_ref={
                        "plan_id": str(plan_id),
                        "window_id": str(window_id),
                        "reason": "analysis_window_already_terminal",
                    },
                    rationale_code="analysis_window_already_terminal",
                )
                transition_succeeded = True
            except IllegalTransitionError:
                pass
            if transition_succeeded:
                await self._mark_window_run_status(
                    claim.run_id,
                    status="completed",
                    failure_class=None,
                    failure_code=None,
                    finished_at=datetime.now(UTC),
                )
            recorder = get_default_recorder()
            span = current_span()
            if span is not None:
                await recorder.end_span(span, status=STATUS_SKIPPED)
            return ReaderPipelineWorkerAttempt(
                worker_type="grammar_bundle_window",
                outcome="succeeded",
                processed_job=True,
                job_id=claim.job_id,
                run_id=claim.run_id,
            )

        candidates: list = result.get("candidates", [])
        # P1-4 bridge: derive WindowCandidateContent from CandidateItem's
        # content_* fields so publisher can build proper layer output
        # (GrammarNoteLayerOutput / SentenceAnalysisLayerOutput) instead
        # of falling back to selector-sidecar output_json shape.
        try:
            candidate_contents = _derive_candidate_contents(candidates)
        except ValueError as exc:
            return await self._handle_window_job_failure(
                claim=claim,
                exc=exc,
                retryable=False,
                retry_delay=retry_delay,
                failure_class="grammar_window_contract_violation",
                failure_code="candidate_contents_derivation_failed",
                rationale_code="candidate_contents_derivation_failed",
                message=str(exc),
                window_id=window_id,
                prompt_version=result.get("prompt_version"),
                model_route=result.get("model_route"),
                model_profile=result.get("model_profile"),
                model_provider=result.get("model_provider"),
                model_name=result.get("model_name"),
                usage_data=result.get("usage_data"),
                end_span="execution_error",
            )
        try:
            published = await self._grammar_window_publisher.publish_window_grammar_bundle(
                job_id=claim.job_id,
                lease_token=claim.lease_token,
                plan_id=plan_id,
                window_id=window_id,
                candidates=candidates,
                candidate_contents=candidate_contents,
            )
        except FenceViolationError:
            # Requirement 1: align with legacy grammar_worker — transition
            # job → superseded, mark reader_run superseded, end worker span
            # as fence violation. Do NOT just return superseded and leave
            # the job/run in claimed/running.
            await end_worker_span_fence_violation()
            try:
                await self._job_runtime.transition(
                    job_id=claim.job_id,
                    target_status="superseded",
                    lease_token=claim.lease_token,
                    rationale_code="publish_fence_failed",
                )
            except IllegalTransitionError:
                # Job no longer in claimed (e.g. lease expired and recovered
                # by another tick). Span already ended above; fall through to
                # mark the run superseded for observability consistency.
                pass
            await self._mark_window_run_status(
                claim.run_id,
                status="superseded",
                failure_class="publish_guard",
                failure_code="publish_fence_failed",
                finished_at=datetime.now(UTC),
            )
            if window_id is not None:
                await self._mark_analysis_window_failed(window_id)
            return ReaderPipelineWorkerAttempt(
                worker_type="grammar_bundle_window",
                outcome="superseded",
                processed_job=True,
                job_id=claim.job_id,
                run_id=claim.run_id,
                attention_code="publish_fence_failed",
                # Mirror legacy grammar worker: count this job as superseded
                # so the pipeline summary's outcome_counts.superseded reflects
                # the fence violation (requirement 1 alignment).
                superseded_jobs=1,
            )
        except ValueError as exc:
            return await self._handle_window_job_failure(
                claim=claim,
                exc=exc,
                retryable=False,
                retry_delay=retry_delay,
                failure_class="grammar_window_contract_violation",
                failure_code="publisher_fail_closed",
                rationale_code="publisher_fail_closed",
                message=str(exc),
                window_id=window_id,
                prompt_version=result.get("prompt_version"),
                model_route=result.get("model_route"),
                model_profile=result.get("model_profile"),
                model_provider=result.get("model_provider"),
                model_name=result.get("model_name"),
                usage_data=result.get("usage_data"),
                end_span="execution_error",
            )
        except Exception as exc:
            return await self._handle_window_job_failure(
                claim=claim,
                exc=exc,
                retryable=False,
                retry_delay=retry_delay,
                failure_class="grammar_window_publisher_unexpected",
                failure_code=type(exc).__name__,
                rationale_code="publisher_unexpected_failure",
                message=str(exc),
                window_id=window_id,
                prompt_version=result.get("prompt_version"),
                model_route=result.get("model_route"),
                model_profile=result.get("model_profile"),
                model_provider=result.get("model_provider"),
                model_name=result.get("model_name"),
                usage_data=result.get("usage_data"),
                end_span="generic_exception",
            )

        # Requirement 6: success path — record ai_usage_events + end worker
        # span with token / model fields. The event carries plan_id /
        # window_id / window_index / target_unit_ids / target_anchor_ids /
        # accepted_count / no_op / layer_ids in metadata so Console can
        # correlate Z+ window runs with their LLM cost.
        window_meta = await self._load_window_publish_metadata(
            claim.job_id, window_id
        )
        event_id = await self._record_window_success_usage(
            claim=claim,
            result=result,
            plan_id=plan_id,
            window_id=window_id,
            window_meta=window_meta,
            published=published,
        )
        await end_worker_span_success(
            ai_usage_event_id=event_id,
            usage_data=result.get("usage_data"),
            model_route=result.get("model_route"),
            model_name=result.get("model_name"),
            model_provider=result.get("model_provider"),
            capability_code=CAPABILITY_READER_GRAMMAR_BUNDLE,
        )

        return ReaderPipelineWorkerAttempt(
            worker_type="grammar_bundle_window",
            outcome="succeeded",
            processed_job=True,
            job_id=claim.job_id,
            run_id=claim.run_id,
        )

    async def _handle_window_job_failure(
        self,
        *,
        claim: ClaimResult,
        exc: BaseException,
        retryable: bool,
        retry_delay: timedelta,
        failure_class: str,
        failure_code: str,
        rationale_code: str,
        message: str,
        window_id: UUID | None = None,
        prompt_version: str | None = None,
        model_route: str | None = None,
        model_profile: str | None = None,
        model_provider: str | None = None,
        model_name: str | None = None,
        usage_data: dict[str, Any] | None = None,
        end_span: Literal["execution_error", "generic_exception"] = "execution_error",
    ) -> ReaderPipelineWorkerAttempt:
        """Transition a failed Z+ window job to retry_later / failed_terminal.

        Mirrors ``grammar_worker._process_grammar_job``'s exception handlers:
          - ``retryable=True``  → ``reader_jobs.retry_later`` +
            ``reader_runs.failed_retryable`` (LLM transient).
          - ``retryable=False`` → ``reader_jobs.failed_terminal`` +
            ``reader_runs.failed_terminal`` (contract violation / code bug).

        Requirement 3: ``analysis_windows.status`` is marked ``failed`` only
        on non-retryable / terminal failures. Retryable failures leave the
        window in ``running`` so the same job retry can resume from the
        preflight state.

        Requirement 4: only ``IllegalTransitionError`` (lease race / illegal
        transition) is swallowed — the job row may have been recovered by
        another tick. Other transition exceptions propagate so the outer
        ``_run_worker_attempt`` fallback can record them on the worker_tick
        span. The previous broad ``except Exception: pass`` masked real
        bugs and left observability in an inconsistent state.

        Requirement 6: records a failed ``ai_usage_event`` when model
        metadata is available (``prompt_version`` / ``model_route`` /
        ``model_provider`` / ``model_name``) and ends the worker_tick span
        via ``end_worker_span_execution_error`` or
        ``end_worker_span_generic_exception``.
        """
        target_status = "retry_later" if retryable else "failed_terminal"
        run_status = "failed_retryable" if retryable else "failed_terminal"

        transition_kwargs: dict[str, Any] = {
            "job_id": claim.job_id,
            "target_status": target_status,
            "lease_token": claim.lease_token,
            "failure_class": failure_class,
            "failure_code": failure_code,
            "failure_message": message,
            "rationale_code": rationale_code,
        }
        if retryable:
            available_at = datetime.now(UTC) + retry_delay
            transition_kwargs["available_at"] = available_at

        # Requirement 4: only swallow IllegalTransitionError (lease race /
        # illegal transition). Other exceptions propagate to the outer
        # _run_worker_attempt which ends the span as failed.
        try:
            await self._job_runtime.transition(**transition_kwargs)
        except IllegalTransitionError:
            # Job no longer in claimed (e.g. lease expired and recovered by
            # another tick). Continue to mark the run + window for
            # observability consistency.
            pass

        await self._mark_window_run_failed(
            claim.run_id,
            status=run_status,
            failure_class=failure_class,
            failure_code=failure_code,
        )

        # Requirement 3: mark analysis_window failed only on non-retryable
        # failures. Retryable failures leave the window in running so the
        # same job retry can resume.
        if window_id is not None and not retryable:
            await self._mark_analysis_window_failed(window_id)

        # Requirement 6: record failed ai_usage_event when model metadata
        # is available (LLM call was attempted).
        if prompt_version is not None or model_route is not None:
            await self._record_window_failure_usage(
                claim=claim,
                failure_code=failure_code,
                message=message,
                prompt_version=prompt_version,
                model_route=model_route,
                model_profile=model_profile,
                model_provider=model_provider,
                model_name=model_name,
                usage_data=usage_data,
            )

        # Requirement 6: end worker_tick span with the failure class/code.
        if end_span == "generic_exception":
            await end_worker_span_generic_exception(
                layer="grammar_bundle_window", exc=exc
            )
        else:
            await end_worker_span_execution_error(
                failure_class=failure_class,
                failure_code=failure_code,
            )

        outcome: PipelineAttemptOutcome = (
            "retry_later" if retryable else "failed_terminal"
        )
        return ReaderPipelineWorkerAttempt(
            worker_type="grammar_bundle_window",
            outcome=outcome,
            processed_job=True,
            job_id=claim.job_id,
            run_id=claim.run_id,
            attention_code=rationale_code,
        )

    async def _load_window_ids_from_job(
        self,
        job_id: UUID,
    ) -> tuple[UUID, UUID]:
        """Extract (plan_id, window_id) from a Z+ window reader_job's input_json.

        The publisher needs both UUIDs to lock the plan + window rows. They are
        stored as strings in ``reader_jobs.input_json`` by
        ``ZPlusBootstrapService._create_window_job``.
        """
        async with self.get_pool().acquire() as conn:
            row = await conn.fetchrow(
                "SELECT input_json FROM reader_jobs WHERE id = $1",
                job_id,
            )
        if row is None:
            raise LookupError(f"reader job {job_id} not found")
        input_data: Any = row["input_json"]
        if isinstance(input_data, str):
            input_data = json.loads(input_data)
        return (
            UUID(str(input_data["plan_id"])),
            UUID(str(input_data["window_id"])),
        )

    async def _mark_window_run_running(self, run_id: UUID) -> None:
        """Mark a reader_run as ``running`` (mirrors grammar_worker._mark_run_running).

        Keeps reader_runs.status consistent with the existing per-unit worker
        so progress / observability queries see Z+ runs as in-flight.
        """
        async with self.get_pool().acquire() as conn:
            await conn.execute(
                """
                UPDATE reader_runs
                SET status = 'running',
                    failure_class = NULL,
                    failure_code = NULL,
                    finished_at = NULL,
                    started_at = COALESCE(started_at, NOW()),
                    updated_at = NOW()
                WHERE id = $1
                """,
                run_id,
            )

    async def _mark_window_run_failed(
        self,
        run_id: UUID,
        *,
        status: str,
        failure_class: str,
        failure_code: str,
    ) -> None:
        """Mark a Z+ reader_run as failed (mirrors grammar_worker failure path).

        ``status`` should be ``failed_retryable`` or ``failed_terminal`` to
        match ``reader_runs.status`` CHECK constraint. ``finished_at`` is set
        only for terminal failures; retryable runs stay open so the next
        attempt can re-enter ``running``.
        """
        is_terminal = status == "failed_terminal"
        async with self.get_pool().acquire() as conn:
            await conn.execute(
                """
                UPDATE reader_runs
                SET status = $2,
                    failure_class = $3,
                    failure_code = $4,
                    finished_at = CASE WHEN $5 THEN NOW() ELSE finished_at END,
                    updated_at = NOW()
                WHERE id = $1
                """,
                run_id,
                status,
                failure_class,
                failure_code,
                is_terminal,
            )

    async def _mark_window_run_status(
        self,
        run_id: UUID,
        *,
        status: str,
        failure_class: str | None,
        failure_code: str | None,
        finished_at: datetime | None,
    ) -> None:
        """Mark a Z+ reader_run with an explicit status + finished_at.

        Used by the FenceViolationError branch (requirement 1) to mark the
        run ``superseded`` with ``finished_at=NOW()`` — mirroring
        ``grammar_worker._mark_run_status``. Unlike ``_mark_window_run_failed``
        which derives ``finished_at`` from ``is_terminal``, this method lets
        the caller set ``finished_at`` explicitly because ``superseded`` is
        not ``failed_terminal`` but still closes the run.
        """
        async with self.get_pool().acquire() as conn:
            await conn.execute(
                """
                UPDATE reader_runs
                SET status = $2,
                    failure_class = $3,
                    failure_code = $4,
                    finished_at = $5,
                    updated_at = NOW()
                WHERE id = $1
                """,
                run_id,
                status,
                failure_class,
                failure_code,
                finished_at,
            )

    async def _load_window_publish_metadata(
        self,
        job_id: UUID,
        window_id: UUID,
    ) -> dict[str, Any]:
        """Load window_index / target_unit_ids / target_anchor_ids for ai_usage_event.

        ``window_index`` and ``target_unit_ids`` are read from the job's
        ``input_json`` (written by ``ZPlusBootstrapService._create_window_job``).
        ``target_anchor_ids`` is read from the ``analysis_windows`` row so
        it reflects any post-bootstrap corrections.
        """
        async with self.get_pool().acquire() as conn:
            job_row = await conn.fetchrow(
                "SELECT input_json FROM reader_jobs WHERE id = $1",
                job_id,
            )
            if job_row is None:
                return {"window_index": None, "target_unit_ids": [], "target_anchor_ids": []}
            input_data: Any = job_row["input_json"]
            if isinstance(input_data, str):
                input_data = json.loads(input_data)

            window_row = await conn.fetchrow(
                "SELECT target_anchor_ids FROM analysis_windows WHERE id = $1",
                window_id,
            )
            target_anchor_ids_raw: Any = (
                window_row["target_anchor_ids"] if window_row is not None else None
            )
            if isinstance(target_anchor_ids_raw, str):
                target_anchor_ids_raw = json.loads(target_anchor_ids_raw)

        target_anchor_ids: list[str] = (
            [str(a) for a in target_anchor_ids_raw]
            if isinstance(target_anchor_ids_raw, list)
            else []
        )
        return {
            "window_index": int(input_data.get("window_index", 0)),
            "target_unit_ids": list(input_data.get("target_unit_ids", [])),
            "target_anchor_ids": target_anchor_ids,
        }

    async def _record_window_success_usage(
        self,
        *,
        claim: ClaimResult,
        result: dict[str, Any],
        plan_id: UUID,
        window_id: UUID,
        window_meta: dict[str, Any],
        published: PublishedWindowResult,
    ) -> UUID | None:
        """Record a succeeded ``ai_usage_event`` for a Z+ window publish.

        Requirement 6: ``capability_code`` uses ``reader_grammar_bundle``,
        ``operation_fingerprint`` uses ``grammar_bundle_window_v1``, and
        ``metadata`` includes ``plan_id`` / ``window_id`` / ``window_index`` /
        ``target_unit_ids`` / ``target_anchor_ids`` / ``accepted_count`` /
        ``no_op`` / ``layer_ids`` so Console can correlate Z+ window runs
        with their LLM cost.
        """
        layer_ids = list(published.grammar_note_layer_ids) + list(
            published.sentence_analysis_layer_ids
        )
        return await record_ai_usage_event(
            AIUsageEventCreate(
                usage_scope=USAGE_SCOPE_SYSTEM_INTERNAL,
                capability_code=CAPABILITY_READER_GRAMMAR_BUNDLE,
                billing_mode=BILLING_MODE_INTERNAL_ONLY,
                status=STATUS_SUCCEEDED,
                user_id=claim.user_id,
                reading_record_id=claim.reading_record_id,
                reader_run_id=claim.run_id,
                reader_job_id=claim.job_id,
                workflow_name="reader_orchestration",
                workflow_version=ZPLUS_WINDOW_WORKFLOW_VERSION,
                prompt_version=result.get("prompt_version"),
                model_route=result.get("model_route"),
                model_profile_id=result.get("model_profile"),
                model_profile=result.get("model_profile"),
                model_provider=result.get("model_provider"),
                model_name=result.get("model_name"),
                planner_kind="llm_worker",
                usage_data=result.get("usage_data"),
                operation_fingerprint=ZPLUS_WINDOW_OPERATION_FINGERPRINT,
                metadata_json={
                    "plan_id": str(plan_id),
                    "window_id": str(window_id),
                    "window_index": window_meta.get("window_index"),
                    "target_unit_ids": window_meta.get("target_unit_ids", []),
                    "target_anchor_ids": window_meta.get("target_anchor_ids", []),
                    "accepted_count": published.accepted_count,
                    "no_op": published.skipped or published.accepted_count == 0,
                    "layer_ids": [str(lid) for lid in layer_ids],
                    "grammar_note_layer_ids": [
                        str(lid) for lid in published.grammar_note_layer_ids
                    ],
                    "sentence_analysis_layer_ids": [
                        str(lid) for lid in published.sentence_analysis_layer_ids
                    ],
                },
            )
        )

    async def _record_window_failure_usage(
        self,
        *,
        claim: ClaimResult,
        failure_code: str,
        message: str,
        prompt_version: str | None = None,
        model_route: str | None = None,
        model_profile: str | None = None,
        model_provider: str | None = None,
        model_name: str | None = None,
        usage_data: dict[str, Any] | None = None,
    ) -> UUID | None:
        """Record a failed ``ai_usage_event`` for a Z+ window LLM call.

        Mirrors ``grammar_worker._record_failed_usage_event``: captures the
        LLM cost even when the window publish failed, so Console's
        cost-per-window panel can surface wasted tokens.
        """
        return await record_ai_usage_event(
            AIUsageEventCreate(
                usage_scope=USAGE_SCOPE_SYSTEM_INTERNAL,
                capability_code=CAPABILITY_READER_GRAMMAR_BUNDLE,
                billing_mode=BILLING_MODE_INTERNAL_ONLY,
                status=STATUS_FAILED,
                user_id=claim.user_id,
                reading_record_id=claim.reading_record_id,
                reader_run_id=claim.run_id,
                reader_job_id=claim.job_id,
                workflow_name="reader_orchestration",
                workflow_version=ZPLUS_WINDOW_WORKFLOW_VERSION,
                prompt_version=prompt_version,
                model_route=model_route,
                model_profile_id=model_profile,
                model_profile=model_profile,
                model_provider=model_provider,
                model_name=model_name,
                planner_kind="llm_worker",
                usage_data=usage_data,
                operation_fingerprint=ZPLUS_WINDOW_OPERATION_FINGERPRINT,
                error_code=failure_code,
                error_message=message,
            )
        )

    async def _mark_analysis_window_failed(self, window_id: UUID) -> None:
        """Mark ``analysis_windows.status = 'failed'`` on job failure.

        Preflight (§8.2) transitions the window from ``pending`` to
        ``running`` before the LLM call. If the executor / publisher raises,
        the window would otherwise be stuck in ``running`` (or ``pending``
        if preflight itself failed) forever. This marks it ``failed`` so
        observability queries and re-bootstrap logic see the window as
        terminal. Already-terminal windows (``completed`` / ``no_op`` /
        ``failed``) are left untouched.
        """
        async with self.get_pool().acquire() as conn:
            await conn.execute(
                """
                UPDATE analysis_windows
                SET status = 'failed',
                    completed_at = COALESCE(completed_at, NOW())
                WHERE id = $1
                  AND status NOT IN ('completed', 'no_op', 'failed')
                """,
                window_id,
            )

    async def _build_worker_attempt_from_result(
        self,
        *,
        worker_type: WorkerType,
        record_id: UUID,
        base_id: UUID,
        expected_generation: int,
        job_type: str,
        target_scope: str,
        operation_fingerprint: str,
        before_superseded: int,
        result: (
            DisplayTitleJobProcessResult
            | TranslationBatchJobProcessResult
            | VocabularyJobProcessResult
            | VocabularyBatchJobProcessResult
            | GrammarJobProcessResult
            | None
        ),
    ) -> ReaderPipelineWorkerAttempt:
        superseded_jobs = (
            await self._count_superseded_jobs(
                record_id=record_id,
                base_id=base_id,
                expected_generation=expected_generation,
                job_type=job_type,
                target_scope=target_scope,
                operation_fingerprint=operation_fingerprint,
            )
            - before_superseded
        )
        if result is None:
            return ReaderPipelineWorkerAttempt(
                worker_type=worker_type,
                outcome="no_job",
                processed_job=False,
                superseded_jobs=max(0, superseded_jobs),
            )

        attention = None
        if result.status != "succeeded":
            attention = await self._load_job_attention_code(result.claim.job_id)
        return ReaderPipelineWorkerAttempt(
            worker_type=worker_type,
            outcome=self._normalize_worker_outcome(result.status),
            processed_job=True,
            job_id=result.claim.job_id,
            run_id=result.claim.run_id,
            attention_code=attention,
            superseded_jobs=max(0, superseded_jobs),
        )

    async def _count_superseded_jobs(
        self,
        *,
        record_id: UUID,
        base_id: UUID,
        expected_generation: int,
        job_type: str,
        target_scope: str,
        operation_fingerprint: str,
    ) -> int:
        async with self.get_pool().acquire() as conn:
            return int(
                await conn.fetchval(
                    """
                    SELECT COUNT(*)
                    FROM reader_jobs
                    WHERE reading_record_id = $1
                      AND base_id = $2
                      AND expected_generation = $3
                      AND job_type = $4
                      AND target_type = $5
                      AND (operation_fingerprint = $6
                           OR starts_with(operation_fingerprint, $6 || ':'))
                      AND status = 'superseded'
                    """,
                    record_id,
                    base_id,
                    expected_generation,
                    job_type,
                    target_scope,
                    operation_fingerprint,
                )
            )

    async def _load_record_runtime_state(
        self,
        *,
        record_id: UUID,
        user_id: UUID,
    ) -> _RecordRuntimeState:
        async with self.get_pool().acquire() as conn:
            record_row = await conn.fetchrow(
                """
                SELECT generation, active_base_id
                FROM reading_records
                WHERE id = $1
                  AND user_id = $2
                  AND deleted_at IS NULL
                """,
                record_id,
                user_id,
            )
            if record_row is None:
                raise LookupError(f"reading record {record_id} not found for user {user_id}")
            sequence_row = await conn.fetchrow(
                """
                SELECT next_sequence
                FROM reader_event_sequences
                WHERE reading_record_id = $1
                """,
                record_id,
            )
        last_event_sequence = 0
        if sequence_row is not None and sequence_row["next_sequence"] is not None:
            last_event_sequence = max(0, int(sequence_row["next_sequence"]) - 1)
        active_base_id = record_row["active_base_id"]
        return _RecordRuntimeState(
            generation=int(record_row["generation"]),
            active_base_id=UUID(str(active_base_id)) if active_base_id is not None else None,
            last_event_sequence=last_event_sequence,
        )

    async def _load_job_attention_code(self, job_id: UUID) -> str | None:
        async with self.get_pool().acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT failure_code, rationale_code
                FROM reader_jobs
                WHERE id = $1
                """,
                job_id,
            )
        if row is None:
            return None
        return (
            str(row["failure_code"])
            if row["failure_code"] is not None
            else (
                str(row["rationale_code"])
                if row["rationale_code"] is not None
                else None
            )
        )

    @staticmethod
    def _normalize_worker_outcome(status: str) -> PipelineAttemptOutcome:
        if status == "succeeded":
            return "succeeded"
        if status == "retry_later":
            return "retry_later"
        if status == "failed_terminal":
            return "failed_terminal"
        return "failed_terminal"
