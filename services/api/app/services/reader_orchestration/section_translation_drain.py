"""T5.6b — budget-aware job_id drain for section_v1 translate_article jobs.

Structure (R1.2 + P1):
  lock + validate section shape / fence
  → load durable ExecutionBudget
  → translation pre-check (force-fail only after shape+fence OK)
  → claim_by_job_id
  → process_claimed_translation_batch_job
  → same durable accounting as translation_batch (attempt_count on claim)

Never calls process_next_*.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from typing import Any
from uuid import UUID

import asyncpg

from app.database import connection as db_connection
from app.services.reader_orchestration.completion_finalizer import (
    BUDGET_EXHAUSTED_FAILURE_CODE,
    BUDGET_EXHAUSTED_FAILURE_REASON,
)
from app.services.reader_orchestration.execution_budget import ExecutionBudget
from app.services.reader_orchestration.job_bootstrap import (
    TRANSLATION_BATCH_JOB_TYPE,
    TRANSLATION_BATCH_TARGET_SCOPE,
    _fingerprint_matches_base,
)
from app.services.reader_orchestration.job_runtime import (
    ClaimResult,
    ReaderJobRuntime,
    STATUS_FAILED_TERMINAL,
)
from app.services.reader_orchestration.section_lane import (
    SECTION_REQUEST_ORIGIN,
    TRANSLATION_SECTION_OPERATION_FINGERPRINT,
)
from app.services.reader_orchestration.translation_worker import (
    DEFAULT_TRANSLATION_RETRY_DELAY,
    TranslationBatchJobProcessResult,
    TranslationWorkerService,
)

DEFAULT_SECTION_DRAIN_LEASE = timedelta(seconds=120)


class SectionDrainOutcome(str, Enum):
    SUCCEEDED = "succeeded"
    ALREADY_CLAIMED = "already_claimed"
    BUDGET_DENIED = "budget_denied"
    NOT_FOUND = "not_found"
    REJECTED = "rejected"
    FAILED = "failed"
    SUPERSEDED = "superseded"
    RETRY_LATER = "retry_later"


@dataclass(frozen=True, slots=True)
class SectionDrainResult:
    outcome: SectionDrainOutcome
    job_id: UUID
    claim: ClaimResult | None = None
    process: TranslationBatchJobProcessResult | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class _SectionJobPrep:
    """Result of lock + section-shape + fence validation before budget."""

    early: SectionDrainResult | None = None
    record_id: UUID | None = None
    base_id: UUID | None = None
    generation: int | None = None


class SectionTranslationDrainService:
    """Targeted drain: shape/fence → budget → claim-by-id → batch process."""

    def __init__(
        self,
        *,
        pool: asyncpg.Pool | None = None,
        job_runtime: ReaderJobRuntime | None = None,
        translation_worker: TranslationWorkerService | None = None,
    ) -> None:
        self._pool = pool
        self._job_runtime = job_runtime or ReaderJobRuntime(pool=pool)
        self._translation_worker = translation_worker or TranslationWorkerService(
            pool=pool
        )

    def get_pool(self) -> asyncpg.Pool:
        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("Database pool not initialized")
        return pool

    async def process_job_id(
        self,
        *,
        job_id: UUID,
        lease_owner: str,
        lease_duration: timedelta = DEFAULT_SECTION_DRAIN_LEASE,
        retry_delay: timedelta = DEFAULT_TRANSLATION_RETRY_DELAY,
        expected_reading_record_id: UUID | None = None,
        expected_base_id: UUID | None = None,
        expected_generation: int | None = None,
    ) -> SectionDrainResult:
        # 1. Lock + validate section shape + fence BEFORE any budget force-fail.
        prep = await self._prepare_section_job(
            job_id,
            expected_reading_record_id=expected_reading_record_id,
            expected_base_id=expected_base_id,
            expected_generation=expected_generation,
        )
        if prep.early is not None:
            return prep.early
        assert prep.record_id is not None
        assert prep.base_id is not None
        assert prep.generation is not None
        record_id = prep.record_id
        base_id = prep.base_id
        generation = prep.generation

        # 2. Durable budget load + pre-check (no claim yet).
        async with self.get_pool().acquire() as conn:
            durable = await ExecutionBudget.load_durable(
                conn,
                record_id=record_id,
                base_id=base_id,
                expected_generation=generation,
            )
        budget = ExecutionBudget()
        budget.load_from_durable(durable)
        if budget.is_exhausted("translation"):
            force_result = await self._force_fail_budget_exhausted(job_id)
            if force_result == "superseded":
                return SectionDrainResult(
                    outcome=SectionDrainOutcome.SUPERSEDED,
                    job_id=job_id,
                    detail="stale_fence",
                )
            if force_result == "rejected":
                return SectionDrainResult(
                    outcome=SectionDrainOutcome.REJECTED,
                    job_id=job_id,
                    detail="not_section_shape",
                )
            return SectionDrainResult(
                outcome=SectionDrainOutcome.BUDGET_DENIED,
                job_id=job_id,
                detail=BUDGET_EXHAUSTED_FAILURE_CODE,
            )

        # 3. Atomic claim by id.
        claim = await self._job_runtime.claim_job_by_id(
            job_id=job_id,
            lease_owner=lease_owner,
            lease_duration=lease_duration,
            expected_reading_record_id=record_id,
            expected_base_id=base_id,
            expected_generation=generation,
            required_job_type=TRANSLATION_BATCH_JOB_TYPE,
            required_target_type=TRANSLATION_BATCH_TARGET_SCOPE,
            required_request_origin=SECTION_REQUEST_ORIGIN,
            required_fingerprint_base=TRANSLATION_SECTION_OPERATION_FINGERPRINT,
        )
        if claim is None:
            return await self._claim_miss_result(job_id)

        # 4. LLM / publish only after claim (worker path).
        process = await self._translation_worker.process_claimed_translation_batch_job(
            claim=claim,
            retry_delay=retry_delay,
            lease_duration=lease_duration,
        )
        outcome = _map_process_outcome(process)
        return SectionDrainResult(
            outcome=outcome,
            job_id=job_id,
            claim=claim,
            process=process,
        )

    async def _prepare_section_job(
        self,
        job_id: UUID,
        *,
        expected_reading_record_id: UUID | None,
        expected_base_id: UUID | None,
        expected_generation: int | None,
    ) -> _SectionJobPrep:
        """FOR UPDATE lock, section-shape check, and fence supersede.

        Non-section / wrong fingerprint / wrong type → REJECTED (no mutation).
        Stale fence → SUPERSEDED.
        Missing → NOT_FOUND.
        """
        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    SELECT *
                    FROM reader_jobs
                    WHERE id = $1
                    FOR UPDATE
                    """,
                    job_id,
                )
                if row is None:
                    return _SectionJobPrep(
                        early=SectionDrainResult(
                            outcome=SectionDrainOutcome.NOT_FOUND,
                            job_id=job_id,
                            detail="job_not_found",
                        )
                    )
                if not _row_is_section_shape(row):
                    return _SectionJobPrep(
                        early=SectionDrainResult(
                            outcome=SectionDrainOutcome.REJECTED,
                            job_id=job_id,
                            detail="not_section_shape",
                        )
                    )
                if (
                    expected_reading_record_id is not None
                    and row["reading_record_id"] != expected_reading_record_id
                ):
                    return _SectionJobPrep(
                        early=SectionDrainResult(
                            outcome=SectionDrainOutcome.REJECTED,
                            job_id=job_id,
                            detail="record_fence_mismatch",
                        )
                    )
                if expected_base_id is not None and row["base_id"] != expected_base_id:
                    return _SectionJobPrep(
                        early=SectionDrainResult(
                            outcome=SectionDrainOutcome.REJECTED,
                            job_id=job_id,
                            detail="base_fence_mismatch",
                        )
                    )
                if (
                    expected_generation is not None
                    and int(row["expected_generation"]) != expected_generation
                ):
                    return _SectionJobPrep(
                        early=SectionDrainResult(
                            outcome=SectionDrainOutcome.REJECTED,
                            job_id=job_id,
                            detail="generation_fence_mismatch",
                        )
                    )

                # Runtime source/route fence: supersede, never budget_exhausted.
                fence_error = await self._job_runtime._validate_fence(conn, row)
                if fence_error is not None:
                    await self._job_runtime._mark_job_superseded(
                        conn,
                        job_row=row,
                        rationale_code=fence_error,
                    )
                    return _SectionJobPrep(
                        early=SectionDrainResult(
                            outcome=SectionDrainOutcome.SUPERSEDED,
                            job_id=job_id,
                            detail=fence_error,
                        )
                    )

                return _SectionJobPrep(
                    record_id=row["reading_record_id"],
                    base_id=row["base_id"],
                    generation=int(row["expected_generation"]),
                )

    async def _force_fail_budget_exhausted(self, job_id: UUID) -> str:
        """Terminalize a still-queued **section** job when budget is exhausted.

        Returns:
          ``failed`` — transitioned to failed_terminal
          ``superseded`` — stale fence discovered under lock
          ``rejected`` — not section shape / not mutable
          ``noop`` — already terminal / missing

        UPDATE is gated by section shape predicates so ordinary jobs are never
        marked budget_exhausted by this path.
        """
        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    SELECT *
                    FROM reader_jobs
                    WHERE id = $1
                    FOR UPDATE
                    """,
                    job_id,
                )
                if row is None:
                    return "noop"
                if not _row_is_section_shape(row):
                    return "rejected"
                if row["status"] not in ("queued", "retry_later", "paused"):
                    return "noop"

                fence_error = await self._job_runtime._validate_fence(conn, row)
                if fence_error is not None:
                    await self._job_runtime._mark_job_superseded(
                        conn,
                        job_row=row,
                        rationale_code=fence_error,
                    )
                    return "superseded"

                updated = await conn.fetchrow(
                    """
                    UPDATE reader_jobs
                    SET status = $2,
                        failure_code = $3,
                        failure_message = $4,
                        rationale_code = $3,
                        lease_owner = NULL,
                        lease_token = NULL,
                        lease_expires_at = NULL,
                        claimed_at = NULL,
                        updated_at = NOW()
                    WHERE id = $1
                      AND status IN ('queued', 'retry_later', 'paused')
                      AND job_type = $5
                      AND target_type = $6
                      AND (input_json->>'request_origin') = $7
                      AND (
                          operation_fingerprint = $8
                          OR operation_fingerprint LIKE ($8 || ':%')
                      )
                    RETURNING id, run_id
                    """,
                    job_id,
                    STATUS_FAILED_TERMINAL,
                    BUDGET_EXHAUSTED_FAILURE_CODE,
                    BUDGET_EXHAUSTED_FAILURE_REASON,
                    TRANSLATION_BATCH_JOB_TYPE,
                    TRANSLATION_BATCH_TARGET_SCOPE,
                    SECTION_REQUEST_ORIGIN,
                    TRANSLATION_SECTION_OPERATION_FINGERPRINT,
                )
                if updated is None:
                    return "noop"

                # Align run terminal diagnostics with existing budget path.
                await conn.execute(
                    """
                    UPDATE reader_runs
                    SET status = 'failed',
                        failure_class = 'budget',
                        failure_code = $2,
                        failure_message = $3,
                        finished_at = COALESCE(finished_at, NOW()),
                        updated_at = NOW()
                    WHERE id = $1
                      AND status IN ('queued', 'running')
                    """,
                    updated["run_id"],
                    BUDGET_EXHAUSTED_FAILURE_CODE,
                    BUDGET_EXHAUSTED_FAILURE_REASON,
                )
                return "failed"

    async def _claim_miss_result(self, job_id: UUID) -> SectionDrainResult:
        """Map a claim miss to superseded / already_claimed / rejected."""
        async with self.get_pool().acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT status, rationale_code, job_type, target_type,
                       operation_fingerprint, input_json
                FROM reader_jobs
                WHERE id = $1
                """,
                job_id,
            )
        if row is None:
            return SectionDrainResult(
                outcome=SectionDrainOutcome.NOT_FOUND,
                job_id=job_id,
                detail="job_not_found",
            )
        status = str(row["status"])
        if status == "superseded":
            return SectionDrainResult(
                outcome=SectionDrainOutcome.SUPERSEDED,
                job_id=job_id,
                detail=row["rationale_code"],
            )
        if not _row_is_section_shape(row):
            return SectionDrainResult(
                outcome=SectionDrainOutcome.REJECTED,
                job_id=job_id,
                detail="not_section_shape",
            )
        return SectionDrainResult(
            outcome=SectionDrainOutcome.ALREADY_CLAIMED,
            job_id=job_id,
            detail="already_claimed_or_not_claimable",
        )


