# task-history: D6-I3P (renamed from test_d6_i3p_artifact_pipeline_worker_service.py)
"""Tests for D6-I3P Artifact Pipeline Worker Service.

Covers the full artifact-backed text/markdown pipeline driven through
``ArtifactInputPipelineWorkerService``:

- ``process_once`` with an extraction job → extraction runs, materialization
  job enqueued in the same transaction.
- ``process_once`` again → materialization job processed.
- Stable text artifact end-to-end: ``original_inputs.source_text`` →
  ``stable_reading_documents`` + ``reading_bases`` + ``article_ready`` event.
- Markdown requiring candidate → ``candidate_reading_documents`` +
  ``candidate_base_ready``.
- Rejected text → ``action_required``, no stable/candidate.
- Provider unconfigured → extraction ``failed_terminal``, no materialization
  enqueue.
- Retryable provider error → exception propagates, job stays ``claimed``.
- ``active_base`` already exists before materialization claim →
  ``superseded``.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.database.connection import init_connection
from app.services.reader_orchestration.artifact_input_application_service import (
    EXTRACTION_JOB_TYPE,
    EXTRACTION_OPERATION_FINGERPRINT,
    EXTRACTION_TARGET_TYPE,
)
from app.services.reader_orchestration.artifact_pipeline_worker_service import (
    ArtifactInputPipelineWorkerService,
)
from app.services.reader_orchestration.text_artifact_extraction_provider import (
    StorageObjectReadResult,
)

pytestmark = [pytest.mark.anyio, pytest.mark.chain_reader_parse, pytest.mark.seam_service_integration, pytest.mark.life_permanent_regression]


from tests.test_reader_orchestration_schema_baseline import BASELINE_SQL, DATABASE_URL  # noqa: E402

# The single baseline includes document blocks and source artifacts.

# Fixed UUIDs (different range from I3O to avoid cross-test conflicts)
_USER_ID = UUID("00000000-0000-0000-0000-000000000f01")
_RECORD_ID = UUID("00000000-0000-0000-0000-000000000f02")
_ORIGINAL_INPUT_ID = UUID("00000000-0000-0000-0000-000000000f03")
_ARTIFACT_ID = UUID("00000000-0000-0000-0000-000000000f04")
_EXTRACTION_RUN_ID = UUID("00000000-0000-0000-0000-000000000f05")

# Stable-ready text: ~60 English words
_STABLE_TEXT = (
    "The quick brown fox jumps over the lazy dog near the riverbank. "
    "A small bird sings in the tree above them. The morning sun casts "
    "long shadows across the meadow. Children laugh and play in the "
    "distance while a gentle breeze rustles the leaves. This peaceful "
    "scene captures a moment of quiet harmony in nature."
)

# Candidate-requiring text: >8000 words with markdown tables
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

_OBJECT_KEY = "dev/test/notes.txt"
_BUCKET = "claread-dev"
_ENDPOINT = "https://oss-cn-shenzhen.aliyuncs.com"


# ---------------------------------------------------------------------------
# Fake storage reader
# ---------------------------------------------------------------------------


class FakeStorageObjectReader:
    """Returns pre-configured bytes for any read_object call."""

    def __init__(self, *, data: bytes) -> None:
        self._data = data

    async def read_object(
        self,
        *,
        bucket: str,
        endpoint: str,
        object_key: str,
    ) -> StorageObjectReadResult:
        return StorageObjectReadResult(
            data=self._data,
            byte_size=len(self._data),
            etag=None,
            content_type=None,
        )


class RetryableErrorStorageObjectReader:
    """Always raises a generic exception (classified as retryable)."""

    async def read_object(
        self,
        *,
        bucket: str,
        endpoint: str,
        object_key: str,
    ) -> StorageObjectReadResult:
        raise ConnectionError("simulated transient storage failure")


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
async def i3p_env() -> asyncpg.Pool:
    schema_name = f"test_i3p_{uuid4().hex}"
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


# ---------------------------------------------------------------------------
# Seed helper
# ---------------------------------------------------------------------------


async def _seed_extraction_job(
    pool: asyncpg.Pool,
    *,
    source_text: str,
    content_type: str = "text/plain",
    source_filename: str = "notes.txt",
    object_key: str = _OBJECT_KEY,
) -> UUID:
    """Seed user, record, original_input (no source_text), source_artifact,
    extraction run + extraction job. Returns the extraction job_id.

    ``content_sha256`` and ``byte_size`` on both the artifact and the job
    ``input_json`` are computed from ``source_text`` so that
    ``TextArtifactExtractionProvider`` validation passes.
    """
    source_bytes = source_text.encode("utf-8")
    source_sha = hashlib.sha256(source_bytes).hexdigest()
    byte_size = len(source_bytes)

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
            VALUES ($1, $2, 'text', 'I3P Test', 'en',
                    'active', 'processing', 'submitted', 1)
            """,
            _RECORD_ID,
            _USER_ID,
        )
        source_ref_json = {
            "artifact_id": str(_ARTIFACT_ID),
            "storage_provider": "oss",
            "bucket": _BUCKET,
            "endpoint": _ENDPOINT,
            "object_key": object_key,
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
                    '{"source_artifact_status": "available"}'::jsonb,
                    $5)
            """,
            _ORIGINAL_INPUT_ID,
            _RECORD_ID,
            _USER_ID,
            source_ref_json,
            source_sha,
        )
        await conn.execute(
            """
            INSERT INTO source_artifacts (
                id, reading_record_id, original_input_id, user_id,
                artifact_kind, storage_provider, bucket, object_key, endpoint,
                content_type, byte_size, content_sha256, source_filename, status
            )
            VALUES ($1, $2, $3, $4,
                    'original_upload', 'oss', $5, $6, $7,
                    $8, $9, $10, $11, 'available')
            """,
            _ARTIFACT_ID,
            _RECORD_ID,
            _ORIGINAL_INPUT_ID,
            _USER_ID,
            _BUCKET,
            object_key,
            _ENDPOINT,
            content_type,
            byte_size,
            source_sha,
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
            "bucket": _BUCKET,
            "endpoint": _ENDPOINT,
            "object_key": object_key,
            "content_type": content_type,
            "byte_size": byte_size,
            "content_sha256": source_sha,
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
                    $8, $9, 3)
            RETURNING id
            """,
            _RECORD_ID,
            _EXTRACTION_RUN_ID,
            _USER_ID,
            EXTRACTION_JOB_TYPE,
            EXTRACTION_TARGET_TYPE,
            str(_ARTIFACT_ID),
            EXTRACTION_OPERATION_FINGERPRINT,
            f"i3p-extraction-{uuid4().hex}",
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
    """Insert a reading_bases row for the seeded record."""
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
# Query helpers
# ---------------------------------------------------------------------------


