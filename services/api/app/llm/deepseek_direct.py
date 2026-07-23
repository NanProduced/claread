"""Narrow Direct DeepSeek OpenAI-compatible model (R4-A5-8A1R3).

Wire contracts (official api.deepseek.com / V4 thinking + tools):

- ``thinking: {"type": "enabled"}`` (or ``{"type": "disabled"}``) in
  ``extra_body`` (not nested effort);
- ``reasoning_effort`` at request **top level** via OpenAI SDK param or
  ``extra_body`` sibling — never inside ``thinking``;
- when thinking is effectively enabled and function tools are present,
  the request JSON must **omit** ``tool_choice`` entirely (not ``auto``
  / ``required``);
- assistant history with tool_calls keeps ``content`` as a string
  (empty string, never JSON null) plus full ``reasoning_content``.

Effective wire state (R4-A5-8A1R3)
----------------------------------
The model stores the **configured** mode (absent/enabled/disabled) and
derives the **effective wire** state from it. Absent is normalized to
enabled (product policy: always send an explicit thinking field so the
payload is self-describing and cannot fall into a non-thinking path).
``_get_tool_choice`` omits ``tool_choice`` when the effective wire state
is enabled — i.e. for both ``absent`` and ``enabled`` configured modes.

Reentrancy (R4-A5-8A1R2)
------------------------
The previous implementation temporarily rewrote
``client.chat.completions.create`` via ``object.__setattr__`` per request.
That is a process-local mutation of a shared resource and is not safe
under concurrent requests on the same model instance. It is replaced by
a per-request override of ``_get_tool_choice`` — a stateless,
reentrant seam that returns ``tool_choice=None`` (→ SDK ``OMIT`` → key
absent) when the *effective* wire thinking state is ``enabled``.

This subclass is **only** used for the ``deepseek_direct`` dialect.
DashScope DeepSeek / Qwen keep their existing paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openai.types import chat
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.settings import ModelSettings

from app.llm.thinking_capability import DirectDeepSeekThinkingMode


class DirectDeepSeekChatModel(OpenAIChatModel):
    """OpenAIChatModel specialized for DeepSeek official thinking wire rules.

    The **configured** thinking mode (absent/enabled/disabled) is fixed at
    construction from the resolved capability. The **effective wire** mode
    is derived from it: absent → enabled (product policy). Only disabled
    yields a disabled wire state.

    The effective wire mode drives two stateless, per-request behaviours:

    1. ``_get_tool_choice`` returns ``None`` (→ wire key absent) when
       thinking is effectively enabled and tools are present;
    2. ``_MapModelResponseContext._into_message_param`` keeps ``content``
       as a string when tool_calls are present (never JSON null).
    """

    def __init__(
        self,
        *args: Any,
        thinking_mode: DirectDeepSeekThinkingMode = "absent",
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._deepseek_thinking_mode: DirectDeepSeekThinkingMode = thinking_mode

    @property
    def deepseek_thinking_mode(self) -> DirectDeepSeekThinkingMode:
        """Configured Direct DeepSeek thinking mode (absent/enabled/disabled).

        This is the caller's configured value, NOT the effective wire state.
        Use :attr:`deepseek_effective_wire_mode` for the normalized wire
        state that drives ``tool_choice`` omission.
        """
        return self._deepseek_thinking_mode

    @property
    def deepseek_effective_wire_mode(self) -> DirectDeepSeekThinkingMode:
        """Effective Direct DeepSeek wire thinking state (R4-A5-8A1R3).

        Absent is normalized to ``enabled`` so the wire payload always
        carries an explicit thinking field. Only ``disabled`` yields a
        disabled wire state.
        """
        if self._deepseek_thinking_mode == "absent":
            return "enabled"
        return self._deepseek_thinking_mode

    @dataclass
    class _MapModelResponseContext(OpenAIChatModel._MapModelResponseContext):
        """Force string content when tool_calls are present (never JSON null)."""

        def _into_message_param(self) -> chat.ChatCompletionAssistantMessageParam:
            message_param = super()._into_message_param()
            if message_param.get("tool_calls"):
                if message_param.get("content") is None:
                    message_param["content"] = ""
            return message_param

    def _get_tool_choice(  # type: ignore[override]
        self,
        model_settings: ModelSettings,
        model_request_parameters: ModelRequestParameters,
    ) -> tuple[list[chat.ChatCompletionToolParam], Any | None]:
        """Reentrant, per-request tool_choice seam.

        Returns ``(tools, None)`` when the effective wire thinking state
        is ``enabled`` (i.e. configured ``absent`` or ``enabled``) and
        tools are present, so the OpenAI SDK omits ``tool_choice`` from
        the wire JSON entirely. No instance-global mutation is performed;
        each call is independent and safe under concurrency.
        """
        tools, tool_choice = super()._get_tool_choice(
            model_settings, model_request_parameters
        )
        if self.deepseek_effective_wire_mode == "enabled" and tools:
            return tools, None
        return tools, tool_choice


def deepseek_v4_openai_profile() -> OpenAIModelProfile:
    """Profile ensuring reasoning_content parse + send-back for DeepSeek V4.

    Single source of truth for Direct/DashScope DeepSeek OpenAI-compatible
    routes. ``supports_thinking=True`` is required so the agent graph
    forwards ``ThinkingPart`` events; without it the graph silently drops
    them.
    """
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
