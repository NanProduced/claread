"""Tests for D6-I4C Article RAG Index Worker Foundation.

Covers the 12+ test requirements from the task spec:
 1. no job -> returns None
 2. happy path fake embedding + fake vector writer -> job succeeded, index_run indexed
 3. job input_json forbidden projection keys do not appear
 4. worker reloads plan and validates plan_content_sha256
 5. plan hash mismatch fail closed, no vector write
 6. index_run missing / wrong status / wrong job_id fail closed
 7. base fence stale generation / inactive base / active_base mismatch superseded
 8. embedding provider unconfigured -> failed_terminal, index_run failed with error_json
 9. retryable embedding error -> retry_later, index_run status not permanently indexing
10. vector writer error retryable / terminal routing
11. duplicate/retry execution idempotency: indexed row + succeeded job no duplicate upsert
12. tests use fake providers only; no real LLM/embedding/Zilliz network
13. plan truth drift (inactive stable document) -> superseded
14. vector chunk payload excludes chunk text
15. git diff --check clean (verified separately)

Uses real PostgreSQL with a temporary schema (BASELINE_SQL, which now
includes 0004_reader_document_blocks.sql and 0010_reader_article_rag_index_state.sql).
All embedding/vector providers are fake/in-memory — no real DashScope/Bailian/Zilliz calls.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.contracts.annotation import utf16_code_unit_length
from app.database.connection import init_connection
from app.services.reader_orchestration.article_rag_index_bootstrap import (
    ArticleRagIndexBootstrapService,
    DEFAULT_INDEX_VERSION,
)
from app.services.reader_orchestration.article_rag_index_plan import (
    ArticleRagIndexPlanError,
    compute_plan_content_sha256,
)
from app.services.reader_orchestration.article_rag_embedding_provider import (
    DashScopeArticleRagEmbeddingProvider,
)
from app.services.reader_orchestration.article_rag_index_worker import (
    ARTICLE_RAG_INDEX_JOB_SOURCE,
    ArticleRagEmbedding,
    ArticleRagEmbeddingProvider,
    ArticleRagIndexWorkerError,
    ArticleRagIndexWorkerResult,
    ArticleRagIndexWorkerService,
    ArticleRagVectorChunk,
    ArticleRagVectorWriteMetadata,
    ArticleRagVectorWriteResult,
    ArticleRagVectorWriter,
    DEFAULT_FAKE_EMBEDDING_DIM,
    DEFAULT_FAKE_EMBEDDING_MODEL,
    DEFAULT_FAKE_VECTOR_COLLECTION,
    DEFAULT_FAKE_VECTOR_STORE_PROVIDER,
    FakeArticleRagEmbeddingProvider,
    FakeArticleRagVectorWriter,
    UnconfiguredArticleRagEmbeddingProvider,
    UnconfiguredArticleRagVectorWriter,
)

pytestmark = pytest.mark.anyio

REPO_ROOT = Path(__file__).resolve().parents[3]

# Reuse seed helpers + UUIDs from the I4A test module.
from tests.test_d6_i4a_article_rag_index_plan import (  # noqa: E402
    _BASE_ID,
    _OTHER_USER_ID,
    _RECORD_ID,
    _STABLE_DOC_ID,
    _USER_ID,
    _build_base_text_and_offsets,
    _main_reading_policy,
    _metadata_only_policy,
    _rag_ask_only_policy,
    _seed_base,
    _seed_block,
    _seed_full_environment,
    _seed_record,
    _seed_stable_document,
    _seed_unit,
    _seed_segment,
    _seed_user,
)
from tests.test_reader_orchestration_schema_baseline import (  # noqa: E402
    BASELINE_SQL,
    DATABASE_URL,
)

# P1-C: migration 0021 adds the durable ``profile_fingerprint`` column
# (NOT NULL, SHA-256 CHECK) to ``reader_article_rag_index_runs``.  The
# bootstrap service now writes this column on every fresh insert, so
# the Article RAG worker test schema must apply migration 0021 on top
# of BASELINE_SQL.  Per-file append (not a global BASELINE_SQL mutation).
_MIGRATION_0021_PATH = (
    REPO_ROOT / "infra" / "migrations" / "0021_reader_article_rag_profile_fingerprint.sql"
)
_MIGRATION_0021_SQL = _MIGRATION_0021_PATH.read_text(encoding="utf-8")

INDEX_WORKER_SCHEMA_SQL = BASELINE_SQL + "\n" + _MIGRATION_0021_SQL


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
async def worker_env() -> asyncpg.Pool:
    schema_name = f"test_i4c_rag_{uuid4().hex}"
    admin_conn = await _connect_admin()
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(INDEX_WORKER_SCHEMA_SQL)
        pool = await _make_pool(schema_name)
        try:
            yield pool
        finally:
            await pool.close()
    finally:
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


# ---------------------------------------------------------------------------
# Environment seed helpers (paragraph-only happy path)
# ---------------------------------------------------------------------------


async def _seed_paragraph_environment(
    pool: asyncpg.Pool,
    *,
    paragraph_text: str = "Indexable paragraph for happy path.",
    record_generation: int = 1,
) -> str:
    """Seed user + record + base + stable document + one main_reading paragraph.

    Returns the base content_sha256.
    """
    await _seed_full_environment(
        pool,
        base_text=paragraph_text,
        record_generation=record_generation,
    )
    await _seed_block(
        pool,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=paragraph_text,
        canonical_text_start_utf16=0,
        canonical_text_end_utf16=utf16_code_unit_length(paragraph_text),
        interpretation_policy=_main_reading_policy(),
    )
    return hashlib.sha256(paragraph_text.encode("utf-8")).hexdigest()


def _build_bootstrap_service(pool: asyncpg.Pool) -> ArticleRagIndexBootstrapService:
    return ArticleRagIndexBootstrapService(pool=pool)


def _build_worker_service(
    pool: asyncpg.Pool,
    *,
    embedding_provider: ArticleRagEmbeddingProvider | None = None,
    vector_writer: ArticleRagVectorWriter | None = None,
) -> ArticleRagIndexWorkerService:
    return ArticleRagIndexWorkerService(
        pool=pool,
        embedding_provider=embedding_provider,
        vector_writer=vector_writer,
    )


_LEASE_OWNER = "test-article-rag-index-worker"
_LEASE_DURATION = timedelta(seconds=60)
_RETRY_DELAY = timedelta(seconds=10)


# ---------------------------------------------------------------------------
# Row fetch helpers
# ---------------------------------------------------------------------------


async def _fetch_index_run(
    conn: asyncpg.Connection,
    *,
    index_run_id: UUID,
) -> asyncpg.Record:
    return await conn.fetchrow(
        """
        SELECT id, reading_record_id, stable_document_id, base_id,
               record_generation,
               stable_document_content_sha256, canonical_text_sha256,
               plan_content_sha256, chunk_count, status,
               index_version, chunker_version,
               embedding_model, vector_store_provider, vector_collection,
               job_id, reader_run_id,
               error_json, metadata_json, completed_at
        FROM reader_article_rag_index_runs
        WHERE id = $1
        """,
        index_run_id,
    )


async def _fetch_job(
    conn: asyncpg.Connection,
    *,
    job_id: UUID,
) -> asyncpg.Record:
    return await conn.fetchrow(
        """
        SELECT id, reading_record_id, base_id, run_id, user_id,
               job_type, target_type, target_key, status, priority,
               expected_generation, operation_fingerprint, idempotency_key,
               input_hash, input_json, max_attempts, attempt_count,
               lease_owner, lease_token, lease_expires_at, claimed_at,
               rationale_code, failure_class, failure_code, failure_message,
               output_ref_json
        FROM reader_jobs
        WHERE id = $1
        """,
        job_id,
    )


async def _fetch_run(
    conn: asyncpg.Connection,
    *,
    run_id: UUID,
) -> asyncpg.Record:
    return await conn.fetchrow(
        """
        SELECT id, reading_record_id, user_id, run_type, status,
               record_generation, failure_class, failure_code, finished_at
        FROM reader_runs
        WHERE id = $1
        """,
        run_id,
    )


# ---------------------------------------------------------------------------
# Custom fake providers for testing error paths and state mutations
# ---------------------------------------------------------------------------


class _RetryableEmbeddingProvider:
    """Fake embedding provider that always raises a retryable error."""

    def __init__(self) -> None:
        self.call_count = 0

    async def embed_texts(
        self,
        texts: list[str],
        *,
        model: str | None = None,
    ) -> list[ArticleRagEmbedding]:
        self.call_count += 1
        raise ArticleRagIndexWorkerError(
            "fake retryable embedding failure",
            retryable=True,
            failure_class="embedding",
            failure_code="embedding_failed",
            diagnostics={
                "provider_status": 429,
                "provider_code": "Throttling.User",
                "provider_retryable": True,
                "failed_batch_ordinal": 1,
                "batch_count": 1,
            },
        )


class _TerminalEmbeddingProvider:
    """Fake embedding provider that always raises a terminal error."""

    def __init__(self) -> None:
        self.call_count = 0

    async def embed_texts(
        self,
        texts: list[str],
        *,
        model: str | None = None,
    ) -> list[ArticleRagEmbedding]:
        self.call_count += 1
        raise ArticleRagIndexWorkerError(
            "fake terminal embedding failure",
            retryable=False,
            failure_class="embedding",
            failure_code="embedding_failed",
        )


class _RetryableVectorWriter:
    """Fake vector writer that always raises a retryable error."""

    def __init__(self) -> None:
        self.call_count = 0

    async def upsert_chunks(
        self,
        *,
        collection: str,
        chunks_with_embeddings: list[ArticleRagVectorChunk],
        metadata: ArticleRagVectorWriteMetadata,
    ) -> ArticleRagVectorWriteResult:
        self.call_count += 1
        raise ArticleRagIndexWorkerError(
            "fake retryable vector write failure",
            retryable=True,
            failure_class="vector_write",
            failure_code="vector_write_failed",
        )


class _TerminalVectorWriter:
    """Fake vector writer that always raises a terminal error."""

    def __init__(self) -> None:
        self.call_count = 0

    async def upsert_chunks(
        self,
        *,
        collection: str,
        chunks_with_embeddings: list[ArticleRagVectorChunk],
        metadata: ArticleRagVectorWriteMetadata,
    ) -> ArticleRagVectorWriteResult:
        self.call_count += 1
        raise ArticleRagIndexWorkerError(
            "fake terminal vector write failure",
            retryable=False,
            failure_class="vector_write",
            failure_code="vector_write_failed",
        )


class _PartialVectorWriter:
    """Fake vector writer that reports a partial successful upsert."""

    def __init__(self, *, upserted_count: int = 0) -> None:
        self.call_count = 0
        self.upserted_count = upserted_count

    async def upsert_chunks(
        self,
        *,
        collection: str,
        chunks_with_embeddings: list[ArticleRagVectorChunk],
        metadata: ArticleRagVectorWriteMetadata,
    ) -> ArticleRagVectorWriteResult:
        self.call_count += 1
        return ArticleRagVectorWriteResult(
            collection=collection,
            upserted_count=self.upserted_count,
            provider_metadata={"provider": "partial_fake"},
        )


class _FenceMutatingEmbeddingProvider:
    """Fake embedding provider that mutates the record during embed_texts.

    Used to test the publish-time fence failure in Phase 5.  The provider
    succeeds (returns real embeddings) but mutates the record so the
    publish fence in _mark_indexed_and_succeed fails.
    """

    def __init__(
        self,
        *,
        pool: asyncpg.Pool,
        mutation: str,
        record_id: UUID = _RECORD_ID,
        base_id: UUID = _BASE_ID,
    ) -> None:
        self._pool = pool
        self._mutation = mutation
        self._record_id = record_id
        self._base_id = base_id
        self._inner = FakeArticleRagEmbeddingProvider()
        self.call_count = 0

    async def _mutate(self) -> None:
        async with self._pool.acquire() as conn:
            if self._mutation == "bump_generation":
                # Bump generation AND clear active_base_id. The FK
                # fk_reading_records_active_base is on
                # (active_base_id, id, generation), so keeping the old
                # active_base_id when bumping generation would violate it.
                # Clearing active_base_id also makes the publish fence fail
                # with stale_generation (generation no longer matches
                # expected_generation).
                await conn.execute(
                    "UPDATE reading_records "
                    "SET generation = generation + 1, active_base_id = NULL "
                    "WHERE id = $1",
                    self._record_id,
                )
            elif self._mutation == "deactivate_base":
                await conn.execute(
                    "UPDATE reading_bases SET status = 'superseded' "
                    "WHERE id = $1",
                    self._base_id,
                )
            elif self._mutation == "active_base_mismatch":
                # Clear active_base_id so the job's base_id no longer matches
                # reading_records.active_base_id. This triggers the
                # active_base_mismatch rationale at publish fence. We avoid
                # creating a second base because uq_reading_bases_record_generation
                # prevents two bases at the same (record_id, record_generation).
                await conn.execute(
                    "UPDATE reading_records SET active_base_id = NULL "
                    "WHERE id = $1",
                    self._record_id,
                )
            else:
                raise ValueError(f"unknown mutation: {self._mutation}")

    async def embed_texts(
        self,
        texts: list[str],
        *,
        model: str | None = None,
    ) -> list[ArticleRagEmbedding]:
        self.call_count += 1
        # Mutate the record BEFORE returning embeddings so the publish
        # fence in Phase 5 detects the mutation.
        await self._mutate()
        return await self._inner.embed_texts(texts, model=model)


# ---------------------------------------------------------------------------
# Helpers to reset a job to queued (simulating lease expiry recovery)
# ---------------------------------------------------------------------------


async def _reset_job_to_queued(
    pool: asyncpg.Pool,
    *,
    job_id: UUID,
) -> None:
    """Reset a job to 'queued' status, simulating recover_stale_leases."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE reader_jobs
            SET status = 'queued',
                lease_owner = NULL,
                lease_token = NULL,
                lease_expires_at = NULL,
                claimed_at = NULL,
                available_at = NOW(),
                updated_at = NOW()
            WHERE id = $1
            """,
            job_id,
        )


# ===================================================================
# Test 1: no job -> returns None
# ===================================================================


async def test_no_job_returns_none(worker_env: asyncpg.Pool) -> None:
    """Requirement 1: when no article_rag_index_build job is queued,
    process_next returns None."""
    await _seed_paragraph_environment(worker_env)
    # No bootstrap — no job enqueued.
    service = _build_worker_service(
        worker_env,
        embedding_provider=FakeArticleRagEmbeddingProvider(),
        vector_writer=FakeArticleRagVectorWriter(),
    )
    result = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )
    assert result is None


# ===================================================================
# Test 2: happy path — fake embedding + fake vector writer
# ===================================================================


async def test_happy_path_fake_providers(worker_env: asyncpg.Pool) -> None:
    """Requirement 2: happy path with fake embedding + fake vector writer.
    Job transitions to succeeded, index_run transitions to indexed,
    reader_run transitions to completed."""
    await _seed_paragraph_environment(worker_env)
    bootstrap = _build_bootstrap_service(worker_env)
    bootstrap_result = await bootstrap.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )
    assert bootstrap_result.idempotent_noop is False
    assert bootstrap_result.chunk_count == 1

    embedding_provider = FakeArticleRagEmbeddingProvider()
    vector_writer = FakeArticleRagVectorWriter()
    service = _build_worker_service(
        worker_env,
        embedding_provider=embedding_provider,
        vector_writer=vector_writer,
    )

    result = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )

    assert result is not None
    assert isinstance(result, ArticleRagIndexWorkerResult)
    assert result.status == "succeeded"
    assert result.job_id == bootstrap_result.job_id
    assert result.index_run_id == bootstrap_result.index_run_id
    assert result.reading_record_id == _RECORD_ID
    assert result.stable_document_id == _STABLE_DOC_ID
    assert result.base_id == _BASE_ID
    assert result.chunk_count == 1
    assert result.embedding_model == DEFAULT_FAKE_EMBEDDING_MODEL
    assert result.vector_store_provider == DEFAULT_FAKE_VECTOR_STORE_PROVIDER
    assert result.vector_collection == DEFAULT_FAKE_VECTOR_COLLECTION
    assert result.retryable is None
    assert result.failure_code is None
    assert result.idempotent_noop is False

    # Fake providers were called exactly once.
    assert embedding_provider.call_count == 1
    assert vector_writer.call_count == 1

    async with worker_env.acquire() as conn:
        job = await _fetch_job(conn, job_id=bootstrap_result.job_id)
        assert job["status"] == "succeeded"
        assert job["lease_token"] is None
        assert job["lease_expires_at"] is None

        index_run = await _fetch_index_run(
            conn, index_run_id=bootstrap_result.index_run_id,
        )
        assert index_run["status"] == "indexed"
        assert index_run["embedding_model"] == DEFAULT_FAKE_EMBEDDING_MODEL
        assert index_run["vector_store_provider"] == DEFAULT_FAKE_VECTOR_STORE_PROVIDER
        assert index_run["vector_collection"] == DEFAULT_FAKE_VECTOR_COLLECTION
        assert index_run["completed_at"] is not None

        # The run_id is linked via job["run_id"], not job_id.
        run = await _fetch_run(conn, run_id=job["run_id"])
        assert run["status"] == "completed"
        assert run["finished_at"] is not None


# ===================================================================
# Test 3: job input_json excludes forbidden projection keys
# ===================================================================


_FORBIDDEN_PAYLOAD_KEYS = frozenset(
    {
        "chunks",
        "chunk_text",
        "chunk_texts",
        "plate",
        "plate_json",
        "markdown",
        "markdown_syntax",
        "dom",
        "dom_selection",
        "slate",
        "slate_path",
        "ui",
        "ui_display_group",
        "render_profile",
        "render_snapshot",
        "citation_refs",
    }
)


async def test_input_json_excludes_forbidden_keys(
    worker_env: asyncpg.Pool,
) -> None:
    """Requirement 3: the job input_json only contains IDs and run params.
    No chunk text, Plate JSON, Markdown syntax, DOM/Slate/UI fields."""
    await _seed_paragraph_environment(worker_env)
    bootstrap = _build_bootstrap_service(worker_env)
    result = await bootstrap.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    async with worker_env.acquire() as conn:
        job = await _fetch_job(conn, job_id=result.job_id)
        input_json = dict(job["input_json"])

    expected_keys = {
        "source",
        "reading_record_id",
        "stable_document_id",
        "base_id",
        "record_generation",
        "index_run_id",
        "index_version",
        "chunker_version",
        "profile_fingerprint",
    }
    assert set(input_json.keys()) == expected_keys
    assert input_json["source"] == ARTICLE_RAG_INDEX_JOB_SOURCE
    # P1-C: profile_fingerprint is the canonical SHA-256 of the V1
    # ArticleRagIndexProfile, frozen into input_json by the bootstrap.
    # The worker is permitted to ignore this field this round.
    assert input_json["profile_fingerprint"] == result.profile_fingerprint
    assert len(input_json["profile_fingerprint"]) == 64

    for forbidden in _FORBIDDEN_PAYLOAD_KEYS:
        assert forbidden not in input_json


# ===================================================================
# Test 4: worker reloads plan and validates plan_content_sha256
# ===================================================================


async def test_worker_reloads_plan_and_validates_hash(
    worker_env: asyncpg.Pool,
) -> None:
    """Requirement 4: the worker reloads the plan via
    ArticleRagIndexPlanService and validates plan_content_sha256 against
    the index_run row.  Verified by checking the fake embedding provider
    was called with the correct chunk texts (which come from the reloaded
    plan, not from input_json)."""
    paragraph_text = "Indexable paragraph for happy path."
    await _seed_paragraph_environment(worker_env, paragraph_text=paragraph_text)
    bootstrap = _build_bootstrap_service(worker_env)
    await bootstrap.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    embedding_provider = FakeArticleRagEmbeddingProvider()
    vector_writer = FakeArticleRagVectorWriter()
    service = _build_worker_service(
        worker_env,
        embedding_provider=embedding_provider,
        vector_writer=vector_writer,
    )

    result = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )
    assert result is not None
    assert result.status == "succeeded"

    # The embedding provider was called with the chunk text from the
    # reloaded plan (not from input_json, which doesn't contain text).
    assert embedding_provider.call_count == 1
    assert embedding_provider.last_texts == [paragraph_text]


# ===================================================================
# Test 5: plan hash mismatch fail closed, no vector write
# ===================================================================


async def test_plan_hash_mismatch_fail_closed_no_vector_write(
    worker_env: asyncpg.Pool,
) -> None:
    """Requirement 5: when the truth layer changes between bootstrap and
    worker execution (plan_content_sha256 differs), the worker fails
    closed with failure_code=plan_hash_mismatch and does NOT call the
    vector writer."""
    first_text = "Indexable paragraph for happy path."
    second_text = "Second paragraph added after bootstrap."
    await _seed_paragraph_environment(worker_env, paragraph_text=first_text)
    bootstrap = _build_bootstrap_service(worker_env)
    bootstrap_result = await bootstrap.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )
    assert bootstrap_result.chunk_count == 1

    # Mutate the truth layer: add a second paragraph block + update base text.
    base_text, offsets = _build_base_text_and_offsets(first_text, second_text)
    new_base_sha = hashlib.sha256(base_text.encode("utf-8")).hexdigest()
    async with worker_env.acquire() as conn:
        await conn.execute(
            """
            UPDATE reading_bases
            SET text = $2, content_sha256 = $3, content_utf16_length = $4
            WHERE id = $1
            """,
            _BASE_ID,
            base_text,
            new_base_sha,
            utf16_code_unit_length(base_text),
        )
    second_start, second_end = offsets[1]
    await _seed_block(
        worker_env,
        block_id="paragraph-2",
        order_index=1,
        block_type="paragraph",
        text_content=second_text,
        canonical_text_start_utf16=second_start,
        canonical_text_end_utf16=second_end,
        interpretation_policy=_main_reading_policy(),
    )

    embedding_provider = FakeArticleRagEmbeddingProvider()
    vector_writer = FakeArticleRagVectorWriter()
    service = _build_worker_service(
        worker_env,
        embedding_provider=embedding_provider,
        vector_writer=vector_writer,
    )

    result = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )

    assert result is not None
    assert result.status == "failed_terminal"
    assert result.failure_code == "plan_hash_mismatch"
    assert result.retryable is False

    # Embedding provider may or may not have been called (plan reload
    # happens before embedding).  Vector writer must NOT have been called.
    assert vector_writer.call_count == 0

    async with worker_env.acquire() as conn:
        job = await _fetch_job(conn, job_id=bootstrap_result.job_id)
        assert job["status"] == "failed_terminal"
        assert job["failure_code"] == "plan_hash_mismatch"

        index_run = await _fetch_index_run(
            conn, index_run_id=bootstrap_result.index_run_id,
        )
        assert index_run["status"] == "failed"
        error_json = dict(index_run["error_json"])
        assert error_json["failure_code"] == "plan_hash_mismatch"
        assert error_json["retryable"] is False


# ===================================================================
# Test 6a: index_run missing -> failed_terminal
# ===================================================================


async def test_index_run_missing_fail_closed(worker_env: asyncpg.Pool) -> None:
    """Requirement 6: when the index_run row is missing (deleted), the
    worker fails closed with failure_code=index_run_link_invalid.

    P1-D-R1: ``_load_job_context`` now reads the full candidate set
    linked by ``job_id`` and verifies cardinality BEFORE the downstream
    ``_mark_indexing_or_detect_noop`` check runs.  Deleting the index_run
    row yields 0 linked rows → ``index_run_link_invalid`` (replaces the
    pre-P1-D ``index_run_missing`` code, which can no longer be reached
    through ``_load_job_context``).
    """
    await _seed_paragraph_environment(worker_env)
    bootstrap = _build_bootstrap_service(worker_env)
    bootstrap_result = await bootstrap.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    # Delete the index_run row (FK ON DELETE CASCADE is not set on
    # reader_jobs, so the job survives).
    async with worker_env.acquire() as conn:
        await conn.execute(
            "DELETE FROM reader_article_rag_index_runs WHERE id = $1",
            bootstrap_result.index_run_id,
        )

    service = _build_worker_service(
        worker_env,
        embedding_provider=FakeArticleRagEmbeddingProvider(),
        vector_writer=FakeArticleRagVectorWriter(),
    )
    result = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )

    assert result is not None
    assert result.status == "failed_terminal"
    assert result.failure_code == "index_run_link_invalid"

    async with worker_env.acquire() as conn:
        job = await _fetch_job(conn, job_id=bootstrap_result.job_id)
        assert job["status"] == "failed_terminal"


# ===================================================================
# Test 6b: index_run wrong status -> failed_terminal
# ===================================================================


async def test_index_run_wrong_status_fail_closed(worker_env: asyncpg.Pool) -> None:
    """Requirement 6: when the index_run status is not queued/indexing/indexed
    (e.g. 'failed'), the worker fails closed with
    failure_code=index_run_wrong_status."""
    await _seed_paragraph_environment(worker_env)
    bootstrap = _build_bootstrap_service(worker_env)
    bootstrap_result = await bootstrap.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    # Manually set the index_run status to 'failed'.
    async with worker_env.acquire() as conn:
        await conn.execute(
            "UPDATE reader_article_rag_index_runs SET status = 'failed' "
            "WHERE id = $1",
            bootstrap_result.index_run_id,
        )

    service = _build_worker_service(
        worker_env,
        embedding_provider=FakeArticleRagEmbeddingProvider(),
        vector_writer=FakeArticleRagVectorWriter(),
    )
    result = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )

    assert result is not None
    assert result.status == "failed_terminal"
    assert result.failure_code == "index_run_wrong_status"


# ===================================================================
# Test 6c: index_run wrong job_id -> failed_terminal
# ===================================================================


async def test_index_run_wrong_job_id_fail_closed(worker_env: asyncpg.Pool) -> None:
    """Requirement 6: when the index_run references a different job_id
    than the current claim, the worker fails closed with
    failure_code=index_run_link_invalid.

    P1-D-R1: ``_load_job_context`` now reads the full candidate set
    linked by ``claim.job_id``.  Repointing the index_run at a bogus
    job_id leaves 0 rows linked to ``claim.job_id`` →
    ``index_run_link_invalid`` (replaces the pre-P1-D
    ``index_run_wrong_job_id`` code, which can no longer be reached
    through ``_load_job_context``).
    """
    await _seed_paragraph_environment(worker_env)
    bootstrap = _build_bootstrap_service(worker_env)
    bootstrap_result = await bootstrap.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    # Point the index_run at a different job_id.
    bogus_job_id = uuid4()
    async with worker_env.acquire() as conn:
        await conn.execute(
            "UPDATE reader_article_rag_index_runs SET job_id = $2 "
            "WHERE id = $1",
            bootstrap_result.index_run_id,
            bogus_job_id,
        )

    service = _build_worker_service(
        worker_env,
        embedding_provider=FakeArticleRagEmbeddingProvider(),
        vector_writer=FakeArticleRagVectorWriter(),
    )
    result = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )

    assert result is not None
    assert result.status == "failed_terminal"
    assert result.failure_code == "index_run_link_invalid"


# ===================================================================
# Test 7a: publish fence — stale generation -> superseded
# ===================================================================


async def test_publish_fence_stale_generation_superseded(
    worker_env: asyncpg.Pool,
) -> None:
    """Requirement 7: when the record generation is bumped between claim
    and publish (during embedding), the publish fence fails and the job
    + index_run are superseded."""
    await _seed_paragraph_environment(worker_env)
    bootstrap = _build_bootstrap_service(worker_env)
    bootstrap_result = await bootstrap.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    # Custom embedding provider that bumps the record generation during
    # embed_texts, causing the publish fence to fail in Phase 5.
    embedding_provider = _FenceMutatingEmbeddingProvider(
        pool=worker_env,
        mutation="bump_generation",
    )
    vector_writer = FakeArticleRagVectorWriter()
    service = _build_worker_service(
        worker_env,
        embedding_provider=embedding_provider,
        vector_writer=vector_writer,
    )

    result = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )

    assert result is not None
    assert result.status == "superseded"
    assert result.failure_code == "publish_fence_failed"

    # Embedding was called (mutation happens during embed_texts).
    assert embedding_provider.call_count == 1
    # Vector writer is not called after fence drift; the worker rechecks
    # the publish fence before any vector-store side effect.
    assert vector_writer.call_count == 0

    async with worker_env.acquire() as conn:
        job = await _fetch_job(conn, job_id=bootstrap_result.job_id)
        assert job["status"] == "superseded"

        index_run = await _fetch_index_run(
            conn, index_run_id=bootstrap_result.index_run_id,
        )
        assert index_run["status"] == "superseded"
        assert index_run["completed_at"] is not None
        error_json = dict(index_run["error_json"])
        assert error_json["failure_code"] == "publish_fence_failed"


# ===================================================================
# Test 7b: publish fence — inactive base -> superseded
# ===================================================================


async def test_publish_fence_inactive_base_superseded(
    worker_env: asyncpg.Pool,
) -> None:
    """Requirement 7: when the base is deactivated between claim and
    publish, the publish fence fails and the job + index_run are
    superseded."""
    await _seed_paragraph_environment(worker_env)
    bootstrap = _build_bootstrap_service(worker_env)
    bootstrap_result = await bootstrap.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    embedding_provider = _FenceMutatingEmbeddingProvider(
        pool=worker_env,
        mutation="deactivate_base",
    )
    vector_writer = FakeArticleRagVectorWriter()
    service = _build_worker_service(
        worker_env,
        embedding_provider=embedding_provider,
        vector_writer=vector_writer,
    )

    result = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )

    assert result is not None
    assert result.status == "superseded"
    assert result.failure_code == "publish_fence_failed"
    assert vector_writer.call_count == 0

    async with worker_env.acquire() as conn:
        job = await _fetch_job(conn, job_id=bootstrap_result.job_id)
        assert job["status"] == "superseded"

        index_run = await _fetch_index_run(
            conn, index_run_id=bootstrap_result.index_run_id,
        )
        assert index_run["status"] == "superseded"
        assert index_run["completed_at"] is not None


# ===================================================================
# Test 7c: publish fence — active_base mismatch -> superseded
# ===================================================================


async def test_publish_fence_active_base_mismatch_superseded(
    worker_env: asyncpg.Pool,
) -> None:
    """Requirement 7: when active_base_id is changed to a different base
    between claim and publish, the publish fence fails and the job +
    index_run are superseded."""
    await _seed_paragraph_environment(worker_env)
    bootstrap = _build_bootstrap_service(worker_env)
    bootstrap_result = await bootstrap.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    embedding_provider = _FenceMutatingEmbeddingProvider(
        pool=worker_env,
        mutation="active_base_mismatch",
    )
    vector_writer = FakeArticleRagVectorWriter()
    service = _build_worker_service(
        worker_env,
        embedding_provider=embedding_provider,
        vector_writer=vector_writer,
    )

    result = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )

    assert result is not None
    assert result.status == "superseded"
    assert result.failure_code == "publish_fence_failed"
    assert vector_writer.call_count == 0

    async with worker_env.acquire() as conn:
        job = await _fetch_job(conn, job_id=bootstrap_result.job_id)
        assert job["status"] == "superseded"

        index_run = await _fetch_index_run(
            conn, index_run_id=bootstrap_result.index_run_id,
        )
        assert index_run["status"] == "superseded"
        assert index_run["completed_at"] is not None


# ===================================================================
# Test 8: embedding provider unconfigured -> failed_terminal
# ===================================================================


async def test_embedding_provider_unconfigured_failed_terminal(
    worker_env: asyncpg.Pool,
) -> None:
    """Requirement 8: when the embedding provider is the default
    UnconfiguredArticleRagEmbeddingProvider, the worker fails closed with
    failure_code=embedding_provider_unconfigured and the index_run
    transitions to 'failed' with error_json."""
    await _seed_paragraph_environment(worker_env)
    bootstrap = _build_bootstrap_service(worker_env)
    bootstrap_result = await bootstrap.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    # No embedding_provider injected -> defaults to Unconfigured.
    service = _build_worker_service(
        worker_env,
        vector_writer=FakeArticleRagVectorWriter(),
    )
    result = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )

    assert result is not None
    assert result.status == "failed_terminal"
    assert result.failure_code == "embedding_provider_unconfigured"
    assert result.retryable is False

    async with worker_env.acquire() as conn:
        job = await _fetch_job(conn, job_id=bootstrap_result.job_id)
        assert job["status"] == "failed_terminal"
        assert job["failure_code"] == "embedding_provider_unconfigured"

        index_run = await _fetch_index_run(
            conn, index_run_id=bootstrap_result.index_run_id,
        )
        assert index_run["status"] == "failed"
        error_json = dict(index_run["error_json"])
        assert error_json["failure_code"] == "embedding_provider_unconfigured"
        assert error_json["retryable"] is False


# ===================================================================
# Test 8b: vector writer unconfigured -> failed_terminal
# ===================================================================


async def test_vector_writer_unconfigured_failed_terminal(
    worker_env: asyncpg.Pool,
) -> None:
    """Requirement 8b: when the vector writer is the default
    UnconfiguredArticleRagVectorWriter, the worker fails closed with
    failure_code=vector_writer_unconfigured."""
    await _seed_paragraph_environment(worker_env)
    bootstrap = _build_bootstrap_service(worker_env)
    bootstrap_result = await bootstrap.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    # No vector_writer injected -> defaults to Unconfigured.
    service = _build_worker_service(
        worker_env,
        embedding_provider=FakeArticleRagEmbeddingProvider(),
    )
    result = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )

    assert result is not None
    assert result.status == "failed_terminal"
    assert result.failure_code == "vector_writer_unconfigured"

    async with worker_env.acquire() as conn:
        index_run = await _fetch_index_run(
            conn, index_run_id=bootstrap_result.index_run_id,
        )
        assert index_run["status"] == "failed"
        error_json = dict(index_run["error_json"])
        assert error_json["failure_code"] == "vector_writer_unconfigured"


# ===================================================================
# Test 9: retryable embedding error -> retry_later
# ===================================================================


async def test_retryable_embedding_error_retry_later(
    worker_env: asyncpg.Pool,
) -> None:
    """Requirement 9: when the embedding provider raises a retryable
    error, the job transitions to retry_later and the index_run goes
    back to 'queued' (not permanently stuck in 'indexing')."""
    await _seed_paragraph_environment(worker_env)
    bootstrap = _build_bootstrap_service(worker_env)
    bootstrap_result = await bootstrap.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    embedding_provider = _RetryableEmbeddingProvider()
    vector_writer = FakeArticleRagVectorWriter()
    service = _build_worker_service(
        worker_env,
        embedding_provider=embedding_provider,
        vector_writer=vector_writer,
    )
    result = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )

    assert result is not None
    assert result.status == "retry_later"
    assert result.failure_code == "embedding_failed"
    assert result.retryable is True

    # Embedding was called (and failed).  Vector writer was NOT called.
    assert embedding_provider.call_count == 1
    assert vector_writer.call_count == 0

    async with worker_env.acquire() as conn:
        job = await _fetch_job(conn, job_id=bootstrap_result.job_id)
        assert job["status"] == "retry_later"
        assert job["rationale_code"] == "embedding_failed"
        assert job["failure_class"] == "embedding"
        assert job["failure_code"] == "embedding_failed"
        assert dict(job["output_ref_json"])["diagnostics"] == {
            "provider_status": 429,
            "provider_code": "Throttling.User",
            "provider_retryable": True,
            "failed_batch_ordinal": 1,
            "batch_count": 1,
        }

        index_run = await _fetch_index_run(
            conn, index_run_id=bootstrap_result.index_run_id,
        )
        # Index run must NOT be permanently stuck in 'indexing'.
        assert index_run["status"] == "queued"
        error_json = dict(index_run["error_json"])
        assert error_json["failure_code"] == "embedding_failed"
        assert error_json["retryable"] is True
        assert error_json["diagnostics"] == {
            "provider_status": 429,
            "provider_code": "Throttling.User",
            "provider_retryable": True,
            "failed_batch_ordinal": 1,
            "batch_count": 1,
        }
        event_payload = await conn.fetchval(
            "SELECT payload_json FROM reader_job_events WHERE job_id = $1 "
            "AND event_type = 'job_retry_later' ORDER BY created_at DESC LIMIT 1",
            bootstrap_result.job_id,
        )
        assert dict(event_payload)["output_ref"]["diagnostics"] == error_json["diagnostics"]


# ===================================================================
# Test 10a: retryable vector writer error -> retry_later
# ===================================================================


async def test_retryable_embedding_error_at_max_attempts_is_terminal_once(
    worker_env: asyncpg.Pool,
) -> None:
    """A claimed final attempt must not be returned to retry_later forever.

    P0-B round 2 strengthening: also asserts exactly 0 ``job_retry_later``
    events, vector writer never called, and a second ``process_next`` does
    not produce a second ``job_failed_terminal`` event.
    """
    await _seed_paragraph_environment(worker_env)
    bootstrap_result = await _build_bootstrap_service(worker_env).bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )
    async with worker_env.acquire() as conn:
        await conn.execute(
            "UPDATE reader_jobs SET max_attempts = 1 WHERE id = $1",
            bootstrap_result.job_id,
        )

    embedding_provider = _RetryableEmbeddingProvider()
    vector_writer = FakeArticleRagVectorWriter()
    service = _build_worker_service(
        worker_env,
        embedding_provider=embedding_provider,
        vector_writer=vector_writer,
    )
    result = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )

    assert result is not None
    assert result.status == "failed_terminal"
    assert result.retryable is False
    assert result.failure_code == "embedding_failed"
    # Vector writer must not be called when embedding fails terminally.
    assert vector_writer.call_count == 0

    # Second process_next must return None (job is terminal, not claimable).
    second_result = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )
    assert second_result is None

    async with worker_env.acquire() as conn:
        job = await _fetch_job(conn, job_id=bootstrap_result.job_id)
        run = await _fetch_run(conn, run_id=job["run_id"])
        index_run = await _fetch_index_run(conn, index_run_id=bootstrap_result.index_run_id)
        terminal_events = await conn.fetchval(
            "SELECT count(*) FROM reader_job_events WHERE job_id = $1 "
            "AND event_type = 'job_failed_terminal'",
            bootstrap_result.job_id,
        )
        retry_events = await conn.fetchval(
            "SELECT count(*) FROM reader_job_events WHERE job_id = $1 "
            "AND event_type = 'job_retry_later'",
            bootstrap_result.job_id,
        )

    assert job["status"] == "failed_terminal"
    assert job["failure_class"] == "embedding"
    assert job["failure_code"] == "embedding_failed"
    assert job["rationale_code"] == "max_attempts_exceeded"
    assert run["status"] == "failed_terminal"
    assert run["failure_class"] == "embedding"
    assert run["failure_code"] == "embedding_failed"
    assert index_run["status"] == "failed"
    assert dict(index_run["error_json"])["rationale_code"] == "max_attempts_exceeded"
    assert terminal_events == 1
    assert retry_events == 0


async def test_retryable_vector_writer_error_retry_later(
    worker_env: asyncpg.Pool,
) -> None:
    """Requirement 10: when the vector writer raises a retryable error,
    the job transitions to retry_later and the index_run goes back to
    'queued'."""
    await _seed_paragraph_environment(worker_env)
    bootstrap = _build_bootstrap_service(worker_env)
    bootstrap_result = await bootstrap.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    embedding_provider = FakeArticleRagEmbeddingProvider()
    vector_writer = _RetryableVectorWriter()
    service = _build_worker_service(
        worker_env,
        embedding_provider=embedding_provider,
        vector_writer=vector_writer,
    )
    result = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )

    assert result is not None
    assert result.status == "retry_later"
    assert result.failure_code == "vector_write_failed"
    assert result.retryable is True

    # Embedding was called (succeeded).  Vector writer was called (and failed).
    assert embedding_provider.call_count == 1
    assert vector_writer.call_count == 1

    async with worker_env.acquire() as conn:
        job = await _fetch_job(conn, job_id=bootstrap_result.job_id)
        assert job["status"] == "retry_later"

        index_run = await _fetch_index_run(
            conn, index_run_id=bootstrap_result.index_run_id,
        )
        assert index_run["status"] == "queued"
        error_json = dict(index_run["error_json"])
        assert error_json["failure_code"] == "vector_write_failed"
        assert error_json["retryable"] is True
        assert index_run["completed_at"] is None


async def test_partial_vector_writer_count_retry_later(
    worker_env: asyncpg.Pool,
) -> None:
    """A writer that reports fewer upserts than planned is retryable.

    The vector writer may have performed a partial idempotent write, so
    the job is retried instead of marked indexed.
    """
    await _seed_paragraph_environment(worker_env)
    bootstrap = _build_bootstrap_service(worker_env)
    bootstrap_result = await bootstrap.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    embedding_provider = FakeArticleRagEmbeddingProvider()
    vector_writer = _PartialVectorWriter(upserted_count=0)
    service = _build_worker_service(
        worker_env,
        embedding_provider=embedding_provider,
        vector_writer=vector_writer,
    )
    result = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )

    assert result is not None
    assert result.status == "retry_later"
    assert result.failure_code == "vector_write_failed"
    assert result.retryable is True
    assert embedding_provider.call_count == 1
    assert vector_writer.call_count == 1

    async with worker_env.acquire() as conn:
        job = await _fetch_job(conn, job_id=bootstrap_result.job_id)
        assert job["status"] == "retry_later"

        index_run = await _fetch_index_run(
            conn, index_run_id=bootstrap_result.index_run_id,
        )
        assert index_run["status"] == "queued"
        assert index_run["completed_at"] is None
        error_json = dict(index_run["error_json"])
        assert error_json["failure_code"] == "vector_write_failed"
        assert error_json["retryable"] is True


# ===================================================================
# Test 10b: terminal vector writer error -> failed_terminal
# ===================================================================


async def test_terminal_vector_writer_error_failed_terminal(
    worker_env: asyncpg.Pool,
) -> None:
    """Requirement 10: when the vector writer raises a terminal error,
    the job transitions to failed_terminal and the index_run transitions
    to 'failed'."""
    await _seed_paragraph_environment(worker_env)
    bootstrap = _build_bootstrap_service(worker_env)
    bootstrap_result = await bootstrap.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    embedding_provider = FakeArticleRagEmbeddingProvider()
    vector_writer = _TerminalVectorWriter()
    service = _build_worker_service(
        worker_env,
        embedding_provider=embedding_provider,
        vector_writer=vector_writer,
    )
    result = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )

    assert result is not None
    assert result.status == "failed_terminal"
    assert result.failure_code == "vector_write_failed"
    assert result.retryable is False

    async with worker_env.acquire() as conn:
        job = await _fetch_job(conn, job_id=bootstrap_result.job_id)
        assert job["status"] == "failed_terminal"

        index_run = await _fetch_index_run(
            conn, index_run_id=bootstrap_result.index_run_id,
        )
        assert index_run["status"] == "failed"
        assert index_run["completed_at"] is not None
        error_json = dict(index_run["error_json"])
        assert error_json["failure_code"] == "vector_write_failed"
        assert error_json["retryable"] is False


# ===================================================================
# Test 10c: terminal embedding error -> failed_terminal
# ===================================================================


async def test_terminal_embedding_error_failed_terminal(
    worker_env: asyncpg.Pool,
) -> None:
    """Requirement 10c: when the embedding provider raises a terminal
    error, the job transitions to failed_terminal."""
    await _seed_paragraph_environment(worker_env)
    bootstrap = _build_bootstrap_service(worker_env)
    bootstrap_result = await bootstrap.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    embedding_provider = _TerminalEmbeddingProvider()
    vector_writer = FakeArticleRagVectorWriter()
    service = _build_worker_service(
        worker_env,
        embedding_provider=embedding_provider,
        vector_writer=vector_writer,
    )
    result = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )

    assert result is not None
    assert result.status == "failed_terminal"
    assert result.failure_code == "embedding_failed"

    async with worker_env.acquire() as conn:
        index_run = await _fetch_index_run(
            conn, index_run_id=bootstrap_result.index_run_id,
        )
        assert index_run["status"] == "failed"
        assert index_run["completed_at"] is not None


# ===================================================================
# Test 11: duplicate execution idempotency
# ===================================================================


async def test_duplicate_execution_idempotent_noop(
    worker_env: asyncpg.Pool,
) -> None:
    """Requirement 11: when a job is re-claimed after the index_run is
    already 'indexed' (e.g. lease expired and was recovered), the worker
    detects the already-indexed state and transitions the job to
    'succeeded' with idempotent_noop=True without calling providers
    again or re-upserting vectors."""
    await _seed_paragraph_environment(worker_env)
    bootstrap = _build_bootstrap_service(worker_env)
    bootstrap_result = await bootstrap.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    embedding_provider = FakeArticleRagEmbeddingProvider()
    vector_writer = FakeArticleRagVectorWriter()
    service = _build_worker_service(
        worker_env,
        embedding_provider=embedding_provider,
        vector_writer=vector_writer,
    )

    # First run: happy path.
    result1 = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )
    assert result1 is not None
    assert result1.status == "succeeded"
    assert result1.idempotent_noop is False
    assert embedding_provider.call_count == 1
    assert vector_writer.call_count == 1

    # Reset the job to 'queued' (simulating lease expiry + recover_stale_leases).
    await _reset_job_to_queued(
        worker_env, job_id=bootstrap_result.job_id,
    )

    # Second run: the index_run is already 'indexed'.
    result2 = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )
    assert result2 is not None
    assert result2.status == "succeeded"
    assert result2.idempotent_noop is True
    assert result2.chunk_count == 1
    assert result2.embedding_model == DEFAULT_FAKE_EMBEDDING_MODEL
    assert result2.vector_store_provider == DEFAULT_FAKE_VECTOR_STORE_PROVIDER
    assert result2.vector_collection == DEFAULT_FAKE_VECTOR_COLLECTION

    # Providers were NOT called again (idempotent no-op).
    assert embedding_provider.call_count == 1
    assert vector_writer.call_count == 1

    async with worker_env.acquire() as conn:
        job = await _fetch_job(conn, job_id=bootstrap_result.job_id)
        assert job["status"] == "succeeded"

        index_run = await _fetch_index_run(
            conn, index_run_id=bootstrap_result.index_run_id,
        )
        assert index_run["status"] == "indexed"


# ===================================================================
# Test 12: no real network calls — fake providers only
# ===================================================================


async def test_no_real_network_calls_fake_providers_only(
    worker_env: asyncpg.Pool,
) -> None:
    """Requirement 12: verify that fake providers are used and
    unconfigured providers raise ArticleRagIndexWorkerError without
    making any network calls."""
    await _seed_paragraph_environment(worker_env)
    bootstrap = _build_bootstrap_service(worker_env)
    await bootstrap.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    embedding_provider = FakeArticleRagEmbeddingProvider()
    vector_writer = FakeArticleRagVectorWriter()
    service = _build_worker_service(
        worker_env,
        embedding_provider=embedding_provider,
        vector_writer=vector_writer,
    )

    result = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )
    assert result is not None
    assert result.status == "succeeded"

    # Fake providers recorded their calls (no network).
    assert embedding_provider.call_count == 1
    assert vector_writer.call_count == 1
    assert len(vector_writer.upserts) == 1

    # Verify the upsert payload has the correct metadata.
    collection, chunks, metadata = vector_writer.upserts[0]
    assert collection == DEFAULT_FAKE_VECTOR_COLLECTION
    assert len(chunks) == 1
    assert isinstance(chunks[0], ArticleRagVectorChunk)
    assert metadata.chunk_count == 1
    assert metadata.reading_record_id == _RECORD_ID
    assert metadata.stable_document_id == _STABLE_DOC_ID
    assert metadata.base_id == _BASE_ID

    # Unconfigured providers raise without network calls.
    unconfigured_emb = UnconfiguredArticleRagEmbeddingProvider()
    with pytest.raises(ArticleRagIndexWorkerError) as exc_info:
        await unconfigured_emb.embed_texts(["test"])
    assert exc_info.value.failure_code == "embedding_provider_unconfigured"
    assert exc_info.value.retryable is False

    unconfigured_writer = UnconfiguredArticleRagVectorWriter()
    with pytest.raises(ArticleRagIndexWorkerError) as exc_info:
        await unconfigured_writer.upsert_chunks(
            collection="test",
            chunks_with_embeddings=[],
            metadata=ArticleRagVectorWriteMetadata(
                collection="test",
                reading_record_id=_RECORD_ID,
                stable_document_id=_STABLE_DOC_ID,
                base_id=_BASE_ID,
                record_generation=1,
                index_version=DEFAULT_INDEX_VERSION,
                chunker_version="test",
                plan_content_sha256="a" * 64,
                chunk_count=0,
                profile_fingerprint=_P1D_V1_PROFILE_FINGERPRINT,
            ),
        )
    assert exc_info.value.failure_code == "vector_writer_unconfigured"
    assert exc_info.value.retryable is False


# ===================================================================
# Test 13: plan truth drift (inactive stable document) -> superseded
# ===================================================================


async def test_plan_truth_drift_inactive_stable_document_superseded(
    worker_env: asyncpg.Pool,
) -> None:
    """Requirement 13: when the stable document is deactivated between
    bootstrap and worker execution, the plan service raises
    ArticleRagIndexPlanError in Phase 2 and the worker supersedes the
    job + index_run."""
    await _seed_paragraph_environment(worker_env)
    bootstrap = _build_bootstrap_service(worker_env)
    bootstrap_result = await bootstrap.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    # Deactivate the stable document (claim-time fence does NOT check
    # stable documents, so the claim succeeds; Phase 2 plan service
    # detects the inactive document and raises ArticleRagIndexPlanError).
    async with worker_env.acquire() as conn:
        await conn.execute(
            "UPDATE stable_reading_documents SET status = 'superseded' "
            "WHERE id = $1",
            _STABLE_DOC_ID,
        )

    embedding_provider = FakeArticleRagEmbeddingProvider()
    vector_writer = FakeArticleRagVectorWriter()
    service = _build_worker_service(
        worker_env,
        embedding_provider=embedding_provider,
        vector_writer=vector_writer,
    )

    result = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )

    assert result is not None
    assert result.status == "superseded"
    assert result.failure_code == "plan_truth_drift"

    # Neither provider was called (plan reload failed in Phase 2).
    assert embedding_provider.call_count == 0
    assert vector_writer.call_count == 0

    async with worker_env.acquire() as conn:
        job = await _fetch_job(conn, job_id=bootstrap_result.job_id)
        assert job["status"] == "superseded"

        index_run = await _fetch_index_run(
            conn, index_run_id=bootstrap_result.index_run_id,
        )
        assert index_run["status"] == "superseded"
        assert index_run["completed_at"] is not None
        error_json = dict(index_run["error_json"])
        assert error_json["failure_code"] == "plan_truth_drift"


# ===================================================================
# Test 14: vector chunk payload excludes chunk text
# ===================================================================


async def test_vector_chunk_payload_excludes_chunk_text(
    worker_env: asyncpg.Pool,
) -> None:
    """Requirement 14: the ArticleRagVectorChunk payload that crosses
    into the vector writer MUST NOT contain chunk text.  Only hashes,
    citation metadata, and the embedding vector are allowed."""
    await _seed_paragraph_environment(worker_env)
    bootstrap = _build_bootstrap_service(worker_env)
    await bootstrap.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    embedding_provider = FakeArticleRagEmbeddingProvider()
    vector_writer = FakeArticleRagVectorWriter()
    service = _build_worker_service(
        worker_env,
        embedding_provider=embedding_provider,
        vector_writer=vector_writer,
    )

    result = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )
    assert result is not None
    assert result.status == "succeeded"

    # Inspect the vector chunk payload.
    assert len(vector_writer.upserts) == 1
    _, chunks, _ = vector_writer.upserts[0]
    assert len(chunks) == 1
    chunk = chunks[0]

    # The chunk has only these fields (no text field).
    assert hasattr(chunk, "chunk_id")
    assert hasattr(chunk, "content_sha256")
    assert hasattr(chunk, "embedding_text_sha256")
    assert hasattr(chunk, "embedding")
    assert hasattr(chunk, "citation")
    assert hasattr(chunk, "metadata")
    assert not hasattr(chunk, "text")
    assert not hasattr(chunk, "chunk_text")

    # Citation contains only truth-layer fields (no Plate/Slate/DOM).
    citation = chunk.citation
    expected_citation_keys = {
        "reading_record_id",
        "stable_document_id",
        "base_id",
        "record_generation",
        "block_ids",
        "unit_ids",
        "anchor_segment_ids",
        "canonical_text_start_utf16",
        "canonical_text_end_utf16",
    }
    assert set(citation.keys()) == expected_citation_keys

    # The embedding has the expected fake model + dim.
    assert chunk.embedding.model == DEFAULT_FAKE_EMBEDDING_MODEL
    assert chunk.embedding.dim == DEFAULT_FAKE_EMBEDDING_DIM
    assert len(chunk.embedding.vector) == DEFAULT_FAKE_EMBEDDING_DIM


# ===================================================================
# Test 15: index_run field mismatch -> failed_terminal
# ===================================================================


async def test_index_run_field_mismatch_fail_closed(
    worker_env: asyncpg.Pool,
) -> None:
    """Requirement 6 (extended): when the index_run fields (base_id,
    stable_document_id, record_generation, index_version, chunker_version)
    do not match the claim / input_json contract, the worker fails
    closed with failure_code=index_run_field_mismatch."""
    await _seed_paragraph_environment(worker_env)
    bootstrap = _build_bootstrap_service(worker_env)
    bootstrap_result = await bootstrap.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    # Corrupt the index_run: change chunker_version.
    async with worker_env.acquire() as conn:
        await conn.execute(
            "UPDATE reader_article_rag_index_runs SET chunker_version = 'bogus' "
            "WHERE id = $1",
            bootstrap_result.index_run_id,
        )

    service = _build_worker_service(
        worker_env,
        embedding_provider=FakeArticleRagEmbeddingProvider(),
        vector_writer=FakeArticleRagVectorWriter(),
    )
    result = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )

    assert result is not None
    assert result.status == "failed_terminal"
    assert result.failure_code == "index_run_field_mismatch"


# ===================================================================
# Test 16: input_json source mismatch -> failed_terminal
# ===================================================================


async def test_input_json_source_mismatch_failed_terminal(
    worker_env: asyncpg.Pool,
) -> None:
    """Requirement 6 (extended): when the job input_json has a wrong
    source tag, the worker fails closed with failure_code=input_json_invalid."""
    await _seed_paragraph_environment(worker_env)
    bootstrap = _build_bootstrap_service(worker_env)
    bootstrap_result = await bootstrap.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    # Corrupt the input_json source.
    async with worker_env.acquire() as conn:
        job = await _fetch_job(conn, job_id=bootstrap_result.job_id)
        input_json = dict(job["input_json"])
        input_json["source"] = "bogus_source"
        from app.database.json_compat import jsonb_param
        await conn.execute(
            "UPDATE reader_jobs SET input_json = $2::jsonb WHERE id = $1",
            bootstrap_result.job_id,
            jsonb_param(input_json),
        )

    service = _build_worker_service(
        worker_env,
        embedding_provider=FakeArticleRagEmbeddingProvider(),
        vector_writer=FakeArticleRagVectorWriter(),
    )
    result = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )

    assert result is not None
    assert result.status == "failed_terminal"
    assert result.failure_code == "input_json_invalid"


async def test_rag_failure_does_not_block_article_ready_or_mutate_truth_layer(
    worker_env: asyncpg.Pool,
) -> None:
    """P0-B non-blocking boundary: RAG failure must not touch the truth layer.

    After a terminal embedding failure:
    - reading_records.readiness_state stays 'article_ready'
    - reader_events count does not increase (no new representation event)
    - reading_bases / stable_document_blocks / reading_units / anchor segments
      are unchanged
    - only reader_jobs / reader_runs / reader_article_rag_index_runs /
      reader_job_events may change
    """
    await _seed_paragraph_environment(worker_env)
    bootstrap_result = await _build_bootstrap_service(worker_env).bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )
    async with worker_env.acquire() as conn:
        await conn.execute(
            "UPDATE reader_jobs SET max_attempts = 1 WHERE id = $1",
            bootstrap_result.job_id,
        )
        # Snapshot truth-layer state before failure.
        record_before = await conn.fetchrow(
            "SELECT readiness_state, product_state, lifecycle_status, "
            "generation, active_base_id FROM reading_records WHERE id = $1",
            _RECORD_ID,
        )
        base_before = await conn.fetchrow(
            "SELECT id, status, record_generation FROM reading_bases "
            "WHERE reading_record_id = $1 AND status = 'active'",
            _RECORD_ID,
        )
        blocks_before = await conn.fetchval(
            "SELECT count(*) FROM stable_document_blocks b "
            "JOIN stable_reading_documents d ON d.id = b.stable_document_id "
            "WHERE d.reading_record_id = $1",
            _RECORD_ID,
        )
        units_before = await conn.fetchval(
            "SELECT count(*) FROM reading_units WHERE reading_record_id = $1",
            _RECORD_ID,
        )
        anchors_before = await conn.fetchval(
            "SELECT count(*) FROM anchor_segments WHERE reading_record_id = $1",
            _RECORD_ID,
        )
        reader_events_before = await conn.fetchval(
            "SELECT count(*) FROM reader_events WHERE reading_record_id = $1",
            _RECORD_ID,
        )

    service = _build_worker_service(
        worker_env,
        embedding_provider=_RetryableEmbeddingProvider(),
        vector_writer=FakeArticleRagVectorWriter(),
    )
    result = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )
    assert result is not None
    assert result.status == "failed_terminal"

    async with worker_env.acquire() as conn:
        record_after = await conn.fetchrow(
            "SELECT readiness_state, product_state, lifecycle_status, "
            "generation, active_base_id FROM reading_records WHERE id = $1",
            _RECORD_ID,
        )
        base_after = await conn.fetchrow(
            "SELECT id, status, record_generation FROM reading_bases "
            "WHERE reading_record_id = $1 AND status = 'active'",
            _RECORD_ID,
        )
        blocks_after = await conn.fetchval(
            "SELECT count(*) FROM stable_document_blocks b "
            "JOIN stable_reading_documents d ON d.id = b.stable_document_id "
            "WHERE d.reading_record_id = $1",
            _RECORD_ID,
        )
        units_after = await conn.fetchval(
            "SELECT count(*) FROM reading_units WHERE reading_record_id = $1",
            _RECORD_ID,
        )
        anchors_after = await conn.fetchval(
            "SELECT count(*) FROM anchor_segments WHERE reading_record_id = $1",
            _RECORD_ID,
        )
        reader_events_after = await conn.fetchval(
            "SELECT count(*) FROM reader_events WHERE reading_record_id = $1",
            _RECORD_ID,
        )

    # Truth layer MUST be untouched by RAG failure.
    assert record_after["readiness_state"] == record_before["readiness_state"]
    assert record_after["readiness_state"] == "article_ready"
    assert record_after["product_state"] == record_before["product_state"]
    assert record_after["lifecycle_status"] == record_before["lifecycle_status"]
    assert record_after["generation"] == record_before["generation"]
    assert record_after["active_base_id"] == record_before["active_base_id"]
    assert base_after["id"] == base_before["id"]
    assert base_after["status"] == base_before["status"]
    assert base_after["record_generation"] == base_before["record_generation"]
    assert blocks_after == blocks_before
    assert units_after == units_before
    assert anchors_after == anchors_before
    # No new reader_events (no representation event published).
    assert reader_events_after == reader_events_before


# ---------------------------------------------------------------------------
# P0-B round 2: atomic terminal path + rollback injection + diagnostics
# ---------------------------------------------------------------------------


async def test_terminal_embedding_400_atomic_job_run_index_run_and_events(
    worker_env: asyncpg.Pool,
) -> None:
    """400/InvalidParameter terminal failure must atomically update all 3 tables.

    Asserts:
    - reader_job = failed_terminal
    - reader_run = failed
    - reader_article_rag_index_runs = failed
    - exactly 1 ``job_failed_terminal`` event
    - exactly 0 ``job_retry_later`` events
    - vector writer never called
    - error_json contains only approved safe fields (no key, text, URI, SDK)
    - article_ready + reader truth layer untouched
    """
    await _seed_paragraph_environment(worker_env)
    bootstrap_result = await _build_bootstrap_service(worker_env).bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    embedding_provider = _TerminalEmbeddingProvider()
    vector_writer = FakeArticleRagVectorWriter()
    service = _build_worker_service(
        worker_env,
        embedding_provider=embedding_provider,
        vector_writer=vector_writer,
    )

    async with worker_env.acquire() as conn:
        record_before = await conn.fetchrow(
            "SELECT readiness_state, active_base_id, generation FROM reading_records WHERE id = $1",
            _RECORD_ID,
        )
        events_before = await conn.fetchval(
            "SELECT count(*) FROM reader_events WHERE reading_record_id = $1",
            _RECORD_ID,
        )

    result = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )
    assert result is not None
    assert result.status == "failed_terminal"
    assert result.retryable is False

    assert embedding_provider.call_count == 1
    assert vector_writer.call_count == 0

    async with worker_env.acquire() as conn:
        job = await _fetch_job(conn, job_id=bootstrap_result.job_id)
        run = await _fetch_run(conn, run_id=job["run_id"])
        index_run = await _fetch_index_run(conn, index_run_id=bootstrap_result.index_run_id)
        terminal_events = await conn.fetchval(
            "SELECT count(*) FROM reader_job_events WHERE job_id = $1 "
            "AND event_type = 'job_failed_terminal'",
            bootstrap_result.job_id,
        )
        retry_events = await conn.fetchval(
            "SELECT count(*) FROM reader_job_events WHERE job_id = $1 "
            "AND event_type = 'job_retry_later'",
            bootstrap_result.job_id,
        )
        record_after = await conn.fetchrow(
            "SELECT readiness_state, active_base_id, generation FROM reading_records WHERE id = $1",
            _RECORD_ID,
        )
        events_after = await conn.fetchval(
            "SELECT count(*) FROM reader_events WHERE reading_record_id = $1",
            _RECORD_ID,
        )

    assert job["status"] == "failed_terminal"
    assert job["failure_class"] == "embedding"
    assert job["failure_code"] == "embedding_failed"
    assert run["status"] == "failed_terminal"
    assert run["failure_class"] == "embedding"
    assert run["failure_code"] == "embedding_failed"
    assert index_run["status"] == "failed"
    assert index_run["completed_at"] is not None
    assert terminal_events == 1
    assert retry_events == 0

    # error_json contains only safe approved fields — no key, text, URI, SDK.
    error_json = dict(index_run["error_json"])
    approved_keys = {
        "failure_class",
        "failure_code",
        "rationale_code",
        "message",
        "retryable",
    }
    assert set(error_json.keys()).issubset(approved_keys), (
        f"unexpected error_json keys: {set(error_json.keys()) - approved_keys}"
    )
    for val in error_json.values():
        assert val is None or isinstance(val, str | bool | int | float)
        if isinstance(val, str):
            lower = val.lower()
            assert "sk-" not in lower
            assert "http" not in lower
            assert "dashscope" not in lower

    # Non-blocking boundary: article_ready + reader_events untouched.
    assert record_after["readiness_state"] == record_before["readiness_state"]
    assert record_after["readiness_state"] == "article_ready"
    assert record_after["active_base_id"] == record_before["active_base_id"]
    assert record_after["generation"] == record_before["generation"]
    assert events_after == events_before


async def test_terminal_failure_rollback_when_index_run_update_fails(
    worker_env: asyncpg.Pool,
) -> None:
    """If the index-run update throws mid-transaction, the entire terminal path rolls back.

    Injects a failure by monkey-patching the worker's
    ``_update_index_run_status_in_transaction`` (via a custom vector writer
    that mutates the index run to an invalid state right before the terminal
    transition) — actually the cleanest injection is to make the
    ``_update_index_run_status`` raise after the job transition has been
    applied within the same caller-owned transaction.

    Asserts after the rolled-back attempt:
    - reader_job remains ``claimed`` (not failed_terminal)
    - reader_run remains ``running`` (not failed)
    - reader_article_rag_index_runs remains ``indexing`` (not failed)
    - 0 ``job_failed_terminal`` events
    - 0 ``job_retry_later`` events
    - article_ready + reader truth layer untouched
    """
    await _seed_paragraph_environment(worker_env)
    bootstrap_result = await _build_bootstrap_service(worker_env).bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    embedding_provider = _TerminalEmbeddingProvider()
    vector_writer = FakeArticleRagVectorWriter()
    service = _build_worker_service(
        worker_env,
        embedding_provider=embedding_provider,
        vector_writer=vector_writer,
    )

    # Move the job into 'claimed' + index_run into 'indexing' by running
    # process_next once with a happy embedding provider so the worker
    # reaches the post-embed phase. Then we reset the job to claimed and
    # re-run with the terminal provider + a patched index-run updater.
    # Simpler: capture the post-claim state directly by triggering a
    # terminal failure with a patched seam that raises AFTER the job
    # transition but BEFORE the index-run update commits.
    #
    # We patch ``_update_index_run_status_in_transaction`` on the instance
    # to raise an exception, simulating a DB-side failure (e.g. constraint
    # violation, connection drop). The caller-owned transaction must roll
    # back the job transition + terminal event too.
    original_update = service._update_index_run_status_in_transaction

    call_count = {"update": 0}

    async def _explode(
        conn,  # noqa: ANN001
        index_run_id: UUID,
        *,
        status: str,
        error_json: dict[str, Any],
    ) -> None:
        call_count["update"] += 1
        # Patched call must be invoked within the caller's transaction.
        # Verify we are inside one (proves atomicity refactor is in place).
        assert conn.is_in_transaction(), (
            "index-run update must run inside the caller-owned transaction"
        )
        raise RuntimeError("injected index-run update failure")

    service._update_index_run_status_in_transaction = _explode  # type: ignore[assignment]

    async with worker_env.acquire() as conn:
        record_before = await conn.fetchrow(
            "SELECT readiness_state, active_base_id, generation FROM reading_records WHERE id = $1",
            _RECORD_ID,
        )
        reader_events_before = await conn.fetchval(
            "SELECT count(*) FROM reader_events WHERE reading_record_id = $1",
            _RECORD_ID,
        )

    # The patched update will raise; process_next must propagate it.
    with pytest.raises(RuntimeError, match="injected index-run update failure"):
        await service.process_next(
            lease_owner=_LEASE_OWNER,
            lease_duration=_LEASE_DURATION,
            retry_delay=_RETRY_DELAY,
        )

    assert call_count["update"] == 1, "patched index-run update must be called exactly once"

    async with worker_env.acquire() as conn:
        job = await _fetch_job(conn, job_id=bootstrap_result.job_id)
        run = await _fetch_run(conn, run_id=job["run_id"])
        index_run = await _fetch_index_run(conn, index_run_id=bootstrap_result.index_run_id)
        terminal_events = await conn.fetchval(
            "SELECT count(*) FROM reader_job_events WHERE job_id = $1 "
            "AND event_type = 'job_failed_terminal'",
            bootstrap_result.job_id,
        )
        retry_events = await conn.fetchval(
            "SELECT count(*) FROM reader_job_events WHERE job_id = $1 "
            "AND event_type = 'job_retry_later'",
            bootstrap_result.job_id,
        )
        record_after = await conn.fetchrow(
            "SELECT readiness_state, active_base_id, generation FROM reading_records WHERE id = $1",
            _RECORD_ID,
        )
        reader_events_after = await conn.fetchval(
            "SELECT count(*) FROM reader_events WHERE reading_record_id = $1",
            _RECORD_ID,
        )

    # Rollback proof: job, run, index_run all unchanged from pre-terminal state.
    assert job["status"] == "claimed"
    assert run["status"] == "running"
    assert index_run["status"] == "indexing"
    assert terminal_events == 0
    assert retry_events == 0
    # Non-blocking boundary still holds.
    assert record_after["readiness_state"] == record_before["readiness_state"]
    assert record_after["readiness_state"] == "article_ready"
    assert reader_events_after == reader_events_before

    # Restore the original method (cleanup for any subsequent assertions).
    service._update_index_run_status_in_transaction = original_update  # type: ignore[assignment]


# ============================================================================
# P0 Combined Integration Gate: real DashScope provider + real worker + real DB
# ============================================================================


# Sensitive sentinels that the fake SDK response echoes into its `message`.
# None of these may appear in any persisted object (job failure_message,
# output_ref_json, index_run error_json, or job_event payload_json).
_SENTINEL_API_KEY = "sk-fake-api-key-sentinel-do-not-leak"
_SENTINEL_CHUNK_TEXT = "SENTINEL-CHUNK-TEXT-DO-NOT-LEAK"
_SENTINEL_URI = "https://fake-dashscope-uri-sentinel.do.not.leak"
_SENTINEL_UPSTREAM_ERROR = "raw upstream SDK error sentinel with api_key and uri"


async def test_dashscope_400_terminalizes_article_rag_job_once_with_safe_diagnostics(
    worker_env: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P0 combined integration gate: real DashScope provider + real worker + real DB.

    Mocks ONLY ``dashscope.TextEmbedding.call`` at the public external
    boundary — no worker private methods are mocked, no fake provider is
    used, no real network is called.

    Proves the end-to-end 400/InvalidParameter terminal path:
    1. SDK outbound batch ≤ 10 (v4 capability).
    2. ``reader_jobs.status = failed_terminal`` on first attempt
       (``max_attempts > 1`` — not retry-exhaustion terminal).
    3. ``reader_runs.status = failed_terminal``.
    4. ``reader_article_rag_index_runs.status = failed``.
    5. Exactly 1 ``job_failed_terminal`` event.
    6. 0 ``job_retry_later`` events.
    7. Vector writer call_count = 0.
    8. Second ``process_next()`` returns None; terminal event still 1.
    9. Safe diagnostics contain only approved fields.
    10-11. No sensitive sentinel in any persisted object.
    12-14. ``article_ready`` + reader truth layer untouched.
    """
    # --- Arrange: enable legacy Bailian config (no registry route) ---
    # The DashScope adapter delegates to ``bailian_embedding.embed_texts_with_metadata``
    # which calls ``resolve_embedding_config()`` → ``get_settings()``.  We set
    # ``BAILIAN_API_KEY`` so the legacy fallback resolves a non-empty key.
    # This is configuration, NOT mocking — the only mock is
    # ``dashscope.TextEmbedding.call``.
    from app.config import settings as settings_module

    settings_module.get_settings.cache_clear()
    monkeypatch.setenv("BAILIAN_API_KEY", _SENTINEL_API_KEY)
    monkeypatch.delenv("RAG_EMBEDDING_MODEL_PROFILE", raising=False)

    # --- Arrange: mock ONLY dashscope.TextEmbedding.call ---
    import dashscope

    sdk_batch_sizes: list[int] = []

    class _FakeDashScope400Response:
        """Simulates a DashScope SDK 400/InvalidParameter response.

        The ``message`` field is loaded with sensitive sentinels to prove
        defence-in-depth: even if the SDK echoes them, they must NOT
        appear in any persisted object.
        """

        status_code = 400
        code = "InvalidParameter"
        message = (
            f"api_key={_SENTINEL_API_KEY} chunk_text={_SENTINEL_CHUNK_TEXT} "
            f"uri={_SENTINEL_URI} upstream_error={_SENTINEL_UPSTREAM_ERROR}"
        )
        output: dict[str, Any] = {}
        request_id = "fake-request-id-sentinel"
        usage = {}

    def _fake_sdk_call(**kwargs: Any) -> _FakeDashScope400Response:
        # Record the batch size (number of input texts) per SDK call.
        input_texts = kwargs.get("input", [])
        sdk_batch_sizes.append(len(input_texts))
        return _FakeDashScope400Response()

    monkeypatch.setattr(dashscope.TextEmbedding, "call", _fake_sdk_call)

    # --- Arrange: seed environment + bootstrap job ---
    await _seed_paragraph_environment(worker_env)
    bootstrap_result = await _build_bootstrap_service(worker_env).bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    # Keep max_attempts > 1 to prove 400 is terminal on the FIRST attempt,
    # not because the retry budget was exhausted.
    async with worker_env.acquire() as conn:
        await conn.execute(
            "UPDATE reader_jobs SET max_attempts = 3 WHERE id = $1",
            bootstrap_result.job_id,
        )

        # Snapshot truth layer + reader events before failure.
        record_before = await conn.fetchrow(
            "SELECT readiness_state, product_state, lifecycle_status, "
            "generation, active_base_id FROM reading_records WHERE id = $1",
            _RECORD_ID,
        )
        base_before = await conn.fetchrow(
            "SELECT id, status, record_generation FROM reading_bases "
            "WHERE reading_record_id = $1 AND status = 'active'",
            _RECORD_ID,
        )
        blocks_before = await conn.fetchval(
            "SELECT count(*) FROM stable_document_blocks b "
            "JOIN stable_reading_documents d ON d.id = b.stable_document_id "
            "WHERE d.reading_record_id = $1",
            _RECORD_ID,
        )
        units_before = await conn.fetchval(
            "SELECT count(*) FROM reading_units WHERE reading_record_id = $1",
            _RECORD_ID,
        )
        anchors_before = await conn.fetchval(
            "SELECT count(*) FROM anchor_segments WHERE reading_record_id = $1",
            _RECORD_ID,
        )
        reader_events_before = await conn.fetchval(
            "SELECT count(*) FROM reader_events WHERE reading_record_id = $1",
            _RECORD_ID,
        )

    # --- Act: use real DashScopeArticleRagEmbeddingProvider ---
    embedding_provider = DashScopeArticleRagEmbeddingProvider(
        model_override="text-embedding-v4",
    )
    vector_writer = FakeArticleRagVectorWriter()
    service = _build_worker_service(
        worker_env,
        embedding_provider=embedding_provider,
        vector_writer=vector_writer,
    )

    result = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )

    # --- Assert: 400 terminalized on first attempt ---
    assert result is not None
    assert result.status == "failed_terminal"
    assert result.retryable is False

    # 1. SDK outbound batch ≤ 10 (v4 capability).
    assert len(sdk_batch_sizes) >= 1, "SDK must have been called at least once"
    assert all(n <= 10 for n in sdk_batch_sizes), (
        f"SDK batch size exceeds v4 capability 10: {sdk_batch_sizes}"
    )

    # 7. Vector writer never called.
    assert vector_writer.call_count == 0

    # --- Assert: job / run / index-run / event state ---
    async with worker_env.acquire() as conn:
        job = await _fetch_job(conn, job_id=bootstrap_result.job_id)
        run = await _fetch_run(conn, run_id=job["run_id"])
        index_run = await _fetch_index_run(
            conn, index_run_id=bootstrap_result.index_run_id,
        )
        terminal_events = await conn.fetchval(
            "SELECT count(*) FROM reader_job_events WHERE job_id = $1 "
            "AND event_type = 'job_failed_terminal'",
            bootstrap_result.job_id,
        )
        retry_events = await conn.fetchval(
            "SELECT count(*) FROM reader_job_events WHERE job_id = $1 "
            "AND event_type = 'job_retry_later'",
            bootstrap_result.job_id,
        )
        all_events = await conn.fetch(
            "SELECT event_type, payload_json FROM reader_job_events "
            "WHERE job_id = $1",
            bootstrap_result.job_id,
        )

    # 2. reader_jobs.status = failed_terminal
    assert job["status"] == "failed_terminal"
    assert job["failure_class"] == "embedding"
    assert job["failure_code"] == "embedding_backend_failed"
    assert job["attempt_count"] == 1, "must be terminal on first attempt"

    # 3. reader_runs.status = failed_terminal
    assert run["status"] == "failed_terminal"
    assert run["failure_class"] == "embedding"
    assert run["failure_code"] == "embedding_backend_failed"

    # 4. reader_article_rag_index_runs.status = failed
    assert index_run["status"] == "failed"
    assert index_run["completed_at"] is not None

    # 5. Exactly 1 job_failed_terminal event.
    assert terminal_events == 1

    # 6. 0 job_retry_later events.
    assert retry_events == 0

    # --- Assert: safe diagnostics (item 9) ---
    error_json = dict(index_run["error_json"])
    diagnostics = error_json.get("diagnostics", {})
    assert diagnostics.get("provider_status") == 400
    assert diagnostics.get("provider_code") == "InvalidParameter"
    assert diagnostics.get("provider_retryable") is False
    assert isinstance(diagnostics.get("failed_batch_ordinal"), int)
    assert diagnostics["failed_batch_ordinal"] > 0
    assert isinstance(diagnostics.get("batch_count"), int)
    assert diagnostics["batch_count"] > 0

    # --- Assert: no sensitive sentinel in any persisted object (items 10-11) ---
    sentinels = [
        _SENTINEL_API_KEY,
        _SENTINEL_CHUNK_TEXT,
        _SENTINEL_URI,
        _SENTINEL_UPSTREAM_ERROR,
    ]

    # 10a. reader_jobs.failure_message
    failure_message = str(job["failure_message"] or "")
    for s in sentinels:
        assert s not in failure_message, (
            f"sentinel {s!r} leaked into reader_jobs.failure_message"
        )

    # 10b. reader_jobs.output_ref_json
    output_ref = job["output_ref_json"]
    output_ref_str = json.dumps(output_ref) if output_ref else ""
    for s in sentinels:
        assert s not in output_ref_str, (
            f"sentinel {s!r} leaked into reader_jobs.output_ref_json"
        )

    # 10c. reader_article_rag_index_runs.error_json
    error_json_str = json.dumps(error_json)
    for s in sentinels:
        assert s not in error_json_str, (
            f"sentinel {s!r} leaked into reader_article_rag_index_runs.error_json"
        )

    # 10d. reader_job_events.payload_json (all events)
    for ev in all_events:
        ev_str = json.dumps(ev["payload_json"]) if ev["payload_json"] else ""
        for s in sentinels:
            assert s not in ev_str, (
                f"sentinel {s!r} leaked into reader_job_events.payload_json "
                f"(event_type={ev['event_type']})"
            )

    # --- Assert: second process_next() returns None, terminal event still 1 (item 8) ---
    result2 = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )
    assert result2 is None, "second process_next must find no claimable job"

    async with worker_env.acquire() as conn:
        terminal_events_after_second = await conn.fetchval(
            "SELECT count(*) FROM reader_job_events WHERE job_id = $1 "
            "AND event_type = 'job_failed_terminal'",
            bootstrap_result.job_id,
        )
        # Duplicate processing must not produce a second terminal event.
        assert terminal_events_after_second == 1

    # --- Assert: non-blocking boundary (items 12-14) ---
    async with worker_env.acquire() as conn:
        record_after = await conn.fetchrow(
            "SELECT readiness_state, product_state, lifecycle_status, "
            "generation, active_base_id FROM reading_records WHERE id = $1",
            _RECORD_ID,
        )
        base_after = await conn.fetchrow(
            "SELECT id, status, record_generation FROM reading_bases "
            "WHERE reading_record_id = $1 AND status = 'active'",
            _RECORD_ID,
        )
        blocks_after = await conn.fetchval(
            "SELECT count(*) FROM stable_document_blocks b "
            "JOIN stable_reading_documents d ON d.id = b.stable_document_id "
            "WHERE d.reading_record_id = $1",
            _RECORD_ID,
        )
        units_after = await conn.fetchval(
            "SELECT count(*) FROM reading_units WHERE reading_record_id = $1",
            _RECORD_ID,
        )
        anchors_after = await conn.fetchval(
            "SELECT count(*) FROM anchor_segments WHERE reading_record_id = $1",
            _RECORD_ID,
        )
        reader_events_after = await conn.fetchval(
            "SELECT count(*) FROM reader_events WHERE reading_record_id = $1",
            _RECORD_ID,
        )

    # 12. reading_records.readiness_state still article_ready.
    assert record_after["readiness_state"] == "article_ready"
    assert record_after["readiness_state"] == record_before["readiness_state"]
    assert record_after["product_state"] == record_before["product_state"]
    assert record_after["lifecycle_status"] == record_before["lifecycle_status"]
    assert record_after["generation"] == record_before["generation"]
    assert record_after["active_base_id"] == record_before["active_base_id"]

    # 13. reader_events count unchanged.
    assert reader_events_after == reader_events_before

    # 14. base / stable document / Unit / anchor identity + count unchanged.
    assert base_after["id"] == base_before["id"]
    assert base_after["status"] == base_before["status"]
    assert base_after["record_generation"] == base_before["record_generation"]
    assert blocks_after == blocks_before
    assert units_after == units_before
    assert anchors_after == anchors_before

    # Cleanup: clear settings cache so env vars don't leak to other tests.
    settings_module.get_settings.cache_clear()


