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
import re
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
    compute_article_rag_index_build_input_hash,
)
from .article_rag_index_plan import (
    ArticleRagIndexPlan,
    ArticleRagIndexPlanError,
    ArticleRagIndexPlanService,
    compute_plan_content_sha256,
)
from .article_rag_index_profile import (
    ArticleRagIndexProfileResolutionError,
    resolve_article_rag_index_profile,
)
from .job_runtime import (
    ClaimResult,
    FenceViolationError,
    ReaderJobRuntime,
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

# P1-D: Worker frozen profile validation failure codes.
# All three are retryable=False, failed_terminal, fixed safe message.
# They MUST NOT interpolate caller-supplied values (expected/persisted/caller).
FAILURE_CODE_INDEX_PROFILE_INVALID = "index_profile_invalid"
FAILURE_CODE_INDEX_PROFILE_MISMATCH = "index_profile_mismatch"
FAILURE_CODE_JOB_INPUT_HASH_MISMATCH = "job_input_hash_mismatch"
# P1-D-R1: cardinality check on reader_article_rag_index_runs.job_id.
# There is NO unique constraint on job_id, so 0 / multiple / id-mismatched
# linkages all fail-closed with this fixed safe code.
FAILURE_CODE_INDEX_RUN_LINK_INVALID = "index_run_link_invalid"

# Fixed safe error messages for P1-D validation failures.  These strings
# are intentionally generic and free of any caller-supplied value so a
# malicious payload cannot leak through error surfaces.
_P1D_MSG_PROFILE_NOT_REGISTERED = (
    "Article RAG index profile is not registered"
)
_P1D_MSG_FINGERPRINT_MISSING_OR_MALFORMED = (
    "Article RAG index profile fingerprint is missing or malformed"
)
_P1D_MSG_FINGERPRINT_MISMATCH = (
    "Article RAG index profile fingerprint does not match the resolved profile"
)
_P1D_MSG_CHUNKER_MISMATCH = (
    "Article RAG index plan chunker_version does not match the resolved profile"
)
_P1D_MSG_INPUT_HASH_MISMATCH = (
    "Article RAG index job input_hash does not match the canonical algorithm"
)
_P1D_MSG_INDEX_RUN_FINGERPRINT_MISMATCH = (
    "Article RAG index run profile_fingerprint does not match the resolved profile"
)
_P1D_MSG_INDEX_RUN_FINGERPRINT_DRIFTED = (
    "Article RAG index run profile_fingerprint drifted before vector write"
)
# P1-D-R1: cardinality / linkage failure message.  Must NOT echo job_id,
# index_run_id, row count, fingerprint/hash, payload, URI/key/sentinel.
_P1D_MSG_INDEX_RUN_LINK_INVALID = (
    "Article RAG index job link to index run is not resolvable"
)

# Canonical SHA-256 shape: 64-character lowercase hex string.
# Used by _validate_canonical_sha256 to reject malformed fingerprints
# and hashes without echoing the offending value.
# P1-D-R1: re.fullmatch (not re.match + $) — Python ``$`` accepts a
# single trailing newline, which would let ``"a"*64 + "\n"`` pass.
# Trailing LF/CRLF is malformed (not a mismatch).
_SHA256_HEX_PATTERN = re.compile(r"[0-9a-f]{64}")


# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------


class ArticleRagIndexWorkerError(RuntimeError):
    """Typed error for worker failures with safe, structured diagnostics."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        failure_class: str,
        failure_code: str,
        rationale_code: str | None = None,
        diagnostics: Mapping[str, str | int | bool | None] | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.failure_class = failure_class
        self.failure_code = failure_code
        self.rationale_code = rationale_code or failure_code
        self.diagnostics = dict(diagnostics or {})


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
    # P1-D: canonical profile_fingerprint carried as diagnostic identity.
    # Does NOT change collection routing in V1; downstream retrieval /
    # citation truth is unaffected by this field.
    # P1-D-R1: required (no default).  Omitting it must raise TypeError
    # at construction time so callers cannot accidentally write a vector
    # without identity provenance.
    profile_fingerprint: str


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
    """Validated job payload.

    P1-D: ``profile_fingerprint`` is the canonical SHA-256 of the
    resolved ArticleRagIndexProfile, validated against the resolver
    and the persisted ``reader_jobs.input_hash``.  It is the trust
    basis for all downstream index-run + pre-vector-write guards.
    """

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
    profile_fingerprint: str


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
                profile_fingerprint=context.profile_fingerprint,
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
                           profile_fingerprint,
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
                    await self._job_runtime.transition_in_transaction(
                        conn,
                        job_id=claim.job_id,
                        target_status="succeeded",
                        lease_token=claim.lease_token,
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
        """Validate that index run fields match claim + input_json.

        P1-D: also validates ``profile_fingerprint`` against the
        ``_JobContext.profile_fingerprint`` (which was itself validated
        against the P1-B resolver).  A mismatch here means the persisted
        index-run row has drifted from the bootstrap-frozen identity
        and must fail-closed with ``index_profile_mismatch``.
        """
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
        # P1-D: persisted profile_fingerprint must precisely equal the
        # context fingerprint (already validated against the resolver).
        # The persisted value is NOT used as a trust source here — only
        # as a drift detector.  Fixed safe message; no echo of expected
        # or persisted value.
        persisted_fp = row["profile_fingerprint"]
        if not isinstance(persisted_fp, str) or persisted_fp != context.profile_fingerprint:
            raise ArticleRagIndexWorkerError(
                _P1D_MSG_INDEX_RUN_FINGERPRINT_MISMATCH,
                retryable=False,
                failure_class="index_profile_mismatch",
                failure_code=FAILURE_CODE_INDEX_PROFILE_MISMATCH,
                rationale_code=FAILURE_CODE_INDEX_PROFILE_MISMATCH,
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
            # P1-E: worker explicitly passes its P1-D-validated
            # ``context.index_version`` to the plan service so the
            # plan / chunker identity is derived from the same frozen
            # profile that was validated at job-claim time.  The plan
            # service re-resolves the profile through its own dispatch
            # seam; an already-queued V1 job's plan interpretation
            # stays frozen because the V1 profile is immutable.
            plan = await self._plan_service.build_index_plan_in_transaction(
                conn,
                record_id=context.reading_record_id,
                user_id=context.user_id,
                index_version=context.index_version,
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
        """Re-lock job/index_run and validate fence before vector upsert.

        The job's status / lease / expiry / fence are validated exclusively
        by the public ``validate_claim_in_transaction`` seam — this method
        does NOT duplicate that logic. Only Article RAG-owned state
        (index_run row) is checked separately here.
        """
        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                index_row = await conn.fetchrow(
                    """
                    SELECT id, status, job_id, base_id, stable_document_id,
                           record_generation, index_version, chunker_version,
                           profile_fingerprint,
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
                # P1-D: pre-vector-write TOCTOU fingerprint drift guard.
                # The persisted profile_fingerprint may have drifted
                # during the (slow) embedding call.  Re-validate against
                # the context fingerprint (which was itself validated
                # against the resolver).  Fixed safe message; no echo of
                # expected or persisted value.
                persisted_fp_before_write = index_row["profile_fingerprint"]
                if (
                    not isinstance(persisted_fp_before_write, str)
                    or persisted_fp_before_write != context.profile_fingerprint
                ):
                    raise ArticleRagIndexWorkerError(
                        _P1D_MSG_INDEX_RUN_FINGERPRINT_DRIFTED,
                        retryable=False,
                        failure_class="index_profile_mismatch",
                        failure_code=FAILURE_CODE_INDEX_PROFILE_MISMATCH,
                        rationale_code=FAILURE_CODE_INDEX_PROFILE_MISMATCH,
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

                # Single source of truth for job status / lease / expiry /
                # fence validation. Locks reader_jobs FOR UPDATE, validates
                # status='claimed' + lease token + lease expiry + publish
                # fence. Raises LeaseTokenMismatchError / LeaseExpiredError /
                # IllegalTransitionError / FenceViolationError on failure;
                # no partial mutation or event is written.
                await self._job_runtime.validate_claim_in_transaction(
                    conn,
                    job_id=claim.job_id,
                    lease_token=claim.lease_token,
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

                # Lock job row + validate lease + publish fence (public seam).
                # The actual transition to succeeded happens below via
                # transition_in_transaction, which re-validates lease + fence
                # atomically. Here we only need the index_run lock + state
                # check; the job transition is fully owned by the runtime seam.

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
                await self._job_runtime.transition_in_transaction(
                    conn,
                    job_id=claim.job_id,
                    target_status="succeeded",
                    lease_token=claim.lease_token,
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
        """Atomically retry-or-terminalize via the public runtime seam.

        Opens a caller-owned transaction and delegates the retry-vs-terminal
        decision to ``transition_retryable_failure_in_transaction``, which
        reads ``attempt_count``/``max_attempts`` inside the DB lock. The
        worker then updates ``reader_runs`` and ``reader_article_rag_index_runs``
        in the same transaction based on the returned ``JobSnapshot.status``.
        """
        output_ref = {"diagnostics": exc.diagnostics} if exc.diagnostics else None
        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                snapshot = await self._job_runtime.transition_retryable_failure_in_transaction(
                    conn,
                    job_id=claim.job_id,
                    lease_token=claim.lease_token,
                    retry_delay=retry_delay,
                    failure_class=exc.failure_class,
                    failure_code=exc.failure_code,
                    failure_message=str(exc),
                    rationale_code=exc.rationale_code,
                    output_ref=output_ref,
                )

                if snapshot.status == "failed_terminal":
                    run_status = "failed_terminal"
                    index_run_status = "failed"
                    index_run_error_json = self._error_json(
                        exc,
                        retryable=False,
                        rationale_code="max_attempts_exceeded",
                        retry_exhausted=True,
                    )
                    result_status = "failed_terminal"
                    result_retryable = False
                else:
                    run_status = "failed_retryable"
                    index_run_status = "queued"
                    index_run_error_json = self._error_json(exc, retryable=True)
                    result_status = "retry_later"
                    result_retryable = True

                await self._mark_run_status_in_transaction(
                    conn,
                    claim.run_id,
                    status=run_status,
                    failure_class=exc.failure_class,
                    failure_code=exc.failure_code,
                    finished_at=datetime.now(UTC),
                )
                if context is not None:
                    await conn.execute(
                        """
                        UPDATE reader_article_rag_index_runs
                        SET status = $2,
                            error_json = $3::jsonb,
                            completed_at = CASE WHEN $2 = 'failed'
                                THEN COALESCE(completed_at, NOW()) ELSE completed_at END,
                            updated_at = NOW()
                        WHERE id = $1
                        """,
                        context.index_run_id,
                        index_run_status,
                        jsonb_param(index_run_error_json),
                    )

        return ArticleRagIndexWorkerResult(
            job_id=claim.job_id,
            index_run_id=context.index_run_id if context else UUID(int=0),
            reading_record_id=claim.reading_record_id,
            stable_document_id=context.stable_document_id if context else UUID(int=0),
            base_id=claim.base_id or UUID(int=0),
            status=result_status,
            chunk_count=0,
            retryable=result_retryable,
            failure_code=exc.failure_code,
        )

    @staticmethod
    def _error_json(
        exc: ArticleRagIndexWorkerError,
        *,
        retryable: bool,
        rationale_code: str | None = None,
        retry_exhausted: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "failure_class": exc.failure_class,
            "failure_code": exc.failure_code,
            "rationale_code": rationale_code or exc.rationale_code,
            "message": str(exc),
            "retryable": retryable,
        }
        if exc.diagnostics:
            payload["diagnostics"] = exc.diagnostics
        if retry_exhausted:
            payload["retry_exhausted"] = True
        return payload

    async def _handle_failed_terminal(
        self,
        *,
        claim: ClaimResult,
        context: _JobContext | None,
        exc: ArticleRagIndexWorkerError,
    ) -> ArticleRagIndexWorkerResult:
        """Atomically transition terminal state across three tables.

        Transitions:
          * reader job → ``failed_terminal``
          * reader run → ``failed_terminal``
          * Article RAG index run → ``failed``

        All three mutations run in a single caller-owned transaction so a
        failure at any step rolls back the entire terminal path. No reader
        representation event is published; ``article_ready`` and the reader
        truth layer (base / Unit / anchor / stable document) are untouched.

        P1-D: when ``context is None`` (validation failed before
        :meth:`_load_job_context` could return), the handler cannot
        trust ``input_json.index_run_id``.  Instead it looks up the
        linked index-run via the trusted DB relationship
        ``reader_article_rag_index_runs.job_id = claim.job_id``.  If no
        linked row exists, the handler terminalizes only the job + run
        and does NOT touch any other index-run row (preventing a
        malicious payload from disturbing unrelated state).
        """
        output_ref = {"diagnostics": exc.diagnostics} if exc.diagnostics else None
        error_json = self._error_json(exc, retryable=False)
        finished_at = datetime.now(UTC)
        linked_index_run_id: UUID | None = None
        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                await self._job_runtime.transition_in_transaction(
                    conn,
                    job_id=claim.job_id,
                    target_status="failed_terminal",
                    lease_token=claim.lease_token,
                    output_ref=output_ref,
                    failure_class=exc.failure_class,
                    failure_code=exc.failure_code,
                    failure_message=str(exc),
                    rationale_code=exc.rationale_code,
                )
                await self._mark_run_status_in_transaction(
                    conn,
                    claim.run_id,
                    status="failed_terminal",
                    failure_class=exc.failure_class,
                    failure_code=exc.failure_code,
                    finished_at=finished_at,
                )
                if context is not None:
                    linked_index_run_id = context.index_run_id
                    await self._update_index_run_status_in_transaction(
                        conn,
                        linked_index_run_id,
                        status="failed",
                        error_json=error_json,
                    )
                else:
                    # P1-D-R1: context=None — do NOT trust the potentially
                    # corrupt ``input_json.index_run_id``.  Look up the
                    # linked index-run via the trusted DB relationship
                    # ``reader_article_rag_index_runs.job_id = claim.job_id``
                    # and lock the current candidate set FOR UPDATE so
                    # existing rows cannot drift while this transaction is
                    # open.  PostgreSQL row locks do not prevent a new row
                    # with the same job_id from being inserted; normal
                    # writers must therefore continue treating job_id as a
                    # single-owner link.
                    #
                    # ``job_id`` has NO unique constraint, so a fetchrow
                    # would arbitrarily pick one row.  Cardinality rules:
                    #   - exactly 1 row → mark that single index-run failed
                    #   - 0 rows         → terminalize only job + run
                    #   - >1 rows        → terminalize only job + run,
                    #                       leave ALL candidate index-run
                    #                       rows untouched for human repair
                    # In all cases the transaction commits atomically —
                    # no partial commit is permitted.
                    linked_rows = await conn.fetch(
                        """
                        SELECT id
                        FROM reader_article_rag_index_runs
                        WHERE job_id = $1
                        FOR UPDATE
                        """,
                        claim.job_id,
                    )
                    if len(linked_rows) == 1:
                        linked_index_run_id = UUID(str(linked_rows[0]["id"]))
                        await self._update_index_run_status_in_transaction(
                            conn,
                            linked_index_run_id,
                            status="failed",
                            error_json=error_json,
                        )
                    # 0 or >1 rows: linked_index_run_id stays None; no
                    # arbitrary index-run is updated.  The job + run
                    # terminalization above is already staged in this same
                    # transaction and commits atomically on context exit.
        return ArticleRagIndexWorkerResult(
            job_id=claim.job_id,
            index_run_id=(
                linked_index_run_id
                if linked_index_run_id is not None
                else (context.index_run_id if context else UUID(int=0))
            ),
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
        """Load and validate the job payload.

        P1-D: validates the bootstrap-frozen profile identity before
        returning the context.  Validation layers (in order):

          1. Basic JSON object + IDs + record_generation (existing).
          2. ``profile_fingerprint`` is a non-empty string with the
             canonical SHA-256 shape (64-char lowercase hex).
          3. ``index_version`` resolves via the P1-B public resolver
             (no fallback to default, no runtime override).
          4. ``profile_fingerprint`` precisely equals
             ``resolution.profile_fingerprint`` for the resolved
             ``index_version`` (no shape-only check).
          5. ``chunker_version`` precisely equals
             ``resolution.profile.chunker_version``.
          6. ``reader_jobs.input_hash`` precisely equals the canonical
             P1-C hash computed via the public bootstrap seam
             ``compute_article_rag_index_build_input_hash``.

        All P1-D failures raise :class:`ArticleRagIndexWorkerError`
        directly (retryable=False) with one of the new failure codes
        ``index_profile_invalid`` / ``index_profile_mismatch`` /
        ``job_input_hash_mismatch`` and a fixed safe message that does
        not echo any caller-supplied value.
        """
        async with self.get_pool().acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT input_json, input_hash, user_id, run_id
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

        # -----------------------------------------------------------------
        # P1-D: Worker frozen profile validation.
        # -----------------------------------------------------------------

        # Layer 2: profile_fingerprint must be a non-empty str with the
        # canonical SHA-256 shape.  Reject None / bool / int / empty /
        # whitespace / wrong length / uppercase / non-hex / key-like /
        # URI / unicode / newline / sentinel without echoing the value.
        payload_fingerprint = input_json.get("profile_fingerprint")
        if not isinstance(payload_fingerprint, str) or not payload_fingerprint:
            raise ArticleRagIndexWorkerError(
                _P1D_MSG_FINGERPRINT_MISSING_OR_MALFORMED,
                retryable=False,
                failure_class="index_profile_invalid",
                failure_code=FAILURE_CODE_INDEX_PROFILE_INVALID,
                rationale_code=FAILURE_CODE_INDEX_PROFILE_INVALID,
            )
        if _SHA256_HEX_PATTERN.fullmatch(payload_fingerprint) is None:
            raise ArticleRagIndexWorkerError(
                _P1D_MSG_FINGERPRINT_MISSING_OR_MALFORMED,
                retryable=False,
                failure_class="index_profile_invalid",
                failure_code=FAILURE_CODE_INDEX_PROFILE_INVALID,
                rationale_code=FAILURE_CODE_INDEX_PROFILE_INVALID,
            )

        # Layer 3: index_version must resolve via the P1-B public resolver.
        # The resolver fail-closes on unknown/blank/whitespace/malicious
        # versions with a fixed local message that does not echo input.
        payload_index_version = str(input_json["index_version"])
        # P1-D-R1: construct the outer error inside the except block but
        # raise it OUTSIDE, so both __cause__ and __context__ are None.
        # We do NOT copy the resolver exception's type / message / repr /
        # args; only the fixed local safe message is preserved.
        resolution_error_to_raise: ArticleRagIndexWorkerError | None = None
        try:
            resolution = resolve_article_rag_index_profile(payload_index_version)
        except ArticleRagIndexProfileResolutionError:
            resolution_error_to_raise = ArticleRagIndexWorkerError(
                _P1D_MSG_PROFILE_NOT_REGISTERED,
                retryable=False,
                failure_class="index_profile_invalid",
                failure_code=FAILURE_CODE_INDEX_PROFILE_INVALID,
                rationale_code=FAILURE_CODE_INDEX_PROFILE_INVALID,
            )
        if resolution_error_to_raise is not None:
            raise resolution_error_to_raise

        # Layer 4: payload fingerprint must precisely equal the resolver
        # fingerprint.  A format-valid but wrong fingerprint (e.g.
        # "a" * 64) must fail-closed — shape-only check is not enough.
        if payload_fingerprint != resolution.profile_fingerprint:
            raise ArticleRagIndexWorkerError(
                _P1D_MSG_FINGERPRINT_MISMATCH,
                retryable=False,
                failure_class="index_profile_mismatch",
                failure_code=FAILURE_CODE_INDEX_PROFILE_MISMATCH,
                rationale_code=FAILURE_CODE_INDEX_PROFILE_MISMATCH,
            )

        # Layer 5: chunker_version must precisely equal the resolved
        # profile's chunker_version.  A valid fingerprint but wrong
        # chunker_version must fail-closed with a fixed safe message.
        payload_chunker_version = str(input_json["chunker_version"])
        if payload_chunker_version != resolution.profile.chunker_version:
            raise ArticleRagIndexWorkerError(
                _P1D_MSG_CHUNKER_MISMATCH,
                retryable=False,
                failure_class="index_profile_invalid",
                failure_code=FAILURE_CODE_INDEX_PROFILE_INVALID,
                rationale_code=FAILURE_CODE_INDEX_PROFILE_INVALID,
            )

        # Layer 6: reader_jobs.input_hash must precisely equal the
        # canonical P1-C hash computed via the public bootstrap seam.
        # The worker must NOT duplicate the hash algorithm; it must
        # call ``compute_article_rag_index_build_input_hash`` so a
        # future algorithm change has a single source of truth.
        #
        # The hash covers (stable_document_id, base_id, plan_content_sha256,
        # index_version, profile_fingerprint).  ``plan_content_sha256`` is
        # NOT in input_json; the worker must recover it from a trusted
        # source.  We load the linked index-run row via the trusted DB
        # relationship ``reader_article_rag_index_runs.job_id = claim.job_id``
        # (NOT via the potentially corrupt ``input_json.index_run_id``).
        # If no linked row exists, the input_hash cannot be validated and
        # the job must fail-closed.
        persisted_input_hash = row["input_hash"]
        if (
            not isinstance(persisted_input_hash, str)
            or not persisted_input_hash
            or _SHA256_HEX_PATTERN.fullmatch(persisted_input_hash) is None
        ):
            raise ArticleRagIndexWorkerError(
                _P1D_MSG_INPUT_HASH_MISMATCH,
                retryable=False,
                failure_class="job_input_hash_mismatch",
                failure_code=FAILURE_CODE_JOB_INPUT_HASH_MISMATCH,
                rationale_code=FAILURE_CODE_JOB_INPUT_HASH_MISMATCH,
            )

        # Trusted lookup: find the linked index-run by job_id, not by
        # input_json.index_run_id.  This is the same relationship the
        # context=None terminalization path uses (see _handle_failed_terminal).
        #
        # P1-D-R1: ``reader_article_rag_index_runs.job_id`` has NO unique
        # constraint, so a ``fetchrow`` would arbitrarily pick one row.
        # We must read the FULL candidate set and verify cardinality:
        #   - exactly 1 row, AND its id == payload_index_run_id → proceed
        #   - 0 rows                                                  → fail-closed
        #   - >1 rows                                                  → fail-closed
        #   - 1 row but id != payload_index_run_id                     → fail-closed
        # All four failure modes use ``index_run_link_invalid`` with a
        # fixed safe message that echoes no caller-supplied value.
        async with self.get_pool().acquire() as conn:
            linked_index_run_rows = await conn.fetch(
                """
                SELECT id, plan_content_sha256
                FROM reader_article_rag_index_runs
                WHERE job_id = $1
                """,
                claim.job_id,
            )
        if (
            len(linked_index_run_rows) != 1
            or UUID(str(linked_index_run_rows[0]["id"])) != payload_index_run_id
        ):
            raise ArticleRagIndexWorkerError(
                _P1D_MSG_INDEX_RUN_LINK_INVALID,
                retryable=False,
                failure_class="index_run_link_invalid",
                failure_code=FAILURE_CODE_INDEX_RUN_LINK_INVALID,
                rationale_code=FAILURE_CODE_INDEX_RUN_LINK_INVALID,
            )
        trusted_plan_content_sha256 = str(
            linked_index_run_rows[0]["plan_content_sha256"]
        )
        expected_input_hash = compute_article_rag_index_build_input_hash(
            stable_document_id=payload_stable_doc_id,
            base_id=payload_base_id,
            plan_content_sha256=trusted_plan_content_sha256,
            index_version=payload_index_version,
            profile_fingerprint=payload_fingerprint,
        )
        if persisted_input_hash != expected_input_hash:
            raise ArticleRagIndexWorkerError(
                _P1D_MSG_INPUT_HASH_MISMATCH,
                retryable=False,
                failure_class="job_input_hash_mismatch",
                failure_code=FAILURE_CODE_JOB_INPUT_HASH_MISMATCH,
                rationale_code=FAILURE_CODE_JOB_INPUT_HASH_MISMATCH,
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
            index_version=payload_index_version,
            chunker_version=payload_chunker_version,
            profile_fingerprint=payload_fingerprint,
        )

    # ------------------------------------------------------------------
    # Low-level DB helpers
    # ------------------------------------------------------------------

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
            await self._update_index_run_status_in_transaction(
                conn,
                index_run_id,
                status=status,
                error_json=error_json,
            )

    async def _update_index_run_status_in_transaction(
        self,
        conn: asyncpg.Connection,
        index_run_id: UUID,
        *,
        status: str,
        error_json: dict[str, Any],
    ) -> None:
        """Update index run status + error_json inside the caller's transaction.

        Counterpart to :meth:`_update_index_run_status` for the atomic
        terminal path. The caller owns the commit/rollback.
        """
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
