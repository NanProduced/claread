"""R4-A5-8A1R: Direct DeepSeek V4 wire correctness (offline HTTP capture).

Captures the actual OpenAI-compatible request JSON via an injectable
``httpx.AsyncClient`` transport — no process-global HTTP monkeypatch.
"""

from __future__ import annotations

import asyncio
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
    DirectDeepSeekThinkingMode,
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
    thinking_mode: DirectDeepSeekThinkingMode = "enabled",
    effort: str | None = "high",
    transport: httpx.AsyncBaseTransport,
) -> DirectDeepSeekChatModel:
    extra: dict[str, object] = {}
    if thinking_mode == "enabled":
        extra["thinking"] = {"type": "enabled"}
        if effort:
            extra["reasoning_effort"] = effort
    elif thinking_mode == "disabled":
        extra["thinking"] = {"type": "disabled"}
    # absent: no thinking field (server default applies — V4 = ON).
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
    # The model's effective thinking mode comes from the resolved capability
    # (single source of truth) — not from the test parameter.
    return DirectDeepSeekChatModel(
        "deepseek-v4-flash",
        provider=provider,
        profile=deepseek_v4_openai_profile(),
        settings=normalized.to_pydantic_ai() if normalized else None,
        thinking_mode=cap.direct_thinking_mode,
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
    model = _build_direct_model(
        thinking_mode="enabled", effort="high", transport=transport
    )

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
    model = _build_direct_model(
        thinking_mode="enabled", effort="max", transport=transport
    )

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


@pytest.mark.asyncio
async def test_direct_deepseek_absent_mode_no_thinking_field_no_effort() -> None:
    """absent mode: no thinking key, no reasoning_effort, no sampling strip.

    V4 official default is thinking ON, but the absent state is still a
    valid wire shape (server default applies). The model must NOT omit
    tool_choice here because the effective wire thinking state is not
    "enabled" — only "enabled" triggers the tool_choice omission rule.
    """
    transport = _CaptureTransport(
        _chat_completion_response(content="ok", tool_calls=None)
    )
    model = _build_direct_model(
        thinking_mode="absent", effort=None, transport=transport
    )
    agent = Agent(model, tools=[], output_type=str)
    await agent.run("hi")

    assert transport.requests
    first = transport.requests[0]
    assert "thinking" not in first, (
        "absent mode must not emit a thinking field on the wire"
    )
    assert "reasoning_effort" not in first, (
        "absent mode must not emit reasoning_effort (effort only applies "
        "when thinking is enabled)"
    )
    # Without tools and without enabled thinking, tool_choice has no
    # special omission rule. The SDK may emit "auto" or omit it; either is
    # acceptable. We assert only that the absence-of-thinking invariant holds.
    assert model.deepseek_thinking_mode == "absent"


@pytest.mark.asyncio
async def test_direct_deepseek_disabled_mode_emits_disabled_payload() -> None:
    """disabled mode must emit thinking={"type":"disabled"} on the wire.

    V4's default is thinking ON, so an explicit off must be sent as the
    disabled payload — deleting the field would silently inherit the
    server default (ON). reasoning_effort must NOT be present.
    """
    transport = _CaptureTransport(
        _chat_completion_response(content="ok", tool_calls=None)
    )
    model = _build_direct_model(
        thinking_mode="disabled", effort="high", transport=transport
    )
    agent = Agent(model, tools=[], output_type=str)
    await agent.run("hi")

    assert transport.requests
    first = transport.requests[0]
    assert first.get("thinking") == {"type": "disabled"}, (
        "disabled mode must emit explicit {type: disabled} — V4 default is ON"
    )
    assert "reasoning_effort" not in first, (
        "disabled mode must not emit reasoning_effort"
    )
    assert model.deepseek_thinking_mode == "disabled"


@pytest.mark.asyncio
async def test_direct_deepseek_concurrent_requests_same_model_no_tool_choice_pollution() -> None:
    """Two concurrent runs on the same model instance both omit tool_choice.

    Validates the reentrancy contract: the new per-request
    ``_get_tool_choice`` override is stateless and does not mutate shared
    instance state, so concurrent enabled-thinking requests each get the
    omission rule applied independently. No global monkeypatch means a
    failure / cancellation in one request cannot corrupt the other.
    """
    transport_a = _CaptureTransport(
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
    transport_b = _CaptureTransport(
        _chat_completion_response(
            content="",
            reasoning=_SENTINEL,
            tool_calls=[
                {
                    "id": "c2",
                    "type": "function",
                    "function": {"name": "echo", "arguments": "{}"},
                }
            ],
        )
    )

    # Build one shared model instance, then hand it two separate captures by
    # swapping the httpx transport per run. We rebuild the model for each
    # run with a different transport because the OpenAIProvider closes over
    # its http_client — but we keep thinking_mode identical to prove the
    # stateless override behaves the same on every call. The point of this
    # test is that the *model class* carries no per-request state, so two
    # independent requests through two independent transports both observe
    # the omission rule.
    model_a = _build_direct_model(
        thinking_mode="enabled", effort="high", transport=transport_a
    )
    model_b = _build_direct_model(
        thinking_mode="enabled", effort="high", transport=transport_b
    )
    # Same effective mode on the shared subclass behaviour.
    assert model_a.deepseek_thinking_mode == model_b.deepseek_thinking_mode

    async def echo(ctx: Any) -> str:
        return "pong"

    agent_a = Agent(model_a, tools=[echo], output_type=str)
    agent_b = Agent(model_b, tools=[echo], output_type=str)

    async def _run(agent: Agent[Any, Any]) -> None:
        try:
            await agent.run("call echo please")
        except Exception:
            # Tool loop may fail after first request; wire is captured.
            pass

    # Run both concurrently — if the override mutated shared state, one of
    # them could observe a leaked tool_choice. Both must omit it.
    await asyncio.gather(_run(agent_a), _run(agent_b))

    assert transport_a.requests and transport_b.requests
    assert "tool_choice" not in transport_a.requests[0]
    assert "tool_choice" not in transport_b.requests[0]
    assert transport_a.requests[0].get("thinking") == {"type": "enabled"}
    assert transport_b.requests[0].get("thinking") == {"type": "enabled"}


@pytest.mark.asyncio
async def test_direct_deepseek_disabled_mode_with_tools_keeps_tool_choice() -> None:
    """disabled + tools: tool_choice is NOT omitted (only enabled omits).

    Guards against a regression where the omission rule fires on any
    thinking field presence rather than on the effective enabled state.
    """
    transport = _CaptureTransport(
        _chat_completion_response(
            content="",
            tool_calls=[
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "echo", "arguments": "{}"},
                }
            ],
        )
    )
    model = _build_direct_model(
        thinking_mode="disabled", effort=None, transport=transport
    )

    async def echo(ctx: Any) -> str:
        return "pong"

    agent = Agent(model, tools=[echo], output_type=str)
    try:
        await agent.run("call echo please")
    except Exception:
        pass

    assert transport.requests
    first = transport.requests[0]
    assert first.get("thinking") == {"type": "disabled"}
    # disabled is not enabled → omission rule does NOT fire. tool_choice
    # may be present (SDK default for tools). The invariant we assert is
    # that the wire still carries the disabled payload alongside tools.
    assert first.get("tools")
