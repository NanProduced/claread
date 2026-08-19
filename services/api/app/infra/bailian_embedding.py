"""DashScope Embedding 客户端封装。

使用 dashscope SDK 调用 text-embedding 模型。
同步接口 + asyncio.to_thread() 包装为异步。

单批最大输入条数按 effective model 从不可变 capability registry 解析：
``text-embedding-v3`` / ``text-embedding-v4`` 均为 10 条/批，
未注册的 model 在任何 outbound 调用前 fail-closed。
不重新引入全局 batch magic number（早期版本硬编码 25 条已被移除）。

模型选择走统一 registry（rag_embedding route），
通过 ``resolve_embedding_config`` 解析 provider/model/profile。
只有当 registry 未配置 rag_embedding route 时，才会回退到
deprecated 的 ``settings.bailian_*`` 旧字段以保持向后兼容。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

import dashscope

from app.config.settings import get_settings
from app.infra.bailian_usage import (
    canonical_embedding_tokens,
    combine_usage_data,
    provider_metadata_from_response,
    usage_data_from_response,
)

logger = logging.getLogger(__name__)

_LEGACY_FALLBACK_WARNING_EMITTED = False


@dataclass(frozen=True)
class _ModelCapability:
    max_items: int


_EMBEDDING_CAPABILITY_REGISTRY: Mapping[str, _ModelCapability] = MappingProxyType(
    {
        "text-embedding-v3": _ModelCapability(max_items=10),
        "text-embedding-v4": _ModelCapability(max_items=10),
    }
)


def _resolve_embedding_capability(effective_model: str) -> _ModelCapability:
    """Resolve the immutable capability for the effective model.

    Raises EmbeddingError(retryable=False) for an unregistered model. This
    MUST happen before any outbound provider call. The error message is a
    fixed, safe local diagnostic; it contains no API key, input text,
    endpoint URI, raw configuration content, SDK object, or the effective
    model value itself (the model is caller-supplied and may carry
    hostile content).
    """
    capability = _EMBEDDING_CAPABILITY_REGISTRY.get(effective_model)
    if capability is None:
        raise EmbeddingError(
            "embedding model capability is not registered; cannot determine safe batch size",
            retryable=False,
        )
    return capability


class EmbeddingError(Exception):
    """Embedding failure with a deliberately small safe diagnostic envelope.

    OBS-01B-B partial-usage fields (all optional, backward compatible):

    - ``provider_call_attempted``: True once a non-empty input reached the
      outbound batch loop (any batch actually attempted). Config / API key
      / capability preflight failures keep the default False.
    - ``completed_batch_count``: number of batches that returned a success
      response before the failing batch (0 when the first batch failed).
    - ``usage_data``: ``combine_usage_data`` aggregate over the completed
      batches ONLY (zeros when none completed). Never a raw provider dict.
    - ``provider_metadata``: per-batch summaries (request_id, input_count,
      canonical provider_usage_available / total_tokens) for the completed
      batches. ``input_chars`` stays as an in-memory diagnostic only.
    - ``model``: the actual ``effective_model`` used for the outbound call.

    None of these fields ever carry response bodies, embeddings, chunk
    text, API keys or raw exception strings.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        provider_code: str | None = None,
        retryable: bool | None = None,
        failed_batch_ordinal: int | None = None,
        batch_count: int | None = None,
        usage_data: dict[str, Any] | None = None,
        provider_metadata: dict[str, Any] | None = None,
        model: str | None = None,
        completed_batch_count: int = 0,
        provider_call_attempted: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.provider_code = provider_code
        self.retryable = retryable
        self.failed_batch_ordinal = failed_batch_ordinal
        self.batch_count = batch_count
        self.usage_data = usage_data
        self.provider_metadata = provider_metadata
        self.model = model
        self.completed_batch_count = completed_batch_count
        self.provider_call_attempted = provider_call_attempted


def _safe_provider_status(value: object) -> int | None:
    """Coerce a provider status to ``int`` in the HTTP-style 100–599 range.

    Anything outside that range (including non-int values, 0, 99, 600+)
    is returned as ``None``.  ``None`` status leaves the existing
    retryability fallback in effect (``status_code is None or
    status_code == 429 or status_code >= 500`` -> retryable=True).
    """
    if isinstance(value, bool):
        # bool is a subclass of int; reject it explicitly so True/False
        # cannot masquerade as 1/0 status codes.
        return None
    if isinstance(value, int) and 100 <= value <= 599:
        return value
    return None