# ===========================================================================
# P1-D: Worker Frozen Profile Validation (TDD)
# ===========================================================================
#
# The Article RAG worker must validate the bootstrap-frozen profile
# identity (index_version, profile_fingerprint, chunker_version,
# reader_jobs.input_hash, reader_article_rag_index_runs.profile_fingerprint)
# before any embedding or vector-write side effect.
#
# All failure paths must:
#   - fail-closed (failed_terminal)
#   - not call embedding provider
#   - not call vector writer
#   - terminalize job + run + linked index-run atomically
#   - emit exactly one terminal event
#   - not echo malicious input in any error surface
#
# Hash golden values are computed INDEPENDENTLY in the test (not via the
# production public seam) to provide genuine cross-validation.
#
# Frozen V1 profile identity (must match the P1-B resolver golden and
# the migration 0021 backfill literal).  Tests reference this constant
# instead of importing from production to keep cross-validation
# independent of the very resolver under test.
_P1D_V1_PROFILE_FINGERPRINT = (
    "e443f581eb3e86aeb9dbcdcee806783186bd85da6c987c60357b61905ea86d6d"
)
_P1D_V1_INDEX_VERSION = "article_rag_index_v1"
_P1D_V1_CHUNKER_VERSION = "article_rag_index_plan_v1"

