"""Tests for D6-I3V ArtifactPipelineStatusQueryService.

Covers every outcome path:
- upload_pending / upload_available_not_submitted
- extraction_queued / extraction_running / extraction_retry_later / extraction_failed
- materialization_queued / materialization_running / materialization_retry_later
  / materialization_failed
- stable_document_ready / candidate_document_required
- input_rejected_or_action_required

Ownership fail-closed:
- wrong user → LookupError
- deleted artifact → LookupError
- mismatched original_input → ArtifactInputStatusQueryError

Read-only guarantees:
- does not SELECT original_inputs.source_text
- does not write to any table
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.database.connection import init_connection
from app.database.json_compat import jsonb_param
from app.services.reader_orchestration.artifact_input_status_query_service import (
    ArtifactInputStatusQueryError,
    ArtifactPipelineStatusQueryService,
)

pytestmark = pytest.mark.anyio

REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE_ARTIFACTS_SQL = (
    REPO_ROOT / "infra" / "migrations" / "0007_reader_source_artifacts.sql"
).read_text(encoding="utf-8")

from tests.test_reader_orchestration_schema_baseline import (  # noqa: E402
    BASELINE_SQL,
    DATABASE_URL,
)

# 0004 (document_blocks) is now in BASELINE_SQL, so the pipeline status
# schema is BASELINE_SQL + 0007 (reader_source_artifacts).
PIPELINE_STATUS_SCHEMA_SQL = BASELINE_SQL + "\n" + SOURCE_ARTIFACTS_SQL

# Fixed UUIDs for deterministic seeding
_USER_ID = UUID("00000000-0000-0000-0000-0000000d3e01")
_OTHER_USER_ID = UUID("00000000-0000-0000-0000-0000000d3e02")
_RECORD_ID = UUID("00000000-0000-0000-0000-0000000d3e03")
_ORIGINAL_INPUT_ID = UUID("00000000-0000-0000-0000-0000000d3e04")
_ARTIFACT_ID = UUID("00000000-0000-0000-0000-0000000d3e05")
_RUN_ID = UUID("00000000-0000-0000-0000-0000000d3e06")
_BASE_ID = UUID("00000000-0000-0000-0000-0000000d3e07")
_STABLE_DOC_ID = UUID("00000000-0000-0000-0000-0000000d3e08")
_CANDIDATE_DOC_ID = UUID("00000000-0000-0000-0000-0000000d3e09")

_DEFAULT_CONTENT_SHA256 = "a" * 64
_EXTRACTED_TEXT = "This is the extracted text from the artifact for testing pipeline status."

_EXTRACTOR_NAME = "test_extractor"
_EXTRACTION_JOB_TYPE = "input_artifact_extraction"
_MATERIALIZATION_JOB_TYPE = "extracted_artifact_materialization"


# ---------------------------------------------------------------------------
# Pool / schema fixtures
# ---------------------------------------------------------------------------


async def _make_pool(schema_name: str) -> asyncpg.Pool:
    async def _init_conn(conn: asyncpg.Connection) -> None:
        await init_connection(conn)

    async def _setup_conn(conn: asyncpg.Connection) -> None:
        await conn.execute(f'SET search_path TO "{schema_name}", public')

    return await asyncpg.create_pool(
        DATABASE_URL,
        min_size=1,
        max_size=4,
        init=_init_conn,
        setup=_setup_conn,
    )


async def _connect_admin(schema_name: str | None = None) -> asyncpg.Connection:
    conn = await asyncpg.connect(DATABASE_URL)
    if schema_name is not None:
        await conn.execute(f'SET search_path TO "{schema_name}", public')
    return conn


@pytest.fixture
async def status_env() -> asyncpg.Pool:
    schema_name = f"test_i3v_status_{uuid4().hex}"
    admin_conn = await _connect_admin()
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(PIPELINE_STATUS_SCHEMA_SQL)
        pool = await _make_pool(schema_name)
        try:
            yield pool
        finally:
            await pool.close()
    finally:
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _seed_user(pool: asyncpg.Pool, user_id: UUID = _USER_ID) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (id) VALUES ($1) ON CONFLICT DO NOTHING",
            user_id,
        )


async def _seed_record(
    pool: asyncpg.Pool,
    *,
    user_id: UUID = _USER_ID,
    record_id: UUID = _RECORD_ID,
    product_state: str = "processing",
    readiness_state: str = "submitted",
    generation: int = 1,
    active_base_id: UUID | None = None,
    deleted_at: datetime | None = None,
    source_type: str = "pdf",
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO reading_records (
                id, user_id, source_type, title, language,
                lifecycle_status, product_state, readiness_state,
                generation, active_base_id, deleted_at
            )
            VALUES ($1, $2, $3, 'I3V Test', 'en',
                    'active', $4, $5,
                    $6, $7, $8)
            """,
            record_id,
            user_id,
            source_type,
            product_state,
            readiness_state,
            generation,
            active_base_id,
            deleted_at,
        )


