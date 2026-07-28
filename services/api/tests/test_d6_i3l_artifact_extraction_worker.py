from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.database.connection import init_connection
from app.services.reader_orchestration.artifact_extraction_worker import (
    ArtifactExtractionError,
    ArtifactExtractionResult,
    ArtifactExtractionWorkerService,
    FAILURE_CODE_ARTIFACT_NOT_BOUND,
    FAILURE_CODE_EXTRACTION_EMPTY_TEXT,
    FAILURE_CODE_INPUT_JSON_INVALID,
)
from app.services.reader_orchestration.artifact_input_application_service import (
    EXTRACTION_JOB_TYPE,
    EXTRACTION_OPERATION_FINGERPRINT,
    EXTRACTION_TARGET_TYPE,
)
from app.services.reader_orchestration.job_runtime import ReaderJobRuntime

pytestmark = pytest.mark.anyio

REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE_ARTIFACTS_SQL = (
    REPO_ROOT / "infra" / "migrations" / "0007_reader_source_artifacts.sql"
).read_text(encoding="utf-8")

from tests.test_reader_orchestration_schema_baseline import BASELINE_SQL, DATABASE_URL  # noqa: E402

EXTRACTION_SCHEMA_SQL = BASELINE_SQL + "\n" + SOURCE_ARTIFACTS_SQL

_USER_ID = UUID("00000000-0000-0000-0000-000000000a01")
_RECORD_ID = UUID("00000000-0000-0000-0000-000000000a02")
_ORIGINAL_INPUT_ID = UUID("00000000-0000-0000-0000-000000000a03")
_ARTIFACT_ID = UUID("00000000-0000-0000-0000-000000000a04")
_RUN_ID = UUID("00000000-0000-0000-0000-000000000a05")

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
async def extraction_env() -> asyncpg.Pool:
    schema_name = f"test_extraction_worker_{uuid4().hex}"
    admin_conn = await _connect_admin()
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(EXTRACTION_SCHEMA_SQL)
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


