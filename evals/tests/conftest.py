"""Real-LLM fail-closed gate for the evals pytest project.

Mirrors the triple gate in ``services/api/tests/conftest.py``: a test
marked ``@pytest.mark.real_llm`` runs ONLY when ALL three conditions
hold:

1. ``CLAREAD_ALLOW_REAL_LLM_TESTS=1`` (explicit opt-in env)
2. ``CLAREAD_REAL_LLM_MODEL=<model>`` (authorized model name)
3. pytest is invoked with exactly ``-m real_llm`` (explicit selection)

evals is an independent pytest project; ``services/api`` conftest
fixtures do NOT apply here. When the gate is closed this conftest
monkeypatches the same production provider boundaries and fails the
test after the fact if any blocked real-provider attempt was recorded.

The guard degrades safely in minimal environments: when the
``services/api`` production modules are not importable (missing
third-party deps), the provider boundaries are unreachable anyway and
only the tracing-env lockdown applies.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SERVICES_API_DIR = _REPO_ROOT / "services" / "api"

# Same bootstrap the evals test modules use individually; done here once
# so the guard fixtures can reach ``app.llm.call_guard`` and the provider
# boundary modules.
if str(_SERVICES_API_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICES_API_DIR))

_REAL_LLM_ALLOW_ENV = "CLAREAD_ALLOW_REAL_LLM_TESTS"
_REAL_LLM_MODEL_ENV = "CLAREAD_REAL_LLM_MODEL"
_TRUTHY_ENV_VALUES = {"1", "true", "yes", "on"}

_LANGSMITH_ENV_DEFAULTS = {
    "LANGSMITH_ENABLED": "false",
    "LANGSMITH_TRACING": "false",
    "LANGSMITH_TRACING_V2": "false",
    "LANGSMITH_OTEL_ENABLED": "false",
    "LANGCHAIN_TRACING": "false",
    "LANGCHAIN_TRACING_V2": "false",
}


def _import_call_guard():
    try:
        from app.llm import call_guard
    except ImportError:
        return None
    return call_guard


def _is_real_llm_test(request) -> bool:
    return request.node.get_closest_marker("real_llm") is not None


def _real_llm_markexpr_selected(config) -> bool:
    # Keep this intentionally strict. Broaden only when we need a second
    # explicitly reviewed real-LLM marker expression.
    markexpr = str(config.getoption("markexpr", default="") or "").strip()
    return markexpr == "real_llm"


def _real_llm_tests_allowed(call_guard) -> bool:
    if call_guard is not None:
        return call_guard.real_llm_tests_allowed()
    return (
        os.getenv(_REAL_LLM_ALLOW_ENV, "").strip().lower()
        in _TRUTHY_ENV_VALUES
    )


def _real_llm_skip_reason(request, call_guard) -> str | None:
    if not _is_real_llm_test(request):
        return None
    if not _real_llm_tests_allowed(call_guard):
        return (
            f"real_llm test; set {_REAL_LLM_ALLOW_ENV}=1 and "
            f"{_REAL_LLM_MODEL_ENV}=<model> to enable"
        )
    if not os.environ.get(_REAL_LLM_MODEL_ENV):
        return (
            f"real_llm test; {_REAL_LLM_MODEL_ENV} must be set to the "
            "authorized model name"
        )
    if not _real_llm_markexpr_selected(request.config):
        return (
            "real_llm test; run with exactly -m real_llm to explicitly "
            "select real LLM tests"
        )
    return None


def _real_llm_gate_open(request, call_guard) -> bool:
    return (
        _is_real_llm_test(request)
        and _real_llm_skip_reason(request, call_guard) is None
    )


def _lockdown_tracing_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _LANGSMITH_ENV_DEFAULTS.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.delenv("LANGCHAIN_API_KEY", raising=False)


@pytest.fixture(autouse=True)
def skip_real_llm_tests(request):
    """Skip real_llm-marked tests unless the triple gate is fully open."""
    if not _is_real_llm_test(request):
        return
    reason = _real_llm_skip_reason(request, _import_call_guard())
    if reason is not None:
        pytest.skip(reason)


@pytest.fixture(autouse=True)
def fail_on_real_llm_attempts(monkeypatch: pytest.MonkeyPatch, request):
    """Block real provider boundaries unless the triple gate is open."""
    call_guard = _import_call_guard()
    if _real_llm_gate_open(request, call_guard):
        yield
        return

    _lockdown_tracing_env(monkeypatch)
    if call_guard is None:
        # services/api production package unavailable in this
        # environment; the provider boundaries cannot be reached.
        yield
        return

    call_guard.pop_blocked_real_llm_attempts()

    class _BlockedStructuredAsyncClient:
        def __init__(self, *args, **kwargs):
            call_guard.block_real_llm_attempt(
                "app.llm.structured_completion.httpx.AsyncClient",
            )

    class _BlockedAioGeneration:
        @staticmethod
        async def call(*args, **kwargs):
            call_guard.block_real_llm_attempt(
                "app.llm.dashscope_stream.AioGeneration.call",
                model_name=str(kwargs.get("model") or "unknown"),
            )

    class _BlockedTextEmbedding:
        @staticmethod
        def call(*args, **kwargs):
            call_guard.block_real_llm_attempt(
                "app.infra.bailian_embedding.dashscope.TextEmbedding.call",
                route="rag_embedding",
                provider="dashscope_embedding",
                model_name=str(kwargs.get("model") or "unknown"),
            )

    class _BlockedTextReRank:
        @staticmethod
        def call(*args, **kwargs):
            call_guard.block_real_llm_attempt(
                "app.infra.bailian_rerank.dashscope.TextReRank.call",
                route="rag_rerank",
                provider="dashscope_rerank",
                model_name=str(kwargs.get("model") or "unknown"),
            )

    try:
        from app.llm import dashscope_stream, structured_completion

        real_httpx = structured_completion.httpx
        monkeypatch.setattr(
            structured_completion,
            "httpx",
            SimpleNamespace(
                AsyncClient=_BlockedStructuredAsyncClient,
                HTTPStatusError=real_httpx.HTTPStatusError,
                TimeoutException=real_httpx.TimeoutException,
                RequestError=real_httpx.RequestError,
            ),
        )
        monkeypatch.setattr(
            dashscope_stream, "AioGeneration", _BlockedAioGeneration
        )
    except ImportError:
        pass
    try:
        from app.infra import bailian_embedding, bailian_rerank

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
    except ImportError:
        pass

    yield
    _lockdown_tracing_env(monkeypatch)

    attempts = call_guard.pop_blocked_real_llm_attempts()
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
        f"{_REAL_LLM_ALLOW_ENV}=1. Attempts: {details}"
    )
