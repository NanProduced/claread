"""D6-I4D: Article RAG DashScope Embedding Adapter.

Real-adapter foundation for :class:`ArticleRagEmbeddingProvider` that
wraps the existing ``app.infra.bailian_embedding`` wrapper so we do not
introduce a new top-level ``dashscope`` import path. The wrapper already
handles credential resolution, registry routing, batch sizing, and
error classification for the DashScope TextEmbedding API; this module
only:

  * computes ``text_sha256 = sha256(input_text.encode("utf-8")).hexdigest()``
    *locally* (the I4C worker enforces ``emb.text_sha256 ==
    chunk.embedding_text_sha256`` so the worker can verify coverage
    without ever storing chunk text in the vector payload);
  * converts the wrapper's ``EmbeddingCallResult`` into
    ``list[ArticleRagEmbedding]`` in input order;
  * re-wraps ``EmbeddingError`` as a typed error that inherits
    :class:`ArticleRagIndexWorkerError` (so the I4C worker's
    ``_process_claimed_job`` exception handler at worker.py:628
    requeues ``retryable=True`` failures and transitions
    ``retryable=False`` ones to ``failed_terminal`` directly) and
    whose message NEVER carries the API key, chunk text, or any
    verbatim SDK content.  Callers that need the upstream diagnostic
    can inspect ``__cause__``.

Default factory fail-closes: when ``reader_article_rag_embedding_provider``
is empty/missing OR the wrapper's ``resolve_embedding_config`` returns
an empty API key, the factory returns
:class:`app.services.reader_orchestration.article_rag_index_worker.UnconfiguredArticleRagEmbeddingProvider`
— no network calls are ever made unless the deployment explicitly opts
in by setting ``READER_ARTICLE_RAG_EMBEDDING_PROVIDER=dashscope`` AND
the registry / ``BAILIAN_API_KEY`` fallback resolves a usable key.

Security contract
-----------------

* The API key is **never** logged, **never** included in exception
  messages, and **never** echoed in ``provider_metadata``.
* Chunk text is **never** logged at INFO or higher, and **never**
  included in exception messages.  Exception messages are a fixed
  diagnostic naming the wrapper, the input count, and the SDK
  exception class — nothing more.
* The embedding SHA-256 is computed locally from the input text BEFORE
  the wrapper is called so that any drift between the caller's text
  and what the wrapper actually saw would be visible to the worker.
* Per Fix 4 (review finding): the factory uses
  :func:`app.infra.bailian_embedding.resolve_embedding_config` as the
  single resolution path — matching the wrapper exactly.  The
  ``DASHSCOPE_API_KEY`` env var is therefore **not** recognised as
  an enable signal for the Article RAG embedding provider.  (It
  remains the recognised signal for the OCR adapter, whose own
  wrapper resolves it independently.)
"""

from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING, Any

from app.infra import bailian_embedding

from .article_rag_index_worker import (
    ArticleRagEmbedding,
    ArticleRagIndexWorkerError,
    UnconfiguredArticleRagEmbeddingProvider,
)

if TYPE_CHECKING:
    from app.config.settings import Settings

logger = logging.getLogger(__name__)


# Provider name constant — kept as a module-level constant for callers
# that want to match the configured provider name without depending on
# a string literal. NOT re-exported from the package root because the
# factory is the only sanctioned entry point.
READER_ARTICLE_RAG_EMBEDDING_PROVIDER_DASHSCOPE = "dashscope"


class DashScopeArticleRagEmbeddingProviderError(ArticleRagIndexWorkerError):
    """Typed failure raised by :class:`DashScopeArticleRagEmbeddingProvider`.

    Inherits :class:`ArticleRagIndexWorkerError` so the I4C worker's
    exception handler (which only catches the worker base class)
    requeues / fails the job correctly.

    ``failure_code`` is a stable, machine-readable label the worker uses
    to drive retry / terminal branching.  ``retryable=True`` for
    transient DashScope errors; ``retryable=False`` for configuration
    and coverage violations.

    The error message is a fixed diagnostic that explicitly excludes
    any chunk text or API key.  The underlying SDK exception (if any)
    is preserved as ``__cause__`` for ops inspection; it is never
    rendered into the message itself.
    """

    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        failure_code: str,
        failure_class: str = "embedding",
        rationale_code: str | None = None,
    ) -> None:
        super().__init__(
            message,
            retryable=retryable,
            failure_class=failure_class,
            failure_code=failure_code,
            rationale_code=rationale_code,
        )


