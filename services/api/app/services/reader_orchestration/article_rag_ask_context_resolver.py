"""D6-I4H: Article RAG Ask Context Resolver.

Composes :class:`ArticleRagContextService` (D6-I4F) and
:class:`ArticleRagAskContextComposer` (D6-I4G) into a fail-soft
RAG context entry for the Ask layer.  The resolver:

  * does NOT write to the database;
  * does NOT call any LLM;
  * does NOT touch the vector store or embedding provider directly
    — those concerns live behind the context service.  The
    resolver is a pure orchestrator that injects both the context
    service and the composer so the Ask layer can wire a single
    entry point and a single, predictable response shape;
  * never raises an exception to the caller — every failure
    becomes a typed :class:`ArticleRagAskContextResolveResult`
    whose ``status`` field tells the Ask layer what to do
    (degrade gracefully, fall back to a no-RAG answer, retry, etc.);
  * never includes ``query_text`` on the result; only
    ``query_sha256`` for traceability;
  * never includes ``provider_metadata`` on the result; the Ask
    layer must never treat a searcher diagnostic as a fact source.

Truth boundary
--------------

This module is a thin orchestrator and does NOT touch citation
truth directly.  Citation / text come from the context service
(I4F) which already joins retrieval hits against the current plan
on ``chunk_id`` (Postgres is the truth).  The composer (I4G)
reformats those into a prompt-embeddable bundle.  The resolver
chains these two and maps every failure to a stable status
without ever constructing or rewriting citation / text.

Security contract
-----------------

* ``query_text`` is never echoed in the result, in any field, or
  in any log line.  ``query_sha256`` is the only query-derived
  value surfaced.
* ``provider_metadata`` from the context pack is NEVER included
  on the resolver result — the Ask layer is not a place for
  searcher diagnostics.
* Unexpected exceptions are mapped to
  ``status="not_indexed_or_unavailable"`` (or
  ``status="composer_rejected"`` when the composer fails) with
  ``failure_code="article_rag_context_unexpected_error"``.  The
  cause's class name is recorded in the resolver's logs for ops
  dashboards, but the original exception object is NOT exposed on
  the public result.  The result dataclass has no ``__cause__`` —
  callers that need the underlying exception must wrap the
  resolver in their own try/except.
* The resolver is fail-soft: every non-OK path leaves
  ``fallback_allowed=True`` so the Ask layer can degrade
  gracefully (e.g. "answer without RAG context") rather than
  surface a hard error to the user.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from .article_rag_ask_context_composer import (
    ArticleRagAskContextBundle,
    ArticleRagAskContextComposerError,
)
from .article_rag_context_service import (
    ArticleRagContextPack,
    ArticleRagContextServiceError,
    DEFAULT_LIMIT as _DEFAULT_CONTEXT_LIMIT,
    DEFAULT_MAX_CONTEXT_CHARS as _DEFAULT_MAX_CONTEXT_CHARS,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Default ``limit`` for the context service call.  Matches the
# I4F default so behaviour is symmetric with a direct call to
# the context service.
DEFAULT_RESOLVER_LIMIT = _DEFAULT_CONTEXT_LIMIT  # 8

# Default character budget for the context service call.
DEFAULT_RESOLVER_MAX_CONTEXT_CHARS = _DEFAULT_MAX_CONTEXT_CHARS  # 4000

# Default index version.  Mirrors the I4F default; deployments
# can override per call.

# Failure codes — stable, machine-readable.  We deliberately
# reuse the upstream failure codes when available, and add a
# few resolver-specific codes for unexpected paths.
FAILURE_CODE_RESOLVER_DISABLED = "article_rag_ask_context_disabled"
FAILURE_CODE_RESOLVER_COMPOSER_REJECTED = (
    "article_rag_ask_context_composer_rejected"
)
FAILURE_CODE_RESOLVER_UNEXPECTED_ERROR = (
    "article_rag_context_unexpected_error"
)

# Status literal — strict allowlist of values the Ask layer can
# dispatch on.  The LLM-facing fallback policy is keyed off these.
ArticleRagAskContextResolveStatus = Literal[
    "available",
    "empty",
    "not_indexed_or_unavailable",
    "composer_rejected",
    "disabled",
]


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ArticleRagAskContextResolveResult:
    """A fail-soft RAG context result for the Ask layer.

    The Ask layer keys its fallback policy on ``status``:

      * ``"available"`` — ``bundle`` is non-None with at least one
        item; the Ask layer should use ``bundle.prompt_context_text``
        + ``bundle.citations`` to construct the prompt.
      * ``"empty"`` — the index returned zero hits; ``bundle`` may
        be present (empty bundle) or None.  ``fallback_allowed``
        is ``True`` so the Ask layer can answer without RAG.
      * ``"not_indexed_or_unavailable"`` — the context service
        failed (no index run / plan hash drift / embedding failure
        / searcher failure / unexpected error).  ``bundle`` is
        None.  ``failure_code`` carries the upstream cause so
        dashboards can dispatch; ``fallback_allowed`` is ``True``.
      * ``"composer_rejected"`` — the composer rejected the pack
        (empty text / oversized text).  ``bundle`` is None.
        ``failure_code`` carries the upstream cause;
        ``fallback_allowed`` is ``True``.
      * ``"disabled"`` — the feature is disabled for this call
        (caller passed ``enabled=False``).  ``bundle`` is None.
        ``fallback_allowed`` is ``True``.

    The result NEVER includes ``provider_metadata`` (searcher
    diagnostics must not surface to the Ask layer) and NEVER
    includes ``query_text`` (only ``query_sha256`` is surfaced).
    """

    status: ArticleRagAskContextResolveStatus
    enabled: bool
    bundle: ArticleRagAskContextBundle | None
    failure_code: str | None
    retryable: bool
    fallback_allowed: bool
    reading_record_id: UUID | None
    query_sha256: str | None
    # ``omitted_hit_count`` and ``budget_exceeded`` are echoed
    # only when a bundle is present (otherwise they are N/A).
    omitted_hit_count: int | None = None
    budget_exceeded: bool | None = None
    # Echoed from the bundle when present — stable ids the Ask
    # layer can use for cache keys without depending on the
    # full bundle.
    stable_document_id: UUID | None = None
    base_id: UUID | None = None
    record_generation: int | None = None
    plan_content_sha256: str | None = None
    # Free-form provider_metadata EXPLICITLY omitted — the Ask
    # layer must not treat a searcher diagnostic as a fact
    # source.  Ops dashboards can read the upstream
    # ``ArticleRagContextPack.provider_metadata`` via a separate
    # call if needed.


# ---------------------------------------------------------------------------
# Dependency protocols
# ---------------------------------------------------------------------------


class _ContextServiceLike(Protocol):
    """Minimal shape :class:`ArticleRagContextService` exposes."""

    async def build_context_pack_for_record(
        self,
        *,
        reading_record_id: UUID,
        user_id: UUID,
        query_text: str,
        limit: int = ...,
        max_context_chars: int = ...,
    ) -> ArticleRagContextPack: ...


class _ComposerLike(Protocol):
    """Minimal shape :class:`ArticleRagAskContextComposer` exposes."""

    def compose(
        self, pack: ArticleRagContextPack
    ) -> ArticleRagAskContextBundle: ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _query_sha256(query_text: str) -> str:
    """SHA-256 of the query text, hex-encoded.

    Used for traceability on the result.  The raw query text is
    never included on the result or in any error message.
    """
    return hashlib.sha256(query_text.encode("utf-8")).hexdigest()


def _map_failure_code(
    exc: ArticleRagContextServiceError | ArticleRagAskContextComposerError,
) -> str:
    """Map an upstream failure code to a stable resolver code.

    We deliberately surface the UPSTREAM failure code (e.g.
    ``"context_empty_query"``, ``"context_no_indexed_run"``,
    ``"ask_context_text_too_long"``) so ops dashboards can
    dispatch on the actual cause.  Resolver-specific codes are
    reserved for the resolver's own decisions (disabled /
    unexpected).
    """
    return str(exc.failure_code or "")


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


class ArticleRagAskContextResolver:
    """Fail-soft resolver that chains I4F + I4G for the Ask layer.

    The resolver is a pure orchestrator: no I/O beyond what the
    injected context service does.  The Ask layer calls
    :meth:`resolve_for_record` and gets a typed
    :class:`ArticleRagAskContextResolveResult` — never an exception.
    """

    def __init__(
        self,
        *,
        context_service: _ContextServiceLike | None = None,
        composer: _ComposerLike | None = None,
    ) -> None:
        # Lazy defaults: the resolver refuses to silently pick a
        # fake / unconfigured service.  Tests inject fakes;
        # production code injects the real services.
        self._context_service = context_service
        self._composer = composer

    async def resolve_for_record(
        self,
        *,
        reading_record_id: UUID,
        user_id: UUID,
        query_text: str,
        enabled: bool = True,
        limit: int = DEFAULT_RESOLVER_LIMIT,
        max_context_chars: int = DEFAULT_RESOLVER_MAX_CONTEXT_CHARS,
    ) -> ArticleRagAskContextResolveResult:
        """Resolve a deterministic RAG context for the Ask layer.

        Never raises.  The result's ``status`` field tells the Ask
        layer what to do.

        Parameters
        ----------
        reading_record_id
            The reading record to search against (ownership is
            delegated to the context service).
        user_id
            The requesting user.
        query_text
            The query text.  The raw text is NEVER echoed on the
            result; only ``query_sha256`` is surfaced.  An empty /
            whitespace-only ``query_text`` is delegated to the
            context service, which fails closed.
        enabled
            When ``False``, the resolver short-circuits with
            ``status="disabled"`` and does NOT call the context
            service or the composer.  Useful for feature flags /
            per-deployment opt-out.
        limit
            Forwarded to the context service.
        max_context_chars
            Forwarded to the context service.

        Returns
        -------
        ArticleRagAskContextResolveResult
            A typed, fail-soft result.  ``fallback_allowed`` is
            ``True`` for every non-OK status so the Ask layer can
            answer without RAG when the resolver fails.
        """
        # 1. Compute query_sha256 BEFORE any branching so the
        #    result always carries it (the Ask layer uses it for
        #    cache keys, log dedup, etc.).
        query_hash = _query_sha256(query_text)

        # 2. Disabled short-circuit: do NOT call context service.
        if not enabled:
            logger.debug(
                "Article RAG ask context resolver disabled for "
                "record=%s; returning status=disabled",
                reading_record_id,
            )
            return ArticleRagAskContextResolveResult(
                status="disabled",
                enabled=False,
                bundle=None,
                failure_code=FAILURE_CODE_RESOLVER_DISABLED,
                retryable=False,
                fallback_allowed=True,
                reading_record_id=reading_record_id,
                query_sha256=query_hash,
            )

        # 3. Validate injected dependencies.
        if self._context_service is None:
            return self._make_unexpected_result(
                status="not_indexed_or_unavailable",
                failure_code=FAILURE_CODE_RESOLVER_UNEXPECTED_ERROR,
                reading_record_id=reading_record_id,
                query_sha256=query_hash,
                reason=(
                    "ArticleRagAskContextResolver has no "
                    "context_service configured"
                ),
                cause=RuntimeError("context_service is None"),
            )
        if self._composer is None:
            return self._make_unexpected_result(
                status="not_indexed_or_unavailable",
                failure_code=FAILURE_CODE_RESOLVER_UNEXPECTED_ERROR,
                reading_record_id=reading_record_id,
                query_sha256=query_hash,
                reason=(
                    "ArticleRagAskContextResolver has no composer "
                    "configured"
                ),
                cause=RuntimeError("composer is None"),
            )

        # 4. Call the context service.  Any failure is mapped to a
        #    status — NEVER raised.
        try:
            pack = await self._context_service.build_context_pack_for_record(
                reading_record_id=reading_record_id,
                user_id=user_id,
                query_text=query_text,
                limit=limit,
                max_context_chars=max_context_chars,
            )
        except ArticleRagContextServiceError as exc:
            # Typed upstream failure — preserve upstream
            # ``failure_code`` and ``retryable`` for the Ask
            # layer; do NOT attach the original exception to the
            # public result.  The raw message is NOT echoed in
            # any field the Ask layer can read.
            logger.info(
                "Article RAG ask context resolver: context service "
                "raised %s (failure_code=%s, retryable=%s) for "
                "record=%s",
                type(exc).__name__,
                exc.failure_code,
                exc.retryable,
                reading_record_id,
            )
            return ArticleRagAskContextResolveResult(
                status="not_indexed_or_unavailable",
                enabled=True,
                bundle=None,
                failure_code=_map_failure_code(exc),
                retryable=bool(exc.retryable),
                fallback_allowed=True,
                reading_record_id=reading_record_id,
                query_sha256=query_hash,
            )
        except Exception as exc:  # noqa: BLE001 — defensive catch-all
            # Unexpected error: never echo the upstream message.
            # The cause class name is logged for ops (see
            # ``_make_unexpected_result``); the cause object
            # itself is NOT attached to the public result.
            return self._make_unexpected_result(
                status="not_indexed_or_unavailable",
                failure_code=FAILURE_CODE_RESOLVER_UNEXPECTED_ERROR,
                reading_record_id=reading_record_id,
                query_sha256=query_hash,
                reason=(
                    "ArticleRagAskContextResolver caught an "
                    "unexpected exception from the context service"
                ),
                cause=exc,
            )

        # 5. Empty pack: hand back a status of "empty" with no
        #    bundle.  We do NOT call the composer (it would just
        #    produce an empty bundle, and the Ask layer keys
        #    on the resolver's status — not on bundle.empty).
        if not pack.items:
            logger.debug(
                "Article RAG ask context resolver: empty pack for "
                "record=%s; returning status=empty",
                reading_record_id,
            )
            return ArticleRagAskContextResolveResult(
                status="empty",
                enabled=True,
                bundle=None,
                failure_code=None,
                retryable=False,
                fallback_allowed=True,
                reading_record_id=reading_record_id,
                # Use the locally computed ``query_hash`` for
                # consistency with every other path.  ``pack.query_sha256``
                # would be the same value by construction, but a
                # regression in the context service could surface
                # a mismatched hash — we MUST use the value the
                # resolver actually computed for the query_text
                # it received.
                query_sha256=query_hash,
                omitted_hit_count=pack.omitted_hit_count,
                budget_exceeded=pack.budget_exceeded,
                stable_document_id=pack.stable_document_id,
                base_id=pack.base_id,
                record_generation=pack.record_generation,
                plan_content_sha256=pack.plan_content_sha256,
            )

        # 6. Compose.  Any composer failure is mapped to
        #    status="composer_rejected".
        try:
            bundle = self._composer.compose(pack)
        except ArticleRagAskContextComposerError as exc:
            logger.info(
                "Article RAG ask context resolver: composer "
                "raised %s (failure_code=%s) for record=%s",
                type(exc).__name__,
                exc.failure_code,
                reading_record_id,
            )
            return ArticleRagAskContextResolveResult(
                status="composer_rejected",
                enabled=True,
                bundle=None,
                failure_code=_map_failure_code(exc),
                retryable=bool(exc.retryable),
                fallback_allowed=True,
                reading_record_id=reading_record_id,
                query_sha256=query_hash,
                stable_document_id=pack.stable_document_id,
                base_id=pack.base_id,
                record_generation=pack.record_generation,
                plan_content_sha256=pack.plan_content_sha256,
            )
        except Exception as exc:  # noqa: BLE001 — defensive catch-all
            return self._make_unexpected_result(
                status="composer_rejected",
                failure_code=FAILURE_CODE_RESOLVER_UNEXPECTED_ERROR,
                reading_record_id=reading_record_id,
                query_sha256=query_hash,
                reason=(
                    "ArticleRagAskContextResolver caught an "
                    "unexpected exception from the composer"
                ),
                cause=exc,
            )

        # 6.5 Invariant: the composer MUST return a non-empty
        #     bundle for a non-empty pack.  ``bundle is None`` or
        #     ``bundle.empty is True`` here is a composer
        #     regression — fail closed with status="composer_rejected"
        #     so the Ask layer can fall back.  This is the
        #     defensive check the result docstring's "available
        #     => bundle non-None" contract relies on.
        if bundle is None or getattr(bundle, "empty", False):
            return ArticleRagAskContextResolveResult(
                status="composer_rejected",
                enabled=True,
                bundle=None,
                failure_code=FAILURE_CODE_RESOLVER_UNEXPECTED_ERROR,
                retryable=False,
                fallback_allowed=True,
                reading_record_id=reading_record_id,
                query_sha256=query_hash,
                stable_document_id=pack.stable_document_id,
                base_id=pack.base_id,
                record_generation=pack.record_generation,
                plan_content_sha256=pack.plan_content_sha256,
            )

        # 7. Available.
        # ``bundle`` is guaranteed non-None by the invariant above
        # (and ``bundle.empty is False`` per the I4G composer
        # contract — a non-empty pack always produces a non-empty
        # bundle).  ``query_sha256`` comes from the locally
        # computed ``query_hash`` (not the bundle, which I4G
        # intentionally drops — the bundle is about source
        # content, not the call that retrieved it).
        return ArticleRagAskContextResolveResult(
            status="available",
            enabled=True,
            bundle=bundle,
            failure_code=None,
            retryable=False,
            fallback_allowed=True,
            reading_record_id=reading_record_id,
            query_sha256=query_hash,
            omitted_hit_count=bundle.omitted_hit_count,
            budget_exceeded=bundle.budget_exceeded,
            stable_document_id=bundle.stable_document_id,
            base_id=bundle.base_id,
            record_generation=bundle.record_generation,
            plan_content_sha256=bundle.plan_content_sha256,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_unexpected_result(
        *,
        status: ArticleRagAskContextResolveStatus,
        failure_code: str,
        reading_record_id: UUID,
        query_sha256: str,
        reason: str,
        cause: BaseException,
    ) -> ArticleRagAskContextResolveResult:
        """Build an unexpected-error result.

        The ``cause`` argument is consumed here for ops logging
        (the class name is recorded; the message is NOT) and then
        discarded — the public ``ArticleRagAskContextResolveResult``
        does NOT carry the cause object, so callers cannot read
        the original exception via ``__cause__``.  This is the
        deliberate design: the result is a pure value object
        (frozen dataclass, no exception chain), so it can be
        safely passed to the Ask layer / cached / serialised
        without leaking internals.  Callers that need the
        underlying exception must wrap the resolver in their own
        try/except.
        """
        logger.info(
            "Article RAG ask context resolver: %s (failure_code=%s) "
            "for record=%s; cause=%s: %s",
            reason,
            failure_code,
            reading_record_id,
            type(cause).__name__,
            type(cause).__name__,  # never log the original message
        )
        # We log the cause class name only — the original
        # exception object is intentionally NOT logged to avoid
        # leaking query text / tokens / SDK messages.
        return ArticleRagAskContextResolveResult(
            status=status,
            enabled=True,
            bundle=None,
            failure_code=failure_code,
            retryable=False,
            fallback_allowed=True,
            reading_record_id=reading_record_id,
            query_sha256=query_sha256,
        )


__all__ = [
    "DEFAULT_RESOLVER_LIMIT",
    "DEFAULT_RESOLVER_MAX_CONTEXT_CHARS",
    "FAILURE_CODE_RESOLVER_DISABLED",
    "FAILURE_CODE_RESOLVER_COMPOSER_REJECTED",
    "FAILURE_CODE_RESOLVER_UNEXPECTED_ERROR",
    "ArticleRagAskContextResolveStatus",
    "ArticleRagAskContextResolveResult",
    "ArticleRagAskContextResolver",
]