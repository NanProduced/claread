from __future__ import annotations

import pytest

from app.llm.call_guard import (
    block_real_llm_attempt,
    pop_blocked_real_llm_attempts,
)

_LANGSMITH_ENV_DEFAULTS = {
    "LANGSMITH_ENABLED": "false",
    "LANGSMITH_TRACING": "false",
    "LANGSMITH_TRACING_V2": "false",
    "LANGSMITH_OTEL_ENABLED": "false",
    "LANGCHAIN_TRACING": "false",
    "LANGCHAIN_TRACING_V2": "false",
}


@pytest.fixture(autouse=True)
def fail_on_real_llm_attempts(monkeypatch: pytest.MonkeyPatch):
    pop_blocked_real_llm_attempts()
    for key, value in _LANGSMITH_ENV_DEFAULTS.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.delenv("LANGCHAIN_API_KEY", raising=False)

    from app.infra import bailian_embedding, bailian_rerank
    from app.llm import dashscope_stream, structured_completion

    class _BlockedStructuredAsyncClient:
        def __init__(self, *args, **kwargs):
            block_real_llm_attempt(
                "app.llm.structured_completion.httpx.AsyncClient",
            )

    class _BlockedAioGeneration:
        @staticmethod
        async def call(*args, **kwargs):
            block_real_llm_attempt(
                "app.llm.dashscope_stream.AioGeneration.call",
                model_name=str(kwargs.get("model") or "unknown"),
            )

    class _BlockedTextEmbedding:
        @staticmethod
        def call(*args, **kwargs):
            block_real_llm_attempt(
                "app.infra.bailian_embedding.dashscope.TextEmbedding.call",
                route="rag_embedding",
                provider="dashscope_embedding",
                model_name=str(kwargs.get("model") or "unknown"),
            )

    class _BlockedTextReRank:
        @staticmethod
        def call(*args, **kwargs):
            block_real_llm_attempt(
                "app.infra.bailian_rerank.dashscope.TextReRank.call",
                route="rag_rerank",
                provider="dashscope_rerank",
                model_name=str(kwargs.get("model") or "unknown"),
            )

    monkeypatch.setattr(
        structured_completion.httpx,
        "AsyncClient",
        _BlockedStructuredAsyncClient,
    )
    monkeypatch.setattr(dashscope_stream, "AioGeneration", _BlockedAioGeneration)
    monkeypatch.setattr(
        bailian_embedding.dashscope,
        "TextEmbedding",
        _BlockedTextEmbedding,
        raising=False,
    )
    monkeypatch.setattr(
        bailian_rerank.dashscope,
        "TextReRank",
        _BlockedTextReRank,
        raising=False,
    )

    yield
    for key, value in _LANGSMITH_ENV_DEFAULTS.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.delenv("LANGCHAIN_API_KEY", raising=False)

    attempts = pop_blocked_real_llm_attempts()
    if not attempts:
        return

    details = "; ".join(
        (
            f"{attempt.surface} route={attempt.route} "
            f"profile={attempt.profile_name} provider={attempt.provider} "
            f"model={attempt.model_name}"
        )
        for attempt in attempts
    )
    pytest.fail(
        "Test attempted to call a real LLM provider. "
        "Mock the LLM boundary or run an explicit integration test with "
        f"CLAREAD_ALLOW_REAL_LLM_TESTS=1. Attempts: {details}"
    )
