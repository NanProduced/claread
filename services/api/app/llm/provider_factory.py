from __future__ import annotations

from collections.abc import Callable

from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.providers.moonshotai import MoonshotAIProvider
from pydantic_ai.providers.openai import OpenAIProvider

from app.llm.types import ResolvedModelConfig


class ModelProviderError(ValueError):
    """Raised when a configured provider cannot be built."""


def _is_deepseek_model(model_config: ResolvedModelConfig) -> bool:
    provider_profile = model_config.provider_options.get("profile")
    return (
        provider_profile == "deepseek_v4"
        or "deepseek.com" in model_config.base_url
        or model_config.model_name.startswith("deepseek-v4-")
    )


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


def _build_openai_compatible_model(model_config: ResolvedModelConfig) -> OpenAIChatModel | None:
    if not model_config.model_name or not model_config.base_url:
        return None

    provider = OpenAIProvider(
        base_url=model_config.base_url,
        api_key=model_config.api_key or None,
    )

    profile = None
    if _is_deepseek_model(model_config):
        profile = _deepseek_v4_profile()
    elif "moonshot" in model_config.base_url or "moonshot" in model_config.model_name:
        # Moonshot is OpenAI-compatible at the transport layer, but its model profile
        # differs in important ways, especially around structured output and tool_choice.
        profile = MoonshotAIProvider.model_profile(model_config.model_name)

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


PROVIDER_BUILDERS: dict[str, Callable[[ResolvedModelConfig], Model | str | None]] = {
    "openai_compatible": _build_openai_compatible_model,
}


def build_model_instance(model_config: ResolvedModelConfig) -> Model | str | None:
    builder = PROVIDER_BUILDERS.get(model_config.provider)
    if builder is None:
        raise ModelProviderError(f"Unsupported model provider: {model_config.provider}")
    return builder(model_config)