async def _seed_original_input(
    pool: asyncpg.Pool,
    *,
    original_input_id: UUID = _ORIGINAL_INPUT_ID,
    reading_record_id: UUID = _RECORD_ID,
    user_id: UUID = _USER_ID,
    source_text: str | None = None,
    input_type: str = "file_ref",
    metadata: dict | None = None,
    content_sha256: str = _DEFAULT_CONTENT_SHA256,
) -> None:
    if metadata is None:
        metadata = {"source_artifact_status": "available"}
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO original_inputs (
                id, reading_record_id, user_id, input_type,
                source_text, source_ref_json, metadata_json, content_sha256
            )
            VALUES ($1, $2, $3, $4,
                    $5, $6::jsonb, $7::jsonb, $8)
            """,
            original_input_id,
            reading_record_id,
            user_id,
            input_type,
            source_text,
            jsonb_param({"artifact_id": str(_ARTIFACT_ID)}),
            jsonb_param(metadata),
            content_sha256,
        )


async def _seed_artifact(
    pool: asyncpg.Pool,
    *,
    artifact_id: UUID = _ARTIFACT_ID,
    user_id: UUID = _USER_ID,
    reading_record_id: UUID | None = _RECORD_ID,
    original_input_id: UUID | None = _ORIGINAL_INPUT_ID,
    status: str = "available",
    artifact_kind: str = "original_upload",
    content_type: str = "application/pdf",
    byte_size: int = 1024,
    content_sha256: str | None = _DEFAULT_CONTENT_SHA256,
    source_filename: str = "test.pdf",
    deleted_at: datetime | None = None,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO source_artifacts (
                id, reading_record_id, original_input_id, user_id,
                artifact_kind, storage_provider, bucket, object_key, endpoint,
                content_type, byte_size, content_sha256, source_filename,
                status, deleted_at
            )
            VALUES ($1, $2, $3, $4,
                    $5, 'oss', 'claread-dev', 'dev/test/test.pdf',
                    'https://oss-cn-shenzhen.aliyuncs.com',
                    $6, $7, $8, $9, $10, $11)
            """,
            artifact_id,
            reading_record_id,
            original_input_id,
            user_id,
            artifact_kind,
            content_type,
            byte_size,
            content_sha256,
            source_filename,
            status,
            deleted_at,
        )


async def _seed_run(
    pool: asyncpg.Pool,
    *,
    run_id: UUID = _RUN_ID,
    reading_record_id: UUID = _RECORD_ID,
    user_id: UUID = _USER_ID,
    run_type: str = "input_artifact_extraction",
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO reader_runs (
                reading_record_id, user_id, run_type, status,
                record_generation, envelope_json, policy_version, trigger_kind, id
            )
            VALUES ($1, $2, $3, 'queued', 1,
                    '{}'::jsonb, 'test_policy_v1', 'system', $4)
            """,
            reading_record_id,
            user_id,
            run_type,
            run_id,
        )


async def _seed_job(
    pool: asyncpg.Pool,
    *,
    job_type: str,
    status: str = "queued",
    reading_record_id: UUID = _RECORD_ID,
    user_id: UUID = _USER_ID,
    run_id: UUID = _RUN_ID,
    target_key: str = str(_ARTIFACT_ID),
    attempt_count: int = 0,
    max_attempts: int = 3,
    failure_class: str | None = None,
    failure_code: str | None = None,
    rationale_code: str | None = None,
    available_at: datetime | None = None,
    operation_fingerprint: str | None = None,
    idempotency_key: str | None = None,
) -> UUID:
    if available_at is None:
        available_at = datetime.now(UTC)
    if operation_fingerprint is None:
        operation_fingerprint = (
            "input_artifact_extraction_v1"
            if job_type == _EXTRACTION_JOB_TYPE
            else "extracted_artifact_materialization_v1"
        )
    if idempotency_key is None:
        idempotency_key = f"{operation_fingerprint}:{target_key}-{uuid4().hex}"

    async with pool.acquire() as conn:
        job_id = await conn.fetchval(
            """
            INSERT INTO reader_jobs (
                reading_record_id, base_id, run_id, user_id,
                job_type, target_type, target_key, status,
                priority, expected_generation, operation_fingerprint,
                idempotency_key, input_json, max_attempts,
                attempt_count, failure_class, failure_code, rationale_code,
                available_at
            )
            VALUES ($1, NULL, $2, $3,
                    $4, 'record', $5, $6,
                    0, 1, $7,
                    $8, '{}'::jsonb, $9,
                    $10, $11, $12, $13,
                    $14)
            RETURNING id
            """,
            reading_record_id,
            run_id,
            user_id,
            job_type,
            target_key,
            status,
            operation_fingerprint,
            idempotency_key,
            max_attempts,
            attempt_count,
            failure_class,
            failure_code,
            rationale_code,
            available_at,
        )
    return UUID(str(job_id))


async def _seed_reading_base(
    pool: asyncpg.Pool,
    *,
    base_id: UUID = _BASE_ID,
    reading_record_id: UUID = _RECORD_ID,
    record_generation: int = 1,
    text: str = _EXTRACTED_TEXT,
    status: str = "active",
) -> str:
    content_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    # Compute UTF-16 code unit length (Python len() counts code points, but
    # for BMP-only text they coincide; the DB constraint verifies equality).
    content_utf16_length = len(text)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO reading_bases (
                id, reading_record_id, base_version, record_generation,
                text, content_sha256, content_utf16_length,
                canonicalizer_version, builder_version, segmenter_version,
                language, title_snapshot, navigation_json, status
            )
            VALUES ($1, $2, 1, $3,
                    $4, $5, $6,
                    'test_canon_v1', 'test_builder_v1', 'test_seg_v1',
                    'en', 'I3V Test', '{"units":[]}'::jsonb, $7)
            """,
            base_id,
            reading_record_id,
            record_generation,
            text,
            content_sha,
            content_utf16_length,
            status,
        )
    return content_sha


