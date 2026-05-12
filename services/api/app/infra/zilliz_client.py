"""Zilliz (Milvus) 向量数据库客户端封装。

参考 database/connection.py 的全局池模式：
- 应用启动时初始化连接
- 提供 readiness check
- 封装 search / insert / query / create_collection 操作

所有方法在 _client 为 None 时返回空结果而非抛异常，
保证 grammar_rag_service 的 fallback 链路安全。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from pymilvus import CollectionSchema, DataType, FieldSchema, MilvusClient

logger = logging.getLogger(__name__)

_client: MilvusClient | None = None


@dataclass
class SearchResult:
    """Zilliz ANN 搜索结果。"""

    id: str
    score: float
    entity: dict[str, Any]


async def init_zilliz(uri: str, token: str) -> None:
    """初始化 Zilliz 客户端连接。

    Args:
        uri: Zilliz Cloud URI（如 https://xxx.api.region.zillizcloud.com）
        token: Zilliz Cloud API token
    """
    global _client
    if not uri or not token:
        logger.warning("Zilliz URI or token is empty, skipping initialization")
        return

    _client = await asyncio.to_thread(
        MilvusClient,
        uri=uri,
        token=token,
    )
    logger.info("Zilliz client initialized (uri=%s)", uri[:30] + "...")


async def close_zilliz() -> None:
    """关闭 Zilliz 客户端连接。"""
    global _client
    if _client is not None:
        try:
            await asyncio.to_thread(_client.close)
        except Exception as e:
            logger.warning("Error closing Zilliz client: %s", e)
        _client = None
        logger.info("Zilliz client closed")


async def is_zilliz_ready() -> bool:
    """检查 Zilliz 是否可用。

    Returns:
        True 表示可用，False 表示不可用或未初始化
    """
    if _client is None:
        return False
    try:
        await asyncio.to_thread(_client.list_collections)
        return True
    except Exception as e:
        logger.warning("Zilliz readiness check failed: %s", e)
        return False


async def zilliz_search(
    collection_name: str,
    query_vector: list[float],
    top_k: int = 8,
    filter_expr: str = "",
    output_fields: list[str] | None = None,
) -> list[SearchResult]:
    """ANN 向量搜索。

    Args:
        collection_name: 目标 collection
        query_vector: 查询向量
        top_k: 返回前 K 个结果
        filter_expr: 标量过滤表达式
        output_fields: 需要返回的字段列表

    Returns:
        SearchResult 列表（含 id/score/entity）。未初始化时返回空列表。
    """
    if _client is None:
        return []

    try:
        results = await asyncio.to_thread(
            _client.search,
            collection_name=collection_name,
            data=[query_vector],
            limit=top_k,
            filter=filter_expr or None,
            output_fields=output_fields,
        )
        if not results or not results[0]:
            return []
        parsed: list[SearchResult] = []
        for hit in results[0]:
            if isinstance(hit, dict):
                hit_id = str(
                    hit.get("id")
                    or hit.get("example_id")
                    or hit.get("entity", {}).get("example_id", "")
                )
                distance = hit.get("distance", 1.0)
                entity = hit.get("entity", {})
            else:
                hit_id = str(
                    getattr(hit, "id", None)
                    or getattr(hit, "example_id", None)
                    or getattr(hit, "entity", {}).get("example_id", "")
                )
                distance = getattr(hit, "distance", 1.0)
                entity = getattr(hit, "entity", {})
            score = max(0.0, 1.0 - abs(distance))
            parsed.append(
                SearchResult(id=hit_id, score=score, entity=entity)
            )
        return parsed
    except Exception as e:
        logger.warning("Zilliz search failed: %s", e)
        return []


async def zilliz_insert(
    collection_name: str,
    data: list[dict[str, Any]],
) -> None:
    """批量插入数据。

    Args:
        collection_name: 目标 collection
        data: 要插入的数据列表，每条为 dict
    """
    if _client is None:
        logger.warning("Zilliz client not initialized, skipping insert")
        return

    try:
        await asyncio.to_thread(
            _client.insert,
            collection_name=collection_name,
            data=data,
        )
        logger.info("Inserted %d records into %s", len(data), collection_name)
    except Exception as e:
        logger.warning("Zilliz insert failed: %s", e)
        raise


async def zilliz_query(
    collection_name: str,
    filter_expr: str,
    output_fields: list[str] | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """标量查询（非向量搜索）。

    Args:
        collection_name: 目标 collection
        filter_expr: 过滤表达式
        output_fields: 需要返回的字段列表
        limit: 返回上限

    Returns:
        查询结果列表。未初始化时返回空列表。
    """
    if _client is None:
        return []

    try:
        results = await asyncio.to_thread(
            _client.query,
            collection_name=collection_name,
            filter=filter_expr,
            output_fields=output_fields,
            limit=limit,
        )
        return results or []
    except Exception as e:
        logger.warning("Zilliz query failed: %s", e)
        return []


async def zilliz_create_collection(
    name: str,
    dimension: int = 1024,
) -> None:
    """创建 collection 并建立 index。

    按 grammar-rag-design.md §16 的 schema 建议：
    - example_id: VARCHAR 主键
    - vector: FLOAT_VECTOR(dimension)
    - reading_variant: VARCHAR
    - output_type: VARCHAR
    - grammar_tags: VARCHAR (JSON 序列化)
    - structure_signals: VARCHAR (JSON 序列化)
    - label: VARCHAR
    - source_sentence: VARCHAR
    - output_fragment: VARCHAR
    - grammar_granularity: VARCHAR
    - quality_score: FLOAT
    - approved: BOOL

    Index: AUTOINDEX on vector field (COSINE)

    Args:
        name: collection 名称
        dimension: 向量维度
    """
    if _client is None:
        logger.warning("Zilliz client not initialized, skipping collection creation")
        return

    existing = await asyncio.to_thread(_client.list_collections)
    if name in existing:
        logger.info("Collection %s already exists, skipping creation", name)
        return

    schema = CollectionSchema(
        fields=[
            FieldSchema(
                name="example_id",
                dtype=DataType.VARCHAR,
                is_primary=True,
                max_length=128,
            ),
            FieldSchema(
                name="vector",
                dtype=DataType.FLOAT_VECTOR,
                dim=dimension,
            ),
            FieldSchema(
                name="reading_variant",
                dtype=DataType.VARCHAR,
                max_length=64,
            ),
            FieldSchema(
                name="output_type",
                dtype=DataType.VARCHAR,
                max_length=32,
            ),
            FieldSchema(
                name="grammar_tags",
                dtype=DataType.VARCHAR,
                max_length=512,
            ),
            FieldSchema(
                name="structure_signals",
                dtype=DataType.VARCHAR,
                max_length=512,
            ),
            FieldSchema(
                name="label",
                dtype=DataType.VARCHAR,
                max_length=256,
            ),
            FieldSchema(
                name="source_sentence",
                dtype=DataType.VARCHAR,
                max_length=2048,
            ),
            FieldSchema(
                name="output_fragment",
                dtype=DataType.VARCHAR,
                max_length=8192,
            ),
            FieldSchema(
                name="grammar_granularity",
                dtype=DataType.VARCHAR,
                max_length=64,
            ),
            FieldSchema(
                name="quality_score",
                dtype=DataType.FLOAT,
            ),
            FieldSchema(
                name="approved",
                dtype=DataType.BOOL,
            ),
        ],
    )

    index_params = _client.prepare_index_params()
    index_params.add_index(
        field_name="vector",
        index_type="AUTOINDEX",
        metric_type="COSINE",
    )

    await asyncio.to_thread(
        _client.create_collection,
        collection_name=name,
        schema=schema,
        index_params=index_params,
    )
    logger.info("Created collection %s (dimension=%d)", name, dimension)