def _text_sha256(text: str) -> str:
    """SHA-256 of the input text, hex-encoded (matching I4C convention)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class DashScopeArticleRagEmbeddingProvider:
    """Real DashScope (Bailian) embedding provider for the Article RAG worker.

    Implements the :class:`ArticleRagEmbeddingProvider` Protocol defined
    in D6-I4C. Wraps :func:`app.infra.bailian_embedding.embed_texts_with_metadata`
    and converts the result into ``ArticleRagEmbedding`` records with a
    *locally-computed* SHA-256.

    The adapter is constructed eagerly with no I/O; the actual DashScope
    call only happens inside :meth:`embed_texts`.  Credential resolution
    is delegated to the underlying wrapper
    (:func:`bailian_embedding.resolve_embedding_config`) so the adapter
    and the wrapper share a single resolution path — the
    ``DASHSCOPE_API_KEY`` env var is **not** consulted here (it is for
    the OCR adapter, which has its own wrapper).
    """

    def __init__(
        self,
        *,
        model_override: str | None = None,
    ) -> None:
        if model_override is not None and not str(model_override).strip():
            raise DashScopeArticleRagEmbeddingProviderError(
                "DashScope embedding provider constructed with an empty "
                "model_override; build_default_article_rag_embedding_provider "
                "must strip these before construction",
                retryable=False,
                failure_class="configuration",
                failure_code="embedding_provider_unconfigured",
            )
        self._model_override = str(model_override).strip() if model_override else None

    @property
    def provider_name(self) -> str:
        return READER_ARTICLE_RAG_EMBEDDING_PROVIDER_DASHSCOPE

    async def embed_texts(
        self,
        texts: list[str],
        *,
        model: str | None = None,
    ) -> list[ArticleRagEmbedding]:
        """Embed chunk texts, returning one record per input text.

        Order is preserved; SHA-256 is computed locally before the
        wrapper is invoked. Empty input list returns empty list with no
        wrapper call. Any ``EmbeddingError`` is re-wrapped as
        :class:`DashScopeArticleRagEmbeddingProviderError` whose
        message is a fixed diagnostic that excludes both the API key
        and any chunk text.  The original SDK exception remains
        reachable via ``__cause__``.
        """
        if not texts:
            return []

        effective_model = model or self._model_override

        try:
            call_result = await bailian_embedding.embed_texts_with_metadata(
                texts,
                model=effective_model,
                dimension=None,
            )
        except bailian_embedding.EmbeddingError as exc:
            # Per Fix 5: never forward the original SDK message — it
            # may echo chunk text.  Surface a fixed diagnostic that
            # names the wrapper, the input count, and the SDK
            # exception class only.  ``__cause__`` preserves the
            # original error for ops inspection.
            raise DashScopeArticleRagEmbeddingProviderError(
                "DashScope embedding call failed via bailian_embedding "
                f"(input_count={len(texts)}, "
                f"wrapper_exc={type(exc).__name__}); see __cause__ for "
                "upstream diagnostic",
                retryable=True,
                failure_class="embedding",
                failure_code="embedding_backend_failed",
            ) from exc

        embeddings = call_result.embeddings
        resolved_model = call_result.model
        resolved_dim = call_result.dimension
        if len(embeddings) != len(texts):
            raise DashScopeArticleRagEmbeddingProviderError(
                "DashScope embedding call returned "
                f"{len(embeddings)} embeddings for {len(texts)} inputs",
                retryable=False,
                failure_class="embedding_coverage",
                failure_code="embedding_coverage_mismatch",
            )

        results: list[ArticleRagEmbedding] = []
        for text, vector in zip(texts, embeddings, strict=True):
            vec_tuple = tuple(float(v) for v in vector)
            if resolved_dim and len(vec_tuple) != resolved_dim:
                # Defence in depth — bailian_embedding should already
                # have cross-checked; we re-check here so a future
                # regression in the wrapper cannot silently propagate a
                # wrong-dim vector.
                raise DashScopeArticleRagEmbeddingProviderError(
                    "DashScope embedding returned a vector of unexpected "
                    f"length for a {resolved_dim}-dim config",
                    retryable=False,
                    failure_class="embedding_coverage",
                    failure_code="embedding_dimension_mismatch",
                )
            results.append(
                ArticleRagEmbedding(
                    text_sha256=_text_sha256(text),
                    model=str(resolved_model),
                    vector=vec_tuple,
                    dim=len(vec_tuple),
                )
            )
        return results


def build_default_article_rag_embedding_provider(
    settings: Settings,
) -> Any:
    """Factory for the default Article RAG embedding provider.

    Returns:
      * :class:`DashScopeArticleRagEmbeddingProvider` when
        ``settings.reader_article_rag_embedding_provider == "dashscope"``
        AND the wrapper's
        :func:`app.infra.bailian_embedding.resolve_embedding_config`
        returns a non-empty API key (the same path the underlying
        wrapper uses on every call);
      * otherwise :class:`UnconfiguredArticleRagEmbeddingProvider`.

    The factory NEVER logs the API key.  The factory NEVER raises on
    misconfiguration — it returns the unconfigured provider so the
    caller surfaces ``FAILURE_CODE_EMBEDDING_PROVIDER_UNCONFIGURED``
    through the worker's error handlers, not as a startup failure.

    Behavioural contract (Fix 4): the ``DASHSCOPE_API_KEY`` env var is
    **not** consulted here.  Use ``BAILIAN_API_KEY`` (or the registry
    route ``RAG_EMBEDDING_MODEL_PROFILE``) to opt into the DashScope
    embedding provider for Article RAG.  This matches the
    authentication path of :func:`bailian_embedding.embed_texts_with_metadata`
    so the factory's enable signal and the wrapper's key resolution
    never diverge.
    """
    provider_name = (
        getattr(settings, "reader_article_rag_embedding_provider", "") or ""
    ).strip().lower()
    if provider_name != READER_ARTICLE_RAG_EMBEDDING_PROVIDER_DASHSCOPE:
        logger.debug(
            "Article RAG embedding provider not configured "
            "(reader_article_rag_embedding_provider=%r); using "
            "UnconfiguredArticleRagEmbeddingProvider",
            provider_name,
        )
        return UnconfiguredArticleRagEmbeddingProvider()

    # Single resolution path — same as the wrapper.
    # ``resolve_embedding_config`` honours the RAG_EMBEDDING_MODEL_PROFILE
    # registry route and falls back to ``settings.bailian_api_key``.  If
    # it returns an empty key here, the wrapper would raise
    # EmbeddingError("No API key configured ...") on the first call —
    # better to fail closed at construction.
    try:
        _resolved_model, _resolved_dim, resolved_key = (
            bailian_embedding.resolve_embedding_config()
        )
    except bailian_embedding.EmbeddingError as exc:
        # Wrapper rejected the runtime configuration.  Fail closed;
        # surface debug only.
        logger.debug(
            "Article RAG embedding provider='dashscope' but "
            "bailian_embedding.resolve_embedding_config raised %s; "
            "falling back to UnconfiguredArticleRagEmbeddingProvider",
            type(exc).__name__,
        )
        return UnconfiguredArticleRagEmbeddingProvider()

    if not (resolved_key or "").strip():
        logger.debug(
            "Article RAG embedding provider='dashscope' but "
            "bailian_embedding.resolve_embedding_config returned an "
            "empty API key; using UnconfiguredArticleRagEmbeddingProvider"
        )
        return UnconfiguredArticleRagEmbeddingProvider()

    model_override = (
        getattr(settings, "reader_article_rag_embedding_model", "") or ""
    ).strip() or None
    return DashScopeArticleRagEmbeddingProvider(model_override=model_override)