async def _seed_stable_document(
    pool: asyncpg.Pool,
    *,
    stable_document_id: UUID = _STABLE_DOC_ID,
    reading_record_id: UUID = _RECORD_ID,
    record_generation: int = 1,
    content_sha256: str = _DEFAULT_CONTENT_SHA256,
    status: str = "active",
    document_version: int = 1,
    title: str = "I3V Test",
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO stable_reading_documents (
                id, reading_record_id, record_generation, title,
                document_version, source_profile_json, content_sha256, status
            )
            VALUES ($1, $2, $3, $4,
                    $5, '{}'::jsonb, $6, $7)
            """,
            stable_document_id,
            reading_record_id,
            record_generation,
            title,
            document_version,
            content_sha256,
            status,
        )


async def _seed_candidate_document(
    pool: asyncpg.Pool,
    *,
    candidate_document_id: UUID = _CANDIDATE_DOC_ID,
    reading_record_id: UUID = _RECORD_ID,
    user_id: UUID = _USER_ID,
    record_generation: int = 1,
    canonical_text_preview: str = "Preview text for candidate document.",
    status: str = "ready",
    title: str = "I3V Candidate",
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO candidate_reading_documents (
                id, reading_record_id, user_id, record_generation,
                title, blocks_json, canonical_text_preview,
                source_refs_json, quality_json, status
            )
            VALUES ($1, $2, $3, $4,
                    $5, '[]'::jsonb, $6,
                    '{}'::jsonb, '{}'::jsonb, $7)
            """,
            candidate_document_id,
            reading_record_id,
            user_id,
            record_generation,
            title,
            canonical_text_preview,
            status,
        )


async def _seed_bound_environment(
    pool: asyncpg.Pool,
    *,
    artifact_status: str = "available",
    artifact_deleted_at: datetime | None = None,
    product_state: str = "processing",
    readiness_state: str = "submitted",
    active_base_id: UUID | None = None,
    source_text: str | None = None,
    input_metadata: dict | None = None,
    user_id: UUID = _USER_ID,
    record_id: UUID = _RECORD_ID,
    original_input_id: UUID = _ORIGINAL_INPUT_ID,
    artifact_id: UUID = _ARTIFACT_ID,
    bind_artifact: bool = True,
) -> None:
    """Seed user + record + original_input + artifact + run (no jobs)."""
    await _seed_user(pool, user_id)
    await _seed_record(
        pool,
        user_id=user_id,
        record_id=record_id,
        product_state=product_state,
        readiness_state=readiness_state,
        active_base_id=active_base_id,
    )
    await _seed_original_input(
        pool,
        original_input_id=original_input_id,
        reading_record_id=record_id,
        user_id=user_id,
        source_text=source_text,
        metadata=input_metadata,
    )
    bound_record = record_id if bind_artifact else None
    bound_input = original_input_id if bind_artifact else None
    await _seed_artifact(
        pool,
        artifact_id=artifact_id,
        user_id=user_id,
        reading_record_id=bound_record,
        original_input_id=bound_input,
        status=artifact_status,
        deleted_at=artifact_deleted_at,
    )
    await _seed_run(pool, reading_record_id=record_id, user_id=user_id)


def _build_service(pool: asyncpg.Pool) -> ArtifactPipelineStatusQueryService:
    return ArtifactPipelineStatusQueryService(pool=pool)


# ---------------------------------------------------------------------------
# Fast-path outcome tests
# ===================================================================


async def test_pending_upload_returns_upload_pending(status_env: asyncpg.Pool) -> None:
    """Artifact status=pending → upload_pending / complete_upload."""
    await _seed_user(status_env)
    await _seed_artifact(
        status_env,
        status="pending",
        reading_record_id=None,
        original_input_id=None,
    )

    service = _build_service(status_env)
    result = await service.load_pipeline_status(
        artifact_id=_ARTIFACT_ID,
        user_id=_USER_ID,
    )

    assert result.outcome == "upload_pending"
    assert result.next_action == "complete_upload"
    assert result.record is None
    assert result.original_input is None
    assert result.extraction_job is None
    assert result.materialization_job is None
    assert result.candidate_document is None
    assert result.stable_document is None
    assert result.artifact.status == "pending"


async def test_available_unbound_returns_upload_available_not_submitted(
    status_env: asyncpg.Pool,
) -> None:
    """Artifact status=available but no record/input → upload_available_not_submitted."""
    await _seed_user(status_env)
    await _seed_artifact(
        status_env,
        status="available",
        reading_record_id=None,
        original_input_id=None,
    )

    service = _build_service(status_env)
    result = await service.load_pipeline_status(
        artifact_id=_ARTIFACT_ID,
        user_id=_USER_ID,
    )

    assert result.outcome == "upload_available_not_submitted"
    assert result.next_action == "submit_input"
    assert result.record is None
    assert result.original_input is None


