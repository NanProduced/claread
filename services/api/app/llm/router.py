from __future__ import annotations

from pydantic_ai.models import Model
from pydantic_ai.models.fallback import FallbackModel

from app.config.settings import Settings
from app.llm.provider_factory import ModelProviderError, build_model_instance
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


def _merge_settings(
    base: RunModelSettings | None,
    override: RunModelSettings | None,
) -> RunModelSettings | None:
    if base is None:
        return override.model_copy(deep=True) if override is not None else None
    return base.merged_with(override)


def _resolve_base_profile_config(
    settings: Settings,
    registry: ModelRegistry,
    route: ModelRoute,
    profile_name: str,
) -> ResolvedModelConfig | None:
    profile = registry.profiles.get(profile_name)
    if profile is None:
        raise ModelSelectionError(f"Unknown model profile for {route}: {profile_name}")
    if not profile.is_configured():
        return None

    model = registry.models.get(profile.model)
    if model is None:
        raise ModelSelectionError(
            f"Unknown model reference for profile {profile_name!r}: {profile.model}"
        )

    provider = registry.providers.get(model.provider)
    if provider is None:
        raise ModelSelectionError(
            f"Unknown provider reference for model {profile.model!r}: {model.provider}"
        )
    if not provider.is_configured():
        return None

    provider_options = dict(provider.provider_options)
    provider_options.update(model.provider_options)

    openai_profile = provider.openai_profile
    if model.openai_profile is not None:
        openai_profile = (
            model.openai_profile.model_copy(deep=True)
            if openai_profile is None
            else openai_profile.merged_with(model.openai_profile)
        )

    model_settings = _merge_settings(provider.model_settings, model.model_settings)
    model_settings = _merge_settings(model_settings, profile.model_settings)

    return ResolvedModelConfig(
        route=route,
        profile_name=profile_name,
        provider=model.provider,
        adapter=provider.adapter,
        model_name=model.model_name,
        base_url=provider.base_url,
        api_key=settings.resolve_external_env_var(
            provider.api_key_env,
            fallback=provider.api_key,
        ),
        provider_options=provider_options,
        model_settings=model_settings,
        openai_profile=openai_profile,
    )


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

    base_config = _resolve_base_profile_config(settings, registry, route, profile_name)
    if base_config is None:
        return None

    return base_config.model_copy(
        update={
            "fallback_profiles": _resolve_fallback_profiles(preset_route, route_override),
            "model_settings": _resolve_route_settings(
                base_config.model_settings,
                preset_route,
                route_override,
            ),
        },
        deep=True,
    )


def _validate_buildable_config(
    route: ModelRoute,
    config: ResolvedModelConfig,
    *,
    target: str | None = None,
) -> None:
    target_label = target or f"profile {config.profile_name!r}"
    try:
        model = build_model_instance(config)
    except ModelProviderError as exc:
        raise ModelSelectionError(
            f"Model selection for route {route} references an unbuildable adapter via "
            f"{target_label} (adapter={config.adapter!r}, provider={config.provider!r}): {exc}"
        ) from exc
    if model is None:
        raise ModelSelectionError(
            f"Model selection for route {route} resolves to an unbuildable model via "
            f"{target_label} (adapter={config.adapter!r}, provider={config.provider!r}, "
            f"model={config.model_name!r})"
        )


def validate_model_selection(
    settings: Settings,
    selection: ModelSelection | None,
    routes: tuple[ModelRoute, ...],
    *,
    buildable: bool = False,
) -> None:
    """Validate that a model selection resolves for every given route.

    Args:
        buildable: If True, also verify that each resolved config can be
            built into a live model via ``build_model_instance``. This
            includes the primary resolved config and any declared
            ``fallback_profiles`` for the route. This is the "buildability"
            gate — it catches cases where a profile resolves successfully but
            references an adapter whose builder returns None (e.g. missing
            api_key at build time).

            When False (default), only resolution is checked.  This is
            appropriate for static catalog / listing scenarios where the
            caller only needs to know the model identity, not actually
            construct a model instance.
    """
    if selection is None:
        return
    registry = build_model_registry(settings) if buildable else None
    for route in routes:
        config = resolve_model_config(settings, route, selection)
        if config is None:
            raise ModelSelectionError(
                f"Model selection resolves to None for route {route}"
            )
        if buildable:
            _validate_buildable_config(route, config)
            assert registry is not None
            for fallback_profile_name in config.fallback_profiles:
                fallback_config = _resolve_base_profile_config(
                    settings,
                    registry,
                    route,
                    fallback_profile_name,
                )
                if fallback_config is None:
                    raise ModelSelectionError(
                        f"Model selection for route {route} has an unavailable fallback profile "
                        f"{fallback_profile_name!r}"
                    )
                fallback_config = fallback_config.model_copy(
                    update={
                        "model_settings": config.model_settings or fallback_config.model_settings,
                    },
                    deep=True,
                )
                _validate_buildable_config(
                    route,
                    fallback_config,
                    target=f"fallback profile {fallback_profile_name!r}",
                )


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
        fallback_config = _resolve_base_profile_config(
            settings,
            registry,
            route,
            fallback_profile_name,
        )
        if fallback_config is None:
            continue
        fallback_config = fallback_config.model_copy(
            update={
                "model_settings": model_config.model_settings or fallback_config.model_settings,
            },
            deep=True,
        )
        fallback_model = build_model_instance(fallback_config)
        if fallback_model is not None:
            fallback_models.append(fallback_model)

    if not fallback_models:
        return primary_model, model_config

    return FallbackModel(primary_model, *fallback_models), model_config
