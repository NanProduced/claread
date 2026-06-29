from __future__ import annotations

import json
import os
from functools import lru_cache

from app.config.settings import Settings
from app.llm.routes import (
    MODEL_ROUTE_ANNOTATION_GENERATION,
    MODEL_ROUTE_DAILY_ANALYSIS,
    MODEL_ROUTE_DAILY_ANNOTATION,
    MODEL_ROUTE_DAILY_REVIEW,
    MODEL_ROUTE_DICT_AI,
    MODEL_ROUTE_RAG_EMBEDDING,
    MODEL_ROUTE_RAG_RERANK,
    MODEL_ROUTE_READER_ASK,
    MODEL_ROUTE_READER_ASK_REPLAN,
    MODEL_ROUTE_READER_LAYER_GRAMMAR_BUNDLE,
    MODEL_ROUTE_READER_LAYER_TRANSLATION,
    MODEL_ROUTE_READER_LAYER_VOCABULARY,
    MODEL_ROUTE_READER_TITLE_GENERATION,
)
from app.llm.types import (
    ModelDefinitionConfig,
    ModelPresetConfig,
    ModelProfileConfig,
    ModelProviderConfig,
    ModelRegistry,
    ModelRegistryConfigDocument,
)


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


def _load_profile_sections(
    settings: Settings,
) -> tuple[
    dict[str, ModelProviderConfig],
    dict[str, ModelDefinitionConfig],
    dict[str, ModelProfileConfig],
]:
    document = ModelRegistryConfigDocument.model_validate(
        _parse_mapping(settings.model_profiles_json, "MODEL_PROFILES_JSON")
    )
    providers = {
        provider_name: provider
        for provider_name, provider in document.providers.items()
        if provider.is_configured()
    }
    models = dict(document.models)
    profiles = {
        profile_name: profile
        for profile_name, profile in document.profiles.items()
        if profile.is_configured()
    }
    return providers, models, profiles


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
    dict_ai_model_profile: str,
    ask_claread_profile: str,
    reader_translation_model_profile: str,
    reader_vocabulary_model_profile: str,
    reader_grammar_bundle_model_profile: str,
    reader_title_model_profile: str,
    reader_ask_replan_model_profile: str,
    daily_annotation_model_profile: str,
    daily_analysis_model_profile: str,
    daily_review_model_profile: str,
    rag_embedding_model_profile: str,
    rag_rerank_model_profile: str,
    model_profiles_json: str,
    model_presets_json: str,
) -> ModelRegistry:
    settings = Settings(
        default_model_profile=default_profile,
        annotation_model_profile=annotation_model_profile,
        dict_ai_model_profile=dict_ai_model_profile,
        ask_claread_profile=ask_claread_profile,
        reader_translation_model_profile=reader_translation_model_profile,
        reader_vocabulary_model_profile=reader_vocabulary_model_profile,
        reader_grammar_bundle_model_profile=reader_grammar_bundle_model_profile,
        reader_title_model_profile=reader_title_model_profile,
        reader_ask_replan_model_profile=reader_ask_replan_model_profile,
        daily_annotation_model_profile=daily_annotation_model_profile,
        daily_analysis_model_profile=daily_analysis_model_profile,
        daily_review_model_profile=daily_review_model_profile,
        rag_embedding_model_profile=rag_embedding_model_profile,
        rag_rerank_model_profile=rag_rerank_model_profile,
        model_profiles_json=model_profiles_json,
        model_presets_json=model_presets_json,
    )
    route_defaults = {
        route: profile_name
        for route, profile_name in {
            MODEL_ROUTE_ANNOTATION_GENERATION: settings.annotation_model_profile,
            MODEL_ROUTE_DICT_AI: (
                settings.dict_ai_model_profile
                or settings.annotation_model_profile
            ),
            MODEL_ROUTE_READER_LAYER_TRANSLATION: (
                settings.reader_translation_model_profile
                or settings.annotation_model_profile
            ),
            MODEL_ROUTE_READER_LAYER_VOCABULARY: (
                settings.reader_vocabulary_model_profile
            ),
            MODEL_ROUTE_READER_LAYER_GRAMMAR_BUNDLE: (
                settings.reader_grammar_bundle_model_profile
            ),
            MODEL_ROUTE_READER_TITLE_GENERATION: (
                settings.reader_title_model_profile
                or settings.reader_translation_model_profile
                or settings.annotation_model_profile
            ),
            MODEL_ROUTE_READER_ASK: (
                settings.ask_claread_profile
                or settings.annotation_model_profile
            ),
            MODEL_ROUTE_READER_ASK_REPLAN: (
                settings.reader_ask_replan_model_profile
                or settings.ask_claread_profile
                or settings.annotation_model_profile
            ),
            MODEL_ROUTE_DAILY_ANNOTATION: settings.daily_annotation_model_profile,
            MODEL_ROUTE_DAILY_ANALYSIS: settings.daily_analysis_model_profile,
            MODEL_ROUTE_DAILY_REVIEW: settings.daily_review_model_profile,
            MODEL_ROUTE_RAG_EMBEDDING: settings.rag_embedding_model_profile,
            MODEL_ROUTE_RAG_RERANK: settings.rag_rerank_model_profile,
        }.items()
        if profile_name
    }
    providers, models, profiles = _load_profile_sections(settings)
    return ModelRegistry(
        default_profile=settings.default_model_profile or None,
        route_defaults=route_defaults,
        providers=providers,
        models=models,
        profiles=profiles,
        presets=_load_presets(settings),
    )


def build_model_registry(settings: Settings) -> ModelRegistry:
    return _build_model_registry_cached(
        default_profile=settings.default_model_profile,
        annotation_model_profile=settings.annotation_model_profile,
        dict_ai_model_profile=settings.dict_ai_model_profile,
        ask_claread_profile=settings.ask_claread_profile,
        reader_translation_model_profile=settings.reader_translation_model_profile,
        reader_vocabulary_model_profile=settings.reader_vocabulary_model_profile,
        reader_grammar_bundle_model_profile=(
            settings.reader_grammar_bundle_model_profile
        ),
        reader_title_model_profile=settings.reader_title_model_profile,
        reader_ask_replan_model_profile=settings.reader_ask_replan_model_profile,
        daily_annotation_model_profile=settings.daily_annotation_model_profile,
        daily_analysis_model_profile=settings.daily_analysis_model_profile,
        daily_review_model_profile=settings.daily_review_model_profile,
        rag_embedding_model_profile=settings.rag_embedding_model_profile,
        rag_rerank_model_profile=settings.rag_rerank_model_profile,
        model_profiles_json=settings.model_profiles_json,
        model_presets_json=settings.model_presets_json,
    )
