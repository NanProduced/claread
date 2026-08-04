"""Artifact extraction worker execution shell (D6-I3L).

Claims ``input_artifact_extraction`` reader_jobs, validates the artifact input
contract, calls an injectable :class:`ArtifactExtractionProvider`, and persists
the extracted text into ``confirmed_source_documents`` (L2: the single full-body
carrier; ``original_inputs`` keeps lineage metadata only). No OSS download, OCR,
or PDF parsing is performed here — the default provider fails closed. Real
providers (OSS/qwen-OCR/PDF parser) are injected in production or faked in
tests.

This worker does NOT create candidate documents, freeze stable documents, or
publish ``article_ready`` events. It only inserts the confirmed-source row
so downstream materialization flows can pick the record up later.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID, uuid4

import asyncpg

from app.database import connection as db_connection
from app.database.json_compat import jsonb_param

from .artifact_input_application_service import (
    EXTRACTION_JOB_SOURCE,
    EXTRACTION_JOB_TYPE,
    EXTRACTION_OPERATION_FINGERPRINT,
    EXTRACTION_TARGET_TYPE,
)
from .confirmed_source_repository import (
    insert_confirmed_source,
)
from .extracted_artifact_materialization_service import (
    _normalize_source_text,
)
from .job_runtime import (
    ClaimResult,
    FenceViolationError,
    ReaderJobRuntime,
    _assert_lease_valid,
)

DEFAULT_EXTRACTION_RETRY_DELAY = timedelta(minutes=5)
EXTRACTION_WORKFLOW_VERSION = "d6-i3l-extraction-worker"

# D6-I3O: Materialization job contract — enqueued after extraction succeeds.
MATERIALIZATION_JOB_TYPE = "extracted_artifact_materialization"
MATERIALIZATION_TARGET_TYPE = "record"
MATERIALIZATION_OPERATION_FINGERPRINT = "extracted_artifact_materialization_v1"
MATERIALIZATION_JOB_SOURCE = "artifact_extraction_worker"
MATERIALIZATION_RUN_TYPE = "extracted_artifact_materialization"
MATERIALIZATION_POLICY_VERSION = "reader_extracted_artifact_materialization_v1"
MATERIALIZATION_TRIGGER_KIND = "system"
DEFAULT_MATERIALIZATION_MAX_ATTEMPTS = 3

FAILURE_CODE_EXTRACTION_EMPTY_TEXT = "extraction_empty_text"
FAILURE_CODE_INPUT_JSON_INVALID = "input_json_invalid"
FAILURE_CODE_RECORD_NOT_FOUND = "record_not_found"
FAILURE_CODE_RECORD_NOT_ACTIVE = "record_not_active"
FAILURE_CODE_STALE_GENERATION = "stale_generation"
FAILURE_CODE_ORIGINAL_INPUT_NOT_FOUND = "original_input_not_found"
FAILURE_CODE_ARTIFACT_NOT_FOUND = "artifact_not_found"
FAILURE_CODE_ARTIFACT_NOT_AVAILABLE = "artifact_not_available"
FAILURE_CODE_ARTIFACT_NOT_BOUND = "artifact_not_bound"
FAILURE_CODE_METADATA_CONFLICT = "metadata_conflict"


@dataclass(frozen=True, slots=True)
class ArtifactExtractionJobContext:
    job_id: UUID
    run_id: UUID
    reading_record_id: UUID
    user_id: UUID
    original_input_id: UUID
    source_artifact_id: UUID
    artifact_kind: str
    storage_provider: str
    bucket: str
    endpoint: str
    object_key: str
    content_type: str | None
    byte_size: int | None
    content_sha256: str | None
    source_filename: str
    expected_generation: int
    operation_fingerprint: str


@dataclass(frozen=True, slots=True)
class ArtifactExtractionResult:
    extracted_text: str
    extractor_name: str
    quality: dict[str, Any] | None = None
    warnings: list[str] | None = None


@dataclass(frozen=True, slots=True)
class ArtifactExtractionJobProcessResult:
    claim: ClaimResult
    context: ArtifactExtractionJobContext | None
    status: str
    extracted_text: str | None = None
    content_sha256: str | None = None


class ArtifactExtractionError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        failure_class: str,
        failure_code: str,
        rationale_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.failure_class = failure_class
        self.failure_code = failure_code
        self.rationale_code = rationale_code or failure_code


class ArtifactExtractionProvider(Protocol):
    async def extract(
        self,
        context: ArtifactExtractionJobContext,
    ) -> ArtifactExtractionResult: ...


class UnconfiguredArtifactExtractionProvider:
    """Default provider that fails closed — no network, no OCR, no PDF parsing."""

    async def extract(
        self,
        context: ArtifactExtractionJobContext,
    ) -> ArtifactExtractionResult:
        raise ArtifactExtractionError(
            (
                "artifact extraction provider is not configured; inject an explicit "
                "fake provider for tests or wire a real OSS/OCR/PDF provider for "
                "production"
            ),
            retryable=False,
            failure_class="configuration",
            failure_code="extraction_provider_unconfigured",
        )


class ArtifactExtractionWorkerService:
    def __init__(
        self,
        *,
        pool: asyncpg.Pool | None = None,
        job_runtime: ReaderJobRuntime | None = None,
        provider: ArtifactExtractionProvider | None = None,
    ) -> None:
        self._pool = pool
        self._job_runtime = job_runtime or ReaderJobRuntime(pool=pool)
        self._provider = provider or UnconfiguredArtifactExtractionProvider()

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
        retry_delay: timedelta = DEFAULT_EXTRACTION_RETRY_DELAY,
    ) -> ArtifactExtractionJobProcessResult | None:
        claim = await self._job_runtime.claim_next_job(
            lease_owner=lease_owner,
            lease_duration=lease_duration,
            job_type=EXTRACTION_JOB_TYPE,
            target_type=EXTRACTION_TARGET_TYPE,
            operation_fingerprint=EXTRACTION_OPERATION_FINGERPRINT,
        )
        if claim is None:
            return None
        if (
            claim.job_type != EXTRACTION_JOB_TYPE
            or claim.target_type != EXTRACTION_TARGET_TYPE
            or claim.operation_fingerprint != EXTRACTION_OPERATION_FINGERPRINT
        ):
            raise RuntimeError(
                "extraction worker claimed unsupported job "
                f"{claim.job_type}/{claim.target_type}/{claim.operation_fingerprint}"
            )
        await self._mark_run_running(claim.run_id)
        return await self._process_claimed_job(claim=claim, retry_delay=retry_delay)

    async def _process_claimed_job(
        self,
        *,
        claim: ClaimResult,
        retry_delay: timedelta,
    ) -> ArtifactExtractionJobProcessResult:
        context: ArtifactExtractionJobContext | None = None

        try:
            context = await self._load_job_context(claim)
            result = await self._provider.extract(context)

            if not result.extracted_text or not result.extracted_text.strip():
                await self._fail_terminal(
                    claim=claim,
                    failure_class="extraction",
                    failure_code=FAILURE_CODE_EXTRACTION_EMPTY_TEXT,
                    failure_message="extraction provider returned empty text",
                )
                return ArtifactExtractionJobProcessResult(
                    claim=claim, context=context, status="failed_terminal",
                )

            content_sha256 = await self._persist_and_succeed(
                claim=claim, context=context, result=result,
            )
            await self._mark_run_status(
                claim.run_id,
                status="completed",
                failure_class=None,
                failure_code=None,
                finished_at=datetime.now(UTC),
            )
            return ArtifactExtractionJobProcessResult(
                claim=claim,
                context=context,
                status="succeeded",
                extracted_text=result.extracted_text,
                content_sha256=content_sha256,
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
            raise
        except ArtifactExtractionError as exc:
            if exc.retryable:
                available_at = datetime.now(UTC) + retry_delay
                await self._job_runtime.transition(
                    job_id=claim.job_id,
                    target_status="retry_later",
                    lease_token=claim.lease_token,
                    available_at=available_at,
                    failure_class=exc.failure_class,
                    failure_code=exc.failure_code,
                    failure_message=str(exc),
                    rationale_code=exc.rationale_code,
                )
                await self._mark_run_status(
                    claim.run_id,
                    status="failed_retryable",
                    failure_class=exc.failure_class,
                    failure_code=exc.failure_code,
                    finished_at=None,
                )
                return ArtifactExtractionJobProcessResult(
                    claim=claim, context=context, status="retry_later",
                )
            await self._fail_terminal(
                claim=claim,
                failure_class=exc.failure_class,
                failure_code=exc.failure_code,
                failure_message=str(exc),
                rationale_code=exc.rationale_code,
            )
            return ArtifactExtractionJobProcessResult(
                claim=claim, context=context, status="failed_terminal",
            )
        except Exception as exc:
            await self._fail_terminal(
                claim=claim,
                failure_class="extraction_execution",
                failure_code=type(exc).__name__,
                failure_message=str(exc),
                rationale_code="extraction_execution_failed",
            )
            return ArtifactExtractionJobProcessResult(
                claim=claim, context=context, status="failed_terminal",
            )

    async def _load_job_context(
        self,
        claim: ClaimResult,
    ) -> ArtifactExtractionJobContext:
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
            if source != EXTRACTION_JOB_SOURCE:
                raise ArtifactExtractionError(
                    f"input_json.source must be {EXTRACTION_JOB_SOURCE!r}; got {source!r}",
                    retryable=False,
                    failure_class="validation",
                    failure_code=FAILURE_CODE_INPUT_JSON_INVALID,
                )

            input_reading_record_id = _get_required_uuid(
                input_json, "reading_record_id", FAILURE_CODE_INPUT_JSON_INVALID,
            )
            input_original_input_id = _get_required_uuid(
                input_json, "original_input_id", FAILURE_CODE_INPUT_JSON_INVALID,
            )
            input_source_artifact_id = _get_required_uuid(
                input_json, "source_artifact_id", FAILURE_CODE_INPUT_JSON_INVALID,
            )

            if input_reading_record_id != claim.reading_record_id:
                raise ArtifactExtractionError(
                    "input_json.reading_record_id does not match claim",
                    retryable=False,
                    failure_class="validation",
                    failure_code=FAILURE_CODE_INPUT_JSON_INVALID,
                )

            # I3K enqueue contract sets target_key=str(artifact_id). A mismatch
            # means the job was malformed (or tampered with): claim.target_key
            # points at artifact A but input_json asks to process artifact B.
            # Fail closed to prevent cross-artifact contamination.
            try:
                target_key_artifact_id = UUID(claim.target_key)
            except (ValueError, TypeError) as exc:
                raise ArtifactExtractionError(
                    f"claim.target_key is not a valid UUID: {claim.target_key!r}",
                    retryable=False,
                    failure_class="validation",
                    failure_code=FAILURE_CODE_INPUT_JSON_INVALID,
                ) from exc
            if target_key_artifact_id != input_source_artifact_id:
                raise ArtifactExtractionError(
                    f"input_json.source_artifact_id {input_source_artifact_id} "
                    f"does not match claim.target_key {claim.target_key!r}",
                    retryable=False,
                    failure_class="validation",
                    failure_code=FAILURE_CODE_INPUT_JSON_INVALID,
                )

            user_id = job_row["user_id"]
            reading_record_id = claim.reading_record_id
            original_input_id = input_original_input_id
            source_artifact_id = input_source_artifact_id
            expected_generation = int(claim.expected_generation)

            # Validate reading_records: active, generation matches, not deleted.
            record_row = await conn.fetchrow(
                """
                SELECT generation, lifecycle_status, deleted_at
                FROM reading_records
                WHERE id = $1
                """,
                reading_record_id,
            )
            if record_row is None or record_row["deleted_at"] is not None:
                raise ArtifactExtractionError(
                    f"reading_record {reading_record_id} not found or deleted",
                    retryable=False,
                    failure_class="validation",
                    failure_code=FAILURE_CODE_RECORD_NOT_FOUND,
                )
            if record_row["lifecycle_status"] != "active":
                raise ArtifactExtractionError(
                    f"reading_record {reading_record_id} lifecycle_status is "
                    f"{record_row['lifecycle_status']!r}, expected 'active'",
                    retryable=False,
                    failure_class="validation",
                    failure_code=FAILURE_CODE_RECORD_NOT_ACTIVE,
                )
            if int(record_row["generation"]) != expected_generation:
                raise ArtifactExtractionError(
                    f"reading_record generation {record_row['generation']} != "
                    f"expected {expected_generation}",
                    retryable=False,
                    failure_class="validation",
                    failure_code=FAILURE_CODE_STALE_GENERATION,
                )

            # Validate original_inputs: belongs to record/user.
            input_row = await conn.fetchrow(
                """
                SELECT id
                FROM original_inputs
                WHERE id = $1
                  AND reading_record_id = $2
                  AND user_id = $3
                """,
                original_input_id,
                reading_record_id,
                user_id,
            )
            if input_row is None:
                raise ArtifactExtractionError(
                    f"original_input {original_input_id} not found for "
                    f"record {reading_record_id} / user {user_id}",
                    retryable=False,
                    failure_class="validation",
                    failure_code=FAILURE_CODE_ORIGINAL_INPUT_NOT_FOUND,
                )

            # Validate source_artifacts: belongs to user, status='available',
            # bound to this record/input.
            artifact_row = await conn.fetchrow(
                """
                SELECT id, status, reading_record_id, original_input_id,
                       artifact_kind, storage_provider, bucket, endpoint,
                       object_key, content_type, byte_size, content_sha256,
                       source_filename
                FROM source_artifacts
                WHERE id = $1
                  AND user_id = $2
                  AND deleted_at IS NULL
                """,
                source_artifact_id,
                user_id,
            )
            if artifact_row is None:
                raise ArtifactExtractionError(
                    f"source_artifact {source_artifact_id} not found",
                    retryable=False,
                    failure_class="validation",
                    failure_code=FAILURE_CODE_ARTIFACT_NOT_FOUND,
                )
            if artifact_row["status"] != "available":
                raise ArtifactExtractionError(
                    f"source_artifact status is {artifact_row['status']!r}, "
                    f"expected 'available'",
                    retryable=False,
                    failure_class="validation",
                    failure_code=FAILURE_CODE_ARTIFACT_NOT_AVAILABLE,
                )
            if (
                artifact_row["reading_record_id"] != reading_record_id
                or artifact_row["original_input_id"] != original_input_id
            ):
                raise ArtifactExtractionError(
                    f"source_artifact {source_artifact_id} is not bound to "
                    f"record {reading_record_id} / input {original_input_id}",
                    retryable=False,
                    failure_class="validation",
                    failure_code=FAILURE_CODE_ARTIFACT_NOT_BOUND,
                )

            return ArtifactExtractionJobContext(
                job_id=claim.job_id,
                run_id=claim.run_id,
                reading_record_id=reading_record_id,
                user_id=user_id,
                original_input_id=original_input_id,
                source_artifact_id=source_artifact_id,
                artifact_kind=artifact_row["artifact_kind"],
                storage_provider=artifact_row["storage_provider"],
                bucket=artifact_row["bucket"],
                endpoint=artifact_row["endpoint"],
                object_key=artifact_row["object_key"],
                content_type=artifact_row["content_type"],
                byte_size=artifact_row["byte_size"],
                content_sha256=artifact_row["content_sha256"],
                source_filename=artifact_row["source_filename"],
                expected_generation=expected_generation,
                operation_fingerprint=str(job_row["operation_fingerprint"]),
            )

    async def _persist_and_succeed(
        self,
        *,
        claim: ClaimResult,
        context: ArtifactExtractionJobContext,
        result: ArtifactExtractionResult,
    ) -> str:
        """Persist extraction result and transition job to succeeded in one txn."""
        extracted_text = result.extracted_text
        content_sha256 = hashlib.sha256(
            extracted_text.encode("utf-8")
        ).hexdigest()
        text_length = len(extracted_text)
        output_ref: dict[str, Any] = {
            "original_input_id": str(context.original_input_id),
            "content_sha256": content_sha256,
            "text_length": text_length,
        }

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
                        "extraction persist requires a claimed job"
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

                input_row = await conn.fetchrow(
                    """
                    SELECT id, metadata_json
                    FROM original_inputs
                    WHERE id = $1
                      AND reading_record_id = $2
                      AND user_id = $3
                    FOR UPDATE
                    """,
                    context.original_input_id,
                    context.reading_record_id,
                    context.user_id,
                )
                if input_row is None:
                    raise ArtifactExtractionError(
                        f"original_input {context.original_input_id} not found "
                        f"during persistence",
                        retryable=False,
                        failure_class="validation",
                        failure_code=FAILURE_CODE_ORIGINAL_INPUT_NOT_FOUND,
                    )

                existing_metadata = _coerce_metadata_dict(
                    input_row["metadata_json"],
                )
                extraction_metadata: dict[str, Any] = {
                    "extraction_status": "succeeded",
                    "extractor_name": result.extractor_name,
                }
                if result.quality is not None:
                    extraction_metadata["extraction_quality"] = result.quality
                if result.warnings is not None:
                    extraction_metadata["extraction_warnings"] = list(
                        result.warnings
                    )
                merged_metadata = _merge_metadata_strict(
                    existing=existing_metadata,
                    incoming=extraction_metadata,
                )

                await conn.execute(
                    """
                    UPDATE original_inputs
                    SET metadata_json = $2::jsonb
                    WHERE id = $1
                    """,
                    context.original_input_id,
                    jsonb_param(merged_metadata),
                )

                # L2：worker 不再 UPDATE original_inputs.source_text /
                # content_sha256——正文唯一载体是
                # confirmed_source_documents。同一 worker 事务内插入
                # source 行（revision=1, edit_source='extraction'，
                # 正文为规范化后的抽取文本，与 materialization 的
                # 解析输入严格同源）。
                await insert_confirmed_source(
                    conn,
                    source_document_id=uuid4(),
                    record_id=context.reading_record_id,
                    user_id=context.user_id,
                    generation=context.expected_generation,
                    original_input_id=context.original_input_id,
                    markdown_text=_normalize_source_text(extracted_text),
                    edit_source="extraction",
                    now=datetime.now(UTC),
                )

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

                # D6-I3O: Enqueue materialization job in the same transaction
                # so extraction success + materialization enqueue are atomic.
                # If the enqueue fails, the whole extraction persist rolls back
                # and the extraction job stays claimed (retryable later).
                await _enqueue_materialization_job(
                    conn,
                    reading_record_id=context.reading_record_id,
                    user_id=context.user_id,
                    original_input_id=context.original_input_id,
                    source_artifact_id=context.source_artifact_id,
                    expected_generation=context.expected_generation,
                )

        return content_sha256

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


def _get_required_str(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ArtifactExtractionError(
            f"input_json.{key} must be a non-empty string",
            retryable=False,
            failure_class="validation",
            failure_code=FAILURE_CODE_INPUT_JSON_INVALID,
        )
    return value


def _get_required_uuid(
    data: Mapping[str, Any],
    key: str,
    failure_code: str,
) -> UUID:
    value = data.get(key)
    if not isinstance(value, str):
        raise ArtifactExtractionError(
            f"input_json.{key} must be a UUID string",
            retryable=False,
            failure_class="validation",
            failure_code=failure_code,
        )
    try:
        return UUID(value)
    except ValueError as exc:
        raise ArtifactExtractionError(
            f"input_json.{key} is not a valid UUID: {value!r}",
            retryable=False,
            failure_class="validation",
            failure_code=failure_code,
        ) from exc


def _coerce_metadata_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    raise ArtifactExtractionError(
        "original_inputs.metadata_json is not a JSON object",
        retryable=False,
        failure_class="validation",
        failure_code=FAILURE_CODE_METADATA_CONFLICT,
    )


def _merge_metadata_strict(
    *,
    existing: dict[str, Any],
    incoming: dict[str, Any],
) -> dict[str, Any]:
    """Merge incoming into existing; fail closed on key conflict.

    If an incoming key already exists in ``existing`` with a different value,
    raise ``ArtifactExtractionError`` so we never silently overwrite prior
    extraction metadata.
    """
    merged = dict(existing)
    for key, value in incoming.items():
        if key in merged and merged[key] != value:
            raise ArtifactExtractionError(
                f"metadata_json.{key} already exists with a different value; "
                f"refusing to overwrite",
                retryable=False,
                failure_class="validation",
                failure_code=FAILURE_CODE_METADATA_CONFLICT,
            )
        merged[key] = value
    return merged


async def _enqueue_materialization_job(
    conn: asyncpg.Connection,
    *,
    reading_record_id: UUID,
    user_id: UUID,
    original_input_id: UUID,
    source_artifact_id: UUID,
    expected_generation: int,
) -> UUID:
    """Enqueue an extracted_artifact_materialization job (D6-I3O).

    Called from the extraction worker's persist transaction after the
    confirmed-source row is written and the extraction job is transitioned to
    ``succeeded``. Runs in the SAME transaction so extraction success and
    materialization enqueue are atomic.

    The job reuses the existing ``reader_runs`` / ``reader_jobs`` tables.
    Duplicate enqueue prevention is provided by the partial unique index
    ``uq_reader_jobs_active_fingerprint`` on
    ``(reading_record_id, COALESCE(base_id, zero), job_type, target_type,
    target_key, expected_generation, operation_fingerprint)``
    scoped to ``status IN ('queued','claimed','retry_later','paused')``.
    Because this function creates a NEW ``reader_runs`` row per call, the
    ``(run_id, idempotency_key)`` constraint cannot fire across runs — the
    active-fingerprint index is what actually blocks a second active job with
    the same semantics. Once the first job terminates
    (succeeded/failed_terminal/superseded) the index no longer covers it, so
    a re-enqueue after termination is allowed (correct for retry scenarios).
    """
    run_row = await conn.fetchrow(
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
            $1,
            $2,
            $3,
            'queued',
            $4,
            $5::jsonb,
            $6,
            $7
        )
        RETURNING id
        """,
        reading_record_id,
        user_id,
        MATERIALIZATION_RUN_TYPE,
        expected_generation,
        jsonb_param(
            {
                "source": MATERIALIZATION_JOB_SOURCE,
                "reading_record_id": str(reading_record_id),
                "original_input_id": str(original_input_id),
                "source_artifact_id": str(source_artifact_id),
            }
        ),
        MATERIALIZATION_POLICY_VERSION,
        MATERIALIZATION_TRIGGER_KIND,
    )
    if run_row is None:
        raise RuntimeError("reader_runs insert did not return a row")

    input_json = {
        "source": MATERIALIZATION_JOB_SOURCE,
        "reading_record_id": str(reading_record_id),
        "original_input_id": str(original_input_id),
        "source_artifact_id": str(source_artifact_id),
        "expected_generation": expected_generation,
    }
    input_hash = hashlib.sha256(
        f"{source_artifact_id}:{expected_generation}".encode("utf-8")
    ).hexdigest()

    job_row = await conn.fetchrow(
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
            priority,
            expected_generation,
            operation_fingerprint,
            idempotency_key,
            input_hash,
            input_json,
            max_attempts
        )
        VALUES (
            $1,
            NULL,
            $2,
            $3,
            $4,
            $5,
            $6,
            'queued',
            0,
            $7,
            $8,
            $9,
            $10,
            $11::jsonb,
            $12
        )
        RETURNING id, status
        """,
        reading_record_id,
        run_row["id"],
        user_id,
        MATERIALIZATION_JOB_TYPE,
        MATERIALIZATION_TARGET_TYPE,
        str(source_artifact_id),
        expected_generation,
        MATERIALIZATION_OPERATION_FINGERPRINT,
        f"{MATERIALIZATION_OPERATION_FINGERPRINT}:{source_artifact_id}",
        input_hash,
        jsonb_param(input_json),
        DEFAULT_MATERIALIZATION_MAX_ATTEMPTS,
    )
    if job_row is None:
        raise RuntimeError("reader_jobs insert did not return a row")

    return job_row["id"]