# ---------------------------------------------------------------------------
# Extraction job outcome tests
# ===================================================================


async def test_extraction_queued_returns_extraction_queued(
    status_env: asyncpg.Pool,
) -> None:
    await _seed_bound_environment(status_env)
    await _seed_job(status_env, job_type=_EXTRACTION_JOB_TYPE, status="queued")

    service = _build_service(status_env)
    result = await service.load_pipeline_status(
        artifact_id=_ARTIFACT_ID,
        user_id=_USER_ID,
    )

    assert result.outcome == "extraction_queued"
    assert result.next_action == "wait_for_worker"
    assert result.extraction_job is not None
    assert result.extraction_job.status == "queued"
    assert result.materialization_job is None


async def test_extraction_claimed_returns_extraction_running(
    status_env: asyncpg.Pool,
) -> None:
    await _seed_bound_environment(status_env)
    await _seed_job(status_env, job_type=_EXTRACTION_JOB_TYPE, status="claimed")

    service = _build_service(status_env)
    result = await service.load_pipeline_status(
        artifact_id=_ARTIFACT_ID,
        user_id=_USER_ID,
    )

    assert result.outcome == "extraction_running"
    assert result.next_action == "wait_for_worker"
    assert result.extraction_job.status == "claimed"


async def test_extraction_retry_later_returns_extraction_retry_later(
    status_env: asyncpg.Pool,
) -> None:
    await _seed_bound_environment(status_env)
    await _seed_job(
        status_env,
        job_type=_EXTRACTION_JOB_TYPE,
        status="retry_later",
        attempt_count=1,
        failure_class="transient",
        failure_code="ocr_backend_transient",
    )

    service = _build_service(status_env)
    result = await service.load_pipeline_status(
        artifact_id=_ARTIFACT_ID,
        user_id=_USER_ID,
    )

    assert result.outcome == "extraction_retry_later"
    assert result.next_action == "retry_later"
    assert result.extraction_job.status == "retry_later"
    assert result.extraction_job.failure_class == "transient"
    assert result.extraction_job.failure_code == "ocr_backend_transient"


async def test_extraction_failed_terminal_returns_extraction_failed(
    status_env: asyncpg.Pool,
) -> None:
    await _seed_bound_environment(status_env)
    await _seed_job(
        status_env,
        job_type=_EXTRACTION_JOB_TYPE,
        status="failed_terminal",
        attempt_count=3,
        max_attempts=3,
        failure_class="permanent",
        failure_code="ocr_permission_denied",
    )

    service = _build_service(status_env)
    result = await service.load_pipeline_status(
        artifact_id=_ARTIFACT_ID,
        user_id=_USER_ID,
    )

    assert result.outcome == "extraction_failed"
    assert result.next_action == "show_error"
    assert result.extraction_job.status == "failed_terminal"
    assert result.extraction_job.max_attempts == 3


# ---------------------------------------------------------------------------
# Materialization job outcome tests
# ===================================================================


async def test_extraction_succeeded_no_materialization_job_raises_inconsistency(
    status_env: asyncpg.Pool,
) -> None:
    """Extraction succeeded but no materialization job → fail closed (409).

    The extraction worker enqueues the materialization job in the same
    transaction that marks extraction succeeded. A missing materialization
    job is data inconsistency, not a transient wait.
    """
    await _seed_bound_environment(status_env)
    await _seed_job(
        status_env,
        job_type=_EXTRACTION_JOB_TYPE,
        status="succeeded",
    )

    service = _build_service(status_env)
    with pytest.raises(ArtifactInputStatusQueryError):
        await service.load_pipeline_status(
            artifact_id=_ARTIFACT_ID,
            user_id=_USER_ID,
        )


async def test_materialization_queued_returns_materialization_queued(
    status_env: asyncpg.Pool,
) -> None:
    await _seed_bound_environment(status_env)
    await _seed_job(
        status_env,
        job_type=_EXTRACTION_JOB_TYPE,
        status="succeeded",
    )
    await _seed_job(
        status_env,
        job_type=_MATERIALIZATION_JOB_TYPE,
        status="queued",
    )

    service = _build_service(status_env)
    result = await service.load_pipeline_status(
        artifact_id=_ARTIFACT_ID,
        user_id=_USER_ID,
    )

    assert result.outcome == "materialization_queued"
    assert result.next_action == "wait_for_worker"
    assert result.materialization_job.status == "queued"


async def test_materialization_claimed_returns_materialization_running(
    status_env: asyncpg.Pool,
) -> None:
    await _seed_bound_environment(status_env)
    await _seed_job(
        status_env,
        job_type=_EXTRACTION_JOB_TYPE,
        status="succeeded",
    )
    await _seed_job(
        status_env,
        job_type=_MATERIALIZATION_JOB_TYPE,
        status="claimed",
    )

    service = _build_service(status_env)
    result = await service.load_pipeline_status(
        artifact_id=_ARTIFACT_ID,
        user_id=_USER_ID,
    )

    assert result.outcome == "materialization_running"
    assert result.next_action == "wait_for_worker"
    assert result.materialization_job.status == "claimed"


