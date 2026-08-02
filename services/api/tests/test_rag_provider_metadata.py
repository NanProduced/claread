"""Regression tests for provider metadata used by neutral RAG adapters."""

from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.mark.anyio
async def test_bailian_embedding_metadata_keeps_legacy_return(monkeypatch):
    from app.infra import bailian_embedding

    class FakeTextEmbedding:
        @staticmethod
        def call(**kwargs):
            assert kwargs["model"] == "text-embedding-v4"
            return SimpleNamespace(
                status_code=200,
                code="",
                message="",
                request_id="emb-1",
                usage={"input_tokens": 11, "total_tokens": 11},
                output={"embeddings": [{"embedding": [0.1, 0.2]}]},
            )

    monkeypatch.setattr(
        bailian_embedding,
        "resolve_embedding_config",
        lambda: ("text-embedding-v4", 1024, "test-key"),
    )
    monkeypatch.setattr(bailian_embedding.dashscope, "TextEmbedding", FakeTextEmbedding)

    legacy = await bailian_embedding.embed_texts(["hello"])
    enriched = await bailian_embedding.embed_texts_with_metadata(["hello"])

    assert legacy == [[0.1, 0.2]]
    assert enriched.embeddings == [[0.1, 0.2]]
    assert enriched.usage_data["aggregate"]["total_tokens"] == 11
    assert enriched.usage_data["provider_usage_available"] is True
    assert enriched.provider_metadata["provider_usage_available"] is True
    assert enriched.provider_metadata["batches"][0]["request_id"] == "emb-1"


@pytest.mark.anyio
async def test_bailian_rerank_metadata_handles_missing_usage(monkeypatch):
    from app.infra import bailian_rerank

    class FakeTextReRank:
        @staticmethod
        def call(**kwargs):
            assert kwargs["model"] == "qwen3-rerank"
            return SimpleNamespace(
                status_code=200,
                code="",
                message="",
                request_id="rerank-1",
                output={
                    "results": [
                        {
                            "index": 0,
                            "relevance_score": 0.91,
                            "document": {"text": "candidate"},
                        }
                    ]
                },
            )

    monkeypatch.setattr(
        bailian_rerank,
        "resolve_rerank_config",
        lambda: ("qwen3-rerank", "test-key"),
    )
    monkeypatch.setattr(bailian_rerank.dashscope, "TextReRank", FakeTextReRank)

    legacy = await bailian_rerank.rerank("query", ["candidate"])
    enriched = await bailian_rerank.rerank_with_metadata("query", ["candidate"])

    assert legacy[0].relevance_score == 0.91
    assert enriched.results[0].index == 0
    assert enriched.usage_data["aggregate"]["total_tokens"] == 0
    assert enriched.usage_data["provider_usage_available"] is False
    assert enriched.provider_metadata["provider_usage_available"] is False
    assert enriched.provider_metadata["request_id"] == "rerank-1"
