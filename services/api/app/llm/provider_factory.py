from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pydantic_ai.models import Model, ModelProfile
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.providers.moonshotai import MoonshotAIProvider
from pydantic_ai.providers.openai import OpenAIProvider

from app.llm.dashscope_stream import request_dashscope_chat, stream_dashscope_chat
from app.llm.types import ModelAdapter, ResolvedModelConfig


class ModelProviderError(ValueError):
    """Raised when a configured provider cannot be built."""


# ---------------------------------------------------------------------------
# Embedding / Rerank resolved config — lightweight build result that captures
# the provider/model/api_key resolved from the registry, without constructing
# a pydantic-ai Model (which only applies to chat/completion adapters).
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ResolvedEmbeddingConfig:
    """Resolved config for a dashscope_embedding adapter.

    Carries everything ``bailian_embedding`` needs to call the DashScope SDK,
    derived from the unified provider/model/profile registry.
    """

    provider: str
    model_name: str
    api_key: str
    dimension: int
    provider_options: dict[str, object]


@dataclass(frozen=True)
class ResolvedRerankConfig:
    """Resolved config for a dashscope_rerank adapter.

    Carries everything ``bailian_rerank`` needs to call the DashScope SDK,
    derived from the unified provider/model/profile registry.
    """

    provider: str
    model_name: str
    api_key: str
    provider_options: dict[str, object]


def _deepseek_v4_profile() -> OpenAIModelProfile:
    """DeepSeek V4 OpenAI-compatible profile."""
    return OpenAIModelProfile(
        supports_json_object_output=True,
        supports_json_schema_output=False,
        default_structured_output_mode="prompted",
        openai_supports_tool_choice_required=False,
        openai_chat_thinking_field="reasoning_content",
        openai_chat_send_back_thinking_parts="field",
    )


def _reasoning_content_profile() -> OpenAIModelProfile:
    """Generic OpenAI-compatible profile for providers that emit reasoning_content."""
    return OpenAIModelProfile(
        openai_supports_tool_choice_required=False,
        openai_chat_thinking_field="reasoning_content",
        openai_chat_send_back_thinking_parts="field",
    )


def _moonshot_profile(model_name: str) -> OpenAIModelProfile:
    """Moonshot-specific profile using pydantic-ai's built-in lookup."""
    return MoonshotAIProvider.model_profile(model_name)


# Config-driven profile hint registry.  When ``openai_profile`` is not
# explicitly declared on the provider or model, the factory checks
# ``provider_options.profile`` for a known hint string and uses the
# corresponding builder.  This replaces the old URL / model-name sniffing
# heuristics with an explicit, config-driven opt-in.
#
# Supported hint values:
#   "deepseek_v4"        – DeepSeek V4 reasoning + prompted JSON output
#   "reasoning_content"  – Generic reasoning_content field support
#   "moonshot"           – Moonshot AI provider quirks
_PROFILE_HINT_BUILDERS: dict[str, Callable[[str], OpenAIModelProfile]] = {
    "deepseek_v4": lambda _model_name: _deepseek_v4_profile(),
    "reasoning_content": lambda _model_name: _reasoning_content_profile(),
    "moonshot": lambda model_name: _moonshot_profile(model_name),
}


def _profile_from_config(model_config: ResolvedModelConfig) -> OpenAIModelProfile | None:
    if model_config.openai_profile is not None:
        return OpenAIModelProfile(**model_config.openai_profile.model_dump(exclude_none=True))
    return None


def _resolve_openai_profile(model_config: ResolvedModelConfig) -> OpenAIModelProfile | None:
    """Resolve the OpenAI model profile from config.

    Priority:
      1. Explicit ``openai_profile`` on the resolved config (provider + model merge).
      2. ``provider_options.profile`` hint mapped to a built-in profile builder.
      3. None — the OpenAIChatModel will use its own defaults.
    """
    profile = _profile_from_config(model_config)
    if profile is not None:
        return profile

    profile_hint = str(model_config.provider_options.get("profile", ""))
    builder = _PROFILE_HINT_BUILDERS.get(profile_hint)
    if builder is not None:
        return builder(model_config.model_name)

    return None


