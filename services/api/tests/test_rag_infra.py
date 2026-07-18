"""Grammar RAG 基础设施层单元测试。

覆盖：
- Settings 新增配置字段默认值
- Zilliz 客户端封装（未初始化/初始化/搜索/插入/查询）
- 百炼 Embedding 客户端（单条/批量/自动分批/API Key 缺失）
- 百炼 Rerank 客户端（正常调用/空输入/API Key 缺失）
"""

from __future__ import annotations

import traceback
from types import SimpleNamespace
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
            assert "retrieval_text" in field_names
            assert "example_id" in field_names
            assert "vector" in field_names
            assert "reading_variant" in field_names
            assert "output_type" in field_names
            assert "grammar_tags" in field_names
            assert "label" in field_names
            assert "quality_score" in field_names
            assert "approved" in field_names
            assert len(schema.fields) == 11
        finally:
            mod._client = original


class TestEmbeddingClient:
    def test_resolve_embedding_config_raises_on_incompatible_adapter(self):
        from app.infra.bailian_embedding import EmbeddingError, resolve_embedding_config

        with patch(
            "app.infra.bailian_embedding.get_settings", return_value=SimpleNamespace()
        ), patch(
            "app.llm.router.resolve_model_config",
            return_value=SimpleNamespace(adapter="openai_compatible"),
        ):
            with pytest.raises(EmbeddingError, match="incompatible adapter"):
                resolve_embedding_config()

    @pytest.mark.anyio
    async def test_embed_texts_raises_when_no_api_key(self):
        from app.infra.bailian_embedding import EmbeddingError, embed_texts
        with patch("app.infra.bailian_embedding.resolve_embedding_config") as mock_resolve:
            mock_resolve.return_value = ("text-embedding-v4", 1024, "")
            with pytest.raises(EmbeddingError, match="No API key"):
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
                ["test text"], model=None, dimension=None
            )

    @pytest.mark.anyio
    async def test_embed_texts_auto_batches_by_capability(self):
        """Single ``embed_texts`` regression: 30 v4 inputs -> 3 batches of 10."""
        from app.infra.bailian_embedding import embed_texts

        recorded_input_counts: list[int] = []

        class FakeTextEmbedding:
            @staticmethod
            def call(**kwargs):
                input_texts = kwargs["input"]
                recorded_input_counts.append(len(input_texts))
                return SimpleNamespace(
                    status_code=200,
                    code="",
                    message="",
                    request_id=f"req-{len(recorded_input_counts)}",
                    usage={"input_tokens": len(input_texts), "total_tokens": len(input_texts)},
                    output={"embeddings": [{"embedding": [0.1, 0.2]} for _ in input_texts]},
                )

        with patch("app.infra.bailian_embedding.resolve_embedding_config") as mock_resolve, \
             patch("app.infra.bailian_embedding.dashscope.TextEmbedding", FakeTextEmbedding):
            mock_resolve.return_value = ("text-embedding-v4", 1024, "test-key")
            texts = [f"text {i}" for i in range(30)]
            result = await embed_texts(texts)

        assert len(result) == 30
        assert recorded_input_counts == [10, 10, 10]

    @pytest.mark.anyio
    async def test_embed_texts_with_metadata_empty_input_uses_resolved_config(self):
        from app.infra.bailian_embedding import embed_texts_with_metadata

        with patch("app.infra.bailian_embedding.resolve_embedding_config") as mock_resolve:
            mock_resolve.return_value = ("text-embedding-v5", 1536, "test_key")

            result = await embed_texts_with_metadata([])

            assert result.model == "text-embedding-v5"
            assert result.dimension == 1536
            assert result.input_count == 0

    @pytest.mark.parametrize(
        ("input_count", "expected_batch_shapes", "model"),
        [
            (1, [1], "text-embedding-v4"),
            (10, [10], "text-embedding-v4"),
            (11, [10, 1], "text-embedding-v4"),
            (26, [10, 10, 6], "text-embedding-v4"),
            (57, [10, 10, 10, 10, 10, 7], "text-embedding-v4"),
            (11, [10, 1], "text-embedding-v3"),
        ],
        ids=[
            "v4_1_as_single",
            "v4_10_as_single",
            "v4_11_as_10_plus_1",
            "v4_26_as_10_10_6",
            "v4_57_as_five_10_plus_7",
            "v3_11_as_10_plus_1",
        ],
    )
    @pytest.mark.anyio
    async def test_embed_texts_with_metadata_batches_by_capability(
        self, input_count, expected_batch_shapes, model
    ):
        """Public seam: outbound DashScope batch shapes follow capability registry.

        Mock boundary is ``dashscope.TextEmbedding.call``; tests assert the
        per-call ``input`` length matches the expected literal batch shape.
        Expected values are independent literals (not derived from the
        production slicing algorithm).
        """
        from app.infra.bailian_embedding import embed_texts_with_metadata

        recorded_input_counts: list[int] = []

        class FakeTextEmbedding:
            @staticmethod
            def call(**kwargs):
                input_texts = kwargs["input"]
                recorded_input_counts.append(len(input_texts))
                return SimpleNamespace(
                    status_code=200,
                    code="",
                    message="",
                    request_id=f"req-{len(recorded_input_counts)}",
                    usage={"input_tokens": len(input_texts), "total_tokens": len(input_texts)},
                    output={"embeddings": [{"embedding": [0.1, 0.2]} for _ in input_texts]},
                )

        with patch("app.infra.bailian_embedding.resolve_embedding_config") as mock_resolve, \
             patch("app.infra.bailian_embedding.dashscope.TextEmbedding", FakeTextEmbedding):
            mock_resolve.return_value = (model, 1024, "test-key")
            texts = [f"text {i}" for i in range(input_count)]
            result = await embed_texts_with_metadata(texts)

        assert len(result.embeddings) == input_count
        assert recorded_input_counts == expected_batch_shapes

    @pytest.mark.anyio
    async def test_embed_texts_with_metadata_second_batch_failure_reports_capability_ordinal(self):
        """Second batch returns 400 -> failed_batch_ordinal=2, batch_count=3, no partial results."""
        from app.infra.bailian_embedding import EmbeddingError, embed_texts_with_metadata

        call_count = {"n": 0}

        class FakeTextEmbedding:
            @staticmethod
            def call(**kwargs):
                call_count["n"] += 1
                if call_count["n"] == 2:
                    return SimpleNamespace(
                        status_code=400,
                        code="InvalidParameter",
                        message="",
                        output={"embeddings": []},
                    )
                input_texts = kwargs["input"]
                return SimpleNamespace(
                    status_code=200,
                    code="",
                    message="",
                    request_id=f"req-{call_count['n']}",
                    usage={"input_tokens": len(input_texts), "total_tokens": len(input_texts)},
                    output={"embeddings": [{"embedding": [0.1, 0.2]} for _ in input_texts]},
                )

        with patch("app.infra.bailian_embedding.resolve_embedding_config") as mock_resolve, \
             patch("app.infra.bailian_embedding.dashscope.TextEmbedding", FakeTextEmbedding):
            mock_resolve.return_value = ("text-embedding-v4", 1024, "test-key")
            texts = [f"text {i}" for i in range(26)]
            with pytest.raises(EmbeddingError) as exc_info:
                await embed_texts_with_metadata(texts)

        assert exc_info.value.failed_batch_ordinal == 2
        assert exc_info.value.batch_count == 3
        assert exc_info.value.retryable is False

    @pytest.mark.anyio
    async def test_embed_texts_with_metadata_batch_metadata_reflects_capability_shape(self):
        """Provider metadata batches reflect capability-aware shape with per-batch usage."""
        from app.infra.bailian_embedding import embed_texts_with_metadata

        call_count = {"n": 0}
        usage_by_ordinal = {
            1: {"input_tokens": 10, "total_tokens": 10},
            2: {"input_tokens": 10, "total_tokens": 10},
            3: {"input_tokens": 6, "total_tokens": 6},
        }

        class FakeTextEmbedding:
            @staticmethod
            def call(**kwargs):
                call_count["n"] += 1
                ordinal = call_count["n"]
                input_texts = kwargs["input"]
                return SimpleNamespace(
                    status_code=200,
                    code="",
                    message="",
                    request_id=f"req-{ordinal}",
                    usage=usage_by_ordinal[ordinal],
                    output={"embeddings": [{"embedding": [0.1, 0.2]} for _ in input_texts]},
                )

        with patch("app.infra.bailian_embedding.resolve_embedding_config") as mock_resolve, \
             patch("app.infra.bailian_embedding.dashscope.TextEmbedding", FakeTextEmbedding):
            mock_resolve.return_value = ("text-embedding-v4", 1024, "test-key")
            texts = [f"text {i}" for i in range(26)]
            result = await embed_texts_with_metadata(texts)

        assert result.batch_count == 3
        assert len(result.provider_metadata["batches"]) == 3
        assert [b["input_count"] for b in result.provider_metadata["batches"]] == [10, 10, 6]
        # Independent literal expectations (not re-derived from slicing algo).
        # texts = [f"text {i}" for i in range(26)] -> "text 0".."text 9" len=6,
        # "text 10".."text 25" len=7. Batches: [0..9], [10..19], [20..25].
        expected_input_chars = [60, 70, 42]
        actual_input_chars = [b["input_chars"] for b in result.provider_metadata["batches"]]
        assert actual_input_chars == expected_input_chars

    @pytest.mark.anyio
    async def test_embed_texts_with_metadata_unknown_model_fails_closed_with_safe_message(self):
        """Malicious unknown model must not leak into the fixed error message."""
        from app.infra.bailian_embedding import EmbeddingError, embed_texts_with_metadata

        malicious_model = (
            "text-embedding-v4\n"
            "api-key=sk-1234567890abcdef\n"
            "https://evil.example.com/path?token=secret\n"
            "<script>alert('xss')</script>"
        )

        class FailIfCalled:
            @staticmethod
            def call(**kwargs):
                raise AssertionError("DashScope TextEmbedding.call must not be invoked")

        with patch("app.infra.bailian_embedding.resolve_embedding_config") as mock_resolve, \
             patch("app.infra.bailian_embedding.dashscope.TextEmbedding", FailIfCalled):
            mock_resolve.return_value = ("text-embedding-v4", 1024, "test-key")
            with pytest.raises(EmbeddingError) as exc_info:
                await embed_texts_with_metadata(["a", "b"], model=malicious_model)

        assert exc_info.value.retryable is False
        assert exc_info.value.provider_code is None
        message = str(exc_info.value)
        # Fixed safe message; no interpolation of caller-supplied model value.
        assert message == (
            "embedding model capability is not registered; cannot determine safe batch size"
        )
        # No sensitive fragment of the malicious model may appear.
        assert "sk-1234567890abcdef" not in message
        assert "evil.example.com" not in message
        assert "api-key=" not in message
        assert "https" not in message
        assert "secret" not in message
        assert "<script>" not in message
        assert "\n" not in message

    @pytest.mark.anyio
    async def test_embed_texts_with_metadata_empty_input_bypasses_capability_check(self):
        """Empty input short-circuits before capability lookup; uses unregistered v5."""
        from app.infra.bailian_embedding import embed_texts_with_metadata

        class FailIfCalled:
            @staticmethod
            def call(**kwargs):
                raise AssertionError("DashScope TextEmbedding.call must not be invoked")

        with patch("app.infra.bailian_embedding.resolve_embedding_config") as mock_resolve, \
             patch("app.infra.bailian_embedding.dashscope.TextEmbedding", FailIfCalled):
            mock_resolve.return_value = ("text-embedding-v5", 1536, "test-key")

            result = await embed_texts_with_metadata([], model="text-embedding-v5")

        assert result.input_count == 0
        assert result.batch_count == 0
        assert result.model == "text-embedding-v5"
        assert result.dimension == 1536
        assert result.embeddings == []

    @pytest.mark.anyio
    async def test_embed_texts_with_metadata_400_invalid_parameter_is_non_retryable(self):
        """Provider 400/InvalidParameter -> retryable=False, safe message, no leaks."""
        from app.infra.bailian_embedding import EmbeddingError, embed_texts_with_metadata

        class FakeTextEmbedding:
            @staticmethod
            def call(**kwargs):
                return SimpleNamespace(
                    status_code=400,
                    code="InvalidParameter",
                    message="raw sdk leak: api_key=sk-leak input=x",
                    output={"embeddings": []},
                )

        with patch("app.infra.bailian_embedding.resolve_embedding_config") as mock_resolve, \
             patch("app.infra.bailian_embedding.dashscope.TextEmbedding", FakeTextEmbedding):
            mock_resolve.return_value = ("text-embedding-v4", 1024, "test-key")
            with pytest.raises(EmbeddingError) as exc_info:
                await embed_texts_with_metadata(["x"])

        assert exc_info.value.status_code == 400
        assert exc_info.value.provider_code == "InvalidParameter"
        assert exc_info.value.retryable is False
        message = str(exc_info.value)
        assert "test-key" not in message
        assert "x" not in message
        assert "raw sdk leak" not in message
        assert "api_key=sk-leak" not in message

    @pytest.mark.anyio
    async def test_embed_texts_with_metadata_provider_code_malicious_values_sanitized_to_none(self):
        """P0 safe-return TDD: malicious ``resp.code`` must NOT leak.

        The fake SDK response carries a hostile ``code`` value that
        contains spaces, a URI, an API-key sentinel, and exceeds 64
        characters.  The wrapper MUST:

        * keep ``status_code = 400`` (int 100–599)
        * keep ``retryable = False`` (existing safe retryability fallback)
        * set ``provider_code = None`` (whitelist rejected the value)
        * keep ``failed_batch_ordinal`` / ``batch_count`` correct
        * leave ``str(exc)``, ``repr(exc)``, ``vars(exc)`` and the
          formatted traceback free of every sentinel

        RED before fix: the wrapper only checks
        ``isinstance(resp.code, str)`` and forwards the raw value, so
        ``provider_code`` carries the hostile content and surfaces in
        str/repr/vars/traceback.
        """
        from app.infra.bailian_embedding import EmbeddingError, embed_texts_with_metadata

        sentinel_api_key = "sk-malicious-code-api-key-sentinel"
        sentinel_uri = "https://malicious-code-uri.example/leak?token=secret"
        sentinel_upstream = "raw upstream SDK sentinel with api_key and uri"

        malicious_code = (
            f"Error api_key={sentinel_api_key} uri={sentinel_uri} "
            f"upstream={sentinel_upstream} "
            + "X" * 80  # exceed 64-char limit
        )

        class FakeTextEmbedding:
            @staticmethod
            def call(**kwargs):
                return SimpleNamespace(
                    status_code=400,
                    code=malicious_code,
                    message=(
                        f"raw sdk message api_key={sentinel_api_key} "
                        f"uri={sentinel_uri} upstream={sentinel_upstream}"
                    ),
                    output={"embeddings": []},
                )

        with patch("app.infra.bailian_embedding.resolve_embedding_config") as mock_resolve, \
             patch("app.infra.bailian_embedding.dashscope.TextEmbedding", FakeTextEmbedding):
            mock_resolve.return_value = ("text-embedding-v4", 1024, "test-key")
            # 26 inputs -> 3 batches of 10/10/6, fail on first batch.
            texts = [f"text-{i}" for i in range(26)]
            with pytest.raises(EmbeddingError) as exc_info:
                await embed_texts_with_metadata(texts)

        exc = exc_info.value
        # Whitelist outcome.
        assert exc.status_code == 400
        assert exc.provider_code is None
        assert exc.retryable is False
        assert exc.failed_batch_ordinal == 1
        assert exc.batch_count == 3

        sentinels = [sentinel_api_key, sentinel_uri, sentinel_upstream, malicious_code]

        # str / repr / vars must all be sentinel-free.
        for rendered in (str(exc), repr(exc), repr(vars(exc))):
            for s in sentinels:
                assert s not in rendered, (
                    f"sentinel {s!r} leaked into EmbeddingError rendering: "
                    f"{rendered!r}"
                )

        # Traceback serialisation must also be sentinel-free.
        tb_text = "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        )
        for s in sentinels:
            assert s not in tb_text, (
                f"sentinel {s!r} leaked into traceback.format_exception: "
                f"{tb_text!r}"
            )

    @pytest.mark.anyio
    async def test_embed_texts_with_metadata_provider_status_invalid_sanitized_to_none(self):
        """P0 safe-return TDD: non-int / out-of-range status -> None + safe retryability.

        The wrapper MUST coerce ``status_code`` to ``int | None`` in the
        100–599 range; anything else is set to None and the existing
        retryability fallback applies (None status -> retryable=True).
        """
        from app.infra.bailian_embedding import EmbeddingError, embed_texts_with_metadata

        class FakeTextEmbedding:
            @staticmethod
            def call(**kwargs):
                return SimpleNamespace(
                    status_code=99,  # out of 100–599 range
                    code="InvalidParameter",
                    message="",
                    output={"embeddings": []},
                )

        with patch("app.infra.bailian_embedding.resolve_embedding_config") as mock_resolve, \
             patch("app.infra.bailian_embedding.dashscope.TextEmbedding", FakeTextEmbedding):
            mock_resolve.return_value = ("text-embedding-v4", 1024, "test-key")
            with pytest.raises(EmbeddingError) as exc_info:
                await embed_texts_with_metadata(["x"])

        exc = exc_info.value
        # status_code 99 is below 100 -> sanitized to None.
        assert exc.status_code is None
        # provider_code is still whitelisted (valid value).
        assert exc.provider_code == "InvalidParameter"
        # Existing retryability fallback: None status -> retryable=True.
        assert exc.retryable is True

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("malicious_code", "label"),
        [
            # Key-like: passes legacy isalnum + '-' check but MUST be
            # rejected by the explicit provider-code allowlist.
            ("sk-1234567890abcdef", "key_like"),
            # Arbitrary Unicode alnum: passes Python ``str.isalnum()``
            # (CJK characters are alphanumeric) but MUST be rejected
            # because it is not in the known DashScope code allowlist.
            ("密钥123", "unicode_alnum"),
        ],
        ids=["key_like", "unicode_alnum"],
    )
    async def test_embed_texts_with_metadata_provider_code_allowlist_rejects(
        self, malicious_code: str, label: str
    ):
        """P0 Round 3 TDD: provider_code uses an explicit allowlist.

        Legacy ``isalnum()`` + length check accepts both key-like
        strings (``sk-1234567890abcdef``) and arbitrary Unicode
        alphanumeric strings (``密钥123``) because Python's
        ``str.isalnum()`` returns True for CJK characters.  Both MUST
        be rejected by an explicit DashScope provider-code allowlist
        and sanitised to ``None``.

        The sentinel MUST NOT appear in:
          * ``str(exc)``
          * ``repr(exc)``
          * ``repr(vars(exc))``
          * ``traceback.format_exception(exc)``
        """
        from app.infra.bailian_embedding import EmbeddingError, embed_texts_with_metadata

        class FakeTextEmbedding:
            @staticmethod
            def call(**kwargs):
                return SimpleNamespace(
                    status_code=400,
                    code=malicious_code,
                    message=f"raw sdk echo code={malicious_code}",
                    output={"embeddings": []},
                )

        with patch("app.infra.bailian_embedding.resolve_embedding_config") as mock_resolve, \
             patch("app.infra.bailian_embedding.dashscope.TextEmbedding", FakeTextEmbedding):
            mock_resolve.return_value = ("text-embedding-v4", 1024, "test-key")
            with pytest.raises(EmbeddingError) as exc_info:
                await embed_texts_with_metadata(["x"])

        exc = exc_info.value
        # Allowlist rejects -> None.
        assert exc.provider_code is None, (
            f"label={label}: provider_code must be None for malicious code, "
            f"got {exc.provider_code!r}"
        )
        # status_code + retryability still correct.
        assert exc.status_code == 400
        assert exc.retryable is False

        sentinels = [malicious_code]
        for rendered in (str(exc), repr(exc), repr(vars(exc))):
            for s in sentinels:
                assert s not in rendered, (
                    f"label={label}: sentinel {s!r} leaked into rendering: "
                    f"{rendered!r}"
                )

        tb_text = "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        )
        for s in sentinels:
            assert s not in tb_text, (
                f"label={label}: sentinel {s!r} leaked into traceback: "
                f"{tb_text!r}"
            )

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "known_code",
        ["InvalidParameter", "Throttling.User"],
        ids=["invalid_parameter", "throttling_user"],
    )
    async def test_embed_texts_with_metadata_provider_code_allowlist_keeps_known(
        self, known_code: str
    ):
        """P0 Round 3 TDD: known DashScope codes are retained by the allowlist."""
        from app.infra.bailian_embedding import EmbeddingError, embed_texts_with_metadata

        class FakeTextEmbedding:
            @staticmethod
            def call(**kwargs):
                return SimpleNamespace(
                    status_code=400,
                    code=known_code,
                    message="",
                    output={"embeddings": []},
                )

        with patch("app.infra.bailian_embedding.resolve_embedding_config") as mock_resolve, \
             patch("app.infra.bailian_embedding.dashscope.TextEmbedding", FakeTextEmbedding):
            mock_resolve.return_value = ("text-embedding-v4", 1024, "test-key")
            with pytest.raises(EmbeddingError) as exc_info:
                await embed_texts_with_metadata(["x"])

        assert exc_info.value.provider_code == known_code

    @pytest.mark.anyio
    async def test_embed_texts_with_metadata_sdk_call_raises_is_caught_and_sanitized(self):
        """P0 SDK-raise closure TDD: SDK call raises RuntimeError directly.

        The DashScope SDK can fail BEFORE returning a response object —
        e.g. on transport/auth/serialisation errors that surface as a
        plain ``RuntimeError`` carrying sensitive content (API key,
        chunk text, URI, raw upstream error message).  The wrapper MUST:

        * catch the ordinary ``Exception`` (NOT ``BaseException``)
        * NOT copy/forward the original exception's message, type name,
          repr, args, or any SDK object
        * raise a fixed safe ``EmbeddingError`` whose message is a
          local fixed literal, ``retryable=True``, ``status_code=None``,
          ``provider_code=None``
        * still let the outer loop populate ``failed_batch_ordinal`` and
          ``batch_count``
        * leave ``__cause__`` and ``__context__`` as ``None`` (no
          ``raise ... from exc``, no ``raise ... from None``, no
          implicit chain)

        RED before fix: ``_call_embedding_sync`` does not wrap the SDK
        call in a try/except, so the SDK's ``RuntimeError`` propagates
        verbatim through ``embed_texts_with_metadata`` and surfaces as
        the raw exception type with sentinels in its message/traceback.
        26 inputs produce 3 batches (10/10/6); the raise happens on
        the first batch.
        """
        from app.infra.bailian_embedding import EmbeddingError, embed_texts_with_metadata

        sentinel_api_key = "sk-sdk-raise-api-key-sentinel"
        sentinel_chunk_text = "SENTINEL-SDK-RAISE-CHUNK-DO-NOT-LEAK"
        sentinel_uri = "https://sdk-raise-uri.example/path?token=secret"
        sentinel_upstream = "raw upstream SDK message with api_key and uri"

        class RaisingTextEmbedding:
            @staticmethod
            def call(**kwargs):
                raise RuntimeError(
                    f"dashscope sdk direct raise api_key={sentinel_api_key} "
                    f"chunk={sentinel_chunk_text} uri={sentinel_uri} "
                    f"upstream={sentinel_upstream}"
                )

        with patch("app.infra.bailian_embedding.resolve_embedding_config") as mock_resolve, \
             patch("app.infra.bailian_embedding.dashscope.TextEmbedding", RaisingTextEmbedding):
            mock_resolve.return_value = ("text-embedding-v4", 1024, "test-key")
            texts = [f"text-{i}" for i in range(26)]
            with pytest.raises(EmbeddingError) as exc_info:
                await embed_texts_with_metadata(texts)

        exc = exc_info.value
        # Fixed local safe message — no interpolation.
        assert str(exc) == (
            "embedding provider call failed before a response was available"
        )
        # Retryable, no status, no code (SDK never returned a response).
        assert exc.retryable is True
        assert exc.status_code is None
        assert exc.provider_code is None
        # Outer loop still populates capability-aware batch metadata
        # (26 inputs / 10 per batch -> ordinal 1, count 3).
        assert exc.failed_batch_ordinal == 1
        assert exc.batch_count == 3
        # No exception chain.
        assert exc.__cause__ is None
        assert exc.__context__ is None

        sentinels = [
            sentinel_api_key,
            sentinel_chunk_text,
            sentinel_uri,
            sentinel_upstream,
        ]
        # str / repr / vars must all be sentinel-free.
        for rendered in (str(exc), repr(exc), repr(vars(exc))):
            for s in sentinels:
                assert s not in rendered, (
                    f"sentinel {s!r} leaked into EmbeddingError rendering: "
                    f"{rendered!r}"
                )

        # Traceback serialisation must also be sentinel-free.
        tb_text = "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        )
        for s in sentinels:
            assert s not in tb_text, (
                f"sentinel {s!r} leaked into traceback.format_exception: "
                f"{tb_text!r}"
            )


