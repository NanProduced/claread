"""DashScope Embedding 客户端封装。

使用 dashscope SDK 调用 text-embedding 模型。
同步接口 + asyncio.to_thread() 包装为异步。

dashscope TextEmbedding 单次最多 25 条输入，
超过时自动分批调用。

模型选择走统一 registry（rag_embedding route），
通过 ``resolve_embedding_config`` 解析 provider/model/profile。
只有当 registry 未配置 rag_embedding route 时，才会回退到
deprecated 的 ``settings.bailian_*`` 旧字段以保持向后兼容。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import dashscope

from app.config.settings import get_settings
from app.infra.bailian_usage import (
    combine_usage_data,
    provider_metadata_from_response,
    usage_data_from_response,
)

logger = logging.getLogger(__name__)

_BATCH_SIZE = 25
_LEGACY_FALLBACK_WARNING_EMITTED = False


class EmbeddingError(Exception):
    """Embedding 调用失败。"""


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
        texts: 待 embedding 的文本列表（不超过 25 条）
        model: 模型名称
        dimension: 向量维度
        api_key: API Key

    Returns:
        embedding 向量列表

    Raises:
        EmbeddingError: 调用失败时
    """
    resp = dashscope.TextEmbedding.call(
        model=model,
        input=texts,
        dimension=dimension,
        api_key=api_key,
    )

    if resp.status_code != 200:
        raise EmbeddingError(
            f"Embedding call failed: status={resp.status_code}, "
            f"code={resp.code}, message={resp.message}"
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

    超过 25 条时自动分批调用。

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

    all_embeddings: list[list[float]] = []

    for i in range(0, len(texts), _BATCH_SIZE):
        batch = texts[i : i + _BATCH_SIZE]
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
        (len(texts) + _BATCH_SIZE - 1) // _BATCH_SIZE,
        effective_model,
        effective_dimension,
    )

    return all_embeddings


async def embed_texts_with_metadata(
    texts: list[str],
    model: str | None = None,
    dimension: int | None = None,
) -> EmbeddingCallResult:
    """批量文本 embedding，并返回安全裁剪后的 provider usage metadata。"""
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

    all_embeddings: list[list[float]] = []
    usage_items: list[dict] = []
    batch_metadata: list[dict] = []

    for i in range(0, len(texts), _BATCH_SIZE):
        batch = texts[i : i + _BATCH_SIZE]
        batch_result = await asyncio.to_thread(
            _call_embedding_sync,
            texts=batch,
            model=effective_model,
            dimension=effective_dimension,
            api_key=api_key,
        )
        all_embeddings.extend(batch_result.embeddings)
        usage_items.append(batch_result.usage_data)
        batch_metadata.append(
            {
                **batch_result.provider_metadata,
                "input_count": len(batch),
                "input_chars": sum(len(text or "") for text in batch),
            }
        )

    batch_count = (len(texts) + _BATCH_SIZE - 1) // _BATCH_SIZE
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