async def _seed_full_environment(
    pool: asyncpg.Pool,
    *,
    artifact_status: str = "available",
    artifact_bound: bool = True,
    input_json_override: dict | None = None,
) -> UUID:
    """Seed user, record, original_input, source_artifact, run, extraction job.

    Returns the job_id.
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
            VALUES ($1, $2, 'pdf', 'Extraction Test', 'en',
                    'active', 'processing', 'submitted', 1)
            """,
            _RECORD_ID,
            _USER_ID,
        )
        # source_text is NULL because extraction hasn't happened yet; the
        # check constraint ck_original_inputs_has_source is satisfied by a
        # non-empty source_ref_json that mirrors what D6-I3J produces when
        # binding a source_artifact to an original_input.
        source_ref_json = {
            "artifact_id": str(_ARTIFACT_ID),
            "storage_provider": "oss",
            "bucket": "claread-dev",
            "endpoint": "https://oss-cn-shenzhen.aliyuncs.com",
            "object_key": "dev/original-inputs/test/artifact.pdf",
            "artifact_kind": "original_upload",
            "content_type": "application/pdf",
            "byte_size": 1024,
            "source_filename": "artifact.pdf",
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
        artifact_record_id = _RECORD_ID if artifact_bound else None
        artifact_input_id = _ORIGINAL_INPUT_ID if artifact_bound else None
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
                    'application/pdf', 1024, $5, 'artifact.pdf', $6)
            """,
            _ARTIFACT_ID,
            artifact_record_id,
            artifact_input_id,
            _USER_ID,
            _DEFAULT_CONTENT_SHA256,
            artifact_status,
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
            _RUN_ID,
        )

        if input_json_override is not None:
            input_json = input_json_override
        else:
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
                "content_type": "application/pdf",
                "byte_size": 1024,
                "content_sha256": _DEFAULT_CONTENT_SHA256,
                "source_filename": "artifact.pdf",
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
            _RUN_ID,
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


class _FakeProvider:
    """Fake extraction provider for tests."""

    def __init__(
        self,
        *,
        result: ArtifactExtractionResult | None = None,
        error: ArtifactExtractionError | None = None,
    ) -> None:
        self._result = result
        self._error = error
        self.calls = 0

    async def extract(self, context) -> ArtifactExtractionResult:
        self.calls += 1
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


def _build_worker(
    pool: asyncpg.Pool,
    *,
    provider: _FakeProvider,
) -> ArtifactExtractionWorkerService:
    return ArtifactExtractionWorkerService(
        pool=pool,
        job_runtime=ReaderJobRuntime(pool=pool),
        provider=provider,
    )


async def _fetch_job(pool: asyncpg.Pool, job_id: UUID) -> asyncpg.Record:
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM reader_jobs WHERE id = $1", job_id)


async def _fetch_original_input(pool: asyncpg.Pool) -> asyncpg.Record:
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT * FROM original_inputs WHERE id = $1",
            _ORIGINAL_INPUT_ID,
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


async def _fetch_run(pool: asyncpg.Pool) -> asyncpg.Record:
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM reader_runs WHERE id = $1", _RUN_ID)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_happy_path_extracts_text_and_persists_result(
    extraction_env: asyncpg.Pool,
) -> None:
    job_id = await _seed_full_environment(extraction_env)
    provider = _FakeProvider(
        result=ArtifactExtractionResult(
            extracted_text=_EXTRACTED_TEXT,
            extractor_name="fake-ocr-provider",
            quality={"confidence": 0.95},
            warnings=["low_dpi_page_3"],
        ),
    )
    worker = _build_worker(extraction_env, provider=provider)

    result = await worker.process_next(
        lease_owner="extraction-worker",
        lease_duration=timedelta(seconds=30),
    )

    assert result is not None
    assert result.status == "succeeded"
    assert result.extracted_text == _EXTRACTED_TEXT
    expected_sha = hashlib.sha256(_EXTRACTED_TEXT.encode("utf-8")).hexdigest()
    assert result.content_sha256 == expected_sha
    assert provider.calls == 1

    job = await _fetch_job(extraction_env, job_id)
    assert job["status"] == "succeeded"
    output_ref = job["output_ref_json"]
    assert output_ref["original_input_id"] == str(_ORIGINAL_INPUT_ID)
    assert output_ref["content_sha256"] == expected_sha
    assert output_ref["text_length"] == len(_EXTRACTED_TEXT)

    input_row = await _fetch_original_input(extraction_env)
    # L2: worker 不再回写 original_inputs.source_text / content_sha256；
    # 正文唯一载体是 confirmed_source_documents（revision=1,
    # edit_source='extraction'，正文为规范化后的抽取文本）。
    assert input_row["source_text"] is None
    assert input_row["content_sha256"] == _DEFAULT_CONTENT_SHA256
    metadata = input_row["metadata_json"]
    assert metadata["extraction_status"] == "succeeded"
    assert metadata["extractor_name"] == "fake-ocr-provider"
    assert metadata["extraction_quality"] == {"confidence": 0.95}
    assert metadata["extraction_warnings"] == ["low_dpi_page_3"]
    # Existing metadata preserved
    assert metadata["source_artifact_status"] == "available"

    async with extraction_env.acquire() as conn:
        source_row = await conn.fetchrow(
            """
            SELECT markdown_text, revision, content_sha256, status, edit_source,
                   original_input_id
            FROM confirmed_source_documents
            WHERE reading_record_id = $1 AND record_generation = 1
            """,
            _RECORD_ID,
        )
    assert source_row is not None
    assert source_row["markdown_text"] == _EXTRACTED_TEXT
    assert source_row["revision"] == 1
    assert source_row["content_sha256"] == expected_sha
    assert source_row["status"] == "draft"
    assert source_row["edit_source"] == "extraction"
    assert source_row["original_input_id"] == _ORIGINAL_INPUT_ID

    run = await _fetch_run(extraction_env)
    assert run["status"] == "completed"


async def test_happy_path_does_not_create_bases_or_article_ready_events(
    extraction_env: asyncpg.Pool,
) -> None:
    await _seed_full_environment(extraction_env)
    provider = _FakeProvider(
        result=ArtifactExtractionResult(
            extracted_text=_EXTRACTED_TEXT,
            extractor_name="fake-ocr-provider",
        ),
    )
    worker = _build_worker(extraction_env, provider=provider)

    await worker.process_next(
        lease_owner="extraction-worker",
        lease_duration=timedelta(seconds=30),
    )

    assert await _count_reading_bases(extraction_env) == 0
    assert await _count_article_ready_events(extraction_env) == 0


async def test_empty_text_results_in_failed_terminal(
    extraction_env: asyncpg.Pool,
) -> None:
    job_id = await _seed_full_environment(extraction_env)
    provider = _FakeProvider(
        result=ArtifactExtractionResult(
            extracted_text="   ",
            extractor_name="fake-ocr-provider",
        ),
    )
    worker = _build_worker(extraction_env, provider=provider)

    result = await worker.process_next(
        lease_owner="extraction-worker",
        lease_duration=timedelta(seconds=30),
    )

    assert result is not None
    assert result.status == "failed_terminal"

    job = await _fetch_job(extraction_env, job_id)
    assert job["status"] == "failed_terminal"
    assert job["failure_code"] == FAILURE_CODE_EXTRACTION_EMPTY_TEXT

    # original_inputs should NOT be updated
    input_row = await _fetch_original_input(extraction_env)
    assert input_row["source_text"] is None


async def test_retryable_provider_error_results_in_retry_later(
    extraction_env: asyncpg.Pool,
) -> None:
    job_id = await _seed_full_environment(extraction_env)
    provider = _FakeProvider(
        error=ArtifactExtractionError(
            "transient OSS download failure",
            retryable=True,
            failure_class="provider",
            failure_code="oss_download_transient",
        ),
    )
    worker = _build_worker(extraction_env, provider=provider)

    result = await worker.process_next(
        lease_owner="extraction-worker",
        lease_duration=timedelta(seconds=30),
    )

    assert result is not None
    assert result.status == "retry_later"

    job = await _fetch_job(extraction_env, job_id)
    assert job["status"] == "retry_later"
    # job_runtime only stores rationale_code for retry_later transitions
    # (failure_code/failure_class are only persisted for failed_terminal).
    assert job["rationale_code"] == "oss_download_transient"

    run = await _fetch_run(extraction_env)
    assert run["status"] == "failed_retryable"


async def test_terminal_provider_error_results_in_failed_terminal(
    extraction_env: asyncpg.Pool,
) -> None:
    job_id = await _seed_full_environment(extraction_env)
    provider = _FakeProvider(
        error=ArtifactExtractionError(
            "unsupported file format",
            retryable=False,
            failure_class="provider",
            failure_code="unsupported_format",
        ),
    )
    worker = _build_worker(extraction_env, provider=provider)

    result = await worker.process_next(
        lease_owner="extraction-worker",
        lease_duration=timedelta(seconds=30),
    )

    assert result is not None
    assert result.status == "failed_terminal"

    job = await _fetch_job(extraction_env, job_id)
    assert job["status"] == "failed_terminal"
    assert job["failure_code"] == "unsupported_format"


async def test_wrong_input_json_source_fail_closed(
    extraction_env: asyncpg.Pool,
) -> None:
    job_id = await _seed_full_environment(
        extraction_env,
        input_json_override={
            "source": "wrong_source",
            "reading_record_id": str(_RECORD_ID),
            "original_input_id": str(_ORIGINAL_INPUT_ID),
            "source_artifact_id": str(_ARTIFACT_ID),
        },
    )
    provider = _FakeProvider(
        result=ArtifactExtractionResult(
            extracted_text=_EXTRACTED_TEXT,
            extractor_name="fake-ocr-provider",
        ),
    )
    worker = _build_worker(extraction_env, provider=provider)

    result = await worker.process_next(
        lease_owner="extraction-worker",
        lease_duration=timedelta(seconds=30),
    )

    assert result is not None
    assert result.status == "failed_terminal"

    job = await _fetch_job(extraction_env, job_id)
    assert job["status"] == "failed_terminal"
    assert job["failure_code"] == FAILURE_CODE_INPUT_JSON_INVALID
    assert provider.calls == 0


async def test_target_key_mismatch_with_source_artifact_id_fail_closed(
    extraction_env: asyncpg.Pool,
) -> None:
    """claim.target_key must match input_json.source_artifact_id.

    I3K sets target_key=str(artifact_id). A mismatch means the job was
    malformed: target_key points at artifact A but input_json asks to process
    artifact B. Fail closed to prevent cross-artifact contamination.
    """
    other_artifact_id = UUID("00000000-0000-0000-0000-000000000b99")
    job_id = await _seed_full_environment(
        extraction_env,
        input_json_override={
            "source": "artifact_input",
            "reading_record_id": str(_RECORD_ID),
            "original_input_id": str(_ORIGINAL_INPUT_ID),
            # source_artifact_id differs from target_key (str(_ARTIFACT_ID))
            "source_artifact_id": str(other_artifact_id),
            "artifact_kind": "original_upload",
            "storage_provider": "oss",
            "bucket": "claread-dev",
            "endpoint": "https://oss-cn-shenzhen.aliyuncs.com",
            "object_key": "dev/original-inputs/test/artifact.pdf",
            "content_type": "application/pdf",
            "byte_size": 1024,
            "content_sha256": _DEFAULT_CONTENT_SHA256,
            "source_filename": "artifact.pdf",
        },
    )
    provider = _FakeProvider(
        result=ArtifactExtractionResult(
            extracted_text=_EXTRACTED_TEXT,
            extractor_name="fake-ocr-provider",
        ),
    )
    worker = _build_worker(extraction_env, provider=provider)

    result = await worker.process_next(
        lease_owner="extraction-worker",
        lease_duration=timedelta(seconds=30),
    )

    assert result is not None
    assert result.status == "failed_terminal"

    job = await _fetch_job(extraction_env, job_id)
    assert job["status"] == "failed_terminal"
    assert job["failure_code"] == FAILURE_CODE_INPUT_JSON_INVALID
    assert provider.calls == 0


async def test_artifact_not_bound_fail_closed(
    extraction_env: asyncpg.Pool,
) -> None:
    job_id = await _seed_full_environment(
        extraction_env,
        artifact_bound=False,
    )
    provider = _FakeProvider(
        result=ArtifactExtractionResult(
            extracted_text=_EXTRACTED_TEXT,
            extractor_name="fake-ocr-provider",
        ),
    )
    worker = _build_worker(extraction_env, provider=provider)

    result = await worker.process_next(
        lease_owner="extraction-worker",
        lease_duration=timedelta(seconds=30),
    )

    assert result is not None
    assert result.status == "failed_terminal"

    job = await _fetch_job(extraction_env, job_id)
    assert job["status"] == "failed_terminal"
    assert job["failure_code"] == FAILURE_CODE_ARTIFACT_NOT_BOUND
    assert provider.calls == 0


async def test_no_job_returns_none(extraction_env: asyncpg.Pool) -> None:
    provider = _FakeProvider(
        result=ArtifactExtractionResult(
            extracted_text=_EXTRACTED_TEXT,
            extractor_name="fake-ocr-provider",
        ),
    )
    worker = _build_worker(extraction_env, provider=provider)

    result = await worker.process_next(
        lease_owner="extraction-worker",
        lease_duration=timedelta(seconds=30),
    )

    assert result is None
    assert provider.calls == 0


async def test_unconfigured_provider_fails_closed(
    extraction_env: asyncpg.Pool,
) -> None:
    job_id = await _seed_full_environment(extraction_env)
    # Default provider (no injection) should fail closed
    worker = ArtifactExtractionWorkerService(
        pool=extraction_env,
        job_runtime=ReaderJobRuntime(pool=extraction_env),
    )

    result = await worker.process_next(
        lease_owner="extraction-worker",
        lease_duration=timedelta(seconds=30),
    )

    assert result is not None
    assert result.status == "failed_terminal"

    job = await _fetch_job(extraction_env, job_id)
    assert job["status"] == "failed_terminal"
    assert job["failure_code"] == "extraction_provider_unconfigured"
