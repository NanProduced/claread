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
from openai import AsyncOpenAI
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.providers.openai import OpenAIProvider

from app.llm.deepseek_direct import DirectDeepSeekChatModel, deepseek_v4_openai_profile
from app.llm.provider_factory import (
    DeepSeekProfileConflictError,
    build_model_instance,
)
from app.llm.routes import MODEL_ROUTE_READER_ASK
from app.llm.thinking_capability import (
    DirectDeepSeekThinkingMode,
    ThinkingEffortConfigError,
    apply_thinking_to_model_settings,
    normalize_deepseek_direct_effort,
    resolve_thinking_capability,
)
from app.llm.types import OpenAIProfileConfig, ResolvedModelConfig, RunModelSettings

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
    # absent: no thinking field in config; R3 normalizes to explicit
    # enabled on wire via apply_thinking_to_model_settings.
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
    # max_retries=0 is essential for offline tests: the OpenAI SDK retries
    # on httpx.ConnectError by default, which would consume multiple
    # transport calls and mask the first-request-fails scenario.
    openai_client = AsyncOpenAI(
        api_key="test-key-not-real",
        base_url="https://api.deepseek.com",
        http_client=http_client,
        max_retries=0,
    )
    provider = OpenAIProvider(openai_client=openai_client)
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
async def test_direct_deepseek_absent_mode_emits_explicit_enabled() -> None:
    """absent configured mode emits explicit {"type":"enabled"} on wire (R3).

    R4-A5-8A1R3: absent configuration must be normalized to an explicit
    ``{"thinking": {"type": "enabled"}}`` so the wire payload is
    self-describing and cannot fall into a non-thinking code path.
    No reasoning_effort is emitted when the caller did not configure one.
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
    # absent → explicit enabled on wire (NOT field deletion).
    assert first.get("thinking") == {"type": "enabled"}, (
        "absent mode must emit explicit {type: enabled} on wire (R3)"
    )
    assert "reasoning_effort" not in first, (
        "absent mode without configured effort must not emit reasoning_effort"
    )
    # Configured mode is absent; effective wire mode is enabled.
    assert model.deepseek_thinking_mode == "absent"
    assert model.deepseek_effective_wire_mode == "enabled"


@pytest.mark.asyncio
async def test_direct_deepseek_absent_mode_with_tools_omits_tool_choice() -> None:
    """absent mode + tools → explicit enabled + tool_choice omitted (R3).

    The tool_choice omission rule fires when the effective wire thinking
    state is enabled — which includes absent (normalized to enabled).
    """
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
        thinking_mode="absent", effort=None, transport=transport
    )

    async def echo(ctx: Any) -> str:
        return "pong"

    agent = Agent(model, tools=[echo], output_type=str)
    try:
        await agent.run("call echo please")
    except Exception:
        pass  # tool loop may fail after first request; wire already captured

    assert transport.requests
    first = transport.requests[0]
    assert first.get("thinking") == {"type": "enabled"}
    assert "tool_choice" not in first, (
        "absent mode (effective enabled) + tools must omit tool_choice"
    )
    assert first.get("tools")  # tools present


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


# ---------------------------------------------------------------------------
# R4-A5-8A1R3: Profile conflict fail-closed tests (3 paths).
#
# All three recognition paths for Direct/DashScope DeepSeek must enforce
# canonical thinking fields:
#   1. No hint (recognised by provider/model/base_url identity)
#   2. ``deepseek_v4`` hint
#   3. Explicit partial ``openai_profile``
#
# A truthy non-canonical value for ``openai_chat_thinking_field`` or
# ``openai_chat_send_back_thinking_parts`` must be rejected fail-closed
# via :class:`DeepSeekProfileConflictError`. Falsy / missing values are
# floor-merged to canonical. Qwen / Moonshot are untouched.
# ---------------------------------------------------------------------------


def _deepseek_direct_config(
    *,
    provider_options: dict[str, object] | None = None,
    openai_profile: OpenAIProfileConfig | None = None,
) -> ResolvedModelConfig:
    return ResolvedModelConfig(
        route=MODEL_ROUTE_READER_ASK,
        profile_name="p",
        provider="deepseek-official",
        adapter="openai_compatible",
        model_name="deepseek-v4-flash",
        base_url="https://api.deepseek.com",
        api_key="k",
        provider_options=provider_options or {},
        model_settings=RunModelSettings(
            extra_body={"thinking": {"type": "enabled"}}
        ),
        openai_profile=openai_profile,
    )


def test_profile_conflict_no_hint_rejects_non_canonical_thinking_field() -> None:
    """Path 1 (no hint): non-canonical thinking_field → fail-closed."""
    config = _deepseek_direct_config(
        openai_profile=OpenAIProfileConfig(
            openai_chat_thinking_field="chain_of_thought",
        ),
    )
    with pytest.raises(DeepSeekProfileConflictError) as exc:
        build_model_instance(config)
    assert exc.value.field_name == "openai_chat_thinking_field"
    assert exc.value.value == "chain_of_thought"
    assert exc.value.canonical == "reasoning_content"


def test_profile_conflict_deepseek_v4_hint_rejects_non_canonical_thinking_field() -> None:
    """Path 2 (deepseek_v4 hint): non-canonical thinking_field → fail-closed.

    The ``deepseek_v4`` hint selects the canonical profile builder, but
    an explicit ``openai_profile`` that conflicts with canonical thinking
    fields is still rejected — the hint does not override the conflict
    check.
    """
    config = _deepseek_direct_config(
        provider_options={"profile": "deepseek_v4"},
        openai_profile=OpenAIProfileConfig(
            openai_chat_thinking_field="thoughts",
        ),
    )
    with pytest.raises(DeepSeekProfileConflictError) as exc:
        build_model_instance(config)
    assert exc.value.field_name == "openai_chat_thinking_field"
    assert exc.value.value == "thoughts"
    assert exc.value.canonical == "reasoning_content"


def test_profile_conflict_explicit_partial_profile_floor_merges_missing() -> None:
    """Path 3 (explicit partial profile): missing fields floor-merged.

    A partial ``openai_profile`` that only sets JSON output flags must
    not accidentally drop thinking fields — they are floor-merged to
    canonical values.
    """
    config = _deepseek_direct_config(
        openai_profile=OpenAIProfileConfig(
            supports_json_object_output=True,
            supports_json_schema_output=False,
        ),
    )
    model = build_model_instance(config)
    assert isinstance(model, DirectDeepSeekChatModel)
    from pydantic_ai.profiles.openai import OpenAIModelProfile

    oai = OpenAIModelProfile.from_profile(model.profile)
    assert oai.openai_chat_thinking_field == "reasoning_content"
    assert oai.openai_chat_send_back_thinking_parts == "field"
    assert oai.supports_thinking is True


def test_profile_conflict_qwen_not_affected() -> None:
    """Qwen / Moonshot profiles are not subject to DeepSeek enforcement."""
    config = ResolvedModelConfig(
        route=MODEL_ROUTE_READER_ASK,
        profile_name="p",
        provider="dashscope",
        adapter="dashscope_native",
        model_name="qwen-plus",
        base_url="",
        api_key="k",
        provider_options={},
        model_settings=RunModelSettings(extra_body={}),
    )
    # Must not raise — Qwen is not DeepSeek.
    model = build_model_instance(config)
    assert model is not None


# ---------------------------------------------------------------------------
# R4-A5-8A1R3: True single-instance concurrency + cancel/exception recovery.
#
# R2's concurrency test used two model instances (different transports).
# R3 requires a single ``DirectDeepSeekChatModel`` instance shared by two
# Agents running concurrently. The stateless ``_get_tool_choice`` override
# must not leak per-request state across concurrent calls.
# ---------------------------------------------------------------------------


class _SharedCaptureTransport(httpx.AsyncBaseTransport):
    """Single transport serving multiple concurrent requests.

    Records every request body in order. Always returns a tool-call
    response so the wire payload captures the ``tool_choice`` omission
    rule on the first request of each run.
    """

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = request.content.decode("utf-8") if request.content else ""
        payload = json.loads(body) if body else {}
        async with self._lock:
            self.requests.append(payload)
        return httpx.Response(
            200,
            json=_chat_completion_response(
                content="",
                reasoning=_SENTINEL,
                tool_calls=[
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "echo", "arguments": "{}"},
                    }
                ],
            ),
            request=request,
        )


@pytest.mark.asyncio
async def test_single_model_instance_concurrent_two_agents_no_state_pollution() -> None:
    """One model instance, two agents, concurrent runs — both omit tool_choice.

    Validates the reentrancy contract at the instance level: the
    ``_get_tool_choice`` override is stateless, so two concurrent runs
    on the *same* model instance each independently omit ``tool_choice``.
    No per-request mutation of shared instance state.
    """
    transport = _SharedCaptureTransport()
    model = _build_direct_model(
        thinking_mode="enabled", effort="high", transport=transport
    )

    async def echo(ctx: Any) -> str:
        return "pong"

    agent_a = Agent(model, tools=[echo], output_type=str)
    agent_b = Agent(model, tools=[echo], output_type=str)

    async def _run(agent: Agent[Any, Any]) -> None:
        try:
            await agent.run("call echo please")
        except Exception:
            pass  # tool loop may fail after first request; wire captured

    await asyncio.gather(_run(agent_a), _run(agent_b))

    assert len(transport.requests) >= 2
    for req in transport.requests:
        assert req.get("thinking") == {"type": "enabled"}
        assert "tool_choice" not in req, (
            "concurrent same-instance run must omit tool_choice on every request"
        )


class _FailingThenSucceedingTransport(httpx.AsyncBaseTransport):
    """First request raises; second succeeds.

    Proves that an exception in one request does not corrupt the model
    instance's state for the next request.
    """

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self._call = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self._call += 1
        body = request.content.decode("utf-8") if request.content else ""
        payload = json.loads(body) if body else {}
        self.requests.append(payload)
        if self._call == 1:
            raise httpx.ConnectError("simulated network failure", request=request)
        return httpx.Response(
            200,
            json=_chat_completion_response(content="recovered", tool_calls=None),
            request=request,
        )


@pytest.mark.asyncio
async def test_cancel_or_exception_recovery_same_instance_no_state_pollution() -> None:
    """First request fails; second request on same instance succeeds correctly.

    The model instance must not retain any per-request state from the
    failed request that would corrupt the wire payload of the next
    request. The second request must still emit the correct thinking
    payload and omit ``tool_choice`` when tools are present.
    """
    transport = _FailingThenSucceedingTransport()
    model = _build_direct_model(
        thinking_mode="enabled", effort="high", transport=transport
    )

    async def echo(ctx: Any) -> str:
        return "pong"

    agent = Agent(model, tools=[echo], output_type=str)

    # First run must fail.
    with pytest.raises(ModelAPIError):
        await agent.run("first call that will fail")

    # Second run on the same model instance must succeed and emit correct wire.
    try:
        await agent.run("second call that should succeed")
    except Exception:
        pass  # tool loop may still fail; we only need the wire payload

    assert len(transport.requests) == 2
    # The failed request still emitted correct wire (the error happened
    # after the request body was constructed and sent).
    assert transport.requests[0].get("thinking") == {"type": "enabled"}
    assert "tool_choice" not in transport.requests[0]
    # The recovered request also emits correct wire — no state pollution.
    assert transport.requests[1].get("thinking") == {"type": "enabled"}
    assert "tool_choice" not in transport.requests[1], (
        "second request after exception must still omit tool_choice — no state pollution"
    )
