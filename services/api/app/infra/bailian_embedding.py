"""阿里云百炼 Embedding 客户端封装。

使用 dashscope SDK 调用 text-embedding-v4 模型。
同步接口 + asyncio.to_thread() 包装为异步。

dashscope TextEmbedding 单次最多 25 条输入，
超过时自动分批调用。
"""

from __future__ import annotations

import asyncio
import logging

import dashscope

from app.config.settings import get_settings

logger = logging.getLogger(__name__)

_BATCH_SIZE = 25


class EmbeddingError(Exception):
    """百炼 Embedding 调用失败。"""


def _call_embedding_sync(
    texts: list[str],
    model: str,
    dimension: int,
    api_key: str,
) -> list[list[float]]:
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

    return embeddings


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
        batch_embeddings = await asyncio.to_thread(
            _call_embedding_sync,
            texts=batch,
            model=model,
            dimension=dimension,
            api_key=api_key,
        )
        all_embeddings.extend(batch_embeddings)

    logger.debug(
        "Embedded %d texts in %d batch(es) (model=%s, dim=%d)",
        len(texts),
        (len(texts) + _BATCH_SIZE - 1) // _BATCH_SIZE,
        model,
        dimension,
    )

    return all_embeddings


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