class TestRerankClient:
    def test_resolve_rerank_config_raises_on_incompatible_adapter(self):
        from app.infra.bailian_rerank import RerankError, resolve_rerank_config

        with patch("app.infra.bailian_rerank.get_settings", return_value=SimpleNamespace()), patch(
            "app.llm.router.resolve_model_config",
            return_value=SimpleNamespace(adapter="openai_compatible"),
        ):
            with pytest.raises(RerankError, match="incompatible adapter"):
                resolve_rerank_config()

    @pytest.mark.anyio
    async def test_rerank_raises_when_no_api_key(self):
        from app.infra.bailian_rerank import RerankError, rerank
        with patch("app.infra.bailian_rerank.resolve_rerank_config") as mock_resolve:
            mock_resolve.return_value = ("qwen3-rerank", "")
            with pytest.raises(RerankError, match="No API key"):
                await rerank("test query", ["doc1", "doc2"])

    @pytest.mark.anyio
    async def test_rerank_returns_empty_for_empty_documents(self):
        from app.infra.bailian_rerank import rerank
        result = await rerank("test query", [])
        assert result == []

    @pytest.mark.anyio
    async def test_rerank_returns_sorted_results(self):
        from app.infra.bailian_rerank import RerankResult, _RerankBatchResult, rerank

        mock_results = [
            RerankResult(index=1, relevance_score=0.95, document="doc2"),
            RerankResult(index=0, relevance_score=0.80, document="doc1"),
        ]

        with patch("app.infra.bailian_rerank.resolve_rerank_config") as mock_resolve, \
             patch("app.infra.bailian_rerank._call_rerank_sync") as mock_call:
            mock_resolve.return_value = ("qwen3-rerank", "test_key")
            mock_call.return_value = _RerankBatchResult(
                results=mock_results,
                usage_data={},
                provider_metadata={},
            )

            result = await rerank("test query", ["doc1", "doc2"], top_n=2)
            assert len(result) == 2
            assert result[0].relevance_score == 0.95
            assert result[1].relevance_score == 0.80

    @pytest.mark.anyio
    async def test_rerank_with_metadata_empty_documents_uses_resolved_config(self):
        from app.infra.bailian_rerank import rerank_with_metadata

        with patch("app.infra.bailian_rerank.resolve_rerank_config") as mock_resolve:
            mock_resolve.return_value = ("qwen3-rerank-v2", "test_key")

            result = await rerank_with_metadata("query", [])

            assert result.model == "qwen3-rerank-v2"
            assert result.top_n == 0
            assert result.input_count == 0
