"""Artifact materialization worker execution shell.

Claims ``extracted_artifact_materialization`` reader_jobs (enqueued after the
extraction worker persists the confirmed-source document), loads the job
payload, and calls
``ExtractedArtifactMaterializationService.materialize_extracted_artifact_in_transaction``
to freeze a stable document / create a candidate / mark action_required.

Transaction model: the materialization + job transition to ``succeeded`` run
in the SAME transaction (via the caller-managed
``materialize_extracted_artifact_in_transaction`` helper). This avoids state
drift where materialization succeeds but the job transition fails. Typed
validation errors from I3N map to ``superseded`` (record has advanced past
materialization) or ``failed_terminal`` (validation/business failure).
Retryable DB/runtime exceptions are NOT caught — they propagate so the
stale-lease recovery path (``recover_stale_leases``) can requeue the job
when the lease expires, per the existing job_runtime transition pattern.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg

from app.database import connection as db_connection

from .artifact_extraction_worker import (
    MATERIALIZATION_JOB_SOURCE,
    MATERIALIZATION_JOB_TYPE,
    MATERIALIZATION_OPERATION_FINGERPRINT,
    MATERIALIZATION_TARGET_TYPE,
)
from .extracted_artifact_materialization_service import (
    MATERIALIZATION_SUPERSEDED_REASONS,
    ExtractedArtifactMaterializationError,
    ExtractedArtifactMaterializationService,
    MaterializationResult,
)
from .job_runtime import (
    ClaimResult,
    FenceViolationError,
    ReaderJobRuntime,
    _assert_lease_valid,
    mark_reader_run_running,
    mark_reader_run_status,
)

DEFAULT_MATERIALIZATION_RETRY_DELAY = timedelta(minutes=5)
MATERIALIZATION_WORKFLOW_VERSION = "artifact-materialization-worker"

FAILURE_CODE_INPUT_JSON_INVALID = "input_json_invalid"
FAILURE_CODE_MATERIALIZE_FAILED = "materialize_failed"
FAILURE_CODE_MATERIALIZE_EXECUTION_FAILED = "materialize_execution_failed"


@dataclass(frozen=True, slots=True)
class MaterializationJobContext:
    """Parsed materialization job payload (validated against claim)."""

    job_id: UUID
    run_id: UUID
    reading_record_id: UUID
    user_id: UUID
    original_input_id: UUID
    source_artifact_id: UUID
    expected_generation: int
    operation_fingerprint: str


@dataclass(frozen=True, slots=True)
class MaterializationJobProcessResult:
    """Returned by :meth:`process_next`."""

    claim: ClaimResult
    context: MaterializationJobContext | None
    status: str
    outcome: str | None = None
    stable_document_id: UUID | None = None
    base_id: UUID | None = None
    candidate_document_id: UUID | None = None


class ArtifactMaterializationWorkerService:
    """Claims and executes ``extracted_artifact_materialization`` jobs."""

    def __init__(
        self,
        *,
        pool: asyncpg.Pool | None = None,
        job_runtime: ReaderJobRuntime | None = None,
        materialization_service: ExtractedArtifactMaterializationService | None = None,
    ) -> None:
        self._pool = pool
        self._job_runtime = job_runtime or ReaderJobRuntime(pool=pool)
        self._materialization_service = (
            materialization_service
            or ExtractedArtifactMaterializationService(pool=pool)
        )

    def get_pool(self) -> asyncpg.Pool:
        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("Database pool not initialized")
        return pool

    async def process_next(
        self,
        *,
        lease_owner: str,
        lease_duration: timedelta,
        retry_delay: timedelta = DEFAULT_MATERIALIZATION_RETRY_DELAY,
    ) -> MaterializationJobProcessResult | None:
        """Claim and process the next materialization job.

        Returns ``None`` if no job is available. On success, transitions the
        job to ``succeeded`` within the same transaction as the materialization
        writes. On typed I3N validation errors, transitions to ``superseded``
        or ``failed_terminal`` based on ``reason_code``. Fence violations map
        to ``superseded``. Retryable DB/runtime exceptions propagate so stale
        lease recovery can requeue the job.
        """
        claim = await self._job_runtime.claim_next_job(
            lease_owner=lease_owner,
            lease_duration=lease_duration,
            job_type=MATERIALIZATION_JOB_TYPE,
            target_type=MATERIALIZATION_TARGET_TYPE,
            operation_fingerprint=MATERIALIZATION_OPERATION_FINGERPRINT,
        )
        if claim is None:
            return None
        if (
            claim.job_type != MATERIALIZATION_JOB_TYPE
            or claim.target_type != MATERIALIZATION_TARGET_TYPE
            or claim.operation_fingerprint != MATERIALIZATION_OPERATION_FINGERPRINT
        ):
            raise RuntimeError(
                "materialization worker claimed unsupported job "
                f"{claim.job_type}/{claim.target_type}/"
                f"{claim.operation_fingerprint}"
            )
        await self._mark_run_running(claim.run_id)
        return await self._process_claimed_job(claim=claim, retry_delay=retry_delay)

    async def _process_claimed_job(
        self,
        *,
        claim: ClaimResult,
        retry_delay: timedelta,
    ) -> MaterializationJobProcessResult:
        context: MaterializationJobContext | None = None

        try:
            context = await self._load_job_context(claim)
            result = await self._persist_and_succeed(claim=claim, context=context)
            await self._mark_run_status(
                claim.run_id,
                status="completed",
                failure_class=None,
                failure_code=None,
                finished_at=datetime.now(UTC),
            )
            return MaterializationJobProcessResult(
                claim=claim,
                context=context,
                status="succeeded",
                outcome=result.outcome,
                stable_document_id=result.stable_document_id,
                base_id=result.base_id,
                candidate_document_id=result.candidate_document_id,
            )
        except FenceViolationError:
            await self._job_runtime.transition(
                job_id=claim.job_id,
                target_status="superseded",
                lease_token=claim.lease_token,
                rationale_code="publish_fence_failed",
            )
            await self._mark_run_status(
                claim.run_id,
                status="superseded",
                failure_class="publish_guard",
                failure_code="publish_fence_failed",
                finished_at=datetime.now(UTC),
            )
            return MaterializationJobProcessResult(
                claim=claim, context=context, status="superseded",
            )
        except _InputJsonError as exc:
            # Malformed job payload — won't succeed on retry. Fail terminal.
            await self._fail_terminal(
                claim=claim,
                failure_class="validation",
                failure_code=FAILURE_CODE_INPUT_JSON_INVALID,
                failure_message=str(exc),
                rationale_code=FAILURE_CODE_INPUT_JSON_INVALID,
            )
            return MaterializationJobProcessResult(
                claim=claim, context=context, status="failed_terminal",
            )
        except ExtractedArtifactMaterializationError as exc:
            if exc.reason_code in MATERIALIZATION_SUPERSEDED_REASONS:
                await self._job_runtime.transition(
                    job_id=claim.job_id,
                    target_status="superseded",
                    lease_token=claim.lease_token,
                    rationale_code=exc.reason_code,
                )
                await self._mark_run_status(
                    claim.run_id,
                    status="superseded",
                    failure_class="materialize_superseded",
                    failure_code=exc.reason_code,
                    finished_at=datetime.now(UTC),
                )
                return MaterializationJobProcessResult(
                    claim=claim, context=context, status="superseded",
                )
            await self._fail_terminal(
                claim=claim,
                failure_class="materialize",
                failure_code=exc.reason_code,
                failure_message=str(exc),
                rationale_code=exc.reason_code,
            )
            return MaterializationJobProcessResult(
                claim=claim, context=context, status="failed_terminal",
            )
        # Retryable DB/runtime exceptions (asyncpg connection errors,
        # deadlocks, serialization failures) are NOT caught here. They
        # propagate so ``recover_stale_leases`` can requeue the job when
        # the lease expires — per the existing job_runtime transition
        # pattern. The job stays ``claimed`` until the lease expires, then
        # is requeued (or moved to failed_terminal after max_attempts).

    async def _load_job_context(
        self,
        claim: ClaimResult,
    ) -> MaterializationJobContext:
        """Load and validate the materialization job payload.

        Validates that ``input_json`` IDs match the claim's
        ``target_key`` (source_artifact_id) and ``reading_record_id``, and
        that ``expected_generation`` matches. Mismatches fail closed.
        """
        async with self.get_pool().acquire() as conn:
            job_row = await conn.fetchrow(
                """
                SELECT id, run_id, reading_record_id, user_id, base_id,
                       expected_generation, operation_fingerprint, input_json
                FROM reader_jobs
                WHERE id = $1
                """,
                claim.job_id,
            )
            if job_row is None:
                raise LookupError(f"reader job {claim.job_id} not found")

            input_json = job_row["input_json"]
            source = _get_required_str(input_json, "source")
            if source != MATERIALIZATION_JOB_SOURCE:
                raise _InputJsonError(
                    f"input_json.source must be {MATERIALIZATION_JOB_SOURCE!r}; "
                    f"got {source!r}"
                )

            input_reading_record_id = _get_required_uuid(
                input_json, "reading_record_id",
            )
            input_original_input_id = _get_required_uuid(
                input_json, "original_input_id",
            )
            input_source_artifact_id = _get_required_uuid(
                input_json, "source_artifact_id",
            )
            input_expected_generation = _get_required_int(
                input_json, "expected_generation",
            )

            if input_reading_record_id != claim.reading_record_id:
                raise _InputJsonError(
                    f"input_json.reading_record_id {input_reading_record_id} "
                    f"does not match claim.reading_record_id "
                    f"{claim.reading_record_id}"
                )

            try:
                target_key_artifact_id = UUID(claim.target_key)
            except (ValueError, TypeError) as exc:
                raise _InputJsonError(
                    f"claim.target_key is not a valid UUID: "
                    f"{claim.target_key!r}"
                ) from exc
            if target_key_artifact_id != input_source_artifact_id:
                raise _InputJsonError(
                    f"input_json.source_artifact_id {input_source_artifact_id} "
                    f"does not match claim.target_key {claim.target_key!r}"
                )

            if input_expected_generation != claim.expected_generation:
                raise _InputJsonError(
                    f"input_json.expected_generation "
                    f"{input_expected_generation} does not match "
                    f"claim.expected_generation {claim.expected_generation}"
                )

            return MaterializationJobContext(
                job_id=claim.job_id,
                run_id=claim.run_id,
                reading_record_id=claim.reading_record_id,
                user_id=job_row["user_id"],
                original_input_id=input_original_input_id,
                source_artifact_id=input_source_artifact_id,
                expected_generation=claim.expected_generation,
                operation_fingerprint=str(job_row["operation_fingerprint"]),
            )

    async def _persist_and_succeed(
        self,
        *,
        claim: ClaimResult,
        context: MaterializationJobContext,
    ) -> MaterializationResult:
        """Run materialization + job transition to succeeded in one txn.

        The materialization service runs via its caller-managed transaction
        helper so the I3N writes (stable document freeze, candidate insert,
        or action_required update) and the job transition to ``succeeded``
        commit atomically. If materialization raises, the transaction rolls
        back and the outer handler transitions the job to ``superseded`` or
        ``failed_terminal``.
        """
        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                job_row = await conn.fetchrow(
                    "SELECT * FROM reader_jobs WHERE id = $1 FOR UPDATE",
                    claim.job_id,
                )
                if job_row is None:
                    raise LookupError(f"reader job {claim.job_id} not found")
                if job_row["status"] != "claimed":
                    raise ValueError(
                        "materialization persist requires a claimed job"
                    )
                _assert_lease_valid(job_row, claim.job_id, claim.lease_token)

                fence_error = await self._job_runtime._validate_fence(  # type: ignore[attr-defined]
                    conn, job_row,
                )
                if fence_error is not None:
                    raise FenceViolationError(
                        f"publish fence failed for job {claim.job_id}: "
                        f"{fence_error}"
                    )

                result = await self._materialization_service \
                    .materialize_extracted_artifact_in_transaction(
                        conn,
                        reading_record_id=context.reading_record_id,
                        original_input_id=context.original_input_id,
                        source_artifact_id=context.source_artifact_id,
                        user_id=context.user_id,
                        expected_generation=context.expected_generation,
                    )

                output_ref = _build_output_ref(result)
                updated = await self._job_runtime._apply_transition(  # type: ignore[attr-defined]
                    conn,
                    job_row=job_row,
                    target_status="succeeded",
                    available_at=None,
                    pause_owner=None,
                    output_ref=output_ref,
                    failure_class=None,
                    failure_code=None,
                    failure_message=None,
                    rationale_code=None,
                )
                await self._job_runtime._insert_job_event(  # type: ignore[attr-defined]
                    conn,
                    reading_record_id=updated["reading_record_id"],
                    run_id=updated["run_id"],
                    job_id=updated["id"],
                    event_type="job_succeeded",
                    payload={
                        "previous_status": "claimed",
                        "target_status": "succeeded",
                        "output_ref": output_ref,
                    },
                )

        return result

    async def _fail_terminal(
        self,
        *,
        claim: ClaimResult,
        failure_class: str,
        failure_code: str,
        failure_message: str,
        rationale_code: str | None = None,
    ) -> None:
        await self._job_runtime.transition(
            job_id=claim.job_id,
            target_status="failed_terminal",
            lease_token=claim.lease_token,
            failure_class=failure_class,
            failure_code=failure_code,
            failure_message=failure_message,
            rationale_code=rationale_code or failure_code,
        )
        await self._mark_run_status(
            claim.run_id,
            status="failed_terminal",
            failure_class=failure_class,
            failure_code=failure_code,
            finished_at=datetime.now(UTC),
        )

    async def _mark_run_running(self, run_id: UUID) -> None:
        async with self.get_pool().acquire() as conn:
            await mark_reader_run_running(conn, run_id)

    async def _mark_run_status(
        self,
        run_id: UUID,
        *,
        status: str,
        failure_class: str | None,
        failure_code: str | None,
        finished_at: datetime | None,
    ) -> None:
        async with self.get_pool().acquire() as conn:
            await mark_reader_run_status(
                conn,
                run_id,
                status=status,
                failure_class=failure_class,
                failure_code=failure_code,
                finished_at=finished_at,
            )


class _InputJsonError(ValueError):
    """Raised when the materialization job payload fails validation.

    Mapped to ``failed_terminal`` with ``failure_code=input_json_invalid``.
    """


def _build_output_ref(result: MaterializationResult) -> dict[str, Any]:
    """Build the ``output_ref_json`` payload for a succeeded materialization job."""
    output_ref: dict[str, Any] = {
        "outcome": result.outcome,
        "reading_record_id": str(result.reading_record_id),
        "original_input_id": str(result.original_input_id),
        "source_artifact_id": str(result.source_artifact_id),
        "record_generation": result.record_generation,
        "suitability_outcome": result.suitability.outcome,
    }
    if result.stable_document_id is not None:
        output_ref["stable_document_id"] = str(result.stable_document_id)
    if result.base_id is not None:
        output_ref["base_id"] = str(result.base_id)
    if result.article_ready_event_id is not None:
        output_ref["article_ready_event_id"] = str(result.article_ready_event_id)
    if result.article_ready_sequence is not None:
        output_ref["article_ready_sequence"] = result.article_ready_sequence
    if result.candidate_document_id is not None:
        output_ref["candidate_document_id"] = str(result.candidate_document_id)
    if result.block_count is not None:
        output_ref["block_count"] = result.block_count
    if result.flags:
        output_ref["flags"] = list(result.flags)
    if result.reasons:
        output_ref["reasons"] = list(result.reasons)
    return output_ref


def _get_required_str(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise _InputJsonError(f"input_json.{key} must be a non-empty string")
    return value


def _get_required_uuid(
    data: Mapping[str, Any],
    key: str,
) -> UUID:
    value = data.get(key)
    if not isinstance(value, str):
        raise _InputJsonError(f"input_json.{key} must be a UUID string")
    try:
        return UUID(value)
    except ValueError as exc:
        raise _InputJsonError(
            f"input_json.{key} is not a valid UUID: {value!r}"
        ) from exc


def _get_required_int(
    data: Mapping[str, Any],
    key: str,
) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise _InputJsonError(f"input_json.{key} must be an integer")
    return value
