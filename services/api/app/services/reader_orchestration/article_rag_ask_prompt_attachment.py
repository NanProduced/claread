"""D6-I4I: Article RAG Ask Prompt Attachment Service.

Converts the result of :class:`ArticleRagAskContextResolver` (D6-I4H)
into a flat, Ask-runtime-consumable
:class:`ArticleRagAskPromptAttachment` value object.  The attachment
service:

  * is a pure orchestrator — no LLM, no DB, no network;
  * never raises — any failure maps to a fail-soft
    ``status="not_indexed_or_unavailable"`` (or the resolver's own
    status when it is a known shape);
  * never includes ``query_text`` — only ``query_sha256``;
  * never includes ``provider_metadata`` — the Ask layer must not
    treat a searcher diagnostic as a fact source;
  * never includes vector payload, Plate / Markdown / DOM / Slate /
    UI display group / render fields — the contract is the same
    as I4F / I4G / I4H;
  * never mutates ``prompt_context_text`` or re-parses it to
    extract citations — citations come from
    ``bundle.citations`` only (which I4G built verbatim from the
    plan-backed retrieval hits, NOT parsed from text);
  * never invents content for ``status="available"`` — if the
    bundle is empty / missing / malformed, the attachment is
    flagged as not-includeable rather than silently degraded.

Truth boundary
--------------

This module is a thin transform.  Citation / text come from the
bundle which the I4G composer built from the I4F context pack
which the I4E retrieval service built by joining hits against the
current plan.  Postgres is the truth; Zilliz is the replica.
The attachment service adds no interpretation layer.

Security contract
-----------------

* ``query_text`` is never echoed in any field the Ask layer can
  read, in any log line, or in any error path.  Only
  ``query_sha256`` is surfaced.
* ``provider_metadata`` from the upstream pack / resolver is
  NEVER carried on the attachment.
* Unexpected exceptions are mapped to a fail-soft status with a
  fixed ``failure_code``; the original exception is NOT attached
  to the attachment (the result is a pure value object that can
  be cached, serialised, and passed across the LLM boundary
  without leaking internals).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from .article_rag_ask_context_composer import (
    ArticleRagAskContextBundle,
    ArticleRagAskContextCitation,
)
from .article_rag_ask_context_resolver import (
    ArticleRagAskContextResolveResult,
    ArticleRagAskContextResolveStatus,
)
from .article_rag_context_service import (
    DEFAULT_LIMIT,
    DEFAULT_MAX_CONTEXT_CHARS,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Default ``limit`` / character budget / index version.  Mirrors
# the resolver defaults; we re-export them here so callers of the
# attachment service have a single import surface.
DEFAULT_ATTACHMENT_LIMIT = DEFAULT_LIMIT  # 8
DEFAULT_ATTACHMENT_MAX_CONTEXT_CHARS = DEFAULT_MAX_CONTEXT_CHARS  # 4000

# Failure codes — stable, machine-readable.  We deliberately
# surface the resolver's failure_code when one is available (so
# dashboards can dispatch on the actual cause) and use a
# resolver-specific code only for the attachment service's own
# unexpected-error path.
FAILURE_CODE_ATTACHMENT_UNEXPECTED_ERROR = (
    "article_rag_prompt_attachment_unexpected_error"
)

# The set of resolver statuses we recognise.  The I4H status is a
# ``typing.Literal`` (compile-time only); a regression in the
# resolver could surface an unrecognised string at runtime
# (e.g. ``"paused"``, ``"failed"``, ``""``).  Anything outside
# this allowlist is treated as a malformed result and fail-softs
# to ``not_indexed_or_unavailable``.
_ALLOWED_RESOLVER_STATUSES = frozenset(
    {
        "available",
        "empty",
        "not_indexed_or_unavailable",
        "composer_rejected",
        "disabled",
    }
)

# I4A citation truth keys (9 keys).  These are the canonical
# citation fields the I4A plan service produces.  Any other key
# in an ``ArticleRagAskContextCitation.citation`` is a regression
# (a hostile fake in a test, or a future bug in the I4E / I4F /
# I4G chain that surfaces provider metadata / query text /
# projection fields).  We strip everything outside this allowlist
# so the attachment can never leak non-citation content.
_ALLOWED_CITATION_KEYS = frozenset(
    {
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
)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ArticleRagAskPromptAttachment:
    """A flat, Ask-runtime-consumable RAG prompt attachment.

    The Ask layer keys its prompt-construction policy on
    ``should_include_context``:

      * ``True`` — the resolver returned a non-empty bundle and
        the attachment carries ``prompt_context_text``,
        ``citations`` (structured), ``context_ids``, and
        ``source_pack_hash``.  The Ask layer should embed these
        into the prompt.
      * ``False`` — no context is available (resolver returned
        ``disabled`` / ``empty`` / ``not_indexed_or_unavailable``
        / ``composer_rejected``, or the resolver returned a
        malformed shape).  ``prompt_context_text`` is the empty
        string and ``citations`` is empty.  ``fallback_allowed``
        is always ``True`` so the Ask layer can answer without
        RAG.

    Stable ids (``reading_record_id`` / ``stable_document_id`` /
    ``base_id`` / ``record_generation`` / ``plan_content_sha256``
    / ``index_version``) are echoed from the resolver result
    whenever the resolver has them — the Ask layer can use them
    for cache keys, log dedup, and prompt-source attribution.

    The attachment NEVER carries ``provider_metadata`` or
    ``query_text`` — only ``query_sha256`` for traceability.
    """

    # Whether the feature is enabled for this call.
    enabled: bool
    # The resolver's status — propagated unchanged so ops
    # dashboards can dispatch on the same value used by I4H.
    status: ArticleRagAskContextResolveStatus
    # Whether the Ask layer should embed the prompt_context_text
    # into the prompt.  Mirrors: ``True`` iff the resolver
    # returned ``status="available"`` AND the bundle is
    # well-formed AND ``prompt_context_text`` is non-empty.
    should_include_context: bool
    # Whether the Ask layer may answer without RAG context.  This
    # is a permissive flag: the Ask layer is free to use RAG
    # context OR fall back to a no-RAG answer on every path.
    fallback_allowed: bool
    # SHA-256 of the query text, for traceability.  NEVER the raw
    # query text.
    query_sha256: str | None
    # The plain-text prompt context (I4G composer output, copied
    # verbatim).  Empty when ``should_include_context`` is False.
    prompt_context_text: str
    # Structured citations (one per item, in score-descending
    # order).  Copied verbatim from ``bundle.citations`` (which
    # I4G built from plan-backed retrieval hits — Postgres truth).
    # Empty when ``should_include_context`` is False.
    citations: tuple[dict[str, Any], ...]
    # Stable context ids embedded in the prompt text.  Mirrors
    # ``bundle.context_ids``.  Empty when ``should_include_context``
    # is False.
    context_ids: tuple[str, ...]
    # Source identity hash (I4G composer output, copied verbatim).
    # The Ask layer can use this as a cache key for the prompt
    # block.  ``None`` when no bundle is available.
    source_pack_hash: str | None
    # Failure code (propagated from the resolver).  The Ask layer
    # surfaces this to ops dashboards; the LLM-facing answer
    # MUST NOT mention the code.
    failure_code: str | None
    # Whether the upstream failure was retryable (propagated from
    # the resolver).
    retryable: bool
    # Diagnostics from the resolver (omitted_hit_count /
    # budget_exceeded).  ``None`` when the resolver has no bundle
    # to echo from.
    omitted_hit_count: int | None
    budget_exceeded: bool | None
    # Stable ids echoed from the resolver.  ``None`` when the
    # resolver has none (e.g. disabled path).
    reading_record_id: UUID | None
    stable_document_id: UUID | None
    base_id: UUID | None
    record_generation: int | None
    plan_content_sha256: str | None


# ---------------------------------------------------------------------------
# Dependency protocol
# ---------------------------------------------------------------------------


class _ResolverLike(Protocol):
    """Minimal shape :class:`ArticleRagAskContextResolver` exposes."""

    async def resolve_for_record(
        self,
        *,
        reading_record_id: UUID,
        user_id: UUID,
        query_text: str,
        enabled: bool = ...,
        limit: int = ...,
        max_context_chars: int = ...,
    ) -> ArticleRagAskContextResolveResult: ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _citations_to_dicts(
    citations: tuple[ArticleRagAskContextCitation, ...],
) -> tuple[dict[str, Any], ...]:
    """Convert I4G citation dataclasses to plain dicts.

    The Ask layer is a transport boundary; we expose dicts (not
    dataclasses) so the attachment can be serialised /
    json.dumps'd / passed across async boundaries without
    requiring the Ask runtime to import the I4G dataclass.  Each
    dict carries the three I4G fields plus the verbatim citation
    dict:

      * ``context_id`` (str)
      * ``chunk_id`` (str)
      * ``citation`` (dict — the plan-backed 9-key I4A shape,
        allowlisted; any other key on the upstream
        ``ArticleRagAskContextCitation.citation`` is stripped)

    The ``citation`` sub-dict is filtered against
    :data:`_ALLOWED_CITATION_KEYS` (the 9 I4A citation truth
    keys) before being placed on the attachment.  This is
    defence in depth against a regression in I4E / I4F / I4G that
    surfaces a hostile field on the citation dict (e.g. a
    provider diagnostic, the query text, or a UI projection
    key) — the attachment MUST NOT echo such a field.
    """
    return tuple(
        {
            "context_id": c.context_id,
            "chunk_id": c.chunk_id,
            "citation": _scrub_citation(c.citation),
        }
        for c in citations
    )


def _scrub_citation(citation: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``citation`` with only the 9 I4A
    citation truth keys.

    The 9 I4A keys are the canonical citation fields the I4A plan
    service produces.  Any other key on the upstream citation
    dict is a regression: a hostile fake in a test, or a future
    bug in the I4E / I4F / I4G chain that surfaces a
    searcher / embedding / projection / query field on the
    citation.  The attachment MUST NOT carry such a field —
    dropping it here is the last line of defence.
    """
    if not isinstance(citation, dict):
        return {}
    return {
        str(key): value
        for key, value in citation.items()
        if key in _ALLOWED_CITATION_KEYS
    }


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ArticleRagAskPromptAttachmentService:
    """Fail-soft transform: resolver result → Ask attachment.

    Pure orchestrator.  No I/O beyond what the injected resolver
    does.  The Ask layer calls :meth:`build_for_ask` and gets a
    typed, never-raises
    :class:`ArticleRagAskPromptAttachment` value object.
    """

    def __init__(
        self,
        *,
        resolver: _ResolverLike | None = None,
    ) -> None:
        # Lazy default: we refuse to silently pick a fake / an
        # unconfigured resolver.  Tests inject fakes; production
        # code injects the real resolver.
        self._resolver = resolver

    async def build_for_ask(
        self,
        *,
        reading_record_id: UUID,
        user_id: UUID,
        query_text: str,
        enabled: bool = True,
        limit: int = DEFAULT_ATTACHMENT_LIMIT,
        max_context_chars: int = DEFAULT_ATTACHMENT_MAX_CONTEXT_CHARS,
    ) -> ArticleRagAskPromptAttachment:
        """Build an :class:`ArticleRagAskPromptAttachment` for the
        Ask layer.

        Never raises.  Every failure (including a misconfigured
        resolver, an unexpected exception, or a malformed
        resolver result) maps to a fail-soft attachment with
        ``should_include_context=False`` and a stable
        ``failure_code``.
        """
        # 1. Validate injected dependency.
        if self._resolver is None:
            return self._make_unexpected_attachment(
                status="not_indexed_or_unavailable",
                reading_record_id=reading_record_id,
                enabled=enabled,
            )

        # 2. Delegate to the resolver.  The resolver itself is
        #    already designed to never raise (every failure is
        #    mapped to a typed result) — but the resolver DOES
        #    depend on injected services (context_service +
        #    composer), and a regression in the resolver (or a
        #    bug in this attachment service's wiring) could
        #    surface an unexpected exception.  We catch
        #    defensively and map to fail-soft.
        try:
            resolver_result = await self._resolver.resolve_for_record(
                reading_record_id=reading_record_id,
                user_id=user_id,
                query_text=query_text,
                enabled=enabled,
                limit=limit,
                max_context_chars=max_context_chars,
            )
        except Exception as exc:  # noqa: BLE001 — defensive catch-all
            # Unexpected resolver exception — the cause class name
            # is logged for ops dashboards; the cause object is
            # NOT attached to the public attachment.
            logger.info(
                "Article RAG ask prompt attachment: resolver "
                "raised %s (unexpected) for record=%s; returning "
                "fail-soft attachment",
                type(exc).__name__,
                reading_record_id,
            )
            return self._make_unexpected_attachment(
                status="not_indexed_or_unavailable",
                reading_record_id=reading_record_id,
                enabled=enabled,
            )

        # 3. Validate the resolver result shape.  A regression in
        #    the resolver (or a hostile fake in a test) could
        #    produce a result that doesn't match the contract —
        #    e.g. ``status="available"`` with ``bundle is None``.
        #    We treat any such shape as a malformed result and
        #    map to a fail-soft attachment rather than crashing.
        return self._build_from_resolver_result(
            resolver_result=resolver_result,
            reading_record_id=reading_record_id,
        )

    # ------------------------------------------------------------------
    # Internal builders
    # ------------------------------------------------------------------

    @staticmethod
    def _build_from_resolver_result(
        *,
        resolver_result: ArticleRagAskContextResolveResult,
        reading_record_id: UUID,
    ) -> ArticleRagAskPromptAttachment:
        """Build the attachment from a (possibly malformed)
        resolver result.

        Failure modes the contract must handle:

          * ``resolver_result`` is not an
            :class:`ArticleRagAskContextResolveResult` (a hostile
            fake in a test) — fall back to fail-soft
            ``not_indexed_or_unavailable``.
          * ``status="available"`` but ``bundle is None`` or
            ``prompt_context_text`` is empty — fall back to
            ``status="not_indexed_or_unavailable"`` (this is a
            contract violation: the resolver already invariant-
            checks this; we defend here in case of a regression).
          * Any other status — propagate unchanged, but with
            ``should_include_context=False``.
        """
        # Defensive shape check: a hostile / regression result
        # must not crash the Ask layer.
        if not isinstance(
            resolver_result, ArticleRagAskContextResolveResult
        ):
            return ArticleRagAskPromptAttachmentService._make_unexpected_attachment(
                status="not_indexed_or_unavailable",
                reading_record_id=reading_record_id,
                enabled=True,
            )

        # Runtime status guard: ``ArticleRagAskContextResolveStatus``
        # is a ``typing.Literal`` (compile-time only).  A
        # regression in the resolver (or a hostile fake in a test)
        # could surface an unrecognised status string (e.g.
        # ``"paused"``, ``"failed"``, ``""``).  The Ask layer keys
        # its fallback policy on the status literal — an unknown
        # value would silently break the dispatch contract.
        # Fail-soft to ``not_indexed_or_unavailable`` so the
        # Ask layer's default branch still works.
        resolver_status = resolver_result.status
        if resolver_status not in _ALLOWED_RESOLVER_STATUSES:
            return ArticleRagAskPromptAttachmentService._make_unexpected_attachment(
                status="not_indexed_or_unavailable",
                reading_record_id=reading_record_id,
                enabled=bool(resolver_result.enabled),
            )

        # The base shape (every status, including the OK path).
        base = ArticleRagAskPromptAttachment(
            enabled=bool(resolver_result.enabled),
            status=resolver_result.status,
            should_include_context=False,  # set below for the OK path
            fallback_allowed=bool(resolver_result.fallback_allowed),
            query_sha256=resolver_result.query_sha256,
            prompt_context_text="",
            citations=(),
            context_ids=(),
            # source_pack_hash is ONLY set on the OK path — the
            # base shape carries None for every non-OK status.
            source_pack_hash=None,
            failure_code=resolver_result.failure_code,
            retryable=bool(resolver_result.retryable),
            omitted_hit_count=resolver_result.omitted_hit_count,
            budget_exceeded=resolver_result.budget_exceeded,
            reading_record_id=resolver_result.reading_record_id,
            stable_document_id=resolver_result.stable_document_id,
            base_id=resolver_result.base_id,
            record_generation=resolver_result.record_generation,
            plan_content_sha256=resolver_result.plan_content_sha256,
        )

        # The OK path: status="available" AND the bundle is
        # well-formed AND the prompt_context_text is non-empty.
        if resolver_result.status != "available":
            return base

        bundle = resolver_result.bundle
        if not isinstance(bundle, ArticleRagAskContextBundle):
            # Contract violation — the resolver invariant failed.
            # Map to fail-soft ``not_indexed_or_unavailable``.
            return ArticleRagAskPromptAttachmentService._make_unexpected_attachment(
                status="not_indexed_or_unavailable",
                reading_record_id=reading_record_id,
                enabled=True,
            )

        # ``prompt_context_text`` MUST be non-empty for the
        # attachment to be includeable.  We copy it verbatim from
        # the bundle — the I4G composer built it; we MUST NOT
        # mutate it (e.g. inject citation JSON).
        prompt_text = bundle.prompt_context_text
        if not isinstance(prompt_text, str) or not prompt_text.strip():
            # Contract violation — fail-soft.
            return ArticleRagAskPromptAttachmentService._make_unexpected_attachment(
                status="not_indexed_or_unavailable",
                reading_record_id=reading_record_id,
                enabled=True,
            )

        # Citations are taken VERBATIM from ``bundle.citations``.
        # We do NOT parse ``prompt_context_text`` to extract
        # citations; we do NOT read any vector metadata to
        # reconstruct them; we do NOT inspect projection fields.
        citations = _citations_to_dicts(bundle.citations)

        # The OK attachment — every field populated.
        return ArticleRagAskPromptAttachment(
            enabled=bool(resolver_result.enabled),
            status=resolver_result.status,
            should_include_context=True,
            fallback_allowed=bool(resolver_result.fallback_allowed),
            query_sha256=resolver_result.query_sha256,
            prompt_context_text=prompt_text,
            citations=citations,
            context_ids=tuple(bundle.context_ids),
            source_pack_hash=bundle.source_pack_hash,
            failure_code=None,
            retryable=False,
            omitted_hit_count=resolver_result.omitted_hit_count,
            budget_exceeded=resolver_result.budget_exceeded,
            reading_record_id=resolver_result.reading_record_id,
            stable_document_id=resolver_result.stable_document_id,
            base_id=resolver_result.base_id,
            record_generation=resolver_result.record_generation,
            plan_content_sha256=resolver_result.plan_content_sha256,
        )

    @staticmethod
    def _make_unexpected_attachment(
        *,
        status: ArticleRagAskContextResolveStatus,
        reading_record_id: UUID,
        enabled: bool,
    ) -> ArticleRagAskPromptAttachment:
        """Build a fail-soft attachment for the unexpected-error
        path.

        Every field is set to its default / None / empty.  The
        failure_code is the resolver-specific
        ``FAILURE_CODE_ATTACHMENT_UNEXPECTED_ERROR``.  This
        helper exists so the unexpected path is symmetric across
        the three call sites (no resolver / resolver raised /
        malformed shape).
        """
        return ArticleRagAskPromptAttachment(
            enabled=bool(enabled),
            status=status,
            should_include_context=False,
            fallback_allowed=True,
            query_sha256=None,
            prompt_context_text="",
            citations=(),
            context_ids=(),
            source_pack_hash=None,
            failure_code=FAILURE_CODE_ATTACHMENT_UNEXPECTED_ERROR,
            retryable=False,
            omitted_hit_count=None,
            budget_exceeded=None,
            reading_record_id=reading_record_id,
            stable_document_id=None,
            base_id=None,
            record_generation=None,
            plan_content_sha256=None,
        )


__all__ = [
    "DEFAULT_ATTACHMENT_LIMIT",
    "DEFAULT_ATTACHMENT_MAX_CONTEXT_CHARS",
    "FAILURE_CODE_ATTACHMENT_UNEXPECTED_ERROR",
    "ArticleRagAskPromptAttachment",
    "ArticleRagAskPromptAttachmentService",
]