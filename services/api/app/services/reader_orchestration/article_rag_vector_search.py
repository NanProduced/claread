"""D6-I4E: Article RAG Vector Search Adapter Foundation.

Defines the read-side ``ArticleRagVectorSearcher`` Protocol that the
D6-I4E retrieval service depends on, plus a real Zilliz / Milvus
search adapter foundation (lazy ``pymilvus`` init, fail-closed default,
opt-in smoke), an in-memory ``FakeArticleRagVectorSearcher`` for tests,
and a settings-driven ``build_default_article_rag_vector_searcher``
factory.

Truth boundary
--------------

A vector hit returned by a searcher carries ``chunk_id`` and a ``score``
*only*.  Anything else returned by the searcher
(``stable_document_id``, ``base_id``, ``index_version``,
``plan_content_sha256``) is **guard metadata**, never a fact source —
the I4E retrieval service ignores the hit's payload-derived citation
fields and joins the hit against the current
:class:`app.services.reader_orchestration.article_rag_index_plan.ArticleRagIndexPlan`
on ``chunk_id``.  Citation truth therefore always returns to Postgres
(``stable_document_blocks`` / ``reading_bases.text`` /
``reading_units`` / ``anchor_segments``); Zilliz is only an index
replica.

The Zilliz search adapter does NOT trust vector payload text or
citation content.  It returns ``chunk_id`` and ``score`` (and the
optional guard metadata as ``dict[str, Any]`` for diagnostics) — the
caller must join against the current plan.

Security contract
-----------------

* The Zilliz token is **never** logged, **never** included in
  exception messages, **never** echoed in ``provider_metadata``.
* The query vector and the query text are **never** logged at INFO
  or higher; only the collection name and the limit are logged at
  DEBUG level.
* Vector hits returned by the SDK are sanitised: the adapter extracts
  only ``chunk_id`` and ``score``, plus the four named guard fields if
  the SDK happens to surface them.  No other SDK response field
  crosses the wire.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

from .article_rag_index_worker import (
    ArticleRagIndexWorkerError,
    FAILURE_CODE_VECTOR_WRITER_UNCONFIGURED,
    UnconfiguredArticleRagVectorWriter,
)

if TYPE_CHECKING:
    from app.config.settings import Settings
    from pymilvus import MilvusClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Provider name constants (module-local; factory is the sanctioned entry point)
# ---------------------------------------------------------------------------

READER_ARTICLE_RAG_VECTOR_SEARCHER_ZILLIZ = "zilliz"


# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------


class ArticleRagVectorSearcherError(ArticleRagIndexWorkerError):
    """Typed failure raised by :class:`ArticleRagVectorSearcher` adapters.

    Inherits :class:`ArticleRagIndexWorkerError` so a future orchestrator
    that catches the worker base class also catches vector-search failures
    consistently.  ``failure_class="vector_search"`` distinguishes this
    from write-side failures in diagnostics.

    The error message is a fixed diagnostic that explicitly excludes the
    query text, the query vector, the collection token, and the URI.
    The underlying SDK exception (if any) is preserved as ``__cause__``
    for ops inspection.
    """

    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        failure_code: str,
        failure_class: str = "vector_search",
        rationale_code: str | None = None,
    ) -> None:
        super().__init__(
            message,
            retryable=retryable,
            failure_class=failure_class,
            failure_code=failure_code,
            rationale_code=rationale_code,
        )


# Re-export the writer-side unconfigured failure code so retrieval
# callers don't have to import both modules.
FAILURE_CODE_VECTOR_SEARCHER_UNCONFIGURED = FAILURE_CODE_VECTOR_WRITER_UNCONFIGURED


# ---------------------------------------------------------------------------
# Hit + result dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ArticleRagVectorSearchHit:
    """One hit returned by a vector searcher.

    The hit carries ``chunk_id`` + ``score`` as the canonical contract.
    The four guard fields below (``stable_document_id``, ``base_id``,
    ``index_version``, ``plan_content_sha256``) are populated by the
    adapter **only** when the SDK surfaces them as named columns — they
    are diagnostics, never a fact source.  The I4E retrieval service
    uses them at most as a guard; it joins hits against the current plan
    on ``chunk_id`` to obtain authoritative citation + text.
    """

    chunk_id: str
    score: float
    # Optional guard metadata.  May be ``None`` if the searcher does not
    # surface them, or if the hit row's column was NULL.
    stable_document_id: UUID | None = None
    base_id: UUID | None = None
    index_version: str | None = None
    plan_content_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class ArticleRagVectorSearchResult:
    """Result of a vector search call.

    ``hits`` is the list of hits in score-descending order (the searcher
    is responsible for this ordering).  ``provider_metadata`` carries
    diagnostic info that the retrieval service may surface to ops but
    MUST NOT surface to end-users as a fact source.
    """

    hits: tuple[ArticleRagVectorSearchHit, ...]
    provider_metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Searcher Protocol
# ---------------------------------------------------------------------------


class ArticleRagVectorSearcher(Protocol):
    """Searches the Article RAG vector store for hits similar to a query.

    Implementations MUST NOT:
      * log the query vector or the query text at INFO or higher
      * include the query vector, query text, or token in raised
        exception messages
      * call real Zilliz / Milvus unless explicitly configured

    Implementations MUST:
      * return hits in score-descending order
      * populate ``chunk_id`` + ``score`` on every hit; the four guard
        fields may be ``None`` when the SDK does not surface them
      * be idempotent (no state changes) — this is a read-only service
    """

    async def search(
        self,
        *,
        collection: str,
        query_vector: tuple[float, ...],
        limit: int,
        stable_document_id: UUID | None = None,
        index_version: str | None = None,
    ) -> ArticleRagVectorSearchResult: ...


# ---------------------------------------------------------------------------
# Unconfigured searcher (fail-closed default)
# ---------------------------------------------------------------------------


class UnconfiguredArticleRagVectorSearcher:
    """Default searcher — fails closed, no Zilliz/Milvus calls."""

    async def search(
        self,
        *,
        collection: str,
        query_vector: tuple[float, ...],
        limit: int,
        stable_document_id: UUID | None = None,
        index_version: str | None = None,
    ) -> ArticleRagVectorSearchResult:
        raise ArticleRagVectorSearcherError(
            "article RAG vector searcher is not configured; inject an "
            "explicit fake searcher for tests or wire a real Zilliz / "
            "Milvus searcher for production",
            retryable=False,
            failure_code=FAILURE_CODE_VECTOR_SEARCHER_UNCONFIGURED,
        )


# ---------------------------------------------------------------------------
# Fake searcher (deterministic, no network)
# ---------------------------------------------------------------------------


class FakeArticleRagVectorSearcher:
    """Deterministic in-memory fake for tests.

    ``hits`` is a list of ``ArticleRagVectorSearchHit``.  ``search``
    returns the first ``limit`` hits in score-descending order.  Records
    every call in ``search_calls`` for assertion.

    No network calls.  No external dependencies.
    """

    def __init__(
        self,
        *,
        hits: list[ArticleRagVectorSearchHit] | None = None,
    ) -> None:
        self._hits: list[ArticleRagVectorSearchHit] = list(hits or [])
        self.search_calls: list[dict[str, Any]] = []

    @property
    def provider_name(self) -> str:
        return "fake-in-memory"

    def set_hits(self, hits: list[ArticleRagVectorSearchHit]) -> None:
        """Replace the in-memory hit list (test-only)."""
        self._hits = list(hits)

    async def search(
        self,
        *,
        collection: str,
        query_vector: tuple[float, ...],
        limit: int,
        stable_document_id: UUID | None = None,
        index_version: str | None = None,
    ) -> ArticleRagVectorSearchResult:
        self.search_calls.append(
            {
                "collection": collection,
                "query_vector": tuple(query_vector),
                "limit": int(limit),
                "stable_document_id": (
                    str(stable_document_id) if stable_document_id else None
                ),
                "index_version": index_version,
            }
        )
        if limit <= 0:
            return ArticleRagVectorSearchResult(
                hits=(),
                provider_metadata={"provider": self.provider_name},
            )
        # Score-descending order is preserved by the caller passing
        # already-sorted hits; we still slice defensively in case a
        # test author forgets.
        sorted_hits = sorted(
            self._hits, key=lambda h: h.score, reverse=True
        )
        return ArticleRagVectorSearchResult(
            hits=tuple(sorted_hits[:limit]),
            provider_metadata={"provider": self.provider_name},
        )


# ---------------------------------------------------------------------------
# Real Zilliz searcher (lazy pymilvus init, asyncio.to_thread)
# ---------------------------------------------------------------------------


def _extract_chunk_id(entry: dict[str, Any]) -> str | None:
    """Extract ``chunk_id`` from a pymilvus search hit, tolerating
    three observed shapes.

    Real pymilvus (2.6.x / 2.7.x) and Milvus (2.4.x / 2.5.x) have
    surfaced the primary key in different shapes depending on
    configuration:

      1. ``{"chunk_id": "...", ...}`` — when the field is named in
         ``output_fields`` (the shape the I4D writer produces and
         the shape our ``output_fields`` config now explicitly
         requests).
      2. ``{"entity": {"chunk_id": "..."}, "id": int64}`` — the
         entity-wrapper shape used by pymilvus 2.5+
         ``search(..., use_full_content=True)`` and some
         ``AnnSearchRequest`` responses.  The top-level ``id`` is
         always present as an int64; the entity has the named
         columns.
      3. ``{"id": int64|string}`` — older clients / default ``_id``
         projection.  When ``chunk_id`` is the primary key but is
         NOT explicitly named in ``output_fields``, pymilvus may
         surface it as ``id``.  We accept this as a last-resort
         fallback.

    Returns the stringified chunk id, or ``None`` when none of the
    three shapes carry a usable value.  We NEVER raise — a missing
    chunk_id on one entry is a per-hit degradation that the caller
    decides how to handle.

    Resolution order:
      1. top-level ``chunk_id`` (when ``output_fields`` includes it)
      2. ``entity.chunk_id`` (entity-wrapper shape)
      3. top-level ``id`` (legacy / fallback)

    The order matters: shape 2 always carries a top-level ``id``
    that is just the int64 primary key, so we must check
    ``entity.chunk_id`` BEFORE ``id`` to avoid returning the int64.
    """
    # Shape 1: top-level chunk_id.
    direct = entry.get("chunk_id")
    if direct is not None and str(direct) != "":
        return str(direct)
    # Shape 2: entity-wrapper (pymilvus 2.5+ use_full_content=True).
    # Check this BEFORE the top-level ``id`` fallback because the
    # entity-wrapper response always has both ``id`` (int64 PK) and
    # ``entity.chunk_id`` (string PK).
    entity = entry.get("entity")
    if isinstance(entity, dict):
        nested = entity.get("chunk_id")
        if nested is not None and str(nested) != "":
            return str(nested)
    # Shape 3: top-level ``id`` fallback (older pymilvus clients).
    id_value = entry.get("id")
    if id_value is not None and str(id_value) != "":
        return str(id_value)
    return None


def _extract_field(entry: dict[str, Any], field_name: str) -> Any | None:
    """Read a named field from a pymilvus hit, tolerating the same
    three shapes as :func:`_extract_chunk_id`.

    The entity-wrapper shape ``{"entity": {field_name: value}, "id": int}``
    is used by pymilvus 2.5+ ``search(..., use_full_content=True)`` and
    by some ``AnnSearchRequest`` responses.  When the field is NOT in
    ``output_fields`` at the top level, pymilvus surfaces it only via
    the entity; top-level lookup would return ``None`` and the
    retrieval service's vector-mismatch fail-closed policy would
    silently degrade.

    Resolution order (same as ``_extract_chunk_id``):
      1. top-level ``field_name``
      2. ``entity[field_name]`` (entity-wrapper shape)
      3. ``None``

    Returns the raw value (untyped) so callers can apply their own
    type coercion (UUID / int / str).
    """
    direct = entry.get(field_name)
    if direct is not None:
        return direct
    entity = entry.get("entity")
    if isinstance(entity, dict):
        nested = entity.get(field_name)
        if nested is not None:
            return nested
    return None


class ZillizArticleRagVectorSearcher:
    """Real Zilliz / Milvus searcher for the Article RAG retrieval service.

    Mirrors :class:`ZillizArticleRagVectorWriter` from D6-I4D:

    * Constructor does **not** open a network connection.  The first
      :meth:`search` call lazily constructs
      :class:`pymilvus.MilvusClient` inside ``asyncio.to_thread`` so the
      event loop is not blocked during the SDK handshake.
    * Search results carry ``chunk_id`` + ``score`` only (plus optional
      guard metadata when the SDK surfaces the columns).  Vector
      payload text or citation content is **never** read.
    * Token, URI, and query vector are **never** logged or echoed in
      exception messages.
    """

    def __init__(
        self,
        *,
        uri: str,
        token: str,
        collection: str,
    ) -> None:
        if not (uri or "").strip():
            raise ArticleRagVectorSearcherError(
                "ZillizArticleRagVectorSearcher constructed with an empty uri",
                retryable=False,
                failure_code="vector_searcher_unconfigured",
            )
        if not (token or "").strip():
            raise ArticleRagVectorSearcherError(
                "ZillizArticleRagVectorSearcher constructed with an empty "
                "token",
                retryable=False,
                failure_code="vector_searcher_unconfigured",
            )
        if not (collection or "").strip():
            raise ArticleRagVectorSearcherError(
                "ZillizArticleRagVectorSearcher constructed with an empty "
                "collection",
                retryable=False,
                failure_code="vector_searcher_unconfigured",
            )
        self._uri = uri.strip()
        self._token = token.strip()
        self._collection = collection.strip()
        self._client: "MilvusClient | None" = None

    @property
    def provider_name(self) -> str:
        return READER_ARTICLE_RAG_VECTOR_SEARCHER_ZILLIZ

    def _ensure_client(self) -> "MilvusClient":
        """Lazily construct the pymilvus MilvusClient on first use.

        Raises :class:`ArticleRagVectorSearcherError` with
        ``failure_code="vector_searcher_sdk_missing"`` if pymilvus is not
        installed — never propagates a raw ``ImportError``.
        """
        if self._client is not None:
            return self._client
        try:
            from pymilvus import MilvusClient  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ArticleRagVectorSearcherError(
                "pymilvus SDK is not installed; cannot construct "
                "ZillizArticleRagVectorSearcher",
                retryable=False,
                failure_code="vector_searcher_sdk_missing",
            ) from exc
        logger.debug(
            "Constructing pymilvus MilvusClient for article RAG vector "
            "searcher (collection=%s, uri=set)",
            self._collection,
        )
        self._client = MilvusClient(uri=self._uri, token=self._token)
        return self._client

    async def search(
        self,
        *,
        collection: str,
        query_vector: tuple[float, ...],
        limit: int,
        stable_document_id: UUID | None = None,
        index_version: str | None = None,
    ) -> ArticleRagVectorSearchResult:
        """Search ``collection`` for the ``limit`` nearest neighbours.

        Returns hits in score-descending order.  The Zilliz ``search``
        API accepts a ``filter`` expression; we apply an optional
        ``stable_document_id`` filter when supplied.  Vector payload
        text / citation content is never read — only ``chunk_id``,
        ``score``, and the four named guard columns are extracted.
        """
        if limit <= 0:
            return ArticleRagVectorSearchResult(
                hits=(), provider_metadata={"provider": self.provider_name}
            )
        if (collection or "").strip() != self._collection:
            # Defensive: refuse if the caller targets a collection that
            # the writer did not provision.  Fail closed with a clear
            # message; the URI / token are not echoed.
            raise ArticleRagVectorSearcherError(
                "ZillizArticleRagVectorSearcher received a mismatched "
                f"collection {collection!r} (searcher configured for "
                f"{self._collection!r})",
                retryable=False,
                failure_code="vector_searcher_collection_mismatch",
            )
        if not query_vector:
            raise ArticleRagVectorSearcherError(
                "ZillizArticleRagVectorSearcher received an empty "
                "query_vector",
                retryable=False,
                failure_code="vector_searcher_empty_query",
            )

        # Build the optional Milvus filter expression.  Only well-formed
        # values are forwarded; we never concatenate the URI / token /
        # query text into the filter.
        filter_expr: str | None = None
        if stable_document_id is not None or index_version is not None:
            parts: list[str] = []
            if stable_document_id is not None:
                parts.append(
                    f'stable_document_id == "{stable_document_id}"'
                )
            if index_version is not None and (index_version or "").strip():
                parts.append(f'index_version == "{index_version}"')
            filter_expr = " and ".join(parts) if parts else None

        def _sync_search() -> list[dict[str, Any]]:
            client = self._ensure_client()
            raw = client.search(
                collection_name=self._collection,
                data=[list(query_vector)],
                limit=int(limit),
                output_fields=[
                    # ``chunk_id`` is the primary key — we must request
                    # it explicitly so it appears in the hit dict.
                    # Without this, pymilvus 2.6.x surfaces the primary
                    # key only when explicitly named in
                    # ``output_fields``; missing it would force us to
                    # fall through to the ``id`` / ``entity.chunk_id``
                    # heuristics below, which are defensive only.
                    "chunk_id",
                    "stable_document_id",
                    "base_id",
                    "index_version",
                    "plan_content_sha256",
                ],
                filter=filter_expr,
            )
            # pymilvus returns a list of lists — one inner list per
            # query vector.  Flatten to the single inner list.
            if not raw:
                return []
            if not isinstance(raw, list) or not raw:
                return []
            return list(raw[0])

        try:
            raw_hits = await asyncio.to_thread(_sync_search)
        except ArticleRagVectorSearcherError:
            raise
        except Exception as exc:  # noqa: BLE001 — SDK-level catch-all
            # Per Fix 5 precedent: never forward the original SDK
            # message — it may echo query text or token.  Surface a
            # fixed diagnostic naming the wrapper, the limit, and the
            # SDK exception class only.
            raise ArticleRagVectorSearcherError(
                "Zilliz search failed via pymilvus "
                f"(limit={limit}, wrapper_exc={type(exc).__name__}); "
                "see __cause__ for upstream diagnostic",
                retryable=True,
                failure_code="vector_search_backend_failed",
            ) from exc

        hits: list[ArticleRagVectorSearchHit] = []
        for entry in raw_hits:
            if not isinstance(entry, dict):
                continue
            chunk_id = _extract_chunk_id(entry)
            if chunk_id is None:
                # Skip malformed entries — fail closed at the SDK layer.
                # We do NOT raise here because a partial miss is a
                # recoverable degradation (the caller still receives the
                # well-formed hits); raising would discard a valid hit
                # set because of one corrupted row.
                continue
            score_raw = entry.get("distance")
            try:
                score = float(score_raw) if score_raw is not None else 0.0
            except (TypeError, ValueError):
                score = 0.0
            # Guard fields.  Each is read via ``_extract_field`` so an
            # entity-wrapper hit (``{"entity": {...}, "id": ...}``)
            # also surfaces its guard metadata.  Without this, a
            # real pymilvus response would leak ``None`` for every
            # guard field and the retrieval service's
            # vector-mismatch fail-closed policy would never fire.
            sd_raw = _extract_field(entry, "stable_document_id")
            try:
                sd_uuid = UUID(str(sd_raw)) if sd_raw else None
            except (TypeError, ValueError):
                sd_uuid = None
            base_raw = _extract_field(entry, "base_id")
            try:
                base_uuid = UUID(str(base_raw)) if base_raw else None
            except (TypeError, ValueError):
                base_uuid = None
            iv_raw = _extract_field(entry, "index_version")
            iv_str = str(iv_raw) if iv_raw else None
            psha_raw = _extract_field(entry, "plan_content_sha256")
            psha_str = str(psha_raw) if psha_raw else None
            hits.append(
                ArticleRagVectorSearchHit(
                    chunk_id=chunk_id,
                    score=score,
                    stable_document_id=sd_uuid,
                    base_id=base_uuid,
                    index_version=iv_str,
                    plan_content_sha256=psha_str,
                )
            )

        return ArticleRagVectorSearchResult(
            hits=tuple(hits),
            provider_metadata={"provider": self.provider_name},
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_default_article_rag_vector_searcher(
    settings: Settings,
) -> ArticleRagVectorSearcher:
    """Factory for the default Article RAG vector searcher.

    Returns:
      * :class:`ZillizArticleRagVectorSearcher` only when
        ``settings.reader_article_rag_vector_provider == "zilliz"``
        AND resolved ``reader_article_rag_zilliz_uri`` is non-empty AND
        resolved ``reader_article_rag_zilliz_token`` is non-empty AND
        ``reader_article_rag_zilliz_collection`` is non-empty;
      * otherwise :class:`UnconfiguredArticleRagVectorSearcher`.

    The factory NEVER logs the token.  The factory NEVER raises on
    misconfiguration — it returns the unconfigured searcher so the
    caller surfaces ``FAILURE_CODE_VECTOR_SEARCHER_UNCONFIGURED``
    through the retrieval service's error handlers, not as a startup
    failure.

    The factory intentionally reuses the **write-side** Zilliz
    configuration (resolved ``reader_article_rag_zilliz_uri`` / ``_token`` /
    ``_collection``) — the reader and the writer target the same collection.
    Future work may split these into dedicated fields if the searcher ever
    needs to target a different read replica.
    """
    provider_name = (
        getattr(settings, "reader_article_rag_vector_provider", "") or ""
    ).strip().lower()
    if provider_name != READER_ARTICLE_RAG_VECTOR_SEARCHER_ZILLIZ:
        logger.debug(
            "Article RAG vector searcher not configured "
            "(reader_article_rag_vector_provider=%r); using "
            "UnconfiguredArticleRagVectorSearcher",
            provider_name,
        )
        return UnconfiguredArticleRagVectorSearcher()

    resolve_uri = getattr(settings, "resolve_reader_article_rag_zilliz_uri", None)
    uri = (
        resolve_uri()
        if callable(resolve_uri)
        else getattr(settings, "reader_article_rag_zilliz_uri", "")
    )
    uri = (uri or "").strip()

    resolve_token = getattr(
        settings, "resolve_reader_article_rag_zilliz_token", None
    )
    token = (
        resolve_token()
        if callable(resolve_token)
        else getattr(settings, "reader_article_rag_zilliz_token", "")
    )
    token = (token or "").strip()
    collection = (
        getattr(settings, "reader_article_rag_zilliz_collection", "") or ""
    ).strip()

    if not uri or not token or not collection:
        logger.debug(
            "Article RAG vector searcher='zilliz' but configuration is "
            "incomplete (uri/empty=%s, token/empty=%s, "
            "collection/empty=%s); using "
            "UnconfiguredArticleRagVectorSearcher",
            not uri,
            not token,
            not collection,
        )
        return UnconfiguredArticleRagVectorSearcher()

    return ZillizArticleRagVectorSearcher(
        uri=uri,
        token=token,
        collection=collection,
    )


# Re-export the writer-side unconfigured class under a searcher name
# so callers can import either with the same identifier from the
# retrieval service module.  This is purely a typing convenience —
# runtime behaviour is unchanged.
SearcherLike = (
    UnconfiguredArticleRagVectorSearcher
    | FakeArticleRagVectorSearcher
    | ZillizArticleRagVectorSearcher
)


__all__ = [
    "READER_ARTICLE_RAG_VECTOR_SEARCHER_ZILLIZ",
    "ArticleRagVectorSearcherError",
    "FAILURE_CODE_VECTOR_SEARCHER_UNCONFIGURED",
    "ArticleRagVectorSearchHit",
    "ArticleRagVectorSearchResult",
    "ArticleRagVectorSearcher",
    "UnconfiguredArticleRagVectorSearcher",
    "FakeArticleRagVectorSearcher",
    "ZillizArticleRagVectorSearcher",
    "build_default_article_rag_vector_searcher",
    "SearcherLike",
]
