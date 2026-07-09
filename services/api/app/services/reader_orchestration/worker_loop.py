from __future__ import annotations

import logging
import os
import socket
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

import asyncpg

from app.database import connection as db_connection

from .completion_finalizer import (
    CompletionFinalizationResult,
    CompletionFinalizer,
    should_attempt_finalization,
)
from .event_runtime import ReaderEventRuntime
from .job_runtime import ReaderJobRuntime
from .pipeline_runner import (
    DEFAULT_PIPELINE_MAX_JOBS,
    DEFAULT_PIPELINE_MAX_TICKS,
    ReaderEnhancementPipelineRunner,
    ReaderPipelineRunSummary,
)
from .product_state import (
    PRODUCT_STATE_UPDATED_EVENT_TYPE,
    build_product_state_event_payload,
    decide_product_state_for_pipeline_summary,
)
from .repository import ReaderOrchestrationRepository
from .span_recorder import (
    SPAN_KIND_PIPELINE_ROOT,
    STATUS_FAILED,
    STATUS_SUCCEEDED,
    get_default_recorder,
)

logger = logging.getLogger(__name__)

DEFAULT_READER_WORKER_LEASE_DURATION = timedelta(seconds=120)
READER_WORKER_USER_ADVISORY_LOCK_NAMESPACE = 1_431_459_667
_RUNNABLE_RECORD_READYNESS_STATES = ("article_ready", "initial_enhancement_ready")
_RUNNABLE_RECORD_PRODUCT_STATES = ("processing", "readable_enhancing")

# Enhancement pipeline job types that the worker loop tracks. The candidate
# scan counts ONLY these job types when deciding whether a record already has
# tracked/runnable enhancement work. Non-enhancement jobs (notably
# ``article_rag_index_build`` from the D6-I4B RAG bootstrap) must NOT keep a
# record out of the enhancement candidate set — otherwise an article_ready
# record whose only job is a succeeded/queued RAG index job would be treated
# as "already has tracked jobs" and never enter the enhancement pipeline,
# leaving display_title / translation / vocabulary / grammar unbootstrapped.
#
# Keep this list in sync with the job_type constants in job_bootstrap.py and
# zplus_bootstrap.py. The CHECK constraint on reader_jobs.job_type is the
# authoritative source of allowed values; this tuple is the subset that the
# enhancement worker loop owns.
ENHANCEMENT_PIPELINE_JOB_TYPES: tuple[str, ...] = (
    "generate_display_title_zh",
    "translate_unit",
    "translate_article",
    "build_vocabulary_layer",
    "build_vocabulary_layer_article",
    "build_grammar_bundle",
    "build_grammar_bundle_window",
)

WorkerLoopRecordOutcome = Literal["processed", "lock_unavailable"]


@dataclass(frozen=True, slots=True)
class WorkerLoopCandidateRecord:
    record_id: UUID
    user_id: UUID
    base_id: UUID
    expected_generation: int
    runnable_job_count: int
    tracked_job_count: int


@dataclass(frozen=True, slots=True)
class ReaderEnhancementWorkerLoopRecordResult:
    candidate: WorkerLoopCandidateRecord
    outcome: WorkerLoopRecordOutcome
    pipeline_summary: ReaderPipelineRunSummary | None = None
    # T3.5: completion finalizer outcome. Present only when the finalizer
    # was actually invoked (i.e. ``stopped_reason == "all_workers_no_job"``
    # and ``product_state_decision.should_update_record`` was False).
    completion_finalization_result: CompletionFinalizationResult | None = None


@dataclass(frozen=True, slots=True)
class ReaderEnhancementWorkerLoopCycleSummary:
    recovered_stale_leases: int
    scanned_candidate_count: int
    processed_count: int
    lock_skipped_count: int
    candidates: tuple[WorkerLoopCandidateRecord, ...]
    results: tuple[ReaderEnhancementWorkerLoopRecordResult, ...]


def record_advisory_lock_key(record_id: UUID) -> int:
    return ((record_id.int >> 64) ^ record_id.int) & ((1 << 63) - 1)


def user_advisory_lock_key(user_id: UUID) -> int:
    raw = (
        (user_id.int >> 96)
        ^ (user_id.int >> 64)
        ^ (user_id.int >> 32)
        ^ user_id.int
    ) & 0xFFFFFFFF
    return raw - (1 << 32) if raw >= (1 << 31) else raw


def build_reader_worker_lease_owner(*, lease_owner_prefix: str) -> str:
    return f"{lease_owner_prefix}:{socket.gethostname()}:{os.getpid()}"