# Sentinel strings used to verify that malicious input is not echoed in
# any error surface (str/repr/traceback/failure_message/output_ref_json/
# error_json/payload_json).
_P1D_SENTINEL_FINGERPRINT = "sk-P1D-FINGERPRINT-SENTINEL-LEAK-1234567890abcdef"
_P1D_SENTINEL_INDEX_VERSION = "sk-P1D-INDEX-VERSION-SENTINEL-LEAK-0987654321"
_P1D_SENTINEL_INPUT_HASH = "sk-P1D-INPUT-HASH-SENTINEL-LEAK-abcdef1234567890"

# Fixed safe error messages used by the production failure paths.  These
# are the only strings tests assert against when checking that the
# sentinel is NOT present — they are intentionally generic and free of
# any caller-supplied value.
_P1D_SAFE_MESSAGES = {
    "Article RAG index profile is not registered",
    "Article RAG index profile fingerprint is missing or malformed",
    "Article RAG index profile fingerprint does not match the resolved profile",
    "Article RAG index plan chunker_version does not match the resolved profile",
    "Article RAG index job input_hash does not match the canonical algorithm",
    "Article RAG index run profile_fingerprint does not match the resolved profile",
    "Article RAG index run profile_fingerprint drifted before vector write",
}


def _p1d_compute_expected_input_hash(
    *,
    stable_document_id: UUID,
    base_id: UUID,
    plan_content_sha256: str,
    index_version: str,
    profile_fingerprint: str,
) -> str:
    """Independent computation of the canonical P1-C input_hash.

    Mirrors the production algorithm but is intentionally duplicated
    here so tests provide genuine cross-validation rather than invoking
    the public seam.  If the production algorithm drifts, this
    independent computation will mismatch and the test will fail —
    which is the intended regression signal.
    """
    raw = (
        f"{stable_document_id}:"
        f"{base_id}:"
        f"{plan_content_sha256}:"
        f"{index_version}:"
        f"{profile_fingerprint}"
    ).encode()
    return hashlib.sha256(raw).hexdigest()


