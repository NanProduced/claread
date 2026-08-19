"""Article RAG DashScope Embedding Adapter.

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
    verbatim SDK content.  The original ``EmbeddingError`` is
    intentionally discarded — ops diagnosis depends only on the
    safe structured diagnostics envelope (no ``__cause__`` /
    ``__context__`` chain is preserved, so traceback serialisation
    cannot leak the lower message).

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
from app.infra.bailian_usage import canonical_embedding_tokens

from .article_rag_index_worker import (
    ARTICLE_RAG_EMBEDDING_COMPLETENESS_COMPLETE,
    ARTICLE_RAG_EMBEDDING_COMPLETENESS_PARTIAL,
    ARTICLE_RAG_EMBEDDING_COMPLETENESS_UNAVAILABLE,
    ARTICLE_RAG_EMBEDDING_USAGE_MAX_BATCHES,
    ArticleRagEmbedding,
    ArticleRagEmbeddingBatchUsage,
    ArticleRagEmbeddingInvocationResult,
    ArticleRagEmbeddingUsageReport,
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
    """Typed DashScope failure carrying only a safe diagnostic envelope."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        failure_code: str,
        failure_class: str = "embedding",
        rationale_code: str | None = None,
        diagnostics: dict[str, str | int | bool | None] | None = None,
        embedding_usage_report: ArticleRagEmbeddingUsageReport | None = None,
    ) -> None:
        super().__init__(
            message,
            retryable=retryable,
            failure_class=failure_class,
            failure_code=failure_code,
            rationale_code=rationale_code,
            diagnostics=diagnostics,
            embedding_usage_report=embedding_usage_report,
        )


