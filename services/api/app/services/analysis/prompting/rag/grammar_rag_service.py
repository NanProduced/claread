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

import json
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
    query_sentence_id: str | None = None
    query_sentence_text: str | None = None
    query_text: str | None = None
    candidate_sentence_ids: list[str] = field(default_factory=list)
    ann_hits: list[dict[str, Any]] = field(default_factory=list)
    rerank_hits: list[dict[str, Any]] = field(default_factory=list)
    dropped_examples: list[dict[str, Any]] = field(default_factory=list)
    selected_examples: list[dict[str, Any]] = field(default_factory=list)
    ann_hit_count: int = 0
    rerank_hit_count: int = 0
    confidence_threshold: float | None = None

    @property
    def is_fallback(self) -> bool:
        return self.selection_mode in ("rag_fallback", "baseline")


@dataclass
class _ScoredCandidate:
    """内部候选，携带 rerank / ANN 分数和 Zilliz entity。"""

    example_id: str
    ann_score: float
    rerank_score: float
    entity: dict[str, Any]


def _normalize_grammar_tags(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return [raw]
        if isinstance(parsed, list):
            return [str(item) for item in parsed if str(item).strip()]
        if parsed is None:
            return []
        return [str(parsed)]
    return [str(value)]


def _compact_candidate_dict(
    *,
    example_id: str,
    ann_score: float | None,
    rerank_score: float | None,
    entity: dict[str, Any],
    drop_stage: str | None = None,
    drop_reason: str | None = None,
) -> dict[str, Any]:
    payload = {
        "example_id": example_id,
        "ann_score": round(ann_score, 4) if ann_score is not None else None,
        "rerank_score": round(rerank_score, 4) if rerank_score is not None else None,
        "reading_variant": entity.get("reading_variant"),
        "output_type": entity.get("output_type"),
        "label": entity.get("label"),
        "grammar_tags": _normalize_grammar_tags(entity.get("grammar_tags")),
    }
    if drop_stage is not None:
        payload["drop_stage"] = drop_stage
    if drop_reason is not None:
        payload["drop_reason"] = drop_reason
    return payload


def _selected_candidate_dict(candidate: _ScoredCandidate) -> dict[str, Any]:
    payload = _compact_candidate_dict(
        example_id=candidate.example_id,
        ann_score=candidate.ann_score,
        rerank_score=candidate.rerank_score,
        entity=candidate.entity,
    )
    payload["source_sentence"] = candidate.entity.get("source_sentence", "")
    payload["output_fragment"] = candidate.entity.get("output_fragment", "")
    return payload


def _ann_hit_dict(search_result: Any) -> dict[str, Any]:
    return _compact_candidate_dict(
        example_id=search_result.id,
        ann_score=search_result.score,
        rerank_score=None,
        entity=search_result.entity,
    )


async def query_grammar_rag(
    variant: str,
    sentences: list[dict],
    output_type: str = "grammar_note",
    top_k: int = 5,
) -> RAGQueryResult:
    """查询 grammar RAG 示例池。"""
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
    result = RAGQueryResult(
        query_count=len(sentences),
        confidence_threshold=settings.grammar_rag_confidence_threshold,
    )

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

    filtered, confidence_drops = _apply_confidence_filter(
        candidates,
        min_score=settings.grammar_rag_confidence_threshold,
    )
    result.dropped_examples.extend(confidence_drops)
    if not filtered:
        result.fallback_reason = "low_confidence"
        result.selection_mode = "rag_fallback"
        return result

    deduped, diversity_drops = _diversity_dedup(filtered)
    result.dropped_examples.extend(diversity_drops)

    budget = _INJECTION_BUDGET.get(output_type, 2)
    final = deduped[:budget]
    result.dropped_examples.extend(
        _compact_candidate_dict(
            example_id=candidate.example_id,
            ann_score=candidate.ann_score,
            rerank_score=candidate.rerank_score,
            entity=candidate.entity,
            drop_stage="budget_trim",
            drop_reason="exceeds_injection_budget",
        )
        for candidate in deduped[budget:]
    )

    output_type_to_example_type = {
        "grammar_note": "grammar",
        "sentence_analysis": "sentence_analysis",
    }
    examples = [
        ExampleEntry(
            example_type=output_type_to_example_type.get(output_type, output_type),
            sentence_text=candidate.entity.get("source_sentence", ""),
            output_fragment=candidate.entity.get("output_fragment", ""),
        )
        for candidate in final
    ]

    result.examples = examples
    result.selection_mode = "rag"
    result.example_count = len(examples)
    result.selected_example_ids = [candidate.example_id for candidate in final]
    result.selected_examples = [
        _selected_candidate_dict(candidate) for candidate in final
    ]

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
        sentences,
        output_type=output_type,
    )
    result.candidate_sentence_ids = [
        str(sentence.get("sentence_id", "")).strip()
        for sentence in candidate_sentences
        if str(sentence.get("sentence_id", "")).strip()
    ]
    if not candidate_sentences:
        return []

    query_sentence = candidate_sentences[0]
    result.query_sentence_id = query_sentence.get("sentence_id")
    result.query_sentence_text = query_sentence.get("text", "")
    query_text = build_query_text(
        sentence=query_sentence.get("text", ""),
        variant=variant,
        output_type=output_type,
    )
    result.query_text = query_text

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
        output_fields=[
            "example_id",
            "reading_variant",
            "output_type",
            "grammar_tags",
            "label",
            "source_sentence",
            "output_fragment",
            "grammar_granularity",
            "quality_score",
            "approved",
        ],
    )

    if not search_results and variant != "default":
        filter_expr = f'{base_filter} and reading_variant == "default"'
        logger.info("RAG ANN fallback to default variant: filter=%s", filter_expr)
        search_results = await zilliz_search(
            collection_name=collection_name,
            query_vector=query_vector,
            top_k=settings.grammar_rag_ann_topk,
            filter_expr=filter_expr,
            output_fields=[
                "example_id",
                "reading_variant",
                "output_type",
                "grammar_tags",
                "label",
                "source_sentence",
                "output_fragment",
                "grammar_granularity",
                "quality_score",
                "approved",
            ],
        )

    result.ann_latency_ms = (time.monotonic() - t0) * 1000
    result.ann_topk = settings.grammar_rag_ann_topk
    result.ann_hits = [_ann_hit_dict(search_result) for search_result in search_results]
    result.ann_hit_count = len(search_results)

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
    result.rerank_hit_count = len(rerank_results)

    candidates: list[_ScoredCandidate] = []
    rerank_hits: list[dict[str, Any]] = []
    for rerank_result in rerank_results:
        original = search_results[rerank_result.index]
        candidate = _ScoredCandidate(
            example_id=original.id,
            ann_score=original.score,
            rerank_score=rerank_result.relevance_score,
            entity=original.entity,
        )
        candidates.append(candidate)
        rerank_hits.append(
            _compact_candidate_dict(
                example_id=candidate.example_id,
                ann_score=candidate.ann_score,
                rerank_score=candidate.rerank_score,
                entity=candidate.entity,
            )
        )

    result.rerank_hits = rerank_hits

    return candidates


