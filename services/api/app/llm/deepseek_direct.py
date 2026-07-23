"""Narrow Direct DeepSeek OpenAI-compatible model (R4-A5-8A1R).

Wire contracts (official api.deepseek.com / V4 thinking + tools):

- ``thinking: {"type": "enabled"}`` in ``extra_body`` (not nested effort);
- ``reasoning_effort`` at request **top level** via OpenAI SDK param or
  ``extra_body`` sibling — never inside ``thinking``;
- when thinking is on and function tools are present, the request JSON
  must **omit** ``tool_choice`` entirely (not ``auto`` / ``required``);
- assistant history with tool_calls keeps ``content`` as a string
  (empty string, never JSON null) plus full ``reasoning_content``.

This subclass is **only** used for the ``deepseek_direct`` dialect.
DashScope DeepSeek / Qwen keep their existing paths.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from openai.types import chat
from openai.types.chat import ChatCompletionChunk
from openai.types.chat.chat_completion import ChatCompletion
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.settings import ModelSettings

# OpenAI Python SDK omit sentinel used by pydantic-ai OpenAIChatModel.
try:
    from openai import omit as OMIT
except ImportError:  # pragma: no cover
    from openai import NOT_GIVEN as OMIT  # type: ignore[assignment]


class DirectDeepSeekChatModel(OpenAIChatModel):
    """OpenAIChatModel specialized for DeepSeek official thinking wire rules."""

    def __init__(
        self,
        *args: Any,
        thinking_enabled: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._deepseek_thinking_enabled = bool(thinking_enabled)

    @dataclass
    class _MapModelResponseContext(OpenAIChatModel._MapModelResponseContext):
        """Force string content when tool_calls are present (never JSON null)."""

        def _into_message_param(self) -> chat.ChatCompletionAssistantMessageParam:
            message_param = super()._into_message_param()
            if message_param.get("tool_calls"):
                if message_param.get("content") is None:
                    message_param["content"] = ""
            return message_param

    async def _completions_create(  # type: ignore[override]
        self,
        messages: list[ModelMessage],
        stream: bool,
        model_settings: ModelSettings,
        model_request_parameters: ModelRequestParameters,
    ) -> ChatCompletion | AsyncIterator[ChatCompletionChunk] | ModelResponse:
        """Wrap the SDK create call to omit tool_choice under thinking+tools."""
        completions = self.client.chat.completions
        original_create = completions.create
        thinking_on = self._deepseek_thinking_enabled

        async def _create_filtered(*args: Any, **kwargs: Any) -> Any:
            if thinking_on:
                tools = kwargs.get("tools")
                # Drop tool_choice when tools are actually present.
                if tools is not None and tools is not OMIT and tools:
                    kwargs["tool_choice"] = OMIT
            return await original_create(*args, **kwargs)

        # Instance-local bind only — not a process-global monkeypatch.
        object.__setattr__(completions, "create", _create_filtered)
        try:
            return await super()._completions_create(
                messages, stream, model_settings, model_request_parameters
            )
        finally:
            object.__setattr__(completions, "create", original_create)


def deepseek_v4_openai_profile() -> OpenAIModelProfile:
    """Profile ensuring reasoning_content parse + send-back for DeepSeek V4."""
    return OpenAIModelProfile(
        supports_json_object_output=True,
        supports_json_schema_output=False,
        default_structured_output_mode="prompted",
        openai_supports_tool_choice_required=False,
        openai_chat_thinking_field="reasoning_content",
        openai_chat_send_back_thinking_parts="field",
        supports_thinking=True,
    )


__all__ = [
    "DirectDeepSeekChatModel",
    "deepseek_v4_openai_profile",
]
