"""DashScope Rerank 客户端封装。

使用 dashscope SDK 调用 rerank 模型。
同步接口 + asyncio.to_thread() 包装为异步。

按 grammar-rag-design.md §12：
- rerank 的文档输入为候选样本的简化文本
- 返回按 relevance_score 降序排列的结果

模型选择走统一 registry（rag_rerank route），
通过 ``resolve_rerank_config`` 解析 provider/model/profile。
当 registry 未配置 rag_rerank route 时，回退到
``settings.bailian_*`` 旧字段以保持向后兼容。
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
    """Rerank 调用失败。"""


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
        api_key: API Key

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
            f"Rerank call failed: status={resp.status_code}, "
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


def resolve_rerank_config() -> tuple[str, str]:
    """Resolve rerank model/api_key from the unified registry.

    Returns:
        (model_name, api_key)

    Resolution order:
      1. If the registry has a ``rag_rerank`` route default that resolves
         to a ``dashscope_rerank`` adapter, use it.
      2. Fall back to ``settings.bailian_*`` legacy fields.
    """
    from app.llm.provider_factory import ResolvedRerankConfig, build_model_instance
    from app.llm.router import resolve_model_config
    from app.llm.routes import MODEL_ROUTE_RAG_RERANK

    settings = get_settings()
    config = resolve_model_config(settings, MODEL_ROUTE_RAG_RERANK)
    if config is not None and config.adapter == "dashscope_rerank":
        built = build_model_instance(config)
        if isinstance(built, ResolvedRerankConfig):
            return built.model_name, built.api_key

    # Legacy fallback
    api_key = settings.bailian_api_key
    return settings.bailian_rerank_model, api_key


async def rerank(
    query: str,
    documents: list[str],
    top_n: int = 5,
    model: str | None = None,
) -> list[RerankResult]:
    """对候选文档精排。

    Args:
        query: 查询文本
        documents: 候选文档列表
        top_n: 返回前 N 个结果
        model: 模型名称（None 时走 registry 解析）

    Returns:
        按 relevance_score 降序排列的 RerankResult 列表

    Raises:
        RerankError: 调用失败时
    """
    if not documents:
        return []

    resolved_model, api_key = resolve_rerank_config()
    effective_model = model or resolved_model
    if not api_key:
        raise RerankError("No API key configured for rerank (registry or BAILIAN_API_KEY)")

    actual_top_n = min(top_n, len(documents))

    batch_result = await asyncio.to_thread(
        _call_rerank_sync,
        query=query,
        documents=documents,
        top_n=actual_top_n,
        model=effective_model,
        api_key=api_key,
    )

    logger.debug(
        "Reranked %d documents, returned top %d (model=%s)",
        len(documents),
        len(batch_result.results),
        effective_model,
    )

    return batch_result.results


async def rerank_with_metadata(
    query: str,
    documents: list[str],
    top_n: int = 5,
    model: str | None = None,
) -> RerankCallResult:
    """对候选文档精排，并返回安全裁剪后的 provider usage metadata。"""
    if not documents:
        effective_model = model or "qwen3-rerank"
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
            model=effective_model,
            input_count=0,
            input_chars=len(query or ""),
            top_n=0,
        )

    resolved_model, api_key = resolve_rerank_config()
    effective_model = model or resolved_model
    if not api_key:
        raise RerankError("No API key configured for rerank (registry or BAILIAN_API_KEY)")

    actual_top_n = min(top_n, len(documents))
    batch_result = await asyncio.to_thread(
        _call_rerank_sync,
        query=query,
        documents=documents,
        top_n=actual_top_n,
        model=effective_model,
        api_key=api_key,
    )

    logger.debug(
        "Reranked %d documents, returned top %d (model=%s)",
        len(documents),
        len(batch_result.results),
        effective_model,
    )

    return RerankCallResult(
        results=batch_result.results,
        usage_data=batch_result.usage_data,
        provider_metadata=batch_result.provider_metadata,
        model=effective_model,
        input_count=len(documents),
        input_chars=len(query or "") + sum(len(document or "") for document in documents),
        top_n=actual_top_n,
    )
