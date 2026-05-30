"""阿里云百炼 Embedding 客户端封装。

使用 dashscope SDK 调用 text-embedding-v4 模型。
同步接口 + asyncio.to_thread() 包装为异步。

dashscope TextEmbedding 单次最多 25 条输入，
超过时自动分批调用。
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


class EmbeddingError(Exception):
    """百炼 Embedding 调用失败。"""


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
        api_key: 百炼 API Key

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
            f"Bailian Embedding call failed: status={resp.status_code}, "
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


async def embed_texts(
    texts: list[str],
    model: str = "text-embedding-v4",
    dimension: int = 1024,
) -> list[list[float]]:
    """批量文本 embedding。

    超过 25 条时自动分批调用。

    Args:
        texts: 待 embedding 的文本列表
        model: 模型名称
        dimension: 向量维度

    Returns:
        embedding 向量列表，与输入顺序一一对应

    Raises:
        EmbeddingError: 调用失败时
    """
    if not texts:
        return []

    settings = get_settings()
    api_key = settings.bailian_api_key
    if not api_key:
        raise EmbeddingError("BAILIAN_API_KEY is not configured")

    all_embeddings: list[list[float]] = []

    for i in range(0, len(texts), _BATCH_SIZE):
        batch = texts[i : i + _BATCH_SIZE]
        batch_result = await asyncio.to_thread(
            _call_embedding_sync,
            texts=batch,
            model=model,
            dimension=dimension,
            api_key=api_key,
        )
        all_embeddings.extend(batch_result.embeddings)

    logger.debug(
        "Embedded %d texts in %d batch(es) (model=%s, dim=%d)",
        len(texts),
        (len(texts) + _BATCH_SIZE - 1) // _BATCH_SIZE,
        model,
        dimension,
    )

    return all_embeddings


async def embed_texts_with_metadata(
    texts: list[str],
    model: str = "text-embedding-v4",
    dimension: int = 1024,
) -> EmbeddingCallResult:
    """批量文本 embedding，并返回安全裁剪后的 provider usage metadata。"""
    if not texts:
        return EmbeddingCallResult(
            embeddings=[],
            usage_data=combine_usage_data([]),
            provider_metadata={"provider_usage_available": False, "batches": []},
            model=model,
            dimension=dimension,
            input_count=0,
            input_chars=0,
            batch_count=0,
        )

    settings = get_settings()
    api_key = settings.bailian_api_key
    if not api_key:
        raise EmbeddingError("BAILIAN_API_KEY is not configured")

    all_embeddings: list[list[float]] = []
    usage_items: list[dict] = []
    batch_metadata: list[dict] = []

    for i in range(0, len(texts), _BATCH_SIZE):
        batch = texts[i : i + _BATCH_SIZE]
        batch_result = await asyncio.to_thread(
            _call_embedding_sync,
            texts=batch,
            model=model,
            dimension=dimension,
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
        model,
        dimension,
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
        model=model,
        dimension=dimension,
        input_count=len(texts),
        input_chars=sum(len(text or "") for text in texts),
        batch_count=batch_count,
    )


async def embed_single(
    text: str,
    model: str = "text-embedding-v4",
    dimension: int = 1024,
) -> list[float]:
    """单条文本 embedding。

    Args:
        text: 待 embedding 的文本
        model: 模型名称
        dimension: 向量维度

    Returns:
        单条 embedding 向量

    Raises:
        EmbeddingError: 调用失败时
    """
    results = await embed_texts([text], model=model, dimension=dimension)
    return results[0]


async def embed_single_with_metadata(
    text: str,
    model: str = "text-embedding-v4",
    dimension: int = 1024,
) -> EmbeddingCallResult:
    """单条文本 embedding，并返回安全裁剪后的 provider usage metadata。"""
    return await embed_texts_with_metadata([text], model=model, dimension=dimension)
