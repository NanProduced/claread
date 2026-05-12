"""Grammar RAG 集成测试。

用 mock 覆盖完整链路：embedding → Zilliz → rerank → ExampleEntry。
同时覆盖各种 fallback 场景和 async strategy builder。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.infra.bailian_rerank import RerankResult
from app.infra.zilliz_client import SearchResult
from app.schemas.internal.execution_plan import GoalExecutionPlan, GoalPolicy


def _make_plan(few_shot_mode: str = "baseline") -> GoalExecutionPlan:
    return GoalExecutionPlan(
        goal_id="daily_reading",
        variant_id="intermediate_reading",
        topology_mode="learning",
        output_mode="learning_scene",
        prompt_profile="standard",
        few_shot_mode=few_shot_mode,
        policy=GoalPolicy(
            annotation_density=3,
            vocabulary_focus="high_value_only",
            grammar_focus="structural",
            translation_focus="natural",
        ),
    )


def _mock_rag_settings(mock_settings):
    mock_settings.return_value.grammar_rag_confidence_threshold = 0.3
    mock_settings.return_value.grammar_rag_ann_topk = 8
    mock_settings.return_value.grammar_rag_rerank_topn = 5
    mock_settings.return_value.bailian_embedding_model = "text-embedding-v4"
    mock_settings.return_value.bailian_embedding_dimension = 1024
    mock_settings.return_value.bailian_rerank_model = "qwen3-rerank"
    mock_settings.return_value.zilliz_collection_grammar_note = "grammar_note_examples"
    mock_settings.return_value.zilliz_collection_sentence_analysis = "sentence_analysis_examples"


_MOCK_SEARCH_RESULTS = [
    SearchResult(id="1", score=0.85, entity={
        "example_id": "grammar-gaokao-000",
        "source_sentence": "The boy who is wearing a red hat is my brother.",
        "output_fragment": '{"type": "grammar_note", "content": "定语从句"}',
        "label": "定语从句",
        "grammar_tags": '["relative_clause"]',
        "reading_variant": "gaokao",
        "output_type": "grammar_note",
    }),
    SearchResult(id="2", score=0.80, entity={
        "example_id": "grammar-gaokao-001",
        "source_sentence": "Not only did the policy raise costs, but it also reduced quality.",
        "output_fragment": '{"type": "grammar_note", "content": "倒装结构"}',
        "label": "倒装结构",
        "grammar_tags": '["inversion"]',
        "reading_variant": "gaokao",
        "output_type": "grammar_note",
    }),
]

_MOCK_RERANK_RESULTS = [
    RerankResult(index=0, relevance_score=0.95, document="doc1"),
    RerankResult(index=1, relevance_score=0.85, document="doc2"),
]

_SENTENCES = [
    {"sentence_id": "s1", "text": "Inspired by the speech, the students decided to start."}
]


class TestFullRAGPipeline:
    @pytest.mark.anyio
    async def test_full_rag_pipeline_grammar_note(self):
        from app.services.analysis.prompting.rag.grammar_rag_service import query_grammar_rag
        with patch("app.infra.bailian_embedding.embed_single", return_value=[0.1] * 1024), \
             patch("app.infra.zilliz_client.zilliz_search", return_value=_MOCK_SEARCH_RESULTS), \
             patch("app.infra.bailian_rerank.rerank", return_value=_MOCK_RERANK_RESULTS), \
             patch("app.services.analysis.prompting.rag.grammar_rag_service.get_settings") as ms:
            _mock_rag_settings(ms)
            result = await query_grammar_rag("gaokao", _SENTENCES, output_type="grammar_note")
        assert result.selection_mode == "rag"
        assert len(result.examples) == 2
        assert result.examples[0].example_type == "grammar"
        assert result.examples[0].sentence_text == "The boy who is wearing a red hat is my brother."
        assert result.selected_example_ids == ["1", "2"]

    @pytest.mark.anyio
    async def test_full_rag_pipeline_sentence_analysis(self):
        from app.services.analysis.prompting.rag.grammar_rag_service import query_grammar_rag
        sa_results = [
            SearchResult(id="10", score=0.90, entity={
                "example_id": "sa-kaoyan-000",
                "source_sentence": "The study suggests that the approach has failed.",
                "output_fragment": '{"type": "sentence_analysis"}',
                "label": "宾语从句",
                "grammar_tags": '["object_clause"]',
                "reading_variant": "kaoyan",
                "output_type": "sentence_analysis",
            }),
        ]
        with patch("app.infra.bailian_embedding.embed_single", return_value=[0.1] * 1024), \
             patch("app.infra.zilliz_client.zilliz_search", return_value=sa_results), \
             patch("app.infra.bailian_rerank.rerank", return_value=[
                 RerankResult(index=0, relevance_score=0.92, document="doc1"),
             ]), \
             patch("app.services.analysis.prompting.rag.grammar_rag_service.get_settings") as ms:
            _mock_rag_settings(ms)
            result = await query_grammar_rag("kaoyan", _SENTENCES, output_type="sentence_analysis")
        assert result.selection_mode == "rag"
        assert len(result.examples) == 1
        assert result.examples[0].example_type == "sentence_analysis"


class TestRAGFallbackScenarios:
    @pytest.mark.anyio
    async def test_rag_fallback_on_embedding_error(self):
        from app.services.analysis.prompting.rag.grammar_rag_service import query_grammar_rag
        with patch("app.infra.bailian_embedding.embed_single", side_effect=Exception("API error")):
            result = await query_grammar_rag("gaokao", _SENTENCES)
        assert result.is_fallback
        assert "retrieval_error" in result.fallback_reason

    @pytest.mark.anyio
    async def test_rag_fallback_on_zilliz_error(self):
        from app.services.analysis.prompting.rag.grammar_rag_service import query_grammar_rag
        with patch("app.infra.bailian_embedding.embed_single", return_value=[0.1] * 1024), \
             patch("app.infra.zilliz_client.zilliz_search", side_effect=Exception("Zilliz down")):
            result = await query_grammar_rag("gaokao", _SENTENCES)
        assert result.is_fallback

    @pytest.mark.anyio
    async def test_rag_fallback_on_rerank_error(self):
        from app.services.analysis.prompting.rag.grammar_rag_service import query_grammar_rag
        with patch("app.infra.bailian_embedding.embed_single", return_value=[0.1] * 1024), \
             patch("app.infra.zilliz_client.zilliz_search", return_value=_MOCK_SEARCH_RESULTS), \
             patch("app.infra.bailian_rerank.rerank", side_effect=Exception("Rerank error")), \
             patch("app.services.analysis.prompting.rag.grammar_rag_service.get_settings") as ms:
            _mock_rag_settings(ms)
            result = await query_grammar_rag("gaokao", _SENTENCES)
        assert result.is_fallback

    @pytest.mark.anyio
    async def test_rag_fallback_on_empty_candidates(self):
        from app.services.analysis.prompting.rag.grammar_rag_service import query_grammar_rag
        with patch("app.infra.bailian_embedding.embed_single", return_value=[0.1] * 1024), \
             patch("app.infra.zilliz_client.zilliz_search", return_value=[]):
            result = await query_grammar_rag("gaokao", _SENTENCES)
        assert result.is_fallback
        assert result.fallback_reason == "empty_candidates"

    @pytest.mark.anyio
    async def test_rag_fallback_on_low_confidence(self):
        from app.services.analysis.prompting.rag.grammar_rag_service import query_grammar_rag
        low_score_results = [
            SearchResult(id="1", score=0.85, entity={
                "example_id": "test-1", "source_sentence": "s1",
                "output_fragment": "{}", "label": "l1",
                "grammar_tags": "[]", "reading_variant": "gaokao",
                "output_type": "grammar_note",
            }),
        ]
        with patch("app.infra.bailian_embedding.embed_single", return_value=[0.1] * 1024), \
             patch("app.infra.zilliz_client.zilliz_search", return_value=low_score_results), \
             patch("app.infra.bailian_rerank.rerank", return_value=[
                 RerankResult(index=0, relevance_score=0.1, document="doc1"),
             ]), \
             patch("app.services.analysis.prompting.rag.grammar_rag_service.get_settings") as ms:
            _mock_rag_settings(ms)
            result = await query_grammar_rag("gaokao", _SENTENCES)
        assert result.is_fallback
        assert result.fallback_reason == "low_confidence"


class TestAsyncStrategyBuilder:
    @pytest.mark.anyio
    async def test_grammar_bundle_async_with_rag_enabled(self):
        from app.services.analysis.prompting.strategy_builder import (
            build_grammar_bundle_async,
        )
        plan = _make_plan(few_shot_mode="baseline")
        with patch("app.infra.bailian_embedding.embed_single", return_value=[0.1] * 1024), \
             patch("app.infra.zilliz_client.zilliz_search", return_value=_MOCK_SEARCH_RESULTS), \
             patch("app.infra.bailian_rerank.rerank", return_value=_MOCK_RERANK_RESULTS), \
             patch(
                 "app.services.analysis.prompting.rag.grammar_rag_service.get_settings",
             ) as rag_ms, \
             patch("app.config.settings.get_settings") as cfg_ms:
            _mock_rag_settings(rag_ms)
            cfg_ms.return_value.grammar_rag_enabled = True
            cfg_ms.return_value.grammar_rag_confidence_threshold = 0.3
            cfg_ms.return_value.grammar_rag_ann_topk = 8
            cfg_ms.return_value.grammar_rag_rerank_topn = 5
            cfg_ms.return_value.bailian_embedding_model = "text-embedding-v4"
            cfg_ms.return_value.bailian_embedding_dimension = 1024
            cfg_ms.return_value.bailian_rerank_model = "qwen3-rerank"
            cfg_ms.return_value.zilliz_collection_grammar_note = "grammar_note_examples"
            cfg_ms.return_value.zilliz_collection_sentence_analysis = (
                "sentence_analysis_examples"
            )
            bundle = await build_grammar_bundle_async(plan, sentences=_SENTENCES)
        assert bundle.example_strategy.selection_mode == "rag"
        assert len(bundle.example_strategy.examples) >= 2

    @pytest.mark.anyio
    async def test_grammar_bundle_async_with_rag_disabled(self):
        from app.services.analysis.prompting.strategy_builder import (
            build_grammar_bundle_async,
        )
        plan = _make_plan(few_shot_mode="rag")
        with patch("app.config.settings.get_settings") as cfg_ms:
            cfg_ms.return_value.grammar_rag_enabled = False
            bundle = await build_grammar_bundle_async(plan, sentences=_SENTENCES)
        assert bundle.example_strategy.selection_mode == "baseline"

    @pytest.mark.anyio
    async def test_grammar_bundle_async_baseline_mode(self):
        from app.services.analysis.prompting.strategy_builder import (
            build_grammar_bundle_async,
        )
        plan = _make_plan(few_shot_mode="baseline")
        with patch("app.config.settings.get_settings") as cfg_ms:
            cfg_ms.return_value.grammar_rag_enabled = False
            bundle = await build_grammar_bundle_async(plan, sentences=_SENTENCES)
        assert bundle.example_strategy.selection_mode == "baseline"
