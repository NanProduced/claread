"""Tests for D6-I3O Materialization Job Runtime Integration.

Covers:
- extraction worker success → durable enqueue of materialization job
  (same transaction, correct payload, idempotency_key)
- materialization worker stable path → article_ready/base/stable doc/job succeeded
- materialization worker candidate path → candidate_base_ready/job succeeded
- materialization worker rejected path → action_required/job succeeded
- active_base already exists → superseded
- input/artifact mismatch → failed_terminal
- no duplicate job on idempotency key
"""

from __future__ import annotations

import hashlib
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.database.connection import init_connection
from app.services.reader_orchestration.artifact_extraction_worker import (
    ArtifactExtractionResult,
    ArtifactExtractionWorkerService,
    MATERIALIZATION_JOB_SOURCE,
    MATERIALIZATION_JOB_TYPE,
    MATERIALIZATION_OPERATION_FINGERPRINT,
    MATERIALIZATION_TARGET_TYPE,
)
from app.services.reader_orchestration.artifact_input_application_service import (
    EXTRACTION_JOB_TYPE,
    EXTRACTION_OPERATION_FINGERPRINT,
    EXTRACTION_TARGET_TYPE,
)
from app.services.reader_orchestration.artifact_materialization_worker import (
    ArtifactMaterializationWorkerService,
    MaterializationJobProcessResult,
)
from app.services.reader_orchestration.job_runtime import ReaderJobRuntime

pytestmark = pytest.mark.anyio

REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE_ARTIFACTS_SQL = "SELECT 1"  # folded into infra/migrations/0001_initial.sql

from tests.test_reader_orchestration_schema_baseline import BASELINE_SQL, DATABASE_URL  # noqa: E402

# 0004 (document_blocks) is now in BASELINE_SQL, so the I3O schema is
# BASELINE_SQL + 0007 (reader_source_artifacts).
I3O_SCHEMA_SQL = BASELINE_SQL + "\n" + SOURCE_ARTIFACTS_SQL

# Fixed UUIDs for deterministic seeding
_USER_ID = UUID("00000000-0000-0000-0000-000000000e01")
_RECORD_ID = UUID("00000000-0000-0000-0000-000000000e02")
_ORIGINAL_INPUT_ID = UUID("00000000-0000-0000-0000-000000000e03")
_ARTIFACT_ID = UUID("00000000-0000-0000-0000-000000000e04")
_EXTRACTION_RUN_ID = UUID("00000000-0000-0000-0000-000000000e05")

# Stable-ready text: ~60 English words, simple structure
_STABLE_TEXT = (
    "The quick brown fox jumps over the lazy dog near the riverbank. "
    "A small bird sings in the tree above them. The morning sun casts "
    "long shadows across the meadow. Children laugh and play in the "
    "distance while a gentle breeze rustles the leaves. This peaceful "
    "scene captures a moment of quiet harmony in nature."
)

# Candidate-requiring text: >8000 words worth of content with markdown tables
_CANDIDATE_MD = (
    "# Large Document Requiring Candidate Review\n\n"
    + "This is a paragraph with enough content to exceed the word limit "
    "for stable document ready path and trigger candidate creation.\n\n"
    + "| Column A | Column B |\n|----------|----------|\n"
    + "| cell1 | cell2 |\n\n"
    + "\n\n".join(
        f"Section {i}: " + ("word " * 200)
        for i in range(50)
    )
)

# Rejected text: too short (< 50 English words)
_REJECTED_TEXT = "Hello world. This is too short."

_EXTRACTED_TEXT = "This is the extracted text from the PDF artifact."
_DEFAULT_CONTENT_SHA256 = "a" * 64


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
async def i3o_env() -> asyncpg.Pool:
    schema_name = f"test_i3o_{uuid4().hex}"
    admin_conn = await _connect_admin()
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(I3O_SCHEMA_SQL)
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