# Explicit allowlist of DashScope provider codes that the
# wrapper may surface in ``EmbeddingError.provider_code``.  Anything not
# in this set is dropped to ``None`` — never echoed, truncated, or
# logged.  This replaces the legacy ``isalnum()`` + length check, which
# accepted both key-like strings (``sk-1234567890abcdef``) and arbitrary
# Unicode alphanumeric strings (``密钥123``) because Python's
# ``str.isalnum()`` returns True for CJK characters.
#
# Add new codes here ONLY after confirming they appear in the official
# DashScope error-code reference.  Unknown codes fail-closed to None.
#
# Closure cleanup removed ``InternalServerError`` and
# ``ServiceUnavailable`` because no first-party DashScope
# TextEmbedding endpoint documentation link could be provided to
# confirm them as current Model Studio codes.  They can be re-added
# when an official reference is supplied.
_SAFE_PROVIDER_CODES: frozenset[str] = frozenset(
    {
        "InvalidParameter",
        # ``Throttling.User`` is retained as a LEGACY COMPATIBILITY
        # code: existing Article RAG provider/worker tests
        # (test_article_rag_index_worker,
        # test_article_rag_provider_adapters) rely on it as the
        # canonical 429 retryable code.  It is NOT confirmed as a current official
        # Model Studio code — do not cite it as such.  Replace with
        # the official code once a first-party reference is supplied.
        "Throttling.User",
        "Throttling",
        "AccessDenied",
        "InvalidApiKey",
        "ModelNotFound",
    }
)


def _safe_provider_code(value: object) -> str | None:
    """Return ``value`` only if it is in the explicit DashScope allowlist.

    Uses a fixed, known-safe set of provider codes (see
    ``_SAFE_PROVIDER_CODES``).  Unknown values — including key-like
    strings, arbitrary Unicode, and any future unmapped code — are
    fail-closed to ``None``.  The original value is never echoed,
    truncated, or logged.
    """
    if not isinstance(value, str) or not value:
        return None
    if value in _SAFE_PROVIDER_CODES:
        return value
    return None


@dataclass
class EmbeddingCallResult:
    embeddings: list[list[float]]
    usage_data: dict
    provider_metadata: dict
    model: str
    dimension: int
    input_count: int
    input_chars: int
    batch_count: int


@dataclass
class _EmbeddingBatchResult:
    embeddings: list[list[float]]
    usage_data: dict
    provider_metadata: dict


@dataclass
class _ResolvedEmbeddingRuntimeConfig:
    model_name: str
    dimension: int
    api_key: str


def _call_embedding_sync(
    texts: list[str],
    model: str,
    dimension: int,
    api_key: str,
) -> _EmbeddingBatchResult:
    """同步调用 dashscope TextEmbedding。

    Args:
        texts: 待 embedding 的文本列表（不超过当前 capability 的 max_items 条；
            v3/v4 均为 10 条/批；未注册 model 在调用前 fail-closed）
        model: 模型名称
        dimension: 向量维度
        api_key: API Key

    Returns:
        embedding 向量列表

    Raises:
        EmbeddingError: 调用失败时
    """
    # ``dashscope.TextEmbedding.call`` can raise
    # an ordinary exception BEFORE returning a response object — e.g.
    # on transport, auth, or serialisation failures that surface as a
    # plain ``RuntimeError`` (or other ``Exception`` subclass) whose
    # message may carry sensitive content (API key, chunk text, URI,
    # raw upstream error).  We catch ``Exception`` (NOT
    # ``BaseException`` — KeyboardInterrupt/SystemExit must still
    # propagate), discard the original exception without copying its
    # message / type name / repr / args / SDK object, and raise a
    # fixed safe ``EmbeddingError`` OUTSIDE the except block so both
    # ``__cause__`` and ``__context__`` remain None.
    sdk_error: EmbeddingError | None = None
    try:
        resp = dashscope.TextEmbedding.call(
            model=model,
            input=texts,
            dimension=dimension,
            api_key=api_key,
        )
    except Exception:
        # No field of the original exception is copied, persisted, or
        # interpolated.  ``retryable=True`` because the SDK never
        # returned a response, so the existing safe retryability
        # fallback (``status_code is None``) applies.  ``status_code``
        # and ``provider_code`` are left None — the outer loop will
        # still populate ``failed_batch_ordinal`` and ``batch_count``.
        sdk_error = EmbeddingError(
            "embedding provider call failed before a response was available",
            status_code=None,
            provider_code=None,
            retryable=True,
        )

    if sdk_error is not None:
        # Raised OUTSIDE the except block: no ``__cause__``, no
        # ``__context__``, no ``raise ... from exc`` / ``from None``.
        raise sdk_error

    if resp.status_code != 200:
        status_code = _safe_provider_status(resp.status_code)
        provider_code = _safe_provider_code(resp.code)
        retryable = status_code is None or status_code == 429 or status_code >= 500
        raise EmbeddingError(
            "embedding provider returned a non-success response",
            status_code=status_code,
            provider_code=provider_code,
            retryable=retryable,
        )

    embeddings: list[list[float]] = []
    for item in resp.output["embeddings"]:
        embeddings.append(item["embedding"])

    return _EmbeddingBatchResult(
        embeddings=embeddings,
        usage_data=usage_data_from_response(resp),
        provider_metadata=provider_metadata_from_response(resp),
    )


