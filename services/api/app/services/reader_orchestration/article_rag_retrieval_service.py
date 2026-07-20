"""D6-I4E: Article RAG Retrieval Service.

Read-only retrieval service that combines the D6-I4A index plan,
the D6-I4D embedding provider, and the D6-I4E vector searcher to
answer a query against a single ``reading_record``.  The service is
fail-closed end-to-end:

  * the active reading record / stable document / reading base MUST
    exist (validated transitively via the I4A plan service);
  * an indexed ``reader_article_rag_index_runs`` row MUST exist for
    the same ``(stable_document_id, index_version)`` with status
    ``indexed``;
  * the current plan's ``compute_plan_content_sha256`` MUST equal the
    indexed run's ``plan_content_sha256`` — content drift is a fail-closed
    condition (the index is stale);
  * vector hits are joined against the current plan on ``chunk_id``
    only; vector payload text / citation content is **never** trusted;
  * vector guard metadata returned by the searcher
    (``stable_document_id``, ``base_id``, ``index_version``,
    ``plan_content_sha256``) is checked for consistency with the
    current plan; mismatches fail closed (we do not want to serve
    cross-document contamination).

Truth boundary
--------------

Zilliz is only an index replica.  Citation truth always returns to
Postgres's ``stable_document_blocks`` / ``reading_bases.text`` /
``reading_units`` / ``anchor_segments`` (via the I4A plan).  The
retrieval service joins every hit against the current plan on
``chunk_id``, returning the plan chunk's ``text`` + ``citation`` +
``metadata_json``.  The vector payload's ``text`` / ``citation`` /
``markdown`` / ``plate`` / ``dom`` / ``slate`` / ``ui`` fields are
**never** read.

Returned metadata is sanitised against a denylist before being placed
on the result.  See :data:`_FORBIDDEN_RESULT_METADATA_KEYS`.

Security contract
-----------------

* The query text is **never** logged at INFO or higher; only the
  ``reading_record_id`` + ``user_id`` + ``limit`` are logged at DEBUG
  level for ops diagnostics.
* The query vector and chunk embeddings are **never** logged.
* The Zilliz token / URI are **never** logged or echoed in exception
  messages; the embedding API key is **never** logged.
* Retrieval errors are typed (``failure_code`` for ops dashboards); the
  underlying SDK exception (if any) is preserved as ``__cause__``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

import asyncpg

from .article_rag_index_plan import (
    ArticleRagIndexChunk,
    ArticleRagIndexPlan,
    ArticleRagIndexPlanService,
    compute_plan_content_sha256,
)
from .article_rag_index_profile import (
    DEFAULT_ARTICLE_RAG_INDEX_VERSION,
    ArticleRagIndexProfileResolutionError,
    resolve_article_rag_index_profile,
)
from .article_rag_index_worker import (
    ArticleRagEmbeddingProvider,
    ArticleRagIndexWorkerError,
)
from .article_rag_vector_search import (
    ArticleRagVectorSearcher,
    ArticleRagVectorSearcherError,
    ArticleRagVectorSearchResult,
    UnconfiguredArticleRagVectorSearcher,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# P1-F: ``DEFAULT_INDEX_VERSION`` is now an alias of the P1-B profile
# module's ``DEFAULT_ARTICLE_RAG_INDEX_VERSION`` — no second literal
# source of truth.  The value is unchanged (``"article_rag_index_v1"``).
DEFAULT_INDEX_VERSION = DEFAULT_ARTICLE_RAG_INDEX_VERSION

# Hard cap on top-k.  Prevents the caller from requesting an unbounded
# number of hits and accidentally draining the entire index in a single
# search.  Callers that need more must paginate.
MAX_RETRIEVAL_LIMIT = 50

# Statuses of ``reader_article_rag_index_runs`` that mean the index is
# queryable.  Other statuses (planned / queued / indexing / failed /
# superseded) mean we MUST NOT serve hits from this run.
_INDEX_RUN_QUERYABLE_STATUSES = frozenset({"indexed"})

# Failure codes — stable, machine-readable.
FAILURE_CODE_RETRIEVAL_NO_INDEXED_RUN = "retrieval_no_indexed_run"
FAILURE_CODE_RETRIEVAL_PLAN_HASH_MISMATCH = "retrieval_plan_hash_mismatch"
FAILURE_CODE_RETRIEVAL_INDEX_RUN_PLAN_MISMATCH = (
    "retrieval_index_run_plan_mismatch"
)
FAILURE_CODE_RETRIEVAL_EMPTY_QUERY = "retrieval_empty_query"
FAILURE_CODE_RETRIEVAL_INVALID_LIMIT = "retrieval_invalid_limit"
FAILURE_CODE_RETRIEVAL_VECTOR_METADATA_MISMATCH = (
    "retrieval_vector_metadata_mismatch"
)
FAILURE_CODE_RETRIEVAL_EMBEDDING_FAILED = "retrieval_embedding_failed"
FAILURE_CODE_RETRIEVAL_VECTOR_SEARCH_FAILED = "retrieval_vector_search_failed"
FAILURE_CODE_RETRIEVAL_NO_VECTOR_COLLECTION = "retrieval_no_vector_collection"
FAILURE_CODE_RETRIEVAL_EMBEDDING_MODEL_MISMATCH = (
    "retrieval_embedding_model_mismatch"
)
FAILURE_CODE_RETRIEVAL_EMBEDDING_DIMENSION_MISMATCH = (
    "retrieval_embedding_dimension_mismatch"
)
# P1-F: profile resolution / indexed-run identity validation failure
# codes.  ``_INVALID`` covers NULL / malformed / unregistered inputs;
# ``_MISMATCH`` covers valid-format but resolver-inconsistent values.
FAILURE_CODE_RETRIEVAL_INDEX_PROFILE_INVALID = (
    "retrieval_index_profile_invalid"
)
FAILURE_CODE_RETRIEVAL_INDEX_PROFILE_MISMATCH = (
    "retrieval_index_profile_mismatch"
)

# P1-F: fixed local error messages for profile failures.  These strings
# never interpolate caller-supplied input — the offending value is never
# echoed in ``str``, ``repr``, ``args``, or traceback.
_P1F_MSG_PROFILE_NOT_RESOLVED = (
    "Article RAG index profile is not supported"
)
_P1F_MSG_INDEXED_RUN_PROFILE_INVALID = (
    "Article RAG index run profile fingerprint is not registered"
)
_P1F_MSG_INDEXED_RUN_PROFILE_MISMATCH = (
    "Article RAG index run profile does not match resolver"
)
_P1F_MSG_PLAN_IDENTITY_MISMATCH = (
    "Article RAG index plan identity does not match resolver"
)
_P1F_MSG_INDEX_RUN_PLAN_MISMATCH = (
    "Article RAG indexed run does not match the current plan"
)
_P1F_MSG_QUERY_EMBEDDING_MODEL_MISMATCH = (
    "Article RAG query embedding model does not match profile"
)
_P1F_MSG_QUERY_EMBEDDING_DIMENSION_MISMATCH = (
    "Article RAG query embedding dimension does not match profile"
)

# P1-F: canonical SHA-256 fingerprint format enforced by migration 0021's
# CHECK constraint.  Used to distinguish ``_INVALID`` (NULL / malformed)
# from ``_MISMATCH`` (valid format but wrong value).
_PROFILE_FINGERPRINT_PATTERN = re.compile(r"[0-9a-f]{64}")

# Denylist for returned metadata — keys that MUST NOT appear on the
# returned ``metadata_json``.  Mirrors the I4D writer denylist extended
# with explicit UI display group keys.  Any presence is a
# truth-boundary violation.
_FORBIDDEN_RESULT_METADATA_KEYS = frozenset({
    # Plate / Slate / DOM / Markdown projection fields — never
    # permitted in RAG citation metadata.
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
    # UI display group / render profile / selection — UI-only fields,
    # never a fact source.
    "ui",
    "ui_display_group",
    "render_profile",
    "render_snapshot",
    "citation_refs",
    # Extended safety: any text / path / value field that may echo
    # chunk content or projection state.
    "text",
    "chunkText",
    "path",
    "selection",
    "value",
    "rich_text",
    "html",
    "innerText",
    "innerHTML",
})


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ArticleRagRetrievalServiceError(ArticleRagIndexWorkerError):
    """Typed failure for retrieval service errors.

    Inherits :class:`ArticleRagIndexWorkerError` so the same error
    taxonomy (retryable / failure_class / failure_code / rationale_code)
    used by I4C's worker is reused here.  ``failure_class`` defaults to
    ``"retrieval"`` so dashboards can route retrieval failures
    separately from write-side failures.
    """

    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        failure_code: str,
        failure_class: str = "retrieval",
        rationale_code: str | None = None,
    ) -> None:
        super().__init__(
            message,
            retryable=retryable,
            failure_class=failure_class,
            failure_code=failure_code,
            rationale_code=rationale_code,
        )


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ArticleRagRetrievalHit:
    """One retrieval hit joined against the current plan.

    ``chunk_id`` / ``text`` / ``citation`` / ``metadata_json`` /
    ``content_sha256`` / ``score`` come from the join of the vector
    hit on ``plan.chunks`` (Postgres is the truth).  Vector payload
    text / citation is never trusted.
    """

    chunk_id: str
    text: str
    citation: dict[str, Any]
    metadata_json: dict[str, Any]
    score: float
    # Plan-backed content hash from the joined index chunk — never
    # recomputed from returned text by consumers.
    content_sha256: str


@dataclass(frozen=True, slots=True)
class ArticleRagRetrievalResult:
    """Result of a retrieval call.

    ``hits`` are ordered by score descending (the searcher returns
    score-descending; we preserve that ordering through the join).
    ``stable_document_id`` / ``base_id`` / ``record_generation`` /
    ``plan_content_sha256`` are the **current**
    plan's authoritative values; they are echoed here for ops
    diagnostics.

    ``index_run_id`` is the immutable ``reader_article_rag_index_runs.id``
    of the **exact** indexed run used for this retrieval (loaded in the
    same fail-closed path that validated plan hash / collection).
    Downstream Ask evidence must use this identity as ``rag_substrate_id``
    rather than re-querying "latest indexed run" (which races reindex).

    ``provider_metadata`` carries searcher-side diagnostics; it MUST NOT
    be surfaced to end users as a fact source.
    """

    reading_record_id: UUID
    stable_document_id: UUID
    base_id: UUID
    record_generation: int
    plan_content_sha256: str
    index_run_id: UUID
    hits: tuple[ArticleRagRetrievalHit, ...]
    provider_metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Reader index-run row (private)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _IndexedRunSnapshot:
    """A snapshot of an ``reader_article_rag_index_runs`` row.

    Used to validate that the index is queryable AND that its
    ``plan_content_sha256`` matches the current plan.  ``status`` is
    included so we can refuse runs that are not yet ``indexed``.

    ``vector_collection`` and ``embedding_model`` are carried so the
    retrieval service can address the right Zilliz collection and
    validate that the embedding model used at query time matches the
    model used to build the index.  Without ``vector_collection``
    the searcher cannot be routed correctly (real deployments may use
    a non-default collection name); without ``embedding_model`` the
    query vector and the indexed vectors would be in different
    embedding spaces, producing silently bad results.

    P1-F: ``profile_fingerprint`` is the durable identity column added
    by migration 0021.  The retrieval service reads it and validates
    it against ``resolution.profile_fingerprint`` as part of the 5-field
    identity check.  NULL / malformed fingerprints fail closed with
    ``retrieval_index_profile_invalid``; valid-format but resolver-
    inconsistent fingerprints fail closed with
    ``retrieval_index_profile_mismatch``.
    """

    index_run_id: UUID
    stable_document_id: UUID
    base_id: UUID
    record_generation: int
    index_version: str
    chunker_version: str
    plan_content_sha256: str
    chunk_count: int
    status: str
    vector_collection: str | None = None
    embedding_model: str | None = None
    profile_fingerprint: str | None = None


# ---------------------------------------------------------------------------
# Connection factory protocol
# ---------------------------------------------------------------------------


class _PoolAcquirer(Protocol):
    """Minimal asyncpg.Pool shape used by the service.

    Exists so tests can inject a ``FakePool`` without depending on
    ``asyncpg`` types directly.
    """

    def acquire(self): ...  # async context manager


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ArticleRagRetrievalService:
    """Read-only retrieval service for Article RAG.

    Combines:
      * :class:`ArticleRagIndexPlanService` to rebuild the current plan;
      * the embedding provider (D6-I4D) to embed the query text;
      * the vector searcher (D6-I4E vector search module) to find
        candidate hits.

    The service is fail-closed: every intermediate check that fails
    raises :class:`ArticleRagRetrievalServiceError` with a stable
    ``failure_code``.  No partial results are ever returned.
    """

    def __init__(
        self,
        *,
        pool: _PoolAcquirer | None = None,
        plan_service: ArticleRagIndexPlanService | None = None,
        embedding_provider: ArticleRagEmbeddingProvider | None = None,
        vector_searcher: ArticleRagVectorSearcher | None = None,
    ) -> None:
        self._pool = pool
        self._plan_service = plan_service or ArticleRagIndexPlanService(
            pool=pool  # type: ignore[arg-type]
        )
        # Lazy / explicit-only: no default embedding provider — the
        # retrieval service refuses to silently pick a fake.  Tests
        # must inject either ``FakeArticleRagEmbeddingProvider`` (the
        # I4C fake) or a real DashScope provider.  When neither is
        # supplied we leave the attribute as ``None`` and raise at the
        # point of use.
        self._embedding_provider = embedding_provider
        self._vector_searcher = (
            vector_searcher or UnconfiguredArticleRagVectorSearcher()
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def retrieve_for_record(
        self,
        *,
        reading_record_id: UUID,
        user_id: UUID,
        query_text: str,
        limit: int = 10,
    ) -> ArticleRagRetrievalResult:
        """Retrieve hits for ``query_text`` against ``reading_record_id``.

        Parameters
        ----------
        reading_record_id
            The reading record to search against.  Ownership is
            validated via the I4A plan service (which raises
            :class:`LookupError` if the record does not belong to
            ``user_id``).
        user_id
            The requesting user (ownership check).
        query_text
            The query text.  Empty / whitespace-only strings fail
            closed with ``failure_code=retrieval_empty_query``.
        limit
            Maximum number of hits to return.  Must be in
            ``[1, MAX_RETRIEVAL_LIMIT]``.  Out-of-range fails closed
            with ``failure_code=retrieval_invalid_limit``.

        Raises
        ------
        ArticleRagRetrievalServiceError
            If any of the fail-closed checks fail.  ``failure_code``
            identifies the cause.
        LookupError
            If the record does not exist or does not belong to
            ``user_id``.
        """
        if not (query_text or "").strip():
            raise ArticleRagRetrievalServiceError(
                "retrieve_for_record called with an empty query_text; "
                "refusing to embed nothing",
                retryable=False,
                failure_code=FAILURE_CODE_RETRIEVAL_EMPTY_QUERY,
            )
        if limit <= 0 or limit > MAX_RETRIEVAL_LIMIT:
            raise ArticleRagRetrievalServiceError(
                f"retrieve_for_record called with limit={limit}; must be "
                f"in [1, {MAX_RETRIEVAL_LIMIT}]",
                retryable=False,
                failure_code=FAILURE_CODE_RETRIEVAL_INVALID_LIMIT,
            )
        if self._embedding_provider is None:
            # Defensive — should never happen in normal flow because
            # tests inject the fake and the factory path injects the
            # real provider.
            raise ArticleRagRetrievalServiceError(
                "ArticleRagRetrievalService has no embedding provider "
                "configured",
                retryable=False,
                failure_code=FAILURE_CODE_RETRIEVAL_EMBEDDING_FAILED,
            )

        # Phase 0 (P1-F): resolve the immutable IndexProfile.  This is
        # the sole entry point for obtaining a profile identity.  Any
        # non-string, empty, whitespace-padded, unregistered, or
        # malicious ``index_version`` fails closed here — BEFORE any
        # DB, plan, embedding, or vector call.  The wrapper error is
        # constructed INSIDE the except block and raised OUTSIDE it so
        # both ``__cause__`` and ``__context__`` remain None (the
        # resolver's exception is NOT chained).
        resolution = None
        resolution_error: ArticleRagRetrievalServiceError | None = None
        try:
            resolution = resolve_article_rag_index_profile(
                DEFAULT_INDEX_VERSION
            )
        except ArticleRagIndexProfileResolutionError:
            resolution_error = ArticleRagRetrievalServiceError(
                _P1F_MSG_PROFILE_NOT_RESOLVED,
                retryable=False,
                failure_code=FAILURE_CODE_RETRIEVAL_INDEX_PROFILE_INVALID,
            )
        if resolution_error is not None:
            # Raised outside the except block: __cause__ is None,
            # __context__ is None.
            raise resolution_error
        if resolution is None:  # pragma: no cover - defensive invariant
            raise ArticleRagRetrievalServiceError(
                _P1F_MSG_PROFILE_NOT_RESOLVED,
                retryable=False,
                failure_code=FAILURE_CODE_RETRIEVAL_INDEX_PROFILE_INVALID,
            )

        profile = resolution.profile

        pool = self._pool
        if pool is None:
            pool = self._plan_service._get_pool()  # type: ignore[attr-defined]
        if pool is None:
            raise ArticleRagRetrievalServiceError(
                "ArticleRagRetrievalService has no asyncpg pool configured",
                retryable=False,
                failure_code=FAILURE_CODE_RETRIEVAL_EMBEDDING_FAILED,
            )

        async with pool.acquire() as conn:
            # Phase A: rebuild current plan (validates ownership + active
            # base + stable document + non-stale generation).  This may
            # raise ``LookupError`` (ownership) or
            # ``ArticleRagIndexPlanError`` (inactive/stale base/doc) —
            # both are propagated as-is to the caller because they are
            # already typed failures with no chunk text or token
            # content.
            #
            # P1-F: forward ``index_version=profile.index_version``
            # explicitly so the plan service uses the same frozen
            # profile identity as the retrieval path.  No path forwards
            # ``None`` or the caller's raw input — always the resolved
            # profile's ``index_version``.
            plan = await self._plan_service.build_index_plan_in_transaction(
                conn,
                record_id=reading_record_id,
                user_id=user_id,
                index_version=profile.index_version,
            )

            # Phase A.2 (P1-F): validate the plan's chunker_version
            # matches the resolved profile's chunker_version.  A
            # mismatch means the plan was built with a different
            # chunker identity than the one the resolver produced for
            # this index_version — fail closed.
            if plan.chunker_version != profile.chunker_version:
                raise ArticleRagRetrievalServiceError(
                    _P1F_MSG_PLAN_IDENTITY_MISMATCH,
                    retryable=False,
                    failure_code=(
                        FAILURE_CODE_RETRIEVAL_INDEX_PROFILE_MISMATCH
                    ),
                )

            # Phase B: locate an indexed run for the same
            # (stable_document_id, index_version).  Absence fails closed.
            indexed = await self._load_indexed_run(
                conn,
                stable_document_id=plan.stable_document_id,
                index_version=profile.index_version,
            )
            if indexed is None:
                raise ArticleRagRetrievalServiceError(
                    f"no queryable index run for "
                    f"stable_document_id={plan.stable_document_id} "
                    f"index_version={profile.index_version}",
                    retryable=False,
                    failure_code=FAILURE_CODE_RETRIEVAL_NO_INDEXED_RUN,
                )

            # Phase C: validate the indexed run's frozen profile identity
            # before interpreting downstream plan metadata.
            if indexed.index_version != profile.index_version:
                raise ArticleRagRetrievalServiceError(
                    _P1F_MSG_INDEXED_RUN_PROFILE_MISMATCH,
                    retryable=False,
                    failure_code=(
                        FAILURE_CODE_RETRIEVAL_INDEX_PROFILE_MISMATCH
                    ),
                )

            fingerprint = indexed.profile_fingerprint
            if (
                not isinstance(fingerprint, str)
                or not _PROFILE_FINGERPRINT_PATTERN.fullmatch(fingerprint)
            ):
                raise ArticleRagRetrievalServiceError(
                    _P1F_MSG_INDEXED_RUN_PROFILE_INVALID,
                    retryable=False,
                    failure_code=(
                        FAILURE_CODE_RETRIEVAL_INDEX_PROFILE_INVALID
                    ),
                )
            if fingerprint != resolution.profile_fingerprint:
                raise ArticleRagRetrievalServiceError(
                    _P1F_MSG_INDEXED_RUN_PROFILE_MISMATCH,
                    retryable=False,
                    failure_code=(
                        FAILURE_CODE_RETRIEVAL_INDEX_PROFILE_MISMATCH
                    ),
                )
            if indexed.chunker_version != profile.chunker_version:
                raise ArticleRagRetrievalServiceError(
                    _P1F_MSG_INDEXED_RUN_PROFILE_MISMATCH,
                    retryable=False,
                    failure_code=(
                        FAILURE_CODE_RETRIEVAL_INDEX_PROFILE_MISMATCH
                    ),
                )
            if indexed.embedding_model != profile.document_embedding_model:
                raise ArticleRagRetrievalServiceError(
                    _P1F_MSG_INDEXED_RUN_PROFILE_MISMATCH,
                    retryable=False,
                    failure_code=(
                        FAILURE_CODE_RETRIEVAL_INDEX_PROFILE_MISMATCH
                    ),
                )
            if indexed.vector_collection != profile.vector_namespace:
                raise ArticleRagRetrievalServiceError(
                    _P1F_MSG_INDEXED_RUN_PROFILE_MISMATCH,
                    retryable=False,
                    failure_code=(
                        FAILURE_CODE_RETRIEVAL_INDEX_PROFILE_MISMATCH
                    ),
                )

            # Phase C.1: the durable index-run row must describe the
            # exact plan rebuilt from current Postgres truth.
            if (
                indexed.base_id != plan.base_id
                or indexed.record_generation != plan.record_generation
                or indexed.chunk_count != len(plan.chunks)
            ):
                raise ArticleRagRetrievalServiceError(
                    _P1F_MSG_INDEX_RUN_PLAN_MISMATCH,
                    retryable=False,
                    failure_code=(
                        FAILURE_CODE_RETRIEVAL_INDEX_RUN_PLAN_MISMATCH
                    ),
                )

            # Phase C.2: validate the deterministic plan hash last.
            current_plan_hash = compute_plan_content_sha256(plan)
            if current_plan_hash != indexed.plan_content_sha256:
                raise ArticleRagRetrievalServiceError(
                    f"current plan_content_sha256 ({current_plan_hash}) "
                    f"does not match indexed run "
                    f"({indexed.plan_content_sha256}); refusing to serve "
                    "stale index hits",
                    retryable=False,
                    failure_code=FAILURE_CODE_RETRIEVAL_PLAN_HASH_MISMATCH,
                )

        # Phase D: embed query (outside DB tx).  P1-F: the model is
        # sourced from ``profile.query_embedding_model`` — NOT from the
        # indexed run's ``embedding_model``.  The indexed run's
        # ``embedding_model`` is the *document* embedding model used at
        # index time; the *query* embedding model is a distinct profile
        # field that may diverge in future versions.  In V1 they are
        # both ``"text-embedding-v4"``, but the routing must use the
        # profile so a future V2 query model does not silently use the
        # V1 document model.
        try:
            query_embeddings = await self._embedding_provider.embed_texts(
                [query_text], model=profile.query_embedding_model
            )
        except ArticleRagIndexWorkerError as exc:
            # Surface the worker-base failure as a retrieval failure so
            # dashboards see ``retrieval_embedding_failed``.
            raise ArticleRagRetrievalServiceError(
                f"embedding provider raised {type(exc).__name__} "
                f"(failure_code={exc.failure_code}); see __cause__ for "
                "upstream diagnostic",
                retryable=exc.retryable,
                failure_code=FAILURE_CODE_RETRIEVAL_EMBEDDING_FAILED,
            ) from exc
        except Exception as exc:  # noqa: BLE001 — defensive catch-all
            raise ArticleRagRetrievalServiceError(
                "embedding provider raised "
                f"{type(exc).__name__}; see __cause__ for upstream "
                "diagnostic",
                retryable=False,
                failure_code=FAILURE_CODE_RETRIEVAL_EMBEDDING_FAILED,
            ) from exc

        if not query_embeddings:
            raise ArticleRagRetrievalServiceError(
                "embedding provider returned no embeddings for the query",
                retryable=False,
                failure_code=FAILURE_CODE_RETRIEVAL_EMBEDDING_FAILED,
            )
        query_embedding = query_embeddings[0]
        query_vector = tuple(query_embedding.vector)
        if not query_vector:
            raise ArticleRagRetrievalServiceError(
                "embedding provider returned an empty query vector",
                retryable=False,
                failure_code=FAILURE_CODE_RETRIEVAL_EMBEDDING_FAILED,
            )

        # Phase D.2 (P1-F): assert the embedding provider used the
        # profile's ``query_embedding_model``.  The NULL bypass is
        # removed — the check is strict.  A mismatch means the query
        # vector and the indexed vectors are in different spaces →
        # silently wrong hits.  The offending model name is never
        # echoed in the error message.
        if (
            not isinstance(query_embedding.model, str)
            or query_embedding.model != profile.query_embedding_model
        ):
            raise ArticleRagRetrievalServiceError(
                _P1F_MSG_QUERY_EMBEDDING_MODEL_MISMATCH,
                retryable=False,
                failure_code=(
                    FAILURE_CODE_RETRIEVAL_EMBEDDING_MODEL_MISMATCH
                ),
            )

        # Phase D.3: the vector's reported and actual dimensions must
        # both equal the immutable profile.  bool is rejected even
        # though it is an int subclass.
        expected_dimension = profile.document_embedding_dimension
        if (
            not isinstance(query_embedding.dim, int)
            or isinstance(query_embedding.dim, bool)
            or query_embedding.dim != expected_dimension
            or len(query_vector) != expected_dimension
        ):
            raise ArticleRagRetrievalServiceError(
                _P1F_MSG_QUERY_EMBEDDING_DIMENSION_MISMATCH,
                retryable=False,
                failure_code=(
                    FAILURE_CODE_RETRIEVAL_EMBEDDING_DIMENSION_MISMATCH
                ),
            )

        # Phase E: vector search (outside DB tx, no I/O to DB).  P1-F:
        # the collection is sourced from ``profile.vector_namespace`` —
        # NOT from the indexed run's ``vector_collection``.  The 5-field
        # validation in Phase C.3 already asserted
        # ``indexed.vector_collection == profile.vector_namespace``, so
        # routing by the profile is equivalent but is the canonical
        # source of truth.  Real deployments may use a non-default
        # collection name; routing to the wrong collection would either
        # return zero hits (best case) or hits from a different index
        # (worst case).
        target_collection = profile.vector_namespace
        try:
            search_result = await self._vector_searcher.search(
                collection=target_collection,
                query_vector=query_vector,
                limit=limit,
                stable_document_id=plan.stable_document_id,
            )
        except ArticleRagVectorSearcherError as exc:
            raise ArticleRagRetrievalServiceError(
                f"vector searcher raised {type(exc).__name__} "
                f"(failure_code={exc.failure_code}); see __cause__ for "
                "upstream diagnostic",
                retryable=exc.retryable,
                failure_code=FAILURE_CODE_RETRIEVAL_VECTOR_SEARCH_FAILED,
            ) from exc
        except Exception as exc:  # noqa: BLE001 — defensive catch-all
            raise ArticleRagRetrievalServiceError(
                "vector searcher raised "
                f"{type(exc).__name__}; see __cause__ for upstream "
                "diagnostic",
                retryable=False,
                failure_code=FAILURE_CODE_RETRIEVAL_VECTOR_SEARCH_FAILED,
            ) from exc

        # Phase F: join hits against the current plan on chunk_id.
        hits = self._join_hits(
            plan=plan,
            indexed=indexed,
            search_result=search_result,
            limit=limit,
        )

        logger.debug(
            "Article RAG retrieval served %d hits for record=%s "
            "limit=%d index_version=%s",
            len(hits),
            reading_record_id,
            limit,
            profile.index_version,
        )

        return ArticleRagRetrievalResult(
            reading_record_id=plan.reading_record_id,
            stable_document_id=plan.stable_document_id,
            base_id=plan.base_id,
            record_generation=plan.record_generation,
            plan_content_sha256=current_plan_hash,
            # Same ``indexed`` snapshot used for plan-hash / collection /
            # embedding-model checks — not a second "latest run" lookup.
            index_run_id=indexed.index_run_id,
            hits=hits,
            provider_metadata=dict(
                getattr(search_result, "provider_metadata", {}) or {}
            ),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _load_indexed_run(
        self,
        conn: asyncpg.Connection,
        *,
        stable_document_id: UUID,
        index_version: str,
    ) -> _IndexedRunSnapshot | None:
        """Return the queryable index run for ``stable_document_id``.

        The queryable status set is ``{"indexed"}`` — ``planned``,
        ``queued``, ``indexing``, ``failed``, ``superseded`` are all
        refused.  If multiple rows exist (shouldn't happen but we
        defend against it), the most recently updated one wins.

        ``index_version`` is an internal identity key (fixed DEFAULT
        profile until Round 2); it is not a caller-selected API parameter.
        """
        row = await conn.fetchrow(
            """
            SELECT id, stable_document_id, base_id, record_generation,
                   index_version, chunker_version, plan_content_sha256,
                   chunk_count, status, updated_at,
                   vector_collection, embedding_model,
                   profile_fingerprint
            FROM reader_article_rag_index_runs
            WHERE stable_document_id = $1
              AND index_version = $2
              AND status = ANY($3::text[])
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            stable_document_id,
            index_version,
            sorted(_INDEX_RUN_QUERYABLE_STATUSES),
        )
        if row is None:
            return None
        return _IndexedRunSnapshot(
            index_run_id=row["id"],
            stable_document_id=row["stable_document_id"],
            base_id=row["base_id"],
            record_generation=int(row["record_generation"]),
            index_version=str(row["index_version"]),
            chunker_version=str(row["chunker_version"]),
            plan_content_sha256=str(row["plan_content_sha256"]),
            chunk_count=int(row["chunk_count"]),
            status=str(row["status"]),
            vector_collection=(
                str(row["vector_collection"])
                if row["vector_collection"] is not None
                else None
            ),
            embedding_model=(
                str(row["embedding_model"])
                if row["embedding_model"] is not None
                else None
            ),
            profile_fingerprint=(
                str(row["profile_fingerprint"])
                if row["profile_fingerprint"] is not None
                else None
            ),
        )

    def _join_hits(
        self,
        *,
        plan: ArticleRagIndexPlan,
        indexed: _IndexedRunSnapshot,
        search_result: ArticleRagVectorSearchResult,
        limit: int,
    ) -> tuple[ArticleRagRetrievalHit, ...]:
        """Join vector hits against the current plan on ``chunk_id``.

        Policy (locked in by the I4E test suite):
          * unknown ``chunk_id`` (not in the current plan) → drop the
            hit silently.  Rationale: pymilvus can in principle return
            hits from older index runs; we want fresh hits only.
          * duplicate ``chunk_id`` → keep the first (highest score).
          * vector guard metadata mismatch (e.g. hit's
            ``stable_document_id`` differs from
            ``plan.stable_document_id``) → **fail closed** with
            ``FAILURE_CODE_RETRIEVAL_VECTOR_METADATA_MISMATCH``.
            Rationale: a cross-document contamination event is exactly
            the kind of silent bug this service exists to prevent.
          * top-k → truncate to ``limit``.

        Returned metadata is sanitised against the denylist.
        """
        chunks_by_id: dict[str, ArticleRagIndexChunk] = {
            chunk.chunk_id: chunk for chunk in plan.chunks
        }
        seen: set[str] = set()
        hits: list[ArticleRagRetrievalHit] = []

        for vector_hit in search_result.hits:
            chunk = chunks_by_id.get(vector_hit.chunk_id)
            if chunk is None:
                # Unknown chunk_id — drop silently.
                continue

            # Vector metadata mismatch → fail closed.
            if (
                vector_hit.stable_document_id is not None
                and vector_hit.stable_document_id != plan.stable_document_id
            ):
                raise ArticleRagRetrievalServiceError(
                    f"vector hit chunk_id={vector_hit.chunk_id} "
                    f"stable_document_id={vector_hit.stable_document_id} "
                    f"does not match current plan "
                    f"stable_document_id={plan.stable_document_id}; "
                    "refusing cross-document contamination",
                    retryable=False,
                    failure_code=(
                        FAILURE_CODE_RETRIEVAL_VECTOR_METADATA_MISMATCH
                    ),
                )
            if (
                vector_hit.base_id is not None
                and vector_hit.base_id != plan.base_id
            ):
                raise ArticleRagRetrievalServiceError(
                    f"vector hit chunk_id={vector_hit.chunk_id} "
                    f"base_id={vector_hit.base_id} does not match current "
                    f"plan base_id={plan.base_id}; refusing "
                    "cross-document contamination",
                    retryable=False,
                    failure_code=(
                        FAILURE_CODE_RETRIEVAL_VECTOR_METADATA_MISMATCH
                    ),
                )
            if (
                vector_hit.index_version is not None
                and vector_hit.index_version != indexed.index_version
            ):
                raise ArticleRagRetrievalServiceError(
                    f"vector hit chunk_id={vector_hit.chunk_id} "
                    f"index_version={vector_hit.index_version} does not "
                    f"match indexed run index_version="
                    f"{indexed.index_version}; refusing "
                    "cross-version contamination",
                    retryable=False,
                    failure_code=(
                        FAILURE_CODE_RETRIEVAL_VECTOR_METADATA_MISMATCH
                    ),
                )
            if (
                vector_hit.plan_content_sha256 is not None
                and vector_hit.plan_content_sha256 != indexed.plan_content_sha256
            ):
                raise ArticleRagRetrievalServiceError(
                    f"vector hit chunk_id={vector_hit.chunk_id} "
                    f"plan_content_sha256="
                    f"{vector_hit.plan_content_sha256} does not match "
                    f"indexed run plan_content_sha256="
                    f"{indexed.plan_content_sha256}; refusing "
                    "drift contamination",
                    retryable=False,
                    failure_code=(
                        FAILURE_CODE_RETRIEVAL_VECTOR_METADATA_MISMATCH
                    ),
                )

            if vector_hit.chunk_id in seen:
                # Duplicate chunk_id — keep the first (highest score).
                continue
            seen.add(vector_hit.chunk_id)

            citation_dict = self._citation_dict_from_chunk(chunk)
            sanitised_metadata = self._scrub_metadata(
                chunk.metadata_json, hit_chunk_id=chunk.chunk_id
            )
            hits.append(
                ArticleRagRetrievalHit(
                    chunk_id=chunk.chunk_id,
                    text=chunk.text,
                    citation=citation_dict,
                    metadata_json=sanitised_metadata,
                    score=float(vector_hit.score),
                    content_sha256=str(chunk.content_sha256),
                )
            )
            if len(hits) >= limit:
                break

        return tuple(hits)

    @staticmethod
    def _citation_dict_from_chunk(
        chunk: ArticleRagIndexChunk,
    ) -> dict[str, Any]:
        """Build the canonical 9-key citation dict for a plan chunk.

        Mirrors the I4D writer's citation shape: only the canonical
        citation fields, in a fixed order, with UUIDs stringified and
        offsets preserved as ``int | None``.  No ``dataclasses.asdict``
        is used (defence in depth — prevents accidental leakage of
        unrelated frozen-dataclass state).
        """
        c = chunk.citation
        return {
            "reading_record_id": str(c.reading_record_id),
            "stable_document_id": str(c.stable_document_id),
            "base_id": str(c.base_id),
            "record_generation": int(c.record_generation),
            "block_ids": [str(bid) for bid in c.block_ids],
            "unit_ids": [str(uid) for uid in c.unit_ids],
            "anchor_segment_ids": [
                str(sid) for sid in c.anchor_segment_ids
            ],
            "canonical_text_start_utf16": (
                int(c.canonical_text_start_utf16)
                if c.canonical_text_start_utf16 is not None
                else None
            ),
            "canonical_text_end_utf16": (
                int(c.canonical_text_end_utf16)
                if c.canonical_text_end_utf16 is not None
                else None
            ),
        }

    @staticmethod
    def _scrub_metadata(
        metadata: dict[str, Any],
        *,
        hit_chunk_id: str,
    ) -> dict[str, Any]:
        """Strip forbidden keys from returned metadata.

        Defence-in-depth: even though I4A guarantees the plan's
        per-chunk metadata comes from a fixed whitelist, we re-check
        here so a future regression in I4A cannot leak Plate / Markdown
        / DOM / Slate / UI display group fields into the retrieval
        response.
        """
        sanitised: dict[str, Any] = {}
        for key, value in metadata.items():
            if key in _FORBIDDEN_RESULT_METADATA_KEYS:
                # Skip silently — we don't want the denylist membership
                # itself to surface in the response.
                continue
            sanitised[str(key)] = value
        # Tag the hit with the chunk_id so downstream consumers can
        # cross-reference without depending on position.
        sanitised.setdefault("chunk_id", str(hit_chunk_id))
        return sanitised