async def _p1d_fetch_index_run_with_fingerprint(
    conn: asyncpg.Connection,
    *,
    index_run_id: UUID,
) -> asyncpg.Record:
    """Fetch index_run row including profile_fingerprint (P1-D)."""
    return await conn.fetchrow(
        """
        SELECT id, status, job_id, base_id, stable_document_id,
               record_generation, index_version, chunker_version,
               profile_fingerprint,
               plan_content_sha256, chunk_count,
               embedding_model, vector_store_provider, vector_collection,
               reader_run_id, error_json, metadata_json, completed_at
        FROM reader_article_rag_index_runs
        WHERE id = $1
        """,
        index_run_id,
    )


async def _p1d_count_terminal_events(
    conn: asyncpg.Connection,
    *,
    job_id: UUID,
) -> int:
    return await conn.fetchval(
        "SELECT count(*) FROM reader_job_events WHERE job_id = $1 "
        "AND event_type = 'job_failed_terminal'",
        job_id,
    )


async def _p1d_count_retry_events(
    conn: asyncpg.Connection,
    *,
    job_id: UUID,
) -> int:
    return await conn.fetchval(
        "SELECT count(*) FROM reader_job_events WHERE job_id = $1 "
        "AND event_type = 'job_retry_later'",
        job_id,
    )


