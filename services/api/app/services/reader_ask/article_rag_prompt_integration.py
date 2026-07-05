"""D6-I4P / D6-I4Q: Article RAG Ask Prompt Runtime Integration.

Minimal integration layer that wires the Article RAG context
pipeline (D6-I4A through I4O) into the existing Reader Ask
prompt runtime.  The integration sits at the boundary between
``runtime_contract_svc.build_prompt_payload(...)`` and
``runtime_contract_svc.prepare_prompt_payload(...)`` in
``app/services/reader_ask/service.py``.

Contract
--------

``ArticleRagPromptIntegration.integrate(...)``:

  * accepts the base ``prompt_payload`` dict (produced by
    ``build_prompt_payload``) plus the reading record id,
    user id, and query text;
  * calls :meth:`ArticleRagAskContextProvider.build_for_ask`
    to obtain an :class:`ArticleRagAskPromptAssembly`;
  * calls :meth:`ArticleRagAskPromptBridge.bridge` to combine
    the base prompt text with the RAG assembly;
  * returns an :class:`ArticleRagPromptIntegrationResult`
    carrying:
      - ``payload``: the (possibly mutated) prompt payload dict.
        On ``should_attach=True`` the ``user_message`` field is
        replaced with ``bridge.prompt_text`` (base prompt + RAG
        envelope).  On ``should_attach=False`` the payload is
        returned unchanged.
      - ``sidecar``: an :class:`ArticleRagSidecar` carrying
        structured citations / context_ids / metadata OUT of
        the prompt payload.  The sidecar NEVER enters
        ``build_reader_ask_prompt`` — it flows through
        ``ReaderAskRuntimeState`` to the completed payload.
  * never raises — every failure (missing provider, missing
    bridge, provider exception, bridge exception, unexpected
    shape) maps to a fail-soft result with the original payload
    and an empty sidecar.

Truth boundary
--------------

The integration layer is the SINGLE boundary between the
Article RAG pipeline and the Ask prompt runtime.  It:

  * never re-parses ``prompt_text`` to extract citations;
  * never writes citation JSON or Article RAG sidecars into the
    prompt payload, because ``build_reader_ask_prompt`` serializes
    the whole payload for the LLM;
  * never includes ``query_text`` in any repr / log / result;
  * never mutates the payload on the no-attach path;
  * never writes to the DB, never calls DashScope / Zilliz
    directly (all retrieval is delegated to the provider).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from app.services.reader_orchestration.article_rag_ask_context_provider import (
    DEFAULT_FACADE_INDEX_VERSION,
    DEFAULT_FACADE_LIMIT,
    DEFAULT_FACADE_MAX_CONTEXT_CHARS,
    ArticleRagAskContextProvider,
)
from app.services.reader_orchestration.article_rag_ask_prompt_bridge import (
    ArticleRagAskPromptBridge,
    ArticleRagAskPromptBridgeResult,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Failure code for the integration layer's own fail-soft path.
# Distinct from the provider / bridge codes so dashboards can
# distinguish "the integration itself caught an unexpected
# error" from "the provider / bridge fail-softed".
FAILURE_CODE_INTEGRATION_UNEXPECTED_ERROR = (
    "article_rag_prompt_integration_unexpected_error"
)


# ---------------------------------------------------------------------------
# Sidecar dataclass (D6-I4Q)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ArticleRagSidecar:
    """Structured sidecar carrying Article RAG citations and
    metadata OUT of the prompt payload.

    This data NEVER enters ``build_reader_ask_prompt`` — it
    flows through ``ReaderAskRuntimeState`` to the completed
    payload.  The sidecar is the output-side contract for
    Article RAG evidence.

    All fields that may carry user-derived content use
    ``field(repr=False)`` so the default repr / str does NOT
    echo it.
    """

    # Whether the RAG context was attached to the prompt.
    should_attach: bool
    # Structured citations (verbatim from the bridge result).
    citations: tuple[dict[str, Any], ...] = field(repr=False, default=())
    # Stable context ids embedded in the prompt attachment.
    context_ids: tuple[str, ...] = field(repr=False, default=())
    # Source identity hash from the I4G composer.
    source_pack_hash: str | None = field(repr=False, default=None)
    # SHA-256 of the query text (never the raw query).
    query_sha256: str | None = field(repr=False, default=None)
    # Upstream status (guarded by the 5-value allowlist).
    status: str = field(repr=False, default="not_indexed_or_unavailable")
    # Upstream failure code.
    failure_code: str | None = field(repr=False, default=None)
    # Upstream retryable flag.
    retryable: bool = True
    # Upstream fallback-allowed flag.
    fallback_allowed: bool = True
    # Strict-allowlist metadata (same allowlist as I4L / I4M).
    metadata_json: dict[str, Any] = field(
        repr=False, default_factory=dict
    )

    @classmethod
    def empty(cls) -> ArticleRagSidecar:
        """Return an empty sidecar for the no-attach / fail-soft path."""
        return cls(should_attach=False)

    @classmethod
    def from_bridge_result(
        cls,
        bridge_result: ArticleRagAskPromptBridgeResult,
    ) -> ArticleRagSidecar:
        """Build a sidecar from a bridge result.

        On the no-attach path the bridge result has empty
        citations / context_ids — the sidecar mirrors that.
        """
        return cls(
            should_attach=bridge_result.should_attach,
            citations=tuple(bridge_result.citations),
            context_ids=tuple(bridge_result.context_ids),
            source_pack_hash=bridge_result.source_pack_hash,
            query_sha256=bridge_result.query_sha256,
            status=bridge_result.status,
            failure_code=bridge_result.failure_code,
            retryable=bridge_result.retryable,
            fallback_allowed=bridge_result.fallback_allowed,
            metadata_json=dict(bridge_result.metadata_json),
        )


@dataclass(frozen=True, slots=True)
class ArticleRagPromptIntegrationResult:
    """Result of :meth:`ArticleRagPromptIntegration.integrate`.

    Carries the (possibly mutated) prompt payload AND the
    structured sidecar.  The sidecar is SEPARATE from the
    payload — it must NOT be merged into ``prompt_payload``
    because ``build_reader_ask_prompt`` serializes the entire
    payload for the LLM.
    """

    # The prompt payload dict.  On the attach path the
    # ``user_message`` field has been replaced with the bridge's
    # combined prompt text.
    payload: dict[str, Any]
    # The structured sidecar.  On the no-attach / fail-soft path
    # this is an empty sidecar (``should_attach=False``).
    sidecar: ArticleRagSidecar


# ---------------------------------------------------------------------------
# Dependency protocols
# ---------------------------------------------------------------------------


class _ProviderLike(Protocol):
    """Minimal shape :class:`ArticleRagAskContextProvider`
    exposes."""

    async def build_for_ask(
        self,
        *,
        reading_record_id: UUID,
        user_id: UUID,
        query_text: str,
        enabled: bool = ...,
        limit: int = ...,
        max_context_chars: int = ...,
        index_version: str = ...,
    ) -> Any: ...


class _BridgeLike(Protocol):
    """Minimal shape :class:`ArticleRagAskPromptBridge` exposes."""

    def bridge(
        self,
        *,
        base_prompt_text: str | None,
        rag_assembly: Any,
    ) -> ArticleRagAskPromptBridgeResult: ...


# ---------------------------------------------------------------------------
# Integration helper
# ---------------------------------------------------------------------------


class ArticleRagPromptIntegration:
    """Minimal integration helper that wires Article RAG context
    into the Reader Ask prompt payload.

    Pure orchestrator.  No I/O beyond what the injected provider
    does.  The Ask service calls :meth:`integrate` between
    ``build_prompt_payload`` and ``prepare_prompt_payload`` and
    gets back an :class:`ArticleRagPromptIntegrationResult`
    carrying the (possibly mutated) payload dict AND a structured
    sidecar.

    Never raises.  Every failure maps to a fail-soft result with
    the original payload unchanged and an empty sidecar.
    """

    def __init__(
        self,
        *,
        provider: _ProviderLike | None = None,
        bridge: _BridgeLike | None = None,
    ) -> None:
        self._provider = provider
        self._bridge = bridge

    async def integrate(
        self,
        *,
        prompt_payload: dict[str, Any],
        reading_record_id: UUID,
        user_id: UUID,
        query_text: str,
        enabled: bool = True,
        limit: int = DEFAULT_FACADE_LIMIT,
        max_context_chars: int = DEFAULT_FACADE_MAX_CONTEXT_CHARS,
        index_version: str = DEFAULT_FACADE_INDEX_VERSION,
    ) -> ArticleRagPromptIntegrationResult:
        """Integrate Article RAG context into ``prompt_payload``.

        Returns an :class:`ArticleRagPromptIntegrationResult`
        carrying the (possibly mutated) payload and a structured
        sidecar.  On the no-attach / fail-soft path the payload is
        returned unchanged and the sidecar is empty.

        Never raises.  Every failure (missing provider / bridge,
        provider exception, bridge exception, unexpected shape)
        maps to a fail-soft result with the original payload.
        """
        empty_sidecar = ArticleRagSidecar.empty()

        # 1. Validate injected dependencies.  A missing provider
        #    or bridge is a wiring error (production code
        #    injects both; tests inject fakes).  The integration
        #    does NOT silently fall back to a default — it
        #    returns the payload unchanged so the Ask runtime
        #    answers without RAG.
        if self._provider is None or self._bridge is None:
            return ArticleRagPromptIntegrationResult(
                payload=prompt_payload,
                sidecar=empty_sidecar,
            )

        # 2. Defensive shape check on the payload.  A regression
        #    could pass a non-dict.  Fail-soft.
        if not isinstance(prompt_payload, dict):
            return ArticleRagPromptIntegrationResult(
                payload=prompt_payload,
                sidecar=empty_sidecar,
            )

        # 3. Extract the base prompt text from the payload.  The
        #    ``user_message`` field is the user's question — the
        #    most natural base for the RAG envelope.  If it's
        #    missing or not a string, the bridge will fail-soft.
        base_prompt_text = prompt_payload.get("user_message", "")
        if not isinstance(base_prompt_text, str):
            base_prompt_text = ""

        # 4. Call the provider to build the RAG assembly.  The
        #    provider is async and never raises (every failure
        #    maps to a fail-soft assembly).  We still wrap in
        #    try/except for defence in depth.
        try:
            assembly = await self._provider.build_for_ask(
                reading_record_id=reading_record_id,
                user_id=user_id,
                query_text=query_text,
                enabled=enabled,
                limit=limit,
                max_context_chars=max_context_chars,
                index_version=index_version,
            )
        except Exception as exc:  # noqa: BLE001 — defensive catch-all
            logger.info(
                "Article RAG prompt integration: provider raised "
                "%s (unexpected); returning original payload "
                "unchanged",
                type(exc).__name__,
            )
            return ArticleRagPromptIntegrationResult(
                payload=prompt_payload,
                sidecar=empty_sidecar,
            )

        # 5. Call the bridge to combine the base prompt text with
        #    the RAG assembly.  The bridge never raises (every
        #    failure maps to a fail-soft bridge result).  We
        #    still wrap in try/except for defence in depth.
        try:
            bridge_result = self._bridge.bridge(
                base_prompt_text=base_prompt_text,
                rag_assembly=assembly,
            )
        except Exception as exc:  # noqa: BLE001 — defensive catch-all
            logger.info(
                "Article RAG prompt integration: bridge raised "
                "%s (unexpected); returning original payload "
                "unchanged",
                type(exc).__name__,
            )
            return ArticleRagPromptIntegrationResult(
                payload=prompt_payload,
                sidecar=empty_sidecar,
            )

        # 6. Shape check on the bridge result.  A regression
        #    could return a non-ArticleRagAskPromptBridgeResult.
        #    Fail-soft.
        if not isinstance(bridge_result, ArticleRagAskPromptBridgeResult):
            logger.info(
                "Article RAG prompt integration: bridge returned "
                "non-ArticleRagAskPromptBridgeResult "
                "(type=%s); returning original payload unchanged",
                type(bridge_result).__name__,
            )
            return ArticleRagPromptIntegrationResult(
                payload=prompt_payload,
                sidecar=empty_sidecar,
            )

        # 7. Build the sidecar from the bridge result.  The
        #    sidecar carries structured citations / context_ids /
        #    metadata OUT of the prompt payload.  On the
        #    no-attach path the bridge result has empty
        #    citations — the sidecar mirrors that.
        sidecar = ArticleRagSidecar.from_bridge_result(bridge_result)

        # 8. No-attach path: return the payload unchanged.  The
        #    Ask runtime answers without RAG.  The sidecar still
        #    carries status / failure_code for ops visibility.
        if not bridge_result.should_attach:
            return ArticleRagPromptIntegrationResult(
                payload=prompt_payload,
                sidecar=sidecar,
            )

        # 9. Attach path: write the combined prompt text back to
        #    ``payload["user_message"]``.  The ``prompt_text`` is
        #    ``base_prompt_text + "\n\n" + envelope`` — the LLM
        #    sees the user's question followed by the RAG context
        #    bracket.
        #
        #    Do NOT write bridge citations, attachment_block, or any
        #    Article RAG sidecar into ``prompt_payload``.  The Reader
        #    Ask agent prompt is built by serializing the entire
        #    payload, so any sidecar stored here would be inlined into
        #    the LLM prompt and duplicate the RAG context.
        prompt_payload["user_message"] = bridge_result.prompt_text

        return ArticleRagPromptIntegrationResult(
            payload=prompt_payload,
            sidecar=sidecar,
        )


# ---------------------------------------------------------------------------
# Production factory
# ---------------------------------------------------------------------------


def build_default_article_rag_prompt_integration(
    settings: Any,
) -> ArticleRagPromptIntegration | None:
    """Factory for the default Article RAG prompt integration.

    Returns:
      * an :class:`ArticleRagPromptIntegration` wired with the
        full production provider chain when
        ``settings.reader_article_rag_enabled`` is ``True`` AND
        the Zilliz / embedding configuration is present;
      * ``None`` otherwise — the caller (Ask service) treats
        ``None`` as "RAG unavailable; answer without RAG".

    The factory NEVER raises on misconfiguration — it returns
    ``None`` so the Ask service's fail-soft path fires silently.

    The factory NEVER logs the Zilliz token / DashScope API
    key.  The factory NEVER makes network calls (the provider
    chain is constructed but not invoked).
    """
    # 1. Top-level feature flag.
    enabled = bool(
        getattr(settings, "reader_article_rag_enabled", False)
    )
    if not enabled:
        logger.debug(
            "Article RAG prompt integration not configured "
            "(reader_article_rag_enabled=False); returning None"
        )
        return None

    # 2. Zilliz vector search configuration.
    resolve_uri = getattr(settings, "resolve_reader_article_rag_zilliz_uri", None)
    zilliz_uri = (
        resolve_uri()
        if callable(resolve_uri)
        else getattr(settings, "reader_article_rag_zilliz_uri", "")
    )
    zilliz_uri = (zilliz_uri or "").strip()

    resolve_token = getattr(
        settings, "resolve_reader_article_rag_zilliz_token", None
    )
    zilliz_token = (
        resolve_token()
        if callable(resolve_token)
        else getattr(settings, "reader_article_rag_zilliz_token", "")
    )
    zilliz_token = (zilliz_token or "").strip()
    zilliz_collection = (
        getattr(settings, "reader_article_rag_zilliz_collection", "")
        or ""
    ).strip()
    if not zilliz_uri or not zilliz_token or not zilliz_collection:
        logger.debug(
            "Article RAG prompt integration: Zilliz configuration "
            "incomplete (uri/empty=%s, token/empty=%s, "
            "collection/empty=%s); returning None",
            not zilliz_uri,
            not zilliz_token,
            not zilliz_collection,
        )
        return None

    # 3. Embedding provider configuration.  We delegate to the
    #    existing factory — if it returns an
    #    ``UnconfiguredArticleRagEmbeddingProvider``, the
    #    retrieval service will fail-closed at runtime and the
    #    provider chain will fail-soft to a no-attach assembly.
    #    We do NOT block construction here: the embedding
    #    provider's own factory is the single source of truth
    #    for embedding config.
    try:
        from app.services.reader_orchestration.article_rag_ask_context_composer import (
            ArticleRagAskContextComposer,
        )
        from app.services.reader_orchestration.article_rag_ask_context_resolver import (
            ArticleRagAskContextResolver,
        )
        from app.services.reader_orchestration.article_rag_ask_integration_adapter import (
            ArticleRagAskIntegrationAdapter,
        )
        from app.services.reader_orchestration.article_rag_ask_prompt_assembly import (
            ArticleRagAskPromptAssemblyService,
        )
        from app.services.reader_orchestration.article_rag_ask_prompt_attachment import (
            ArticleRagAskPromptAttachmentService,
        )
        from app.services.reader_orchestration.article_rag_ask_prompt_section import (
            ArticleRagAskPromptSectionBuilder,
        )
        from app.services.reader_orchestration.article_rag_ask_runtime_adapter import (
            ArticleRagAskRuntimeAdapter,
        )
        from app.services.reader_orchestration.article_rag_context_service import (
            ArticleRagContextService,
        )
        from app.services.reader_orchestration.article_rag_embedding_provider import (
            build_default_article_rag_embedding_provider,
        )
        from app.services.reader_orchestration.article_rag_retrieval_service import (
            ArticleRagRetrievalService,
        )
        from app.services.reader_orchestration.article_rag_vector_search import (
            build_default_article_rag_vector_searcher,
        )
    except ImportError as exc:
        logger.debug(
            "Article RAG prompt integration: import failed "
            "(%s); returning None",
            type(exc).__name__,
        )
        return None

    try:
        embedding_provider = build_default_article_rag_embedding_provider(
            settings
        )
        vector_searcher = build_default_article_rag_vector_searcher(
            settings
        )
        retrieval_service = ArticleRagRetrievalService(
            embedding_provider=embedding_provider,
            vector_searcher=vector_searcher,
        )
        context_service = ArticleRagContextService(
            retrieval_service=retrieval_service,
        )
        composer = ArticleRagAskContextComposer()
        resolver = ArticleRagAskContextResolver(
            context_service=context_service,
            composer=composer,
        )
        attachment_service = ArticleRagAskPromptAttachmentService(
            resolver=resolver,
        )
        integration_adapter = ArticleRagAskIntegrationAdapter(
            attachment_service=attachment_service,
        )
        section_builder = ArticleRagAskPromptSectionBuilder()
        runtime_adapter = ArticleRagAskRuntimeAdapter()
        assembly_service = ArticleRagAskPromptAssemblyService()
        provider = ArticleRagAskContextProvider(
            integration_adapter=integration_adapter,
            section_builder=section_builder,
            runtime_adapter=runtime_adapter,
            assembly_service=assembly_service,
        )
        bridge = ArticleRagAskPromptBridge()
    except Exception as exc:  # noqa: BLE001 — defensive catch-all
        logger.debug(
            "Article RAG prompt integration: construction failed "
            "(%s); returning None",
            type(exc).__name__,
        )
        return None

    return ArticleRagPromptIntegration(
        provider=provider,
        bridge=bridge,
    )


__all__ = [
    "FAILURE_CODE_INTEGRATION_UNEXPECTED_ERROR",
    "ArticleRagPromptIntegration",
    "ArticleRagPromptIntegrationResult",
    "ArticleRagSidecar",
    "build_default_article_rag_prompt_integration",
]