async def _fetch_record(pool: asyncpg.Pool) -> asyncpg.Record:
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT * FROM reading_records WHERE id = $1", _RECORD_ID
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


async def _count_reading_units(pool: asyncpg.Pool) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM reading_units WHERE reading_record_id = $1",
            _RECORD_ID,
        )


async def _count_anchor_segments(pool: asyncpg.Pool) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM anchor_segments WHERE reading_record_id = $1",
            _RECORD_ID,
        )


async def _count_article_ready_events(pool: asyncpg.Pool) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM reader_events WHERE reading_record_id = $1 AND event_type = 'article_ready'",
            _RECORD_ID,
        )


async def _count_materialization_jobs(pool: asyncpg.Pool) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM reader_jobs WHERE job_type = 'extracted_artifact_materialization'"
        )


async def _fetch_materialization_job(pool: asyncpg.Pool) -> asyncpg.Record | None:
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT * FROM reader_jobs WHERE job_type = 'extracted_artifact_materialization'"
        )


async def _fetch_extraction_job_status(pool: asyncpg.Pool, job_id: UUID) -> str:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT status FROM reader_jobs WHERE id = $1", job_id
        )


_LEASE_OWNER = "i3p-test"
_LEASE_DURATION = timedelta(seconds=30)


# ===================================================================
# process_once: extraction priority + materialization enqueue
# ===================================================================


async def test_process_once_extraction_priority_then_materialization(
    i3p_env: asyncpg.Pool,
) -> None:
    """process_once with an extraction job runs extraction first, then a
    second process_once processes the enqueued materialization job."""
    job_id = await _seed_extraction_job(
        i3p_env, source_text=_STABLE_TEXT, content_type="text/plain",
    )

    reader = FakeStorageObjectReader(data=_STABLE_TEXT.encode("utf-8"))
    service = ArtifactInputPipelineWorkerService(
        pool=i3p_env, storage_reader=reader,
    )

    # 1st call: extraction
    result1 = await service.process_once(
        lease_owner=_LEASE_OWNER, lease_duration=_LEASE_DURATION,
    )
    assert result1 is not None
    assert result1.stage == "extraction"
    assert result1.status == "succeeded"

    # Materialization job was enqueued
    assert await _count_materialization_jobs(i3p_env) == 1
    assert await _fetch_extraction_job_status(i3p_env, job_id) == "succeeded"

    # 2nd call: materialization
    result2 = await service.process_once(
        lease_owner=_LEASE_OWNER, lease_duration=_LEASE_DURATION,
    )
    assert result2 is not None
    assert result2.stage == "materialization"
    assert result2.status == "succeeded"
    assert result2.outcome == "stable_document_ready"

    # 3rd call: no more jobs
    result3 = await service.process_once(
        lease_owner=_LEASE_OWNER, lease_duration=_LEASE_DURATION,
    )
    assert result3 is None