async def test_materialization_retry_later_returns_materialization_retry_later(
    status_env: asyncpg.Pool,
) -> None:
    await _seed_bound_environment(status_env)
    await _seed_job(
        status_env,
        job_type=_EXTRACTION_JOB_TYPE,
        status="succeeded",
    )
    await _seed_job(
        status_env,
        job_type=_MATERIALIZATION_JOB_TYPE,
        status="retry_later",
        attempt_count=1,
    )

    service = _build_service(status_env)
    result = await service.load_pipeline_status(
        artifact_id=_ARTIFACT_ID,
        user_id=_USER_ID,
    )

    assert result.outcome == "materialization_retry_later"
    assert result.next_action == "retry_later"
    assert result.materialization_job.status == "retry_later"


async def test_materialization_failed_terminal_returns_materialization_failed(
    status_env: asyncpg.Pool,
) -> None:
    await _seed_bound_environment(status_env)
    await _seed_job(
        status_env,
        job_type=_EXTRACTION_JOB_TYPE,
        status="succeeded",
    )
    await _seed_job(
        status_env,
        job_type=_MATERIALIZATION_JOB_TYPE,
        status="failed_terminal",
        attempt_count=3,
        max_attempts=3,
        failure_class="permanent",
        failure_code="materialization_persistence_error",
    )

    service = _build_service(status_env)
    result = await service.load_pipeline_status(
        artifact_id=_ARTIFACT_ID,
        user_id=_USER_ID,
    )

    assert result.outcome == "materialization_failed"
    assert result.next_action == "show_error"
    assert result.materialization_job.status == "failed_terminal"


# ---------------------------------------------------------------------------
# Stable document / candidate / rejected outcome tests
# ===================================================================


async def test_stable_document_ready_returns_stable_document_ready(
    status_env: asyncpg.Pool,
) -> None:
    """Materialization succeeded + product_state=readable_enhancing → stable_document_ready."""
    # Circular FK: reading_records.active_base_id → reading_bases, and
    # reading_bases.reading_record_id → reading_records (immediate). Insert the
    # record with active_base_id=NULL first, then the base, then UPDATE the
    # record to point at the base.
    await _seed_bound_environment(
        status_env,
        product_state="readable_enhancing",
        readiness_state="article_ready",
        active_base_id=None,
        source_text=_EXTRACTED_TEXT,
        input_metadata={
            "source_artifact_status": "available",
            "extraction_status": "succeeded",
            "extractor_name": _EXTRACTOR_NAME,
        },
    )
    canonical_sha = await _seed_reading_base(status_env)
    async with status_env.acquire() as conn:
        await conn.execute(
            "UPDATE reading_records SET active_base_id = $1 WHERE id = $2",
            _BASE_ID,
            _RECORD_ID,
        )
    await _seed_job(
        status_env,
        job_type=_EXTRACTION_JOB_TYPE,
        status="succeeded",
    )
    await _seed_job(
        status_env,
        job_type=_MATERIALIZATION_JOB_TYPE,
        status="succeeded",
    )
    await _seed_stable_document(
        status_env,
        content_sha256=canonical_sha,
    )

    service = _build_service(status_env)
    result = await service.load_pipeline_status(
        artifact_id=_ARTIFACT_ID,
        user_id=_USER_ID,
    )

    assert result.outcome == "stable_document_ready"
    assert result.next_action == "open_reader"
    assert result.stable_document is not None
    assert result.stable_document.stable_document_id == _STABLE_DOC_ID
    assert result.stable_document.base_id == _BASE_ID
    assert result.stable_document.record_generation == 1
    assert result.stable_document.content_sha256 == canonical_sha
    assert result.stable_document.canonical_text_sha256 == canonical_sha


async def test_candidate_document_required_returns_candidate_document_required(
    status_env: asyncpg.Pool,
) -> None:
    """Materialization succeeded + product_state=needs_confirmation → candidate_document_required."""
    await _seed_bound_environment(
        status_env,
        product_state="needs_confirmation",
        readiness_state="candidate_base_ready",
        source_text=_EXTRACTED_TEXT,
        input_metadata={
            "source_artifact_status": "available",
            "extraction_status": "succeeded",
            "extractor_name": _EXTRACTOR_NAME,
        },
    )
    await _seed_job(
        status_env,
        job_type=_EXTRACTION_JOB_TYPE,
        status="succeeded",
    )
    await _seed_job(
        status_env,
        job_type=_MATERIALIZATION_JOB_TYPE,
        status="succeeded",
    )
    await _seed_candidate_document(status_env)

    service = _build_service(status_env)
    result = await service.load_pipeline_status(
        artifact_id=_ARTIFACT_ID,
        user_id=_USER_ID,
    )

    assert result.outcome == "candidate_document_required"
    assert result.next_action == "confirm_candidate_document"
    assert result.candidate_document is not None
    assert result.candidate_document.candidate_document_id == _CANDIDATE_DOC_ID
    assert result.candidate_document.record_generation == 1
    assert result.candidate_document.canonical_text_preview.startswith("Preview text")