def resolve_embedding_config() -> tuple[str, int, str]:
    """Resolve embedding model/dimension/api_key from the unified registry.

    Returns:
        (model_name, dimension, api_key)

    Resolution order:
      1. If the registry has a ``rag_embedding`` route default that resolves
         to a ``dashscope_embedding`` adapter, use it.
      2. Fall back to ``settings.bailian_*`` legacy fields only when the
         route default is unset.
    """
    resolved = _resolve_embedding_runtime_config()
    return resolved.model_name, resolved.dimension, resolved.api_key


def _resolve_embedding_runtime_config() -> _ResolvedEmbeddingRuntimeConfig:
    """Resolve the effective embedding runtime config.

    Legacy fallback is only allowed when the ``rag_embedding`` route default is
    entirely unset. If a route exists but points to an incompatible adapter, we
    fail fast so misconfiguration cannot be silently masked by old
    ``BAILIAN_*`` fields.
    """
    from app.llm.provider_factory import ResolvedEmbeddingConfig, build_model_instance
    from app.llm.router import resolve_model_config
    from app.llm.routes import MODEL_ROUTE_RAG_EMBEDDING

    settings = get_settings()
    config = resolve_model_config(settings, MODEL_ROUTE_RAG_EMBEDDING)
    if config is None:
        _warn_legacy_embedding_fallback_once()
        return _ResolvedEmbeddingRuntimeConfig(
            model_name=settings.bailian_embedding_model,
            dimension=settings.bailian_embedding_dimension,
            api_key=settings.bailian_api_key,
        )

    if config.adapter != "dashscope_embedding":
        raise EmbeddingError(
            "RAG_EMBEDDING_MODEL_PROFILE resolved to incompatible adapter "
            f"{config.adapter!r}; expected 'dashscope_embedding'."
        )

    built = build_model_instance(config)
    if not isinstance(built, ResolvedEmbeddingConfig):
        raise EmbeddingError(
            "rag_embedding route resolved, but provider builder did not return "
            "a ResolvedEmbeddingConfig."
        )

    return _ResolvedEmbeddingRuntimeConfig(
        model_name=built.model_name,
        dimension=built.dimension,
        api_key=built.api_key,
    )


def _warn_legacy_embedding_fallback_once() -> None:
    global _LEGACY_FALLBACK_WARNING_EMITTED
    if _LEGACY_FALLBACK_WARNING_EMITTED:
        return
    logger.warning(
        "rag_embedding route is unset; falling back to deprecated BAILIAN_* "
        "embedding settings. Configure RAG_EMBEDDING_MODEL_PROFILE to use the "
        "registry path."
    )
    _LEGACY_FALLBACK_WARNING_EMITTED = True


async def embed_texts(
    texts: list[str],
    model: str | None = None,
    dimension: int | None = None,
) -> list[list[float]]:
    """批量文本 embedding。

    超过当前 capability ``max_items`` 条时自动分批调用：
    ``text-embedding-v3`` / ``text-embedding-v4`` 均为 10 条/批；
    未注册 model 在任何 outbound 调用前 fail-closed。

    Args:
        texts: 待 embedding 的文本列表
        model: 模型名称（None 时走 registry 解析）
        dimension: 向量维度（None 时走 registry 解析）

    Returns:
        embedding 向量列表，与输入顺序一一对应

    Raises:
        EmbeddingError: 调用失败时
    """
    if not texts:
        return []

    resolved_model, resolved_dimension, api_key = resolve_embedding_config()
    effective_model = model or resolved_model
    effective_dimension = dimension or resolved_dimension
    if not api_key:
        raise EmbeddingError("No API key configured for embedding (registry or BAILIAN_API_KEY)")

    capability = _resolve_embedding_capability(effective_model)

    all_embeddings: list[list[float]] = []

    for i in range(0, len(texts), capability.max_items):
        batch = texts[i : i + capability.max_items]
        batch_result = await asyncio.to_thread(
            _call_embedding_sync,
            texts=batch,
            model=effective_model,
            dimension=effective_dimension,
            api_key=api_key,
        )
        all_embeddings.extend(batch_result.embeddings)

    logger.debug(
        "Embedded %d texts in %d batch(es) (model=%s, dim=%d)",
        len(texts),
        (len(texts) + capability.max_items - 1) // capability.max_items,
        effective_model,
        effective_dimension,
    )

    return all_embeddings


