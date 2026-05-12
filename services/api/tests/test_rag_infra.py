"""Grammar RAG 基础设施层单元测试。

覆盖：
- Settings 新增配置字段默认值
- Zilliz 客户端封装（未初始化/初始化/搜索/插入/查询）
- 百炼 Embedding 客户端（单条/批量/自动分批/API Key 缺失）
- 百炼 Rerank 客户端（正常调用/空输入/API Key 缺失）
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestSettingsZillizDefaults:
    def test_zilliz_uri_defaults_empty(self):
        from app.config.settings import Settings
        s = Settings()
        assert s.zilliz_uri == ""

    def test_zilliz_token_defaults_empty(self):
        from app.config.settings import Settings
        s = Settings()
        assert s.zilliz_token == ""

    def test_zilliz_collection_grammar_note_default(self):
        from app.config.settings import Settings
        s = Settings()
        assert s.zilliz_collection_grammar_note == "grammar_note_examples"

    def test_zilliz_collection_sentence_analysis_default(self):
        from app.config.settings import Settings
        s = Settings()
        assert s.zilliz_collection_sentence_analysis == "sentence_analysis_examples"


class TestSettingsBailianDefaults:
    def test_bailian_api_key_defaults_empty(self):
        from app.config.settings import Settings
        s = Settings()
        assert s.bailian_api_key == ""

    def test_bailian_embedding_model_default(self):
        from app.config.settings import Settings
        s = Settings()
        assert s.bailian_embedding_model == "text-embedding-v4"

    def test_bailian_embedding_dimension_default(self):
        from app.config.settings import Settings
        s = Settings()
        assert s.bailian_embedding_dimension == 1024

    def test_bailian_rerank_model_default(self):
        from app.config.settings import Settings
        s = Settings()
        assert s.bailian_rerank_model == "qwen3-rerank"


class TestSettingsRAGParamsDefaults:
    def test_grammar_rag_ann_topk_default(self):
        from app.config.settings import Settings
        s = Settings()
        assert s.grammar_rag_ann_topk == 8

    def test_grammar_rag_rerank_topn_default(self):
        from app.config.settings import Settings
        s = Settings()
        assert s.grammar_rag_rerank_topn == 5

    def test_grammar_rag_confidence_threshold_default(self):
        from app.config.settings import Settings
        s = Settings()
        assert s.grammar_rag_confidence_threshold == 0.3


class TestZillizClientNotInitialized:
    @pytest.mark.anyio
    async def test_search_returns_empty_when_not_initialized(self):
        import app.infra.zilliz_client as mod
        from app.infra.zilliz_client import zilliz_search
        original = mod._client
        mod._client = None
        try:
            result = await zilliz_search("test_collection", [0.1] * 1024)
            assert result == []
        finally:
            mod._client = original

    @pytest.mark.anyio
    async def test_query_returns_empty_when_not_initialized(self):
        import app.infra.zilliz_client as mod
        from app.infra.zilliz_client import zilliz_query
        original = mod._client
        mod._client = None
        try:
            result = await zilliz_query("test_collection", 'approved == true')
            assert result == []
        finally:
            mod._client = original

    @pytest.mark.anyio
    async def test_is_ready_returns_false_when_not_initialized(self):
        import app.infra.zilliz_client as mod
        from app.infra.zilliz_client import is_zilliz_ready
        original = mod._client
        mod._client = None
        try:
            assert await is_zilliz_ready() is False
        finally:
            mod._client = original


class TestZillizClientInitAndClose:
    @pytest.mark.anyio
    async def test_init_skips_when_uri_empty(self):
        import app.infra.zilliz_client as mod
        from app.infra.zilliz_client import init_zilliz
        original = mod._client
        mod._client = None
        try:
            await init_zilliz(uri="", token="some_token")
            assert mod._client is None
        finally:
            mod._client = original

    @pytest.mark.anyio
    async def test_init_skips_when_token_empty(self):
        import app.infra.zilliz_client as mod
        from app.infra.zilliz_client import init_zilliz
        original = mod._client
        mod._client = None
        try:
            await init_zilliz(uri="https://example.com", token="")
            assert mod._client is None
        finally:
            mod._client = original

    @pytest.mark.anyio
    async def test_init_creates_client(self):
        import app.infra.zilliz_client as mod
        from app.infra.zilliz_client import init_zilliz
        original = mod._client
        mock_client = MagicMock()
        with patch("app.infra.zilliz_client.MilvusClient", return_value=mock_client):
            await init_zilliz(uri="https://example.zillizcloud.com", token="test_token")
            assert mod._client is mock_client
        mod._client = original

    @pytest.mark.anyio
    async def test_close_resets_client_to_none(self):
        import app.infra.zilliz_client as mod
        from app.infra.zilliz_client import close_zilliz
        original = mod._client
        mock_client = MagicMock()
        mod._client = mock_client
        try:
            await close_zilliz()
            assert mod._client is None
            mock_client.close.assert_called_once()
        finally:
            mod._client = original


class TestZillizClientSearch:
    @pytest.mark.anyio
    async def test_search_returns_search_results(self):
        import app.infra.zilliz_client as mod
        from app.infra.zilliz_client import SearchResult, zilliz_search
        original = mod._client
        mock_client = MagicMock()
        mock_client.search.return_value = [
            [
                {
                    "id": "1",
                    "distance": 0.15,
                    "entity": {"example_id": "grammar-gaokao-000", "label": "test"},
                },
                {
                    "id": "2",
                    "distance": 0.30,
                    "entity": {"example_id": "grammar-gaokao-001", "label": "test2"},
                },
            ]
        ]
        mod._client = mock_client
        try:
            result = await zilliz_search(
                "grammar_note_examples",
                [0.1] * 1024,
                top_k=2,
            )
            assert len(result) == 2
            assert isinstance(result[0], SearchResult)
            assert result[0].id == "1"
            assert result[0].score == pytest.approx(0.85)
            assert result[0].entity["example_id"] == "grammar-gaokao-000"
            assert result[1].score == pytest.approx(0.70)
        finally:
            mod._client = original

    @pytest.mark.anyio
    async def test_search_returns_empty_on_empty_results(self):
        import app.infra.zilliz_client as mod
        from app.infra.zilliz_client import zilliz_search
        original = mod._client
        mock_client = MagicMock()
        mock_client.search.return_value = [[]]
        mod._client = mock_client
        try:
            result = await zilliz_search("grammar_note_examples", [0.1] * 1024)
            assert result == []
        finally:
            mod._client = original

    @pytest.mark.anyio
    async def test_search_returns_empty_on_exception(self):
        import app.infra.zilliz_client as mod
        from app.infra.zilliz_client import zilliz_search
        original = mod._client
        mock_client = MagicMock()
        mock_client.search.side_effect = Exception("connection error")
        mod._client = mock_client
        try:
            result = await zilliz_search("grammar_note_examples", [0.1] * 1024)
            assert result == []
        finally:
            mod._client = original


class TestZillizSchemaContract:
    @pytest.mark.anyio
    async def test_create_collection_schema_includes_new_fields(self):
        import app.infra.zilliz_client as mod
        from app.infra.zilliz_client import zilliz_create_collection
        original = mod._client
        mock_client = MagicMock()
        mock_client.list_collections.return_value = []
        mock_client.prepare_index_params.return_value = MagicMock()
        mod._client = mock_client
        try:
            await zilliz_create_collection("test_collection", dimension=1024)
            call_kwargs = mock_client.create_collection.call_args
            schema = call_kwargs.kwargs["schema"]
            field_names = [f.name for f in schema.fields]
            assert "source_sentence" in field_names
            assert "output_fragment" in field_names
            assert "grammar_granularity" in field_names
            assert "example_id" in field_names
            assert "vector" in field_names
            assert "reading_variant" in field_names
            assert "output_type" in field_names
            assert "grammar_tags" in field_names
            assert "structure_signals" in field_names
            assert "label" in field_names
            assert "quality_score" in field_names
            assert "approved" in field_names
            assert len(schema.fields) == 12
        finally:
            mod._client = original


class TestEmbeddingClient:
    @pytest.mark.anyio
    async def test_embed_texts_raises_when_no_api_key(self):
        from app.infra.bailian_embedding import EmbeddingError, embed_texts
        with patch("app.infra.bailian_embedding.get_settings") as mock_settings:
            mock_settings.return_value.bailian_api_key = ""
            with pytest.raises(EmbeddingError, match="BAILIAN_API_KEY"):
                await embed_texts(["test text"])

    @pytest.mark.anyio
    async def test_embed_texts_returns_empty_for_empty_input(self):
        from app.infra.bailian_embedding import embed_texts
        result = await embed_texts([])
        assert result == []

    @pytest.mark.anyio
    async def test_embed_single_calls_embed_texts(self):
        from app.infra.bailian_embedding import embed_single
        with patch("app.infra.bailian_embedding.embed_texts") as mock_embed:
            mock_embed.return_value = [[0.1, 0.2, 0.3]]
            result = await embed_single("test text")
            assert result == [0.1, 0.2, 0.3]
            mock_embed.assert_called_once_with(
                ["test text"], model="text-embedding-v4", dimension=1024
            )

    @pytest.mark.anyio
    async def test_embed_texts_auto_batches_over_25(self):
        from app.infra.bailian_embedding import embed_texts
        texts = [f"text {i}" for i in range(30)]

        with patch("app.infra.bailian_embedding.get_settings") as mock_settings, \
             patch("app.infra.bailian_embedding._call_embedding_sync") as mock_call:
            mock_settings.return_value.bailian_api_key = "test_key"
            mock_call.side_effect = lambda texts, **kw: [[0.1] * 1024 for _ in texts]

            result = await embed_texts(texts)
            assert len(result) == 30
            assert mock_call.call_count == 2

    @pytest.mark.anyio
    async def test_embed_texts_single_batch(self):
        from app.infra.bailian_embedding import embed_texts
        texts = [f"text {i}" for i in range(3)]

        with patch("app.infra.bailian_embedding.get_settings") as mock_settings, \
             patch("app.infra.bailian_embedding._call_embedding_sync") as mock_call:
            mock_settings.return_value.bailian_api_key = "test_key"
            mock_call.return_value = [[0.1] * 1024 for _ in texts]

            result = await embed_texts(texts)
            assert len(result) == 3
            assert mock_call.call_count == 1


class TestRerankClient:
    @pytest.mark.anyio
    async def test_rerank_raises_when_no_api_key(self):
        from app.infra.bailian_rerank import RerankError, rerank
        with patch("app.infra.bailian_rerank.get_settings") as mock_settings:
            mock_settings.return_value.bailian_api_key = ""
            with pytest.raises(RerankError, match="BAILIAN_API_KEY"):
                await rerank("test query", ["doc1", "doc2"])

    @pytest.mark.anyio
    async def test_rerank_returns_empty_for_empty_documents(self):
        from app.infra.bailian_rerank import rerank
        result = await rerank("test query", [])
        assert result == []

    @pytest.mark.anyio
    async def test_rerank_returns_sorted_results(self):
        from app.infra.bailian_rerank import RerankResult, rerank

        mock_results = [
            RerankResult(index=1, relevance_score=0.95, document="doc2"),
            RerankResult(index=0, relevance_score=0.80, document="doc1"),
        ]

        with patch("app.infra.bailian_rerank.get_settings") as mock_settings, \
             patch("app.infra.bailian_rerank._call_rerank_sync") as mock_call:
            mock_settings.return_value.bailian_api_key = "test_key"
            mock_call.return_value = mock_results

            result = await rerank("test query", ["doc1", "doc2"], top_n=2)
            assert len(result) == 2
            assert result[0].relevance_score == 0.95
            assert result[1].relevance_score == 0.80