# ===================================================================
# E2E: stable text artifact
# ===================================================================


async def test_stable_text_artifact_end_to_end(i3p_env: asyncpg.Pool) -> None:
    """Stable text artifact: extraction → materialization → article_ready +
    stable_document + reading_base + units + segments + event."""
    await _seed_extraction_job(
        i3p_env, source_text=_STABLE_TEXT, content_type="text/plain",
    )

    reader = FakeStorageObjectReader(data=_STABLE_TEXT.encode("utf-8"))
    service = ArtifactInputPipelineWorkerService(
        pool=i3p_env, storage_reader=reader,
    )

    results = await service.drain(
        lease_owner=_LEASE_OWNER, lease_duration=_LEASE_DURATION,
    )
    assert len(results) == 2
    assert results[0].stage == "extraction"
    assert results[0].status == "succeeded"
    assert results[1].stage == "materialization"
    assert results[1].status == "succeeded"
    assert results[1].outcome == "stable_document_ready"

    record = await _fetch_record(i3p_env)
    assert record["readiness_state"] == "article_ready"
    assert record["product_state"] == "readable_enhancing"
    assert record["active_base_id"] is not None

    assert await _count_stable_documents(i3p_env) == 1
    assert await _count_reading_bases(i3p_env) == 1
    assert await _count_reading_units(i3p_env) > 0
    assert await _count_anchor_segments(i3p_env) > 0
    assert await _count_article_ready_events(i3p_env) == 1


# ===================================================================
# E2E: markdown requiring candidate
# ===================================================================


async def test_markdown_candidate_path_end_to_end(i3p_env: asyncpg.Pool) -> None:
    """Markdown artifact exceeding word limit → candidate_reading_documents +
    candidate_base_ready, no stable doc / base."""
    await _seed_extraction_job(
        i3p_env,
        source_text=_CANDIDATE_MD,
        content_type="text/markdown",
        source_filename="large.md",
        object_key="dev/test/large.md",
    )

    reader = FakeStorageObjectReader(data=_CANDIDATE_MD.encode("utf-8"))
    service = ArtifactInputPipelineWorkerService(
        pool=i3p_env, storage_reader=reader,
    )

    results = await service.drain(
        lease_owner=_LEASE_OWNER, lease_duration=_LEASE_DURATION,
    )
    assert len(results) == 2
    assert results[1].stage == "materialization"
    assert results[1].status == "succeeded"
    assert results[1].outcome == "candidate_document_required"

    record = await _fetch_record(i3p_env)
    assert record["readiness_state"] == "candidate_base_ready"
    assert record["product_state"] == "needs_confirmation"
    assert record["active_base_id"] is None

    assert await _count_candidates(i3p_env) == 1
    assert await _count_stable_documents(i3p_env) == 0
    assert await _count_reading_bases(i3p_env) == 0


# ===================================================================
# E2E: rejected text
# ===================================================================


async def test_rejected_text_end_to_end(i3p_env: asyncpg.Pool) -> None:
    """Rejected text (too short) → action_required, no stable/candidate/base."""
    await _seed_extraction_job(
        i3p_env, source_text=_REJECTED_TEXT, content_type="text/plain",
    )

    reader = FakeStorageObjectReader(data=_REJECTED_TEXT.encode("utf-8"))
    service = ArtifactInputPipelineWorkerService(
        pool=i3p_env, storage_reader=reader,
    )

    results = await service.drain(
        lease_owner=_LEASE_OWNER, lease_duration=_LEASE_DURATION,
    )
    assert len(results) == 2
    assert results[1].stage == "materialization"
    assert results[1].status == "succeeded"
    assert results[1].outcome == "input_rejected_or_action_required"

    record = await _fetch_record(i3p_env)
    assert record["product_state"] == "action_required"
    assert record["readiness_state"] == "submitted"
    assert record["active_base_id"] is None

    assert await _count_stable_documents(i3p_env) == 0
    assert await _count_candidates(i3p_env) == 0
    assert await _count_reading_bases(i3p_env) == 0
    assert await _count_article_ready_events(i3p_env) == 0


