"""Tests for D6-I3M TextArtifactExtractionProvider and worker integration.

Provider unit tests construct :class:`ArtifactExtractionJobContext` directly
and inject a :class:`FakeStorageObjectReader` — no DB needed.

The end-to-end test seeds a full schema, injects the provider into the worker,
and verifies confirmed-source output plus ``original_inputs`` lineage metadata.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.database.connection import init_connection
from app.services.reader_orchestration.artifact_extraction_worker import (
    ArtifactExtractionError,
    ArtifactExtractionJobContext,
    ArtifactExtractionWorkerService,
)
from app.services.reader_orchestration.job_runtime import ReaderJobRuntime
from app.services.reader_orchestration.text_artifact_extraction_provider import (
    EXTRACTOR_NAME,
    FAILURE_CODE_BYTE_SIZE_MISMATCH,
    FAILURE_CODE_DECODE_ERROR,
    FAILURE_CODE_EMPTY_TEXT,
    FAILURE_CODE_SHA256_MISMATCH,
    FAILURE_CODE_STORAGE_READ_ERROR,
    FAILURE_CODE_UNSUPPORTED_CONTENT_TYPE,
    StorageObjectReadResult,
    TextArtifactExtractionProvider,
)

pytestmark = pytest.mark.anyio


from tests.test_reader_orchestration_schema_baseline import BASELINE_SQL, DATABASE_URL  # noqa: E402

# Fixed UUIDs for deterministic seeding
_USER_ID = UUID("00000000-0000-0000-0000-00000000c001")
_RECORD_ID = UUID("00000000-0000-0000-0000-00000000c002")
_ORIGINAL_INPUT_ID = UUID("00000000-0000-0000-0000-00000000c003")
_ARTIFACT_ID = UUID("00000000-0000-0000-0000-00000000c004")
_RUN_ID = UUID("00000000-0000-0000-0000-00000000c005")

_TEXT_CONTENT = "The quick brown fox jumps over the lazy dog."
_TEXT_BYTES = _TEXT_CONTENT.encode("utf-8")
_TEXT_SHA256 = hashlib.sha256(_TEXT_BYTES).hexdigest()
_ORIGINAL_INPUT_SHA256 = "a" * 64


# ---------------------------------------------------------------------------
# Fake storage reader
# ---------------------------------------------------------------------------


class FakeStorageObjectReader:
    """In-memory storage reader that returns pre-configured bytes."""

    def __init__(
        self,
        *,
        data: bytes,
        error: Exception | None = None,
    ) -> None:
        self._data = data
        self._error = error
        self.calls: list[dict[str, str]] = []

    async def read_object(
        self,
        *,
        bucket: str,
        endpoint: str,
        object_key: str,
    ) -> StorageObjectReadResult:
        self.calls.append(
            {"bucket": bucket, "endpoint": endpoint, "object_key": object_key}
        )
        if self._error is not None:
            raise self._error
        return StorageObjectReadResult(data=self._data, byte_size=len(self._data))


# ---------------------------------------------------------------------------
# Context builder for provider unit tests
# ---------------------------------------------------------------------------


def _make_context(
    *,
    content_type: str | None = "text/plain",
    source_filename: str = "notes.txt",
    byte_size: int | None = None,
    content_sha256: str | None = None,
    object_key: str = "dev/test/notes.txt",
) -> ArtifactExtractionJobContext:
    return ArtifactExtractionJobContext(
        job_id=uuid4(),
        run_id=uuid4(),
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        original_input_id=_ORIGINAL_INPUT_ID,
        source_artifact_id=_ARTIFACT_ID,
        artifact_kind="original_upload",
        storage_provider="oss",
        bucket="claread-dev",
        endpoint="https://oss-cn-shenzhen.aliyuncs.com",
        object_key=object_key,
        content_type=content_type,
        byte_size=byte_size,
        content_sha256=content_sha256,
        source_filename=source_filename,
        expected_generation=1,
        operation_fingerprint="input_artifact_extraction_v1",
    )


# ===================================================================
# Provider unit tests (no DB)
# ===================================================================


async def test_happy_path_text_plain() -> None:
    reader = FakeStorageObjectReader(data=_TEXT_BYTES)
    provider = TextArtifactExtractionProvider(reader=reader)

    result = await provider.extract(_make_context())

    assert result.extracted_text == _TEXT_CONTENT
    assert result.extractor_name == EXTRACTOR_NAME
    assert result.quality is not None
    assert result.quality["content_type"] == "text/plain"
    assert result.quality["source_filename"] == "notes.txt"
    assert result.quality["byte_size"] == len(_TEXT_BYTES)
    assert result.quality["content_sha256_verified"] is False
    assert result.quality["encoding"] == "utf-8"
    assert result.warnings is None
    assert len(reader.calls) == 1
    assert reader.calls[0]["object_key"] == "dev/test/notes.txt"


async def test_happy_path_text_markdown() -> None:
    md = b"# Title\n\nSome **bold** text.\n"
    reader = FakeStorageObjectReader(data=md)
    provider = TextArtifactExtractionProvider(reader=reader)

    result = await provider.extract(
        _make_context(content_type="text/markdown", source_filename="readme.md")
    )

    assert result.extracted_text == md.decode("utf-8")
    assert result.quality["content_type"] == "text/markdown"
    assert result.quality["encoding"] == "utf-8"


async def test_happy_path_text_x_markdown() -> None:
    md = b"## Section\n\nContent.\n"
    reader = FakeStorageObjectReader(data=md)
    provider = TextArtifactExtractionProvider(reader=reader)

    result = await provider.extract(
        _make_context(
            content_type="text/x-markdown", source_filename="page.x.md"
        )
    )

    assert result.extracted_text == md.decode("utf-8")
    assert result.quality["content_type"] == "text/x-markdown"


async def test_happy_path_octet_stream_with_txt_extension() -> None:
    reader = FakeStorageObjectReader(data=_TEXT_BYTES)
    provider = TextArtifactExtractionProvider(reader=reader)

    result = await provider.extract(
        _make_context(
            content_type="application/octet-stream",
            source_filename="unknown.txt",
        )
    )

    assert result.extracted_text == _TEXT_CONTENT
    assert result.warnings is not None
    assert any("application/octet-stream" in w for w in result.warnings)


async def test_happy_path_octet_stream_with_md_extension() -> None:
    md = b"# Hello\n"
    reader = FakeStorageObjectReader(data=md)
    provider = TextArtifactExtractionProvider(reader=reader)

    result = await provider.extract(
        _make_context(
            content_type="application/octet-stream",
            source_filename="doc.md",
        )
    )

    assert result.extracted_text == "# Hello\n"
    assert result.warnings is not None


async def test_happy_path_with_bom() -> None:
    bom_text = "BOM prefixed text."
    bom_bytes = b"\xef\xbb\xbf" + bom_text.encode("utf-8")
    reader = FakeStorageObjectReader(data=bom_bytes)
    provider = TextArtifactExtractionProvider(reader=reader)

    result = await provider.extract(_make_context(byte_size=len(bom_bytes)))

    assert result.extracted_text == bom_text
    assert result.quality["encoding"] == "utf-8-bom"
    assert result.quality["byte_size"] == len(bom_bytes)


async def test_happy_path_with_sha256_verification() -> None:
    reader = FakeStorageObjectReader(data=_TEXT_BYTES)
    provider = TextArtifactExtractionProvider(reader=reader)

    result = await provider.extract(
        _make_context(content_sha256=_TEXT_SHA256, byte_size=len(_TEXT_BYTES))
    )

    assert result.quality["content_sha256_verified"] is True


async def test_content_type_with_charset_suffix() -> None:
    reader = FakeStorageObjectReader(data=_TEXT_BYTES)
    provider = TextArtifactExtractionProvider(reader=reader)

    result = await provider.extract(
        _make_context(content_type="text/plain; charset=utf-8")
    )

    assert result.extracted_text == _TEXT_CONTENT


async def test_unsupported_content_type_fail_closed() -> None:
    reader = FakeStorageObjectReader(data=_TEXT_BYTES)
    provider = TextArtifactExtractionProvider(reader=reader)

    with pytest.raises(ArtifactExtractionError) as exc_info:
        await provider.extract(
            _make_context(
                content_type="application/pdf",
                source_filename="doc.pdf",
            )
        )

    assert exc_info.value.failure_code == FAILURE_CODE_UNSUPPORTED_CONTENT_TYPE
    assert exc_info.value.retryable is False
    assert len(reader.calls) == 0  # reader not called


async def test_octet_stream_without_txt_md_extension_fail_closed() -> None:
    reader = FakeStorageObjectReader(data=_TEXT_BYTES)
    provider = TextArtifactExtractionProvider(reader=reader)

    with pytest.raises(ArtifactExtractionError) as exc_info:
        await provider.extract(
            _make_context(
                content_type="application/octet-stream",
                source_filename="data.bin",
            )
        )

    assert exc_info.value.failure_code == FAILURE_CODE_UNSUPPORTED_CONTENT_TYPE
    assert len(reader.calls) == 0


async def test_none_content_type_fail_closed() -> None:
    reader = FakeStorageObjectReader(data=_TEXT_BYTES)
    provider = TextArtifactExtractionProvider(reader=reader)

    with pytest.raises(ArtifactExtractionError) as exc_info:
        await provider.extract(_make_context(content_type=None))

    assert exc_info.value.failure_code == FAILURE_CODE_UNSUPPORTED_CONTENT_TYPE


async def test_sha256_mismatch_fail_closed() -> None:
    reader = FakeStorageObjectReader(data=_TEXT_BYTES)
    provider = TextArtifactExtractionProvider(reader=reader)

    with pytest.raises(ArtifactExtractionError) as exc_info:
        await provider.extract(
            _make_context(content_sha256="0" * 64)
        )

    assert exc_info.value.failure_code == FAILURE_CODE_SHA256_MISMATCH
    assert exc_info.value.retryable is False


async def test_byte_size_mismatch_fail_closed() -> None:
    reader = FakeStorageObjectReader(data=_TEXT_BYTES)
    provider = TextArtifactExtractionProvider(reader=reader)

    with pytest.raises(ArtifactExtractionError) as exc_info:
        await provider.extract(
            _make_context(byte_size=len(_TEXT_BYTES) + 100)
        )

    assert exc_info.value.failure_code == FAILURE_CODE_BYTE_SIZE_MISMATCH


async def test_decode_error_binary_fail_closed() -> None:
    # Invalid UTF-8 sequence
    binary = b"\xff\xfe\x00\x01\x02\xff"
    reader = FakeStorageObjectReader(data=binary)
    provider = TextArtifactExtractionProvider(reader=reader)

    with pytest.raises(ArtifactExtractionError) as exc_info:
        await provider.extract(_make_context(byte_size=len(binary)))

    assert exc_info.value.failure_code == FAILURE_CODE_DECODE_ERROR


async def test_empty_text_fail_closed() -> None:
    reader = FakeStorageObjectReader(data=b"")
    provider = TextArtifactExtractionProvider(reader=reader)

    with pytest.raises(ArtifactExtractionError) as exc_info:
        await provider.extract(_make_context(byte_size=0))

    assert exc_info.value.failure_code == FAILURE_CODE_EMPTY_TEXT


async def test_whitespace_only_text_fail_closed() -> None:
    reader = FakeStorageObjectReader(data=b"  \n\t  \r\n  ")
    provider = TextArtifactExtractionProvider(reader=reader)

    with pytest.raises(ArtifactExtractionError) as exc_info:
        await provider.extract(_make_context())

    assert exc_info.value.failure_code == FAILURE_CODE_EMPTY_TEXT


async def test_storage_read_transient_error_is_retryable() -> None:
    """Transient storage errors (timeout, connection reset) default to retryable.

    The worker will schedule retry_later instead of failing the job terminal.
    """
    reader = FakeStorageObjectReader(
        data=b"", error=ConnectionError("OSS timeout")
    )
    provider = TextArtifactExtractionProvider(reader=reader)

    with pytest.raises(ArtifactExtractionError) as exc_info:
        await provider.extract(_make_context())

    assert exc_info.value.failure_code == FAILURE_CODE_STORAGE_READ_ERROR
    assert exc_info.value.retryable is True
    assert "OSS timeout" in str(exc_info.value)


async def test_storage_read_terminal_error_from_reader_passes_through() -> None:
    """A reader can signal a terminal error by raising ArtifactExtractionError(retryable=False).

    E.g. 404 Not Found / 403 Forbidden are permanent and should not be retried.
    """
    terminal_error = ArtifactExtractionError(
        "object not found",
        retryable=False,
        failure_class="extraction",
        failure_code="object_not_found",
    )
    reader = FakeStorageObjectReader(data=b"", error=terminal_error)
    provider = TextArtifactExtractionProvider(reader=reader)

    with pytest.raises(ArtifactExtractionError) as exc_info:
        await provider.extract(_make_context())

    assert exc_info.value.failure_code == "object_not_found"
    assert exc_info.value.retryable is False


async def test_bom_with_invalid_utf8_after_bom_fail_closed() -> None:
    bom_bad = b"\xef\xbb\xbf\xff\xff\xff"
    reader = FakeStorageObjectReader(data=bom_bad)
    provider = TextArtifactExtractionProvider(reader=reader)

    with pytest.raises(ArtifactExtractionError) as exc_info:
        await provider.extract(_make_context(byte_size=len(bom_bad)))

    assert exc_info.value.failure_code == FAILURE_CODE_DECODE_ERROR


async def test_quality_metadata_completeness() -> None:
    reader = FakeStorageObjectReader(data=_TEXT_BYTES)
    provider = TextArtifactExtractionProvider(reader=reader)

    result = await provider.extract(
        _make_context(
            content_sha256=_TEXT_SHA256,
            byte_size=len(_TEXT_BYTES),
        )
    )

    q = result.quality
    assert q is not None
    assert q["content_type"] == "text/plain"
    assert q["source_filename"] == "notes.txt"
    assert q["byte_size"] == len(_TEXT_BYTES)
    assert q["content_sha256_verified"] is True
    assert q["encoding"] == "utf-8"


# ===================================================================
# Worker + provider end-to-end test (DB-backed)
# ===================================================================


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
async def e2e_env() -> asyncpg.Pool:
    schema_name = f"test_i3m_e2e_{uuid4().hex}"
    admin_conn = await _connect_admin()
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)
        pool = await _make_pool(schema_name)
        try:
            yield pool
        finally:
            await pool.close()
    finally:
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


async def _seed_text_artifact_environment(
    pool: asyncpg.Pool,
    *,
    content_type: str = "text/plain",
    source_filename: str = "notes.txt",
    byte_size: int | None = len(_TEXT_BYTES),
    content_sha256: str | None = _TEXT_SHA256,
) -> UUID:
    """Seed user, record, original_input, source_artifact, run, extraction job.

    Returns the job_id. The source_artifact's content_type / byte_size /
    content_sha256 are set from the parameters so the provider can validate
    against them.
    """
    input_json = {
        "source": "artifact_input",
        "reading_record_id": str(_RECORD_ID),
        "original_input_id": str(_ORIGINAL_INPUT_ID),
        "source_artifact_id": str(_ARTIFACT_ID),
        "artifact_kind": "original_upload",
        "storage_provider": "oss",
        "bucket": "claread-dev",
        "endpoint": "https://oss-cn-shenzhen.aliyuncs.com",
        "object_key": "dev/test/notes.txt",
        "content_type": content_type,
        "byte_size": byte_size,
        "content_sha256": content_sha256,
        "source_filename": source_filename,
    }

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
            VALUES ($1, $2, 'text', 'I3M E2E Test', 'en',
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
            "object_key": "dev/test/notes.txt",
            "artifact_kind": "original_upload",
            "content_type": content_type,
            "byte_size": byte_size,
            "source_filename": source_filename,
        }
        await conn.execute(
            """
            INSERT INTO original_inputs (
                id, reading_record_id, user_id, input_type,
                source_text, source_ref_json, metadata_json, content_sha256
            )
            VALUES ($1, $2, $3, 'file_ref',
                    NULL, $4,
                    '{}'::jsonb,
                    $5)
            """,
            _ORIGINAL_INPUT_ID,
            _RECORD_ID,
            _USER_ID,
            source_ref_json,
            _ORIGINAL_INPUT_SHA256,
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
            byte_size,
            content_sha256,
            source_filename,
        )
        await conn.execute(
            """
            INSERT INTO reader_runs (
                id, reading_record_id, user_id, run_type, status,
                record_generation, envelope_json, policy_version, trigger_kind
            )
            VALUES ($1, $2, $3, 'input_artifact_extraction', 'queued', 1,
                    '{}'::jsonb, 'reader_input_artifact_extraction_v1', 'system')
            """,
            _RUN_ID,
            _RECORD_ID,
            _USER_ID,
        )
        job_id = await conn.fetchval(
            """
            INSERT INTO reader_jobs (
                reading_record_id, base_id, run_id, user_id,
                job_type, target_type, target_key, status, priority,
                expected_generation, operation_fingerprint,
                idempotency_key, input_json, max_attempts
            )
            VALUES ($1, NULL, $2, $3,
                    'input_artifact_extraction', 'record', $4, 'queued', 0,
                    1, 'input_artifact_extraction_v1',
                    $5, $6::jsonb, 3)
            RETURNING id
            """,
            _RECORD_ID,
            _RUN_ID,
            _USER_ID,
            str(_ARTIFACT_ID),  # target_key = str(artifact_id) per I3K contract
            f"i3m-e2e-{uuid4().hex}",
            input_json,
        )
    return job_id


async def _fetch_original_input(
    pool: asyncpg.Pool, input_id: UUID
) -> asyncpg.Record:
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT source_text, content_sha256, metadata_json FROM original_inputs WHERE id = $1",
            input_id,
        )


async def _fetch_job(pool: asyncpg.Pool, job_id: UUID) -> asyncpg.Record:
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT status, failure_code, output_ref_json FROM reader_jobs WHERE id = $1",
            job_id,
        )


async def _count_reading_bases(pool: asyncpg.Pool) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT COUNT(*) FROM reading_bases")


async def _count_article_ready_events(pool: asyncpg.Pool) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM reader_events WHERE event_type = 'article_ready'"
        )


async def test_worker_with_text_provider_end_to_end(e2e_env: asyncpg.Pool) -> None:
    """Full pipeline: fake storage -> TextArtifactExtractionProvider -> worker -> DB.

    Verifies that source text stays out of ``original_inputs``, extraction
    metadata is updated, the job transitions to succeeded, and no
    reading_bases or article_ready events are created (I3L invariant).
    """
    job_id = await _seed_text_artifact_environment(e2e_env)

    fake_reader = FakeStorageObjectReader(data=_TEXT_BYTES)
    provider = TextArtifactExtractionProvider(reader=fake_reader)
    worker = ArtifactExtractionWorkerService(
        pool=e2e_env,
        job_runtime=ReaderJobRuntime(pool=e2e_env),
        provider=provider,
    )

    result = await worker.process_next(
        lease_owner="i3m-test-worker",
        lease_duration=timedelta(seconds=30),
    )

    assert result is not None
    assert result.status == "succeeded"
    assert result.extracted_text == _TEXT_CONTENT
    assert result.content_sha256 == _TEXT_SHA256

    # original_inputs remains lineage-only; source truth is confirmed-source.
    input_row = await _fetch_original_input(e2e_env, _ORIGINAL_INPUT_ID)
    assert input_row["source_text"] is None
    assert input_row["content_sha256"] == _ORIGINAL_INPUT_SHA256
    metadata = input_row["metadata_json"]
    assert metadata["extraction_status"] == "succeeded"
    assert metadata["extractor_name"] == EXTRACTOR_NAME
    assert metadata["extraction_quality"]["content_type"] == "text/plain"
    assert metadata["extraction_quality"]["content_sha256_verified"] is True
    assert metadata["extraction_quality"]["encoding"] == "utf-8"

    # Verify job transition
    job = await _fetch_job(e2e_env, job_id)
    assert job["status"] == "succeeded"
    output_ref = job["output_ref_json"]
    assert output_ref["original_input_id"] == str(_ORIGINAL_INPUT_ID)
    assert output_ref["content_sha256"] == _TEXT_SHA256
    assert output_ref["text_length"] == len(_TEXT_CONTENT)

    # I3L invariant: no bases, no article_ready events
    assert await _count_reading_bases(e2e_env) == 0
    assert await _count_article_ready_events(e2e_env) == 0

    # Storage reader was called with the correct object_key
    assert len(fake_reader.calls) == 1
    assert fake_reader.calls[0]["object_key"] == "dev/test/notes.txt"


async def test_worker_with_text_provider_sha_mismatch_fail_terminal(
    e2e_env: asyncpg.Pool,
) -> None:
    """If storage bytes don't match the artifact's content_sha256, job fails terminal."""
    job_id = await _seed_text_artifact_environment(
        e2e_env,
        content_sha256="0" * 64,  # wrong sha
    )

    fake_reader = FakeStorageObjectReader(data=_TEXT_BYTES)
    provider = TextArtifactExtractionProvider(reader=fake_reader)
    worker = ArtifactExtractionWorkerService(
        pool=e2e_env,
        job_runtime=ReaderJobRuntime(pool=e2e_env),
        provider=provider,
    )

    result = await worker.process_next(
        lease_owner="i3m-test-worker",
        lease_duration=timedelta(seconds=30),
    )

    assert result is not None
    assert result.status == "failed_terminal"

    job = await _fetch_job(e2e_env, job_id)
    assert job["status"] == "failed_terminal"
    assert job["failure_code"] == FAILURE_CODE_SHA256_MISMATCH

    # original_inputs.source_text must NOT be updated
    input_row = await _fetch_original_input(e2e_env, _ORIGINAL_INPUT_ID)
    assert input_row["source_text"] is None


async def test_worker_with_text_provider_markdown_end_to_end(
    e2e_env: asyncpg.Pool,
) -> None:
    """Markdown artifact with .md extension end-to-end."""
    md_content = "# Hello World\n\nThis is a **markdown** document.\n"
    md_bytes = md_content.encode("utf-8")
    md_sha = hashlib.sha256(md_bytes).hexdigest()

    job_id = await _seed_text_artifact_environment(
        e2e_env,
        content_type="text/markdown",
        source_filename="readme.md",
        byte_size=len(md_bytes),
        content_sha256=md_sha,
    )

    fake_reader = FakeStorageObjectReader(data=md_bytes)
    provider = TextArtifactExtractionProvider(reader=fake_reader)
    worker = ArtifactExtractionWorkerService(
        pool=e2e_env,
        job_runtime=ReaderJobRuntime(pool=e2e_env),
        provider=provider,
    )

    result = await worker.process_next(
        lease_owner="i3m-test-worker",
        lease_duration=timedelta(seconds=30),
    )

    assert result is not None
    assert result.status == "succeeded"
    assert result.extracted_text == md_content

    input_row = await _fetch_original_input(e2e_env, _ORIGINAL_INPUT_ID)
    assert input_row["source_text"] is None
    assert input_row["content_sha256"] == _ORIGINAL_INPUT_SHA256
    assert input_row["metadata_json"]["extraction_quality"]["content_type"] == "text/markdown"