__all__ = [
    "DEFAULT_INDEX_VERSION",
    "MAX_RETRIEVAL_LIMIT",
    "FAILURE_CODE_RETRIEVAL_NO_INDEXED_RUN",
    "FAILURE_CODE_RETRIEVAL_PLAN_HASH_MISMATCH",
    "FAILURE_CODE_RETRIEVAL_INDEX_RUN_PLAN_MISMATCH",
    "FAILURE_CODE_RETRIEVAL_EMPTY_QUERY",
    "FAILURE_CODE_RETRIEVAL_INVALID_LIMIT",
    "FAILURE_CODE_RETRIEVAL_VECTOR_METADATA_MISMATCH",
    "FAILURE_CODE_RETRIEVAL_EMBEDDING_FAILED",
    "FAILURE_CODE_RETRIEVAL_VECTOR_SEARCH_FAILED",
    "FAILURE_CODE_RETRIEVAL_NO_VECTOR_COLLECTION",
    "FAILURE_CODE_RETRIEVAL_EMBEDDING_MODEL_MISMATCH",
    "FAILURE_CODE_RETRIEVAL_EMBEDDING_DIMENSION_MISMATCH",
    "FAILURE_CODE_RETRIEVAL_INDEX_PROFILE_INVALID",
    "FAILURE_CODE_RETRIEVAL_INDEX_PROFILE_MISMATCH",
    "ArticleRagRetrievalServiceError",
    "ArticleRagRetrievalHit",
    "ArticleRagRetrievalResult",
    "ArticleRagRetrievalService",
]