async def test_action_required_returns_input_rejected_or_action_required(
    status_env: asyncpg.Pool,
) -> None:
    """Materialization succeeded + product_state=action_required → input_rejected_or_action_required."""
    await _seed_bound_environment(
        status_env,
        product_state="action_required",
        source_text="Too short.",
    )
    await _seed_job(
        status_env,
        job_type=_EXTRACTION_JOB_TYPE,
        status="succeeded",
    )
    await _seed_job(
        status_env,
        job_type=_MATERIALIZATION_JOB_TYPE,
        status="succeeded",
    )

    service = _build_service(status_env)
    result = await service.load_pipeline_status(
        artifact_id=_ARTIFACT_ID,
        user_id=_USER_ID,
    )

    assert result.outcome == "input_rejected_or_action_required"
    assert result.next_action == "revise_input"
    assert result.candidate_document is None
    assert result.stable_document is None


async def test_failed_product_state_returns_input_rejected_or_action_required(
    status_env: asyncpg.Pool,
) -> None:
    """Materialization succeeded + product_state=failed → input_rejected_or_action_required."""
    await _seed_bound_environment(
        status_env,
        product_state="failed",
        source_text="Some text.",
    )
    await _seed_job(
        status_env,
        job_type=_EXTRACTION_JOB_TYPE,
        status="succeeded",
    )
    await _seed_job(
        status_env,
        job_type=_MATERIALIZATION_JOB_TYPE,
        status="succeeded",
    )

    service = _build_service(status_env)
    result = await service.load_pipeline_status(
        artifact_id=_ARTIFACT_ID,
        user_id=_USER_ID,
    )

    assert result.outcome == "input_rejected_or_action_required"
    assert result.next_action == "revise_input"


# ---------------------------------------------------------------------------
# Ownership / fail-closed tests
# ===================================================================


async def test_artifact_not_found_raises_lookup_error(
    status_env: asyncpg.Pool,
) -> None:
    await _seed_user(status_env)

    service = _build_service(status_env)
    with pytest.raises(LookupError):
        await service.load_pipeline_status(
            artifact_id=UUID("00000000-0000-0000-0000-00000000dead"),
            user_id=_USER_ID,
        )


async def test_wrong_user_raises_lookup_error(status_env: asyncpg.Pool) -> None:
    """Wrong user → LookupError (fail closed, 404)."""
    await _seed_user(status_env)
    await _seed_user(status_env, _OTHER_USER_ID)
    await _seed_artifact(
        status_env,
        user_id=_OTHER_USER_ID,
        reading_record_id=None,
        original_input_id=None,
    )

    service = _build_service(status_env)
    with pytest.raises(LookupError):
        await service.load_pipeline_status(
            artifact_id=_ARTIFACT_ID,
            user_id=_USER_ID,
        )


async def test_deleted_artifact_raises_lookup_error(
    status_env: asyncpg.Pool,
) -> None:
    """deleted_at IS NULL filter excludes soft-deleted artifacts."""
    await _seed_user(status_env)
    await _seed_artifact(
        status_env,
        reading_record_id=None,
        original_input_id=None,
        deleted_at=datetime.now(UTC),
    )

    service = _build_service(status_env)
    with pytest.raises(LookupError):
        await service.load_pipeline_status(
            artifact_id=_ARTIFACT_ID,
            user_id=_USER_ID,
        )


async def test_mismatched_original_input_raises_inconsistency_error(
    status_env: asyncpg.Pool,
) -> None:
    """original_input.reading_record_id != artifact.reading_record_id → 409."""
    other_record_id = UUID("00000000-0000-0000-0000-0000000d3e99")
    await _seed_user(status_env)
    await _seed_record(status_env, record_id=_RECORD_ID)
    await _seed_record(status_env, record_id=other_record_id)
    # original_input belongs to other_record_id, but artifact points to _RECORD_ID
    await _seed_original_input(status_env, reading_record_id=other_record_id)
    await _seed_artifact(
        status_env,
        reading_record_id=_RECORD_ID,
        original_input_id=_ORIGINAL_INPUT_ID,
    )
    await _seed_run(status_env, reading_record_id=_RECORD_ID)

    service = _build_service(status_env)
    with pytest.raises(ArtifactInputStatusQueryError):
        await service.load_pipeline_status(
            artifact_id=_ARTIFACT_ID,
            user_id=_USER_ID,
        )


async def test_bound_artifact_no_extraction_job_raises_inconsistency(
    status_env: asyncpg.Pool,
) -> None:
    """Artifact is bound but no extraction job → ArtifactInputStatusQueryError."""
    await _seed_bound_environment(status_env)

    service = _build_service(status_env)
    with pytest.raises(ArtifactInputStatusQueryError):
        await service.load_pipeline_status(
            artifact_id=_ARTIFACT_ID,
            user_id=_USER_ID,
        )


# ---------------------------------------------------------------------------
# Read-only guarantees
# ===================================================================


