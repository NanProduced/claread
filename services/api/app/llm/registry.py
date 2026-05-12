from __future__ import annotations

import json
import os
from functools import lru_cache

from app.config.settings import Settings
from app.llm.routes import MODEL_ROUTE_ANNOTATION_GENERATION
from app.llm.types import ModelPresetConfig, ModelProfileConfig, ModelRegistry


def _parse_mapping(raw: str, env_name: str) -> dict[str, object]:
    if not raw.strip():
        return {}

    if os.path.isfile(raw):
        with open(raw, encoding="utf-8") as f:
            payload = json.load(f)
    else:
        payload = json.loads(raw)

    if not isinstance(payload, dict):
        raise ValueError(f"{env_name} must be a JSON object keyed by name")
    return payload


def _load_profiles(settings: Settings) -> dict[str, ModelProfileConfig]:
    payload = _parse_mapping(settings.model_profiles_json, "MODEL_PROFILES_JSON")
    profiles: dict[str, ModelProfileConfig] = {}
    for profile_name, profile_payload in payload.items():
        if not isinstance(profile_payload, dict):
            raise ValueError(f"MODEL_PROFILES_JSON[{profile_name!r}] must be a JSON object")
        profile = ModelProfileConfig.model_validate(profile_payload)
        if profile.is_configured():
            profiles[profile_name] = profile
    return profiles


def _load_presets(settings: Settings) -> dict[str, ModelPresetConfig]:
    payload = _parse_mapping(settings.model_presets_json, "MODEL_PRESETS_JSON")
    presets: dict[str, ModelPresetConfig] = {}
    for preset_name, preset_payload in payload.items():
        if not isinstance(preset_payload, dict):
            raise ValueError(f"MODEL_PRESETS_JSON[{preset_name!r}] must be a JSON object")
        presets[preset_name] = ModelPresetConfig.model_validate(preset_payload)
    return presets


@lru_cache(maxsize=1)
def _build_model_registry_cached(
    *,
    default_profile: str,
    annotation_model_profile: str,
    model_profiles_json: str,
    model_presets_json: str,
) -> ModelRegistry:
    settings = Settings(
        default_model_profile=default_profile,
        annotation_model_profile=annotation_model_profile,
        model_profiles_json=model_profiles_json,
        model_presets_json=model_presets_json,
    )
    route_defaults = {
        route: profile_name
        for route, profile_name in {
            MODEL_ROUTE_ANNOTATION_GENERATION: settings.annotation_model_profile,
        }.items()
        if profile_name
    }
    return ModelRegistry(
        default_profile=settings.default_model_profile or None,
        route_defaults=route_defaults,
        profiles=_load_profiles(settings),
        presets=_load_presets(settings),
    )


def build_model_registry(settings: Settings) -> ModelRegistry:
    return _build_model_registry_cached(
        default_profile=settings.default_model_profile,
        annotation_model_profile=settings.annotation_model_profile,
        model_profiles_json=settings.model_profiles_json,
        model_presets_json=settings.model_presets_json,
    )