def _build_openai_compatible_model(model_config: ResolvedModelConfig) -> OpenAIChatModel | None:
    if not model_config.model_name or not model_config.base_url:
        return None

    provider = OpenAIProvider(
        base_url=model_config.base_url,
        api_key=model_config.api_key or None,
    )

    profile = _resolve_openai_profile(model_config)

    return OpenAIChatModel(
        model_config.model_name,
        provider=provider,
        profile=profile,
        settings=(
            model_config.model_settings.to_pydantic_ai()
            if model_config.model_settings
            else None
        ),
    )


def _dashscope_native_profile() -> ModelProfile:
    """Conservative default profile for DashScope native streaming.

    DashScope native Qwen / GLM do not advertise tool_choice=required or
    strict JSON schema, and structured output must be prompted.
    ``supports_thinking=True`` is required so the agent graph forwards
    ``ThinkingPart`` events emitted by ``FunctionModel``; without it the
    graph silently drops them.
    """
    return ModelProfile(
        supports_json_object_output=False,
        supports_json_schema_output=False,
        default_structured_output_mode="prompted",
        supports_thinking=True,
    )


def _build_dashscope_native_model(
    model_config: ResolvedModelConfig,
) -> FunctionModel | None:
    if not model_config.model_name or not model_config.api_key:
        return None

    settings = (
        model_config.model_settings.to_pydantic_ai()
        if model_config.model_settings
        else None
    )

    async def _request(messages, agent_info):
        return await request_dashscope_chat(
            model=model_config.model_name,
            messages=list(messages),
            api_key=model_config.api_key,
            model_settings=agent_info.model_settings,
            provider_options=model_config.provider_options,
            function_tools=agent_info.function_tools,
            output_tools=agent_info.output_tools,
            allow_text_output=agent_info.allow_text_output,
            instructions=agent_info.instructions,
        )

    async def _stream(messages, agent_info):
        async for part in stream_dashscope_chat(
            model=model_config.model_name,
            messages=list(messages),
            api_key=model_config.api_key,
            model_settings=agent_info.model_settings,
            provider_options=model_config.provider_options,
            function_tools=agent_info.function_tools,
            output_tools=agent_info.output_tools,
            allow_text_output=agent_info.allow_text_output,
            instructions=agent_info.instructions,
        ):
            yield part

    return FunctionModel(
        function=_request,
        stream_function=_stream,
        model_name=model_config.model_name,
        profile=_dashscope_native_profile(),
        settings=settings,
    )


def _build_dashscope_embedding_model(
    model_config: ResolvedModelConfig,
) -> ResolvedEmbeddingConfig | None:
    """Build a resolved embedding config from the unified registry.

    The ``dimension`` is read from ``provider_options.dimension`` (int).
    If not specified, defaults to 1024.
    """
    if not model_config.model_name or not model_config.api_key:
        return None

    dimension = int(model_config.provider_options.get("dimension", 1024))
    return ResolvedEmbeddingConfig(
        provider=model_config.provider,
        model_name=model_config.model_name,
        api_key=model_config.api_key,
        dimension=dimension,
        provider_options=model_config.provider_options,
    )


def _build_dashscope_rerank_model(
    model_config: ResolvedModelConfig,
) -> ResolvedRerankConfig | None:
    """Build a resolved rerank config from the unified registry."""
    if not model_config.model_name or not model_config.api_key:
        return None

    return ResolvedRerankConfig(
        provider=model_config.provider,
        model_name=model_config.model_name,
        api_key=model_config.api_key,
        provider_options=model_config.provider_options,
    )


PROVIDER_BUILDERS: dict[ModelAdapter, Callable[[ResolvedModelConfig], Model | str | None]] = {
    "openai_compatible": _build_openai_compatible_model,
    "dashscope_native": _build_dashscope_native_model,
    "dashscope_embedding": _build_dashscope_embedding_model,
    "dashscope_rerank": _build_dashscope_rerank_model,
}


def build_model_instance(model_config: ResolvedModelConfig) -> Model | str | None:
    builder = PROVIDER_BUILDERS.get(model_config.adapter)
    if builder is None:
        raise ModelProviderError(f"Unsupported model adapter: {model_config.adapter}")
    return builder(model_config)
