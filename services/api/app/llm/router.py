from __future__ import annotations

from pydantic_ai.models import Model
from pydantic_ai.models.fallback import FallbackModel

from app.config.settings import Settings
from app.llm.provider_factory import build_model_instance
from app.llm.registry import build_model_registry
from app.llm.routes import ModelRoute
from app.llm.types import (
    ModelPresetConfig,
    ModelRegistry,
    ModelSelection,
    ResolvedModelConfig,
    RouteModelSelection,
    RunModelSettings,
)


class ModelSelectionError(ValueError):
    """Raised when runtime routing references an unknown preset or profile."""


def _load_preset(
    registry: ModelRegistry,
    selection: ModelSelection | None,
) -> ModelPresetConfig | None:
    if selection is None or not selection.preset:
        return None

    preset = registry.presets.get(selection.preset)
    if preset is None:
        raise ModelSelectionError(f"Unknown model preset: {selection.preset}")
    return preset


def _route_override(
    selection: ModelSelection | None,
    route: ModelRoute,
) -> RouteModelSelection | None:
    if selection is None:
        return None
    return selection.routes.get(route)


def _resolve_profile_name(
    registry: ModelRegistry,
    route: ModelRoute,
    selection: ModelSelection | None,
) -> tuple[str | None, RouteModelSelection | None, RouteModelSelection | None]:
    preset = _load_preset(registry, selection)
    preset_route = preset.routes.get(route) if preset else None
    route_override = _route_override(selection, route)

    if route_override and route_override.profile:
        return route_override.profile, preset_route, route_override

    if preset_route and preset_route.profile:
        return preset_route.profile, preset_route, route_override

    if selection and selection.default_profile:
        return selection.default_profile, preset_route, route_override

    if preset and preset.default_profile:
        return preset.default_profile, preset_route, route_override

    if route in registry.route_defaults:
        return registry.route_defaults[route], preset_route, route_override

    return registry.default_profile, preset_route, route_override


def _resolve_fallback_profiles(
    preset_route: RouteModelSelection | None,
    route_override: RouteModelSelection | None,
) -> list[str]:
    if route_override and "fallback_profiles" in route_override.model_fields_set:
        return list(route_override.fallback_profiles)
    if preset_route and "fallback_profiles" in preset_route.model_fields_set:
        return list(preset_route.fallback_profiles)
    return []


def _resolve_route_settings(
    profile_settings: RunModelSettings | None,
    preset_route: RouteModelSelection | None,
    route_override: RouteModelSelection | None,
) -> RunModelSettings | None:
    settings_chain = profile_settings
    if preset_route and preset_route.model_settings is not None:
        settings_chain = (
            preset_route.model_settings
            if settings_chain is None
            else settings_chain.merged_with(preset_route.model_settings)
        )
    if route_override and route_override.model_settings is not None:
        settings_chain = (
            route_override.model_settings
            if settings_chain is None
            else settings_chain.merged_with(route_override.model_settings)
        )
    return settings_chain


def resolve_model_config(
    settings: Settings,
    route: ModelRoute,
    selection: ModelSelection | None = None,
) -> ResolvedModelConfig | None:
    registry = build_model_registry(settings)
    profile_name, preset_route, route_override = _resolve_profile_name(
        registry,
        route,
        selection,
    )
    if not profile_name:
        return None

    profile = registry.profiles.get(profile_name)
    if profile is None:
        raise ModelSelectionError(f"Unknown model profile for {route}: {profile_name}")
    if not profile.is_configured():
        return None

    return ResolvedModelConfig(
        route=route,
        profile_name=profile_name,
        provider=profile.provider,
        model_name=profile.model_name,
        base_url=profile.base_url,
        api_key=profile.api_key,
        provider_options=profile.provider_options,
        fallback_profiles=_resolve_fallback_profiles(preset_route, route_override),
        model_settings=_resolve_route_settings(
            profile.model_settings,
            preset_route,
            route_override,
        ),
    )


def validate_model_selection(
    settings: Settings,
    selection: ModelSelection | None,
    routes: tuple[ModelRoute, ...],
) -> None:
    if selection is None:
        return
    for route in routes:
        resolve_model_config(settings, route, selection)


def build_model_for_route(
    settings: Settings,
    route: ModelRoute,
    selection: ModelSelection | None = None,
) -> tuple[Model | str | None, ResolvedModelConfig | None]:
    model_config = resolve_model_config(settings, route, selection)
    if model_config is None:
        return None, None

    primary_model = build_model_instance(model_config)
    if primary_model is None:
        return None, model_config

    if not model_config.fallback_profiles:
        return primary_model, model_config

    registry = build_model_registry(settings)
    fallback_models = []
    for fallback_profile_name in model_config.fallback_profiles:
        fallback_profile = registry.profiles.get(fallback_profile_name)
        if fallback_profile is None:
            raise ModelSelectionError(
                f"Unknown fallback model profile for {route}: {fallback_profile_name}"
            )
        fallback_config = ResolvedModelConfig(
            route=route,
            profile_name=fallback_profile_name,
            provider=fallback_profile.provider,
            model_name=fallback_profile.model_name,
            base_url=fallback_profile.base_url,
            api_key=fallback_profile.api_key,
            provider_options=fallback_profile.provider_options,
            model_settings=model_config.model_settings or fallback_profile.model_settings,
        )
        fallback_model = build_model_instance(fallback_config)
        if fallback_model is not None:
            fallback_models.append(fallback_model)

    if not fallback_models:
        return primary_model, model_config

    return FallbackModel(primary_model, *fallback_models), model_config
