from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config.settings import Settings
from app.llm.provider_factory import ModelProviderError, build_model_instance
from app.llm.router import ModelSelectionError, resolve_model_config, validate_model_selection
from app.llm.routes import (
    MODEL_ROUTE_READER_ASK,
    MODEL_ROUTE_READER_ASK_REPLAN,
)
from app.llm.types import ModelSelection, ResolvedModelConfig
from app.services.ai_usage.billing import (
    DEFAULT_READER_ASK_BILLING_CONFIG,
    WeightedTokensBillingConfig,
)
from app.services.reader_ask import config as cfg

# Round 18: the planner LLM route and its selected-model DTO field have both
# been removed. Ask model options only resolve/build the main answer and
# repair/replan routes.
_ASK_MODEL_ROUTES = (
    MODEL_ROUTE_READER_ASK,
    MODEL_ROUTE_READER_ASK_REPLAN,
)


class ReaderAskModelOptionError(ValueError):
    """Raised when an Ask model option key is invalid."""


class ReaderAskModelOptionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    label: str = Field(min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=240)
    selection: ModelSelection | None = None
    price_multiplier: float = Field(default=1.0, gt=0.0)
    runtime_budget: "ReaderAskRuntimeBudgetConfig | None" = None
    enabled: bool = True


class ReaderAskRuntimeBudgetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_input_tokens: int = Field(default=cfg.DEFAULT_RUNTIME_MAX_INPUT_TOKENS, ge=1)
    max_output_tokens: int = Field(default=cfg.DEFAULT_RUNTIME_MAX_OUTPUT_TOKENS, ge=1)
    max_turn_output_tokens: int = Field(
        default=cfg.DEFAULT_RUNTIME_MAX_TURN_OUTPUT_TOKENS,
        ge=1,
    )
    prompt_buffer_tokens: int = Field(default=cfg.PROMPT_BUDGET_BUFFER_TOKENS, ge=0)

    @model_validator(mode="after")
    def validate_output_caps(self) -> Self:
        if self.max_turn_output_tokens < self.max_output_tokens:
            raise ValueError(
                "max_turn_output_tokens must be greater than or equal to "
                "max_output_tokens"
            )
        return self


class ReaderAskModelCatalogConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    default_option: str | None = None
    billing_defaults: WeightedTokensBillingConfig = Field(
        default_factory=lambda: DEFAULT_READER_ASK_BILLING_CONFIG.model_copy(deep=True)
    )
    runtime_defaults: ReaderAskRuntimeBudgetConfig = Field(
        default_factory=lambda: ReaderAskRuntimeBudgetConfig()
    )
    options: dict[str, ReaderAskModelOptionConfig] = Field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class ResolvedReaderAskModelOption:
    key: str
    label: str
    description: str | None
    selection: ModelSelection | None
    billing: WeightedTokensBillingConfig
    runtime_budget: ReaderAskRuntimeBudgetConfig
    main_model_name: str | None
    replan_model_name: str | None
    is_default: bool
    used_fallback: bool = False
    requested_key: str | None = None


def _parse_catalog(settings: Settings) -> ReaderAskModelCatalogConfig:
    raw = settings.reader_ask_model_options_json.strip()
    if not raw:
        return ReaderAskModelCatalogConfig()

    source = settings.resolve_config_path(raw)
    if os.path.isfile(source):
        with open(source, encoding="utf-8") as f:
            payload = json.load(f)
    else:
        payload = json.loads(raw)
    return ReaderAskModelCatalogConfig.model_validate(payload)


def _validate_buildable_route_model(
    *,
    option_key: str,
    route: str,
    model_config: ResolvedModelConfig,
    fallback_profile_name: str | None = None,
) -> None:
    target = (
        f"fallback profile {fallback_profile_name!r}"
        if fallback_profile_name is not None
        else f"profile {model_config.profile_name!r}"
    )
    try:
        model = build_model_instance(model_config)
    except ModelProviderError as exc:
        raise ModelSelectionError(
            f"Reader Ask model option {option_key!r} uses an unsupported adapter for "
            f"{route} via {target}: {exc}"
        ) from exc
    if model is None:
        raise ModelSelectionError(
            f"Reader Ask model option {option_key!r} resolves to an unbuildable model for "
            f"{route} via {target} (adapter={model_config.adapter!r}, "
            f"provider={model_config.provider!r}, model={model_config.model_name!r})"
        )