# ===================================================================
# Provider unconfigured → extraction failed_terminal
# ===================================================================


async def test_unconfigured_provider_extraction_failed_terminal(
    i3p_env: asyncpg.Pool,
) -> None:
    """When no storage_reader is injected, the default
    UnconfiguredArtifactExtractionProvider fails terminal, and no
    materialization job is enqueued."""
    job_id = await _seed_extraction_job(
        i3p_env, source_text=_STABLE_TEXT, content_type="text/plain",
    )

    # No storage_reader → UnconfiguredArtifactExtractionProvider
    service = ArtifactInputPipelineWorkerService(pool=i3p_env)

    result = await service.process_once(
        lease_owner=_LEASE_OWNER, lease_duration=_LEASE_DURATION,
    )
    assert result is not None
    assert result.stage == "extraction"
    assert result.status == "failed_terminal"

    # No materialization job enqueued
    assert await _count_materialization_jobs(i3p_env) == 0
    assert await _fetch_extraction_job_status(i3p_env, job_id) == "failed_terminal"


# ===================================================================
# Retryable provider error → exception propagates, job stays claimed
# ===================================================================


async def test_retryable_provider_error_schedules_retry(
    i3p_env: asyncpg.Pool,
) -> None:
    """A retryable storage read error is caught by TextArtifactExtractionProvider
    (wrapped as ``ArtifactExtractionError(retryable=True)``) and then by the
    extraction worker, which transitions the job to ``retry_later`` — NOT
    ``failed_terminal``. No materialization job is enqueued.

    This matches the D6-I3L extraction worker design: retryable provider errors
    schedule a retry with a delay. The materialization worker (D6-I3O) has a
    different pattern for retryable DB exceptions (propagate, stay ``claimed``
    for stale-lease recovery) — that is covered in the I3O test suite.
    """
    await _seed_extraction_job(
        i3p_env, source_text=_STABLE_TEXT, content_type="text/plain",
    )

    reader = RetryableErrorStorageObjectReader()
    service = ArtifactInputPipelineWorkerService(
        pool=i3p_env, storage_reader=reader,
    )

    result = await service.process_once(
        lease_owner=_LEASE_OWNER, lease_duration=_LEASE_DURATION,
    )
    assert result is not None
    assert result.stage == "extraction"
    assert result.status == "retry_later"

    # Job is in retry_later (not failed_terminal, not claimed)
    async with i3p_env.acquire() as conn:
        job_status = await conn.fetchval(
            "SELECT status FROM reader_jobs WHERE job_type = 'input_artifact_extraction'"
        )
    assert job_status == "retry_later"

    # No materialization job enqueued
    assert await _count_materialization_jobs(i3p_env) == 0


# ===================================================================
# active_base exists before materialization claim → superseded
# ===================================================================


async def test_active_base_exists_before_materialization_superseded(
    i3p_env: asyncpg.Pool,
) -> None:
    """If active_base_id is set before the materialization worker claims the
    job, ``claim_next_job`` auto-supersedes the job (fence violation) and
    returns ``None``. ``process_once`` returns ``None``. The job's DB status
    is ``superseded`` with ``rationale_code = active_base_already_exists``."""
    await _seed_extraction_job(
        i3p_env, source_text=_STABLE_TEXT, content_type="text/plain",
    )

    reader = FakeStorageObjectReader(data=_STABLE_TEXT.encode("utf-8"))
    service = ArtifactInputPipelineWorkerService(
        pool=i3p_env, storage_reader=reader,
    )

    # 1st call: extraction succeeds, enqueues materialization
    result1 = await service.process_once(
        lease_owner=_LEASE_OWNER, lease_duration=_LEASE_DURATION,
    )
    assert result1 is not None
    assert result1.status == "succeeded"

    # Simulate another flow setting active_base before materialization claims
    base_id = await _insert_reading_base(i3p_env, base_text="Pre-existing base.")
    async with i3p_env.acquire() as conn:
        await conn.execute(
            "UPDATE reading_records SET active_base_id = $2 WHERE id = $1",
            _RECORD_ID,
            base_id,
        )

    # 2nd call: materialization claim → fence auto-supersedes → returns None
    result2 = await service.process_once(
        lease_owner=_LEASE_OWNER, lease_duration=_LEASE_DURATION,
    )
    assert result2 is None

    # The materialization job was auto-superseded in the DB
    mat_job = await _fetch_materialization_job(i3p_env)
    assert mat_job is not None
    assert mat_job["status"] == "superseded"
    assert mat_job["rationale_code"] == "active_base_already_exists"

    # The pre-existing base is untouched; no new stable doc created
    assert await _count_stable_documents(i3p_env) == 0
    assert await _count_reading_bases(i3p_env) == 1  # only the pre-existing one


