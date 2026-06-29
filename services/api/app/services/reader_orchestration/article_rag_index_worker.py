"""D6-I4C: Article RAG Index Worker Foundation.

Claims ``article_rag_index_build`` reader_jobs (enqueued by D6-I4B
bootstrap), reloads the truth-layer index plan via
``ArticleRagIndexPlanService``, calls an embedding provider and a vector
writer, and transitions the index state row from ``queued`` →
``indexing`` → ``indexed``.

This worker does NOT:
  * call real embedding models (DashScope / Bailian / OpenAI)
  * connect to real Zilliz / Milvus
  * send external events
  * store chunk text in ``reader_jobs.input_json`` or the index state row
  * modify I4A citation truth rules

Transaction model:
  * Phase 1 (DB tx): lock ``reader_article_rag_index_runs`` FOR UPDATE,
    validate state, transition ``queued``/``indexing`` → ``indexing``.
  * Phase 2 (DB tx, read-only): rebuild the plan via
    ``ArticleRagIndexPlanService.build_index_plan_in_transaction``,
    validate ``plan_content_sha256`` + ``chunk_count``.
  * Phase 3 (no DB): call embedding provider with chunk texts.
  * Phase 4 (no DB): call vector writer with chunks + embeddings
    (no chunk text — only hashes + citation metadata).
  * Phase 5 (DB tx): lock index run FOR UPDATE again, re-validate
    lease/fence, transition ``indexing`` → ``indexed``, transition job
    to ``succeeded``.

Provider calls happen OUTSIDE DB transactions so they don't hold locks.
The final ``indexed`` transition + job ``succeeded`` transition run in
the SAME short transaction to avoid state drift.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

import asyncpg

from app.database import connection as db_connection
from app.database.json_compat import jsonb_param

from .article_rag_index_bootstrap import (
    ARTICLE_RAG_INDEX_BUILD_JOB_TYPE,
    ARTICLE_RAG_INDEX_BUILD_TARGET_TYPE,
    DEFAULT_INDEX_VERSION,
)
from .article_rag_index_plan import (
    ArticleRagIndexPlan,
    ArticleRagIndexPlanError,
    ArticleRagIndexPlanService,
    compute_plan_content_sha256,
)
from .job_runtime import (
    ClaimResult,
    FenceViolationError,
    ReaderJobRuntime,
    _assert_lease_valid,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_ARTICLE_RAG_INDEX_LEASE_DURATION = timedelta(seconds=60)
DEFAULT_ARTICLE_RAG_INDEX_RETRY_DELAY = timedelta(minutes=2)
ARTICLE_RAG_INDEX_WORKER_VERSION = "d6-i4c-article-rag-index-worker"

# Job payload source tag — must match D6-I4B bootstrap.
ARTICLE_RAG_INDEX_JOB_SOURCE = "article_rag_index_bootstrap"

# Default vector store metadata (fake — no real Zilliz/Milvus connection).
DEFAULT_FAKE_EMBEDDING_MODEL = "fake-embedding-deterministic-v1"
DEFAULT_FAKE_VECTOR_STORE_PROVIDER = "fake-in-memory"
DEFAULT_FAKE_VECTOR_COLLECTION = "article_rag_index_v1"
DEFAULT_FAKE_EMBEDDING_DIM = 8

# Failure codes.
FAILURE_CODE_INPUT_JSON_INVALID = "input_json_invalid"
FAILURE_CODE_INDEX_RUN_MISSING = "index_run_missing"
FAILURE_CODE_INDEX_RUN_WRONG_STATUS = "index_run_wrong_status"
FAILURE_CODE_INDEX_RUN_WRONG_JOB_ID = "index_run_wrong_job_id"
FAILURE_CODE_INDEX_RUN_FIELD_MISMATCH = "index_run_field_mismatch"
FAILURE_CODE_PLAN_HASH_MISMATCH = "plan_hash_mismatch"
FAILURE_CODE_EMBEDDING_PROVIDER_UNCONFIGURED = "embedding_provider_unconfigured"
FAILURE_CODE_VECTOR_WRITER_UNCONFIGURED = "vector_writer_unconfigured"
FAILURE_CODE_EMBEDDING_FAILED = "embedding_failed"
FAILURE_CODE_VECTOR_WRITE_FAILED = "vector_write_failed"
FAILURE_CODE_ALREADY_INDEXED = "already_indexed"


# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------


class ArticleRagIndexWorkerError(RuntimeError):
    """Typed error for worker failures.

    ``retryable`` controls whether the job transitions to ``retry_later``
    or ``failed_terminal``.  ``failure_class`` / ``failure_code`` are
    persisted on the job row.  ``rationale_code`` is persisted on both
    the job row and the index run error_json.
    """

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


class _InputJsonError(ValueError):
    """Raised when the job payload fails validation.

    Mapped to ``failed_terminal`` with ``failure_code=input_json_invalid``.
    """


# ---------------------------------------------------------------------------
# Provider data models
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ArticleRagEmbedding:
    """One embedding vector for one chunk.

    ``text_sha256`` links the embedding back to the chunk's
    ``embedding_text_sha256`` so the worker can verify coverage without
    storing chunk text in the vector payload.
    """

    text_sha256: str
    model: str
    vector: tuple[float, ...]
    dim: int


@dataclass(frozen=True, slots=True)
class ArticleRagVectorChunk:
    """One chunk ready for vector store upsert.

    Carries NO chunk text — only deterministic hashes, the embedding,
    citation metadata, and per-chunk metadata.  This is the contract
    boundary: nothing beyond these fields crosses into the vector store.
    """

    chunk_id: str
    content_sha256: str
    embedding_text_sha256: str
    embedding: ArticleRagEmbedding
    citation: dict[str, Any]
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ArticleRagVectorWriteMetadata:
    """Per-upsert metadata passed to the vector writer."""

    collection: str
    reading_record_id: UUID
    stable_document_id: UUID
    base_id: UUID
    record_generation: int
    index_version: str
    chunker_version: str
    plan_content_sha256: str
    chunk_count: int


@dataclass(frozen=True, slots=True)
class ArticleRagVectorWriteResult:
    """Result of a vector store upsert."""

    collection: str
    upserted_count: int
    provider_metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Provider Protocols
# ---------------------------------------------------------------------------


class ArticleRagEmbeddingProvider(Protocol):
    """Embeds chunk texts into dense vectors.

    Implementations MUST NOT:
      * log chunk text at INFO or higher
      * include chunk text in raised exceptions
      * call real LLM/embedding APIs unless explicitly configured

    Implementations MUST:
      * return embeddings in the same order as the input texts
      * set ``ArticleRagEmbedding.text_sha256`` to the SHA-256 of the
        corresponding input text (so the worker can verify coverage)
    """

    async def embed_texts(
        self,
        texts: list[str],
        *,
        model: str | None = None,
    ) -> list[ArticleRagEmbedding]: ...


class ArticleRagVectorWriter(Protocol):
    """Upserts chunks (with embeddings) into a vector store.

    Implementations MUST NOT:
      * call real Zilliz / Milvus / Pinecone unless explicitly configured
      * log chunk text or embedding vectors at INFO or higher
      * include chunk text in raised exceptions

    Implementations MUST:
      * be idempotent on ``(collection, chunk_id)`` — re-upserting the
        same chunk overwrites the prior vector
      * return ``ArticleRagVectorWriteResult`` with the upserted count
    """

    async def upsert_chunks(
        self,
        *,
        collection: str,
        chunks_with_embeddings: list[ArticleRagVectorChunk],
        metadata: ArticleRagVectorWriteMetadata,
    ) -> ArticleRagVectorWriteResult: ...


# ---------------------------------------------------------------------------
# Unconfigured providers (fail-closed defaults)
# ---------------------------------------------------------------------------


class UnconfiguredArticleRagEmbeddingProvider:
    """Default embedding provider — fails closed, no network calls."""

    async def embed_texts(
        self,
        texts: list[str],
        *,
        model: str | None = None,
    ) -> list[ArticleRagEmbedding]:
        raise ArticleRagIndexWorkerError(
            "article RAG embedding provider is not configured; inject an "
            "explicit fake provider for tests or wire a real DashScope / "
            "Bailian / OpenAI provider for production",
            retryable=False,
            failure_class="configuration",
            failure_code=FAILURE_CODE_EMBEDDING_PROVIDER_UNCONFIGURED,
        )


class UnconfiguredArticleRagVectorWriter:
    """Default vector writer — fails closed, no Zilliz/Milvus calls."""

    async def upsert_chunks(
        self,
        *,
        collection: str,
        chunks_with_embeddings: list[ArticleRagVectorChunk],
        metadata: ArticleRagVectorWriteMetadata,
    ) -> ArticleRagVectorWriteResult:
        raise ArticleRagIndexWorkerError(
            "article RAG vector writer is not configured; inject an "
            "explicit fake writer for tests or wire a real Zilliz / "
            "Milvus writer for production",
            retryable=False,
            failure_class="configuration",
            failure_code=FAILURE_CODE_VECTOR_WRITER_UNCONFIGURED,
        )


# ---------------------------------------------------------------------------
# Fake providers (test-only, deterministic, no network)
# ---------------------------------------------------------------------------


class FakeArticleRagEmbeddingProvider:
    """Deterministic fake embedding provider for tests.

    Generates small fixed-dimension vectors by hashing the text.  No
    network calls, no external dependencies.  The vector is derived
    from the SHA-256 of the text so the same text always produces the
    same vector (deterministic across runs).
    """

    def __init__(
        self,
        *,
        dim: int = DEFAULT_FAKE_EMBEDDING_DIM,
        model: str = DEFAULT_FAKE_EMBEDDING_MODEL,
    ) -> None:
        self._dim = dim
        self._model = model
        self.call_count = 0
        self.last_texts: list[str] | None = None

    async def embed_texts(
        self,
        texts: list[str],
        *,
        model: str | None = None,
    ) -> list[ArticleRagEmbedding]:
        self.call_count += 1
        self.last_texts = list(texts)
        used_model = model or self._model
        results: list[ArticleRagEmbedding] = []
        for text in texts:
            text_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
            digest = hashlib.sha256(
                (text_sha + "|" + used_model).encode("utf-8")
            ).digest()
            # Build a deterministic vector of ``dim`` floats in [-1, 1).
            vector: list[float] = []
            for i in range(self._dim):
                byte_val = digest[i % len(digest)]
                vector.append((byte_val / 255.0) * 2.0 - 1.0)
            results.append(
                ArticleRagEmbedding(
                    text_sha256=text_sha,
                    model=used_model,
                    vector=tuple(vector),
                    dim=self._dim,
                )
            )
        return results


class FakeArticleRagVectorWriter:
    """In-memory fake vector writer for tests.

    Records every upsert call.  No network calls.  ``upserts`` is a
    list of ``(collection, chunks, metadata)`` tuples for inspection.
    """

    def __init__(
        self,
        *,
        provider_name: str = DEFAULT_FAKE_VECTOR_STORE_PROVIDER,
    ) -> None:
        self._provider_name = provider_name
        self.upserts: list[
            tuple[
                str,
                list[ArticleRagVectorChunk],
                ArticleRagVectorWriteMetadata,
            ]
        ] = []
        self.call_count = 0

    @property
    def provider_name(self) -> str:
        return self._provider_name

    async def upsert_chunks(
        self,
        *,
        collection: str,
        chunks_with_embeddings: list[ArticleRagVectorChunk],
        metadata: ArticleRagVectorWriteMetadata,
    ) -> ArticleRagVectorWriteResult:
        self.call_count += 1
        self.upserts.append(
            (collection, list(chunks_with_embeddings), metadata)
        )
        return ArticleRagVectorWriteResult(
            collection=collection,
            upserted_count=len(chunks_with_embeddings),
            provider_metadata={"provider": self._provider_name},
        )


# ---------------------------------------------------------------------------
# Worker result
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ArticleRagIndexWorkerResult:
    """Returned by :meth:`ArticleRagIndexWorkerService.process_next`."""

    job_id: UUID
    index_run_id: UUID
    reading_record_id: UUID
    stable_document_id: UUID
    base_id: UUID
    status: str
    chunk_count: int
    embedding_model: str | None = None
    vector_store_provider: str | None = None
    vector_collection: str | None = None
    retryable: bool | None = None
    failure_code: str | None = None
    idempotent_noop: bool = False


# ---------------------------------------------------------------------------
# Internal: parsed job context
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _JobContext:
    """Validated job payload."""

    job_id: UUID
    run_id: UUID
    reading_record_id: UUID
    user_id: UUID
    stable_document_id: UUID
    base_id: UUID
    record_generation: int
    index_run_id: UUID
    index_version: str
    chunker_version: str


# ---------------------------------------------------------------------------
# Worker service
# ---------------------------------------------------------------------------


class ArticleRagIndexWorkerService:
    """Claims and executes ``article_rag_index_build`` jobs.

    The worker never calls real embedding models or Zilliz/Milvus.
    Inject explicit providers (fake for tests, real for production) via
    the constructor.
    """

    def __init__(
        self,
        *,
        pool: asyncpg.Pool | None = None,
        job_runtime: ReaderJobRuntime | None = None,
        plan_service: ArticleRagIndexPlanService | None = None,
        embedding_provider: ArticleRagEmbeddingProvider | None = None,
        vector_writer: ArticleRagVectorWriter | None = None,
        default_vector_collection: str = DEFAULT_FAKE_VECTOR_COLLECTION,
    ) -> None:
        self._pool = pool
        self._job_runtime = job_runtime or ReaderJobRuntime(pool=pool)
        self._plan_service = plan_service or ArticleRagIndexPlanService(pool=pool)
        self._embedding_provider = (
            embedding_provider
            or UnconfiguredArticleRagEmbeddingProvider()
        )
        self._vector_writer = (
            vector_writer
            or UnconfiguredArticleRagVectorWriter()
        )
        self._default_vector_collection = default_vector_collection

    def get_pool(self) -> asyncpg.Pool:
        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("Database pool not initialized")
        return pool

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def process_next(
        self,
        *,
        lease_owner: str,
        lease_duration: timedelta = DEFAULT_ARTICLE_RAG_INDEX_LEASE_DURATION,
        retry_delay: timedelta = DEFAULT_ARTICLE_RAG_INDEX_RETRY_DELAY,
    ) -> ArticleRagIndexWorkerResult | None:
        """Claim and process the next article RAG index build job.

        Returns ``None`` if no job is available.  On success, transitions
        the job to ``succeeded`` and the index run to ``indexed``.
        """
        claim = await self._job_runtime.claim_next_job(
            lease_owner=lease_owner,
            lease_duration=lease_duration,
            job_type=ARTICLE_RAG_INDEX_BUILD_JOB_TYPE,
            target_type=ARTICLE_RAG_INDEX_BUILD_TARGET_TYPE,
        )
        if claim is None:
            return None

        if (
            claim.job_type != ARTICLE_RAG_INDEX_BUILD_JOB_TYPE
            or claim.target_type != ARTICLE_RAG_INDEX_BUILD_TARGET_TYPE
        ):
            raise RuntimeError(
                "article RAG index worker claimed unsupported job "
                f"{claim.job_type}/{claim.target_type}"
            )

        if claim.base_id is None:
            # article_rag_index_build is base-scoped per ck_reader_jobs_base_scope.
            # A null base_id means the DB constraint was relaxed or bypassed.
            raise RuntimeError(
                f"article RAG index worker claimed job {claim.job_id} with "
                f"null base_id — this violates ck_reader_jobs_base_scope"
            )

        await self._mark_run_running(claim.run_id)
        return await self._process_claimed_job(
            claim=claim,
            retry_delay=retry_delay,
        )

    # ------------------------------------------------------------------
    # Claimed-job execution
    # ------------------------------------------------------------------

    async def _process_claimed_job(
        self,
        *,
        claim: ClaimResult,
        retry_delay: timedelta,
    ) -> ArticleRagIndexWorkerResult:
        context: _JobContext | None = None

        try:
            context = await self._load_job_context(claim)

            # Phase 1: mark indexing (or detect idempotent no-op).
            idempotent = await self._mark_indexing_or_detect_noop(claim, context)
            if idempotent is not None:
                return idempotent

            # Phase 2: reload plan + validate hash.
            plan, index_run_snapshot = await self._reload_and_validate_plan(
                claim, context
            )

            # Phase 3: embed (outside DB tx).
            embeddings = await self._embedding_provider.embed_texts(
                [chunk.text for chunk in plan.chunks],
            )
            self._validate_embedding_coverage(plan, embeddings)

            # Re-check the publish fence before any vector-store side effect.
            # The embedding call can be slow; if generation/base drifted while
            # it was running, do not upsert stale vectors and then discover the
            # drift only after the external write.
            await self._validate_before_vector_write(
                claim=claim,
                context=context,
                index_run_snapshot=index_run_snapshot,
            )

            # Phase 4: vector write (outside DB tx).
            vector_chunks = self._build_vector_chunks(plan, embeddings)
            write_metadata = ArticleRagVectorWriteMetadata(
                collection=self._default_vector_collection,
                reading_record_id=context.reading_record_id,
                stable_document_id=context.stable_document_id,
                base_id=context.base_id,
                record_generation=context.record_generation,
                index_version=context.index_version,
                chunker_version=context.chunker_version,
                plan_content_sha256=index_run_snapshot["plan_content_sha256"],
                chunk_count=len(plan.chunks),
            )
            write_result = await self._vector_writer.upsert_chunks(
                collection=self._default_vector_collection,
                chunks_with_embeddings=vector_chunks,
                metadata=write_metadata,
            )
            if write_result.upserted_count != len(vector_chunks):
                raise ArticleRagIndexWorkerError(
                    "article RAG vector writer upserted "
                    f"{write_result.upserted_count} chunks for "
                    f"{len(vector_chunks)} planned chunks",
                    retryable=True,
                    failure_class="vector_write",
                    failure_code=FAILURE_CODE_VECTOR_WRITE_FAILED,
                )

            # Phase 5: mark indexed + transition job succeeded (one tx).
            return await self._mark_indexed_and_succeed(
                claim=claim,
                context=context,
                plan=plan,
                index_run_snapshot=index_run_snapshot,
                embedding_model=embeddings[0].model if embeddings else None,
                vector_store_provider=write_result.provider_metadata.get(
                    "provider", "unknown"
                ),
                vector_collection=write_result.collection,
                upserted_count=write_result.upserted_count,
            )

        except FenceViolationError:
            return await self._handle_supersede(
                claim=claim,
                context=context,
                rationale_code="publish_fence_failed",
                failure_class="publish_guard",
                failure_code="publish_fence_failed",
                message="publish fence failed during article RAG index build",
            )
        except ArticleRagIndexPlanError as exc:
            # Plan service detected truth-layer drift (stale generation,
            # inactive base, active base mismatch, no eligible chunks).
            return await self._handle_supersede(
                claim=claim,
                context=context,
                rationale_code="plan_truth_drift",
                failure_class="plan_truth_drift",
                failure_code="plan_truth_drift",
                message=str(exc),
            )
        except ArticleRagIndexWorkerError as exc:
            if exc.retryable:
                return await self._handle_retry_later(
                    claim=claim,
                    context=context,
                    retry_delay=retry_delay,
                    exc=exc,
                )
            return await self._handle_failed_terminal(
                claim=claim,
                context=context,
                exc=exc,
            )
        except _InputJsonError as exc:
            return await self._handle_failed_terminal(
                claim=claim,
                context=context,
                exc=ArticleRagIndexWorkerError(
                    str(exc),
                    retryable=False,
                    failure_class="validation",
                    failure_code=FAILURE_CODE_INPUT_JSON_INVALID,
                ),
            )
        # Retryable DB/runtime exceptions (asyncpg connection errors,
        # deadlocks, serialization failures) are NOT caught here. They
        # propagate so ``recover_stale_leases`` can requeue the job when
        # the lease expires. The index run stays ``indexing`` transiently
        # and is re-entered on the next claim.

    # ------------------------------------------------------------------
    # Phase 1: mark indexing / detect idempotent no-op
    # ------------------------------------------------------------------

    async def _mark_indexing_or_detect_noop(
        self,
        claim: ClaimResult,
        context: _JobContext,
    ) -> ArticleRagIndexWorkerResult | None:
        """Lock index run, validate state, transition to ``indexing``.

        Returns a non-None result if the index run is already ``indexed``
        (idempotent no-op).  Returns ``None`` to continue the normal flow.
        """
        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    SELECT id, status, job_id, base_id, stable_document_id,
                           record_generation, index_version, chunker_version,
                           plan_content_sha256, chunk_count,
                           embedding_model, vector_store_provider,
                           vector_collection
                    FROM reader_article_rag_index_runs
                    WHERE id = $1
                    FOR UPDATE
                    """,
                    context.index_run_id,
                )
                if row is None:
                    raise ArticleRagIndexWorkerError(
                        f"index run {context.index_run_id} not found",
                        retryable=False,
                        failure_class="index_run_state",
                        failure_code=FAILURE_CODE_INDEX_RUN_MISSING,
                    )

                current_status = str(row["status"])

                # Idempotent no-op: already indexed.
                if current_status == "indexed":
                    if row["job_id"] != claim.job_id:
                        raise ArticleRagIndexWorkerError(
                            f"index run {context.index_run_id} is indexed "
                            f"but references job_id={row['job_id']} while "
                            f"current claim is job_id={claim.job_id}",
                            retryable=False,
                            failure_class="index_run_state",
                            failure_code=FAILURE_CODE_INDEX_RUN_WRONG_JOB_ID,
                        )
                    # Verify fields match (defense in depth).
                    self._validate_index_run_fields(row, claim, context)

                    # Transition job to succeeded with rationale
                    # already_indexed (idempotent no-op).
                    await self._transition_job_in_transaction(
                        conn=conn,
                        claim=claim,
                        target_status="succeeded",
                        rationale_code=FAILURE_CODE_ALREADY_INDEXED,
                        output_ref={
                            "idempotent_noop": True,
                            "index_run_id": str(context.index_run_id),
                            "stable_document_id": str(context.stable_document_id),
                            "chunk_count": int(row["chunk_count"]),
                            "embedding_model": row["embedding_model"],
                            "vector_store_provider": row["vector_store_provider"],
                            "vector_collection": row["vector_collection"],
                        },
                    )
                    await self._mark_run_status_in_transaction(
                        conn,
                        claim.run_id,
                        status="completed",
                        failure_class=None,
                        failure_code=None,
                        finished_at=datetime.now(UTC),
                    )

                    return ArticleRagIndexWorkerResult(
                        job_id=claim.job_id,
                        index_run_id=context.index_run_id,
                        reading_record_id=context.reading_record_id,
                        stable_document_id=context.stable_document_id,
                        base_id=context.base_id,
                        status="succeeded",
                        chunk_count=int(row["chunk_count"]),
                        embedding_model=(
                            str(row["embedding_model"])
                            if row["embedding_model"] is not None
                            else None
                        ),
                        vector_store_provider=(
                            str(row["vector_store_provider"])
                            if row["vector_store_provider"] is not None
                            else None
                        ),
                        vector_collection=(
                            str(row["vector_collection"])
                            if row["vector_collection"] is not None
                            else None
                        ),
                        idempotent_noop=True,
                    )

                # Validate status is queued or indexing (re-entry after
                # transient failure).
                if current_status not in ("queued", "indexing"):
                    raise ArticleRagIndexWorkerError(
                        f"index run {context.index_run_id} has unexpected "
                        f"status '{current_status}' (expected queued or "
                        f"indexing)",
                        retryable=False,
                        failure_class="index_run_state",
                        failure_code=FAILURE_CODE_INDEX_RUN_WRONG_STATUS,
                    )

                # Validate job_id linkage.
                if row["job_id"] != claim.job_id:
                    raise ArticleRagIndexWorkerError(
                        f"index run {context.index_run_id} references "
                        f"job_id={row['job_id']} but current claim is "
                        f"job_id={claim.job_id}",
                        retryable=False,
                        failure_class="index_run_state",
                        failure_code=FAILURE_CODE_INDEX_RUN_WRONG_JOB_ID,
                    )

                # Validate fields match the claim + input_json.
                self._validate_index_run_fields(row, claim, context)

                # Transition queued → indexing.
                await conn.execute(
                    """
                    UPDATE reader_article_rag_index_runs
                    SET status = 'indexing', updated_at = NOW()
                    WHERE id = $1
                    """,
                    context.index_run_id,
                )

        return None

    def _validate_index_run_fields(
        self,
        row: asyncpg.Record,
        claim: ClaimResult,
        context: _JobContext,
    ) -> None:
        """Validate that index run fields match claim + input_json."""
        if (
            str(row["base_id"]) != str(context.base_id)
            or str(row["stable_document_id"]) != str(context.stable_document_id)
            or int(row["record_generation"]) != context.record_generation
            or str(row["index_version"]) != context.index_version
            or str(row["chunker_version"]) != context.chunker_version
        ):
            raise ArticleRagIndexWorkerError(
                f"index run {context.index_run_id} fields do not match "
                f"claim / input_json contract",
                retryable=False,
                failure_class="index_run_state",
                failure_code=FAILURE_CODE_INDEX_RUN_FIELD_MISMATCH,
            )

    # ------------------------------------------------------------------
    # Phase 2: reload plan + validate hash
    # ------------------------------------------------------------------

    async def _reload_and_validate_plan(
        self,
        claim: ClaimResult,
        context: _JobContext,
    ) -> tuple[ArticleRagIndexPlan, dict[str, Any]]:
        """Rebuild the plan and validate ``plan_content_sha256``.

        Returns ``(plan, index_run_snapshot)`` where
        ``index_run_snapshot`` includes the persisted
        ``plan_content_sha256`` and ``chunk_count`` for later phases.
        """
        async with self.get_pool().acquire() as conn:
            # Rebuild plan (read-only — no transaction needed, but we
            # use the connection for a consistent snapshot).
            plan = await self._plan_service.build_index_plan_in_transaction(
                conn,
                record_id=context.reading_record_id,
                user_id=context.user_id,
            )

            row = await conn.fetchrow(
                """
                SELECT plan_content_sha256, chunk_count
                FROM reader_article_rag_index_runs
                WHERE id = $1
                """,
                context.index_run_id,
            )
            if row is None:
                raise ArticleRagIndexWorkerError(
                    f"index run {context.index_run_id} disappeared during "
                    f"plan reload",
                    retryable=False,
                    failure_class="index_run_state",
                    failure_code=FAILURE_CODE_INDEX_RUN_MISSING,
                )

            snapshot: dict[str, Any] = {
                "plan_content_sha256": str(row["plan_content_sha256"]),
                "chunk_count": int(row["chunk_count"]),
            }

        rebuilt_hash = compute_plan_content_sha256(plan)
        if rebuilt_hash != snapshot["plan_content_sha256"]:
            raise ArticleRagIndexWorkerError(
                f"plan_content_sha256 mismatch: index run has "
                f"{snapshot['plan_content_sha256']} but rebuilt plan has "
                f"{rebuilt_hash}. The truth layer changed between bootstrap "
                f"and worker execution.",
                retryable=False,
                failure_class="plan_hash_mismatch",
                failure_code=FAILURE_CODE_PLAN_HASH_MISMATCH,
                rationale_code=FAILURE_CODE_PLAN_HASH_MISMATCH,
            )
        if len(plan.chunks) != snapshot["chunk_count"]:
            raise ArticleRagIndexWorkerError(
                f"chunk_count mismatch: index run has "
                f"{snapshot['chunk_count']} but rebuilt plan has "
                f"{len(plan.chunks)} chunks",
                retryable=False,
                failure_class="plan_hash_mismatch",
                failure_code=FAILURE_CODE_PLAN_HASH_MISMATCH,
                rationale_code=FAILURE_CODE_PLAN_HASH_MISMATCH,
            )

        return plan, snapshot

    # ------------------------------------------------------------------
    # Phase 3/4 helpers
    # ------------------------------------------------------------------

    def _validate_embedding_coverage(
        self,
        plan: ArticleRagIndexPlan,
        embeddings: list[ArticleRagEmbedding],
    ) -> None:
        """Verify embedding count + per-chunk text_sha256 coverage."""
        if len(embeddings) != len(plan.chunks):
            raise ArticleRagIndexWorkerError(
                f"embedding provider returned {len(embeddings)} embeddings "
                f"for {len(plan.chunks)} chunks",
                retryable=False,
                failure_class="embedding_coverage",
                failure_code=FAILURE_CODE_EMBEDDING_FAILED,
            )
        for chunk, emb in zip(plan.chunks, embeddings, strict=True):
            if emb.text_sha256 != chunk.embedding_text_sha256:
                raise ArticleRagIndexWorkerError(
                    f"embedding text_sha256 mismatch for chunk "
                    f"{chunk.chunk_id}: provider returned "
                    f"{emb.text_sha256} but plan has "
                    f"{chunk.embedding_text_sha256}",
                    retryable=False,
                    failure_class="embedding_coverage",
                    failure_code=FAILURE_CODE_EMBEDDING_FAILED,
                )

    def _build_vector_chunks(
        self,
        plan: ArticleRagIndexPlan,
        embeddings: list[ArticleRagEmbedding],
    ) -> list[ArticleRagVectorChunk]:
        """Build vector chunks for the writer (no chunk text)."""
        vector_chunks: list[ArticleRagVectorChunk] = []
        for chunk, emb in zip(plan.chunks, embeddings, strict=True):
            citation = chunk.citation
            citation_dict: dict[str, Any] = {
                "reading_record_id": str(citation.reading_record_id),
                "stable_document_id": str(citation.stable_document_id),
                "base_id": str(citation.base_id),
                "record_generation": citation.record_generation,
                "block_ids": list(citation.block_ids),
                "unit_ids": list(citation.unit_ids),
                "anchor_segment_ids": list(citation.anchor_segment_ids),
                "canonical_text_start_utf16": citation.canonical_text_start_utf16,
                "canonical_text_end_utf16": citation.canonical_text_end_utf16,
            }
            vector_chunks.append(
                ArticleRagVectorChunk(
                    chunk_id=chunk.chunk_id,
                    content_sha256=chunk.content_sha256,
                    embedding_text_sha256=chunk.embedding_text_sha256,
                    embedding=emb,
                    citation=citation_dict,
                    metadata=dict(chunk.metadata_json),
                )
            )
        return vector_chunks

    # ------------------------------------------------------------------
    # Phase 4 guard: validate before vector-store side effects
    # ------------------------------------------------------------------

    async def _validate_before_vector_write(
        self,
        *,
        claim: ClaimResult,
        context: _JobContext,
        index_run_snapshot: dict[str, Any],
    ) -> None:
        """Re-lock job/index_run and validate fence before vector upsert."""
        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                index_row = await conn.fetchrow(
                    """
                    SELECT id, status, job_id, base_id, stable_document_id,
                           record_generation, index_version, chunker_version,
                           plan_content_sha256, chunk_count
                    FROM reader_article_rag_index_runs
                    WHERE id = $1
                    FOR UPDATE
                    """,
                    context.index_run_id,
                )
                if index_row is None:
                    raise ArticleRagIndexWorkerError(
                        f"index run {context.index_run_id} disappeared "
                        f"before vector write",
                        retryable=False,
                        failure_class="index_run_state",
                        failure_code=FAILURE_CODE_INDEX_RUN_MISSING,
                    )
                if str(index_row["status"]) != "indexing":
                    raise ArticleRagIndexWorkerError(
                        f"index run {context.index_run_id} status is "
                        f"'{index_row['status']}' (expected indexing) "
                        f"before vector write",
                        retryable=False,
                        failure_class="index_run_state",
                        failure_code=FAILURE_CODE_INDEX_RUN_WRONG_STATUS,
                    )
                if index_row["job_id"] != claim.job_id:
                    raise ArticleRagIndexWorkerError(
                        f"index run {context.index_run_id} job_id mismatch "
                        f"before vector write",
                        retryable=False,
                        failure_class="index_run_state",
                        failure_code=FAILURE_CODE_INDEX_RUN_WRONG_JOB_ID,
                    )
                self._validate_index_run_fields(index_row, claim, context)
                if (
                    str(index_row["plan_content_sha256"])
                    != index_run_snapshot["plan_content_sha256"]
                    or int(index_row["chunk_count"])
                    != int(index_run_snapshot["chunk_count"])
                ):
                    raise ArticleRagIndexWorkerError(
                        f"index run {context.index_run_id} plan snapshot "
                        f"changed before vector write",
                        retryable=False,
                        failure_class="plan_hash_mismatch",
                        failure_code=FAILURE_CODE_PLAN_HASH_MISMATCH,
                        rationale_code=FAILURE_CODE_PLAN_HASH_MISMATCH,
                    )

                job_row = await conn.fetchrow(
                    "SELECT * FROM reader_jobs WHERE id = $1 FOR UPDATE",
                    claim.job_id,
                )
                if job_row is None:
                    raise LookupError(f"reader job {claim.job_id} not found")
                if str(job_row["status"]) != "claimed":
                    raise ValueError(
                        "pre-vector-write validation requires a claimed job"
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

    # ------------------------------------------------------------------
    # Phase 5: mark indexed + transition job succeeded
    # ------------------------------------------------------------------

    async def _mark_indexed_and_succeed(
        self,
        *,
        claim: ClaimResult,
        context: _JobContext,
        plan: ArticleRagIndexPlan,
        index_run_snapshot: dict[str, Any],
        embedding_model: str | None,
        vector_store_provider: str,
        vector_collection: str,
        upserted_count: int,
    ) -> ArticleRagIndexWorkerResult:
        """Final transaction: index run → indexed, job → succeeded."""
        now = datetime.now(UTC)
        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                # Lock index run.
                row = await conn.fetchrow(
                    """
                    SELECT id, status, job_id
                    FROM reader_article_rag_index_runs
                    WHERE id = $1
                    FOR UPDATE
                    """,
                    context.index_run_id,
                )
                if row is None:
                    raise ArticleRagIndexWorkerError(
                        f"index run {context.index_run_id} disappeared "
                        f"during mark-indexed phase",
                        retryable=False,
                        failure_class="index_run_state",
                        failure_code=FAILURE_CODE_INDEX_RUN_MISSING,
                    )
                if str(row["status"]) != "indexing":
                    raise ArticleRagIndexWorkerError(
                        f"index run {context.index_run_id} status is "
                        f"'{row['status']}' (expected indexing) during "
                        f"mark-indexed phase",
                        retryable=False,
                        failure_class="index_run_state",
                        failure_code=FAILURE_CODE_INDEX_RUN_WRONG_STATUS,
                    )
                if row["job_id"] != claim.job_id:
                    raise ArticleRagIndexWorkerError(
                        f"index run {context.index_run_id} job_id mismatch "
                        f"during mark-indexed phase",
                        retryable=False,
                        failure_class="index_run_state",
                        failure_code=FAILURE_CODE_INDEX_RUN_WRONG_JOB_ID,
                    )

                # Lock job row + validate lease.
                job_row = await conn.fetchrow(
                    "SELECT * FROM reader_jobs WHERE id = $1 FOR UPDATE",
                    claim.job_id,
                )
                if job_row is None:
                    raise LookupError(f"reader job {claim.job_id} not found")
                if str(job_row["status"]) != "claimed":
                    raise ValueError(
                        "mark-indexed requires a claimed job"
                    )
                _assert_lease_valid(job_row, claim.job_id, claim.lease_token)

                # Publish fence: verify base/generation still valid.
                fence_error = await self._job_runtime._validate_fence(  # type: ignore[attr-defined]
                    conn, job_row,
                )
                if fence_error is not None:
                    raise FenceViolationError(
                        f"publish fence failed for job {claim.job_id}: "
                        f"{fence_error}"
                    )

                # Transition index run → indexed.
                await conn.execute(
                    """
                    UPDATE reader_article_rag_index_runs
                    SET status = 'indexed',
                        embedding_model = $2,
                        vector_store_provider = $3,
                        vector_collection = $4,
                        completed_at = $5,
                        updated_at = $5
                    WHERE id = $1
                    """,
                    context.index_run_id,
                    embedding_model,
                    vector_store_provider,
                    vector_collection,
                    now,
                )

                # Transition job → succeeded.
                output_ref: dict[str, Any] = {
                    "index_run_id": str(context.index_run_id),
                    "stable_document_id": str(context.stable_document_id),
                    "base_id": str(context.base_id),
                    "record_generation": context.record_generation,
                    "index_version": context.index_version,
                    "chunker_version": context.chunker_version,
                    "chunk_count": len(plan.chunks),
                    "embedding_model": embedding_model,
                    "vector_store_provider": vector_store_provider,
                    "vector_collection": vector_collection,
                    "upserted_count": upserted_count,
                    "plan_content_sha256": index_run_snapshot["plan_content_sha256"],
                }
                await self._transition_job_in_transaction(
                    conn=conn,
                    claim=claim,
                    target_status="succeeded",
                    rationale_code=None,
                    output_ref=output_ref,
                )

                # Transition reader_run → completed.
                await self._mark_run_status_in_transaction(
                    conn,
                    claim.run_id,
                    status="completed",
                    failure_class=None,
                    failure_code=None,
                    finished_at=now,
                )

        return ArticleRagIndexWorkerResult(
            job_id=claim.job_id,
            index_run_id=context.index_run_id,
            reading_record_id=context.reading_record_id,
            stable_document_id=context.stable_document_id,
            base_id=context.base_id,
            status="succeeded",
            chunk_count=len(plan.chunks),
            embedding_model=embedding_model,
            vector_store_provider=vector_store_provider,
            vector_collection=vector_collection,
        )

    # ------------------------------------------------------------------
    # Error handlers
    # ------------------------------------------------------------------

    async def _handle_supersede(
        self,
        *,
        claim: ClaimResult,
        context: _JobContext | None,
        rationale_code: str,
        failure_class: str,
        failure_code: str,
        message: str,
    ) -> ArticleRagIndexWorkerResult:
        """Transition job → superseded, index run → superseded."""
        await self._job_runtime.transition(
            job_id=claim.job_id,
            target_status="superseded",
            lease_token=claim.lease_token,
            rationale_code=rationale_code,
        )
        await self._mark_run_status(
            claim.run_id,
            status="superseded",
            failure_class=failure_class,
            failure_code=failure_code,
            finished_at=datetime.now(UTC),
        )
        if context is not None:
            await self._update_index_run_status(
                context.index_run_id,
                status="superseded",
                error_json={
                    "failure_class": failure_class,
                    "failure_code": failure_code,
                    "rationale_code": rationale_code,
                    "message": message,
                },
            )
        return ArticleRagIndexWorkerResult(
            job_id=claim.job_id,
            index_run_id=context.index_run_id if context else UUID(int=0),
            reading_record_id=claim.reading_record_id,
            stable_document_id=context.stable_document_id if context else UUID(int=0),
            base_id=claim.base_id or UUID(int=0),
            status="superseded",
            chunk_count=0,
            failure_code=failure_code,
        )

    async def _handle_retry_later(
        self,
        *,
        claim: ClaimResult,
        context: _JobContext | None,
        retry_delay: timedelta,
        exc: ArticleRagIndexWorkerError,
    ) -> ArticleRagIndexWorkerResult:
        """Transition job → retry_later, index run → queued."""
        available_at = datetime.now(UTC) + retry_delay
        await self._job_runtime.transition(
            job_id=claim.job_id,
            target_status="retry_later",
            lease_token=claim.lease_token,
            available_at=available_at,
            rationale_code=exc.rationale_code,
        )
        await self._mark_run_status(
            claim.run_id,
            status="failed_retryable",
            failure_class=exc.failure_class,
            failure_code=exc.failure_code,
            finished_at=datetime.now(UTC),
        )
        if context is not None:
            await self._update_index_run_status(
                context.index_run_id,
                status="queued",
                error_json={
                    "failure_class": exc.failure_class,
                    "failure_code": exc.failure_code,
                    "rationale_code": exc.rationale_code,
                    "message": str(exc),
                    "retryable": True,
                },
            )
        return ArticleRagIndexWorkerResult(
            job_id=claim.job_id,
            index_run_id=context.index_run_id if context else UUID(int=0),
            reading_record_id=claim.reading_record_id,
            stable_document_id=context.stable_document_id if context else UUID(int=0),
            base_id=claim.base_id or UUID(int=0),
            status="retry_later",
            chunk_count=0,
            retryable=True,
            failure_code=exc.failure_code,
        )

    async def _handle_failed_terminal(
        self,
        *,
        claim: ClaimResult,
        context: _JobContext | None,
        exc: ArticleRagIndexWorkerError,
    ) -> ArticleRagIndexWorkerResult:
        """Transition job → failed_terminal, index run → failed."""
        await self._job_runtime.transition(
            job_id=claim.job_id,
            target_status="failed_terminal",
            lease_token=claim.lease_token,
            failure_class=exc.failure_class,
            failure_code=exc.failure_code,
            failure_message=str(exc),
            rationale_code=exc.rationale_code,
        )
        await self._mark_run_status(
            claim.run_id,
            status="failed_terminal",
            failure_class=exc.failure_class,
            failure_code=exc.failure_code,
            finished_at=datetime.now(UTC),
        )
        if context is not None:
            await self._update_index_run_status(
                context.index_run_id,
                status="failed",
                error_json={
                    "failure_class": exc.failure_class,
                    "failure_code": exc.failure_code,
                    "rationale_code": exc.rationale_code,
                    "message": str(exc),
                    "retryable": False,
                },
            )
        return ArticleRagIndexWorkerResult(
            job_id=claim.job_id,
            index_run_id=context.index_run_id if context else UUID(int=0),
            reading_record_id=claim.reading_record_id,
            stable_document_id=context.stable_document_id if context else UUID(int=0),
            base_id=claim.base_id or UUID(int=0),
            status="failed_terminal",
            chunk_count=0,
            retryable=False,
            failure_code=exc.failure_code,
        )

    # ------------------------------------------------------------------
    # Job context loading + validation
    # ------------------------------------------------------------------

    async def _load_job_context(self, claim: ClaimResult) -> _JobContext:
        """Load and validate the job payload."""
        async with self.get_pool().acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT input_json, user_id, run_id
                FROM reader_jobs
                WHERE id = $1
                """,
                claim.job_id,
            )
        if row is None:
            raise _InputJsonError(f"reader job {claim.job_id} not found")

        raw_input = row["input_json"]
        if isinstance(raw_input, str):
            try:
                input_json: Any = json.loads(raw_input)
            except (json.JSONDecodeError, TypeError) as exc:
                raise _InputJsonError(
                    f"job {claim.job_id} input_json is not valid JSON"
                ) from exc
        elif isinstance(raw_input, Mapping):
            input_json = dict(raw_input)
        else:
            raise _InputJsonError(
                f"job {claim.job_id} input_json is not a JSON object"
            )

        # source tag
        source = input_json.get("source")
        if source != ARTICLE_RAG_INDEX_JOB_SOURCE:
            raise _InputJsonError(
                f"job {claim.job_id} input_json.source is '{source}' "
                f"(expected '{ARTICLE_RAG_INDEX_JOB_SOURCE}')"
            )

        # Required fields.
        required_str_fields = (
            "reading_record_id",
            "stable_document_id",
            "base_id",
            "index_run_id",
        )
        for field_name in required_str_fields:
            val = input_json.get(field_name)
            if not isinstance(val, str) or not val:
                raise _InputJsonError(
                    f"job {claim.job_id} input_json.{field_name} is missing "
                    f"or not a non-empty string"
                )

        # index_version / chunker_version must be non-empty strings.
        for field_name in ("index_version", "chunker_version"):
            val = input_json.get(field_name)
            if not isinstance(val, str) or not val:
                raise _InputJsonError(
                    f"job {claim.job_id} input_json.{field_name} is missing "
                    f"or not a non-empty string"
                )

        # record_generation must be a positive int.
        gen_raw = input_json.get("record_generation")
        if not isinstance(gen_raw, int) or gen_raw < 1:
            raise _InputJsonError(
                f"job {claim.job_id} input_json.record_generation is "
                f"{gen_raw!r} (expected int >= 1)"
            )

        # Parse UUIDs.
        try:
            payload_record_id = UUID(input_json["reading_record_id"])
            payload_stable_doc_id = UUID(input_json["stable_document_id"])
            payload_base_id = UUID(input_json["base_id"])
            payload_index_run_id = UUID(input_json["index_run_id"])
        except (ValueError, TypeError) as exc:
            raise _InputJsonError(
                f"job {claim.job_id} input_json contains invalid UUIDs"
            ) from exc

        # Cross-validate with claim.
        if payload_record_id != claim.reading_record_id:
            raise _InputJsonError(
                f"job {claim.job_id} input_json.reading_record_id "
                f"{payload_record_id} does not match claim "
                f"{claim.reading_record_id}"
            )
        if claim.base_id is None or payload_base_id != claim.base_id:
            raise _InputJsonError(
                f"job {claim.job_id} input_json.base_id {payload_base_id} "
                f"does not match claim base_id {claim.base_id}"
            )
        if gen_raw != claim.expected_generation:
            raise _InputJsonError(
                f"job {claim.job_id} input_json.record_generation {gen_raw} "
                f"does not match claim expected_generation "
                f"{claim.expected_generation}"
            )

        # target_key must equal stable_document_id.
        try:
            target_key_uuid = UUID(claim.target_key)
        except (ValueError, TypeError) as exc:
            raise _InputJsonError(
                f"job {claim.job_id} target_key '{claim.target_key}' is "
                f"not a valid UUID"
            ) from exc
        if target_key_uuid != payload_stable_doc_id:
            raise _InputJsonError(
                f"job {claim.job_id} target_key {target_key_uuid} does not "
                f"match input_json.stable_document_id "
                f"{payload_stable_doc_id}"
            )

        return _JobContext(
            job_id=claim.job_id,
            run_id=claim.run_id,
            reading_record_id=payload_record_id,
            user_id=UUID(str(row["user_id"])),
            stable_document_id=payload_stable_doc_id,
            base_id=payload_base_id,
            record_generation=gen_raw,
            index_run_id=payload_index_run_id,
            index_version=str(input_json["index_version"]),
            chunker_version=str(input_json["chunker_version"]),
        )

    # ------------------------------------------------------------------
    # Low-level DB helpers
    # ------------------------------------------------------------------

    async def _transition_job_in_transaction(
        self,
        *,
        conn: asyncpg.Connection,
        claim: ClaimResult,
        target_status: str,
        rationale_code: str | None,
        output_ref: dict[str, Any] | None,
    ) -> None:
        """Transition job within the caller's transaction."""
        job_row = await conn.fetchrow(
            "SELECT * FROM reader_jobs WHERE id = $1 FOR UPDATE",
            claim.job_id,
        )
        if job_row is None:
            raise LookupError(f"reader job {claim.job_id} not found")
        if str(job_row["status"]) != "claimed":
            raise ValueError(
                f"job {claim.job_id} status is '{job_row['status']}' "
                f"(expected claimed) for in-transaction transition to "
                f"{target_status}"
            )
        _assert_lease_valid(job_row, claim.job_id, claim.lease_token)

        # For succeeded, validate publish fence.
        if target_status == "succeeded":
            fence_error = await self._job_runtime._validate_fence(  # type: ignore[attr-defined]
                conn, job_row,
            )
            if fence_error is not None:
                raise FenceViolationError(
                    f"publish fence failed for job {claim.job_id}: "
                    f"{fence_error}"
                )

        updated = await self._job_runtime._apply_transition(  # type: ignore[attr-defined]
            conn,
            job_row=job_row,
            target_status=target_status,
            available_at=None,
            pause_owner=None,
            output_ref=output_ref,
            failure_class=None,
            failure_code=None,
            failure_message=None,
            rationale_code=rationale_code,
        )
        event_type = self._event_type_for_target(target_status)
        await self._job_runtime._insert_job_event(  # type: ignore[attr-defined]
            conn,
            reading_record_id=updated["reading_record_id"],
            run_id=updated["run_id"],
            job_id=updated["id"],
            event_type=event_type,
            payload={
                "previous_status": "claimed",
                "target_status": target_status,
                **({"rationale_code": rationale_code} if rationale_code else {}),
                **({"output_ref": output_ref} if output_ref else {}),
            },
        )

    @staticmethod
    def _event_type_for_target(target_status: str) -> str:
        mapping = {
            "succeeded": "job_succeeded",
            "failed_terminal": "job_failed_terminal",
            "retry_later": "job_retry_later",
            "superseded": "job_superseded",
            "cancelled": "job_cancelled",
            "skipped": "job_skipped",
            "paused": "job_paused",
            "queued": "job_requeued",
        }
        return mapping.get(target_status, "job_transitioned")

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
            await self._mark_run_status_in_transaction(
                conn,
                run_id,
                status=status,
                failure_class=failure_class,
                failure_code=failure_code,
                finished_at=finished_at,
            )

    async def _mark_run_status_in_transaction(
        self,
        conn: asyncpg.Connection,
        run_id: UUID,
        *,
        status: str,
        failure_class: str | None,
        failure_code: str | None,
        finished_at: datetime | None,
    ) -> None:
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

    async def _update_index_run_status(
        self,
        index_run_id: UUID,
        *,
        status: str,
        error_json: dict[str, Any],
    ) -> None:
        """Update index run status + error_json (outside any transaction)."""
        async with self.get_pool().acquire() as conn:
            await conn.execute(
                """
                UPDATE reader_article_rag_index_runs
                SET status = $2,
                    error_json = $3::jsonb,
                    completed_at = CASE
                        WHEN $2 IN ('failed', 'superseded')
                            THEN COALESCE(completed_at, NOW())
                        WHEN $2 = 'queued'
                            THEN NULL
                        ELSE completed_at
                    END,
                    updated_at = NOW()
                WHERE id = $1
                """,
                index_run_id,
                status,
                jsonb_param(error_json),
            )
