from __future__ import annotations
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai.settings import ModelSettings

from app.llm.routes import ModelRoute


class RunModelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_tokens: int | None = Field(default=None, ge=1)
    temperature: float | None = Field(default=None, ge=0.0)
    top_p: float | None = Field(default=None, ge=0.0, le=1.0)
    timeout: float | None = Field(default=None, gt=0.0)
    parallel_tool_calls: bool | None = None
    seed: int | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    stop_sequences: list[str] | None = None
    extra_headers: dict[str, str] | None = None
    extra_body: dict[str, object] | None = None

    def merged_with(self, override: RunModelSettings | None) -> RunModelSettings:
        if override is None:
            return self.model_copy(deep=True)
        merged = self.model_dump(exclude_none=True)
        merged.update(override.model_dump(exclude_none=True))
        return RunModelSettings.model_validate(merged)

    def with_max_tokens(self, max_tokens: int) -> RunModelSettings:
        return self.model_copy(update={"max_tokens": max_tokens}, deep=True)

    def thinking_enabled(self) -> bool:
        body = self.extra_body or {}
        if body.get("enable_thinking") is True:
            return True
        thinking = body.get("thinking")
        return isinstance(thinking, dict) and thinking.get("type") == "enabled"

    def to_pydantic_ai(self) -> ModelSettings | None:
        payload = self.model_dump(exclude_none=True)
        if not payload:
            return None
        return cast(ModelSettings, payload)


class OpenAIProfileConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supports_json_object_output: bool | None = None
    supports_json_schema_output: bool | None = None
    default_structured_output_mode: str | None = None
    openai_supports_tool_choice_required: bool | None = None
    openai_supports_strict_tool_definition: bool | None = None
    openai_chat_thinking_field: str | None = None
    openai_chat_send_back_thinking_parts: Literal["field"] | None = None

    def merged_with(self, override: OpenAIProfileConfig | None) -> OpenAIProfileConfig:
        if override is None:
            return self.model_copy(deep=True)
        merged = self.model_dump(exclude_none=True)
        merged.update(override.model_dump(exclude_none=True))
        return OpenAIProfileConfig.model_validate(merged)


class ModelProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adapter: str = "openai_compatible"
    base_url: str = ""
    api_key: str = ""
    api_key_env: str = ""
    model_settings: RunModelSettings | None = None
    provider_options: dict[str, object] = Field(default_factory=dict)
    openai_profile: OpenAIProfileConfig | None = None

    def is_configured(self) -> bool:
        if self.adapter == "openai_compatible":
            return bool(self.base_url)
        return True


class ModelDefinitionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1)
    model_name: str = Field(min_length=1)
    model_settings: RunModelSettings | None = None
    provider_options: dict[str, object] = Field(default_factory=dict)
    openai_profile: OpenAIProfileConfig | None = None


class ModelProfileConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1)
    model_settings: RunModelSettings | None = None

    def is_configured(self) -> bool:
        return True


class RouteModelSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: str | None = None
    fallback_profiles: list[str] = Field(default_factory=list)
    model_settings: RunModelSettings | None = None


class ModelSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preset: str | None = None
    default_profile: str | None = None
    routes: dict[ModelRoute, RouteModelSelection] = Field(default_factory=dict)


class ModelPresetConfig(ModelSelection):
    pass


class ModelRegistryConfigDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    providers: dict[str, ModelProviderConfig] = Field(default_factory=dict)
    models: dict[str, ModelDefinitionConfig] = Field(default_factory=dict)
    profiles: dict[str, ModelProfileConfig] = Field(default_factory=dict)


class ModelRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_profile: str | None = None
    route_defaults: dict[ModelRoute, str] = Field(default_factory=dict)
    providers: dict[str, ModelProviderConfig] = Field(default_factory=dict)
    models: dict[str, ModelDefinitionConfig] = Field(default_factory=dict)
    profiles: dict[str, ModelProfileConfig] = Field(default_factory=dict)
    presets: dict[str, ModelPresetConfig] = Field(default_factory=dict)


class ResolvedModelConfig(BaseModel):
    route: ModelRoute
    profile_name: str
    provider: str
    adapter: str
    model_name: str
    base_url: str = ""
    api_key: str = ""
    provider_options: dict[str, object] = Field(default_factory=dict)
    fallback_profiles: list[str] = Field(default_factory=list)
    model_settings: RunModelSettings | None = None
    openai_profile: OpenAIProfileConfig | None = None

    def cache_key(self) -> str:
        return self.model_dump_json(exclude_none=True)


def parse_model_selection(raw: object) -> ModelSelection | None:
    if raw is None:
        return None
    if isinstance(raw, ModelSelection):
        return raw
    if isinstance(raw, dict) and not raw:
        return None
    return ModelSelection.model_validate(raw)
