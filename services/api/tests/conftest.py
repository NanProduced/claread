from __future__ import annotations

import os

import pytest

from app.llm.call_guard import (
    block_real_llm_attempt,
    pop_blocked_real_llm_attempts,
    real_llm_tests_allowed,
)

_LANGSMITH_ENV_DEFAULTS = {
    "LANGSMITH_ENABLED": "false",
    "LANGSMITH_TRACING": "false",
    "LANGSMITH_TRACING_V2": "false",
    "LANGSMITH_OTEL_ENABLED": "false",
    "LANGCHAIN_TRACING": "false",
    "LANGCHAIN_TRACING_V2": "false",
}

_REAL_LLM_MODEL_ENV = "CLAREAD_REAL_LLM_MODEL"


def _is_real_llm_test(request) -> bool:
    return request.node.get_closest_marker("real_llm") is not None


def _real_llm_markexpr_selected(config) -> bool:
    # Keep this intentionally strict.  Broaden only when we need a second
    # explicitly reviewed real-LLM marker expression.
    markexpr = str(config.getoption("markexpr", default="") or "").strip()
    return markexpr == "real_llm"


def _real_llm_skip_reason(request) -> str | None:
    if not _is_real_llm_test(request):
        return None
    if not real_llm_tests_allowed():
        return (
            "real_llm test; set CLAREAD_ALLOW_REAL_LLM_TESTS=1 and "
            f"{_REAL_LLM_MODEL_ENV}=<model> to enable"
        )
    if not os.environ.get(_REAL_LLM_MODEL_ENV):
        return (
            f"real_llm test; {_REAL_LLM_MODEL_ENV} must be set to the "
            f"authorized model name (e.g. {_REAL_LLM_MODEL_ENV}=qwen-plus)"
        )
    if not _real_llm_markexpr_selected(request.config):
        return (
            "real_llm test; run with exactly -m real_llm to explicitly select "
            "real LLM tests"
        )
    return None


def _real_llm_gate_open(request) -> bool:
    return _is_real_llm_test(request) and _real_llm_skip_reason(request) is None


@pytest.fixture(autouse=True)
def skip_real_llm_tests(request):
    """Skip tests marked @pytest.mark.real_llm unless ALL three conditions are met:

    1. CLAREAD_ALLOW_REAL_LLM_TESTS=1 is set
    2. CLAREAD_REAL_LLM_MODEL is set (specifies which model to use)
    3. -m real_llm is passed to pytest (mark expression explicitly selects real_llm)

    This triple gate ensures:
    - A stale env var alone never silently enables real LLM tests
    - The user must explicitly name the model they authorized
    - The pytest mark expression must explicitly opt in
    """
    if not _is_real_llm_test(request):
        return

    reason = _real_llm_skip_reason(request)
    if reason is not None:
        pytest.skip(reason)


@pytest.fixture(autouse=True)
def fail_on_real_llm_attempts(monkeypatch: pytest.MonkeyPatch, request):
    # If the test is marked real_llm AND all three gates passed (the test
    # was not skipped by skip_real_llm_tests), skip all monkeypatching so
    # the real provider can be reached.
    if _real_llm_gate_open(request):
        yield
        return

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