async def _seed_extraction_environment(
    pool: asyncpg.Pool,
    *,
    content_type: str = "application/pdf",
    source_filename: str = "artifact.pdf",
) -> UUID:
    """Seed user, record, original_input (no source_text yet), source_artifact,
    extraction run + extraction job. Returns the extraction job_id.

    This mirrors what ArtifactInputApplicationService.submit_input would create.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (id) VALUES ($1) ON CONFLICT DO NOTHING",
            _USER_ID,
        )
        await conn.execute(
            """
            INSERT INTO reading_records (
                id, user_id, source_type, title, language,
                lifecycle_status, product_state, readiness_state, generation
            )
            VALUES ($1, $2, 'pdf', 'I3O Test', 'en',
                    'active', 'processing', 'submitted', 1)
            """,
            _RECORD_ID,
            _USER_ID,
        )
        source_ref_json = {
            "artifact_id": str(_ARTIFACT_ID),
            "storage_provider": "oss",
            "bucket": "claread-dev",
            "endpoint": "https://oss-cn-shenzhen.aliyuncs.com",
            "object_key": "dev/original-inputs/test/artifact.pdf",
            "artifact_kind": "original_upload",
            "content_type": content_type,
            "byte_size": 1024,
            "source_filename": source_filename,
        }
        await conn.execute(
            """
            INSERT INTO original_inputs (
                id, reading_record_id, user_id, input_type,
                source_text, source_ref_json, metadata_json, content_sha256
            )
            VALUES ($1, $2, $3, 'file_ref',
                    NULL, $4::jsonb,
                    '{"source_artifact_status": "available"}'::jsonb,
                    $5)
            """,
            _ORIGINAL_INPUT_ID,
            _RECORD_ID,
            _USER_ID,
            source_ref_json,
            _DEFAULT_CONTENT_SHA256,
        )
        await conn.execute(
            """
            INSERT INTO source_artifacts (
                id, reading_record_id, original_input_id, user_id,
                artifact_kind, storage_provider, bucket, object_key, endpoint,
                content_type, byte_size, content_sha256, source_filename, status
            )
            VALUES ($1, $2, $3, $4,
                    'original_upload', 'oss', 'claread-dev',
                    'dev/original-inputs/test/artifact.pdf',
                    'https://oss-cn-shenzhen.aliyuncs.com',
                    $5, 1024, $6, $7, 'available')
            """,
            _ARTIFACT_ID,
            _RECORD_ID,
            _ORIGINAL_INPUT_ID,
            _USER_ID,
            content_type,
            _DEFAULT_CONTENT_SHA256,
            source_filename,
        )
        await conn.execute(
            """
            INSERT INTO reader_runs (
                reading_record_id, user_id, run_type, status,
                record_generation, envelope_json, policy_version, trigger_kind, id
            )
            VALUES ($1, $2, 'input_artifact_extraction', 'queued', 1,
                    '{}'::jsonb, 'reader_input_artifact_extraction_v1', 'system', $3)
            """,
            _RECORD_ID,
            _USER_ID,
            _EXTRACTION_RUN_ID,
        )

        input_json = {
            "source": "artifact_input",
            "reading_record_id": str(_RECORD_ID),
            "original_input_id": str(_ORIGINAL_INPUT_ID),
            "source_artifact_id": str(_ARTIFACT_ID),
            "artifact_kind": "original_upload",
            "storage_provider": "oss",
            "bucket": "claread-dev",
            "endpoint": "https://oss-cn-shenzhen.aliyuncs.com",
            "object_key": "dev/original-inputs/test/artifact.pdf",
            "content_type": content_type,
            "byte_size": 1024,
            "content_sha256": _DEFAULT_CONTENT_SHA256,
            "source_filename": source_filename,
        }

        job_id = await conn.fetchval(
            """
            INSERT INTO reader_jobs (
                reading_record_id, base_id, run_id, user_id,
                job_type, target_type, target_key, status,
                priority, expected_generation, operation_fingerprint,
                idempotency_key, input_json, max_attempts
            )
            VALUES ($1, NULL, $2, $3,
                    $4, $5, $6, 'queued',
                    0, 1, $7,
                    $8, $9::jsonb, 3)
            RETURNING id
            """,
            _RECORD_ID,
            _EXTRACTION_RUN_ID,
            _USER_ID,
            EXTRACTION_JOB_TYPE,
            EXTRACTION_TARGET_TYPE,
            str(_ARTIFACT_ID),
            EXTRACTION_OPERATION_FINGERPRINT,
            f"extraction-test-{uuid4().hex}",
            input_json,
        )
    assert isinstance(job_id, UUID)
    return job_id


async def _seed_materialization_environment(
    pool: asyncpg.Pool,
    *,
    source_text: str = _STABLE_TEXT,
    content_type: str = "text/plain",
    source_filename: str = "notes.txt",
    active_base_id: UUID | None = None,
    product_state: str = "processing",
    readiness_state: str = "submitted",
    generation: int = 1,
) -> UUID:
    """Seed user, record (with source_text already populated), original_input,
    source_artifact, materialization run + materialization job.

    Returns the materialization job_id. This simulates the state AFTER
    extraction has succeeded and the materialization job has been enqueued.
    """
    source_sha = hashlib.sha256(source_text.encode("utf-8")).hexdigest()

    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (id) VALUES ($1) ON CONFLICT DO NOTHING",
            _USER_ID,
        )
        await conn.execute(
            """
            INSERT INTO reading_records (
                id, user_id, source_type, title, language,
                lifecycle_status, product_state, readiness_state,
                generation, active_base_id
            )
            VALUES ($1, $2, 'text', 'I3O Mat Test', 'en',
                    'active', $3, $4, $5, $6)
            """,
            _RECORD_ID,
            _USER_ID,
            product_state,
            readiness_state,
            generation,
            active_base_id,
        )
        source_ref_json = {
            "artifact_id": str(_ARTIFACT_ID),
            "storage_provider": "oss",
            "bucket": "claread-dev",
            "object_key": "dev/test/notes.txt",
            "artifact_kind": "original_upload",
            "content_type": content_type,
            "source_filename": source_filename,
        }
        await conn.execute(
            """
            INSERT INTO original_inputs (
                id, reading_record_id, user_id, input_type,
                source_text, source_ref_json, metadata_json, content_sha256
            )
            VALUES ($1, $2, $3, 'file_ref',
                    $4, $5::jsonb,
                    '{"extraction_status": "succeeded"}'::jsonb,
                    $6)
            """,
            _ORIGINAL_INPUT_ID,
            _RECORD_ID,
            _USER_ID,
            source_text,
            source_ref_json,
            source_sha,
        )
        # L2：模拟 extraction 完成态——confirmed_source_documents 行是
        # 正文唯一载体（revision=1, edit_source='extraction'），
        # materialization 从该行读取。
        markdown_text = source_text.replace("\r\n", "\n").replace("\r", "\n")
        await conn.execute(
            """
            INSERT INTO confirmed_source_documents (
                id, reading_record_id, user_id, record_generation,
                original_input_id, markdown_text, revision,
                content_sha256, status, edit_source
            )
            VALUES ($1, $2, $3, $4, $5, $6, 1, $7, 'draft', 'extraction')
            """,
            uuid4(),
            _RECORD_ID,
            _USER_ID,
            generation,
            _ORIGINAL_INPUT_ID,
            markdown_text,
            hashlib.sha256(markdown_text.encode("utf-8")).hexdigest(),
        )
        await conn.execute(
            """
            INSERT INTO source_artifacts (
                id, reading_record_id, original_input_id, user_id,
                artifact_kind, storage_provider, bucket, object_key, endpoint,
                content_type, byte_size, content_sha256, source_filename, status
            )
            VALUES ($1, $2, $3, $4,
                    'original_upload', 'oss', 'claread-dev',
                    'dev/test/notes.txt',
                    'https://oss-cn-shenzhen.aliyuncs.com',
                    $5, $6, $7, $8, 'available')
            """,
            _ARTIFACT_ID,
            _RECORD_ID,
            _ORIGINAL_INPUT_ID,
            _USER_ID,
            content_type,
            len(source_text.encode("utf-8")),
            source_sha,
            source_filename,
        )

        # Create a materialization run + job (mirrors what the extraction
        # worker's _enqueue_materialization_job would create)
        run_id = await conn.fetchval(
            """
            INSERT INTO reader_runs (
                reading_record_id, user_id, run_type, status,
                record_generation, envelope_json, policy_version, trigger_kind
            )
            VALUES ($1, $2, $3, 'queued', $4,
                    '{}'::jsonb, 'reader_extracted_artifact_materialization_v1',
                    'system')
            RETURNING id
            """,
            _RECORD_ID,
            _USER_ID,
            MATERIALIZATION_JOB_TYPE,
            generation,
        )

        input_json = {
            "source": MATERIALIZATION_JOB_SOURCE,
            "reading_record_id": str(_RECORD_ID),
            "original_input_id": str(_ORIGINAL_INPUT_ID),
            "source_artifact_id": str(_ARTIFACT_ID),
            "expected_generation": generation,
        }
        input_hash = hashlib.sha256(
            f"{_ARTIFACT_ID}:{generation}".encode("utf-8")
        ).hexdigest()

        job_id = await conn.fetchval(
            """
            INSERT INTO reader_jobs (
                reading_record_id, base_id, run_id, user_id,
                job_type, target_type, target_key, status,
                priority, expected_generation, operation_fingerprint,
                idempotency_key, input_hash, input_json, max_attempts
            )
            VALUES ($1, NULL, $2, $3,
                    $4, $5, $6, 'queued',
                    0, $7, $8,
                    $9, $10, $11::jsonb, 3)
            RETURNING id
            """,
            _RECORD_ID,
            run_id,
            _USER_ID,
            MATERIALIZATION_JOB_TYPE,
            MATERIALIZATION_TARGET_TYPE,
            str(_ARTIFACT_ID),
            generation,
            MATERIALIZATION_OPERATION_FINGERPRINT,
            f"{MATERIALIZATION_OPERATION_FINGERPRINT}:{_ARTIFACT_ID}",
            input_hash,
            input_json,
        )
    assert isinstance(job_id, UUID)
    return job_id


async def _insert_reading_base(
    pool: asyncpg.Pool,
    *,
    base_text: str = "Existing base text.",
    record_generation: int = 1,
) -> UUID:
    """Insert a reading_bases row for the seeded record and return its id.

    Used by tests that need ``reading_records.active_base_id`` to point at a
    real base row (the FK constraint ``fk_reading_records_active_base``
    requires ``(id, reading_record_id, record_generation)`` to match).
    """
    base_sha = hashlib.sha256(base_text.encode("utf-8")).hexdigest()
    base_id = uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO reading_bases (
                id, reading_record_id, base_version, record_generation,
                text, content_sha256, content_utf16_length,
                canonicalizer_version, builder_version, segmenter_version,
                language, title_snapshot, navigation_json, status
            )
            VALUES ($1, $2, 1, $3, $4, $5, $6,
                    'd3-p1-canonicalizer', 'd3-p1-builder', 'd3-p1-segmenter',
                    'en', 'Existing', '{"units":[]}'::jsonb, 'active')
            """,
            base_id,
            _RECORD_ID,
            record_generation,
            base_text,
            base_sha,
            len(base_text),
        )
    return base_id