def _text_sha256(text: str) -> str:
    """SHA-256 of the input text, hex-encoded (matching I4C convention)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _safe_embedding_diagnostics(
    exc: bailian_embedding.EmbeddingError,
) -> dict[str, str | int | bool | None]:
    """Return only bounded fields explicitly supplied by the wrapper.

    Reuses the wrapper's shared whitelist helpers
    (``_safe_provider_status`` / ``_safe_provider_code``) so the
    adapter and the wrapper apply identical sanitisation rules.  The
    wrapper already sanitises at the SDK boundary; this second pass
    defends against an ``EmbeddingError`` constructed elsewhere that
    bypassed the wrapper's whitelist (e.g. a test fake or a future
    caller).
    """
    diagnostics: dict[str, str | int | bool | None] = {}
    status = bailian_embedding._safe_provider_status(
        getattr(exc, "status_code", None)
    )
    if status is not None:
        diagnostics["provider_status"] = status
    code = bailian_embedding._safe_provider_code(
        getattr(exc, "provider_code", None)
    )
    if code is not None:
        diagnostics["provider_code"] = code
    retryable = getattr(exc, "retryable", None)
    if isinstance(retryable, bool):
        diagnostics["provider_retryable"] = retryable
    ordinal = getattr(exc, "failed_batch_ordinal", None)
    batch_count = getattr(exc, "batch_count", None)
    # ``bool`` is a subclass of ``int`` in Python, so the
    # legacy ``isinstance(value, int) and value > 0`` check accepts
    # ``True`` (== 1) as a valid positive integer.  Reject bool
    # explicitly so ``True``/``False`` cannot leak into diagnostics
    # (where they would JSON-serialise as ``true``/``false`` instead
    # of the expected int).
    if isinstance(ordinal, int) and not isinstance(ordinal, bool) and ordinal > 0:
        diagnostics["failed_batch_ordinal"] = ordinal
    if (
        isinstance(batch_count, int)
        and not isinstance(batch_count, bool)
        and batch_count > 0
    ):
        diagnostics["batch_count"] = batch_count
    return diagnostics


class DashScopeArticleRagEmbeddingProvider:
    """Real DashScope (Bailian) embedding provider for the Article RAG worker.

    Implements the retrieval-facing :class:`ArticleRagEmbeddingProvider`
    Protocol (``embed_texts``) AND the index-scoped
    :class:`ArticleRagIndexEmbeddingProvider` Protocol
    (``embed_texts_with_usage``, OBS-01B-B). Wraps
    :func:`app.infra.bailian_embedding.embed_texts_with_metadata` and
    converts the result into ``ArticleRagEmbedding`` records with a
    *locally-computed* SHA-256, plus the typed
    :class:`ArticleRagEmbeddingUsageReport` built from the wrapper's
    canonical embedding token mapping.

    The adapter is constructed eagerly with no I/O; the actual DashScope
    call only happens inside :meth:`embed_texts_with_usage`.
    Credential resolution is delegated to the underlying wrapper
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

    # ------------------------------------------------------------------
    # Report construction helpers (safe, bounded, canonical-only)
    # ------------------------------------------------------------------

    @staticmethod
    def _batch_summaries(
        batches: list[dict[str, Any]] | None,
    ) -> tuple[
        tuple[ArticleRagEmbeddingBatchUsage, ...],
        int,
        int,
    ]:
        """Return ``(batches, available_batch_count, truncated_count)``.

        ``batches`` is bounded to
        ``ARTICLE_RAG_EMBEDDING_USAGE_MAX_BATCHES`` entries; the
        truncated count is exact. ``input_chars`` and any other
        non-whitelisted keys from the wrapper's in-memory batch metadata
        are dropped here — only ordinal / request_id / input_count /
        total_tokens survive.
        """
        entries = list(batches or [])
        available = sum(
            1 for entry in entries if entry.get("provider_usage_available")
        )
        truncated = max(
            0, len(entries) - ARTICLE_RAG_EMBEDDING_USAGE_MAX_BATCHES
        )
        bounded = entries[:ARTICLE_RAG_EMBEDDING_USAGE_MAX_BATCHES]
        summaries = tuple(
            ArticleRagEmbeddingBatchUsage(
                ordinal=ordinal,
                request_id=(
                    str(entry["request_id"])
                    if entry.get("request_id") is not None
                    else None
                ),
                input_count=int(entry.get("input_count") or 0),
                total_tokens=int(entry.get("total_tokens") or 0),
            )
            for ordinal, entry in enumerate(bounded, start=1)
        )
        return summaries, available, truncated

    @staticmethod
    def _success_completeness(
        *,
        completed_batch_count: int,
        available_batch_count: int,
    ) -> str:
        if completed_batch_count == 0:
            return ARTICLE_RAG_EMBEDDING_COMPLETENESS_UNAVAILABLE
        if available_batch_count == completed_batch_count:
            return ARTICLE_RAG_EMBEDDING_COMPLETENESS_COMPLETE
        if available_batch_count >= 1:
            return ARTICLE_RAG_EMBEDDING_COMPLETENESS_PARTIAL
        return ARTICLE_RAG_EMBEDDING_COMPLETENESS_UNAVAILABLE

    @staticmethod
    def _failure_completeness(
        *,
        completed_batch_count: int,
        available_batch_count: int,
    ) -> str:
        if completed_batch_count == 0:
            return ARTICLE_RAG_EMBEDDING_COMPLETENESS_UNAVAILABLE
        if available_batch_count >= 1:
            return ARTICLE_RAG_EMBEDDING_COMPLETENESS_PARTIAL
        return ARTICLE_RAG_EMBEDDING_COMPLETENESS_UNAVAILABLE

    def _report_from_success(
        self,
        call_result: Any,
        *,
        effective_model: str | None,
    ) -> ArticleRagEmbeddingUsageReport:
        canonical = canonical_embedding_tokens(call_result.usage_data)
        batch_count = int(call_result.batch_count or 0)
        batches_meta = (call_result.provider_metadata or {}).get("batches") or []
        summaries, available, truncated = self._batch_summaries(batches_meta)
        # Success path: every planned batch completed.
        completed = len(batches_meta)
        input_tokens = int(canonical["aggregate"]["input_tokens"])
        return ArticleRagEmbeddingUsageReport(
            provider_call_attempted=True,
            provider_succeeded=True,
            usage_completeness=self._success_completeness(
                completed_batch_count=completed,
                available_batch_count=available,
            ),
            input_tokens=input_tokens,
            output_tokens=0,
            total_tokens=input_tokens,
            batch_count=batch_count,
            completed_batch_count=completed,
            failed_batch_ordinal=None,
            batches=summaries,
            batches_truncated_count=truncated,
            provider_name=READER_ARTICLE_RAG_EMBEDDING_PROVIDER_DASHSCOPE,
            # Actual outbound model — never a frozen-contract stand-in.
            model_name=str(
                call_result.model or effective_model or "unknown"
            ),
        )

    def _report_from_wrapper_error(
        self,
        exc: bailian_embedding.EmbeddingError,
        *,
        effective_model: str | None,
    ) -> ArticleRagEmbeddingUsageReport | None:
        """Build a partial/failure report from a wrapper EmbeddingError.

        Returns ``None`` for pre-provider failures (``provider_call_
        attempted=False``): no outbound call happened, so no usage may be
        claimed. Tokens cover the COMPLETED batches only — never
        fabricated from the planned input.
        """
        if not exc.provider_call_attempted:
            return None
        canonical = canonical_embedding_tokens(exc.usage_data)
        batches_meta = (exc.provider_metadata or {}).get("batches") or []
        summaries, available, truncated = self._batch_summaries(batches_meta)
        completed = int(exc.completed_batch_count or 0)
        input_tokens = int(canonical["aggregate"]["input_tokens"])
        return ArticleRagEmbeddingUsageReport(
            provider_call_attempted=True,
            provider_succeeded=False,
            usage_completeness=self._failure_completeness(
                completed_batch_count=completed,
                available_batch_count=available,
            ),
            input_tokens=input_tokens,
            output_tokens=0,
            total_tokens=input_tokens,
            batch_count=int(exc.batch_count or 0),
            completed_batch_count=completed,
            failed_batch_ordinal=exc.failed_batch_ordinal,
            batches=summaries,
            batches_truncated_count=truncated,
            provider_name=READER_ARTICLE_RAG_EMBEDDING_PROVIDER_DASHSCOPE,
            # Actual outbound model: wrapper-reported first, then the
            # adapter-determined effective_model, never the contract.
            model_name=str(
                exc.model or effective_model or "unknown"
            ),
        )

    # ------------------------------------------------------------------
    # Index-scoped protocol method (single provider invocation)
    # ------------------------------------------------------------------

    async def embed_texts_with_usage(
        self,
        texts: list[str],
        *,
        model: str | None = None,
    ) -> ArticleRagEmbeddingInvocationResult:
        """Embed chunk texts and return embeddings + typed usage report.

        One wrapper invocation. The usage report is built from the
        successful ``EmbeddingCallResult`` BEFORE local coverage /
        dimension validation, so a post-provider validation failure still
        raises a typed error carrying ``provider_succeeded=True`` plus the
        full report (later stages record provider success +
        validation-failed-after-embedding). Wrapper failures attach a
        partial report (or ``None`` for pre-provider failures) to the
        typed error. The typed error is raised OUTSIDE the except block so
        ``__cause__`` / ``__context__`` stay None.
        """
        if not texts:
            return ArticleRagEmbeddingInvocationResult(
                embeddings=(),
                usage_report=ArticleRagEmbeddingUsageReport(
                    provider_call_attempted=False,
                    provider_succeeded=True,
                    usage_completeness=(
                        ARTICLE_RAG_EMBEDDING_COMPLETENESS_UNAVAILABLE
                    ),
                    input_tokens=0,
                    output_tokens=0,
                    total_tokens=0,
                    batch_count=0,
                    completed_batch_count=0,
                    failed_batch_ordinal=None,
                    batches=(),
                    batches_truncated_count=0,
                    provider_name=READER_ARTICLE_RAG_EMBEDDING_PROVIDER_DASHSCOPE,
                    model_name=str(model or self._model_override or "unknown"),
                ),
            )

        effective_model = model or self._model_override

        # Extract only the whitelisted diagnostic fields inside the
        # except block, then raise the safe outer error AFTER leaving the
        # block. This keeps both ``__cause__`` (no ``raise ... from exc``)
        # and ``__context__`` (no implicit chain from re-raising inside
        # ``except``) None, so traceback serialisation cannot echo the
        # lower exception's message.
        rewrap_error: DashScopeArticleRagEmbeddingProviderError | None = None
        call_result = None
        try:
            call_result = await bailian_embedding.embed_texts_with_metadata(
                texts,
                model=effective_model,
                dimension=None,
            )
        except bailian_embedding.EmbeddingError as exc:
            diagnostics = _safe_embedding_diagnostics(exc)
            retryable = (
                bool(exc.retryable) if exc.retryable is not None else True
            )
            rewrap_error = DashScopeArticleRagEmbeddingProviderError(
                "DashScope embedding call failed via bailian_embedding "
                f"(input_count={len(texts)}, "
                f"wrapper_exc={type(exc).__name__}); safe diagnostics "
                "in `.diagnostics`",
                retryable=retryable,
                failure_class="embedding",
                failure_code="embedding_backend_failed",
                diagnostics=diagnostics,
                embedding_usage_report=self._report_from_wrapper_error(
                    exc, effective_model=effective_model
                ),
            )

        if rewrap_error is not None:
            # Raised OUTSIDE the except block: no __cause__, no __context__.
            raise rewrap_error

        assert call_result is not None  # narrow type for type checkers

        # Build the FULL usage report BEFORE local validation so any
        # coverage/dimension failure below can carry provider_succeeded=
        # True with complete provider-side usage.
        usage_report = self._report_from_success(
            call_result, effective_model=effective_model
        )

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
                embedding_usage_report=usage_report,
            )

        results: list[ArticleRagEmbedding] = []
        for text, vector in zip(texts, embeddings, strict=True):
            vec_tuple = tuple(float(v) for v in vector)
            if resolved_dim and len(vec_tuple) != resolved_dim:
                raise DashScopeArticleRagEmbeddingProviderError(
                    "DashScope embedding returned a vector of unexpected "
                    f"length for a {resolved_dim}-dim config",
                    retryable=False,
                    failure_class="embedding_coverage",
                    failure_code="embedding_dimension_mismatch",
                    embedding_usage_report=usage_report,
                )
            results.append(
                ArticleRagEmbedding(
                    text_sha256=_text_sha256(text),
                    model=str(resolved_model),
                    vector=vec_tuple,
                    dim=len(vec_tuple),
                )
            )
        return ArticleRagEmbeddingInvocationResult(
            embeddings=tuple(results),
            usage_report=usage_report,
        )

    # ------------------------------------------------------------------
    # Legacy retrieval-facing surface (delegates, never re-invokes)
    # ------------------------------------------------------------------

    async def embed_texts(
        self,
        texts: list[str],
        *,
        model: str | None = None,
    ) -> list[ArticleRagEmbedding]:
        """Embed chunk texts while retaining only safe failure diagnostics."""
        result = await self.embed_texts_with_usage(texts, model=model)
        return list(result.embeddings)


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