async def embed_texts_with_metadata(
    texts: list[str],
    model: str | None = None,
    dimension: int | None = None,
) -> EmbeddingCallResult:
    """批量文本 embedding，并返回安全裁剪后的 provider usage metadata。

    OBS-01B-B：多批次部分失败时，已完成批次的 aggregate usage 与逐批
    canonical 摘要会附加到 re-raise 的同一个 ``EmbeddingError`` 上
    （``usage_data`` / ``provider_metadata`` / ``model`` /
    ``completed_batch_count`` / ``provider_call_attempted``），供上层
    adapter 构造 partial usage report。config / API key / capability
    等 preflight 失败不携带任何 usage（未发生 outbound 调用）。
    """
    resolved_model, resolved_dimension, api_key = resolve_embedding_config()
    effective_model = model or resolved_model
    effective_dimension = dimension or resolved_dimension
    if not texts:
        return EmbeddingCallResult(
            embeddings=[],
            usage_data=combine_usage_data([]),
            provider_metadata={"provider_usage_available": False, "batches": []},
            model=effective_model,
            dimension=effective_dimension,
            input_count=0,
            input_chars=0,
            batch_count=0,
        )

    if not api_key:
        raise EmbeddingError("No API key configured for embedding (registry or BAILIAN_API_KEY)")

    capability = _resolve_embedding_capability(effective_model)

    all_embeddings: list[list[float]] = []
    usage_items: list[dict] = []
    batch_metadata: list[dict] = []

    # A non-empty input has reached the outbound batch loop: every call
    # from here on is provider-attempted (even if the first batch fails).
    provider_call_attempted = True
    batch_count = (len(texts) + capability.max_items - 1) // capability.max_items

    for i in range(0, len(texts), capability.max_items):
        batch = texts[i : i + capability.max_items]
        try:
            batch_result = await asyncio.to_thread(
                _call_embedding_sync,
                texts=batch,
                model=effective_model,
                dimension=effective_dimension,
                api_key=api_key,
            )
        except EmbeddingError as exc:
            exc.failed_batch_ordinal = (i // capability.max_items) + 1
            exc.batch_count = batch_count
            exc.completed_batch_count = len(batch_metadata)
            exc.provider_call_attempted = provider_call_attempted
            # Aggregate + per-batch summaries over the COMPLETED batches
            # only (zeros when none completed). Safe shapes only — no raw
            # provider dict, response body, or exception payload.
            exc.usage_data = combine_usage_data(usage_items)
            exc.provider_metadata = {
                "provider_usage_available": any(
                    item.get("provider_usage_available") for item in usage_items
                ),
                "batches": list(batch_metadata),
            }
            exc.model = effective_model
            raise
        all_embeddings.extend(batch_result.embeddings)
        usage_items.append(batch_result.usage_data)
        canonical = canonical_embedding_tokens(batch_result.usage_data)
        batch_metadata.append(
            {
                **batch_result.provider_metadata,
                "input_count": len(batch),
                # In-memory diagnostic only; downstream durable reports
                # must not carry input_chars.
                "input_chars": sum(len(text or "") for text in batch),
                # Canonical embedding-boundary fields (OBS-01B-B).
                "provider_usage_available": canonical["provider_usage_available"],
                "total_tokens": canonical["aggregate"]["total_tokens"],
            }
        )

    logger.debug(
        "Embedded %d texts in %d batch(es) (model=%s, dim=%d)",
        len(texts),
        batch_count,
        effective_model,
        effective_dimension,
    )

    return EmbeddingCallResult(
        embeddings=all_embeddings,
        usage_data=combine_usage_data(usage_items),
        provider_metadata={
            "provider_usage_available": any(
                item.get("provider_usage_available") for item in usage_items
            ),
            "batches": batch_metadata,
        },
        model=effective_model,
        dimension=effective_dimension,
        input_count=len(texts),
        input_chars=sum(len(text or "") for text in texts),
        batch_count=batch_count,
    )


async def embed_single(
    text: str,
    model: str | None = None,
    dimension: int | None = None,
) -> list[float]:
    """单条文本 embedding。

    Args:
        text: 待 embedding 的文本
        model: 模型名称（None 时走 registry 解析）
        dimension: 向量维度（None 时走 registry 解析）

    Returns:
        单条 embedding 向量

    Raises:
        EmbeddingError: 调用失败时
    """
    results = await embed_texts([text], model=model, dimension=dimension)
    return results[0]


async def embed_single_with_metadata(
    text: str,
    model: str | None = None,
    dimension: int | None = None,
) -> EmbeddingCallResult:
    """单条文本 embedding，并返回安全裁剪后的 provider usage metadata。"""
    return await embed_texts_with_metadata([text], model=model, dimension=dimension)