# ---------------------------------------------------------------------------
# Worker / query helpers
# ---------------------------------------------------------------------------


class _FakeExtractionProvider:
    """Fake extraction provider for tests."""

    def __init__(
        self,
        *,
        result: ArtifactExtractionResult | None = None,
    ) -> None:
        self._result = result
        self.calls = 0

    async def extract(self, context) -> ArtifactExtractionResult:
        self.calls += 1
        assert self._result is not None
        return self._result


def _build_extraction_worker(
    pool: asyncpg.Pool,
    *,
    provider: _FakeExtractionProvider,
) -> ArtifactExtractionWorkerService:
    return ArtifactExtractionWorkerService(
        pool=pool,
        job_runtime=ReaderJobRuntime(pool=pool),
        provider=provider,
    )


def _build_materialization_worker(
    pool: asyncpg.Pool,
) -> ArtifactMaterializationWorkerService:
    return ArtifactMaterializationWorkerService(
        pool=pool,
        job_runtime=ReaderJobRuntime(pool=pool),
    )


async def _fetch_job(pool: asyncpg.Pool, job_id: UUID) -> asyncpg.Record:
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM reader_jobs WHERE id = $1", job_id)


async def _fetch_materialization_job(pool: asyncpg.Pool) -> asyncpg.Record:
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            SELECT * FROM reader_jobs
            WHERE job_type = $1
            ORDER BY created_at ASC
            LIMIT 1
            """,
            MATERIALIZATION_JOB_TYPE,
        )


async def _fetch_record(pool: asyncpg.Pool) -> asyncpg.Record:
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT * FROM reading_records WHERE id = $1", _RECORD_ID
        )


async def _count_materialization_jobs(pool: asyncpg.Pool) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM reader_jobs WHERE job_type = $1",
            MATERIALIZATION_JOB_TYPE,
        )


async def _count_stable_documents(pool: asyncpg.Pool) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM stable_reading_documents WHERE reading_record_id = $1",
            _RECORD_ID,
        )


async def _count_candidates(pool: asyncpg.Pool) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM candidate_reading_documents WHERE reading_record_id = $1",
            _RECORD_ID,
        )


async def _count_reading_bases(pool: asyncpg.Pool) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM reading_bases WHERE reading_record_id = $1",
            _RECORD_ID,
        )


async def _count_article_ready_events(pool: asyncpg.Pool) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM reader_events WHERE reading_record_id = $1 "
            "AND event_type = 'article_ready'",
            _RECORD_ID,
        )


# ---------------------------------------------------------------------------
# Tests: extraction worker enqueues materialization job
# ---------------------------------------------------------------------------


async def test_extraction_success_enqueues_materialization_job(
    i3o_env: asyncpg.Pool,
) -> None:
    """After extraction succeeds, a materialization job must be enqueued in the
    same transaction with the correct payload and idempotency_key."""
    await _seed_extraction_environment(i3o_env)
    provider = _FakeExtractionProvider(
        result=ArtifactExtractionResult(
            extracted_text=_EXTRACTED_TEXT,
            extractor_name="fake-ocr-provider",
            quality={"confidence": 0.95},
        ),
    )
    worker = _build_extraction_worker(i3o_env, provider=provider)

    result = await worker.process_next(
        lease_owner="extraction-worker",
        lease_duration=timedelta(seconds=30),
    )

    assert result is not None
    assert result.status == "succeeded"

    # A materialization job must exist
    assert await _count_materialization_jobs(i3o_env) == 1
    mat_job = await _fetch_materialization_job(i3o_env)
    assert mat_job["job_type"] == MATERIALIZATION_JOB_TYPE
    assert mat_job["target_type"] == MATERIALIZATION_TARGET_TYPE
    assert mat_job["target_key"] == str(_ARTIFACT_ID)
    assert mat_job["status"] == "queued"
    assert mat_job["expected_generation"] == 1
    assert (
        mat_job["operation_fingerprint"]
        == MATERIALIZATION_OPERATION_FINGERPRINT
    )
    assert (
        mat_job["idempotency_key"]
        == f"{MATERIALIZATION_OPERATION_FINGERPRINT}:{_ARTIFACT_ID}"
    )

    # Verify input_json payload contract
    input_json = mat_job["input_json"]
    assert input_json["source"] == MATERIALIZATION_JOB_SOURCE
    assert input_json["reading_record_id"] == str(_RECORD_ID)
    assert input_json["original_input_id"] == str(_ORIGINAL_INPUT_ID)
    assert input_json["source_artifact_id"] == str(_ARTIFACT_ID)
    assert input_json["expected_generation"] == 1

    # A reader_runs row must exist for the materialization job
    async with i3o_env.acquire() as conn:
        mat_run = await conn.fetchrow(
            """
            SELECT * FROM reader_runs
            WHERE run_type = $1
            """,
            MATERIALIZATION_JOB_TYPE,
        )
    assert mat_run is not None
    assert mat_run["status"] == "queued"
    assert mat_run["record_generation"] == 1


async def test_extraction_success_does_not_enqueue_duplicate_materialization_job(
    i3o_env: asyncpg.Pool,
) -> None:
    """Running the extraction worker a second time (no new extraction job)
    must not enqueue a second materialization job.

    The first run consumes the extraction job and enqueues one materialization
    job. The second run finds no extraction job to claim and returns None.
    Duplicate active-job prevention (if the same extraction job were retried)
    is covered by ``uq_reader_jobs_active_fingerprint``, not by this test.
    """
    await _seed_extraction_environment(i3o_env)
    provider = _FakeExtractionProvider(
        result=ArtifactExtractionResult(
            extracted_text=_EXTRACTED_TEXT,
            extractor_name="fake-ocr-provider",
        ),
    )
    worker = _build_extraction_worker(i3o_env, provider=provider)

    # First run: should succeed and enqueue materialization job
    result1 = await worker.process_next(
        lease_owner="extraction-worker",
        lease_duration=timedelta(seconds=30),
    )
    assert result1 is not None
    assert result1.status == "succeeded"
    assert await _count_materialization_jobs(i3o_env) == 1

    # Second run: no more extraction jobs to claim
    result2 = await worker.process_next(
        lease_owner="extraction-worker",
        lease_duration=timedelta(seconds=30),
    )
    assert result2 is None
    # Still only 1 materialization job
    assert await _count_materialization_jobs(i3o_env) == 1


# ---------------------------------------------------------------------------
# Tests: materialization worker stable path
# ---------------------------------------------------------------------------


async def test_materialization_worker_stable_path_succeeds(
    i3o_env: asyncpg.Pool,
) -> None:
    """Materialization worker stable path → article_ready/base/stable doc/job succeeded."""
    job_id = await _seed_materialization_environment(
        i3o_env,
        source_text=_STABLE_TEXT,
        content_type="text/plain",
        source_filename="notes.txt",
    )
    worker = _build_materialization_worker(i3o_env)

    result = await worker.process_next(
        lease_owner="materialization-worker",
        lease_duration=timedelta(seconds=30),
    )

    assert result is not None
    assert result.status == "succeeded"
    assert result.outcome == "stable_document_ready"
    assert result.stable_document_id is not None
    assert result.base_id is not None

    job = await _fetch_job(i3o_env, job_id)
    assert job["status"] == "succeeded"
    output_ref = job["output_ref_json"]
    assert output_ref["outcome"] == "stable_document_ready"
    assert output_ref["stable_document_id"] == str(result.stable_document_id)
    assert output_ref["base_id"] == str(result.base_id)

    # Verify DB state
    record = await _fetch_record(i3o_env)
    assert record["readiness_state"] == "article_ready"
    assert record["product_state"] == "readable_enhancing"
    assert record["active_base_id"] == result.base_id

    assert await _count_stable_documents(i3o_env) == 1
    assert await _count_reading_bases(i3o_env) == 1
    assert await _count_article_ready_events(i3o_env) == 1


# ---------------------------------------------------------------------------
# Tests: materialization worker candidate path
# ---------------------------------------------------------------------------


async def test_materialization_worker_candidate_path_succeeds(
    i3o_env: asyncpg.Pool,
) -> None:
    """Materialization worker candidate path → candidate_base_ready/job succeeded."""
    job_id = await _seed_materialization_environment(
        i3o_env,
        source_text=_CANDIDATE_MD,
        content_type="text/markdown",
        source_filename="large.md",
    )
    worker = _build_materialization_worker(i3o_env)

    result = await worker.process_next(
        lease_owner="materialization-worker",
        lease_duration=timedelta(seconds=30),
    )

    assert result is not None
    assert result.status == "succeeded"
    assert result.outcome == "candidate_document_required"
    assert result.candidate_document_id is not None

    job = await _fetch_job(i3o_env, job_id)
    assert job["status"] == "succeeded"
    output_ref = job["output_ref_json"]
    assert output_ref["outcome"] == "candidate_document_required"
    assert output_ref["candidate_document_id"] == str(result.candidate_document_id)

    # Verify DB state
    record = await _fetch_record(i3o_env)
    assert record["readiness_state"] == "candidate_base_ready"
    assert record["product_state"] == "needs_confirmation"
    assert record["active_base_id"] is None

    assert await _count_candidates(i3o_env) == 1
    assert await _count_stable_documents(i3o_env) == 0
    assert await _count_reading_bases(i3o_env) == 0


# ---------------------------------------------------------------------------
# Tests: materialization worker rejected path
# ---------------------------------------------------------------------------


async def test_materialization_worker_rejected_path_succeeds(
    i3o_env: asyncpg.Pool,
) -> None:
    """Materialization worker rejected path → action_required/job succeeded."""
    job_id = await _seed_materialization_environment(
        i3o_env,
        source_text=_REJECTED_TEXT,
        content_type="text/plain",
        source_filename="short.txt",
    )
    worker = _build_materialization_worker(i3o_env)

    result = await worker.process_next(
        lease_owner="materialization-worker",
        lease_duration=timedelta(seconds=30),
    )

    assert result is not None
    assert result.status == "succeeded"
    assert result.outcome == "input_rejected_or_action_required"
    assert result.stable_document_id is None
    assert result.candidate_document_id is None

    job = await _fetch_job(i3o_env, job_id)
    assert job["status"] == "succeeded"
    output_ref = job["output_ref_json"]
    assert output_ref["outcome"] == "input_rejected_or_action_required"

    # Verify DB state
    record = await _fetch_record(i3o_env)
    assert record["product_state"] == "action_required"
    assert record["readiness_state"] == "submitted"
    assert record["active_base_id"] is None

    assert await _count_candidates(i3o_env) == 0
    assert await _count_stable_documents(i3o_env) == 0
    assert await _count_reading_bases(i3o_env) == 0


# ---------------------------------------------------------------------------
# Tests: superseded paths
# ---------------------------------------------------------------------------


async def test_materialization_worker_active_base_already_exists_superseded(
    i3o_env: asyncpg.Pool,
) -> None:
    """If active_base_id is already set when the worker claims the job, the
    fence at claim time marks the job superseded (active_base_already_exists)."""
    # Seed environment WITHOUT active_base_id first
    job_id = await _seed_materialization_environment(
        i3o_env,
        source_text=_STABLE_TEXT,
    )
    # Create a reading_base row, then set active_base_id to point to it.
    # The FK constraint fk_reading_records_active_base requires the base
    # to exist with matching (id, reading_record_id, record_generation).
    base_text = "Existing base text."
    base_id = await _insert_reading_base(i3o_env, base_text=base_text)
    async with i3o_env.acquire() as conn:
        await conn.execute(
            "UPDATE reading_records SET active_base_id = $2 WHERE id = $1",
            _RECORD_ID,
            base_id,
        )

    worker = _build_materialization_worker(i3o_env)

    result = await worker.process_next(
        lease_owner="materialization-worker",
        lease_duration=timedelta(seconds=30),
    )

    # claim_next_job validates the fence and auto-supersedes jobs where
    # active_base_id is already set. The job never reaches the worker, so
    # process_next returns None (no claimable job).
    assert result is None

    job = await _fetch_job(i3o_env, job_id)
    assert job["status"] == "superseded"
    assert job["rationale_code"] == "active_base_already_exists"

    # No materialization writes should have happened
    assert await _count_stable_documents(i3o_env) == 0
    assert await _count_candidates(i3o_env) == 0


async def test_materialization_worker_state_already_advanced_superseded(
    i3o_env: asyncpg.Pool,
) -> None:
    """If the record has already advanced past processing/submitted (e.g.,
    materialization already ran), the I3N service raises with
    reason_code=materialization_already_run and the worker transitions to
    superseded."""
    job_id = await _seed_materialization_environment(
        i3o_env,
        source_text=_STABLE_TEXT,
        product_state="readable_enhancing",
        readiness_state="article_ready",
    )
    worker = _build_materialization_worker(i3o_env)

    result = await worker.process_next(
        lease_owner="materialization-worker",
        lease_duration=timedelta(seconds=30),
    )

    assert result is not None
    assert result.status == "superseded"

    job = await _fetch_job(i3o_env, job_id)
    assert job["status"] == "superseded"
    assert job["rationale_code"] == "materialization_already_run"


# ---------------------------------------------------------------------------
# Tests: failed_terminal paths
# ---------------------------------------------------------------------------


async def test_materialization_worker_input_artifact_mismatch_failed_terminal(
    i3o_env: asyncpg.Pool,
) -> None:
    """If input_json.original_input_id does not match any real original_input
    for this record, the I3N service raises with reason_code=
    original_input_not_found and the worker transitions to failed_terminal."""
    # Seed environment, then corrupt the original_input_id in the job payload
    job_id = await _seed_materialization_environment(
        i3o_env,
        source_text=_STABLE_TEXT,
    )
    wrong_input_id = UUID("00000000-0000-0000-0000-000000000eff")

    async with i3o_env.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT input_json FROM reader_jobs WHERE id = $1", job_id,
        )
        new_input_json = dict(row["input_json"])
        new_input_json["original_input_id"] = str(wrong_input_id)
        await conn.execute(
            "UPDATE reader_jobs SET input_json = $2 WHERE id = $1",
            job_id,
            new_input_json,
        )

    worker = _build_materialization_worker(i3o_env)

    result = await worker.process_next(
        lease_owner="materialization-worker",
        lease_duration=timedelta(seconds=30),
    )

    assert result is not None
    assert result.status == "failed_terminal"

    job = await _fetch_job(i3o_env, job_id)
    assert job["status"] == "failed_terminal"
    assert job["failure_code"] == "original_input_not_found"
    assert job["rationale_code"] == "original_input_not_found"

    # No materialization writes should have happened
    assert await _count_stable_documents(i3o_env) == 0
    assert await _count_candidates(i3o_env) == 0


async def test_materialization_worker_artifact_not_found_failed_terminal(
    i3o_env: asyncpg.Pool,
) -> None:
    """If input_json.source_artifact_id does not match any real source_artifact,
    the I3N service raises with reason_code=source_artifact_not_found and the
    worker transitions to failed_terminal."""
    job_id = await _seed_materialization_environment(
        i3o_env,
        source_text=_STABLE_TEXT,
    )
    wrong_artifact_id = UUID("00000000-0000-0000-0000-000000000efe")

    async with i3o_env.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT input_json FROM reader_jobs WHERE id = $1", job_id,
        )
        new_input_json = dict(row["input_json"])
        new_input_json["source_artifact_id"] = str(wrong_artifact_id)
        await conn.execute(
            """
            UPDATE reader_jobs
            SET input_json = $2, target_key = $3
            WHERE id = $1
            """,
            job_id,
            new_input_json,
            str(wrong_artifact_id),
        )

    worker = _build_materialization_worker(i3o_env)

    result = await worker.process_next(
        lease_owner="materialization-worker",
        lease_duration=timedelta(seconds=30),
    )

    assert result is not None
    assert result.status == "failed_terminal"

    job = await _fetch_job(i3o_env, job_id)
    assert job["status"] == "failed_terminal"
    assert job["failure_code"] == "source_artifact_not_found"


async def test_materialization_worker_input_json_reading_record_mismatch_failed_terminal(
    i3o_env: asyncpg.Pool,
) -> None:
    """If input_json.reading_record_id does not match claim.reading_record_id,
    the worker fails closed with input_json_invalid."""
    job_id = await _seed_materialization_environment(
        i3o_env,
        source_text=_STABLE_TEXT,
    )
    wrong_record_id = UUID("00000000-0000-0000-0000-000000000efd")

    async with i3o_env.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT input_json FROM reader_jobs WHERE id = $1", job_id,
        )
        new_input_json = dict(row["input_json"])
        new_input_json["reading_record_id"] = str(wrong_record_id)
        await conn.execute(
            "UPDATE reader_jobs SET input_json = $2 WHERE id = $1",
            job_id,
            new_input_json,
        )

    worker = _build_materialization_worker(i3o_env)

    result = await worker.process_next(
        lease_owner="materialization-worker",
        lease_duration=timedelta(seconds=30),
    )

    assert result is not None
    assert result.status == "failed_terminal"

    job = await _fetch_job(i3o_env, job_id)
    assert job["status"] == "failed_terminal"
    assert job["failure_code"] == "input_json_invalid"


# ---------------------------------------------------------------------------
# Tests: duplicate enqueue prevention
# ---------------------------------------------------------------------------


async def test_no_duplicate_active_materialization_job_on_active_fingerprint(
    i3o_env: asyncpg.Pool,
) -> None:
    """A second active materialization job with the same fingerprint must fail.

    Duplicate enqueue prevention is provided by the partial unique index
    ``uq_reader_jobs_active_fingerprint`` (scoped to statuses
    ``queued/claimed/retry_later/paused``), NOT by ``idempotency_key``.
    ``_enqueue_materialization_job`` creates a NEW ``reader_runs`` row per
    call, so the ``(run_id, idempotency_key)`` constraint cannot fire across
    runs. The active-fingerprint index is what blocks a second active job with
    the same ``(reading_record_id, base_id=NULL, job_type, target_type,
    target_key, expected_generation, operation_fingerprint)``.
    """
    await _seed_materialization_environment(
        i3o_env,
        source_text=_STABLE_TEXT,
    )

    # Attempt to insert a second materialization job with a NEW run but the
    # same active fingerprint — must raise a unique violation.
    async with i3o_env.acquire() as conn:
        run_id = await conn.fetchval(
            """
            INSERT INTO reader_runs (
                reading_record_id, user_id, run_type, status,
                record_generation, envelope_json, policy_version, trigger_kind
            )
            VALUES ($1, $2, $3, 'queued', 1,
                    '{}'::jsonb, 'reader_extracted_artifact_materialization_v1',
                    'system')
            RETURNING id
            """,
            _RECORD_ID,
            _USER_ID,
            MATERIALIZATION_JOB_TYPE,
        )
        input_json = {
            "source": MATERIALIZATION_JOB_SOURCE,
            "reading_record_id": str(_RECORD_ID),
            "original_input_id": str(_ORIGINAL_INPUT_ID),
            "source_artifact_id": str(_ARTIFACT_ID),
            "expected_generation": 1,
        }
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                """
                INSERT INTO reader_jobs (
                    reading_record_id, base_id, run_id, user_id,
                    job_type, target_type, target_key, status,
                    priority, expected_generation, operation_fingerprint,
                    idempotency_key, input_json, max_attempts
                )
                VALUES ($1, NULL, $2, $3,
                        $4, $5, $6, 'queued',
                        0, 1, $7,
                        $8, $9::jsonb, 3)
                """,
                _RECORD_ID,
                run_id,
                _USER_ID,
                MATERIALIZATION_JOB_TYPE,
                MATERIALIZATION_TARGET_TYPE,
                str(_ARTIFACT_ID),
                MATERIALIZATION_OPERATION_FINGERPRINT,
                f"{MATERIALIZATION_OPERATION_FINGERPRINT}:{_ARTIFACT_ID}",
                input_json,
            )

    # Only the original materialization job should exist
    assert await _count_materialization_jobs(i3o_env) == 1


# ---------------------------------------------------------------------------
# Tests: no job available
# ---------------------------------------------------------------------------


async def test_materialization_worker_no_job_returns_none(
    i3o_env: asyncpg.Pool,
) -> None:
    """If no materialization job is available, process_next returns None."""
    # Seed extraction environment (no materialization job enqueued)
    await _seed_extraction_environment(i3o_env)
    worker = _build_materialization_worker(i3o_env)

    result = await worker.process_next(
        lease_owner="materialization-worker",
        lease_duration=timedelta(seconds=30),
    )

    assert result is None