def _validate_catalog(settings: Settings, catalog: ReaderAskModelCatalogConfig) -> None:
    for option_key, option in catalog.options.items():
        if not option_key.strip():
            raise ValueError("Reader Ask model option keys must be non-empty")
        if not option.enabled:
            continue
        # resolve-only: buildability is verified separately below via
        # _validate_buildable_route_model, so no need for buildable=True here.
        validate_model_selection(settings, option.selection, _ASK_MODEL_ROUTES)
        for route in _ASK_MODEL_ROUTES:
            model_config = resolve_model_config(settings, route, option.selection)
            if model_config is None:
                raise ModelSelectionError(
                    f"Reader Ask model option {option_key!r} is missing route config for {route}"
                )
            _validate_buildable_route_model(
                option_key=option_key,
                route=route,
                model_config=model_config,
            )
            for fallback_profile_name in model_config.fallback_profiles:
                fallback_config = resolve_model_config(
                    settings,
                    route,
                    ModelSelection(default_profile=fallback_profile_name),
                )
                if fallback_config is None:
                    raise ModelSelectionError(
                        f"Reader Ask model option {option_key!r} has an invalid fallback profile "
                        f"{fallback_profile_name!r} for {route}"
                    )
                _validate_buildable_route_model(
                    option_key=option_key,
                    route=route,
                    model_config=fallback_config,
                    fallback_profile_name=fallback_profile_name,
                )

    # When no enabled options exist, the fallback (route defaults) is the only
    # option available to users — it must be buildable.
    enabled_options = _enabled_options(catalog)
    if not enabled_options:
        _validate_fallback_buildable(settings)


@lru_cache(maxsize=1)
def _build_catalog_cached(
    *,
    default_model_profile: str,
    annotation_model_profile: str,
    dict_ai_model_profile: str,
    ask_claread_profile: str,
    reader_ask_replan_model_profile: str,
    daily_annotation_model_profile: str,
    daily_analysis_model_profile: str,
    daily_review_model_profile: str,
    model_profiles_json: str,
    model_presets_json: str,
    reader_ask_model_options_json: str,
) -> ReaderAskModelCatalogConfig:
    settings = Settings(
        default_model_profile=default_model_profile,
        annotation_model_profile=annotation_model_profile,
        dict_ai_model_profile=dict_ai_model_profile,
        ask_claread_profile=ask_claread_profile,
        reader_ask_replan_model_profile=reader_ask_replan_model_profile,
        daily_annotation_model_profile=daily_annotation_model_profile,
        daily_analysis_model_profile=daily_analysis_model_profile,
        daily_review_model_profile=daily_review_model_profile,
        model_profiles_json=model_profiles_json,
        model_presets_json=model_presets_json,
        reader_ask_model_options_json=reader_ask_model_options_json,
    )
    catalog = _parse_catalog(settings)
    _validate_catalog(settings, catalog)
    return catalog


def build_reader_ask_model_catalog(settings: Settings) -> ReaderAskModelCatalogConfig:
    return _build_catalog_cached(
        default_model_profile=settings.default_model_profile,
        annotation_model_profile=settings.annotation_model_profile,
        dict_ai_model_profile=settings.dict_ai_model_profile,
        ask_claread_profile=settings.ask_claread_profile,
        reader_ask_replan_model_profile=settings.reader_ask_replan_model_profile,
        daily_annotation_model_profile=settings.daily_annotation_model_profile,
        daily_analysis_model_profile=settings.daily_analysis_model_profile,
        daily_review_model_profile=settings.daily_review_model_profile,
        model_profiles_json=settings.model_profiles_json,
        model_presets_json=settings.model_presets_json,
        reader_ask_model_options_json=settings.reader_ask_model_options_json,
    )


def _resolved_model_name(
    settings: Settings,
    route: str,
    selection: ModelSelection | None,
) -> str | None:
    try:
        config = resolve_model_config(settings, route, selection)
    except ModelSelectionError:
        return None
    return config.model_name if config is not None else None


def _resolve_option(
    settings: Settings,
    *,
    key: str,
    option: ReaderAskModelOptionConfig,
    billing_defaults: WeightedTokensBillingConfig,
    runtime_defaults: ReaderAskRuntimeBudgetConfig,
    is_default: bool,
    used_fallback: bool = False,
    requested_key: str | None = None,
) -> ResolvedReaderAskModelOption:
    billing = billing_defaults.model_copy(
        update={"price_multiplier": option.price_multiplier},
        deep=True,
    )
    runtime_budget = runtime_defaults.model_copy(deep=True)
    if option.runtime_budget is not None:
        runtime_budget = runtime_budget.model_copy(
            update=option.runtime_budget.model_dump(exclude_unset=True),
            deep=True,
        )
    return ResolvedReaderAskModelOption(
        key=key,
        label=option.label,
        description=option.description,
        selection=option.selection,
        billing=billing,
        runtime_budget=runtime_budget,
        main_model_name=_resolved_model_name(settings, MODEL_ROUTE_READER_ASK, option.selection),
        replan_model_name=_resolved_model_name(settings, MODEL_ROUTE_READER_ASK_REPLAN, option.selection),
        is_default=is_default,
        used_fallback=used_fallback,
        requested_key=requested_key,
    )