async def _p1d_set_job_input_json(
    pool: asyncpg.Pool,
    *,
    job_id: UUID,
    input_json: dict[str, Any],
) -> None:
    from app.database.json_compat import jsonb_param
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE reader_jobs SET input_json = $2::jsonb WHERE id = $1",
            job_id,
            jsonb_param(input_json),
        )


async def _p1d_set_job_input_hash(
    pool: asyncpg.Pool,
    *,
    job_id: UUID,
    input_hash: str,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE reader_jobs SET input_hash = $2 WHERE id = $1",
            job_id,
            input_hash,
        )


async def _p1d_set_index_run_fingerprint(
    pool: asyncpg.Pool,
    *,
    index_run_id: UUID,
    fingerprint: str,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE reader_article_rag_index_runs "
            "SET profile_fingerprint = $2 WHERE id = $1",
            index_run_id,
            fingerprint,
        )


async def _p1d_fetch_job_payload(
    pool: asyncpg.Pool,
    *,
    job_id: UUID,
) -> dict[str, Any]:
    """Fetch the job's input_json as a mutable dict (P1-D helper)."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT input_json, input_hash FROM reader_jobs WHERE id = $1",
            job_id,
        )
    return {
        "input_json": dict(row["input_json"]),
        "input_hash": str(row["input_hash"]),
    }


def _p1d_assert_no_sentinel_in_surfaces(
    *,
    sentinel: str,
    job: asyncpg.Record | None = None,
    index_run: asyncpg.Record | None = None,
    events: list[asyncpg.Record] | None = None,
    exc: Exception | None = None,
) -> None:
    """Verify a malicious sentinel string is not echoed in any error surface.

    Surfaces checked:
      - str(exc) / repr(exc) / traceback.format_exception(exc)
      - reader_jobs.failure_message
      - reader_jobs.output_ref_json
      - reader_article_rag_index_runs.error_json
      - reader_job_events.payload_json (per event)
    """
    surfaces: list[str] = []

    if exc is not None:
        surfaces.append(str(exc))
        surfaces.append(repr(exc))
        import traceback as _tb
        surfaces.extend(
            _tb.format_exception(type(exc), exc, exc.__traceback__)
        )

    if job is not None:
        fm = job.get("failure_message")
        if isinstance(fm, str):
            surfaces.append(fm)
        orj = job.get("output_ref_json")
        if orj is not None:
            surfaces.append(json.dumps(orj, default=str))

    if index_run is not None:
        ej = index_run.get("error_json")
        if ej is not None:
            surfaces.append(json.dumps(ej, default=str))

    if events:
        for ev in events:
            pj = ev.get("payload_json")
            if pj is not None:
                surfaces.append(json.dumps(pj, default=str))

    for surface in surfaces:
        assert sentinel not in surface, (
            f"sentinel {sentinel!r} leaked into error surface: "
            f"{surface[:200]!r}"
        )


# ===================================================================
# P1-D Scenario 1: Happy path — valid P1-C job succeeds end-to-end
# ===================================================================


async def test_p1d_happy_path_p1c_job_succeeds_with_profile_validation(
    worker_env: asyncpg.Pool,
) -> None:
    """P1-D Scenario 1: a valid P1-C bootstrap job must succeed end-to-end.

    Asserts the bootstrap froze the correct profile identity across all
    three layers (index-run column, input_json, input_hash) and the
    worker succeeds without any profile validation failure.

    Embedding provider and vector writer must each be called exactly
    once; index-run indexed; job succeeded; reader run completed.
    """
    from app.services.reader_orchestration.article_rag_index_profile import (
        resolve_article_rag_index_profile,
    )

    await _seed_paragraph_environment(worker_env)
    bootstrap = _build_bootstrap_service(worker_env)
    bootstrap_result = await bootstrap.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    # Verify the bootstrap froze the correct profile identity.
    resolution = resolve_article_rag_index_profile(_P1D_V1_INDEX_VERSION)
    assert bootstrap_result.profile_fingerprint == resolution.profile_fingerprint
    assert bootstrap_result.profile_fingerprint == _P1D_V1_PROFILE_FINGERPRINT

    # Verify the input_hash matches the independent P1-C computation.
    payload = await _p1d_fetch_job_payload(
        worker_env, job_id=bootstrap_result.job_id,
    )
    expected_hash = _p1d_compute_expected_input_hash(
        stable_document_id=bootstrap_result.stable_document_id,
        base_id=bootstrap_result.base_id,
        plan_content_sha256=bootstrap_result.plan_content_sha256,
        index_version=_P1D_V1_INDEX_VERSION,
        profile_fingerprint=_P1D_V1_PROFILE_FINGERPRINT,
    )
    assert payload["input_hash"] == expected_hash
    assert payload["input_json"]["profile_fingerprint"] == _P1D_V1_PROFILE_FINGERPRINT

    embedding_provider = FakeArticleRagEmbeddingProvider()
    vector_writer = FakeArticleRagVectorWriter()
    service = _build_worker_service(
        worker_env,
        embedding_provider=embedding_provider,
        vector_writer=vector_writer,
    )

    result = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )

    assert result is not None
    assert result.status == "succeeded"
    assert embedding_provider.call_count == 1
    assert vector_writer.call_count == 1

    async with worker_env.acquire() as conn:
        job = await _fetch_job(conn, job_id=bootstrap_result.job_id)
        run = await _fetch_run(conn, run_id=job["run_id"])
        index_run = await _p1d_fetch_index_run_with_fingerprint(
            conn, index_run_id=bootstrap_result.index_run_id,
        )

    assert job["status"] == "succeeded"
    assert run["status"] == "completed"
    assert index_run["status"] == "indexed"
    assert index_run["profile_fingerprint"] == _P1D_V1_PROFILE_FINGERPRINT


# ===================================================================
# P1-D Scenario 2: input_json.profile_fingerprint missing
# ===================================================================


async def test_p1d_missing_profile_fingerprint_failed_terminal(
    worker_env: asyncpg.Pool,
) -> None:
    """P1-D Scenario 2: input_json.profile_fingerprint missing -> failed_terminal.

    Asserts:
      - result.status == "failed_terminal"
      - failure_code in the index_profile_invalid family
      - embedding provider 0 calls
      - vector writer 0 calls
      - job failed_terminal
      - run failed_terminal
      - linked index-run failed (via trusted job_id lookup, not input_json)
      - exactly 1 terminal event
      - 0 retry_later events
    """
    await _seed_paragraph_environment(worker_env)
    bootstrap_result = await _build_bootstrap_service(worker_env).bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    # Remove profile_fingerprint from input_json.
    payload = await _p1d_fetch_job_payload(
        worker_env, job_id=bootstrap_result.job_id,
    )
    input_json = payload["input_json"]
    input_json.pop("profile_fingerprint", None)
    await _p1d_set_job_input_json(
        worker_env, job_id=bootstrap_result.job_id, input_json=input_json,
    )

    embedding_provider = FakeArticleRagEmbeddingProvider()
    vector_writer = FakeArticleRagVectorWriter()
    service = _build_worker_service(
        worker_env,
        embedding_provider=embedding_provider,
        vector_writer=vector_writer,
    )

    result = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )

    assert result is not None
    assert result.status == "failed_terminal"
    assert result.failure_code == "index_profile_invalid"
    assert result.retryable is False
    assert embedding_provider.call_count == 0
    assert vector_writer.call_count == 0

    async with worker_env.acquire() as conn:
        job = await _fetch_job(conn, job_id=bootstrap_result.job_id)
        run = await _fetch_run(conn, run_id=job["run_id"])
        index_run = await _p1d_fetch_index_run_with_fingerprint(
            conn, index_run_id=bootstrap_result.index_run_id,
        )
        terminal_events = await _p1d_count_terminal_events(
            conn, job_id=bootstrap_result.job_id,
        )
        retry_events = await _p1d_count_retry_events(
            conn, job_id=bootstrap_result.job_id,
        )

    assert job["status"] == "failed_terminal"
    assert run["status"] == "failed_terminal"
    assert index_run["status"] == "failed"
    assert terminal_events == 1
    assert retry_events == 0


# ===================================================================
# P1-D Scenario 3: profile_fingerprint malformed matrix
# ===================================================================


_P1D_MALFORMED_FINGERPRINT_CASES = [
    pytest.param(None, id="none"),
    pytest.param("", id="empty"),
    pytest.param("   ", id="whitespace_only"),
    pytest.param("a" * 63, id="too_short_63_chars"),
    pytest.param("a" * 65, id="too_long_65_chars"),
    pytest.param("A" * 64, id="uppercase_sha256"),
    pytest.param("z" * 64, id="non_hex_sha256"),
    pytest.param("key-like-string-value", id="key_like"),
    pytest.param("http://example.com/fingerprint", id="uri"),
    pytest.param("finger" + " " + "a" * 57, id="contains_space"),
    pytest.param("finger\nprint" + "a" * 53, id="contains_newline"),
    pytest.param("UNICODE_FINGERPRINT_Ñ_é", id="unicode"),
    pytest.param(True, id="bool"),
    pytest.param(12345, id="int"),
    pytest.param("sk-P1D-FINGERPRINT-SENTINEL", id="raw_error_sentinel"),
]


@pytest.mark.parametrize("malformed_fingerprint", _P1D_MALFORMED_FINGERPRINT_CASES)
async def test_p1d_malformed_profile_fingerprint_matrix(
    worker_env: asyncpg.Pool,
    malformed_fingerprint: Any,
) -> None:
    """P1-D Scenario 3: malformed profile_fingerprint must fail-closed.

    Covers: None, empty, whitespace, bool, int, 63/65 chars, uppercase,
    non-hex, key-like, URI, Unicode, newline, sentinel.

    All cases must:
      - fail_terminal
      - 0 embedding calls
      - 0 vector writer calls
      - terminalize job + run + linked index-run
      - 1 terminal event, 0 retry events
      - not echo the malformed input in any error surface
    """
    await _seed_paragraph_environment(worker_env)
    bootstrap_result = await _build_bootstrap_service(worker_env).bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    payload = await _p1d_fetch_job_payload(
        worker_env, job_id=bootstrap_result.job_id,
    )
    input_json = payload["input_json"]
    input_json["profile_fingerprint"] = malformed_fingerprint
    await _p1d_set_job_input_json(
        worker_env, job_id=bootstrap_result.job_id, input_json=input_json,
    )

    embedding_provider = FakeArticleRagEmbeddingProvider()
    vector_writer = FakeArticleRagVectorWriter()
    service = _build_worker_service(
        worker_env,
        embedding_provider=embedding_provider,
        vector_writer=vector_writer,
    )

    result = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )

    assert result is not None
    assert result.status == "failed_terminal"
    assert result.retryable is False
    assert embedding_provider.call_count == 0
    assert vector_writer.call_count == 0

    async with worker_env.acquire() as conn:
        job = await _fetch_job(conn, job_id=bootstrap_result.job_id)
        run = await _fetch_run(conn, run_id=job["run_id"])
        index_run = await _p1d_fetch_index_run_with_fingerprint(
            conn, index_run_id=bootstrap_result.index_run_id,
        )
        terminal_events = await _p1d_count_terminal_events(
            conn, job_id=bootstrap_result.job_id,
        )
        retry_events = await _p1d_count_retry_events(
            conn, job_id=bootstrap_result.job_id,
        )
        events = await conn.fetch(
            "SELECT event_type, payload_json FROM reader_job_events "
            "WHERE job_id = $1",
            bootstrap_result.job_id,
        )

    assert job["status"] == "failed_terminal"
    assert run["status"] == "failed_terminal"
    assert index_run["status"] == "failed"
    assert terminal_events == 1
    assert retry_events == 0

    # Verify the malformed value is not echoed in any error surface.
    sentinel_repr = repr(malformed_fingerprint)
    if isinstance(malformed_fingerprint, str):
        sentinel_str = malformed_fingerprint
    else:
        sentinel_str = sentinel_repr
    # Only check non-empty sentinels (empty/None cannot leak meaningfully).
    if sentinel_str and sentinel_str.strip():
        _p1d_assert_no_sentinel_in_surfaces(
            sentinel=sentinel_str,
            job=job,
            index_run=index_run,
            events=events,
        )


# ===================================================================
# P1-D Scenario 4: unknown/malicious index_version
# ===================================================================


async def test_p1d_unknown_index_version_failed_terminal(
    worker_env: asyncpg.Pool,
) -> None:
    """P1-D Scenario 4: unknown index_version must fail-closed via resolver.

    The worker must NOT:
      - fallback to V1
      - use a runtime default
      - directly trust input_json
      - echo the malicious version

    The worker must use the P1-B public resolver and fail if the
    version is not registered.
    """
    await _seed_paragraph_environment(worker_env)
    bootstrap_result = await _build_bootstrap_service(worker_env).bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    payload = await _p1d_fetch_job_payload(
        worker_env, job_id=bootstrap_result.job_id,
    )
    input_json = payload["input_json"]
    input_json["index_version"] = "article_rag_index_v999_unknown"
    await _p1d_set_job_input_json(
        worker_env, job_id=bootstrap_result.job_id, input_json=input_json,
    )

    embedding_provider = FakeArticleRagEmbeddingProvider()
    vector_writer = FakeArticleRagVectorWriter()
    service = _build_worker_service(
        worker_env,
        embedding_provider=embedding_provider,
        vector_writer=vector_writer,
    )

    result = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )

    assert result is not None
    assert result.status == "failed_terminal"
    assert result.failure_code == "index_profile_invalid"
    assert embedding_provider.call_count == 0
    assert vector_writer.call_count == 0

    async with worker_env.acquire() as conn:
        job = await _fetch_job(conn, job_id=bootstrap_result.job_id)
        run = await _fetch_run(conn, run_id=job["run_id"])
        index_run = await _p1d_fetch_index_run_with_fingerprint(
            conn, index_run_id=bootstrap_result.index_run_id,
        )
        events = await conn.fetch(
            "SELECT event_type, payload_json FROM reader_job_events "
            "WHERE job_id = $1",
            bootstrap_result.job_id,
        )

    assert job["status"] == "failed_terminal"
    assert run["status"] == "failed_terminal"
    assert index_run["status"] == "failed"

    # Verify the malicious version is not echoed.
    _p1d_assert_no_sentinel_in_surfaces(
        sentinel="article_rag_index_v999_unknown",
        job=job,
        index_run=index_run,
        events=events,
    )


async def test_p1d_malicious_index_version_sentinel_not_echoed(
    worker_env: asyncpg.Pool,
) -> None:
    """P1-D Scenario 4b: malicious index_version sentinel must not leak."""
    await _seed_paragraph_environment(worker_env)
    bootstrap_result = await _build_bootstrap_service(worker_env).bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    payload = await _p1d_fetch_job_payload(
        worker_env, job_id=bootstrap_result.job_id,
    )
    input_json = payload["input_json"]
    input_json["index_version"] = _P1D_SENTINEL_INDEX_VERSION
    await _p1d_set_job_input_json(
        worker_env, job_id=bootstrap_result.job_id, input_json=input_json,
    )

    embedding_provider = FakeArticleRagEmbeddingProvider()
    vector_writer = FakeArticleRagVectorWriter()
    service = _build_worker_service(
        worker_env,
        embedding_provider=embedding_provider,
        vector_writer=vector_writer,
    )

    result = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )

    assert result is not None
    assert result.status == "failed_terminal"
    assert embedding_provider.call_count == 0
    assert vector_writer.call_count == 0

    async with worker_env.acquire() as conn:
        job = await _fetch_job(conn, job_id=bootstrap_result.job_id)
        run = await _fetch_run(conn, run_id=job["run_id"])
        index_run = await _p1d_fetch_index_run_with_fingerprint(
            conn, index_run_id=bootstrap_result.index_run_id,
        )
        events = await conn.fetch(
            "SELECT event_type, payload_json FROM reader_job_events "
            "WHERE job_id = $1",
            bootstrap_result.job_id,
        )

    assert job["status"] == "failed_terminal"
    assert run["status"] == "failed_terminal"
    assert index_run["status"] == "failed"

    _p1d_assert_no_sentinel_in_surfaces(
        sentinel=_P1D_SENTINEL_INDEX_VERSION,
        job=job,
        index_run=index_run,
        events=events,
    )


# ===================================================================
# P1-D Scenario 5: fingerprint mismatch with resolver
# ===================================================================


@pytest.mark.parametrize(
    "wrong_fingerprint",
    [
        pytest.param("a" * 64, id="all_a"),
        pytest.param("b" * 64, id="all_b"),
        pytest.param("0" * 64, id="all_zeros"),
        pytest.param("f" * 64, id="all_f"),
    ],
)
async def test_p1d_fingerprint_mismatch_with_resolver_failed_terminal(
    worker_env: asyncpg.Pool,
    wrong_fingerprint: str,
) -> None:
    """P1-D Scenario 5: a format-valid but wrong fingerprint must fail.

    The worker must not only check the shape (64-char hex) — it must
    verify the fingerprint precisely equals the resolver's
    profile_fingerprint for the resolved index_version.
    """
    await _seed_paragraph_environment(worker_env)
    bootstrap_result = await _build_bootstrap_service(worker_env).bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    payload = await _p1d_fetch_job_payload(
        worker_env, job_id=bootstrap_result.job_id,
    )
    input_json = payload["input_json"]
    input_json["profile_fingerprint"] = wrong_fingerprint
    await _p1d_set_job_input_json(
        worker_env, job_id=bootstrap_result.job_id, input_json=input_json,
    )

    embedding_provider = FakeArticleRagEmbeddingProvider()
    vector_writer = FakeArticleRagVectorWriter()
    service = _build_worker_service(
        worker_env,
        embedding_provider=embedding_provider,
        vector_writer=vector_writer,
    )

    result = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )

    assert result is not None
    assert result.status == "failed_terminal"
    assert result.failure_code == "index_profile_mismatch"
    assert embedding_provider.call_count == 0
    assert vector_writer.call_count == 0

    async with worker_env.acquire() as conn:
        job = await _fetch_job(conn, job_id=bootstrap_result.job_id)
        run = await _fetch_run(conn, run_id=job["run_id"])
        index_run = await _p1d_fetch_index_run_with_fingerprint(
            conn, index_run_id=bootstrap_result.index_run_id,
        )
        terminal_events = await _p1d_count_terminal_events(
            conn, job_id=bootstrap_result.job_id,
        )
        retry_events = await _p1d_count_retry_events(
            conn, job_id=bootstrap_result.job_id,
        )

    assert job["status"] == "failed_terminal"
    assert run["status"] == "failed_terminal"
    assert index_run["status"] == "failed"
    assert terminal_events == 1
    assert retry_events == 0


# ===================================================================
# P1-D Scenario 6: chunker_version mismatch with resolved profile
# ===================================================================


async def test_p1d_chunker_version_mismatch_failed_terminal(
    worker_env: asyncpg.Pool,
) -> None:
    """P1-D Scenario 6: chunker_version != resolved profile.chunker_version.

    A valid fingerprint but wrong chunker_version must fail-closed
    with a fixed safe error message and no provider side effects.
    """
    await _seed_paragraph_environment(worker_env)
    bootstrap_result = await _build_bootstrap_service(worker_env).bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    payload = await _p1d_fetch_job_payload(
        worker_env, job_id=bootstrap_result.job_id,
    )
    input_json = payload["input_json"]
    input_json["chunker_version"] = "article_rag_index_plan_v999_mismatch"
    await _p1d_set_job_input_json(
        worker_env, job_id=bootstrap_result.job_id, input_json=input_json,
    )

    embedding_provider = FakeArticleRagEmbeddingProvider()
    vector_writer = FakeArticleRagVectorWriter()
    service = _build_worker_service(
        worker_env,
        embedding_provider=embedding_provider,
        vector_writer=vector_writer,
    )

    result = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )

    assert result is not None
    assert result.status == "failed_terminal"
    assert result.failure_code == "index_profile_invalid"
    assert embedding_provider.call_count == 0
    assert vector_writer.call_count == 0

    async with worker_env.acquire() as conn:
        job = await _fetch_job(conn, job_id=bootstrap_result.job_id)
        run = await _fetch_run(conn, run_id=job["run_id"])
        index_run = await _p1d_fetch_index_run_with_fingerprint(
            conn, index_run_id=bootstrap_result.index_run_id,
        )
        terminal_events = await _p1d_count_terminal_events(
            conn, job_id=bootstrap_result.job_id,
        )

    assert job["status"] == "failed_terminal"
    assert run["status"] == "failed_terminal"
    assert index_run["status"] == "failed"
    assert terminal_events == 1


# ===================================================================
# P1-D Scenario 7: reader_jobs.input_hash mismatch
# ===================================================================


async def test_p1d_input_hash_old_algorithm_failed_terminal(
    worker_env: asyncpg.Pool,
) -> None:
    """P1-D Scenario 7a: pre-P1-C hash algorithm (no fingerprint) must fail.

    The worker must use the same public canonical hash seam as the
    bootstrap.  An input_hash computed without the profile_fingerprint
    field (pre-P1-C algorithm) must be rejected.
    """
    await _seed_paragraph_environment(worker_env)
    bootstrap_result = await _build_bootstrap_service(worker_env).bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    # Compute the OLD (pre-P1-C) hash: same prefix, no fingerprint suffix.
    old_hash_raw = (
        f"{bootstrap_result.stable_document_id}:"
        f"{bootstrap_result.base_id}:"
        f"{bootstrap_result.plan_content_sha256}:"
        f"{_P1D_V1_INDEX_VERSION}"
    ).encode()
    old_hash = hashlib.sha256(old_hash_raw).hexdigest()
    await _p1d_set_job_input_hash(
        worker_env, job_id=bootstrap_result.job_id, input_hash=old_hash,
    )

    embedding_provider = FakeArticleRagEmbeddingProvider()
    vector_writer = FakeArticleRagVectorWriter()
    service = _build_worker_service(
        worker_env,
        embedding_provider=embedding_provider,
        vector_writer=vector_writer,
    )

    result = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )

    assert result is not None
    assert result.status == "failed_terminal"
    assert result.failure_code == "job_input_hash_mismatch"
    assert embedding_provider.call_count == 0
    assert vector_writer.call_count == 0

    async with worker_env.acquire() as conn:
        job = await _fetch_job(conn, job_id=bootstrap_result.job_id)
        run = await _fetch_run(conn, run_id=job["run_id"])
        index_run = await _p1d_fetch_index_run_with_fingerprint(
            conn, index_run_id=bootstrap_result.index_run_id,
        )
        terminal_events = await _p1d_count_terminal_events(
            conn, job_id=bootstrap_result.job_id,
        )

    assert job["status"] == "failed_terminal"
    assert run["status"] == "failed_terminal"
    assert index_run["status"] == "failed"
    assert terminal_events == 1


@pytest.mark.parametrize(
    "wrong_hash",
    [
        pytest.param("c" * 64, id="wrong_64_hex"),
        pytest.param("0" * 64, id="all_zeros"),
        pytest.param("malformed_hash", id="malformed_short"),
        pytest.param("not-a-hex-at-all", id="malformed_non_hex"),
    ],
)
async def test_p1d_input_hash_mismatch_matrix_failed_terminal(
    worker_env: asyncpg.Pool,
    wrong_hash: str,
) -> None:
    """P1-D Scenario 7b: any wrong/malformed input_hash must fail-closed."""
    await _seed_paragraph_environment(worker_env)
    bootstrap_result = await _build_bootstrap_service(worker_env).bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    await _p1d_set_job_input_hash(
        worker_env, job_id=bootstrap_result.job_id, input_hash=wrong_hash,
    )

    embedding_provider = FakeArticleRagEmbeddingProvider()
    vector_writer = FakeArticleRagVectorWriter()
    service = _build_worker_service(
        worker_env,
        embedding_provider=embedding_provider,
        vector_writer=vector_writer,
    )

    result = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )

    assert result is not None
    assert result.status == "failed_terminal"
    assert result.failure_code == "job_input_hash_mismatch"
    assert embedding_provider.call_count == 0
    assert vector_writer.call_count == 0

    async with worker_env.acquire() as conn:
        job = await _fetch_job(conn, job_id=bootstrap_result.job_id)
        index_run = await _p1d_fetch_index_run_with_fingerprint(
            conn, index_run_id=bootstrap_result.index_run_id,
        )
        terminal_events = await _p1d_count_terminal_events(
            conn, job_id=bootstrap_result.job_id,
        )

    assert job["status"] == "failed_terminal"
    assert index_run["status"] == "failed"
    assert terminal_events == 1


# ===================================================================
# P1-D Scenario 8: persisted index-run fingerprint mismatch
# ===================================================================


async def test_p1d_persisted_index_run_fingerprint_mismatch_failed_terminal(
    worker_env: asyncpg.Pool,
) -> None:
    """P1-D Scenario 8: persisted index-run.profile_fingerprint mismatch.

    input_json + resolver are correct, but the database
    reader_article_rag_index_runs.profile_fingerprint has been changed
    to a different valid SHA-256.  The worker must detect this before
    calling embedding and fail-closed.
    """
    await _seed_paragraph_environment(worker_env)
    bootstrap_result = await _build_bootstrap_service(worker_env).bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    # Mutate the persisted index-run fingerprint to a different valid SHA-256.
    different_valid_fingerprint = "c" * 64
    await _p1d_set_index_run_fingerprint(
        worker_env,
        index_run_id=bootstrap_result.index_run_id,
        fingerprint=different_valid_fingerprint,
    )

    embedding_provider = FakeArticleRagEmbeddingProvider()
    vector_writer = FakeArticleRagVectorWriter()
    service = _build_worker_service(
        worker_env,
        embedding_provider=embedding_provider,
        vector_writer=vector_writer,
    )

    result = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )

    assert result is not None
    assert result.status == "failed_terminal"
    assert result.failure_code == "index_profile_mismatch"
    assert embedding_provider.call_count == 0
    assert vector_writer.call_count == 0

    async with worker_env.acquire() as conn:
        job = await _fetch_job(conn, job_id=bootstrap_result.job_id)
        run = await _fetch_run(conn, run_id=job["run_id"])
        index_run = await _p1d_fetch_index_run_with_fingerprint(
            conn, index_run_id=bootstrap_result.index_run_id,
        )
        terminal_events = await _p1d_count_terminal_events(
            conn, job_id=bootstrap_result.job_id,
        )

    assert job["status"] == "failed_terminal"
    assert run["status"] == "failed_terminal"
    assert index_run["status"] == "failed"
    assert terminal_events == 1


# ===================================================================
# P1-D Scenario 9: pre-vector-write TOCTOU
# ===================================================================


class _P1dFingerprintDriftEmbeddingProvider:
    """Fake embedding provider that mutates the index-run fingerprint
    during embed_texts to simulate a TOCTOU drift.

    The embedding call succeeds (returns real embeddings), but the
    persisted index-run profile_fingerprint is changed to a different
    valid SHA-256 before the provider returns.  The pre-vector-write
    guard must detect this drift and refuse to call the vector writer.
    """

    def __init__(
        self,
        *,
        pool: asyncpg.Pool,
        index_run_id: UUID,
        drift_fingerprint: str = "d" * 64,
    ) -> None:
        self._pool = pool
        self._index_run_id = index_run_id
        self._drift_fingerprint = drift_fingerprint
        self._inner = FakeArticleRagEmbeddingProvider()
        self.call_count = 0

    async def embed_texts(
        self,
        texts: list[str],
        *,
        model: str | None = None,
    ) -> list[ArticleRagEmbedding]:
        self.call_count += 1
        # Drift the persisted fingerprint DURING embedding.
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE reader_article_rag_index_runs "
                "SET profile_fingerprint = $2 WHERE id = $1",
                self._index_run_id,
                self._drift_fingerprint,
            )
        return await self._inner.embed_texts(texts, model=model)


async def test_p1d_pre_vector_write_toctou_fingerprint_drift(
    worker_env: asyncpg.Pool,
) -> None:
    """P1-D Scenario 9: pre-vector-write TOCTOU fingerprint drift.

    The embedding provider mutates the index-run fingerprint during
    embed_texts.  The pre-vector-write guard must:
      - detect the drift
      - refuse to call vector writer (call_count == 0)
      - not mark indexed
      - terminalize job + run + index-run
      - emit exactly 1 terminal event
    """
    await _seed_paragraph_environment(worker_env)
    bootstrap_result = await _build_bootstrap_service(worker_env).bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    embedding_provider = _P1dFingerprintDriftEmbeddingProvider(
        pool=worker_env,
        index_run_id=bootstrap_result.index_run_id,
        drift_fingerprint="d" * 64,
    )
    vector_writer = FakeArticleRagVectorWriter()
    service = _build_worker_service(
        worker_env,
        embedding_provider=embedding_provider,
        vector_writer=vector_writer,
    )

    result = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )

    assert result is not None
    assert result.status == "failed_terminal"
    # Embedding may have completed before drift was detected.
    assert embedding_provider.call_count == 1
    # Vector writer must NOT have been called.
    assert vector_writer.call_count == 0

    async with worker_env.acquire() as conn:
        job = await _fetch_job(conn, job_id=bootstrap_result.job_id)
        run = await _fetch_run(conn, run_id=job["run_id"])
        index_run = await _p1d_fetch_index_run_with_fingerprint(
            conn, index_run_id=bootstrap_result.index_run_id,
        )
        terminal_events = await _p1d_count_terminal_events(
            conn, job_id=bootstrap_result.job_id,
        )

    assert job["status"] == "failed_terminal"
    assert run["status"] == "failed_terminal"
    assert index_run["status"] == "failed"
    assert terminal_events == 1


# ===================================================================
# P1-D Scenario 10: already-indexed idempotent fingerprint verification
# ===================================================================


async def test_p1d_already_indexed_idempotent_fingerprint_mismatch_failed(
    worker_env: asyncpg.Pool,
) -> None:
    """P1-D Scenario 10: already-indexed path must also verify fingerprint.

    The idempotent no-op path (index_run.status == 'indexed') must NOT
    bypass the profile guard.  If the persisted fingerprint differs
    from the resolver/input_json, the worker must fail-closed rather
    than silently succeed with a stale identity.
    """
    await _seed_paragraph_environment(worker_env)
    bootstrap_result = await _build_bootstrap_service(worker_env).bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    # Run the worker once to index the run.
    first_provider = FakeArticleRagEmbeddingProvider()
    first_writer = FakeArticleRagVectorWriter()
    first_service = _build_worker_service(
        worker_env,
        embedding_provider=first_provider,
        vector_writer=first_writer,
    )
    first_result = await first_service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )
    assert first_result is not None
    assert first_result.status == "succeeded"

    # Re-enqueue a second job pointing at the same index-run via a fresh
    # bootstrap (idempotent_noop path will return the existing indexed
    # run).  Then mutate the persisted fingerprint to a different valid
    # SHA-256 so the already-indexed path's fingerprint guard fires.
    different_valid_fingerprint = "e" * 64
    await _p1d_set_index_run_fingerprint(
        worker_env,
        index_run_id=bootstrap_result.index_run_id,
        fingerprint=different_valid_fingerprint,
    )

    # Re-queue the original job to simulate a duplicate claim.
    await _reset_job_to_queued(
        worker_env, job_id=bootstrap_result.job_id,
    )

    embedding_provider = FakeArticleRagEmbeddingProvider()
    vector_writer = FakeArticleRagVectorWriter()
    service = _build_worker_service(
        worker_env,
        embedding_provider=embedding_provider,
        vector_writer=vector_writer,
    )

    result = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )

    assert result is not None
    assert result.status == "failed_terminal"
    assert result.failure_code == "index_profile_mismatch"
    assert embedding_provider.call_count == 0
    assert vector_writer.call_count == 0

    async with worker_env.acquire() as conn:
        job = await _fetch_job(conn, job_id=bootstrap_result.job_id)
        index_run = await _p1d_fetch_index_run_with_fingerprint(
            conn, index_run_id=bootstrap_result.index_run_id,
        )
        terminal_events = await _p1d_count_terminal_events(
            conn, job_id=bootstrap_result.job_id,
        )

    assert job["status"] == "failed_terminal"
    assert index_run["status"] == "failed"
    assert terminal_events == 1


# ===================================================================
# P1-D Scenario 11: safe sentinel propagation
# ===================================================================


async def test_p1d_malicious_fingerprint_sentinel_not_echoed_in_surfaces(
    worker_env: asyncpg.Pool,
) -> None:
    """P1-D Scenario 11a: malicious fingerprint sentinel must not leak.

    Verifies the sentinel is NOT present in:
      - reader_jobs.failure_message
      - reader_jobs.output_ref_json
      - reader_article_rag_index_runs.error_json
      - reader_job_events.payload_json
    """
    await _seed_paragraph_environment(worker_env)
    bootstrap_result = await _build_bootstrap_service(worker_env).bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    payload = await _p1d_fetch_job_payload(
        worker_env, job_id=bootstrap_result.job_id,
    )
    input_json = payload["input_json"]
    input_json["profile_fingerprint"] = _P1D_SENTINEL_FINGERPRINT
    await _p1d_set_job_input_json(
        worker_env, job_id=bootstrap_result.job_id, input_json=input_json,
    )

    embedding_provider = FakeArticleRagEmbeddingProvider()
    vector_writer = FakeArticleRagVectorWriter()
    service = _build_worker_service(
        worker_env,
        embedding_provider=embedding_provider,
        vector_writer=vector_writer,
    )

    result = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )

    assert result is not None
    assert result.status == "failed_terminal"
    assert embedding_provider.call_count == 0
    assert vector_writer.call_count == 0

    async with worker_env.acquire() as conn:
        job = await _fetch_job(conn, job_id=bootstrap_result.job_id)
        index_run = await _p1d_fetch_index_run_with_fingerprint(
            conn, index_run_id=bootstrap_result.index_run_id,
        )
        events = await conn.fetch(
            "SELECT event_type, payload_json FROM reader_job_events "
            "WHERE job_id = $1",
            bootstrap_result.job_id,
        )

    _p1d_assert_no_sentinel_in_surfaces(
        sentinel=_P1D_SENTINEL_FINGERPRINT,
        job=job,
        index_run=index_run,
        events=events,
    )


async def test_p1d_malicious_input_hash_sentinel_not_echoed_in_surfaces(
    worker_env: asyncpg.Pool,
) -> None:
    """P1-D Scenario 11b: malicious input_hash sentinel must not leak."""
    await _seed_paragraph_environment(worker_env)
    bootstrap_result = await _build_bootstrap_service(worker_env).bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    await _p1d_set_job_input_hash(
        worker_env,
        job_id=bootstrap_result.job_id,
        input_hash=_P1D_SENTINEL_INPUT_HASH,
    )

    embedding_provider = FakeArticleRagEmbeddingProvider()
    vector_writer = FakeArticleRagVectorWriter()
    service = _build_worker_service(
        worker_env,
        embedding_provider=embedding_provider,
        vector_writer=vector_writer,
    )

    result = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )

    assert result is not None
    assert result.status == "failed_terminal"
    assert embedding_provider.call_count == 0
    assert vector_writer.call_count == 0

    async with worker_env.acquire() as conn:
        job = await _fetch_job(conn, job_id=bootstrap_result.job_id)
        index_run = await _p1d_fetch_index_run_with_fingerprint(
            conn, index_run_id=bootstrap_result.index_run_id,
        )
        events = await conn.fetch(
            "SELECT event_type, payload_json FROM reader_job_events "
            "WHERE job_id = $1",
            bootstrap_result.job_id,
        )

    _p1d_assert_no_sentinel_in_surfaces(
        sentinel=_P1D_SENTINEL_INPUT_HASH,
        job=job,
        index_run=index_run,
        events=events,
    )


# ===================================================================
# P1-D Scenario 12: context=None terminalization via trusted job_id lookup
# ===================================================================


async def test_p1d_context_none_terminalizes_linked_index_run_via_job_id(
    worker_env: asyncpg.Pool,
) -> None:
    """P1-D Scenario 12: when _load_job_context fails, the worker must
    terminalize the linked index-run via the trusted DB relationship
    reader_article_rag_index_runs.job_id = claim.job_id, NOT via the
    (potentially corrupt) input_json.index_run_id.

    Setup:
      - Remove profile_fingerprint from input_json (forces context=None)
      - Also corrupt input_json.index_run_id to a bogus UUID

    Asserts:
      - The REAL index-run (linked via job_id) is terminalized to 'failed'
      - The bogus index_run_id is NOT used to update any row
      - job + run + linked index-run all terminal
      - exactly 1 terminal event
    """
    await _seed_paragraph_environment(worker_env)
    bootstrap_result = await _build_bootstrap_service(worker_env).bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    # Corrupt input_json: remove profile_fingerprint AND set bogus index_run_id.
    bogus_index_run_id = str(uuid4())
    payload = await _p1d_fetch_job_payload(
        worker_env, job_id=bootstrap_result.job_id,
    )
    input_json = payload["input_json"]
    input_json.pop("profile_fingerprint", None)
    input_json["index_run_id"] = bogus_index_run_id
    await _p1d_set_job_input_json(
        worker_env, job_id=bootstrap_result.job_id, input_json=input_json,
    )

    embedding_provider = FakeArticleRagEmbeddingProvider()
    vector_writer = FakeArticleRagVectorWriter()
    service = _build_worker_service(
        worker_env,
        embedding_provider=embedding_provider,
        vector_writer=vector_writer,
    )

    result = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )

    assert result is not None
    assert result.status == "failed_terminal"
    assert embedding_provider.call_count == 0
    assert vector_writer.call_count == 0

    async with worker_env.acquire() as conn:
        job = await _fetch_job(conn, job_id=bootstrap_result.job_id)
        run = await _fetch_run(conn, run_id=job["run_id"])
        # The REAL index-run (linked via job_id) must be terminalized.
        real_index_run = await _p1d_fetch_index_run_with_fingerprint(
            conn, index_run_id=bootstrap_result.index_run_id,
        )
        terminal_events = await _p1d_count_terminal_events(
            conn, job_id=bootstrap_result.job_id,
        )
        retry_events = await _p1d_count_retry_events(
            conn, job_id=bootstrap_result.job_id,
        )

    assert job["status"] == "failed_terminal"
    assert run["status"] == "failed_terminal"
    assert real_index_run["status"] == "failed"
    assert terminal_events == 1
    assert retry_events == 0


# ===================================================================
# P1-D Scenario 13: vector write metadata carries profile_fingerprint
# ===================================================================


class _P1dMetadataCapturingVectorWriter:
    """Fake vector writer that captures the ArticleRagVectorWriteMetadata
    so tests can verify it carries the canonical profile_fingerprint.
    """

    def __init__(self) -> None:
        self.call_count = 0
        self.captured_metadata: ArticleRagVectorWriteMetadata | None = None

    async def upsert_chunks(
        self,
        *,
        collection: str,
        chunks_with_embeddings: list[ArticleRagVectorChunk],
        metadata: ArticleRagVectorWriteMetadata,
    ) -> ArticleRagVectorWriteResult:
        self.call_count += 1
        self.captured_metadata = metadata
        return ArticleRagVectorWriteResult(
            collection=collection,
            upserted_count=len(chunks_with_embeddings),
            provider_metadata={"provider": "p1d_fake_capturing"},
        )


async def test_p1d_vector_write_metadata_carries_profile_fingerprint(
    worker_env: asyncpg.Pool,
) -> None:
    """P1-D Scenario 13: ArticleRagVectorWriteMetadata must carry the
    canonical profile_fingerprint on the happy path.
    """
    await _seed_paragraph_environment(worker_env)
    await _build_bootstrap_service(worker_env).bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    embedding_provider = FakeArticleRagEmbeddingProvider()
    vector_writer = _P1dMetadataCapturingVectorWriter()
    service = _build_worker_service(
        worker_env,
        embedding_provider=embedding_provider,
        vector_writer=vector_writer,
    )

    result = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )

    assert result is not None
    assert result.status == "succeeded"
    assert vector_writer.call_count == 1
    assert vector_writer.captured_metadata is not None
    captured = vector_writer.captured_metadata
    assert captured.profile_fingerprint == _P1D_V1_PROFILE_FINGERPRINT


# ===================================================================
# P1-D-R1 Rework: fail-closed gap RED tests.
#
# These tests were added in the P1-D-R1 rework round to close 5
# fail-closed gaps discovered in the P1-D review.  Each test asserts
# the desired post-fix behaviour; before the fixes they FAIL (RED).
# ===================================================================


# ---------------------------------------------------------------------
# RED 1: ArticleRagVectorWriteMetadata.profile_fingerprint must be a
# constructor required argument (no "" default).
# ---------------------------------------------------------------------


async def test_p1d_r1_metadata_omission_raises_typeerror() -> None:
    """RED 1: omitting profile_fingerprint must raise TypeError.

    Before the fix, ``profile_fingerprint: str = ""`` allows callers to
    silently omit the field, which is fail-open.  After the fix the
    field has no default and construction fails with TypeError.
    """
    from uuid import uuid4 as _uuid4

    with pytest.raises(TypeError):
        ArticleRagVectorWriteMetadata(
            collection="probe",
            reading_record_id=_uuid4(),
            stable_document_id=_uuid4(),
            base_id=_uuid4(),
            record_generation=1,
            index_version="probe",
            chunker_version="probe",
            plan_content_sha256="0" * 64,
            chunk_count=0,
            # profile_fingerprint intentionally omitted — must TypeError.
        )


# ---------------------------------------------------------------------
# RED 2: SHA-256 strict fullmatch — trailing LF/CRLF must be rejected
# as malformed, not accepted as a shape-pass then mismatch.
# ---------------------------------------------------------------------


async def test_p1d_r1_fingerprint_trailing_lf_rejected_as_malformed(
    worker_env: asyncpg.Pool,
) -> None:
    """RED 2a: 64-hex + trailing LF must fail shape check (fullmatch).

    Before the fix, ``re.match("^[0-9a-f]{64}$", value)`` accepts a
    single trailing newline because Python ``$`` matches before a
    final ``\\n``.  The fingerprint passes the shape check and then
    fails the resolver-equality check, producing
    ``index_profile_mismatch``.  After the fix, ``re.fullmatch`` rejects
    the trailing LF as malformed, producing ``index_profile_invalid``.
    """
    await _seed_paragraph_environment(worker_env)
    bootstrap_result = await _build_bootstrap_service(
        worker_env,
    ).bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    payload = await _p1d_fetch_job_payload(
        worker_env, job_id=bootstrap_result.job_id,
    )
    input_json = payload["input_json"]
    input_json["profile_fingerprint"] = "a" * 64 + "\n"
    await _p1d_set_job_input_json(
        worker_env, job_id=bootstrap_result.job_id, input_json=input_json,
    )

    embedding_provider = FakeArticleRagEmbeddingProvider()
    vector_writer = FakeArticleRagVectorWriter()
    service = _build_worker_service(
        worker_env,
        embedding_provider=embedding_provider,
        vector_writer=vector_writer,
    )

    result = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )

    assert result is not None
    assert result.status == "failed_terminal"
    assert result.failure_code == "index_profile_invalid"
    assert result.retryable is False
    assert embedding_provider.call_count == 0
    assert vector_writer.call_count == 0


async def test_p1d_r1_fingerprint_trailing_crlf_rejected_as_malformed(
    worker_env: asyncpg.Pool,
) -> None:
    """RED 2b: 64-hex + trailing CRLF must fail shape check (fullmatch)."""
    await _seed_paragraph_environment(worker_env)
    bootstrap_result = await _build_bootstrap_service(
        worker_env,
    ).bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    payload = await _p1d_fetch_job_payload(
        worker_env, job_id=bootstrap_result.job_id,
    )
    input_json = payload["input_json"]
    input_json["profile_fingerprint"] = "a" * 64 + "\r\n"
    await _p1d_set_job_input_json(
        worker_env, job_id=bootstrap_result.job_id, input_json=input_json,
    )

    embedding_provider = FakeArticleRagEmbeddingProvider()
    vector_writer = FakeArticleRagVectorWriter()
    service = _build_worker_service(
        worker_env,
        embedding_provider=embedding_provider,
        vector_writer=vector_writer,
    )

    result = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )

    assert result is not None
    assert result.status == "failed_terminal"
    assert result.failure_code == "index_profile_invalid"
    assert result.retryable is False
    assert embedding_provider.call_count == 0
    assert vector_writer.call_count == 0


async def test_p1d_r1_input_hash_trailing_lf_rejected_as_malformed(
    worker_env: asyncpg.Pool,
) -> None:
    """RED 2c: valid 64-hex + trailing LF in input_hash must be rejected.

    The failure_code is ``job_input_hash_mismatch`` either way (malformed
    or mismatch); the semantic distinction is that the strict fullmatch
    rejects the trailing LF at the shape gate rather than letting it
    through to the equality check.  This is a characterization test —
    it may pass on the first run because the failure_code is the same.
    """
    await _seed_paragraph_environment(worker_env)
    bootstrap_result = await _build_bootstrap_service(
        worker_env,
    ).bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    # A valid-shape 64-hex value that is NOT the real V1 hash, with LF.
    await _p1d_set_job_input_hash(
        worker_env,
        job_id=bootstrap_result.job_id,
        input_hash="a" * 64 + "\n",
    )

    embedding_provider = FakeArticleRagEmbeddingProvider()
    vector_writer = FakeArticleRagVectorWriter()
    service = _build_worker_service(
        worker_env,
        embedding_provider=embedding_provider,
        vector_writer=vector_writer,
    )

    result = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )

    assert result is not None
    assert result.status == "failed_terminal"
    assert result.failure_code == "job_input_hash_mismatch"
    assert result.retryable is False
    assert embedding_provider.call_count == 0
    assert vector_writer.call_count == 0


async def test_p1d_r1_input_hash_trailing_crlf_rejected_as_malformed(
    worker_env: asyncpg.Pool,
) -> None:
    """RED 2d: valid 64-hex + trailing CRLF in input_hash must be rejected."""
    await _seed_paragraph_environment(worker_env)
    bootstrap_result = await _build_bootstrap_service(
        worker_env,
    ).bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    await _p1d_set_job_input_hash(
        worker_env,
        job_id=bootstrap_result.job_id,
        input_hash="a" * 64 + "\r\n",
    )

    embedding_provider = FakeArticleRagEmbeddingProvider()
    vector_writer = FakeArticleRagVectorWriter()
    service = _build_worker_service(
        worker_env,
        embedding_provider=embedding_provider,
        vector_writer=vector_writer,
    )

    result = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )

    assert result is not None
    assert result.status == "failed_terminal"
    assert result.failure_code == "job_input_hash_mismatch"
    assert result.retryable is False
    assert embedding_provider.call_count == 0
    assert vector_writer.call_count == 0


# ---------------------------------------------------------------------
# RED 3: job_id cardinality check — 0/multiple linked index-runs must
# fail-closed with index_run_link_invalid.
# ---------------------------------------------------------------------


async def test_p1d_r1_zero_job_id_links_fail_closed(
    worker_env: asyncpg.Pool,
) -> None:
    """RED 3a: zero linked index-runs for job_id must fail-closed.

    Before the fix, ``_load_job_context`` uses ``fetchrow`` which
    returns None for 0 rows; the code then skips the input_hash
    recompute and defers to ``_mark_indexing_or_detect_noop`` which
    raises ``index_run_missing``.  After the fix, the cardinality check
    in ``_load_job_context`` raises ``index_run_link_invalid`` directly.
    """
    await _seed_paragraph_environment(worker_env)
    bootstrap_result = await _build_bootstrap_service(
        worker_env,
    ).bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    # Delete the linked index-run row so the job_id lookup returns 0 rows.
    async with worker_env.acquire() as conn:
        await conn.execute(
            "DELETE FROM reader_article_rag_index_runs WHERE id = $1",
            bootstrap_result.index_run_id,
        )

    embedding_provider = FakeArticleRagEmbeddingProvider()
    vector_writer = FakeArticleRagVectorWriter()
    service = _build_worker_service(
        worker_env,
        embedding_provider=embedding_provider,
        vector_writer=vector_writer,
    )

    result = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )

    assert result is not None
    assert result.status == "failed_terminal"
    assert result.failure_code == "index_run_link_invalid"
    assert result.retryable is False
    assert embedding_provider.call_count == 0
    assert vector_writer.call_count == 0

    async with worker_env.acquire() as conn:
        job = await _fetch_job(conn, job_id=bootstrap_result.job_id)
        run = await _fetch_run(conn, run_id=job["run_id"])
        terminal_events = await _p1d_count_terminal_events(
            conn, job_id=bootstrap_result.job_id,
        )
        retry_events = await _p1d_count_retry_events(
            conn, job_id=bootstrap_result.job_id,
        )

    assert job["status"] == "failed_terminal"
    assert run["status"] == "failed_terminal"
    assert terminal_events == 1
    assert retry_events == 0


async def test_p1d_r1_multiple_job_id_links_fail_closed(
    worker_env: asyncpg.Pool,
) -> None:
    """RED 3b: multiple index-runs sharing same job_id must fail-closed.

    Before the fix, ``_load_job_context`` uses ``fetchrow`` which
    arbitrarily picks one row.  After the fix, the cardinality check
    reads the full candidate set and raises ``index_run_link_invalid``
    for >1 rows.  ``_handle_failed_terminal(context=None)`` also reads
    the full candidate set and refuses to update any row when the
    cardinality is not exactly 1.
    """
    await _seed_paragraph_environment(worker_env)
    bootstrap_result = await _build_bootstrap_service(
        worker_env,
    ).bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    # Seed failed and superseded index-runs sharing the same job_id.  The
    # actual active uniqueness constraint is on
    # (stable_document_id, index_version), and terminal rows are excluded
    # from it.  There is no uniqueness constraint on job_id.
    extra_failed_index_run_id = uuid4()
    extra_superseded_index_run_id = uuid4()
    async with worker_env.acquire() as conn:
        for extra_index_run_id, terminal_status in (
            (extra_failed_index_run_id, "failed"),
            (extra_superseded_index_run_id, "superseded"),
        ):
            await conn.execute(
                """
                INSERT INTO reader_article_rag_index_runs (
                    id, job_id, base_id, stable_document_id, reading_record_id,
                    reader_run_id, record_generation, index_version,
                    chunker_version, profile_fingerprint,
                    stable_document_content_sha256, canonical_text_sha256,
                    plan_content_sha256,
                    chunk_count, status, created_at, updated_at
                ) VALUES (
                    $1, $2, $3, $4, $5, NULL, $6, $7, $8, $9, $10, $11, $12,
                    $13, $14, NOW(), NOW()
                )
                """,
                extra_index_run_id,
                bootstrap_result.job_id,
                _BASE_ID,
                _STABLE_DOC_ID,
                _RECORD_ID,
                1,
                _P1D_V1_INDEX_VERSION,
                _P1D_V1_CHUNKER_VERSION,
                _P1D_V1_PROFILE_FINGERPRINT,
                "0" * 64,
                "0" * 64,
                "0" * 64,
                0,
                terminal_status,
            )

    # Capture original statuses of all candidate rows.
    async with worker_env.acquire() as conn:
        orig_main = await _p1d_fetch_index_run_with_fingerprint(
            conn, index_run_id=bootstrap_result.index_run_id,
        )
        orig_failed = await _p1d_fetch_index_run_with_fingerprint(
            conn, index_run_id=extra_failed_index_run_id,
        )
        orig_superseded = await _p1d_fetch_index_run_with_fingerprint(
            conn, index_run_id=extra_superseded_index_run_id,
        )

    embedding_provider = FakeArticleRagEmbeddingProvider()
    vector_writer = FakeArticleRagVectorWriter()
    service = _build_worker_service(
        worker_env,
        embedding_provider=embedding_provider,
        vector_writer=vector_writer,
    )

    result = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )

    assert result is not None
    assert result.status == "failed_terminal"
    assert result.failure_code == "index_run_link_invalid"
    assert result.retryable is False
    assert embedding_provider.call_count == 0
    assert vector_writer.call_count == 0

    async with worker_env.acquire() as conn:
        job = await _fetch_job(conn, job_id=bootstrap_result.job_id)
        run = await _fetch_run(conn, run_id=job["run_id"])
        main_now = await _p1d_fetch_index_run_with_fingerprint(
            conn, index_run_id=bootstrap_result.index_run_id,
        )
        failed_now = await _p1d_fetch_index_run_with_fingerprint(
            conn, index_run_id=extra_failed_index_run_id,
        )
        superseded_now = await _p1d_fetch_index_run_with_fingerprint(
            conn, index_run_id=extra_superseded_index_run_id,
        )
        terminal_events = await _p1d_count_terminal_events(
            conn, job_id=bootstrap_result.job_id,
        )
        retry_events = await _p1d_count_retry_events(
            conn, job_id=bootstrap_result.job_id,
        )
        events = await conn.fetch(
            "SELECT event_type, payload_json FROM reader_job_events "
            "WHERE job_id = $1",
            bootstrap_result.job_id,
        )

    assert job["status"] == "failed_terminal"
    assert run["status"] == "failed_terminal"
    # All candidate index-runs must keep their original status — no
    # arbitrary update.
    assert main_now["status"] == orig_main["status"]
    assert failed_now["status"] == orig_failed["status"]
    assert superseded_now["status"] == orig_superseded["status"]
    assert terminal_events == 1
    assert retry_events == 0

    # The error surface must not echo candidate IDs or row count.
    for candidate_id in (
        extra_failed_index_run_id,
        extra_superseded_index_run_id,
    ):
        _p1d_assert_no_sentinel_in_surfaces(
            sentinel=str(candidate_id),
            job=job,
            events=events,
        )


# ---------------------------------------------------------------------
# RED 4: resolver exception chain must be closed — __cause__ and
# __context__ must both be None on the outer ArticleRagIndexWorkerError.
# ---------------------------------------------------------------------


class _P1dR1ErrorCapturingWorker(ArticleRagIndexWorkerService):
    """Test-only worker subclass that captures the actual
    ArticleRagIndexWorkerError at the terminal handler entry, then
    delegates to super.

    Used to verify the resolver exception chain is fully closed
    (__cause__ is None, __context__ is None, no sentinel in
    str/repr/traceback).
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.captured_errors: list[ArticleRagIndexWorkerError] = []

    async def _handle_failed_terminal(
        self,
        *,
        claim: Any,
        context: Any,
        exc: ArticleRagIndexWorkerError,
    ) -> ArticleRagIndexWorkerResult:
        self.captured_errors.append(exc)
        return await super()._handle_failed_terminal(
            claim=claim, context=context, exc=exc,
        )