class ReaderEnhancementWorkerLoopService:
    def __init__(
        self,
        *,
        pool: asyncpg.Pool | None = None,
        pipeline_runner: ReaderEnhancementPipelineRunner | None = None,
        job_runtime: ReaderJobRuntime | None = None,
        repository: ReaderOrchestrationRepository | None = None,
        event_runtime: ReaderEventRuntime | None = None,
        completion_finalizer: CompletionFinalizer | None = None,
    ) -> None:
        self._pool = pool
        self._pipeline_runner = pipeline_runner or ReaderEnhancementPipelineRunner(pool=pool)
        self._job_runtime = job_runtime or ReaderJobRuntime(pool=pool)
        self._repository = repository or ReaderOrchestrationRepository(pool=pool)
        self._event_runtime = event_runtime or ReaderEventRuntime(pool=pool)
        # T3.5: completion finalizer advances ``readiness_state`` to
        # ``coverage_complete`` once all enhancement jobs and analysis
        # windows reach a terminal status. Defaults to a finalizer that
        # reuses this service's repository so production wiring requires
        # no extra configuration; tests can inject a stub.
        self._completion_finalizer = (
            completion_finalizer or CompletionFinalizer(repository=self._repository)
        )

    def get_pool(self) -> asyncpg.Pool:
        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("Database pool not initialized")
        return pool

    async def scan_eligible_records(
        self,
        *,
        batch_size: int,
    ) -> tuple[WorkerLoopCandidateRecord, ...]:
        if batch_size < 1:
            raise ValueError("batch_size must be >= 1")

        async with self.get_pool().acquire() as conn:
            rows = await conn.fetch(
                """
                WITH scoped_records AS (
                    SELECT
                        record.id AS record_id,
                        record.user_id,
                        record.generation AS expected_generation,
                        record.active_base_id AS base_id,
                        COUNT(job.id) FILTER (
                            WHERE job.status IN (
                                'queued',
                                'claimed',
                                'retry_later',
                                'paused',
                                'succeeded',
                                'failed_terminal',
                                'skipped'
                            )
                        ) AS tracked_job_count,
                        COUNT(job.id) FILTER (
                            WHERE job.status = 'queued'
                               OR (
                                   job.status = 'retry_later'
                                   AND job.available_at <= NOW()
                               )
                        ) AS runnable_job_count
                    FROM reading_records record
                    JOIN reading_bases base
                      ON base.id = record.active_base_id
                     AND base.reading_record_id = record.id
                    LEFT JOIN reader_jobs job
                      ON job.reading_record_id = record.id
                     AND job.base_id = record.active_base_id
                     AND job.expected_generation = record.generation
                     AND job.job_type = ANY($4::text[])
                    WHERE record.deleted_at IS NULL
                      AND record.lifecycle_status = 'active'
                      AND record.product_state = ANY($1::text[])
                      AND record.readiness_state = ANY($2::text[])
                      AND record.active_base_id IS NOT NULL
                      AND base.status = 'active'
                      AND base.record_generation = record.generation
                    GROUP BY
                        record.id,
                        record.user_id,
                        record.generation,
                        record.active_base_id
                )
                SELECT
                    record_id,
                    user_id,
                    base_id,
                    expected_generation,
                    runnable_job_count,
                    tracked_job_count
                FROM scoped_records
                WHERE runnable_job_count > 0
                   OR tracked_job_count = 0
                ORDER BY
                    CASE WHEN runnable_job_count > 0 THEN 0 ELSE 1 END,
                    record_id ASC
                LIMIT $3
                """,
                list(_RUNNABLE_RECORD_PRODUCT_STATES),
                list(_RUNNABLE_RECORD_READYNESS_STATES),
                batch_size,
                list(ENHANCEMENT_PIPELINE_JOB_TYPES),
            )

        return tuple(
            WorkerLoopCandidateRecord(
                record_id=row["record_id"],
                user_id=row["user_id"],
                base_id=row["base_id"],
                expected_generation=int(row["expected_generation"]),
                runnable_job_count=int(row["runnable_job_count"] or 0),
                tracked_job_count=int(row["tracked_job_count"] or 0),
            )
            for row in rows
        )

    async def process_candidate(
        self,
        *,
        candidate: WorkerLoopCandidateRecord,
        lease_owner_prefix: str,
        lease_duration: timedelta = DEFAULT_READER_WORKER_LEASE_DURATION,
        max_ticks: int = DEFAULT_PIPELINE_MAX_TICKS,
        max_jobs: int = DEFAULT_PIPELINE_MAX_JOBS,
    ) -> ReaderEnhancementWorkerLoopRecordResult:
        lease_owner = build_reader_worker_lease_owner(
            lease_owner_prefix=lease_owner_prefix
        )
        lock_key = record_advisory_lock_key(candidate.record_id)
        user_lock_key = user_advisory_lock_key(candidate.user_id)

        async with self.get_pool().acquire() as lock_conn:
            locked = await lock_conn.fetchval(
                "SELECT pg_try_advisory_lock($1)",
                lock_key,
            )
            if not locked:
                return ReaderEnhancementWorkerLoopRecordResult(
                    candidate=candidate,
                    outcome="lock_unavailable",
                )

            user_locked = False
            try:
                user_locked = await lock_conn.fetchval(
                    "SELECT pg_try_advisory_lock($1, $2)",
                    READER_WORKER_USER_ADVISORY_LOCK_NAMESPACE,
                    user_lock_key,
                )
                if not user_locked:
                    return ReaderEnhancementWorkerLoopRecordResult(
                        candidate=candidate,
                        outcome="lock_unavailable",
                    )

                recorder = get_default_recorder()
                # Reuse the trace_id the orchestrator assigned and persisted
                # into reader_runs.envelope_json so the span tree links back
                # to the run (gap report #3). Falls back to a fresh uuid4()
                # for legacy rows without trace_id in the envelope.
                trace_id = await self._repository.read_trace_id_for_record(
                    candidate.record_id
                )
                if trace_id is None:
                    trace_id = uuid4()
                pipeline_span = await recorder.start_span(
                    trace_id=trace_id,
                    span_kind=SPAN_KIND_PIPELINE_ROOT,
                    reading_record_id=candidate.record_id,
                    metadata={"lease_owner": lease_owner},
                )
                try:
                    async with recorder.use_span(pipeline_span):
                        summary = await self._pipeline_runner.run(
                            record_id=candidate.record_id,
                            user_id=candidate.user_id,
                            lease_owner=lease_owner,
                            lease_duration=lease_duration,
                            max_ticks=max_ticks,
                            max_jobs=max_jobs,
                        )
                        product_state_decision = decide_product_state_for_pipeline_summary(summary)
                        product_state_updated = False
                        product_state_event_sequence: int | None = None
                        completion_finalization_result: (
                            CompletionFinalizationResult | None
                        ) = None
                        if product_state_decision.should_update_record:
                            updated_at = datetime.now(UTC)
                            async with lock_conn.transaction():
                                product_state_updated = (
                                    await self._repository.update_record_product_state_if_active(
                                        lock_conn,
                                        record_id=candidate.record_id,
                                        expected_generation=candidate.expected_generation,
                                        next_product_state=product_state_decision.next_product_state,
                                        updated_at=updated_at,
                                    )
                                )
                                if product_state_updated:
                                    published_event = await (
                                        self._event_runtime.publish_event_in_transaction(
                                            lock_conn,
                                            record_id=candidate.record_id,
                                            event_type=PRODUCT_STATE_UPDATED_EVENT_TYPE,
                                            payload_json=build_product_state_event_payload(
                                                decision=product_state_decision,
                                                attention_code=summary.attention_code,
                                                stopped_reason=summary.stopped_reason,
                                                stopped_outcome=summary.stopped_outcome,
                                            ),
                                            created_at=updated_at,
                                        )
                                    )
                                    product_state_event_sequence = published_event.sequence
                        elif should_attempt_finalization(summary):
                            # T3.5: pipeline reached ``all_workers_no_job``
                            # without an attention signal. Verify every
                            # enhancement job and analysis window is
                            # terminal before transitioning
                            # ``readiness_state -> coverage_complete``.
                            # The finalizer is a no-op write when the
                            # record still has in-flight work, so the
                            # worker loop will re-scan on the next cycle.
                            updated_at = datetime.now(UTC)
                            async with lock_conn.transaction():
                                completion_finalization_result = (
                                    await self._completion_finalizer.finalize_completion_state(
                                        lock_conn,
                                        record_id=candidate.record_id,
                                        base_id=candidate.base_id,
                                        expected_generation=candidate.expected_generation,
                                        summary=summary,
                                        enhancement_job_types=ENHANCEMENT_PIPELINE_JOB_TYPES,
                                        event_runtime=self._event_runtime,
                                        updated_at=updated_at,
                                    )
                                )
                    await recorder.end_span(
                        pipeline_span,
                        status=STATUS_SUCCEEDED,
                        extra_metadata={
                            "stopped_reason": summary.stopped_reason,
                            "total_ticks": summary.total_ticks,
                            "total_jobs": summary.total_jobs,
                            "completion_finalized": (
                                completion_finalization_result.finalized
                                if completion_finalization_result is not None
                                else False
                            ),
                            "completion_outcome": (
                                completion_finalization_result.outcome
                                if completion_finalization_result is not None
                                else None
                            ),
                            "completion_skip_reason": (
                                completion_finalization_result.skip_reason
                                if completion_finalization_result is not None
                                else None
                            ),
                        },
                    )
                except Exception as exc:
                    await recorder.end_span(
                        pipeline_span,
                        status=STATUS_FAILED,
                        failure_class="pipeline_exception",
                        failure_code=type(exc).__name__,
                    )
                    raise
            finally:
                if user_locked:
                    user_unlocked = await lock_conn.fetchval(
                        "SELECT pg_advisory_unlock($1, $2)",
                        READER_WORKER_USER_ADVISORY_LOCK_NAMESPACE,
                        user_lock_key,
                    )
                    if user_unlocked is False:
                        logger.warning(
                            "reader enhancement worker failed to release user advisory lock",
                            extra={
                                "user_id": str(candidate.user_id),
                                "lock_key": user_lock_key,
                            },
                        )
                unlocked = await lock_conn.fetchval(
                    "SELECT pg_advisory_unlock($1)",
                    lock_key,
                )
                if unlocked is False:
                    logger.warning(
                        "reader enhancement worker failed to release advisory lock",
                        extra={
                            "record_id": str(candidate.record_id),
                            "lock_key": lock_key,
                        },
                    )

        log_method = (
            logger.warning
            if summary.stopped_reason == "attention_required"
            else logger.info
        )
        log_method(
            "reader enhancement worker processed record",
            extra={
                "record_id": str(summary.record_id),
                "base_id": str(summary.base_id),
                "expected_generation": summary.expected_generation,
                "stopped_reason": summary.stopped_reason,
                "stopped_worker_type": summary.stopped_worker_type,
                "stopped_outcome": summary.stopped_outcome,
                "attention_code": summary.attention_code,
                "snapshot_reload_recommended": summary.snapshot_reload_recommended,
                "total_ticks": summary.total_ticks,
                "total_jobs": summary.total_jobs,
                "product_state_decision_reason": product_state_decision.reason_code,
                "product_state_decision_user_visible": product_state_decision.user_visible,
                "next_product_state": product_state_decision.next_product_state,
                "product_state_updated": product_state_updated,
                "product_state_event_sequence": product_state_event_sequence,
                "completion_finalized": (
                    completion_finalization_result.finalized
                    if completion_finalization_result is not None
                    else False
                ),
                "completion_outcome": (
                    completion_finalization_result.outcome
                    if completion_finalization_result is not None
                    else None
                ),
                "completion_skip_reason": (
                    completion_finalization_result.skip_reason
                    if completion_finalization_result is not None
                    else None
                ),
                "completion_event_sequence": (
                    completion_finalization_result.event_sequence
                    if completion_finalization_result is not None
                    else None
                ),
            },
        )
        return ReaderEnhancementWorkerLoopRecordResult(
            candidate=candidate,
            outcome="processed",
            pipeline_summary=summary,
            completion_finalization_result=completion_finalization_result,
        )

    async def run_once(
        self,
        *,
        batch_size: int,
        lease_owner_prefix: str,
        lease_duration: timedelta = DEFAULT_READER_WORKER_LEASE_DURATION,
        max_ticks: int = DEFAULT_PIPELINE_MAX_TICKS,
        max_jobs: int = DEFAULT_PIPELINE_MAX_JOBS,
    ) -> ReaderEnhancementWorkerLoopCycleSummary:
        if batch_size < 1:
            raise ValueError("batch_size must be >= 1")
        recovered_stale_leases = await self._job_runtime.recover_stale_leases(
            batch_size=batch_size,
        )
        candidates = await self.scan_eligible_records(batch_size=batch_size)
        results: list[ReaderEnhancementWorkerLoopRecordResult] = []
        for candidate in candidates:
            results.append(
                await self.process_candidate(
                    candidate=candidate,
                    lease_owner_prefix=lease_owner_prefix,
                    lease_duration=lease_duration,
                    max_ticks=max_ticks,
                    max_jobs=max_jobs,
                )
            )

        processed_count = sum(1 for result in results if result.outcome == "processed")
        lock_skipped_count = sum(
            1 for result in results if result.outcome == "lock_unavailable"
        )
        return ReaderEnhancementWorkerLoopCycleSummary(
            recovered_stale_leases=recovered_stale_leases,
            scanned_candidate_count=len(candidates),
            processed_count=processed_count,
            lock_skipped_count=lock_skipped_count,
            candidates=candidates,
            results=tuple(results),
        )