def _fallback_default_option(settings: Settings) -> ResolvedReaderAskModelOption:
    main_model_name = _resolved_model_name(settings, MODEL_ROUTE_READER_ASK, None)
    replan_model_name = _resolved_model_name(settings, MODEL_ROUTE_READER_ASK_REPLAN, None)
    label = main_model_name or "Default Ask model"
    return ResolvedReaderAskModelOption(
        key="default",
        label=label,
        description="Fallback to the current Ask Claread route defaults.",
        selection=None,
        billing=DEFAULT_READER_ASK_BILLING_CONFIG.model_copy(deep=True),
        runtime_budget=ReaderAskRuntimeBudgetConfig(),
        main_model_name=main_model_name,
        replan_model_name=replan_model_name,
        is_default=True,
    )


def _validate_fallback_buildable(settings: Settings) -> None:
    """Verify that the route-default fallback option can actually build models.

    This is a startup-time gate: if the Ask route defaults resolve but cannot
    be built (e.g. dashscope_native adapter with missing api_key), we raise
    early rather than failing at request time.
    """
    for route in _ASK_MODEL_ROUTES:
        config = resolve_model_config(settings, route, None)
        if config is None:
            raise ModelSelectionError(
                f"Ask route default for {route} does not resolve to a model config"
            )
        try:
            model = build_model_instance(config)
        except ModelProviderError as exc:
            raise ModelSelectionError(
                f"Ask route default for {route} is not buildable "
                f"(adapter={config.adapter!r}, provider={config.provider!r}): {exc}"
            ) from exc
        if model is None:
            raise ModelSelectionError(
                f"Ask route default for {route} is not buildable "
                f"(adapter={config.adapter!r}, provider={config.provider!r}, "
                f"model={config.model_name!r})"
            )


def _enabled_options(catalog: ReaderAskModelCatalogConfig) -> list[tuple[str, ReaderAskModelOptionConfig]]:
    return [
        (key, option)
        for key, option in catalog.options.items()
        if option.enabled
    ]


def resolve_default_reader_ask_model_option(settings: Settings) -> ResolvedReaderAskModelOption:
    catalog = build_reader_ask_model_catalog(settings)
    enabled_options = _enabled_options(catalog)
    if not enabled_options:
        return _fallback_default_option(settings)

    if catalog.default_option:
        option = catalog.options.get(catalog.default_option)
        if option is not None and option.enabled:
            return _resolve_option(
                settings,
                key=catalog.default_option,
                option=option,
                billing_defaults=catalog.billing_defaults,
                runtime_defaults=catalog.runtime_defaults,
                is_default=True,
            )

    first_key, first_option = enabled_options[0]
    return _resolve_option(
        settings,
        key=first_key,
        option=first_option,
        billing_defaults=catalog.billing_defaults,
        runtime_defaults=catalog.runtime_defaults,
        is_default=True,
    )


def resolve_reader_ask_model_option(
    settings: Settings,
    selected_key: str | None,
    *,
    strict: bool,
) -> ResolvedReaderAskModelOption:
    if not selected_key:
        return resolve_default_reader_ask_model_option(settings)

    catalog = build_reader_ask_model_catalog(settings)
    option = catalog.options.get(selected_key)
    if option is not None and option.enabled:
        default_option = resolve_default_reader_ask_model_option(settings)
        return _resolve_option(
            settings,
            key=selected_key,
            option=option,
            billing_defaults=catalog.billing_defaults,
            runtime_defaults=catalog.runtime_defaults,
            is_default=selected_key == default_option.key,
        )

    if strict:
        raise ReaderAskModelOptionError(f"Unknown Ask Claread model option: {selected_key}")

    fallback = resolve_default_reader_ask_model_option(settings)
    return ResolvedReaderAskModelOption(
        key=fallback.key,
        label=fallback.label,
        description=fallback.description,
        selection=fallback.selection,
        billing=fallback.billing.model_copy(deep=True),
        runtime_budget=fallback.runtime_budget.model_copy(deep=True),
        main_model_name=fallback.main_model_name,
        replan_model_name=fallback.replan_model_name,
        is_default=True,
        used_fallback=True,
        requested_key=selected_key,
    )


def list_reader_ask_model_options(settings: Settings) -> tuple[list[ResolvedReaderAskModelOption], str]:
    catalog = build_reader_ask_model_catalog(settings)
    default_option = resolve_default_reader_ask_model_option(settings)
    enabled_options = _enabled_options(catalog)
    if not enabled_options:
        return [default_option], default_option.key

    items = [
        _resolve_option(
            settings,
            key=key,
            option=option,
            billing_defaults=catalog.billing_defaults,
            runtime_defaults=catalog.runtime_defaults,
            is_default=key == default_option.key,
        )
        for key, option in enabled_options
    ]
    return items, default_option.key
