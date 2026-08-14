"""Artifact Input Pipeline Status query service (read-only).

Provides a single entry point for clients to poll the pipeline status of a
source artifact after init-upload / complete-upload / submit-input. The
service performs no writes, no OSS network calls, and triggers no workers.

Ownership is enforced fail-closed:
- ``source_artifacts.id`` must exist, belong to the requesting user, and
  have ``deleted_at IS NULL``.
- If the artifact is bound to a ``reading_record_id`` / ``original_input_id``
  both must belong to the same user and be mutually consistent.

The source of truth remains the existing domain tables
(``reading_records`` / ``original_inputs`` / ``source_artifacts`` /
``reader_jobs`` / ``candidate_reading_documents`` / ``stable_reading_documents``
/ ``reading_bases``). Plate / Markdown / Slate / DOM projections are never
loaded here.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

import asyncpg

from app.services.reader_orchestration.repository import (
    ReaderOrchestrationRepository,
)

# ---------------------------------------------------------------------------
# Constants — mirror artifact_input_application_service.py and
# artifact_extraction_worker.py. Both job types use ``target_key = str(artifact_id)``.
# ---------------------------------------------------------------------------
_EXTRACTION_JOB_TYPE = "input_artifact_extraction"
_MATERIALIZATION_JOB_TYPE = "extracted_artifact_materialization"

# ---------------------------------------------------------------------------
# Enum aliases (mirrored in schemas/reader_orchestration.py).
# ---------------------------------------------------------------------------
PipelineOutcome = Literal[
    "upload_pending",
    "upload_available_not_submitted",
    "extraction_queued",
    "extraction_running",
    "extraction_retry_later",
    "extraction_failed",
    "materialization_queued",
    "materialization_running",
    "materialization_retry_later",
    "materialization_failed",
    "stable_document_ready",
    "candidate_document_required",
    "input_rejected_or_action_required",
]

PipelineNextAction = Literal[
    "complete_upload",
    "submit_input",
    "wait_for_worker",
    "retry_later",
    "show_error",
    "open_reader",
    "confirm_candidate_document",
    "revise_input",
]


class ArtifactInputStatusQueryError(ValueError):
    """Raised when the pipeline status facts are inconsistent.

    Routes map this to HTTP 409 so clients can distinguish inconsistency
    from a genuine not-found (HTTP 404 via ``LookupError``).
    """


# ---------------------------------------------------------------------------
# Frozen result dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ArtifactSummary:
    artifact_id: UUID
    status: str
    artifact_kind: str
    storage_provider: str
    bucket: str | None
    endpoint: str | None
    object_key: str
    content_type: str | None
    byte_size: int | None
    content_sha256: str | None
    source_filename: str | None
    reading_record_id: UUID | None
    original_input_id: UUID | None


@dataclass(frozen=True, slots=True)
class RecordSummary:
    reading_record_id: UUID
    generation: int
    product_state: str
    readiness_state: str
    active_base_id: UUID | None
    source_type: str
    title: str | None
    language: str | None


@dataclass(frozen=True, slots=True)
class OriginalInputSummary:
    original_input_id: UUID
    input_type: str
    content_sha256: str
    has_source_text: bool
    # L2 (Q5)：confirmed_source_documents 行存在性。has_source_text
    # 保留 legacy 原义（对新输入恒 false），消费者迁移到本字段。
    has_confirmed_source: bool
    extraction_status: str | None
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class JobSummary:
    job_id: UUID
    status: str
    attempt_count: int
    max_attempts: int
    failure_class: str | None
    failure_code: str | None
    rationale_code: str | None
    available_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class CandidateDocumentSummary:
    candidate_document_id: UUID
    record_generation: int
    canonical_text_preview: str


@dataclass(frozen=True, slots=True)
class StableDocumentSummary:
    stable_document_id: UUID
    base_id: UUID
    record_generation: int
    content_sha256: str
    canonical_text_sha256: str


@dataclass(frozen=True, slots=True)
class ArtifactInputStatusResult:
    artifact: ArtifactSummary
    record: RecordSummary | None
    original_input: OriginalInputSummary | None
    extraction_job: JobSummary | None
    materialization_job: JobSummary | None
    candidate_document: CandidateDocumentSummary | None
    stable_document: StableDocumentSummary | None
    outcome: PipelineOutcome
    next_action: PipelineNextAction


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coerce_json_object(raw: Any, *, field_name: str) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        return dict(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ArtifactInputStatusQueryError(
                f"{field_name} is not valid JSON"
            ) from exc
        if not isinstance(parsed, Mapping):
            raise ArtifactInputStatusQueryError(
                f"{field_name} parses to a non-object JSON value"
            )
        return dict(parsed)
    raise ArtifactInputStatusQueryError(f"{field_name} must be a JSON object")


def _build_job_summary(row: asyncpg.Record | None) -> JobSummary | None:
    if row is None:
        return None
    return JobSummary(
        job_id=UUID(str(row["id"])),
        status=str(row["status"]),
        attempt_count=int(row["attempt_count"]),
        max_attempts=int(row["max_attempts"]),
        failure_class=(
            str(row["failure_class"])
            if row["failure_class"] is not None
            else None
        ),
        failure_code=(
            str(row["failure_code"])
            if row["failure_code"] is not None
            else None
        ),
        rationale_code=(
            str(row["rationale_code"])
            if row["rationale_code"] is not None
            else None
        ),
        available_at=row["available_at"],
        updated_at=row["updated_at"],
    )


def _determine_outcome(
    *,
    artifact_status: str,
    extraction_job: JobSummary | None,
    materialization_job: JobSummary | None,
    product_state: str,
) -> tuple[PipelineOutcome, PipelineNextAction]:
    """Map artifact status + job states + record product_state to outcome."""
    if artifact_status == "pending":
        return "upload_pending", "complete_upload"
    if artifact_status == "failed":
        return "extraction_failed", "show_error"

    # artifact_status == "available" — must be bound and have an extraction job
    if extraction_job is None:
        raise ArtifactInputStatusQueryError(
            "Artifact is bound but no extraction job found for this artifact."
        )

    ext_status = extraction_job.status

    if ext_status == "queued":
        return "extraction_queued", "wait_for_worker"
    if ext_status == "claimed":
        return "extraction_running", "wait_for_worker"
    if ext_status == "retry_later":
        return "extraction_retry_later", "retry_later"
    if ext_status == "failed_terminal":
        return "extraction_failed", "show_error"
    if ext_status != "succeeded":
        # paused / skipped / cancelled / superseded / unknown — fail closed.
        # The extraction worker enqueues the materialization job in the same
        # transaction that marks extraction succeeded, so any non-succeeded
        # state here means the pipeline is in an unexpected state.
        raise ArtifactInputStatusQueryError(
            f"Extraction job is in unexpected state: {ext_status}"
        )

    # ext_status == "succeeded" — materialization job must exist (the
    # extraction worker enqueues it in the same transaction). Missing job
    # is data inconsistency, not a transient wait.
    if materialization_job is None:
        raise ArtifactInputStatusQueryError(
            "Extraction job succeeded but no materialization job was found; "
            "pipeline state is inconsistent."
        )

    mat_status = materialization_job.status

    if mat_status == "queued":
        return "materialization_queued", "wait_for_worker"
    if mat_status == "claimed":
        return "materialization_running", "wait_for_worker"
    if mat_status == "retry_later":
        return "materialization_retry_later", "retry_later"
    if mat_status == "failed_terminal":
        return "materialization_failed", "show_error"
    if mat_status != "succeeded":
        # paused / skipped / cancelled / superseded / unknown — fail closed.
        raise ArtifactInputStatusQueryError(
            f"Materialization job is in unexpected state: {mat_status}"
        )

    # mat_status == "succeeded" — look at record.product_state
    if product_state == "readable_enhancing":
        return "stable_document_ready", "open_reader"
    if product_state == "needs_confirmation":
        return "candidate_document_required", "confirm_candidate_document"
    if product_state in ("action_required", "failed"):
        return "input_rejected_or_action_required", "revise_input"
    if product_state == "processing":
        # Materialization succeeded but the record hasn't transitioned —
        # this is data inconsistency (the materialization worker transitions
        # the record in the same transaction). Fail closed.
        raise ArtifactInputStatusQueryError(
            "Materialization job succeeded but reading record is still in "
            "'processing' state; pipeline state is inconsistent."
        )
    if product_state == "deleted":
        raise ArtifactInputStatusQueryError(
            "Reading record product_state is 'deleted'."
        )

    raise ArtifactInputStatusQueryError(
        f"Unknown product_state: {product_state}"
    )


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ArtifactPipelineStatusQueryService:
    """Read-only pipeline status query service.

    Accepts an optional ``pool`` for tests; falls back to the shared
    ``ReaderOrchestrationRepository`` pool in production. Never opens a
    write transaction.
    """

    def __init__(self, *, pool: asyncpg.Pool | None = None) -> None:
        self._pool = pool

    def _get_pool(self) -> asyncpg.Pool:
        if self._pool is not None:
            return self._pool
        return ReaderOrchestrationRepository().get_pool()

    async def load_pipeline_status(
        self,
        *,
        artifact_id: UUID,
        user_id: UUID,
    ) -> ArtifactInputStatusResult:
        pool = self._get_pool()

        async with pool.acquire() as conn:
            # 1. Load artifact with ownership check (fail closed → LookupError).
            artifact_row = await conn.fetchrow(
                """
                SELECT
                    id, reading_record_id, original_input_id, user_id,
                    artifact_kind, storage_provider, bucket, object_key, endpoint,
                    content_type, byte_size, content_sha256, source_filename,
                    status, deleted_at
                FROM source_artifacts
                WHERE id = $1
                  AND deleted_at IS NULL
                """,
                artifact_id,
            )
            if artifact_row is None:
                raise LookupError(
                    f"Source artifact {artifact_id} was not found."
                )
            if UUID(str(artifact_row["user_id"])) != user_id:
                # Fail closed: wrong user → 404 (do not leak existence).
                raise LookupError(
                    f"Source artifact {artifact_id} was not found for user {user_id}."
                )

            artifact = ArtifactSummary(
                artifact_id=UUID(str(artifact_row["id"])),
                status=str(artifact_row["status"]),
                artifact_kind=str(artifact_row["artifact_kind"]),
                storage_provider=str(artifact_row["storage_provider"]),
                bucket=(
                    str(artifact_row["bucket"])
                    if artifact_row["bucket"] is not None
                    else None
                ),
                endpoint=(
                    str(artifact_row["endpoint"])
                    if artifact_row["endpoint"] is not None
                    else None
                ),
                object_key=str(artifact_row["object_key"]),
                content_type=(
                    str(artifact_row["content_type"])
                    if artifact_row["content_type"] is not None
                    else None
                ),
                byte_size=(
                    int(artifact_row["byte_size"])
                    if artifact_row["byte_size"] is not None
                    else None
                ),
                content_sha256=(
                    str(artifact_row["content_sha256"])
                    if artifact_row["content_sha256"] is not None
                    else None
                ),
                source_filename=(
                    str(artifact_row["source_filename"])
                    if artifact_row["source_filename"] is not None
                    else None
                ),
                reading_record_id=(
                    UUID(str(artifact_row["reading_record_id"]))
                    if artifact_row["reading_record_id"] is not None
                    else None
                ),
                original_input_id=(
                    UUID(str(artifact_row["original_input_id"]))
                    if artifact_row["original_input_id"] is not None
                    else None
                ),
            )

            # 2. Validate binding state BEFORE any fast return.
            #    source_artifacts only enforces plain FK; we must verify that
            #    bound record/input belong to the same user and are mutually
            #    consistent. Half-bound (exactly one id non-null) is
            #    inconsistent and must fail closed — not submit_input.
            has_record = artifact.reading_record_id is not None
            has_input = artifact.original_input_id is not None
            if has_record != has_input:
                raise ArtifactInputStatusQueryError(
                    f"Artifact {artifact_id} has mismatched bindings: "
                    f"reading_record_id={artifact.reading_record_id}, "
                    f"original_input_id={artifact.original_input_id}."
                )

            record: RecordSummary | None = None
            original_input: OriginalInputSummary | None = None

            if has_record and has_input:
                # Load reading_record with ownership check.
                record_row = await conn.fetchrow(
                    """
                    SELECT
                        id, user_id, source_type, title, language,
                        product_state, readiness_state, generation,
                        active_base_id, deleted_at
                    FROM reading_records
                    WHERE id = $1
                    """,
                    artifact.reading_record_id,
                )
                if record_row is None:
                    raise ArtifactInputStatusQueryError(
                        f"Reading record {artifact.reading_record_id} "
                        f"not found."
                    )
                if UUID(str(record_row["user_id"])) != user_id:
                    raise ArtifactInputStatusQueryError(
                        f"Reading record {artifact.reading_record_id} does "
                        f"not belong to user {user_id}."
                    )
                if record_row["deleted_at"] is not None:
                    raise ArtifactInputStatusQueryError(
                        f"Reading record {artifact.reading_record_id} "
                        f"is deleted."
                    )

                record = RecordSummary(
                    reading_record_id=UUID(str(record_row["id"])),
                    generation=int(record_row["generation"]),
                    product_state=str(record_row["product_state"]),
                    readiness_state=str(record_row["readiness_state"]),
                    active_base_id=(
                        UUID(str(record_row["active_base_id"]))
                        if record_row["active_base_id"] is not None
                        else None
                    ),
                    source_type=str(record_row["source_type"]),
                    title=(
                        str(record_row["title"])
                        if record_row["title"] is not None
                        else None
                    ),
                    language=(
                        str(record_row["language"])
                        if record_row["language"] is not None
                        else None
                    ),
                )

                # Load original_input with ownership check.
                # IMPORTANT: never SELECT source_text — only derive
                # has_source_text (legacy 语义保留) 与
                # has_confirmed_source（L2 Q5：source 行存在性）。
                input_row = await conn.fetchrow(
                    """
                    SELECT
                        id, reading_record_id, user_id, input_type,
                        source_text IS NOT NULL AS has_source_text,
                        EXISTS(
                            SELECT 1 FROM confirmed_source_documents cs
                            WHERE cs.reading_record_id = $2
                              AND cs.record_generation = $3
                        ) AS has_confirmed_source,
                        content_sha256, metadata_json
                    FROM original_inputs
                    WHERE id = $1
                    """,
                    artifact.original_input_id,
                    artifact.reading_record_id,
                    int(record_row["generation"]),
                )
                if input_row is None:
                    raise ArtifactInputStatusQueryError(
                        f"Original input {artifact.original_input_id} "
                        f"not found."
                    )
                if UUID(str(input_row["user_id"])) != user_id:
                    raise ArtifactInputStatusQueryError(
                        f"Original input {artifact.original_input_id} does "
                        f"not belong to user {user_id}."
                    )
                # Mutual consistency: input.reading_record_id must match
                # artifact.reading_record_id.
                if (
                    UUID(str(input_row["reading_record_id"]))
                    != artifact.reading_record_id
                ):
                    raise ArtifactInputStatusQueryError(
                        f"Original input {artifact.original_input_id} does "
                        f"not belong to reading record "
                        f"{artifact.reading_record_id}."
                    )

                metadata = _coerce_json_object(
                    input_row["metadata_json"],
                    field_name="original_inputs.metadata_json",
                )
                extraction_status_raw = metadata.get("extraction_status")
                extraction_status = (
                    str(extraction_status_raw)
                    if extraction_status_raw is not None
                    else None
                )

                original_input = OriginalInputSummary(
                    original_input_id=UUID(str(input_row["id"])),
                    input_type=str(input_row["input_type"]),
                    content_sha256=str(input_row["content_sha256"]),
                    has_source_text=bool(input_row["has_source_text"]),
                    has_confirmed_source=bool(input_row["has_confirmed_source"]),
                    extraction_status=extraction_status,
                    metadata=metadata,
                )

            # 3. Fast paths for unbound / failed artifacts.
            #    Bindings (if any) have already been validated above.
            if artifact.status == "pending":
                return ArtifactInputStatusResult(
                    artifact=artifact,
                    record=record,
                    original_input=original_input,
                    extraction_job=None,
                    materialization_job=None,
                    candidate_document=None,
                    stable_document=None,
                    outcome="upload_pending",
                    next_action="complete_upload",
                )

            if artifact.status == "failed":
                return ArtifactInputStatusResult(
                    artifact=artifact,
                    record=record,
                    original_input=original_input,
                    extraction_job=None,
                    materialization_job=None,
                    candidate_document=None,
                    stable_document=None,
                    outcome="extraction_failed",
                    next_action="show_error",
                )

            # artifact.status == "deleted" cannot reach here because of the
            # ``deleted_at IS NULL`` filter; guard defensively.
            if artifact.status == "deleted":
                raise LookupError(
                    f"Source artifact {artifact_id} was not found."
                )

            # 4. Available artifact — if not bound, prompt submit-input.
            if not has_record:
                return ArtifactInputStatusResult(
                    artifact=artifact,
                    record=None,
                    original_input=None,
                    extraction_job=None,
                    materialization_job=None,
                    candidate_document=None,
                    stable_document=None,
                    outcome="upload_available_not_submitted",
                    next_action="submit_input",
                )

            # 5. Available + bound — load latest extraction + materialization
            #    jobs. Both job types use ``target_key = str(artifact_id)``.
            #    record and original_input are already loaded and validated.
            assert record is not None  # has_record is True here
            extraction_job_row = await conn.fetchrow(
                """
                SELECT
                    id, status, attempt_count, max_attempts,
                    failure_class, failure_code, rationale_code,
                    available_at, updated_at
                FROM reader_jobs
                WHERE job_type = $1
                  AND target_key = $2
                  AND reading_record_id = $3
                  AND user_id = $4
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                _EXTRACTION_JOB_TYPE,
                str(artifact_id),
                artifact.reading_record_id,
                user_id,
            )
            extraction_job = _build_job_summary(extraction_job_row)

            materialization_job_row = await conn.fetchrow(
                """
                SELECT
                    id, status, attempt_count, max_attempts,
                    failure_class, failure_code, rationale_code,
                    available_at, updated_at
                FROM reader_jobs
                WHERE job_type = $1
                  AND target_key = $2
                  AND reading_record_id = $3
                  AND user_id = $4
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                _MATERIALIZATION_JOB_TYPE,
                str(artifact_id),
                artifact.reading_record_id,
                user_id,
            )
            materialization_job = _build_job_summary(materialization_job_row)

            # 6. Determine outcome.
            outcome, next_action = _determine_outcome(
                artifact_status=artifact.status,
                extraction_job=extraction_job,
                materialization_job=materialization_job,
                product_state=record.product_state,
            )

            # 7. Load candidate / stable document based on outcome.
            candidate_document: CandidateDocumentSummary | None = None
            stable_document: StableDocumentSummary | None = None

            if outcome == "candidate_document_required":
                # Filter by record_generation to avoid returning a stale
                # candidate from an older generation (the candidate table
                # has no unique constraint on (record_id, generation)).
                candidate_row = await conn.fetchrow(
                    """
                    SELECT id, record_generation, canonical_text_preview
                    FROM candidate_reading_documents
                    WHERE reading_record_id = $1
                      AND user_id = $2
                      AND record_generation = $3
                      AND status = 'ready'
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    artifact.reading_record_id,
                    user_id,
                    record.generation,
                )
                if candidate_row is None:
                    raise ArtifactInputStatusQueryError(
                        f"Record {record.reading_record_id} is in "
                        f"needs_confirmation but no ready candidate document "
                        f"was found for generation {record.generation}."
                    )
                candidate_document = CandidateDocumentSummary(
                    candidate_document_id=UUID(str(candidate_row["id"])),
                    record_generation=int(candidate_row["record_generation"]),
                    canonical_text_preview=str(
                        candidate_row["canonical_text_preview"]
                    ),
                )

            elif outcome == "stable_document_ready":
                if record.active_base_id is None:
                    raise ArtifactInputStatusQueryError(
                        f"Record {record.reading_record_id} has no "
                        f"active_base_id."
                    )
                stable_row = await conn.fetchrow(
                    """
                    SELECT id, record_generation, content_sha256
                    FROM stable_reading_documents
                    WHERE reading_record_id = $1
                      AND record_generation = $2
                      AND status = 'active'
                    """,
                    artifact.reading_record_id,
                    record.generation,
                )
                if stable_row is None:
                    raise ArtifactInputStatusQueryError(
                        f"Record {record.reading_record_id} is in "
                        f"readable_enhancing but no active stable document "
                        f"was found for generation {record.generation}."
                    )
                base_row = await conn.fetchrow(
                    """
                    SELECT content_sha256
                    FROM reading_bases
                    WHERE id = $1
                      AND reading_record_id = $2
                      AND record_generation = $3
                      AND status = 'active'
                    """,
                    record.active_base_id,
                    artifact.reading_record_id,
                    record.generation,
                )
                if base_row is None:
                    raise ArtifactInputStatusQueryError(
                        f"Reading base {record.active_base_id} not found for "
                        f"record {record.reading_record_id}."
                    )
                stable_document = StableDocumentSummary(
                    stable_document_id=UUID(str(stable_row["id"])),
                    base_id=record.active_base_id,
                    record_generation=int(stable_row["record_generation"]),
                    content_sha256=str(stable_row["content_sha256"]),
                    canonical_text_sha256=str(base_row["content_sha256"]),
                )

        return ArtifactInputStatusResult(
            artifact=artifact,
            record=record,
            original_input=original_input,
            extraction_job=extraction_job,
            materialization_job=materialization_job,
            candidate_document=candidate_document,
            stable_document=stable_document,
            outcome=outcome,
            next_action=next_action,
        )
