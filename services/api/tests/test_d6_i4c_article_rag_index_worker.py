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
from datetime import UTC, datetime, timedelta
from pathlib import Path
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

INDEX_WORKER_SCHEMA_SQL = BASELINE_SQL


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
               rationale_code, failure_class, failure_code
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
    }
    assert set(input_json.keys()) == expected_keys
    assert input_json["source"] == ARTICLE_RAG_INDEX_JOB_SOURCE

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
    worker fails closed with failure_code=index_run_missing."""
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
    assert result.failure_code == "index_run_missing"

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
    failure_code=index_run_wrong_job_id."""
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
    assert result.failure_code == "index_run_wrong_job_id"


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

        index_run = await _fetch_index_run(
            conn, index_run_id=bootstrap_result.index_run_id,
        )
        # Index run must NOT be permanently stuck in 'indexing'.
        assert index_run["status"] == "queued"
        error_json = dict(index_run["error_json"])
        assert error_json["failure_code"] == "embedding_failed"
        assert error_json["retryable"] is True


# ===================================================================
# Test 10a: retryable vector writer error -> retry_later
# ===================================================================


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