# ===================================================================
# No job available → process_once returns None
# ===================================================================


async def test_no_job_returns_none(i3p_env: asyncpg.Pool) -> None:
    """process_once returns None when no extraction or materialization job
    is available."""
    reader = FakeStorageObjectReader(data=_STABLE_TEXT.encode("utf-8"))
    service = ArtifactInputPipelineWorkerService(
        pool=i3p_env, storage_reader=reader,
    )

    result = await service.process_once(
        lease_owner=_LEASE_OWNER, lease_duration=_LEASE_DURATION,
    )
    assert result is None


# ===================================================================
# Script-level drain cycle: stale-lease recovery (D6 backend hardening: Task 1)
# ===================================================================
#
# These tests exercise ``_run_drain_cycle`` from the artifact pipeline worker
# script directly using a no-network fake service. They do NOT use the
# ``i3p_env`` DB fixture — recovery and drain are stubbed at the script
# boundary so no real Postgres or network calls happen.


class TestArtifactPipelineDrainStaleLease:
    async def test_drain_cycle_calls_recover_before_processing(self) -> None:
        """Drain cycle must call ``ReaderJobRuntime.recover_stale_leases`` before
        the downstream ``service.drain``, using the independent batch size.
        """
        from unittest.mock import AsyncMock

        from scripts.run_reader_artifact_pipeline_worker import _run_drain_cycle

        order: list[str] = []

        async def _recover_side_effect(*, batch_size: int) -> int:
            order.append(f"recover:{batch_size}")
            return 0

        recover_mock = AsyncMock(side_effect=_recover_side_effect)

        from app.services.reader_orchestration import job_runtime

        original_recover = job_runtime.ReaderJobRuntime.recover_stale_leases
        # ``AsyncMock`` correctly handles the bound-method ``self`` (a plain
        # async function with keyword-only args does NOT — Python would pass
        # the instance as a positional arg, breaking the keyword-only call).
        job_runtime.ReaderJobRuntime.recover_stale_leases = recover_mock  # type: ignore[assignment]
        try:
            class _Svc:
                async def drain(
                    self,
                    *,
                    lease_owner: str,
                    lease_duration: timedelta,
                    max_ticks: int,
                ) -> list:
                    order.append("drain")
                    return []

            svc = _Svc()
            out = await _run_drain_cycle(
                service=svc,  # type: ignore[arg-type]
                lease_owner="owner",
                lease_duration=timedelta(seconds=30),
                max_ticks=5,
                recover_batch_size=200,
            )
        finally:
            job_runtime.ReaderJobRuntime.recover_stale_leases = original_recover  # type: ignore[assignment]

        assert out == []
        assert order == ["recover:200", "drain"], (
            "stale-lease recovery must precede drain"
        )

    async def test_recover_failure_is_not_swallowed(self) -> None:
        """If ``recover_stale_leases`` raises, the drain cycle must re-raise
        and MUST NOT call ``service.drain``.
        """
        from scripts.run_reader_artifact_pipeline_worker import _run_drain_cycle

        async def _recover_side_effect(*, batch_size: int) -> int:
            raise RuntimeError("simulated DB drop")

        from unittest.mock import AsyncMock

        recover_mock = AsyncMock(side_effect=_recover_side_effect)

        from app.services.reader_orchestration import job_runtime

        original_recover = job_runtime.ReaderJobRuntime.recover_stale_leases
        job_runtime.ReaderJobRuntime.recover_stale_leases = recover_mock  # type: ignore[assignment]
        try:
            class _Svc:
                async def drain(
                    self, *, lease_owner, lease_duration, max_ticks
                ) -> list:
                    raise AssertionError("drain must NOT run if recover raised")

            svc = _Svc()
            with pytest.raises(RuntimeError, match="simulated DB drop"):
                await _run_drain_cycle(
                    service=svc,  # type: ignore[arg-type]
                    lease_owner="o",
                    lease_duration=timedelta(seconds=10),
                    max_ticks=1,
                )
        finally:
            job_runtime.ReaderJobRuntime.recover_stale_leases = original_recover  # type: ignore[assignment]
