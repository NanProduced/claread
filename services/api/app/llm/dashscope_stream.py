"""DashScope native SDK adapter for pydantic-ai FunctionModel.

Routes Ask Claread main / replan through DashScope's native SDK to recover
``reasoning_content`` support without the OpenAI-compatible adapter path.

The adapter exposes:

- ``stream_dashscope_chat`` for ``FunctionModel.stream_function``
- ``request_dashscope_chat`` for ``FunctionModel.function``

Both paths share the same message conversion, tool schema mapping, runtime
settings handling, and usage extraction.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence
from typing import Any

from dashscope import AioGeneration
from dashscope.api_entities.dashscope_response import (
    DashScopeAPIResponse,
    Message,
)
from dashscope.common.error import DashScopeException
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import DeltaThinkingPart, DeltaToolCall
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.usage import RequestUsage

from app.llm.types import RunModelSettings

logger = logging.getLogger(__name__)

_BASE_NATIVE_KWARGS: dict[str, object] = {
    "result_format": "message",
}
_INTERNAL_PROVIDER_OPTION_KEYS = {"profile"}
_THINKING_VENDOR_PART_ID = 0
_TOOL_VENDOR_PART_ID_OFFSET = 1


def _convert_messages(
    messages: list[ModelMessage], *, instructions: str | None = None
) -> list[Message]:
    """Translate pydantic-ai messages into DashScope ``Message`` objects."""
    out: list[Message] = []
    if instructions and instructions.strip():
        out.append(Message(role="system", content=instructions.strip()))
    for msg in messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                kind = part.part_kind
                if kind == "system-prompt":
                    out.append(Message(role="system", content=part.content))
                elif kind == "user-prompt":
                    out.append(Message(role="user", content=part.content))
                elif kind == "tool-return":
                    assert isinstance(part, ToolReturnPart)
                    out.append(
                        Message(
                            role="tool",
                            content=part.model_response_str(),
                            tool_call_id=part.tool_call_id,
                        )
                    )
                elif kind == "retry-prompt":
                    assert isinstance(part, RetryPromptPart)
                    out.append(Message(role="user", content=part.model_response()))
        elif isinstance(msg, ModelResponse):
            text_chunks: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            for part in msg.parts:
                if isinstance(part, TextPart):
                    text_chunks.append(part.content)
                elif isinstance(part, ToolCallPart):
                    tool_calls.append(
                        {
                            "id": part.tool_call_id or "",
                            "type": "function",
                            "function": {
                                "name": part.tool_name,
                                "arguments": part.args_as_json_str(),
                            },
                        }
                    )
            entry = Message(role="assistant", content="".join(text_chunks))
            if tool_calls:
                entry["tool_calls"] = tool_calls
            out.append(entry)
    return out


def _model_settings_payload(
    model_settings: RunModelSettings | dict[str, Any] | None,
) -> dict[str, Any]:
    if model_settings is None:
        return {}
    if isinstance(model_settings, RunModelSettings):
        return model_settings.model_dump(exclude_none=True)
    if isinstance(model_settings, dict):
        return dict(model_settings)
    items = getattr(model_settings, "items", None)
    if callable(items):
        return {str(key): value for key, value in items()}
    return {}


def _dashscope_tools(
    function_tools: Sequence[ToolDefinition],
    output_tools: Sequence[ToolDefinition],
) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    for tool in [*function_tools, *output_tools]:
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": tool.parameters_json_schema,
                },
            }
        )
    return tools


def _request_kwargs(
    *,
    model_settings: RunModelSettings | dict[str, Any] | None,
    provider_options: dict[str, object],
    function_tools: Sequence[ToolDefinition],
    output_tools: Sequence[ToolDefinition],
    allow_text_output: bool,
    stream: bool,
) -> dict[str, Any]:
    """Map runtime settings + tools into DashScope SDK kwargs."""
    kwargs: dict[str, Any] = dict(_BASE_NATIVE_KWARGS)
    kwargs["stream"] = stream
    if stream:
        kwargs["incremental_output"] = True

    settings_payload = _model_settings_payload(model_settings)
    if settings_payload.get("max_tokens") is not None:
        kwargs["max_tokens"] = settings_payload["max_tokens"]
    if settings_payload.get("temperature") is not None:
        kwargs["temperature"] = settings_payload["temperature"]
    if settings_payload.get("top_p") is not None:
        kwargs["top_p"] = settings_payload["top_p"]
    if settings_payload.get("stop_sequences"):
        kwargs["stop"] = settings_payload["stop_sequences"]

    extra_body = settings_payload.get("extra_body")
    if isinstance(extra_body, dict):
        for key, value in extra_body.items():
            if key in kwargs:
                continue
            kwargs[str(key)] = value

    tools = _dashscope_tools(function_tools, output_tools)
    if tools:
        kwargs["tools"] = tools
        # DashScope native currently works best with permissive tool routing.
        # We deliberately avoid ``required`` here.
        kwargs["tool_choice"] = "auto" if allow_text_output else "auto"

    for key, value in provider_options.items():
        if str(key) in _INTERNAL_PROVIDER_OPTION_KEYS:
            continue
        kwargs.setdefault(str(key), value)
    return kwargs


def _coerce_message(message: Message | dict[str, Any] | None) -> Message | None:
    if message is None:
        return None
    if isinstance(message, Message):
        return message
    if isinstance(message, dict):
        return Message(**message)
    return None


def _safe_get(message: Message, key: str) -> Any:
    """DictMixin lookups raise KeyError on miss; use this for tolerant access."""
    try:
        return getattr(message, key)
    except (KeyError, AttributeError):
        return None


def _usage_to_dict(usage: Any) -> dict[str, int]:
    if usage is None:
        return {}
    if isinstance(usage, dict):
        out: dict[str, int] = {}
        for key, value in usage.items():
            if value is None:
                continue
            if isinstance(value, int | float):
                out[str(key)] = int(value)
            elif isinstance(value, str):
                try:
                    out[str(key)] = int(value)
                except ValueError:
                    pass
        return out
    out = {}
    for key in ("input_tokens", "output_tokens", "reasoning_tokens", "total_tokens"):
        value = getattr(usage, key, None)
        if isinstance(value, int | float):
            out[key] = int(value)
    return out


def _usage_to_request_usage(usage: Any) -> RequestUsage:
    usage_dict = _usage_to_dict(usage)
    detail_keys = {"input_tokens", "output_tokens"}
    return RequestUsage(
        input_tokens=usage_dict.get("input_tokens", 0),
        output_tokens=usage_dict.get("output_tokens", 0),
        details={key: value for key, value in usage_dict.items() if key not in detail_keys},
    )


def _raise_on_error(response_or_chunk: Any) -> None:
    status_code = getattr(response_or_chunk, "status_code", None)
    if status_code is None or status_code == 200:
        return
    code = getattr(response_or_chunk, "code", "") or "DashScopeError"
    message = getattr(response_or_chunk, "message", "") or "DashScope call failed"
    raise DashScopeException(f"{code}: {message}")


def _message_to_response_parts(message: Message) -> list[TextPart | ThinkingPart | ToolCallPart]:
    parts: list[TextPart | ThinkingPart | ToolCallPart] = []

    reasoning = _safe_get(message, "reasoning_content")
    if reasoning:
        parts.append(ThinkingPart(content=str(reasoning)))

    tool_calls = _safe_get(message, "tool_calls")
    if tool_calls:
        for tool_call in tool_calls:
            function = (
                tool_call.get("function", {})
                if isinstance(tool_call, dict)
                else getattr(tool_call, "function", {}) or {}
            )
            name = (
                function.get("name", "")
                if isinstance(function, dict)
                else getattr(function, "name", "") or ""
            )
            arguments = (
                function.get("arguments", "")
                if isinstance(function, dict)
                else getattr(function, "arguments", "") or ""
            )
            tool_call_id = (
                tool_call.get("id", "")
                if isinstance(tool_call, dict)
                else getattr(tool_call, "id", "") or ""
            )
            resolved_tool_call_id = str(tool_call_id) or f"dashscope_native_tool_{len(parts)}"
            parts.append(
                ToolCallPart(
                    tool_name=str(name),
                    args=str(arguments) if arguments else None,
                    tool_call_id=resolved_tool_call_id,
                )
            )

    content = _safe_get(message, "content")
    if content:
        parts.append(TextPart(content=str(content)))
    return parts


async def request_dashscope_chat(
    *,
    model: str,
    messages: list[ModelMessage],
    api_key: str,
    model_settings: RunModelSettings | dict[str, Any] | None,
    provider_options: dict[str, object],
    instructions: str | None = None,
    function_tools: Sequence[ToolDefinition] = (),
    output_tools: Sequence[ToolDefinition] = (),
    allow_text_output: bool = True,
) -> ModelResponse:
    """Return a non-streamed ``ModelResponse`` for ``FunctionModel.function``."""
    ds_messages = _convert_messages(messages, instructions=instructions)
    kwargs = _request_kwargs(
        model_settings=model_settings,
        provider_options=provider_options,
        function_tools=function_tools,
        output_tools=output_tools,
        allow_text_output=allow_text_output,
        stream=False,
    )
    response = await AioGeneration.call(
        model=model,
        messages=ds_messages,
        api_key=api_key,
        **kwargs,
    )
    _raise_on_error(response)

    output = getattr(response, "output", None)
    choices = getattr(output, "choices", None) or []
    if not choices:
        return ModelResponse(
            parts=[],
            usage=_usage_to_request_usage(getattr(response, "usage", None)),
        )

    choice = choices[0]
    message = _coerce_message(getattr(choice, "message", None))
    if message is None:
        return ModelResponse(
            parts=[],
            usage=_usage_to_request_usage(getattr(response, "usage", None)),
        )

    return ModelResponse(
        parts=_message_to_response_parts(message),
        usage=_usage_to_request_usage(getattr(response, "usage", None)),
        finish_reason="tool_call" if _safe_get(message, "tool_calls") else "stop",
    )


async def stream_dashscope_chat(
    *,
    model: str,
    messages: list[ModelMessage],
    api_key: str,
    model_settings: RunModelSettings | dict[str, Any] | None,
    provider_options: dict[str, object],
    function_tools: Sequence[ToolDefinition] = (),
    output_tools: Sequence[ToolDefinition] = (),
    allow_text_output: bool = True,
    instructions: str | None = None,
) -> AsyncIterator[str | dict[int, DeltaThinkingPart] | dict[int, DeltaToolCall]]:
    """Yield FunctionModel-compatible parts from a DashScope native stream."""
    ds_messages = _convert_messages(messages, instructions=instructions)
    kwargs = _request_kwargs(
        model_settings=model_settings,
        provider_options=provider_options,
        function_tools=function_tools,
        output_tools=output_tools,
        allow_text_output=allow_text_output,
        stream=True,
    )
    response = await AioGeneration.call(
        model=model,
        messages=ds_messages,
        api_key=api_key,
        **kwargs,
    )

    last_usage: dict[str, int] | None = None
    chunk_count = 0
    try:
        async for chunk in response:
            chunk_count += 1
            _raise_on_error(chunk)

            output = getattr(chunk, "output", None)
            if output is None:
                continue
            choices = getattr(output, "choices", None) or []
            if not choices:
                continue
            choice = choices[0]
            message = _coerce_message(getattr(choice, "message", None))
            if message is None:
                continue

            reasoning = _safe_get(message, "reasoning_content")
            if reasoning:
                yield {_THINKING_VENDOR_PART_ID: DeltaThinkingPart(content=str(reasoning))}

            tool_calls = _safe_get(message, "tool_calls")
            if tool_calls:
                for index, tool_call in enumerate(tool_calls):
                    function = (
                        tool_call.get("function", {})
                        if isinstance(tool_call, dict)
                        else getattr(tool_call, "function", {}) or {}
                    )
                    name = (
                        function.get("name", "")
                        if isinstance(function, dict)
                        else getattr(function, "name", "") or ""
                    )
                    arguments = (
                        function.get("arguments", "")
                        if isinstance(function, dict)
                        else getattr(function, "arguments", "") or ""
                    )
                    tool_call_id = (
                        tool_call.get("id", "")
                        if isinstance(tool_call, dict)
                        else getattr(tool_call, "id", "") or ""
                    )
                    resolved_tool_call_id = str(tool_call_id) or f"dashscope_native_tool_{index}"
                    yield {
                        index + _TOOL_VENDOR_PART_ID_OFFSET: DeltaToolCall(
                            name=str(name),
                            json_args=str(arguments),
                            tool_call_id=resolved_tool_call_id,
                        )
                    }

            content = _safe_get(message, "content")
            if content:
                yield str(content)

            usage = getattr(chunk, "usage", None)
            if usage is not None:
                usage_dict = _usage_to_dict(usage)
                if usage_dict:
                    last_usage = usage_dict
    finally:
        if last_usage is not None:
            logger.info(
                "dashscope_native_usage model=%s chunks=%d usage=%s",
                model,
                chunk_count,
                last_usage,
            )


__all__ = ["stream_dashscope_chat", "request_dashscope_chat", "DashScopeAPIResponse"]
