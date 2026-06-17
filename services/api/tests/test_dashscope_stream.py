"""Tests for the DashScope native streaming wrapper."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    PartStartEvent,
    SystemPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelProfile, ModelRequestParameters
from pydantic_ai.models.function import DeltaThinkingPart, DeltaToolCall, FunctionModel
from pydantic_ai.tools import ToolDefinition

from app.llm.dashscope_stream import (
    _convert_messages,
    _request_kwargs,
    _usage_to_dict,
    request_dashscope_chat,
    stream_dashscope_chat,
)
from app.llm.types import RunModelSettings


def _mock_chunk(
    *,
    reasoning: str | None = None,
    content: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    usage: dict[str, int] | None = None,
    status_code: int = 200,
    code: str = "",
    message: str = "",
) -> MagicMock:
    """Build a MagicMock that mimics DashScope's ``GenerationResponse`` chunk.

    The stream wrapper calls ``isinstance(message, Message)`` and
    ``isinstance(message, dict)`` to coerce ``choice.message``.  We use a
    ``Message`` instance directly so the coercion is a no-op.
    """
    from dashscope.api_entities.dashscope_response import Message

    chunk = MagicMock()
    chunk.status_code = status_code
    chunk.code = code
    chunk.message = message
    msg = Message(role="assistant", content=content or "")
    msg.reasoning_content = reasoning
    msg.tool_calls = tool_calls
    choice = MagicMock()
    choice.message = msg
    output = MagicMock()
    output.choices = [choice]
    chunk.output = output
    chunk.usage = usage
    return chunk


def _async_iter(items: list[Any]) -> Any:
    async def _aiter() -> Any:
        for item in items:
            yield item

    return _aiter()


class _FakeResponse:
    def __init__(self, chunks: list[Any]) -> None:
        self._chunks = chunks

    def __aiter__(self) -> Any:
        async def _gen() -> Any:
            for c in self._chunks:
                yield c

        return _gen()


def test_request_kwargs_always_incremental_output_for_streams() -> None:
    out = _request_kwargs(
        model_settings=RunModelSettings(max_tokens=1024, temperature=0.3),
        provider_options={},
        function_tools=[],
        output_tools=[],
        allow_text_output=True,
        stream=True,
    )
    assert out["result_format"] == "message"
    assert out["incremental_output"] is True
    assert out["stream"] is True
    assert out["max_tokens"] == 1024
    assert out["temperature"] == 0.3


def test_extra_body_settings_pass_through() -> None:
    out = _request_kwargs(
        model_settings=RunModelSettings(extra_body={"enable_thinking": True, "search": False}),
        provider_options={},
        function_tools=[],
        output_tools=[],
        allow_text_output=True,
        stream=True,
    )
    assert out["enable_thinking"] is True
    assert out["search"] is False


def test_provider_options_setdefault_does_not_override_explicit() -> None:
    out = _request_kwargs(
        model_settings=RunModelSettings(),
        provider_options={"result_format": "should_be_overridden_by_explicit"},
        function_tools=[],
        output_tools=[],
        allow_text_output=True,
        stream=True,
    )
    # result_format is set by stream kwargs first, setdefault would not override
    assert out["result_format"] == "message"


def test_request_kwargs_accepts_runtime_dict_and_tools() -> None:
    tool = ToolDefinition(
        name="get_record_context",
        description="Load record context",
        parameters_json_schema={"type": "object", "properties": {}},
    )
    out = _request_kwargs(
        model_settings={"max_tokens": 321, "extra_body": {"enable_thinking": True}},
        provider_options={"profile": "should_be_filtered"},
        function_tools=[tool],
        output_tools=[],
        allow_text_output=True,
        stream=True,
    )
    assert out["max_tokens"] == 321
    assert out["enable_thinking"] is True
    assert out["tool_choice"] == "auto"
    assert out["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "get_record_context",
                "description": "Load record context",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    assert "profile" not in out


def test_convert_messages_handles_user_and_assistant() -> None:
    msgs = [
        ModelRequest(parts=[SystemPromptPart(content="you are helpful")]),
        ModelRequest(parts=[UserPromptPart(content="hi")]),
    ]
    out = _convert_messages(msgs)
    assert out == [
        {"role": "system", "content": "you are helpful"},
        {"role": "user", "content": "hi"},
    ]


def test_convert_messages_handles_tool_return() -> None:
    msgs = [
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="lookup",
                    content="found",
                    tool_call_id="call_1",
                )
            ]
        )
    ]
    out = _convert_messages(msgs)
    assert out == [
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": "found",
        }
    ]


def test_convert_messages_handles_assistant_with_tool_calls() -> None:
    msgs = [
        ModelRequest(
            parts=[
                UserPromptPart(content="hi"),
            ]
        ),
    ]
    from pydantic_ai.messages import ModelResponse

    response = MagicMock(spec=ModelResponse)
    response.parts = [
        TextPart(content="hello "),
        ToolCallPart(tool_name="lookup", args={"q": "x"}, tool_call_id="c1"),
    ]
    msgs.append(response)
    out = _convert_messages(msgs)
    assert out[-1]["role"] == "assistant"
    assert out[-1]["content"] == "hello "
    assert out[-1]["tool_calls"] == [
        {
            "id": "c1",
            "type": "function",
            "function": {"name": "lookup", "arguments": '{"q":"x"}'},
        }
    ]


def test_usage_to_dict_handles_dataclass_and_dict() -> None:
    class _U:
        input_tokens = 10
        output_tokens = 20

    assert _usage_to_dict(_U()) == {"input_tokens": 10, "output_tokens": 20}
    assert _usage_to_dict({"input_tokens": 1}) == {"input_tokens": 1}
    assert _usage_to_dict(None) == {}


async def test_stream_yields_reasoning_before_text() -> None:
    chunks = [
        _mock_chunk(reasoning="Let me think"),
        _mock_chunk(reasoning=" more"),
        _mock_chunk(reasoning="", content="Hello"),
        _mock_chunk(content=" world"),
        _mock_chunk(usage={"input_tokens": 5, "output_tokens": 10}),
    ]
    response = _FakeResponse(chunks)

    with patch("app.llm.dashscope_stream.AioGeneration") as mock_gen:
        mock_gen.call = AsyncMock(return_value=response)
        yielded = [
            part
            async for part in stream_dashscope_chat(
                model="qwen3.7-max",
                messages=[],
                api_key="k",
                model_settings=RunModelSettings(),
                provider_options={},
            )
        ]

    # All reasoning parts come before any text
    seen_text = False
    saw_reasoning = False
    for part in yielded:
        if isinstance(part, str):
            seen_text = True
            assert not saw_reasoning or part == "Hello" or part == " world"
        elif isinstance(part, dict):
            assert not seen_text, "reasoning must precede text"
            for v in part.values():
                assert isinstance(v, DeltaThinkingPart)
                saw_reasoning = True
    assert seen_text is True
    assert "Hello" in yielded
    assert " world" in yielded


async def test_stream_yields_tool_call_delta() -> None:
    chunk = _mock_chunk(
        content=None,
        tool_calls=[
            {
                "id": "c1",
                "type": "function",
                "function": {"name": "lookup", "arguments": '{"q":"x"}'},
            }
        ],
    )
    response = _FakeResponse([chunk])

    with patch("app.llm.dashscope_stream.AioGeneration") as mock_gen:
        mock_gen.call = AsyncMock(return_value=response)
        yielded = [
            part
            async for part in stream_dashscope_chat(
                model="qwen3.7-max",
                messages=[],
                api_key="k",
                model_settings=RunModelSettings(),
                provider_options={},
            )
        ]
    assert len(yielded) == 1
    assert isinstance(yielded[0], dict)
    delta = next(iter(yielded[0].values()))
    assert isinstance(delta, DeltaToolCall)
    assert delta.name == "lookup"
    assert delta.json_args == '{"q":"x"}'
    assert delta.tool_call_id == "c1"


async def test_stream_uses_disjoint_part_ids_for_reasoning_then_tool_call() -> None:
    chunks = [
        _mock_chunk(reasoning="Let me inspect the article."),
        _mock_chunk(
            content=None,
            tool_calls=[
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": '{"q":"x"}'},
                }
            ],
        ),
    ]
    response = _FakeResponse(chunks)

    with patch("app.llm.dashscope_stream.AioGeneration") as mock_gen:
        mock_gen.call = AsyncMock(return_value=response)
        yielded = [
            part
            async for part in stream_dashscope_chat(
                model="glm-5.1",
                messages=[],
                api_key="k",
                model_settings=RunModelSettings(extra_body={"enable_thinking": True}),
                provider_options={},
            )
        ]

    assert len(yielded) == 2
    assert isinstance(yielded[0], dict)
    assert isinstance(yielded[0][0], DeltaThinkingPart)
    assert isinstance(yielded[1], dict)
    assert isinstance(yielded[1][1], DeltaToolCall)


async def test_function_model_consumes_reasoning_then_tool_stream_without_collision() -> None:
    chunks = [
        _mock_chunk(reasoning="The user wants a richer summary."),
        _mock_chunk(
            tool_calls=[
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": '{"q":"x"}'},
                }
            ],
        ),
    ]
    response = _FakeResponse(chunks)
    tool = ToolDefinition(
        name="lookup",
        description="Look up context",
        parameters_json_schema={"type": "object", "properties": {}},
    )

    async def _stream(messages, agent_info):
        async for part in stream_dashscope_chat(
            model="glm-5.1",
            messages=list(messages),
            api_key="k",
            model_settings=agent_info.model_settings,
            provider_options={},
            function_tools=agent_info.function_tools,
            output_tools=agent_info.output_tools,
            allow_text_output=agent_info.allow_text_output,
        ):
            yield part

    model = FunctionModel(
        stream_function=_stream,
        model_name="glm-5.1",
        profile=ModelProfile(supports_thinking=True),
    )

    with patch("app.llm.dashscope_stream.AioGeneration") as mock_gen:
        mock_gen.call = AsyncMock(return_value=response)
        async with model.request_stream(
            [],
            None,
            ModelRequestParameters(function_tools=[tool], allow_text_output=True),
        ) as stream:
            events = [event async for event in stream]

    start_parts = [event.part for event in events if isinstance(event, PartStartEvent)]
    assert any(isinstance(part, ThinkingPart) for part in start_parts)
    assert any(
        isinstance(part, ToolCallPart) and part.tool_name == "lookup"
        for part in start_parts
    )


async def test_request_builds_model_response_with_reasoning_and_tool_calls() -> None:
    response = _mock_chunk(
        reasoning="先看文章上下文",
        content="final answer",
        tool_calls=[
            {
                "id": "c1",
                "type": "function",
                "function": {"name": "get_record_context", "arguments": '{"record_id":"r1"}'},
            }
        ],
        usage={"input_tokens": 12, "output_tokens": 34, "reasoning_tokens": 5},
    )

    with patch("app.llm.dashscope_stream.AioGeneration") as mock_gen:
        mock_gen.call = AsyncMock(return_value=response)
        result = await request_dashscope_chat(
            model="qwen3.7-max",
            messages=[],
            api_key="k",
            model_settings={"max_tokens": 512},
            provider_options={},
            function_tools=[],
            output_tools=[],
            allow_text_output=True,
        )

    assert isinstance(result.parts[0], ThinkingPart)
    assert result.parts[0].content == "先看文章上下文"
    assert isinstance(result.parts[1], ToolCallPart)
    assert result.parts[1].tool_name == "get_record_context"
    assert result.parts[1].args_as_json_str() == '{"record_id":"r1"}'
    assert isinstance(result.parts[2], TextPart)
    assert result.parts[2].content == "final answer"
    assert result.usage.input_tokens == 12
    assert result.usage.output_tokens == 34
    assert result.usage.details["reasoning_tokens"] == 5


async def test_stream_propagates_dashscope_exception_on_error() -> None:
    from dashscope.common.error import DashScopeException

    chunk = _mock_chunk(status_code=400)
    chunk.code = "InvalidParameter"
    chunk.message = "bad model"
    response = _FakeResponse([chunk])

    with patch("app.llm.dashscope_stream.AioGeneration") as mock_gen:
        mock_gen.call = AsyncMock(return_value=response)
        with pytest.raises(DashScopeException):
            async for _ in stream_dashscope_chat(
                model="qwen3.7-max",
                messages=[],
                api_key="k",
                model_settings=RunModelSettings(),
                provider_options={},
            ):
                pass


async def test_stream_skips_chunks_without_choices() -> None:
    chunk = MagicMock()
    chunk.status_code = 200
    chunk.output = MagicMock()
    chunk.output.choices = []
    response = _FakeResponse([chunk])

    with patch("app.llm.dashscope_stream.AioGeneration") as mock_gen:
        mock_gen.call = AsyncMock(return_value=response)
        yielded = [
            part
            async for part in stream_dashscope_chat(
                model="qwen3.7-max",
                messages=[],
                api_key="k",
                model_settings=RunModelSettings(),
                provider_options={},
            )
        ]
    assert yielded == []
