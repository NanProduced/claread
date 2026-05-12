"""阿里云百炼 Rerank 客户端封装。

使用 dashscope SDK 调用 qwen3-rerank 模型。
同步接口 + asyncio.to_thread() 包装为异步。

按 grammar-rag-design.md §12：
- rerank 的文档输入为候选样本的简化文本
- 返回按 relevance_score 降序排列的结果
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import dashscope

from app.config.settings import get_settings

logger = logging.getLogger(__name__)


class RerankError(Exception):
    """百炼 Rerank 调用失败。"""


@dataclass
class RerankResult:
    """单条 rerank 结果。"""

    index: int
    relevance_score: float
    document: str


def _call_rerank_sync(
    query: str,
    documents: list[str],
    top_n: int,
    model: str,
    api_key: str,
) -> list[RerankResult]:
    """同步调用 dashscope Rerank。

    Args:
        query: 查询文本
        documents: 候选文档列表
        top_n: 返回前 N 个结果
        model: 模型名称
        api_key: 百炼 API Key

    Returns:
        按 relevance_score 降序排列的 RerankResult 列表

    Raises:
        RerankError: 调用失败时
    """
    resp = dashscope.TextReRank.call(
        model=model,
        query=query,
        documents=documents,
        top_n=top_n,
        return_documents=True,
        api_key=api_key,
    )

    if resp.status_code != 200:
        raise RerankError(
            f"Bailian Rerank call failed: status={resp.status_code}, "
            f"code={resp.code}, message={resp.message}"
        )

    results: list[RerankResult] = []
    for item in resp.output["results"]:
        results.append(
            RerankResult(
                index=item["index"],
                relevance_score=item["relevance_score"],
                document=item.get("document", {}).get("text", ""),
            )
        )

    return results


async def rerank(
    query: str,
    documents: list[str],
    top_n: int = 5,
    model: str = "qwen3-rerank",
) -> list[RerankResult]:
    """对候选文档精排。

    Args:
        query: 查询文本
        documents: 候选文档列表
        top_n: 返回前 N 个结果
        model: 模型名称

    Returns:
        按 relevance_score 降序排列的 RerankResult 列表

    Raises:
        RerankError: 调用失败时
    """
    if not documents:
        return []

    settings = get_settings()
    api_key = settings.bailian_api_key
    if not api_key:
        raise RerankError("BAILIAN_API_KEY is not configured")

    actual_top_n = min(top_n, len(documents))

    results = await asyncio.to_thread(
        _call_rerank_sync,
        query=query,
        documents=documents,
        top_n=actual_top_n,
        model=model,
        api_key=api_key,
    )

    logger.debug(
        "Reranked %d documents, returned top %d (model=%s)",
        len(documents),
        len(results),
        model,
    )

    return results
