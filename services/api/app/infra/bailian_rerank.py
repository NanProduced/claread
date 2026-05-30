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
from app.infra.bailian_usage import provider_metadata_from_response, usage_data_from_response

logger = logging.getLogger(__name__)


class RerankError(Exception):
    """百炼 Rerank 调用失败。"""


@dataclass
class RerankResult:
    """单条 rerank 结果。"""

    index: int
    relevance_score: float
    document: str


@dataclass
class RerankCallResult:
    results: list[RerankResult]
    usage_data: dict
    provider_metadata: dict
    model: str
    input_count: int
    input_chars: int
    top_n: int


@dataclass
class _RerankBatchResult:
    results: list[RerankResult]
    usage_data: dict
    provider_metadata: dict


def _call_rerank_sync(
    query: str,
    documents: list[str],
    top_n: int,
    model: str,
    api_key: str,
) -> _RerankBatchResult:
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

    return _RerankBatchResult(
        results=results,
        usage_data=usage_data_from_response(resp),
        provider_metadata=provider_metadata_from_response(resp),
    )


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

    batch_result = await asyncio.to_thread(
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
        len(batch_result.results),
        model,
    )

    return batch_result.results


async def rerank_with_metadata(
    query: str,
    documents: list[str],
    top_n: int = 5,
    model: str = "qwen3-rerank",
) -> RerankCallResult:
    """对候选文档精排，并返回安全裁剪后的 provider usage metadata。"""
    if not documents:
        return RerankCallResult(
            results=[],
            usage_data={
                "provider_usage_available": False,
                "aggregate": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                },
            },
            provider_metadata={"provider_usage_available": False},
            model=model,
            input_count=0,
            input_chars=len(query or ""),
            top_n=0,
        )

    settings = get_settings()
    api_key = settings.bailian_api_key
    if not api_key:
        raise RerankError("BAILIAN_API_KEY is not configured")

    actual_top_n = min(top_n, len(documents))
    batch_result = await asyncio.to_thread(
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
        len(batch_result.results),
        model,
    )

    return RerankCallResult(
        results=batch_result.results,
        usage_data=batch_result.usage_data,
        provider_metadata=batch_result.provider_metadata,
        model=model,
        input_count=len(documents),
        input_chars=len(query or "") + sum(len(document or "") for document in documents),
        top_n=actual_top_n,
    )
