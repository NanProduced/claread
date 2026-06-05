"""RAG Readiness Gate + 真实检索测试。

Tests for:
- RAG-02: GRAMMAR_RAG_ENABLED config default
- RAG-03: Only grammar can use RAG; vocabulary/translation always baseline
- RAG-04/16: grammar_rag_service fallback + 真实检索
- RAG-08: debug info structure
- RAG-14: SearchResult 协议
- RAG-16: 置信度过滤、多样性去重、注入预算
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.infra.bailian_embedding import EmbeddingCallResult
from app.infra.bailian_rerank import RerankCallResult, RerankResult
from app.infra.zilliz_client import SearchResult
from app.schemas.internal.execution_plan import GoalExecutionPlan, GoalPolicy
from app.services.analysis.prompting.example_strategy import (
    get_grammar_example_strategy,
    get_translation_example_strategy,
    get_vocabulary_example_strategy,
)

_TEST_SENTENCE = {
    "sentence_id": "s1",
    "text": "Inspired by the speech, the students decided to start.",
}

_SHORT_SENTENCE = {"sentence_id": "s1", "text": "Test sentence."}

_MOCK_EMBEDDING_RESULT = EmbeddingCallResult(
    embeddings=[[0.1] * 1024],
    usage_data={"total_tokens": 10},
    provider_metadata={},
    model="text-embedding-v4",
    dimension=1024,
    input_count=1,
    input_chars=100,
    batch_count=1,
)


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
    mock_settings.return_value.zilliz_collection_sentence_analysis = (
        "sentence_analysis_examples"
    )


class TestRAGConfig:
    def test_grammar_rag_enabled_defaults_to_false(self):
        from app.config.settings import Settings
        s = Settings()
        assert s.grammar_rag_enabled is False


class TestRAGRestriction:
    def test_vocabulary_rag_mode_falls_back_to_baseline(self):
        plan = _make_plan(few_shot_mode="rag")
        strategy = get_vocabulary_example_strategy(
            plan, sentences=[_SHORT_SENTENCE]
        )
        assert strategy.selection_mode == "baseline"

    def test_translation_rag_mode_falls_back_to_baseline(self):
        plan = _make_plan(few_shot_mode="rag")
        strategy = get_translation_example_strategy(
            plan, sentences=[_SHORT_SENTENCE]
        )
        assert strategy.selection_mode == "baseline"

    @patch("app.config.settings.get_settings")
    def test_grammar_rag_disabled_falls_back_to_baseline(self, mock_settings):
        mock_settings.return_value.grammar_rag_enabled = False
        plan = _make_plan(few_shot_mode="rag")
        strategy = get_grammar_example_strategy(
            plan, sentences=[_SHORT_SENTENCE]
        )
        assert strategy.selection_mode == "baseline"

    @patch("app.config.settings.get_settings")
    def test_grammar_rag_enabled_attempts_rag_then_fallback(self, mock_settings):
        mock_settings.return_value.grammar_rag_enabled = True
        plan = _make_plan(few_shot_mode="rag")
        strategy = get_grammar_example_strategy(
            plan, sentences=[_SHORT_SENTENCE]
        )
        assert strategy.selection_mode == "rag_fallback"


class TestGrammarRAGFallback:
    @pytest.mark.anyio
    async def test_empty_sentences_returns_fallback(self):
        from app.services.analysis.prompting.rag.grammar_rag_service import (
            query_grammar_rag,
        )
        result = await query_grammar_rag("gaokao", [])
        assert result.is_fallback
        assert result.fallback_reason == "no_input_sentences"
        assert result.examples == []

    @pytest.mark.anyio
    async def test_retrieval_error_returns_fallback(self):
        from app.services.analysis.prompting.rag.grammar_rag_service import (
            query_grammar_rag,
        )
        with patch(
            "app.infra.bailian_embedding.embed_single_with_metadata",
            side_effect=Exception("unavailable"),
        ):
            result = await query_grammar_rag("gaokao", [_TEST_SENTENCE])
        assert result.is_fallback
        assert "retrieval_error" in result.fallback_reason

    @pytest.mark.anyio
    async def test_empty_candidates_returns_fallback(self):
        from app.services.analysis.prompting.rag.grammar_rag_service import (
            query_grammar_rag,
        )
        with patch(
            "app.infra.bailian_embedding.embed_single_with_metadata",
            return_value=_MOCK_EMBEDDING_RESULT,
        ), patch("app.infra.zilliz_client.zilliz_search", return_value=[]):
            result = await query_grammar_rag("gaokao", [_TEST_SENTENCE])
        assert result.is_fallback
        assert result.fallback_reason == "empty_candidates"

    @pytest.mark.anyio
    async def test_low_confidence_returns_fallback(self):
        from app.services.analysis.prompting.rag.grammar_rag_service import (
            query_grammar_rag,
        )
        mock_rerank = RerankCallResult(
            results=[RerankResult(index=0, relevance_score=0.05, document="doc1")],
            usage_data={"total_tokens": 10},
            provider_metadata={},
            model="qwen3-rerank",
            input_count=1,
            input_chars=100,
            top_n=1,
        )
        with patch(
            "app.infra.bailian_embedding.embed_single_with_metadata",
            return_value=_MOCK_EMBEDDING_RESULT,
        ), patch(
            "app.infra.zilliz_client.zilliz_search",
            return_value=[
                SearchResult(
                    id="1", score=0.85, entity={
                        "example_id": "test-1", "source_sentence": "s1",
                        "output_fragment": "{}", "label": "l1",
                        "grammar_tags": "[]", "reading_variant": "gaokao",
                        "output_type": "grammar_note",
                    },
                ),
            ],
        ), patch(
            "app.infra.bailian_rerank.rerank_with_metadata",
            return_value=mock_rerank,
        ), patch(
            "app.services.analysis.prompting.rag.grammar_rag_service.get_settings",
        ) as mock_settings:
            _mock_rag_settings(mock_settings)
            result = await query_grammar_rag("gaokao", [_TEST_SENTENCE])
        assert result.is_fallback
        assert result.fallback_reason == "low_confidence"
        assert result.dropped_examples == [
            {
                "example_id": "1",
                "ann_score": 0.85,
                "rerank_score": 0.05,
                "reading_variant": "gaokao",
                "output_type": "grammar_note",
                "label": "l1",
                "grammar_tags": [],
                "drop_stage": "confidence_filter",
                "drop_reason": "below_confidence_threshold",
            }
        ]

    @pytest.mark.anyio
    async def test_successful_rag_returns_examples(self):
        from app.services.analysis.prompting.rag.grammar_rag_service import (
            query_grammar_rag,
        )
        mock_rerank = RerankCallResult(
            results=[
                RerankResult(index=0, relevance_score=0.95, document="doc1"),
                RerankResult(index=1, relevance_score=0.85, document="doc2"),
            ],
            usage_data={"total_tokens": 20},
            provider_metadata={},
            model="qwen3-rerank",
            input_count=1,
            input_chars=200,
            top_n=2,
        )
        with patch(
            "app.infra.bailian_embedding.embed_single_with_metadata",
            return_value=_MOCK_EMBEDDING_RESULT,
        ), patch(
            "app.infra.zilliz_client.zilliz_search",
            return_value=[
                SearchResult(
                    id="1", score=0.85, entity={
                        "example_id": "test-1",
                        "source_sentence": "The boy who is wearing a red hat is my brother.",
                        "output_fragment": '{"type": "grammar_note"}',
                        "label": "定语从句",
                        "grammar_tags": '["relative_clause"]',
                        "reading_variant": "gaokao",
                        "output_type": "grammar_note",
                    },
                ),
                SearchResult(
                    id="2", score=0.80, entity={
                        "example_id": "test-2",
                        "source_sentence": "Not only did the policy raise costs.",
                        "output_fragment": '{"type": "grammar_note"}',
                        "label": "倒装结构",
                        "grammar_tags": '["inversion"]',
                        "reading_variant": "gaokao",
                        "output_type": "grammar_note",
                    },
                ),
            ],
        ), patch(
            "app.infra.bailian_rerank.rerank_with_metadata",
            return_value=mock_rerank,
        ), patch(
            "app.services.analysis.prompting.rag.grammar_rag_service.get_settings",
        ) as mock_settings:
            _mock_rag_settings(mock_settings)
            result = await query_grammar_rag("gaokao", [_TEST_SENTENCE])
        assert not result.is_fallback
        assert result.selection_mode == "rag"
        assert len(result.examples) == 2
        assert result.examples[0].sentence_text == (
            "The boy who is wearing a red hat is my brother."
        )
        assert result.selected_example_ids == ["1", "2"]
        assert result.embedding_latency_ms > 0
        assert result.query_sentence_id == "s1"
        assert result.query_sentence_text == _TEST_SENTENCE["text"]
        assert result.query_text
        assert "variant:" in result.query_text
        assert "output_type:" in result.query_text
        assert "variant=" not in result.query_text
        assert "output_type=" not in result.query_text
        assert result.ann_hit_count == 2
        assert result.rerank_hit_count == 2
        assert result.selected_examples == [
            {
                "example_id": "1",
                "ann_score": 0.85,
                "rerank_score": 0.95,
                "reading_variant": "gaokao",
                "output_type": "grammar_note",
                "label": "定语从句",
                "grammar_tags": ["relative_clause"],
                "source_sentence": "The boy who is wearing a red hat is my brother.",
                "output_fragment": '{"type": "grammar_note"}',
            },
            {
                "example_id": "2",
                "ann_score": 0.8,
                "rerank_score": 0.85,
                "reading_variant": "gaokao",
                "output_type": "grammar_note",
                "label": "倒装结构",
                "grammar_tags": ["inversion"],
                "source_sentence": "Not only did the policy raise costs.",
                "output_fragment": '{"type": "grammar_note"}',
            },
        ]

    @pytest.mark.anyio
    async def test_injection_budget_limits_grammar_note_to_2(self):
        from app.services.analysis.prompting.rag.grammar_rag_service import (
            query_grammar_rag,
        )
        three_results = [
            SearchResult(
                id=str(i), score=0.9 - i * 0.05, entity={
                    "example_id": f"test-{i}",
                    "source_sentence": f"sentence {i}",
                    "output_fragment": "{}", "label": f"label_{i}",
                    "grammar_tags": f'["tag_{i}"]',
                    "reading_variant": "gaokao",
                    "output_type": "grammar_note",
                },
            )
            for i in range(3)
        ]
        mock_rerank = RerankCallResult(
            results=[
                RerankResult(index=i, relevance_score=0.95 - i * 0.1, document=f"doc{i}")
                for i in range(3)
            ],
            usage_data={"total_tokens": 30},
            provider_metadata={},
            model="qwen3-rerank",
            input_count=1,
            input_chars=300,
            top_n=3,
        )
        with patch(
            "app.infra.bailian_embedding.embed_single_with_metadata",
            return_value=_MOCK_EMBEDDING_RESULT,
        ), patch(
            "app.infra.zilliz_client.zilliz_search",
            return_value=three_results,
        ), patch(
            "app.infra.bailian_rerank.rerank_with_metadata",
            return_value=mock_rerank,
        ), patch(
            "app.services.analysis.prompting.rag.grammar_rag_service.get_settings",
        ) as mock_settings:
            _mock_rag_settings(mock_settings)
            result = await query_grammar_rag("gaokao", [_SHORT_SENTENCE])
        assert len(result.examples) == 2
        assert result.dropped_examples == [
            {
                "example_id": "2",
                "ann_score": 0.8,
                "rerank_score": 0.75,
                "reading_variant": "gaokao",
                "output_type": "grammar_note",
                "label": "label_2",
                "grammar_tags": ["tag_2"],
                "drop_stage": "budget_trim",
                "drop_reason": "exceeds_injection_budget",
            }
        ]

    @pytest.mark.anyio
    async def test_diversity_dedup_removes_duplicate_labels(self):
        from app.services.analysis.prompting.rag.grammar_rag_service import (
            _diversity_dedup,
            _ScoredCandidate,
        )
        candidates = [
            _ScoredCandidate(
                example_id="1", ann_score=0.93, rerank_score=0.9,
                entity={"label": "定语从句", "source_sentence": "s1", "grammar_tags": '["a"]'},
            ),
            _ScoredCandidate(
                example_id="2", ann_score=0.91, rerank_score=0.8,
                entity={"label": "定语从句", "source_sentence": "s2", "grammar_tags": '["b"]'},
            ),
            _ScoredCandidate(
                example_id="3", ann_score=0.89, rerank_score=0.7,
                entity={"label": "倒装", "source_sentence": "s3", "grammar_tags": '["c"]'},
            ),
        ]
        kept, dropped = _diversity_dedup(candidates)
        assert len(kept) == 2
        assert kept[0].example_id == "1"
        assert kept[1].example_id == "3"
        assert dropped == [
            {
                "example_id": "2",
                "ann_score": 0.91,
                "rerank_score": 0.8,
                "reading_variant": None,
                "output_type": None,
                "label": "定语从句",
                "grammar_tags": ["b"],
                "drop_stage": "diversity_dedup",
                "drop_reason": "duplicate_label",
            }
        ]


class TestRAGDebugInfo:
    @pytest.mark.anyio
    async def test_debug_info_has_required_fields(self):
        from app.services.analysis.prompting.rag.grammar_rag_service import (
            build_rag_debug_info,
            query_grammar_rag,
        )
        with patch(
            "app.infra.bailian_embedding.embed_single_with_metadata",
            return_value=_MOCK_EMBEDDING_RESULT,
        ), patch("app.infra.zilliz_client.zilliz_search", return_value=[]):
            result = await query_grammar_rag("gaokao", [_SHORT_SENTENCE])
        info = build_rag_debug_info(result)
        assert "selection_mode" in info
        assert "example_count" in info
        assert "fallback_reason" in info
        assert "query_count" in info
        assert "is_fallback" in info
        assert "selected_example_ids" in info
        assert "ann_topk" in info
        assert "rerank_topn" in info
        assert "embedding_latency_ms" in info
        assert "ann_latency_ms" in info
        assert "rerank_latency_ms" in info
        assert "query_sentence_id" in info
        assert "query_sentence_text" in info
        assert "query_text" in info
        assert "candidate_sentence_ids" in info
        assert "ann_hit_count" in info
        assert "rerank_hit_count" in info
        assert "confidence_threshold" in info
        assert "ann_hits" in info
        assert "rerank_hits" in info
        assert "dropped_examples" in info
        assert "selected_examples" in info
        assert "query_tags" in info
        assert "tag_overlap" in info