async def test_does_not_select_source_text(status_env: asyncpg.Pool) -> None:
    """The service must never load original_inputs.source_text.

    We verify this by setting source_text to a sentinel value and asserting
    the result.original_input only exposes has_source_text (bool), not the
    raw text.
    """
    sentinel = "SENTINEL_SOURCE_TEXT_MUST_NOT_LEAK_12345"
    await _seed_bound_environment(
        status_env,
        source_text=sentinel,
        input_metadata={
            "source_artifact_status": "available",
            "extraction_status": "succeeded",
            "extractor_name": _EXTRACTOR_NAME,
        },
    )
    await _seed_job(
        status_env,
        job_type=_EXTRACTION_JOB_TYPE,
        status="succeeded",
    )
    await _seed_job(
        status_env,
        job_type=_MATERIALIZATION_JOB_TYPE,
        status="queued",
    )

    service = _build_service(status_env)
    result = await service.load_pipeline_status(
        artifact_id=_ARTIFACT_ID,
        user_id=_USER_ID,
    )

    assert result.original_input is not None
    assert result.original_input.has_source_text is True
    # The OriginalInputSummary dataclass has no source_text field.
    assert not hasattr(result.original_input, "source_text")
    # Verify the sentinel does not appear anywhere in the result's repr.
    assert sentinel not in repr(result)


async def test_service_does_not_write_to_any_table(
    status_env: asyncpg.Pool,
) -> None:
    """The service must be read-only: no inserts/updates to business tables."""
    await _seed_bound_environment(status_env)
    await _seed_job(status_env, job_type=_EXTRACTION_JOB_TYPE, status="queued")

    # Snapshot row counts before query.
    tables_to_check = [
        "source_artifacts",
        "reading_records",
        "original_inputs",
        "reader_jobs",
        "candidate_reading_documents",
        "stable_reading_documents",
        "reading_bases",
    ]

    async def _count_rows(table_name: str) -> int:
        async with status_env.acquire() as conn:
            return await conn.fetchval(f"SELECT COUNT(*) FROM {table_name}")

    counts_before = {t: await _count_rows(t) for t in tables_to_check}

    service = _build_service(status_env)
    result = await service.load_pipeline_status(
        artifact_id=_ARTIFACT_ID,
        user_id=_USER_ID,
    )
    assert result.outcome == "extraction_queued"

    counts_after = {t: await _count_rows(t) for t in tables_to_check}
    assert counts_before == counts_after, (
        f"Row counts changed: before={counts_before}, after={counts_after}"
    )


async def test_extraction_status_returned_from_metadata(
    status_env: asyncpg.Pool,
) -> None:
    """extraction_status is read from original_inputs.metadata_json."""
    await _seed_bound_environment(
        status_env,
        source_text=_EXTRACTED_TEXT,
        input_metadata={
            "source_artifact_status": "available",
            "extraction_status": "succeeded",
            "extractor_name": _EXTRACTOR_NAME,
        },
    )
    await _seed_job(
        status_env,
        job_type=_EXTRACTION_JOB_TYPE,
        status="succeeded",
    )
    await _seed_job(
        status_env,
        job_type=_MATERIALIZATION_JOB_TYPE,
        status="queued",
    )

    service = _build_service(status_env)
    result = await service.load_pipeline_status(
        artifact_id=_ARTIFACT_ID,
        user_id=_USER_ID,
    )

    assert result.original_input is not None
    assert result.original_input.extraction_status == "succeeded"
    assert result.original_input.metadata.get("extractor_name") == _EXTRACTOR_NAME


async def test_processing_product_state_with_succeeded_materialization_raises_inconsistency(
    status_env: asyncpg.Pool,
) -> None:
    """Materialization succeeded + product_state=processing → fail closed (409).

    The materialization worker transitions the record's product_state in the
    same transaction that marks the job succeeded. If the record is still
    'processing' after materialization succeeded, the pipeline state is
    inconsistent.
    """
    await _seed_bound_environment(
        status_env,
        product_state="processing",
        source_text=_EXTRACTED_TEXT,
    )
    await _seed_job(
        status_env,
        job_type=_EXTRACTION_JOB_TYPE,
        status="succeeded",
    )
    await _seed_job(
        status_env,
        job_type=_MATERIALIZATION_JOB_TYPE,
        status="succeeded",
    )

    service = _build_service(status_env)
    with pytest.raises(ArtifactInputStatusQueryError):
        await service.load_pipeline_status(
            artifact_id=_ARTIFACT_ID,
            user_id=_USER_ID,
        )


# ===================================================================
# P1/P2 regression tests: paused jobs, half-bound, stale candidate
# ===================================================================


async def test_paused_extraction_job_raises_inconsistency(
    status_env: asyncpg.Pool,
) -> None:
    """Extraction job with status='paused' → fail closed (not treated as succeeded)."""
    await _seed_bound_environment(status_env)
    await _seed_job(
        status_env,
        job_type=_EXTRACTION_JOB_TYPE,
        status="paused",
    )

    service = _build_service(status_env)
    with pytest.raises(ArtifactInputStatusQueryError):
        await service.load_pipeline_status(
            artifact_id=_ARTIFACT_ID,
            user_id=_USER_ID,
        )


