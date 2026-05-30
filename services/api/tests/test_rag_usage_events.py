from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

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
        "get_settings",
        lambda: SimpleNamespace(bailian_api_key="test-key"),
    )
    monkeypatch.setattr(
        bailian_embedding.dashscope,
        "TextEmbedding",
        FakeTextEmbedding,
    )

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
        "get_settings",
        lambda: SimpleNamespace(bailian_api_key="test-key"),
    )
    monkeypatch.setattr(
        bailian_rerank.dashscope,
        "TextReRank",
        FakeTextReRank,
    )

    legacy = await bailian_rerank.rerank("query", ["candidate"])
    enriched = await bailian_rerank.rerank_with_metadata("query", ["candidate"])

    assert legacy[0].relevance_score == 0.91
    assert enriched.results[0].index == 0
    assert enriched.usage_data["aggregate"]["total_tokens"] == 0
    assert enriched.usage_data["provider_usage_available"] is False
    assert enriched.provider_metadata["provider_usage_available"] is False
    assert enriched.provider_metadata["request_id"] == "rerank-1"


@pytest.mark.anyio
async def test_record_rag_usage_events_aggregates_two_capabilities(monkeypatch):
    from app.services.analysis import rag_usage_events

    usage_mock = AsyncMock()
    monkeypatch.setattr(rag_usage_events, "record_ai_usage_event", usage_mock)

    await rag_usage_events.record_rag_usage_events_from_result(
        result={
            "rag_debug": {
                "agents": {
                    "grammar": {
                        "grammar_note": {
                            "embedding_model": "text-embedding-v4",
                            "embedding_usage": {
                                "provider_usage_available": True,
                                "aggregate": {"input_tokens": 5, "output_tokens": 0, "total_tokens": 5},
                            },
                            "embedding_latency_ms": 12.4,
                            "embedding_input_count": 1,
                            "embedding_input_chars": 20,
                            "rerank_model": "qwen3-rerank",
                            "rerank_usage": {
                                "provider_usage_available": True,
                                "aggregate": {"input_tokens": 7, "output_tokens": 0, "total_tokens": 7},
                            },
                            "rerank_latency_ms": 30.1,
                            "rerank_input_count": 4,
                            "rerank_input_chars": 120,
                        },
                        "sentence_analysis": {
                            "embedding_model": "text-embedding-v4",
                            "embedding_usage": {
                                "provider_usage_available": False,
                                "aggregate": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                            },
                            "embedding_latency_ms": 10,
                        },
                    }
                }
            }
        },
        user_id=uuid4(),
        task_id=uuid4(),
        record_id=uuid4(),
        request_id="req-1",
        workflow_name="article_analysis",
        workflow_version="3.0.0",
        schema_version="3.0.0",
        prompt_version="prompt-1",
        metadata_json={"entrypoint": "test"},
    )

    assert usage_mock.await_count == 2
    events = [call.args[0] for call in usage_mock.await_args_list]
    by_capability = {event.capability_code: event for event in events}
    assert by_capability["rag_embedding"].usage_data["aggregate"]["total_tokens"] == 5
    assert by_capability["rag_embedding"].metadata_json["call_count"] == 2
    assert by_capability["rag_rerank"].usage_data["aggregate"]["total_tokens"] == 7
    assert by_capability["rag_rerank"].model_provider == "bailian"
    assert by_capability["rag_rerank"].billing_mode == "internal_only"


@pytest.mark.anyio
async def test_record_rag_usage_events_suppresses_write_failures(monkeypatch):
    from app.services.analysis import rag_usage_events

    async def fail_record(*args, **kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(rag_usage_events, "record_ai_usage_event", fail_record)

    await rag_usage_events.record_rag_usage_events_from_result(
        result={
            "rag_debug": {
                "agents": {
                    "grammar": {
                        "grammar_note": {
                            "embedding_model": "text-embedding-v4",
                            "embedding_usage": {
                                "provider_usage_available": False,
                                "aggregate": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                            },
                        }
                    }
                }
            }
        },
        user_id=None,
        task_id=None,
        record_id=None,
        request_id=None,
        workflow_name="article_analysis",
        workflow_version="3.0.0",
        schema_version="3.0.0",
        prompt_version="prompt-1",
    )
