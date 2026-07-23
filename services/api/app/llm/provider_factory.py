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
#                          (single source: deepseek_v4_openai_profile)
#   "reasoning_content"  – Generic reasoning_content field support
#   "moonshot"           – Moonshot AI provider quirks
def _deepseek_v4_profile_hint(_model_name: str) -> OpenAIModelProfile:
    # Single source of truth lives in deepseek_direct to avoid divergence
    # between the hint path and the no-hint fallback.
    from app.llm.deepseek_direct import deepseek_v4_openai_profile

    return deepseek_v4_openai_profile()


_PROFILE_HINT_BUILDERS: dict[str, Callable[[str], OpenAIModelProfile]] = {
    "deepseek_v4": _deepseek_v4_profile_hint,
    "reasoning_content": lambda _model_name: _reasoning_content_profile(),
    "moonshot": lambda model_name: _moonshot_profile(model_name),
}


def _profile_from_config(model_config: ResolvedModelConfig) -> OpenAIModelProfile | None:
    if model_config.openai_profile is not None:
        return OpenAIModelProfile(**model_config.openai_profile.model_dump(exclude_none=True))
    return None


def _ensure_deepseek_thinking_fields(
    profile: OpenAIModelProfile | None,
) -> OpenAIModelProfile:
    """Floor-merge DeepSeek protocol-required thinking fields (R4-A5-8A1R2).

    Recognisable Direct/DashScope DeepSeek must always carry
    ``openai_chat_thinking_field="reasoning_content"``,
    ``openai_chat_send_back_thinking_parts="field"`` and
    ``supports_thinking=True`` so the agent graph forwards ThinkingPart
    events and history conversion echoes reasoning_content on tool rounds.

    A partial explicit ``openai_profile`` (e.g. only JSON output flags)
    must not accidentally drop these protocol-required fields. Fields the
    caller explicitly set are preserved; only missing ones are filled.
    """
    from app.llm.deepseek_direct import deepseek_v4_openai_profile

    floor = deepseek_v4_openai_profile()
    if profile is None:
        return floor
    updates: dict[str, object] = {}
    if not profile.openai_chat_thinking_field:
        updates["openai_chat_thinking_field"] = floor.openai_chat_thinking_field
    if not profile.openai_chat_send_back_thinking_parts:
        updates["openai_chat_send_back_thinking_parts"] = (
            floor.openai_chat_send_back_thinking_parts
        )
    if not profile.supports_thinking:
        updates["supports_thinking"] = floor.supports_thinking
    if not updates:
        return profile
    return profile.model_copy(update=updates)


def _resolve_openai_profile(model_config: ResolvedModelConfig) -> OpenAIModelProfile | None:
    """Resolve the OpenAI model profile from config.

    Priority:
      1. Explicit ``openai_profile`` on the resolved config (provider + model merge).
      2. ``provider_options.profile`` hint mapped to a built-in profile builder.
      3. Dialect-safe default for recognisable DeepSeek openai_compatible
         routes (Direct + DashScope OpenAI-compat) even when no profile hint
         is present — ensures reasoning_content parse/send-back.
      4. None — the OpenAIChatModel will use its own defaults.

    For recognisable Direct/DashScope DeepSeek, protocol-required thinking
    fields are floor-merged onto the resolved profile so a partial explicit
    ``openai_profile`` cannot drop them. Qwen / Moonshot / legacy providers
    are untouched.
    """
    from app.llm.thinking_capability import resolve_thinking_dialect

    dialect = resolve_thinking_dialect(
        adapter=model_config.adapter,
        provider=model_config.provider,
        model_name=model_config.model_name,
        base_url=model_config.base_url,
        provider_options=model_config.provider_options,
        openai_profile=model_config.openai_profile,
    )
    is_deepseek = dialect in ("deepseek_direct", "dashscope_deepseek")

    profile = _profile_from_config(model_config)
    if profile is None:
        profile_hint = str(model_config.provider_options.get("profile", ""))
        builder = _PROFILE_HINT_BUILDERS.get(profile_hint)
        if builder is not None:
            profile = builder(model_config.model_name)
        elif is_deepseek:
            from app.llm.deepseek_direct import deepseek_v4_openai_profile

            profile = deepseek_v4_openai_profile()

    if is_deepseek:
        return _ensure_deepseek_thinking_fields(profile)
    return profile


def _build_openai_compatible_model(model_config: ResolvedModelConfig) -> OpenAIChatModel | None:
    if not model_config.model_name or not model_config.base_url:
        return None

    provider = OpenAIProvider(
        base_url=model_config.base_url,
        api_key=model_config.api_key or None,
    )

    profile = _resolve_openai_profile(model_config)

    # Dialect-aware thinking normalizer: DeepSeek direct thinking strips
    # sampling knobs; enable payload is shaped per dialect. Does not mutate
    # the caller's ResolvedModelConfig / secrets store.
    from app.llm.deepseek_direct import DirectDeepSeekChatModel
    from app.llm.thinking_capability import (
        apply_thinking_to_model_settings,
        resolve_thinking_capability_from_config,
    )

    capability = resolve_thinking_capability_from_config(model_config)
    normalized_settings = apply_thinking_to_model_settings(
        model_config.model_settings, capability
    )
    settings_payload = (
        normalized_settings.to_pydantic_ai()
        if normalized_settings is not None
        else None
    )

    if capability.dialect == "deepseek_direct":
        return DirectDeepSeekChatModel(
            model_config.model_name,
            provider=provider,
            profile=profile,
            settings=settings_payload,
            thinking_mode=capability.direct_thinking_mode,
        )

    return OpenAIChatModel(
        model_config.model_name,
        provider=provider,
        profile=profile,
        settings=settings_payload,
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

    from app.llm.thinking_capability import (
        apply_thinking_to_model_settings,
        resolve_thinking_capability_from_config,
    )

    capability = resolve_thinking_capability_from_config(model_config)
    normalized = apply_thinking_to_model_settings(
        model_config.model_settings, capability
    )
    # Capability flag for history preserve (tests may also override via
    # provider_options.preserve_reasoning_content).
    provider_options = dict(model_config.provider_options)
    if capability.preserve_reasoning_on_history:
        provider_options.setdefault("preserve_reasoning_content", True)
    else:
        provider_options.setdefault("preserve_reasoning_content", False)

    settings = normalized.to_pydantic_ai() if normalized is not None else None

    async def _request(messages, agent_info):
        return await request_dashscope_chat(
            model=model_config.model_name,
            messages=list(messages),
            api_key=model_config.api_key,
            model_settings=agent_info.model_settings or (
                normalized.model_dump(exclude_none=True) if normalized else None
            ),
            provider_options=provider_options,
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
            model_settings=agent_info.model_settings or (
                normalized.model_dump(exclude_none=True) if normalized else None
            ),
            provider_options=provider_options,
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