async def test_p1d_r1_resolver_wrapper_closes_exception_chain(
    worker_env: asyncpg.Pool,
) -> None:
    """RED 4: resolver exception wrapper must not chain __cause__/__context__.

    Before the fix, ``raise ArticleRagIndexWorkerError(...) from exc``
    sets ``__cause__ = exc`` (the ArticleRagIndexProfileResolutionError)
    and implicit chaining sets ``__context__``.  After the fix, the
    outer error is constructed inside the except block but raised
    outside it, so both ``__cause__`` and ``__context__`` are None.
    """
    import traceback as tb_module

    await _seed_paragraph_environment(worker_env)
    bootstrap_result = await _build_bootstrap_service(
        worker_env,
    ).bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    payload = await _p1d_fetch_job_payload(
        worker_env, job_id=bootstrap_result.job_id,
    )
    input_json = payload["input_json"]
    input_json["index_version"] = _P1D_SENTINEL_INDEX_VERSION
    await _p1d_set_job_input_json(
        worker_env, job_id=bootstrap_result.job_id, input_json=input_json,
    )

    embedding_provider = FakeArticleRagEmbeddingProvider()
    vector_writer = FakeArticleRagVectorWriter()
    service = _P1dR1ErrorCapturingWorker(
        pool=worker_env,
        embedding_provider=embedding_provider,
        vector_writer=vector_writer,
    )

    result = await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )

    assert result is not None
    assert result.status == "failed_terminal"
    assert result.failure_code == "index_profile_invalid"
    assert embedding_provider.call_count == 0
    assert vector_writer.call_count == 0
    assert len(service.captured_errors) == 1

    err = service.captured_errors[0]
    # __cause__ must be None (no explicit chaining).
    assert err.__cause__ is None, (
        f"__cause__ must be None, got {err.__cause__!r}"
    )
    # __context__ must be None (no implicit chaining).
    assert err.__context__ is None, (
        f"__context__ must be None, got {err.__context__!r}"
    )

    # str/repr/traceback must not carry the sentinel index_version.
    err_str = str(err)
    err_repr = repr(err)
    err_tb = "".join(
        tb_module.format_exception(type(err), err, err.__traceback__)
    )
    assert _P1D_SENTINEL_INDEX_VERSION not in err_str
    assert _P1D_SENTINEL_INDEX_VERSION not in err_repr
    assert _P1D_SENTINEL_INDEX_VERSION not in err_tb

    # Verify persisted surfaces also carry no sentinel.
    async with worker_env.acquire() as conn:
        job = await _fetch_job(conn, job_id=bootstrap_result.job_id)
        index_run = await _p1d_fetch_index_run_with_fingerprint(
            conn, index_run_id=bootstrap_result.index_run_id,
        )
        events = await conn.fetch(
            "SELECT event_type, payload_json FROM reader_job_events "
            "WHERE job_id = $1",
            bootstrap_result.job_id,
        )

    _p1d_assert_no_sentinel_in_surfaces(
        sentinel=_P1D_SENTINEL_INDEX_VERSION,
        job=job,
        index_run=index_run,
        events=events,
        exc=err,
    )