def _apply_confidence_filter(
    candidates: list[_ScoredCandidate],
    min_score: float = 0.3,
) -> tuple[list[_ScoredCandidate], list[dict[str, Any]]]:
    """过滤低置信度候选。"""
    kept: list[_ScoredCandidate] = []
    dropped: list[dict[str, Any]] = []
    for candidate in candidates:
        if candidate.rerank_score >= min_score:
            kept.append(candidate)
            continue
        dropped.append(
            _compact_candidate_dict(
                example_id=candidate.example_id,
                ann_score=candidate.ann_score,
                rerank_score=candidate.rerank_score,
                entity=candidate.entity,
                drop_stage="confidence_filter",
                drop_reason="below_confidence_threshold",
            )
        )
    return kept, dropped


def _diversity_dedup(
    candidates: list[_ScoredCandidate],
) -> tuple[list[_ScoredCandidate], list[dict[str, Any]]]:
    """多样性去重。"""
    seen_labels: set[str] = set()
    seen_sentences: set[str] = set()
    seen_tag_sets: set[str] = set()
    kept: list[_ScoredCandidate] = []
    dropped: list[dict[str, Any]] = []

    for candidate in candidates:
        label = str(candidate.entity.get("label", "") or "")
        sentence = str(candidate.entity.get("source_sentence", "") or "")
        tags = tuple(_normalize_grammar_tags(candidate.entity.get("grammar_tags")))
        tag_key = json.dumps(tags, ensure_ascii=True)

        drop_reason: str | None = None
        if label and label in seen_labels:
            drop_reason = "duplicate_label"
        elif sentence and sentence in seen_sentences:
            drop_reason = "duplicate_sentence"
        elif tags and tag_key in seen_tag_sets:
            drop_reason = "duplicate_tag_set"

        if drop_reason is not None:
            dropped.append(
                _compact_candidate_dict(
                    example_id=candidate.example_id,
                    ann_score=candidate.ann_score,
                    rerank_score=candidate.rerank_score,
                    entity=candidate.entity,
                    drop_stage="diversity_dedup",
                    drop_reason=drop_reason,
                )
            )
            continue

        if label:
            seen_labels.add(label)
        if sentence:
            seen_sentences.add(sentence)
        if tags:
            seen_tag_sets.add(tag_key)
        kept.append(candidate)

    return kept, dropped


def build_rag_debug_info(result: RAGQueryResult) -> dict[str, Any]:
    """构造 RAG 调试信息，用于 prompt debug / snapshot 输出。"""
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
        "query_sentence_id": result.query_sentence_id,
        "query_sentence_text": result.query_sentence_text,
        "query_text": result.query_text,
        "candidate_sentence_ids": result.candidate_sentence_ids,
        "ann_hit_count": result.ann_hit_count,
        "rerank_hit_count": result.rerank_hit_count,
        "confidence_threshold": result.confidence_threshold,
        "ann_hits": result.ann_hits,
        "rerank_hits": result.rerank_hits,
        "dropped_examples": result.dropped_examples,
        "selected_examples": result.selected_examples,
    }
