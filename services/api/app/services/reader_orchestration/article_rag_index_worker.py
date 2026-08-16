"""Article RAG Index Worker Foundation.

Claims ``article_rag_index_build`` reader_jobs (enqueued by the index bootstrap
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
  * Initial DB transaction: lock ``reader_article_rag_index_runs`` FOR UPDATE,
    validate state, transition ``queued``/``indexing`` → ``indexing``.
  * Read-only DB transaction: rebuild the plan via
    ``ArticleRagIndexPlanService.build_index_plan_in_transaction``,
    validate ``plan_content_sha256`` + ``chunk_count``.
  * Outside DB: call embedding provider with chunk texts.
  * Outside DB: call vector writer with chunks + embeddings
    (no chunk text — only hashes + citation metadata).
  * Final DB transaction: lock index run FOR UPDATE again, re-validate
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

from app.contracts.article_rag_contract import ARTICLE_RAG_EMBEDDING_CONTRACT
from app.database import connection as db_connection
from app.database.json_compat import jsonb_param

from .advisory_lock import (
    LOCK_NAMESPACE_VECTOR_MUTATION,
    SessionAdvisoryLock,
    advisory_lock_key,
)
from .article_rag_index_bootstrap import (
    ARTICLE_RAG_INDEX_BUILD_JOB_TYPE,
    ARTICLE_RAG_INDEX_BUILD_RUN_TYPE,
    ARTICLE_RAG_INDEX_BUILD_TARGET_TYPE,
    compute_article_rag_index_build_input_hash,
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
    mark_reader_run_running,
    mark_reader_run_status,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_ARTICLE_RAG_INDEX_LEASE_DURATION = timedelta(seconds=60)
DEFAULT_ARTICLE_RAG_INDEX_RETRY_DELAY = timedelta(minutes=2)
ARTICLE_RAG_INDEX_WORKER_VERSION = "article-rag-index-worker"

# Job payload source tag — must match the index bootstrap.
ARTICLE_RAG_INDEX_JOB_SOURCE = "article_rag_index_bootstrap"

# Default vector store metadata (fake — no real Zilliz/Milvus connection).
# The fake embedding provider's defaults match the frozen
# ArticleRagEmbeddingContract so the worker's contract enforcement
# accepts fake embeddings out of the box without each test having to
# opt in.  The fake remains obviously-named via
# ``DEFAULT_FAKE_VECTOR_STORE_PROVIDER``; only the model/dim/collection
# values are aligned with the contract so the frozen vector-space
# contract is satisfiable in test fixtures.
DEFAULT_FAKE_EMBEDDING_MODEL = ARTICLE_RAG_EMBEDDING_CONTRACT.document_embedding_model
DEFAULT_FAKE_VECTOR_STORE_PROVIDER = "fake-in-memory"
DEFAULT_FAKE_VECTOR_COLLECTION = ARTICLE_RAG_EMBEDDING_CONTRACT.vector_collection
DEFAULT_FAKE_EMBEDDING_DIM = ARTICLE_RAG_EMBEDDING_CONTRACT.document_embedding_dimension

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

# Worker frozen input-hash validation failure code.
# retryable=False, failed_terminal, fixed safe message.
# MUST NOT interpolate caller-supplied values (expected/persisted/caller).
FAILURE_CODE_JOB_INPUT_HASH_MISMATCH = "job_input_hash_mismatch"
# Cardinality check on reader_article_rag_index_runs.job_id.
# There is NO unique constraint on job_id, so 0 / multiple / id-mismatched
# linkages all fail-closed with this fixed safe code.
FAILURE_CODE_INDEX_RUN_LINK_INVALID = "index_run_link_invalid"

# Article RAG frozen document embedding and vector write contract
# failure codes.  All retryable=False, failed_terminal, fixed safe
# message.  They MUST NOT interpolate caller-supplied values
# (provider-returned model, vector content, chunk text, key/URI,
# collection name, SDK error).
FAILURE_CODE_EMBEDDING_TEXT_TYPE_UNSUPPORTED = (
    "embedding_text_type_unsupported"
)
FAILURE_CODE_VECTOR_COLLECTION_MISMATCH = "vector_collection_mismatch"
FAILURE_CODE_EMBEDDING_MODEL_MISMATCH = "embedding_model_mismatch"
FAILURE_CODE_EMBEDDING_DIMENSION_MISMATCH = "embedding_dimension_mismatch"
FAILURE_CODE_VECTOR_WRITE_RESULT_COLLECTION_MISMATCH = (
    "vector_write_result_collection_mismatch"
)
# Indexed idempotent identity drift failure code.  Fires when an
# already-``indexed`` row's persisted ``embedding_model`` or
# ``vector_collection`` no longer matches the frozen contract.
# All retryable=False, failed_terminal, fixed safe message.  MUST NOT
# interpolate caller-supplied values (persisted model, collection,
# sentinel).
FAILURE_CODE_INDEXED_IDENTITY_MISMATCH = "index_run_indexed_identity_mismatch"

# Fixed safe error messages for input-hash / linkage validation failures.
# These strings are intentionally generic and free of any caller-supplied
# value so a malicious payload cannot leak through error surfaces.
_MSG_INPUT_HASH_MISMATCH = (
    "Article RAG index job input_hash does not match the canonical algorithm"
)
# Cardinality / linkage failure message.  Must NOT echo job_id,
# index_run_id, row count, hash, payload, URI/key/sentinel.
_MSG_INDEX_RUN_LINK_INVALID = (
    "Article RAG index job link to index run is not resolvable"
)

# Fixed safe messages for embedding/vector-write contract failures.
# Must NOT echo provider-returned model, vector content, chunk text,
# collection name, dim value, or any caller-supplied value.
_MSG_EMBEDDING_TEXT_TYPE_UNSUPPORTED = (
    "Article RAG document embedding text_type is not supported by the frozen contract"
)
_MSG_VECTOR_COLLECTION_MISMATCH = (
    "Article RAG worker runtime collection does not match the frozen contract vector collection"
)
_MSG_EMBEDDING_MODEL_MISMATCH = (
    "Article RAG embedding provider returned a model that does not match the frozen contract"
)
_MSG_EMBEDDING_DIMENSION_MISMATCH = (
    "Article RAG embedding provider returned a dimension or vector "
    "length that does not match the frozen contract"
)
_MSG_VECTOR_WRITE_RESULT_COLLECTION_MISMATCH = (
    "Article RAG vector writer returned a collection that does not "
    "match the frozen contract vector collection"
)
# Fixed safe message for indexed idempotent identity drift.
# Must NOT echo persisted model, collection, or any caller-supplied sentinel.
_MSG_INDEXED_IDENTITY_MISMATCH = (
    "Article RAG index run indexed identity does not match the "
    "frozen contract"
)

# Canonical SHA-256 shape: 64-character lowercase hex string.
# Used by _validate_canonical_sha256 to reject malformed hashes without
# echoing the offending value.  re.fullmatch (not re.match + $) —
# Python ``$`` accepts a single trailing newline, which would let
# ``"a"*64 + "\n"`` pass.  Trailing LF/CRLF is malformed (not a mismatch).
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
    plan_content_sha256: str
    chunk_count: int
    # Frozen document embedding + vector-space contract fields.
    # All three are required (no default).  They MUST be sourced from
    # the frozen ARTICLE_RAG_EMBEDDING_CONTRACT — never from provider
    # return values, writer return values, settings, or runtime
    # configuration.  The writer uses them for defence-in-depth
    # validation before any client / network / upsert call.
    embedding_model: str
    embedding_dimension: int
    embedding_text_type: str


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

    The embedding + vector contract fields
    (``document_embedding_model``, ``document_embedding_dimension``,
    ``document_embedding_text_type`` and ``vector_namespace``) are
    sourced verbatim from the frozen
    :data:`ARTICLE_RAG_EMBEDDING_CONTRACT`.  They are the single source
    of truth for the embedding call, the vector write metadata, and the
    persisted index-run identity columns.
    """

    job_id: UUID
    run_id: UUID
    reading_record_id: UUID
    user_id: UUID
    stable_document_id: UUID
    base_id: UUID
    record_generation: int
    index_run_id: UUID
    # Frozen embedding + vector-space contract (single source of truth).
    document_embedding_model: str
    document_embedding_dimension: int
    document_embedding_text_type: str
    vector_namespace: str


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

    async def reconcile_orphaned_index_runs(
        self,
        *,
        batch_size: int = 100,
    ) -> int:
        """Converge active index runs whose job died outside this worker.

        ``ReaderJobRuntime.recover_stale_leases`` and the claim-time fence
        only converge ``reader_jobs`` — they cannot know about the
        article-RAG-owned ``reader_article_rag_index_runs``. When a build
        job dies outside the worker (stale-lease max-attempt exhaustion,
        claim-fence / route-flip supersede, or a dangling job row), the
        index run stays ``queued``/``indexing`` as an active orphan and
        fail-closes every subsequent bootstrap ensure with
        ``idempotent_run_inconsistent``.

        This pass converges each orphan in ONE transaction (index run +
        reader run, with the job's terminal state re-verified under the
        index-run row lock):

        * job missing / ``failed_terminal`` → index run ``failed``,
          reader run ``failed_terminal`` (guarded to non-terminal runs);
        * job ``cancelled`` / ``superseded`` → index run ``superseded``,
          reader run ``superseded`` (guarded);
        * job requeued (``queued`` / ``retry_later`` / ``paused``) with
          index run left at ``indexing`` / ``planned`` → index run back
          to ``queued`` (in-flight semantics; the job will be re-claimed);
        * job ``claimed`` → legitimate in-flight combo, untouched.

        Returns the number of converged index runs. Vector data is NOT
        touched — external cleanup is a separate, async concern.
        """
        candidates = await self.get_pool().fetch(
            """
            SELECT ir.id AS index_run_id, ir.status AS index_status,
                   ir.job_id, ir.reader_run_id
            FROM reader_article_rag_index_runs ir
            LEFT JOIN reader_jobs j ON j.id = ir.job_id
            WHERE ir.status IN ('planned', 'queued', 'indexing')
              AND (
                    j.id IS NULL
                    OR j.status IN ('failed_terminal', 'cancelled', 'superseded')
                    OR (
                        j.status IN ('queued', 'retry_later', 'paused')
                        AND ir.status IN ('planned', 'indexing')
                    )
              )
            ORDER BY ir.created_at
            LIMIT $1
            """,
            batch_size,
        )
        reconciled = 0
        for row in candidates:
            reconciled += await self._reconcile_orphan_row(row)
        return reconciled

    async def _reconcile_orphan_row(self, row: asyncpg.Record) -> int:
        """Converge one orphan candidate atomically; 0 if it raced away.

        The index run's ``job_id`` / ``reader_run_id`` linkage fields carry
        no FK and are treated as untrusted: the reader run is only updated
        when ownership can be re-established under the lock. run type /
        reading record / generation only prove "same kind of run" (the
        same record can hold multiple build runs); ownership requires the
        bootstrap-minted reverse identity
        ``reader_runs.envelope_json.index_run_id == index_run.id``, plus —
        when the job row still exists — the job's own ``run_id`` equaling
        the index run's ``reader_run_id``. Otherwise only the index run
        converges; a corrupted linkage must never terminalize an
        unrelated run.
        """
        index_run_id: UUID = row["index_run_id"]
        job_id: UUID | None = row["job_id"]

        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                # Re-verify under the index-run row lock: a concurrent
                # worker may have already converged or advanced it.
                current = await conn.fetchrow(
                    """
                    SELECT status, job_id, reader_run_id,
                           reading_record_id, record_generation
                    FROM reader_article_rag_index_runs
                    WHERE id = $1 FOR UPDATE
                    """,
                    index_run_id,
                )
                if current is None or current["status"] not in (
                    "planned", "queued", "indexing",
                ):
                    return 0
                # Prefer the locked row's linkage over the candidate
                # snapshot's.
                job_id = current["job_id"]
                reader_run_id: UUID | None = current["reader_run_id"]

                job_status: str | None = None
                job_run_id: UUID | None = None
                job_failure_class: str | None = None
                job_failure_code: str | None = None
                if job_id is not None:
                    job_row = await conn.fetchrow(
                        """
                        SELECT status, run_id, failure_class, failure_code
                        FROM reader_jobs WHERE id = $1
                        """,
                        job_id,
                    )
                    if job_row is not None:
                        job_status = str(job_row["status"])
                        job_run_id = job_row["run_id"]
                        job_failure_class = job_row["failure_class"]
                        job_failure_code = job_row["failure_code"]

                if job_status == "claimed":
                    # Became legitimately in-flight after candidate
                    # selection — leave it alone.
                    return 0

                if job_status in (None, "failed_terminal"):
                    index_target = "failed"
                    run_target = "failed_terminal"
                    reason = (
                        "reconcile_job_missing"
                        if job_status is None
                        else "reconcile_dead_job"
                    )
                elif job_status in ("cancelled", "superseded"):
                    index_target = "superseded"
                    run_target = "superseded"
                    reason = "reconcile_dead_job"
                else:
                    # Job requeued by stale-lease recovery; realign the
                    # index run to in-flight-queued semantics.
                    index_target = "queued"
                    run_target = None
                    reason = "reconcile_inflight_requeued"

                error_json: dict[str, Any] = {}
                if index_target in ("failed", "superseded"):
                    error_json = {
                        "failure_class": job_failure_class
                        or "lifecycle_reconciliation",
                        "failure_code": job_failure_code or reason,
                        "rationale_code": reason,
                    }

                await self._update_index_run_status_in_transaction(
                    conn,
                    index_run_id,
                    status=index_target,
                    error_json=error_json,
                )
                if reader_run_id is not None and run_target is not None:
                    # Linkage trust gate: only terminalize the reader run
                    # when it is verifiably THIS index run's build run.
                    # run_type / record / generation only prove "same kind
                    # of run" — the same record can legitimately hold
                    # multiple build runs. Ownership is established by the
                    # bootstrap-minted reverse identity
                    # ``reader_runs.envelope_json.index_run_id`` (plus the
                    # job's own run_id when the job row still exists).
                    # Covers all non-terminal run statuses (including
                    # failed_retryable / paused / waiting_*) by excluding
                    # the explicit terminal set.
                    await conn.execute(
                        """
                        UPDATE reader_runs
                        SET status = $2,
                            failure_class = $3,
                            failure_code = $4,
                            finished_at = COALESCE(finished_at, NOW()),
                            updated_at = NOW()
                        WHERE id = $1
                          AND status NOT IN (
                                'completed', 'failed_terminal',
                                'cancelled', 'superseded'
                              )
                          AND run_type = $5
                          AND reading_record_id = $6
                          AND record_generation = $7
                          AND envelope_json ->> 'index_run_id' = $9
                          AND (
                                $8::uuid IS NULL
                                OR $8::uuid = $1
                          )
                        """,
                        reader_run_id,
                        run_target,
                        job_failure_class,
                        job_failure_code or reason,
                        ARTICLE_RAG_INDEX_BUILD_RUN_TYPE,
                        current["reading_record_id"],
                        current["record_generation"],
                        job_run_id,
                        str(index_run_id),
                    )
                return 1

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

            # Pre-call validation: runtime/default collection must
            # precisely equal the frozen contract vector_collection.
            # No strip, no case-normalisation, no fallback.  Fail-closed
            # BEFORE any embedding provider call so a misconfigured worker
            # cannot trigger paid embedding work for a wrong vector space.
            if self._default_vector_collection != context.vector_namespace:
                raise ArticleRagIndexWorkerError(
                    _MSG_VECTOR_COLLECTION_MISMATCH,
                    retryable=False,
                    failure_class="vector_collection_mismatch",
                    failure_code=FAILURE_CODE_VECTOR_COLLECTION_MISMATCH,
                    rationale_code=FAILURE_CODE_VECTOR_COLLECTION_MISMATCH,
                )

            # Pre-call validation: the frozen contract only supports the
            # ``provider_default`` document_embedding_text_type.  Any
            # other value must fail-closed BEFORE the embedding provider
            # is called.  The real text_type provider seam is an
            # independent task.
            if context.document_embedding_text_type != "provider_default":
                raise ArticleRagIndexWorkerError(
                    _MSG_EMBEDDING_TEXT_TYPE_UNSUPPORTED,
                    retryable=False,
                    failure_class="embedding_text_type_unsupported",
                    failure_code=FAILURE_CODE_EMBEDDING_TEXT_TYPE_UNSUPPORTED,
                    rationale_code=FAILURE_CODE_EMBEDDING_TEXT_TYPE_UNSUPPORTED,
                )

            # Mark indexing (or detect idempotent no-op).
            idempotent = await self._mark_indexing_or_detect_noop(claim, context)
            if idempotent is not None:
                return idempotent

            # Reload plan + validate hash.
            plan, index_run_snapshot = await self._reload_and_validate_plan(
                claim, context
            )

            # Embed outside the DB transaction.
            # Explicitly pass the contract document_embedding_model so
            # provider factory ``model_override`` / settings cannot
            # silently substitute a different model.
            embeddings = await self._embedding_provider.embed_texts(
                [chunk.text for chunk in plan.chunks],
                model=context.document_embedding_model,
            )
            self._validate_embedding_coverage(plan, embeddings, context=context)

            # Re-check the publish fence before any vector-store side effect.
            # The embedding call can be slow; if generation/base drifted while
            # it was running, do not upsert stale vectors and then discover the
            # drift only after the external write.
            #
            # The fence re-validation and the vector upsert run UNDER the
            # stable-document mutation advisory lock shared with the
            # vector-GC service (Wave 9): a record deletion racing this
            # write either waits for our upsert (GC then deletes our rows)
            # or holds the lock first (we re-validate, see the deleted
            # record / superseded run / cancelled job, and do zero upserts).
            # The lock connection stays checked out during the external
            # vector I/O, but no DB transaction is held open on it.
            mutation_key = advisory_lock_key(
                LOCK_NAMESPACE_VECTOR_MUTATION, context.stable_document_id
            )
            async with self.get_pool().acquire() as lock_conn:
                mutation_lock = SessionAdvisoryLock(lock_conn, mutation_key)
                try:
                    await mutation_lock.acquire()
                    await self._validate_before_vector_write(
                        claim=claim,
                        context=context,
                        index_run_snapshot=index_run_snapshot,
                    )

                    # Write vectors outside the DB transaction.
                    # Collection is sourced from the contract vector_collection,
                    # not from the worker's default_vector_collection (which has
                    # already been validated to equal it above).
                    vector_chunks = self._build_vector_chunks(plan, embeddings)
                    write_metadata = ArticleRagVectorWriteMetadata(
                        collection=context.vector_namespace,
                        reading_record_id=context.reading_record_id,
                        stable_document_id=context.stable_document_id,
                        base_id=context.base_id,
                        record_generation=context.record_generation,
                        plan_content_sha256=index_run_snapshot["plan_content_sha256"],
                        chunk_count=len(plan.chunks),
                        # Frozen contract fields.
                        embedding_model=context.document_embedding_model,
                        embedding_dimension=context.document_embedding_dimension,
                        embedding_text_type=context.document_embedding_text_type,
                    )
                    write_result = await self._vector_writer.upsert_chunks(
                        collection=context.vector_namespace,
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

                    # write_result.collection must precisely equal the contract
                    # vector_collection.  A writer that returns a different
                    # collection (e.g. SDK-routed, alias-resolved, or malicious)
                    # cannot be trusted — refuse to mark indexed.
                    if write_result.collection != context.vector_namespace:
                        raise ArticleRagIndexWorkerError(
                            _MSG_VECTOR_WRITE_RESULT_COLLECTION_MISMATCH,
                            retryable=False,
                            failure_class="vector_write_result_collection_mismatch",
                            failure_code=(
                                FAILURE_CODE_VECTOR_WRITE_RESULT_COLLECTION_MISMATCH
                            ),
                            rationale_code=(
                                FAILURE_CODE_VECTOR_WRITE_RESULT_COLLECTION_MISMATCH
                            ),
                        )
                finally:
                    await mutation_lock.unlock()

            # Mark indexed + transition job succeeded in one transaction.
            # Persist contract-derived embedding_model and
            # vector_collection — NOT embeddings[0].model or
            # write_result.collection (both have already been validated
            # to equal the contract values).
            return await self._mark_indexed_and_succeed(
                claim=claim,
                context=context,
                plan=plan,
                index_run_snapshot=index_run_snapshot,
                embedding_model=context.document_embedding_model,
                vector_store_provider=write_result.provider_metadata.get(
                    "provider", "unknown"
                ),
                vector_collection=context.vector_namespace,
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
            if exc.failure_class == "plan_hash_mismatch":
                # Obsolete truth: the truth layer changed between bootstrap
                # and worker execution, so the persisted index run no longer
                # describes the current document. Supersede (not fail) —
                # this is content churn, not an infrastructure defect, and
                # must not pollute failure metrics. Same semantics as the
                # plan-service-level ArticleRagIndexPlanError branch above.
                return await self._handle_supersede(
                    claim=claim,
                    context=context,
                    rationale_code=exc.rationale_code or exc.failure_code,
                    failure_class=exc.failure_class,
                    failure_code=exc.failure_code,
                    message=str(exc),
                )
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
    # Mark indexing / detect idempotent no-op
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
                           record_generation,
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
                    # Verify persisted indexed identity still matches the
                    # frozen contract.  This MUST run before the no-op
                    # transition to ``succeeded`` so a drifted row (e.g.
                    # ``embedding_model`` or ``vector_collection`` mutated
                    # out-of-band) fails closed as ``failed_terminal``
                    # instead of returning an idempotent success.
                    self._validate_indexed_identity(row, context)

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
        """Validate that index run fields match claim + input_json."""
        if (
            str(row["base_id"]) != str(context.base_id)
            or str(row["stable_document_id"]) != str(context.stable_document_id)
            or int(row["record_generation"]) != context.record_generation
        ):
            raise ArticleRagIndexWorkerError(
                f"index run {context.index_run_id} fields do not match "
                f"claim / input_json contract",
                retryable=False,
                failure_class="index_run_state",
                failure_code=FAILURE_CODE_INDEX_RUN_FIELD_MISMATCH,
            )

    def _validate_indexed_identity(
        self,
        row: asyncpg.Record,
        context: _JobContext,
    ) -> None:
        """Validate persisted indexed identity matches the frozen contract.

        When an index run is already ``indexed`` and the worker is about
        to short-circuit with an idempotent no-op success, the persisted
        ``embedding_model`` and ``vector_collection`` columns MUST be
        non-NULL and exactly equal the frozen contract values
        (``context.document_embedding_model`` and
        ``context.vector_namespace``).

        This guard prevents a drifted indexed row (e.g. out-of-band
        UPDATE on ``embedding_model`` or ``vector_collection``) from
        being silently re-confirmed as a successful no-op.  On drift the
        worker fails closed with ``failed_terminal`` and the failure code
        ``index_run_indexed_identity_mismatch`` — the persisted malicious
        model/collection is NEVER echoed in any error surface.

        This check is ONLY applied on the ``indexed`` no-op branch.
        ``queued`` / ``indexing`` rows are not yet required to have
        ``embedding_model`` / ``vector_collection`` populated (those
        columns are written during the Phase-5 success transition), so
        we do NOT call this method from the queued/indexing path.
        """
        persisted_model = row["embedding_model"]
        persisted_collection = row["vector_collection"]
        # Use ``is None`` rather than truthiness — empty string is also
        # invalid for an indexed row, but NULL is the canonical
        # "not yet written" marker and we want to fail closed on both.
        # Fixed safe message: do NOT echo persisted_model /
        # persisted_collection / context values.
        if (
            persisted_model is None
            or not isinstance(persisted_model, str)
            or persisted_model != context.document_embedding_model
            or persisted_collection is None
            or not isinstance(persisted_collection, str)
            or persisted_collection != context.vector_namespace
        ):
            raise ArticleRagIndexWorkerError(
                _MSG_INDEXED_IDENTITY_MISMATCH,
                retryable=False,
                failure_class="index_run_indexed_identity_mismatch",
                failure_code=FAILURE_CODE_INDEXED_IDENTITY_MISMATCH,
                rationale_code=FAILURE_CODE_INDEXED_IDENTITY_MISMATCH,
            )

    # ------------------------------------------------------------------
    # Reload plan + validate hash
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
    # Embedding and vector-write helpers
    # ------------------------------------------------------------------

    def _validate_embedding_coverage(
        self,
        plan: ArticleRagIndexPlan,
        embeddings: list[ArticleRagEmbedding],
        *,
        context: _JobContext,
    ) -> None:
        """Verify embedding count + per-chunk text_sha256 coverage.

        Also verify that every returned embedding's ``model`` is
        a ``str`` and precisely equals ``context.document_embedding_model``,
        that ``dim`` is a non-bool ``int`` and precisely equals
        ``context.document_embedding_dimension``, and that
        ``len(vector)`` precisely equals ``context.document_embedding_dimension``.

        All validation failures use fixed safe messages that do NOT echo the
        provider-returned model, the dim, the vector content, or any
        chunk text / sha.  Any single bad embedding fails the whole
        batch before the vector writer is called.
        """
        if len(embeddings) != len(plan.chunks):
            raise ArticleRagIndexWorkerError(
                f"embedding provider returned {len(embeddings)} embeddings "
                f"for {len(plan.chunks)} chunks",
                retryable=False,
                failure_class="embedding_coverage",
                failure_code=FAILURE_CODE_EMBEDDING_FAILED,
            )
        expected_model = context.document_embedding_model
        expected_dim = context.document_embedding_dimension
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
            # Model must be a str and precisely match the profile.
            # bool is not a valid model.  None / int / trailing space /
            # trailing LF are all rejected.  Fixed safe message; no echo.
            if not isinstance(emb.model, str) or emb.model != expected_model:
                raise ArticleRagIndexWorkerError(
                    _MSG_EMBEDDING_MODEL_MISMATCH,
                    retryable=False,
                    failure_class="embedding_model_mismatch",
                    failure_code=FAILURE_CODE_EMBEDDING_MODEL_MISMATCH,
                    rationale_code=FAILURE_CODE_EMBEDDING_MODEL_MISMATCH,
                )
            # Dim must be a non-bool int and precisely match the
            # profile.  ``True`` / ``False`` are ints in Python; reject
            # them explicitly so a malicious provider cannot pass a
            # boolean where a dimension is expected.
            if isinstance(emb.dim, bool) or not isinstance(emb.dim, int):
                raise ArticleRagIndexWorkerError(
                    _MSG_EMBEDDING_DIMENSION_MISMATCH,
                    retryable=False,
                    failure_class="embedding_dimension_mismatch",
                    failure_code=FAILURE_CODE_EMBEDDING_DIMENSION_MISMATCH,
                    rationale_code=FAILURE_CODE_EMBEDDING_DIMENSION_MISMATCH,
                )
            if emb.dim != expected_dim:
                raise ArticleRagIndexWorkerError(
                    _MSG_EMBEDDING_DIMENSION_MISMATCH,
                    retryable=False,
                    failure_class="embedding_dimension_mismatch",
                    failure_code=FAILURE_CODE_EMBEDDING_DIMENSION_MISMATCH,
                    rationale_code=FAILURE_CODE_EMBEDDING_DIMENSION_MISMATCH,
                )
            # len(vector) must precisely equal the profile dim.
            # A wrong-length vector is rejected even when dim is correct
            # (defence in depth: a provider could lie about dim).
            if len(emb.vector) != expected_dim:
                raise ArticleRagIndexWorkerError(
                    _MSG_EMBEDDING_DIMENSION_MISMATCH,
                    retryable=False,
                    failure_class="embedding_dimension_mismatch",
                    failure_code=FAILURE_CODE_EMBEDDING_DIMENSION_MISMATCH,
                    rationale_code=FAILURE_CODE_EMBEDDING_DIMENSION_MISMATCH,
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
    # Validate before vector-store side effects
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
                # Deletion fence: the record must still exist and be
                # undeleted.  Wave 8 soft-deletes atomically supersede the
                # index run, so this is defence-in-depth on top of the run
                # status check below — but it MUST run here, under the
                # mutation lock, so a late writer racing a deletion can
                # never upsert vectors for a deleted record.
                record_row = await conn.fetchrow(
                    """
                    SELECT deleted_at
                    FROM reading_records
                    WHERE id = $1
                    """,
                    context.reading_record_id,
                )
                if record_row is None or record_row["deleted_at"] is not None:
                    raise FenceViolationError(
                        "reading record deleted before vector write"
                    )
                index_row = await conn.fetchrow(
                    """
                    SELECT id, status, job_id, base_id, stable_document_id,
                           record_generation,
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
    # Mark indexed + transition job succeeded
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
        """Transition job → superseded, run → superseded, index run →
        superseded — atomically in one caller-owned transaction.

        All three writes commit or roll back together; a mid-group failure
        can no longer leave a terminal job paired with an active index
        run (the orphan combo that fail-closes bootstrap ensure).
        """
        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                await self._job_runtime.transition_in_transaction(
                    conn,
                    job_id=claim.job_id,
                    target_status="superseded",
                    lease_token=claim.lease_token,
                    failure_class=failure_class,
                    failure_code=failure_code,
                    failure_message=message,
                    rationale_code=rationale_code,
                )
                await self._mark_run_status_in_transaction(
                    conn,
                    claim.run_id,
                    status="superseded",
                    failure_class=failure_class,
                    failure_code=failure_code,
                    finished_at=datetime.now(UTC),
                )
                if context is not None:
                    await self._update_index_run_status_in_transaction(
                        conn,
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

        When ``context is None`` (validation failed before
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
                    # context=None — do NOT trust the potentially
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

        Validation layers (in order):

          1. Basic JSON object + IDs + record_generation.
          2. Cross-validate with claim (reading_record_id / base_id /
             expected_generation / target_key).
          3. ``reader_jobs.input_hash`` precisely equals the canonical
             hash computed via the public bootstrap seam
             ``compute_article_rag_index_build_input_hash``.  The worker
             must NOT duplicate the hash algorithm.
          4. Cardinality check on
             ``reader_article_rag_index_runs.job_id`` — exactly 1 linked
             row whose id equals ``input_json.index_run_id``.

        Embedding + vector contract fields are sourced verbatim from
        :data:`ARTICLE_RAG_EMBEDDING_CONTRACT` — no settings, no env,
        no runtime override, no provider/writer return value.
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
        # input_hash validation via the public bootstrap seam.
        # -----------------------------------------------------------------
        #
        # ``reader_jobs.input_hash`` must precisely equal the canonical
        # hash computed via ``compute_article_rag_index_build_input_hash``.
        # The worker must NOT duplicate the hash algorithm; it must call
        # the public seam so a future algorithm change has a single
        # source of truth.
        #
        # The hash covers (stable_document_id, base_id, plan_content_sha256).
        # ``plan_content_sha256`` is NOT in input_json; the worker must
        # recover it from a trusted source.  We load the linked index-run
        # row via the trusted DB relationship
        # ``reader_article_rag_index_runs.job_id = claim.job_id``
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
                _MSG_INPUT_HASH_MISMATCH,
                retryable=False,
                failure_class="job_input_hash_mismatch",
                failure_code=FAILURE_CODE_JOB_INPUT_HASH_MISMATCH,
                rationale_code=FAILURE_CODE_JOB_INPUT_HASH_MISMATCH,
            )

        # Trusted lookup: find the linked index-run by job_id, not by
        # input_json.index_run_id.  This is the same relationship the
        # context=None terminalization path uses (see _handle_failed_terminal).
        #
        # ``reader_article_rag_index_runs.job_id`` has NO unique
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
                _MSG_INDEX_RUN_LINK_INVALID,
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
        )
        if persisted_input_hash != expected_input_hash:
            raise ArticleRagIndexWorkerError(
                _MSG_INPUT_HASH_MISMATCH,
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
            # Source embedding + vector-space contract exclusively from
            # the frozen ARTICLE_RAG_EMBEDDING_CONTRACT.  No settings,
            # no env, no runtime override, no provider/writer return value.
            document_embedding_model=ARTICLE_RAG_EMBEDDING_CONTRACT.document_embedding_model,
            document_embedding_dimension=(
                int(ARTICLE_RAG_EMBEDDING_CONTRACT.document_embedding_dimension)
            ),
            document_embedding_text_type=(
                ARTICLE_RAG_EMBEDDING_CONTRACT.document_embedding_text_type
            ),
            vector_namespace=ARTICLE_RAG_EMBEDDING_CONTRACT.vector_collection,
        )

    # ------------------------------------------------------------------
    # Low-level DB helpers
    # ------------------------------------------------------------------

    async def _mark_run_running(self, run_id: UUID) -> None:
        async with self.get_pool().acquire() as conn:
            await mark_reader_run_running(conn, run_id)

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
        await mark_reader_run_status(
            conn,
            run_id,
            status=status,
            failure_class=failure_class,
            failure_code=failure_code,
            finished_at=finished_at,
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
