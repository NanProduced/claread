"""Article RAG exact-id vector deletion adapter (Wave 9).

Minimal production vector-delete seam for the Article RAG single path:
enumerate by ``stable_document_id`` (discovery only) and delete by exact
``chunk_id`` primary keys in fixed deterministic batches.  The deletion
protocol is deliberately separate from the upsert protocol — existing
writer fakes are NOT forced to implement delete.

Invariants
----------

* The delete path NEVER creates, drops, or compacts a collection.
* The delete filter NEVER uses ``stable_document_id`` — only exact
  ``chunk_id`` primary keys discovered by enumeration.
* Every discovered ``chunk_id`` must be a 64-char lowercase SHA-256 hex
  string; any malformed id fails closed BEFORE any delete call.
* Discovery is capped at a fixed limit; exceeding it fails closed with
  zero delete calls.
* Collection missing or empty enumeration is an idempotent success with
  ``outcome="no_vectors"`` and zero delete calls.
* After deletion a full re-enumeration must confirm zero rows; leftover
  rows are a retryable failure (the next attempt converges them).
* SDK exceptions map to fixed safe errors: no collection name, no ids,
  no stable document id, no URI/token, no raw SDK text.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

from app.config.settings import Settings

from .article_rag_index_worker import ArticleRagIndexWorkerError

if TYPE_CHECKING:
    from pymilvus import MilvusClient

logger = logging.getLogger(__name__)

# Fixed discovery cap.  Deliberately NOT configurable — a bounded enum
# is a safety property, not a tuning knob.
GC_DISCOVERY_LIMIT = 10_000
# Fixed deterministic delete batch size.
GC_DELETE_BATCH_SIZE = 100

# Canonical Article RAG chunk-id shape: the deterministic chunk id in
# article_rag_index_plan is the first 16 hex chars of a SHA-256 digest
# (lowercase).  The safety property is: fixed-length lowercase hex and
# nothing else — no wildcards, no user content, no quotes.  Anything
# else fails closed BEFORE any delete call.
_CHUNK_ID_PATTERN = re.compile(r"[0-9a-f]{16}")

# Provider name constant — mirrors article_rag_vector_store.
READER_ARTICLE_RAG_VECTOR_PROVIDER_ZILLIZ = "zilliz"

# Failure codes.  ``unsafe_chunk_id`` / ``discovery_limit_exceeded`` /
# ``collection_mismatch`` / ``malformed_identity`` are non-retryable
# (the GC service maps them to failed_terminal); the ``vector_deletion_*``
# codes are retryable (the GC service schedules retries).
FAILURE_CODE_UNSAFE_CHUNK_ID = "unsafe_chunk_id"
FAILURE_CODE_DISCOVERY_LIMIT_EXCEEDED = "discovery_limit_exceeded"
FAILURE_CODE_COLLECTION_MISMATCH = "collection_mismatch"
FAILURE_CODE_MALFORMED_IDENTITY = "malformed_identity"
FAILURE_CODE_UNCONFIGURED = "vector_deleter_unconfigured"
FAILURE_CODE_FLUSH_FAILED = "vector_deletion_flush_failed"
FAILURE_CODE_QUERY_FAILED = "vector_deletion_query_failed"
FAILURE_CODE_DELETE_FAILED = "vector_deletion_delete_failed"
FAILURE_CODE_VERIFY_FAILED = "vector_deletion_verify_failed"
FAILURE_CODE_SDK_ERROR = "vector_deletion_sdk_error"

# Fixed safe messages — MUST NOT echo collection names, chunk ids,
# stable document ids, URIs, tokens, or raw SDK text.
_MSG_COLLECTION_MISMATCH = (
    "Article RAG vector deleter collection does not match the configured "
    "deletion collection"
)
_MSG_UNSAFE_CHUNK_ID = (
    "Article RAG vector discovery returned an unsafe chunk id"
)
_MSG_MALFORMED_IDENTITY = (
    "Article RAG vector deletion received an invalid stable document identity"
)
_MSG_DISCOVERY_LIMIT_EXCEEDED = (
    "Article RAG vector discovery exceeded the fixed safety limit"
)
_MSG_QUERY_FAILED = (
    "Article RAG vector discovery query failed via the vector SDK"
)
_MSG_FLUSH_FAILED = (
    "Article RAG vector flush failed via the vector SDK"
)
_MSG_DELETE_FAILED = (
    "Article RAG vector delete failed via the vector SDK"
)
_MSG_VERIFY_FAILED = (
    "Article RAG vector post-delete verification found leftover rows"
)
_MSG_SDK_ERROR = (
    "Article RAG vector deletion failed via the vector SDK"
)
_MSG_UNCONFIGURED = (
    "article RAG vector deleter is not configured; inject an explicit "
    "deleter for tests or wire a real Zilliz / Milvus deleter for production"
)


@dataclass(frozen=True, slots=True)
class ArticleRagVectorDeletionResult:
    """Result of one exact-id deletion pass for one stable document."""

    outcome: Literal["deleted", "no_vectors"]
    discovered_chunk_count: int
    deleted_chunk_count: int
    delete_call_count: int


class ArticleRagVectorDeletionError(ArticleRagIndexWorkerError):
    """Typed deletion failure with fixed safe diagnostics.

    ``retryable=True`` → the GC service schedules a retry event.
    ``retryable=False`` → the GC service writes failed_terminal.
    """

    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        failure_code: str,
        failure_class: str = "vector_deletion",
    ) -> None:
        super().__init__(
            message,
            retryable=retryable,
            failure_class=failure_class,
            failure_code=failure_code,
        )


class ArticleRagVectorDeleter:
    """Deletes every vector row of one stable document by exact chunk ids.

    The ``collection`` argument is the deletion target; implementations
    must refuse a collection that differs from their configured one.
    """

    async def delete_for_stable_document(
        self,
        *,
        collection: str,
        stable_document_id: UUID,
    ) -> ArticleRagVectorDeletionResult: ...


class UnconfiguredArticleRagVectorDeleter:
    """Fail-closed default — no network calls, retryable via retry events."""

    async def delete_for_stable_document(
        self,
        *,
        collection: str,
        stable_document_id: UUID,
    ) -> ArticleRagVectorDeletionResult:
        raise ArticleRagVectorDeletionError(
            _MSG_UNCONFIGURED,
            retryable=True,
            failure_code=FAILURE_CODE_UNCONFIGURED,
            failure_class="configuration",
        )


class ZillizArticleRagVectorDeleter:
    """Real Zilliz / Milvus exact-id vector deleter.

    ``client_factory`` is a test seam: production uses the default
    pymilvus ``MilvusClient`` construction; tests inject an in-memory
    fake.  SDK calls run inside :func:`asyncio.to_thread`.
    """

    def __init__(
        self,
        *,
        uri: str,
        token: str,
        collection: str,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        if not uri or not uri.strip():
            raise ArticleRagVectorDeletionError(
                "Article RAG vector deleter constructed without a URI",
                retryable=False,
                failure_code=FAILURE_CODE_UNCONFIGURED,
                failure_class="configuration",
            )
        if not token or not token.strip():
            raise ArticleRagVectorDeletionError(
                "Article RAG vector deleter constructed without a token",
                retryable=False,
                failure_code=FAILURE_CODE_UNCONFIGURED,
                failure_class="configuration",
            )
        if not collection or not collection.strip():
            raise ArticleRagVectorDeletionError(
                "Article RAG vector deleter constructed without a collection name",
                retryable=False,
                failure_code=FAILURE_CODE_UNCONFIGURED,
                failure_class="configuration",
            )
        self._uri = uri.strip()
        self._token = token  # held only for SDK construction; never logged.
        self._collection = collection.strip()
        self._client_factory = client_factory
        self._client: MilvusClient | None = None

    @property
    def provider_name(self) -> str:
        return READER_ARTICLE_RAG_VECTOR_PROVIDER_ZILLIZ

    def _ensure_client(self) -> MilvusClient:
        if self._client is not None:
            return self._client
        if self._client_factory is not None:
            self._client = self._client_factory()
            return self._client
        try:
            from pymilvus import MilvusClient  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ArticleRagVectorDeletionError(
                "pymilvus SDK is not installed; cannot construct Zilliz "
                "vector deleter",
                retryable=False,
                failure_code="vector_deleter_sdk_missing",
                failure_class="sdk_unavailable",
            ) from exc
        logger.debug(
            "Constructing pymilvus MilvusClient for article RAG vector deleter "
            "(uri=%s, collection=%s)",
            self._uri,
            self._collection,
        )
        self._client = MilvusClient(uri=self._uri, token=self._token)
        return self._client

    async def delete_for_stable_document(
        self,
        *,
        collection: str,
        stable_document_id: UUID,
    ) -> ArticleRagVectorDeletionResult:
        if collection != self._collection:
            raise ArticleRagVectorDeletionError(
                _MSG_COLLECTION_MISMATCH,
                retryable=False,
                failure_code=FAILURE_CODE_COLLECTION_MISMATCH,
                failure_class="vector_collection_mismatch",
            )
        if not isinstance(stable_document_id, UUID):
            raise ArticleRagVectorDeletionError(
                _MSG_MALFORMED_IDENTITY,
                retryable=False,
                failure_code=FAILURE_CODE_MALFORMED_IDENTITY,
                failure_class="malformed_identity",
            )

        def _sync() -> ArticleRagVectorDeletionResult:
            client = self._ensure_client()
            if not client.has_collection(collection_name=collection):
                return ArticleRagVectorDeletionResult(
                    outcome="no_vectors",
                    discovered_chunk_count=0,
                    deleted_chunk_count=0,
                    delete_call_count=0,
                )
            try:
                client.flush(collection_name=collection)
            except Exception as exc:  # noqa: BLE001
                raise self._safe_error(
                    _MSG_FLUSH_FAILED,
                    FAILURE_CODE_FLUSH_FAILED,
                ) from exc

            chunk_ids = self._enumerate_chunk_ids(client, collection, stable_document_id)
            if not chunk_ids:
                return ArticleRagVectorDeletionResult(
                    outcome="no_vectors",
                    discovered_chunk_count=0,
                    deleted_chunk_count=0,
                    delete_call_count=0,
                )

            deleted_total = 0
            delete_call_count = 0
            for batch in self._stable_batches(chunk_ids):
                try:
                    client.delete(collection_name=collection, ids=batch)
                except Exception as exc:  # noqa: BLE001
                    raise self._safe_error(
                        _MSG_DELETE_FAILED,
                        FAILURE_CODE_DELETE_FAILED,
                    ) from exc
                deleted_total += len(batch)
                delete_call_count += 1

            try:
                client.flush(collection_name=collection)
            except Exception as exc:  # noqa: BLE001
                raise self._safe_error(
                    _MSG_FLUSH_FAILED,
                    FAILURE_CODE_FLUSH_FAILED,
                ) from exc

            remaining = self._enumerate_chunk_ids(client, collection, stable_document_id)
            if remaining:
                raise ArticleRagVectorDeletionError(
                    _MSG_VERIFY_FAILED,
                    retryable=True,
                    failure_code=FAILURE_CODE_VERIFY_FAILED,
                )

            return ArticleRagVectorDeletionResult(
                outcome="deleted",
                discovered_chunk_count=len(chunk_ids),
                deleted_chunk_count=deleted_total,
                delete_call_count=delete_call_count,
            )

        try:
            return await asyncio.to_thread(_sync)
        except ArticleRagVectorDeletionError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise self._safe_error(
                _MSG_SDK_ERROR,
                FAILURE_CODE_SDK_ERROR,
            ) from exc

    def _enumerate_chunk_ids(
        self,
        client: Any,
        collection: str,
        stable_document_id: UUID,
    ) -> list[str]:
        """Fully enumerate chunk ids for one stable document with validation."""
        try:
            iterator = client.query_iterator(
                collection_name=collection,
                filter=f'stable_document_id == "{stable_document_id}"',
                output_fields=["chunk_id"],
                batch_size=GC_DELETE_BATCH_SIZE,
            )
        except Exception as exc:  # noqa: BLE001
            raise self._safe_error(
                _MSG_QUERY_FAILED,
                FAILURE_CODE_QUERY_FAILED,
            ) from exc

        chunk_ids: list[str] = []
        while True:
            try:
                page = iterator.next()
            except StopIteration:
                break
            except Exception as exc:  # noqa: BLE001
                raise self._safe_error(
                    _MSG_QUERY_FAILED,
                    FAILURE_CODE_QUERY_FAILED,
                ) from exc
            if not page:
                break
            for row in page:
                raw = row.get("chunk_id") if isinstance(row, dict) else None
                if not isinstance(raw, str) or _CHUNK_ID_PATTERN.fullmatch(raw) is None:
                    raise ArticleRagVectorDeletionError(
                        _MSG_UNSAFE_CHUNK_ID,
                        retryable=False,
                        failure_code=FAILURE_CODE_UNSAFE_CHUNK_ID,
                    )
                chunk_ids.append(raw)
                if len(chunk_ids) > GC_DISCOVERY_LIMIT:
                    raise ArticleRagVectorDeletionError(
                        _MSG_DISCOVERY_LIMIT_EXCEEDED,
                        retryable=False,
                        failure_code=FAILURE_CODE_DISCOVERY_LIMIT_EXCEEDED,
                    )
        return chunk_ids

    @staticmethod
    def _stable_batches(chunk_ids: list[str]) -> list[list[str]]:
        """Deterministic fixed-size batches over sorted ids."""
        ordered = sorted(chunk_ids)
        return [
            ordered[i:i + GC_DELETE_BATCH_SIZE]
            for i in range(0, len(ordered), GC_DELETE_BATCH_SIZE)
        ]

    @staticmethod
    def _safe_error(
        message: str,
        failure_code: str,
    ) -> ArticleRagVectorDeletionError:
        return ArticleRagVectorDeletionError(
            message,
            retryable=True,
            failure_code=failure_code,
        )


def build_default_article_rag_vector_deleter(
    settings: Settings,
) -> ArticleRagVectorDeleter:
    """Factory for the default Article RAG vector deleter.

    Returns a real :class:`ZillizArticleRagVectorDeleter` only when the
    zilliz provider + URI + token + collection are all configured;
    otherwise the fail-closed :class:`UnconfiguredArticleRagVectorDeleter`
    (retryable, never touches the network).  Never raises, never logs the
    token.
    """
    provider_name = (
        getattr(settings, "reader_article_rag_vector_provider", "") or ""
    ).strip().lower()
    if provider_name != READER_ARTICLE_RAG_VECTOR_PROVIDER_ZILLIZ:
        logger.debug(
            "Article RAG vector provider not configured for deletion "
            "(reader_article_rag_vector_provider=%r); using "
            "UnconfiguredArticleRagVectorDeleter",
            provider_name,
        )
        return UnconfiguredArticleRagVectorDeleter()

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
            "Article RAG vector provider='zilliz' but deletion configuration "
            "is incomplete; using UnconfiguredArticleRagVectorDeleter"
        )
        return UnconfiguredArticleRagVectorDeleter()

    return ZillizArticleRagVectorDeleter(
        uri=uri,
        token=token,
        collection=collection,
    )


__all__ = [
    "READER_ARTICLE_RAG_VECTOR_PROVIDER_ZILLIZ",
    "ArticleRagVectorDeleter",
    "ArticleRagVectorDeletionError",
    "ArticleRagVectorDeletionResult",
    "ZillizArticleRagVectorDeleter",
    "UnconfiguredArticleRagVectorDeleter",
    "build_default_article_rag_vector_deleter",
]
