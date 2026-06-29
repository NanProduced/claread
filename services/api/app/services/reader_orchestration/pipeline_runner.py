from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Literal
from uuid import UUID

import asyncpg

from app.database import connection as db_connection
from app.services.reader_orchestration.grammar_worker import (
    DEFAULT_GRAMMAR_RETRY_DELAY,
    GrammarBundleWorkerService,
    GrammarJobProcessResult,
)
from app.services.reader_orchestration.display_title_worker import (
    DEFAULT_DISPLAY_TITLE_RETRY_DELAY,
    DisplayTitleJobProcessResult,
    DisplayTitleWorkerService,
)
from app.services.reader_orchestration.job_bootstrap import (
    DISPLAY_TITLE_JOB_TYPE,
    DISPLAY_TITLE_OPERATION_FINGERPRINT,
    DISPLAY_TITLE_TARGET_SCOPE,
    GRAMMAR_JOB_TYPE,
    GRAMMAR_OPERATION_FINGERPRINT,
    GRAMMAR_TARGET_SCOPE,
    TRANSLATION_JOB_TYPE,
    TRANSLATION_OPERATION_FINGERPRINT,
    TRANSLATION_TARGET_SCOPE,
    VOCABULARY_JOB_TYPE,
    VOCABULARY_OPERATION_FINGERPRINT,
    VOCABULARY_TARGET_SCOPE,
    EnhancementBootstrapJobCounts,
    EnhancementBootstrapSummary,
    EnhancementJobBootstrapService,
)
from app.services.reader_orchestration.job_runtime import FenceViolationError
from app.services.reader_orchestration.orchestrator import ReaderOrchestrator
from app.services.reader_orchestration.translation_worker import (
    DEFAULT_TRANSLATION_RETRY_DELAY,
)
from app.services.reader_orchestration.vocabulary_worker import (
    DEFAULT_VOCABULARY_RETRY_DELAY,
    VocabularyJobProcessResult,
    VocabularyWorkerService,
)

WorkerType = Literal["display_title", "translation", "vocabulary", "grammar_bundle"]
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

DEFAULT_PIPELINE_MAX_TICKS = 24
DEFAULT_PIPELINE_MAX_JOBS = 24


@dataclass(frozen=True, slots=True)
class EnhancementWorkerTickCounts:
    display_title: int = 0
    translation: int = 0
    vocabulary: int = 0
    grammar_bundle: int = 0


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
        vocabulary_worker_service: VocabularyWorkerService | None = None,
        grammar_worker_service: GrammarBundleWorkerService | None = None,
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
        self._vocabulary_worker_service = vocabulary_worker_service or VocabularyWorkerService(
            pool=pool
        )
        self._grammar_worker_service = grammar_worker_service or GrammarBundleWorkerService(
            pool=pool
        )

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
        return await self._bootstrap_service.bootstrap_missing_jobs(
            record_id=record_id,
            user_id=user_id,
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
            "vocabulary": 0,
            "grammar_bundle": 0,
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

        worker_order: tuple[WorkerType, ...] = (
            "display_title",
            "translation",
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
                vocabulary=tick_counts["vocabulary"],
                grammar_bundle=tick_counts["grammar_bundle"],
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
        if worker_type == "vocabulary":
            return await self._run_vocabulary_attempt(
                record_id=record_id,
                base_id=base_id,
                expected_generation=expected_generation,
                lease_owner=lease_owner,
                lease_duration=lease_duration,
                retry_delay=vocabulary_retry_delay,
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
            result = await self._display_title_worker_service.process_next_display_title_job_for_record(
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
            | VocabularyJobProcessResult
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
                      AND operation_fingerprint = $6
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
