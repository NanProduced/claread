from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.llm import agent_runner
from app.llm.call_guard import (
    ALLOW_REAL_LLM_TESTS_ENV,
    RealLLMCallBlockedError,
    pop_blocked_real_llm_attempts,
)
from app.llm.routes import MODEL_ROUTE_ANNOTATION_GENERATION


class _FakeAgent:
    async def run(self, *args, **kwargs):
        raise AssertionError("agent.run should be blocked before execution")


class _AllowedFakeAgent:
    async def run(self, *args, **kwargs):
        return SimpleNamespace(output="ok", usage=lambda: None)


def _realish_model_config() -> SimpleNamespace:
    return SimpleNamespace(
        route=MODEL_ROUTE_ANNOTATION_GENERATION,
        profile_name="real-profile",
        provider="dashscope",
        model_name="qwen-test",
        api_key="secret-key",
    )


async def test_run_agent_with_route_blocks_real_config_under_pytest(monkeypatch):
    config = _realish_model_config()
    monkeypatch.setattr(
        agent_runner,
        "build_model_for_route",
        lambda *_args, **_kwargs: (object(), config),
    )

    with pytest.raises(RealLLMCallBlockedError, match="Blocked real LLM call"):
        await agent_runner.run_agent_with_route(
            agent=_FakeAgent(),
            prompt="prompt",
            deps=None,
            route=MODEL_ROUTE_ANNOTATION_GENERATION,
        )

    attempts = pop_blocked_real_llm_attempts()
    assert len(attempts) == 1
    assert attempts[0].profile_name == "real-profile"
    assert attempts[0].model_name == "qwen-test"


async def test_run_agent_with_route_allows_explicit_integration_opt_in(monkeypatch):
    config = _realish_model_config()
    monkeypatch.setenv(ALLOW_REAL_LLM_TESTS_ENV, "1")
    monkeypatch.setattr(
        agent_runner,
        "build_model_for_route",
        lambda *_args, **_kwargs: (object(), config),
    )

    result = await agent_runner.run_agent_with_route(
        agent=_AllowedFakeAgent(),
        prompt="prompt",
        deps=None,
        route=MODEL_ROUTE_ANNOTATION_GENERATION,
    )

    assert result.output == "ok"
    assert result._resolved_model_config == config
