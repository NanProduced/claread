"""Grammar RAG service — 真实检索实现。

按 grammar-rag-design.md 完整实现：
1. select_candidate_sentences → 选出候选句
2. build_query_text → 构造 query_text
3. embed_single → 百炼 Embedding
4. zilliz_search → Zilliz ANN（含 filter）
5. rerank → 百炼 Rerank
6. _apply_confidence_filter → 按 rerank score 过滤
7. 多样性去重 → 按 grammar_tags/label 去重
8. 注入预算控制 → grammar_note 最多 2 条, sentence_analysis 最多 1 条
9. 构造 ExampleEntry 列表

所有外部调用失败时自动 fallback 到 baseline。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from app.config.settings import get_settings
from app.services.analysis.prompting.example_strategy import ExampleEntry
from app.services.analysis.prompting.rag.grammar_retrieval_hints import (
    build_query_text,
    select_candidate_sentences,
)

logger = logging.getLogger(__name__)

_INJECTION_BUDGET = {
    "grammar_note": 2,
    "sentence_analysis": 1,
}


@dataclass
class RAGQueryResult:
    """RAG 查询结果，携带诊断信息。"""

    examples: list[ExampleEntry] = field(default_factory=list)
    selection_mode: str = "rag_fallback"
    fallback_reason: str | None = None
    example_count: int = 0
    query_count: int = 0
    selected_example_ids: list[str] = field(default_factory=list)
    ann_topk: int = 0
    rerank_topn: int = 0
    embedding_latency_ms: float = 0.0
    ann_latency_ms: float = 0.0
    rerank_latency_ms: float = 0.0

    @property
    def is_fallback(self) -> bool:
        return self.selection_mode in ("rag_fallback", "baseline")


@dataclass
class _ScoredCandidate:
    """内部候选，携带 rerank score 和 Zilliz entity。"""

    example_id: str
    score: float
    entity: dict[str, Any]


async def query_grammar_rag(
    variant: str,
    sentences: list[dict],
    output_type: str = "grammar_note",
    top_k: int = 5,
) -> RAGQueryResult:
    """查询 grammar RAG 示例池。

    Args:
        variant: 阅读变体，如 gaokao / cet / kaoyan
        sentences: 输入句子列表 [{"sentence_id": str, "text": str}]
        output_type: grammar_note 或 sentence_analysis
        top_k: 召回上限

    Returns:
        RAGQueryResult，失败时自动 fallback。
    """
    if not sentences:
        logger.info("RAG query skipped: no input sentences")
        return RAGQueryResult(
            fallback_reason="no_input_sentences",
            selection_mode="rag_fallback",
        )

    logger.info(
        "RAG query start: variant=%s, output_type=%s, sentences=%d",
        variant, output_type, len(sentences),
    )
    try:
        result = await _do_rag_query(variant, sentences, output_type, top_k)
        logger.info(
            "RAG query done: mode=%s, examples=%d, ids=%s, "
            "embed=%.0fms, ann=%.0fms, rerank=%.0fms, fallback=%s",
            result.selection_mode,
            result.example_count,
            result.selected_example_ids,
            result.embedding_latency_ms,
            result.ann_latency_ms,
            result.rerank_latency_ms,
            result.fallback_reason or "none",
        )
        return result
    except Exception as exc:
        logger.warning(
            "Grammar RAG retrieval failed, falling back to baseline: %s",
            exc,
            exc_info=True,
        )
        return RAGQueryResult(
            fallback_reason=f"retrieval_error: {type(exc).__name__}: {exc}",
            selection_mode="rag_fallback",
            query_count=len(sentences),
        )


async def _do_rag_query(
    variant: str,
    sentences: list[dict],
    output_type: str,
    top_k: int,
) -> RAGQueryResult:
    """执行完整 RAG 查询链路。"""
    settings = get_settings()
    result = RAGQueryResult(query_count=len(sentences))

    candidates = await _retrieve_from_backend(
        variant=variant,
        sentences=sentences,
        output_type=output_type,
        top_k=top_k,
        result=result,
    )

    if not candidates:
        result.fallback_reason = "empty_candidates"
        result.selection_mode = "rag_fallback"
        return result

    filtered = _apply_confidence_filter(
        candidates,
        min_score=settings.grammar_rag_confidence_threshold,
    )
    if not filtered:
        result.fallback_reason = "low_confidence"
        result.selection_mode = "rag_fallback"
        return result

    deduped = _diversity_dedup(filtered)
    budget = _INJECTION_BUDGET.get(output_type, 2)
    final = deduped[:budget]

    _OUTPUT_TYPE_TO_EXAMPLE_TYPE = {
        "grammar_note": "grammar",
        "sentence_analysis": "sentence_analysis",
    }
    examples = [
        ExampleEntry(
            example_type=_OUTPUT_TYPE_TO_EXAMPLE_TYPE.get(output_type, output_type),
            sentence_text=c.entity.get("source_sentence", ""),
            output_fragment=c.entity.get("output_fragment", ""),
        )
        for c in final
    ]

    result.examples = examples
    result.selection_mode = "rag"
    result.example_count = len(examples)
    result.selected_example_ids = [c.example_id for c in final]

    return result


async def _retrieve_from_backend(
    variant: str,
    sentences: list[dict],
    output_type: str,
    top_k: int,
    result: RAGQueryResult,
) -> list[_ScoredCandidate]:
    """调用外部检索后端（Zilliz + Bailian）。"""
    from app.infra.bailian_embedding import embed_single
    from app.infra.bailian_rerank import rerank
    from app.infra.zilliz_client import zilliz_search

    settings = get_settings()

    candidate_sentences = select_candidate_sentences(
        sentences, output_type=output_type
    )
    if not candidate_sentences:
        return []

    query_sentence = candidate_sentences[0]
    query_text = build_query_text(
        sentence=query_sentence.get("text", ""),
        variant=variant,
        output_type=output_type,
    )

    t0 = time.monotonic()
    query_vector = await embed_single(
        query_text,
        model=settings.bailian_embedding_model,
        dimension=settings.bailian_embedding_dimension,
    )
    result.embedding_latency_ms = (time.monotonic() - t0) * 1000

    collection_name = (
        settings.zilliz_collection_grammar_note
        if output_type == "grammar_note"
        else settings.zilliz_collection_sentence_analysis
    )

    base_filter = f'approved == true and output_type == "{output_type}"'

    t0 = time.monotonic()
    filter_expr = f'{base_filter} and reading_variant == "{variant}"'
    logger.info("RAG ANN search: collection=%s, filter=%s", collection_name, filter_expr)
    search_results = await zilliz_search(
        collection_name=collection_name,
        query_vector=query_vector,
        top_k=settings.grammar_rag_ann_topk,
        filter_expr=filter_expr,
    )

    if not search_results and variant != "default":
        filter_expr = f'{base_filter} and reading_variant == "default"'
        logger.info("RAG ANN fallback to default variant: filter=%s", filter_expr)
        search_results = await zilliz_search(
            collection_name=collection_name,
            query_vector=query_vector,
            top_k=settings.grammar_rag_ann_topk,
            filter_expr=filter_expr,
        )

    result.ann_latency_ms = (time.monotonic() - t0) * 1000
    result.ann_topk = settings.grammar_rag_ann_topk

    if not search_results:
        logger.info("RAG ANN returned 0 results")
        return []

    logger.info("RAG ANN returned %d results, proceeding to rerank", len(search_results))

    rerank_docs = []
    for sr in search_results:
        doc = (
            f"variant={sr.entity.get('reading_variant', '')}\n"
            f"output_type={sr.entity.get('output_type', '')}\n"
            f"grammar_tags={sr.entity.get('grammar_tags', '')}\n"
            f"sentence={sr.entity.get('source_sentence', '')}\n"
            f"label={sr.entity.get('label', '')}"
        )
        rerank_docs.append(doc)

    t0 = time.monotonic()
    rerank_results = await rerank(
        query=query_text,
        documents=rerank_docs,
        top_n=settings.grammar_rag_rerank_topn,
        model=settings.bailian_rerank_model,
    )
    result.rerank_latency_ms = (time.monotonic() - t0) * 1000
    result.rerank_topn = settings.grammar_rag_rerank_topn

    candidates: list[_ScoredCandidate] = []
    for rr in rerank_results:
        original = search_results[rr.index]
        candidates.append(
            _ScoredCandidate(
                example_id=original.id,
                score=rr.relevance_score,
                entity=original.entity,
            )
        )

    return candidates


def _apply_confidence_filter(
    candidates: list[_ScoredCandidate],
    min_score: float = 0.3,
) -> list[_ScoredCandidate]:
    """过滤低置信度候选。"""
    return [c for c in candidates if c.score >= min_score]


def _diversity_dedup(
    candidates: list[_ScoredCandidate],
) -> list[_ScoredCandidate]:
    """多样性去重。

    按 design doc §13.2：
    - 按 label 去重（不选三条都讲同一种语法的）
    - 按 grammar_tags 去重
    - 按 source_sentence 近重复去重
    """
    seen_labels: set[str] = set()
    seen_sentences: set[str] = set()
    seen_tag_sets: set[str] = set()
    result: list[_ScoredCandidate] = []

    for c in candidates:
        label = c.entity.get("label", "")
        sentence = c.entity.get("source_sentence", "")
        tags_str = c.entity.get("grammar_tags", "[]")

        if label in seen_labels:
            continue
        if sentence in seen_sentences:
            continue
        if tags_str in seen_tag_sets:
            continue

        seen_labels.add(label)
        seen_sentences.add(sentence)
        seen_tag_sets.add(tags_str)
        result.append(c)

    return result


def build_rag_debug_info(result: RAGQueryResult) -> dict[str, Any]:
    """构造 RAG 调试信息，用于 prompt debug 输出。"""
    return {
        "selection_mode": result.selection_mode,
        "example_count": result.example_count,
        "fallback_reason": result.fallback_reason,
        "query_count": result.query_count,
        "is_fallback": result.is_fallback,
        "selected_example_ids": result.selected_example_ids,
        "ann_topk": result.ann_topk,
        "rerank_topn": result.rerank_topn,
        "embedding_latency_ms": round(result.embedding_latency_ms, 1),
        "ann_latency_ms": round(result.ann_latency_ms, 1),
        "rerank_latency_ms": round(result.rerank_latency_ms, 1),
    }