async def test_paused_materialization_job_raises_inconsistency(
    status_env: asyncpg.Pool,
) -> None:
    """Materialization job with status='paused' → fail closed."""
    await _seed_bound_environment(
        status_env,
        source_text=_EXTRACTED_TEXT,
    )
    await _seed_job(
        status_env,
        job_type=_EXTRACTION_JOB_TYPE,
        status="succeeded",
    )
    await _seed_job(
        status_env,
        job_type=_MATERIALIZATION_JOB_TYPE,
        status="paused",
    )

    service = _build_service(status_env)
    with pytest.raises(ArtifactInputStatusQueryError):
        await service.load_pipeline_status(
            artifact_id=_ARTIFACT_ID,
            user_id=_USER_ID,
        )


async def test_half_bound_record_only_raises_inconsistency(
    status_env: asyncpg.Pool,
) -> None:
    """Artifact with reading_record_id but no original_input_id → fail closed."""
    await _seed_user(status_env)
    await _seed_record(status_env)
    await _seed_run(status_env)
    await _seed_artifact(
        status_env,
        reading_record_id=_RECORD_ID,
        original_input_id=None,
    )

    service = _build_service(status_env)
    with pytest.raises(ArtifactInputStatusQueryError):
        await service.load_pipeline_status(
            artifact_id=_ARTIFACT_ID,
            user_id=_USER_ID,
        )


async def test_half_bound_input_only_raises_inconsistency(
    status_env: asyncpg.Pool,
) -> None:
    """Artifact with original_input_id but no reading_record_id → fail closed."""
    await _seed_user(status_env)
    await _seed_record(status_env)
    await _seed_original_input(status_env)
    await _seed_artifact(
        status_env,
        reading_record_id=None,
        original_input_id=_ORIGINAL_INPUT_ID,
    )

    service = _build_service(status_env)
    with pytest.raises(ArtifactInputStatusQueryError):
        await service.load_pipeline_status(
            artifact_id=_ARTIFACT_ID,
            user_id=_USER_ID,
        )


async def test_stale_candidate_document_filtered_by_generation(
    status_env: asyncpg.Pool,
) -> None:
    """Candidate from an older generation must not be returned.

    The candidate table has no unique constraint on
    (reading_record_id, record_generation). Without a generation filter,
    a stale candidate from generation=1 could be returned when the record
    is at generation=2.
    """
    stale_candidate_id = UUID("00000000-0000-0000-0000-0000000d3e0a")
    await _seed_user(status_env)
    await _seed_record(
        status_env,
        generation=2,
        product_state="needs_confirmation",
        readiness_state="candidate_base_ready",
    )
    await _seed_original_input(
        status_env,
        source_text=_EXTRACTED_TEXT,
        metadata={
            "source_artifact_status": "available",
            "extraction_status": "succeeded",
            "extractor_name": _EXTRACTOR_NAME,
        },
    )
    await _seed_artifact(status_env)
    await _seed_run(status_env)
    await _seed_job(
        status_env,
        job_type=_EXTRACTION_JOB_TYPE,
        status="succeeded",
    )
    await _seed_job(
        status_env,
        job_type=_MATERIALIZATION_JOB_TYPE,
        status="succeeded",
    )
    # Only a stale candidate at generation=1 — record is at generation=2.
    await _seed_candidate_document(
        status_env,
        candidate_document_id=stale_candidate_id,
        record_generation=1,
    )

    service = _build_service(status_env)
    with pytest.raises(ArtifactInputStatusQueryError):
        await service.load_pipeline_status(
            artifact_id=_ARTIFACT_ID,
            user_id=_USER_ID,
        )


async def test_current_generation_candidate_returned_over_stale(
    status_env: asyncpg.Pool,
) -> None:
    """When candidates exist at both gen=1 and gen=2, return gen=2."""
    stale_candidate_id = UUID("00000000-0000-0000-0000-0000000d3e0a")
    current_candidate_id = UUID("00000000-0000-0000-0000-0000000d3e0b")
    await _seed_user(status_env)
    await _seed_record(
        status_env,
        generation=2,
        product_state="needs_confirmation",
        readiness_state="candidate_base_ready",
    )
    await _seed_original_input(
        status_env,
        source_text=_EXTRACTED_TEXT,
        metadata={
            "source_artifact_status": "available",
            "extraction_status": "succeeded",
            "extractor_name": _EXTRACTOR_NAME,
        },
    )
    await _seed_artifact(status_env)
    await _seed_run(status_env)
    await _seed_job(
        status_env,
        job_type=_EXTRACTION_JOB_TYPE,
        status="succeeded",
    )
    await _seed_job(
        status_env,
        job_type=_MATERIALIZATION_JOB_TYPE,
        status="succeeded",
    )
    await _seed_candidate_document(
        status_env,
        candidate_document_id=stale_candidate_id,
        record_generation=1,
        canonical_text_preview="Stale preview from generation 1.",
    )
    await _seed_candidate_document(
        status_env,
        candidate_document_id=current_candidate_id,
        record_generation=2,
        canonical_text_preview="Current preview from generation 2.",
    )

    service = _build_service(status_env)
    result = await service.load_pipeline_status(
        artifact_id=_ARTIFACT_ID,
        user_id=_USER_ID,
    )

    assert result.outcome == "candidate_document_required"
    assert result.candidate_document is not None
    assert result.candidate_document.candidate_document_id == current_candidate_id
    assert result.candidate_document.record_generation == 2
    assert "generation 2" in result.candidate_document.canonical_text_preview
