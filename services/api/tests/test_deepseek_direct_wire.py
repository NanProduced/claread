"""R4-A5-8A1R: Direct DeepSeek V4 wire correctness (offline HTTP capture).

Captures the actual OpenAI-compatible request JSON via an injectable
``httpx.AsyncClient`` transport — no process-global HTTP monkeypatch.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic_ai import Agent
from pydantic_ai.providers.openai import OpenAIProvider

from app.llm.deepseek_direct import DirectDeepSeekChatModel, deepseek_v4_openai_profile
from app.llm.provider_factory import build_model_instance
from app.llm.routes import MODEL_ROUTE_READER_ASK
from app.llm.thinking_capability import (
    ThinkingEffortConfigError,
    apply_thinking_to_model_settings,
    normalize_deepseek_direct_effort,
    resolve_thinking_capability,
)
from app.llm.types import ResolvedModelConfig, RunModelSettings

_SENTINEL = "SENTINEL_REASONING_WIRE_8A1R_NEVER_SSE"


class _CaptureTransport(httpx.AsyncBaseTransport):
    """Record request bodies without a process-global HTTP patch."""

    def __init__(self, response_json: dict[str, Any]) -> None:
        self.requests: list[dict[str, Any]] = []
        self._response_json = response_json

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = request.content.decode("utf-8") if request.content else ""
        payload = json.loads(body) if body else {}
        self.requests.append(payload)
        return httpx.Response(
            200,
            json=self._response_json,
            request=request,
        )


def _chat_completion_response(
    *,
    content: str = "ok",
    reasoning: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "role": "assistant",
        "content": content,
    }
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1,
        "model": "deepseek-v4-flash",
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if tool_calls else "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def _build_direct_model(
    *,
    thinking: bool,
    effort: str | None = "high",
    transport: _CaptureTransport,
) -> DirectDeepSeekChatModel:
    extra: dict[str, object] = {}
    if thinking:
        extra["thinking"] = {"type": "enabled"}
        if effort:
            extra["reasoning_effort"] = effort
    settings = RunModelSettings(extra_body=extra or None)
    cap = resolve_thinking_capability(
        adapter="openai_compatible",
        provider="deepseek",
        model_name="deepseek-v4-flash",
        base_url="https://api.deepseek.com",
        model_settings=settings,
    )
    normalized = apply_thinking_to_model_settings(settings, cap)
    http_client = httpx.AsyncClient(
        transport=transport,
        base_url="https://api.deepseek.com",
    )
    provider = OpenAIProvider(
        base_url="https://api.deepseek.com",
        api_key="test-key-not-real",
        http_client=http_client,
    )
    return DirectDeepSeekChatModel(
        "deepseek-v4-flash",
        provider=provider,
        profile=deepseek_v4_openai_profile(),
        settings=normalized.to_pydantic_ai() if normalized else None,
        thinking_enabled=thinking,
    )


def test_normalize_deepseek_effort_rules() -> None:
    assert normalize_deepseek_direct_effort("high") == "high"
    assert normalize_deepseek_direct_effort("max") == "max"
    assert normalize_deepseek_direct_effort("low") == "high"
    assert normalize_deepseek_direct_effort("medium") == "high"
    assert normalize_deepseek_direct_effort("xhigh") == "max"
    assert normalize_deepseek_direct_effort(None) is None
    with pytest.raises(ThinkingEffortConfigError):
        normalize_deepseek_direct_effort("turbo")


def test_apply_thinking_puts_effort_at_top_level_not_nested() -> None:
    settings = RunModelSettings(
        temperature=0.5,
        extra_body={
            "thinking": {"type": "enabled", "reasoning_effort": "max"},
        },
    )
    cap = resolve_thinking_capability(
        adapter="openai_compatible",
        provider="deepseek",
        model_name="deepseek-v4-pro",
        base_url="https://api.deepseek.com",
        model_settings=settings,
    )
    out = apply_thinking_to_model_settings(settings, cap)
    assert out is not None
    assert out.extra_body is not None
    assert out.extra_body["thinking"] == {"type": "enabled"}
    assert "reasoning_effort" not in out.extra_body["thinking"]
    assert out.extra_body["reasoning_effort"] == "max"
    assert out.temperature is None


@pytest.mark.asyncio
async def test_direct_deepseek_first_request_wire_json_thinking_and_no_tool_choice() -> None:
    transport = _CaptureTransport(
        _chat_completion_response(
            content="",
            reasoning=_SENTINEL,
            tool_calls=[
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "echo", "arguments": "{}"},
                }
            ],
        )
    )
    model = _build_direct_model(thinking=True, effort="high", transport=transport)

    async def echo(ctx: Any) -> str:
        return "pong"

    agent = Agent(model, tools=[echo], output_type=str)
    # Force a tool-bearing first response via model; agent will call tools.
    # We only care about the first wire request payload.
    try:
        await agent.run("call echo please")
    except Exception:
        # Tool loop may fail after first request; wire is already captured.
        pass

    assert transport.requests, "expected at least one HTTP request"
    first = transport.requests[0]
    # OpenAI SDK merges extra_body into the top-level JSON body.
    assert first.get("thinking") == {"type": "enabled"}
    assert first.get("reasoning_effort") == "high"
    thinking_obj = first.get("thinking")
    assert isinstance(thinking_obj, dict)
    assert "reasoning_effort" not in thinking_obj
    # tool_choice must be absent when thinking + tools.
    assert "tool_choice" not in first
    assert first.get("tools")  # tools present


@pytest.mark.asyncio
async def test_direct_deepseek_tool_continuation_carries_reasoning_content() -> None:
    """Second request after tool must include full reasoning_content on wire."""
    call_n = {"i": 0}

    def _response_for_request() -> dict[str, Any]:
        call_n["i"] += 1
        if call_n["i"] == 1:
            return _chat_completion_response(
                content="",
                reasoning=_SENTINEL,
                tool_calls=[
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {
                            "name": "echo",
                            "arguments": "{}",
                        },
                    }
                ],
            )
        return _chat_completion_response(content="done after tool")

    class _SeqTransport(httpx.AsyncBaseTransport):
        def __init__(self) -> None:
            self.requests: list[dict[str, Any]] = []

        async def handle_async_request(
            self, request: httpx.Request
        ) -> httpx.Response:
            body = request.content.decode("utf-8") if request.content else ""
            payload = json.loads(body) if body else {}
            self.requests.append(payload)
            return httpx.Response(
                200, json=_response_for_request(), request=request
            )

    transport = _SeqTransport()
    model = _build_direct_model(thinking=True, effort="max", transport=transport)

    async def echo(ctx: Any) -> str:
        return "pong"

    agent = Agent(model, tools=[echo], output_type=str)
    result = await agent.run("use the echo tool then answer")
    assert result.output  # completed

    assert len(transport.requests) >= 2
    second = transport.requests[1]
    messages = second.get("messages") or []
    assistants = [m for m in messages if m.get("role") == "assistant"]
    assert assistants
    # Find the tool-call assistant turn.
    tool_assistant = next(
        (m for m in assistants if m.get("tool_calls")), None
    )
    assert tool_assistant is not None
    assert tool_assistant.get("reasoning_content") == _SENTINEL
    assert isinstance(tool_assistant.get("content"), str)
    assert tool_assistant.get("content") is not None  # not JSON null
    assert tool_assistant.get("tool_calls")
    # Second request must not leak tool_choice under thinking either.
    assert "tool_choice" not in second
    assert second.get("thinking") == {"type": "enabled"}
    assert second.get("reasoning_effort") == "max"


def test_factory_applies_deepseek_profile_without_hint() -> None:
    config = ResolvedModelConfig(
        route=MODEL_ROUTE_READER_ASK,
        profile_name="p",
        provider="deepseek-official",
        adapter="openai_compatible",
        model_name="deepseek-v4-flash",
        base_url="https://api.deepseek.com",
        api_key="k",
        provider_options={},  # no profile hint
        model_settings=RunModelSettings(
            extra_body={"thinking": {"type": "enabled"}}
        ),
    )
    model = build_model_instance(config)
    assert isinstance(model, DirectDeepSeekChatModel)
    profile = model.profile
    from pydantic_ai.profiles.openai import OpenAIModelProfile

    oai = OpenAIModelProfile.from_profile(profile)
    assert oai.openai_chat_thinking_field == "reasoning_content"
    assert oai.openai_chat_send_back_thinking_parts == "field"


def test_factory_dashscope_deepseek_compat_profile_without_hint() -> None:
    config = ResolvedModelConfig(
        route=MODEL_ROUTE_READER_ASK,
        profile_name="p",
        provider="dashscope-openai",
        adapter="openai_compatible",
        model_name="deepseek-v4-flash",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key="k",
        provider_options={},
        model_settings=RunModelSettings(
            extra_body={"enable_thinking": True}
        ),
    )
    model = build_model_instance(config)
    # DashScope DeepSeek stays on OpenAIChatModel, not DirectDeepSeek.
    from pydantic_ai.models.openai import OpenAIChatModel

    assert isinstance(model, OpenAIChatModel)
    assert not isinstance(model, DirectDeepSeekChatModel)
    from pydantic_ai.profiles.openai import OpenAIModelProfile

    oai = OpenAIModelProfile.from_profile(model.profile)
    assert oai.openai_chat_thinking_field == "reasoning_content"