def _row_is_section_shape(row: Any) -> bool:
    """True iff job is translate_article/unit_range section_v1 + section fp."""
    if str(row["job_type"]) != TRANSLATION_BATCH_JOB_TYPE:
        return False
    if str(row["target_type"]) != TRANSLATION_BATCH_TARGET_SCOPE:
        return False
    fp = str(row["operation_fingerprint"] or "")
    if not _fingerprint_matches_base(fp, TRANSLATION_SECTION_OPERATION_FINGERPRINT):
        return False
    input_json = row["input_json"] or {}
    if not isinstance(input_json, dict):
        return False
    return input_json.get("request_origin") == SECTION_REQUEST_ORIGIN


def _map_process_outcome(
    process: TranslationBatchJobProcessResult,
) -> SectionDrainOutcome:
    text = str(process.status or "").lower()
    if "succeed" in text:
        return SectionDrainOutcome.SUCCEEDED
    if "supersed" in text:
        return SectionDrainOutcome.SUPERSEDED
    if "retry" in text:
        return SectionDrainOutcome.RETRY_LATER
    if "fail" in text:
        return SectionDrainOutcome.FAILED
    return SectionDrainOutcome.FAILED


__all__ = [
    "DEFAULT_SECTION_DRAIN_LEASE",
    "SectionDrainOutcome",
    "SectionDrainResult",
    "SectionTranslationDrainService",
]
