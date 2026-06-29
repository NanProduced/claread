"""D6-I4F: Article RAG Context Pack Service.

Converts the result of :class:`ArticleRagRetrievalService` into a
deterministic, LLM/Ask-consumable context pack.  No DB writes, no
LLM calls, no real network — the service is a pure read-side
transform that:

  1. calls :meth:`ArticleRagRetrievalService.retrieve_for_record`;
  2. wraps each retrieval hit in an
     :class:`ArticleRagContextItem` carrying a stable, deterministic
     ``context_id`` (e.g. ``"rag-1"``) and a rank (1-based);
  3. applies a character budget
     (``max_context_chars``, default 4000) on the chunk text, with a
     first-chunk-always-included escape hatch (when the first hit is
     itself larger than the budget, it is kept intact and
     ``budget_exceeded=True`` is set);
  4. re-scrubs ``metadata_json`` against a denylist of Plate /
     Markdown / DOM / Slate / UI display group / render / text / html
     / citation_refs keys (the retrieval service already scrubs, but
     a future regression in the retrieval path must not leak through
     here);
  5. records the SHA-256 of the query text for traceability without
     ever including the raw ``query_text`` on the result or in any
     error message.

Truth boundary
--------------

Citation truth comes from the retrieval service's plan-backed
citation (Postgres is the truth).  This module MUST NOT read the
vector payload's ``citation`` or ``text`` — the retrieval service
already joins hits against the current plan on ``chunk_id`` and
exposes the plan chunk's text + citation.  We surface those fields
verbatim.

Security contract
-----------------

* ``query_text`` is **never** included on the returned pack and
  **never** included in any error message; we record
  ``query_sha256 = sha256(query_text.encode("utf-8")).hexdigest()``
  for traceability.
* Retrieval / embedding / searcher error messages are wrapped as a
  typed :class:`ArticleRagContextServiceError` whose diagnostic names
  the failure class + code only — never the original SDK message.
  The underlying exception is preserved as ``__cause__``.
* ``provider_metadata`` is whitelist-scrubbed (not denylist) before
  being placed on the pack.  Only a tiny set of explicitly-safe
  scalar keys (``provider``, ``collection``, ``hit_count``, ``limit``,
  ``latency_ms``, ``total_latency_ms``, ``embedding_model``,
  ``index_version``, ``plan_content_sha256``, ``region``,
  ``namespace``) with non-suspicious scalar values passes through;
  anything else — unknown keys, list values, dict values, scalar
  values containing forbidden substrings (e.g. ``token=``,
  ``query=``), or values exceeding the length cap — is dropped.
* The pack is deterministic: identical inputs always produce an
  identical pack (including the same ``omitted_hit_count`` and
  ``budget_exceeded``).
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

from .article_rag_index_worker import ArticleRagIndexWorkerError
from .article_rag_retrieval_service import (
    DEFAULT_INDEX_VERSION,
    MAX_RETRIEVAL_LIMIT,
    ArticleRagRetrievalResult,
    ArticleRagRetrievalService,
    ArticleRagRetrievalServiceError,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Default for the context pack budget.  Matches the Ask layer's
# conservative initial context budget; deployments can override per
# request.
DEFAULT_MAX_CONTEXT_CHARS = 4000

# Default ``limit`` for the underlying retrieval call.  Mirrors the
# task description; deployments can override.
DEFAULT_LIMIT = 8

# Failure codes — stable, machine-readable.  We deliberately reuse the
# retrieval service's failure-code taxonomy for codes that the
# retrieval service already produces (e.g. empty query / invalid
# limit) so dashboards dispatch on a single label.
FAILURE_CODE_CONTEXT_EMPTY_QUERY = "context_empty_query"
FAILURE_CODE_CONTEXT_INVALID_LIMIT = "context_invalid_limit"
FAILURE_CODE_CONTEXT_INVALID_BUDGET = "context_invalid_budget"
FAILURE_CODE_CONTEXT_RETRIEVAL_FAILED = "context_retrieval_failed"

# Denylist for returned metadata.  Mirrors the I4E retrieval
# service's denylist (defence in depth — a future regression in the
# retrieval path must not leak through here).  Any presence on a
# context item is silently dropped; the denylist membership itself
# never surfaces to the caller.
_FORBIDDEN_CONTEXT_METADATA_KEYS = frozenset({
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


class ArticleRagContextServiceError(ArticleRagIndexWorkerError):
    """Typed failure for the context-pack service.

    Inherits :class:`ArticleRagIndexWorkerError` so any future
    orchestrator that catches the worker base class also catches
    context-pack failures.  ``failure_class`` defaults to ``"context"``
    so dashboards can route context-pack failures separately from
    write-side / retrieval-side failures.

    The error message is a fixed diagnostic that explicitly excludes
    the query text, the query vector, the embedding API key, the
    Zilliz token / URI, and the upstream SDK message.  The underlying
    exception (if any) is preserved as ``__cause__``.
    """

    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        failure_code: str,
        failure_class: str = "context",
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
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ArticleRagContextItem:
    """One context item in the pack.

    ``context_id`` is a deterministic, stable identifier derived from
    the rank (e.g. ``"rag-1"``, ``"rag-2"``).  The LLM can use it to
    cite the source back to the user.

    ``citation`` comes verbatim from the retrieval service — the
    retrieval service joins hits against the current plan on
    ``chunk_id``, so ``citation`` is plan-backed (Postgres truth).
    The vector payload's citation is NEVER read.

    ``metadata_json`` is re-scrubbed against the denylist before
    being placed on the item.
    """

    context_id: str
    rank: int
    chunk_id: str
    score: float
    text: str
    citation: dict[str, Any]
    metadata_json: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ArticleRagContextPack:
    """A deterministic context pack for Ask / LLM consumption.

    ``query_text`` is **never** included on the pack; only
    ``query_sha256`` for traceability.

    ``items`` are in score-descending order (the retrieval service's
    order is preserved).

    ``total_text_chars`` is the sum of ``len(item.text)`` across
    ``items``.

    ``omitted_hit_count`` is the number of retrieval hits that did
    NOT make it into ``items`` because the character budget was
    exhausted.

    ``budget_exceeded`` is ``True`` when the FIRST retrieval hit was
    itself larger than the budget AND was still included (per the
    first-chunk-always-included rule); the LLM consumer can decide
    whether to truncate further or to skip the item entirely.

    ``provider_metadata`` is searcher-side diagnostics from the
    retrieval call; it MUST NOT be surfaced to end users as a fact
    source.
    """

    reading_record_id: UUID
    stable_document_id: UUID
    base_id: UUID
    record_generation: int
    index_version: str
    plan_content_sha256: str
    query_sha256: str
    items: tuple[ArticleRagContextItem, ...]
    total_text_chars: int
    omitted_hit_count: int
    budget_exceeded: bool
    max_context_chars: int
    provider_metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Retrieval service protocol (for test injection)
# ---------------------------------------------------------------------------


class _RetrievalServiceLike(Protocol):
    """Minimal shape :class:`ArticleRagContextService` depends on.

    Exists so tests can inject a ``FakeRetrievalService`` without
    depending on asyncpg / plan / searcher / embedding details.
    """

    async def retrieve_for_record(
        self,
        *,
        reading_record_id: UUID,
        user_id: UUID,
        query_text: str,
        limit: int = ...,
        index_version: str = ...,
    ) -> ArticleRagRetrievalResult: ...


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


def _query_sha256(query_text: str) -> str:
    """SHA-256 of the query text, hex-encoded.

    Used for traceability on the context pack.  The raw query text is
    never included on the pack or in any error message.
    """
    return hashlib.sha256(query_text.encode("utf-8")).hexdigest()


def _scrub_metadata(
    metadata: dict[str, Any],
    *,
    item_chunk_id: str,
    item_context_id: str,
) -> dict[str, Any]:
    """Strip forbidden keys from returned metadata.

    Defence in depth: the retrieval service already scrubs; we
    re-scrub here so a future regression in the retrieval path
    cannot leak Plate / Markdown / DOM / Slate / UI display group /
    text / html fields into the Ask/LLM-facing context pack.

    Additionally, we tag each item with ``chunk_id`` and
    ``context_id`` so downstream consumers can cross-reference
    without depending on position.
    """
    sanitised: dict[str, Any] = {}
    for key, value in metadata.items():
        if key in _FORBIDDEN_CONTEXT_METADATA_KEYS:
            continue
        sanitised[str(key)] = value
    # Tag for downstream traceability — these keys are NOT in the
    # denylist; they are explicitly safe to surface.
    sanitised.setdefault("chunk_id", str(item_chunk_id))
    sanitised.setdefault("context_id", str(item_context_id))
    return sanitised


# Whitelist of allowed keys on ``provider_metadata``.  V1 is
# deliberately tiny: only keys whose semantics are clearly safe to
# surface to ops dashboards / ``repr(pack)`` debuggers.  Anything not
# on this list is dropped.
#
# We use a denylist-as-defence-in-depth: even whitelisted values are
# still passed through ``_safe_scalar_value`` which rejects values
# that look like credentials, query text, URLs, or any other
# suspicious substring.  This means a future regression like
# ``{"provider": "zilliz-token=XYZ"}`` (whitelisted key, suspicious
# value) is still caught.
_ALLOWED_PROVIDER_METADATA_KEYS = frozenset({
    "provider",
    "collection",
    "hit_count",
    "limit",
    "latency_ms",
    "total_latency_ms",
    "embedding_model",
    "index_version",
    "plan_content_sha256",
    "region",
    "namespace",
})

# Substrings we refuse to surface even in whitelisted values.  We
# check case-insensitively because a regression like
# ``"Token=ABC"`` or ``"SECRET-..."`` is exactly the class of leak
# we are defending against.
_FORBIDDEN_VALUE_SUBSTRINGS = (
    "token",
    "uri",
    "url=",
    "secret",
    "api_key",
    "apikey",
    "password",
    "passwd",
    "credential",
    "auth=",
    "bearer ",
    "query_text",
    "query=",
    "query_vector",
    "embedding=",
    "sdk_message",
    "error_message",
    "exception",
    "traceback",
    "stacktrace",
    "plate",
    "markdown",
    "dom",
    "slate",
    "render",
    "html=",
    "innerhtml",
    "innertext",
)

# Length cap on whitelisted string values.  Anything longer is
# almost certainly a regression (a "provider" name should not be
# 1000 chars).
_MAX_WHITELISTED_VALUE_LEN = 128


def _safe_scalar_value(value: Any) -> Any:
    """Return ``value`` if it is a SAFE scalar; otherwise ``None``.

    Safe scalar: ``str`` / ``int`` / ``float`` / ``bool`` (NOT
    ``None``).  Strings must additionally:
      * be at most :data:`_MAX_WHITELISTED_VALUE_LEN` characters;
      * not contain any forbidden substring (case-insensitive).

    Lists, dicts, tuples, sets, and ``None`` are all rejected — a
    whitelisted key whose value is a list or dict is treated as
    suspicious.  This catches the reviewer's example:
    ``{"provider": ["token=..."]}`` → ``provider`` is whitelisted,
    but the list value is not safe → dropped.
    """
    if value is None or isinstance(value, (list, dict, tuple, set)):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, str):
        if len(value) > _MAX_WHITELISTED_VALUE_LEN:
            return None
        lowered = value.lower()
        for substr in _FORBIDDEN_VALUE_SUBSTRINGS:
            if substr in lowered:
                return None
        return value
    # Anything else (``bytes``, custom objects, etc.) is treated as
    # unsafe.
    return None


def _scrub_provider_metadata(
    metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    """Whitelist ``provider_metadata`` down to safe scalar fields.

    Reviewer P2 fix (round 2): the previous denylist-based scrub
    only dropped known-bad key names.  Unknown keys with sensitive
    values (e.g. ``{"diagnostic": "SECRET-QUERY..."}`` or
    ``{"provider": ["token=..."]}``) still passed through.  This
    implementation is the opposite policy: a tiny whitelist of
    explicitly-safe keys, plus a value-side predicate that rejects
    anything looking like a credential, URL, or query fragment.

    The whitelist is checked by lowercased key name.  Values that
    are not scalars (lists, dicts, ``None``) are dropped.  Scalar
    values are passed through :func:`_safe_scalar_value` for an
    additional substring check.
    """
    if not metadata:
        return {}
    sanitised: dict[str, Any] = {}
    for key, raw_value in metadata.items():
        key_lower = str(key).lower()
        if key_lower not in _ALLOWED_PROVIDER_METADATA_KEYS:
            # Unknown key — drop.  This is the strict-whitelist
            # policy: explicit allowlist only.
            continue
        safe_value = _safe_scalar_value(raw_value)
        if safe_value is None:
            # Value failed the scalar / substring check — drop the
            # entire entry.  Examples:
            #   ``{"provider": ["token=..."]}`` → list → drop
            #   ``{"provider": "zilliz SECRET-QUERY"}`` → substring
            #     match → drop
            continue
        sanitised[key_lower] = safe_value
    return sanitised


class ArticleRagContextService:
    """Read-only service that turns a retrieval result into a
    deterministic context pack.

    The service wraps :class:`ArticleRagRetrievalService` and applies
    a deterministic transform:

      * empty hits → empty pack (``items=()``);
      * hits preserved in score-descending order;
      * ``context_id`` assigned as ``f"rag-{rank}"`` (1-based);
      * metadata re-scrubbed against the denylist;
      * character budget applied with first-chunk-always-included
        rule;
      * ``query_text`` never appears on the result or in any error.

    The service does NOT write to the database, does NOT call any
    LLM, and does NOT make any network calls beyond what the
    underlying retrieval service already makes (which the caller
    controls via the injected ``retrieval_service``).
    """

    def __init__(
        self,
        *,
        retrieval_service: _RetrievalServiceLike | None = None,
    ) -> None:
        # No default factory — we want callers to be explicit about
        # which retrieval service to use.  Tests inject a fake;
        # production code injects the real service.
        self._retrieval_service = retrieval_service

    async def build_context_pack_for_record(
        self,
        *,
        reading_record_id: UUID,
        user_id: UUID,
        query_text: str,
        limit: int = DEFAULT_LIMIT,
        max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
        index_version: str = DEFAULT_INDEX_VERSION,
    ) -> ArticleRagContextPack:
        """Build a deterministic context pack for ``query_text``.

        Parameters
        ----------
        reading_record_id
            The reading record to search against.
        user_id
            The requesting user (ownership check, delegated to the
            retrieval service).
        query_text
            The query text.  Empty / whitespace-only strings fail
            closed with ``failure_code=context_empty_query``; the raw
            text is never logged or echoed in any error message.
        limit
            Maximum number of retrieval hits to consider.  Must be in
            ``[1, MAX_RETRIEVAL_LIMIT]``; the upper bound is enforced
            by the retrieval service.  Below 1 / above the upper
            bound fails closed with ``failure_code=context_invalid_limit``.
        max_context_chars
            Total character budget for the chunk text in ``items``.
            Must be a positive integer; 0 or negative fails closed
            with ``failure_code=context_invalid_budget``.
        index_version
            The Article RAG index version to target.

        Raises
        ------
        ArticleRagContextServiceError
            On any fail-closed condition.  ``failure_code`` identifies
            the cause; ``__cause__`` preserves the upstream
            exception.
        """
        # 1. Validate input — fail closed BEFORE invoking the
        #    retrieval service so a blank query never crosses the
        #    embedding provider.
        if not (query_text or "").strip():
            raise ArticleRagContextServiceError(
                "build_context_pack_for_record called with an empty "
                "query_text; refusing to call the retrieval service",
                retryable=False,
                failure_code=FAILURE_CODE_CONTEXT_EMPTY_QUERY,
            )
        if limit <= 0 or limit > MAX_RETRIEVAL_LIMIT:
            raise ArticleRagContextServiceError(
                f"build_context_pack_for_record called with limit="
                f"{limit}; must be in [1, {MAX_RETRIEVAL_LIMIT}]",
                retryable=False,
                failure_code=FAILURE_CODE_CONTEXT_INVALID_LIMIT,
            )
        if max_context_chars <= 0:
            raise ArticleRagContextServiceError(
                f"build_context_pack_for_record called with "
                f"max_context_chars={max_context_chars}; must be a "
                "positive integer",
                retryable=False,
                failure_code=FAILURE_CODE_CONTEXT_INVALID_BUDGET,
            )

        # 2. Compute query_sha256 BEFORE the retrieval call so we can
        #    include it on the pack even if the retrieval call fails
        #    partially.  The hash is deterministic and contains no
        #    raw query content.
        query_hash = _query_sha256(query_text)

        # 3. Delegate to the retrieval service.  We do NOT catch
        #    ``ArticleRagRetrievalServiceError`` separately: the
        #    retrieval service's error taxonomy is a strict superset
        #    of the context service's.  We wrap the entire retrieval
        #    call in a generic try/except so any non-typed failure
        #    (e.g. ``AttributeError`` when the retrieval service is
        #    ``None``) is surfaced as a typed failure rather than a
        #    bare traceback.
        if self._retrieval_service is None:
            raise ArticleRagContextServiceError(
                "ArticleRagContextService has no retrieval_service "
                "configured; inject an explicit fake for tests or a "
                "real ArticleRagRetrievalService for production",
                retryable=False,
                failure_code=FAILURE_CODE_CONTEXT_RETRIEVAL_FAILED,
            )

        try:
            retrieval_result = (
                await self._retrieval_service.retrieve_for_record(
                    reading_record_id=reading_record_id,
                    user_id=user_id,
                    query_text=query_text,
                    limit=limit,
                    index_version=index_version,
                )
            )
        except ArticleRagRetrievalServiceError as exc:
            raise ArticleRagContextServiceError(
                f"retrieval service raised {type(exc).__name__} "
                f"(failure_code={exc.failure_code}); see __cause__ for "
                "upstream diagnostic",
                retryable=exc.retryable,
                failure_code=FAILURE_CODE_CONTEXT_RETRIEVAL_FAILED,
            ) from exc
        except Exception as exc:  # noqa: BLE001 — defensive catch-all
            raise ArticleRagContextServiceError(
                "retrieval service raised "
                f"{type(exc).__name__}; see __cause__ for upstream "
                "diagnostic",
                retryable=False,
                failure_code=FAILURE_CODE_CONTEXT_RETRIEVAL_FAILED,
            ) from exc

        # 4. Empty hits is NOT an error — return an empty pack.
        if not retrieval_result.hits:
            logger.debug(
                "Article RAG context pack served 0 items for record=%s "
                "limit=%d index_version=%s",
                reading_record_id,
                limit,
                index_version,
            )
            return ArticleRagContextPack(
                reading_record_id=retrieval_result.reading_record_id,
                stable_document_id=retrieval_result.stable_document_id,
                base_id=retrieval_result.base_id,
                record_generation=retrieval_result.record_generation,
                index_version=retrieval_result.index_version,
                plan_content_sha256=retrieval_result.plan_content_sha256,
                query_sha256=query_hash,
                items=(),
                total_text_chars=0,
                omitted_hit_count=0,
                budget_exceeded=False,
                max_context_chars=max_context_chars,
                provider_metadata=_scrub_provider_metadata(
                    retrieval_result.provider_metadata
                ),
            )

        # 5. Apply the character budget with the
        #    first-chunk-always-included rule.  We do NOT split
        #    chunks — each item's text is the plan chunk's text
        #    verbatim.  We do NOT truncate per-item either (would
        #    corrupt citation offsets).
        items: list[ArticleRagContextItem] = []
        total_chars = 0
        omitted = 0
        budget_exceeded = False
        for rank, hit in enumerate(retrieval_result.hits, start=1):
            context_id = f"rag-{rank}"
            sanitised_md = _scrub_metadata(
                hit.metadata_json,
                item_chunk_id=hit.chunk_id,
                item_context_id=context_id,
            )
            item = ArticleRagContextItem(
                context_id=context_id,
                rank=rank,
                chunk_id=hit.chunk_id,
                score=float(hit.score),
                text=hit.text,
                citation=dict(hit.citation),
                metadata_json=sanitised_md,
            )
            chunk_len = len(item.text)
            if not items:
                # First item — always included.  If it exceeds the
                # budget, flag ``budget_exceeded=True`` so the LLM
                # consumer can decide how to handle it.
                items.append(item)
                total_chars += chunk_len
                if chunk_len > max_context_chars:
                    budget_exceeded = True
                continue
            # Subsequent items — only included if they fit.
            # Contract: once a hit does NOT fit, STOP the loop.  We do
            # NOT skip the unfit hit and try to add the next one,
            # because that would break the "score-descending contiguous
            # prefix" invariant and produce a pack where rank/context_id
            # numbers no longer correspond to the retrieval order.  All
            # remaining hits (including the unfit one) are counted in
            # ``omitted_hit_count`` and the loop breaks.
            if total_chars + chunk_len > max_context_chars:
                remaining = len(retrieval_result.hits) - (rank - 1)
                omitted += remaining
                break
            items.append(item)
            total_chars += chunk_len

        logger.debug(
            "Article RAG context pack served %d items "
            "(omitted=%d, budget_exceeded=%s, total_chars=%d) for "
            "record=%s limit=%d index_version=%s",
            len(items),
            omitted,
            budget_exceeded,
            total_chars,
            reading_record_id,
            limit,
            index_version,
        )

        return ArticleRagContextPack(
            reading_record_id=retrieval_result.reading_record_id,
            stable_document_id=retrieval_result.stable_document_id,
            base_id=retrieval_result.base_id,
            record_generation=retrieval_result.record_generation,
            index_version=retrieval_result.index_version,
            plan_content_sha256=retrieval_result.plan_content_sha256,
            query_sha256=query_hash,
            items=tuple(items),
            total_text_chars=total_chars,
            omitted_hit_count=omitted,
            budget_exceeded=budget_exceeded,
            max_context_chars=max_context_chars,
            provider_metadata=_scrub_provider_metadata(
                retrieval_result.provider_metadata
            ),
        )


__all__ = [
    "DEFAULT_MAX_CONTEXT_CHARS",
    "DEFAULT_LIMIT",
    "FAILURE_CODE_CONTEXT_EMPTY_QUERY",
    "FAILURE_CODE_CONTEXT_INVALID_LIMIT",
    "FAILURE_CODE_CONTEXT_INVALID_BUDGET",
    "FAILURE_CODE_CONTEXT_RETRIEVAL_FAILED",
    "ArticleRagContextServiceError",
    "ArticleRagContextItem",
    "ArticleRagContextPack",
    "ArticleRagContextService",
]