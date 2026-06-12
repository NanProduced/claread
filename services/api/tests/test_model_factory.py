import json

import pytest
from pydantic_ai.models.function import FunctionModel

from app.config import settings as settings_module
from app.config.settings import Settings
from app.llm.provider_factory import build_model_instance
from app.llm.router import ModelSelectionError, resolve_model_config
from app.llm.routes import (
    MODEL_ROUTE_ANNOTATION_GENERATION,
    MODEL_ROUTE_DAILY_ANALYSIS,
    MODEL_ROUTE_DICT_AI,
    MODEL_ROUTE_READER_ASK_REPLAN,
)
from app.llm.types import ModelSelection, ResolvedModelConfig, RouteModelSelection, RunModelSettings


def _catalog(profile_specs: dict[str, dict[str, object]]) -> str:
    providers: dict[str, dict[str, object]] = {}
    models: dict[str, dict[str, object]] = {}
    profiles: dict[str, dict[str, object]] = {}

    for profile_name, spec in profile_specs.items():
        provider_name = str(spec.get("provider_name", f"{profile_name}__provider"))
        model_key = str(spec.get("model_key", f"{profile_name}__model"))

        if provider_name not in providers:
            provider_payload: dict[str, object] = {
                "adapter": spec.get("adapter", "openai_compatible"),
                "base_url": spec["base_url"],
            }
            if "api_key" in spec:
                provider_payload["api_key"] = spec["api_key"]
            if "api_key_env" in spec:
                provider_payload["api_key_env"] = spec["api_key_env"]
            if "provider_options" in spec:
                provider_payload["provider_options"] = spec["provider_options"]
            if "provider_openai_profile" in spec:
                provider_payload["openai_profile"] = spec["provider_openai_profile"]
            if "provider_model_settings" in spec:
                provider_payload["model_settings"] = spec["provider_model_settings"]
            providers[provider_name] = provider_payload

        model_payload: dict[str, object] = {
            "provider": provider_name,
            "model_name": spec["model_name"],
        }
        if "model_provider_options" in spec:
            model_payload["provider_options"] = spec["model_provider_options"]
        if "model_openai_profile" in spec:
            model_payload["openai_profile"] = spec["model_openai_profile"]
        if "model_settings" in spec:
            model_payload["model_settings"] = spec["model_settings"]
        models[model_key] = model_payload

        profile_payload: dict[str, object] = {"model": model_key}
        if "profile_model_settings" in spec:
            profile_payload["model_settings"] = spec["profile_model_settings"]
        profiles[profile_name] = profile_payload

    return json.dumps(
        {
            "providers": providers,
            "models": models,
            "profiles": profiles,
        }
    )


def test_resolve_model_config_uses_annotation_route_default() -> None:
    settings = Settings(
        annotation_model_profile="minimax_m27",
        model_profiles_json=_catalog(
            {
                "minimax_m27": {
                    "model_name": "MiniMax-M2.7",
                    "base_url": "https://api.minimax.io/v1",
                    "api_key": "test-minimax-key",
                }
            }
        ),
    )

    annotation_model = resolve_model_config(settings, MODEL_ROUTE_ANNOTATION_GENERATION)

    assert annotation_model is not None
    assert annotation_model.profile_name == "minimax_m27"
    assert annotation_model.model_name == "MiniMax-M2.7"
    assert annotation_model.base_url == "https://api.minimax.io/v1"


def test_resolve_model_config_supports_runtime_overrides_and_presets() -> None:
    settings = Settings(
        annotation_model_profile="local_qwen",
        model_profiles_json=_catalog(
            {
                "local_qwen": {
                    "model_name": "Qwen/Qwen3-8B",
                    "base_url": "http://127.0.0.1:8000/v1",
                    "api_key": "",
                },
                "gpt4o_like": {
                    "model_name": "gpt-4o-mini",
                    "base_url": "https://api.example.com/v1",
                    "api_key": "key",
                },
                "minimax_m27": {
                    "model_name": "MiniMax-M2.7",
                    "base_url": "https://api.minimax.io/v1",
                    "api_key": "test-minimax-key",
                },
            }
        ),
        model_presets_json=json.dumps(
            {
                "quality_eval": {
                    "routes": {
                        "annotation_generation": {"profile": "gpt4o_like"},
                    }
                }
            }
        ),
    )
    selection = ModelSelection(
        preset="quality_eval",
        routes={
            MODEL_ROUTE_ANNOTATION_GENERATION: RouteModelSelection(profile="minimax_m27"),
        },
    )

    annotation_model = resolve_model_config(settings, MODEL_ROUTE_ANNOTATION_GENERATION, selection)

    assert annotation_model is not None
    assert annotation_model.profile_name == "minimax_m27"


def test_resolve_model_config_uses_daily_route_default() -> None:
    settings = Settings(
        default_model_profile="shared_default",
        daily_analysis_model_profile="daily_quality",
        model_profiles_json=_catalog(
            {
                "shared_default": {
                    "model_name": "shared-default",
                    "base_url": "https://api.example.com/v1",
                    "api_key": "key",
                },
                "daily_quality": {
                    "model_name": "daily-quality-model",
                    "base_url": "https://api.example.com/v1",
                    "api_key": "key",
                },
            }
        ),
    )

    daily_model = resolve_model_config(settings, MODEL_ROUTE_DAILY_ANALYSIS)

    assert daily_model is not None
    assert daily_model.profile_name == "daily_quality"
    assert daily_model.model_name == "daily-quality-model"


def test_resolve_model_config_uses_dict_ai_route_default_with_annotation_fallback() -> None:
    settings = Settings(
        default_model_profile="shared_default",
        annotation_model_profile="annotation_quality",
        dict_ai_model_profile="",
        model_profiles_json=_catalog(
            {
                "shared_default": {
                    "model_name": "shared-default",
                    "base_url": "https://api.example.com/v1",
                    "api_key": "key",
                },
                "annotation_quality": {
                    "model_name": "annotation-quality-model",
                    "base_url": "https://api.example.com/v1",
                    "api_key": "key",
                },
            }
        ),
    )

    dict_ai_model = resolve_model_config(settings, MODEL_ROUTE_DICT_AI)

    assert dict_ai_model is not None
    assert dict_ai_model.profile_name == "annotation_quality"
    assert dict_ai_model.model_name == "annotation-quality-model"


def test_resolve_model_config_uses_preset_when_no_route_override_exists() -> None:
    settings = Settings(
        annotation_model_profile="local_qwen",
        model_profiles_json=_catalog(
            {
                "local_qwen": {
                    "model_name": "Qwen/Qwen3-8B",
                    "base_url": "http://127.0.0.1:8000/v1",
                    "api_key": "",
                },
                "gpt4o_like": {
                    "model_name": "gpt-4o-mini",
                    "base_url": "https://api.example.com/v1",
                    "api_key": "key",
                },
            }
        ),
        model_presets_json=json.dumps(
            {
                "quality_eval": {
                    "routes": {
                        "annotation_generation": {"profile": "gpt4o_like"},
                    }
                }
            }
        ),
    )

    annotation_model = resolve_model_config(
        settings,
        MODEL_ROUTE_ANNOTATION_GENERATION,
        ModelSelection(preset="quality_eval"),
    )

    assert annotation_model is not None
    assert annotation_model.profile_name == "gpt4o_like"


def test_resolve_model_config_rejects_unknown_preset() -> None:
    settings = Settings()

    try:
        resolve_model_config(
            settings,
            MODEL_ROUTE_ANNOTATION_GENERATION,
            ModelSelection(preset="missing_preset"),
        )
    except ModelSelectionError as exc:
        assert "Unknown model preset" in str(exc)
    else:
        raise AssertionError("expected ModelSelectionError for unknown preset")


def test_deepseek_v4_profile_uses_prompted_json_output() -> None:
    model = build_model_instance(
        ResolvedModelConfig(
            route=MODEL_ROUTE_ANNOTATION_GENERATION,
            profile_name="deepseek-v4-pro",
            provider="deepseek",
            adapter="openai_compatible",
            model_name="deepseek-v4-pro",
            base_url="https://api.deepseek.com",
            api_key="test-key",
            provider_options={"profile": "deepseek_v4"},
            model_settings=RunModelSettings(
                extra_body={"thinking": {"type": "disabled"}},
            ),
        )
    )

    assert model is not None
    assert model.profile.default_structured_output_mode == "prompted"
    assert model.profile.supports_json_object_output is True
    assert model.profile.supports_json_schema_output is False
    assert model.profile.openai_supports_tool_choice_required is False


def test_qwen_profile_maps_reasoning_content_for_visible_thinking() -> None:
    model = build_model_instance(
        ResolvedModelConfig(
            route=MODEL_ROUTE_ANNOTATION_GENERATION,
            profile_name="qwen35",
            provider="dashscope_compat",
            adapter="openai_compatible",
            model_name="qwen3.5-122b-a10b",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key="test-key",
            provider_options={"profile": "reasoning_content"},
            model_settings=RunModelSettings(
                extra_body={"enable_thinking": True},
            ),
        )
    )

    assert model is not None
    assert model.profile.openai_chat_thinking_field == "reasoning_content"
    assert model.profile.openai_chat_send_back_thinking_parts == "field"
    assert model.profile.openai_supports_tool_choice_required is False


def test_dashscope_native_builder_returns_function_model() -> None:
    model = build_model_instance(
        ResolvedModelConfig(
            route=MODEL_ROUTE_READER_ASK_REPLAN,
            profile_name="ask-replan-qwen37-max-native",
            provider="dashscope",
            adapter="dashscope_native",
            model_name="qwen3.7-max",
            api_key="test-key",
            provider_options={"transport": "dashscope_native"},
            model_settings=RunModelSettings(
                max_tokens=4096,
                extra_body={"enable_thinking": True},
            ),
        )
    )

    assert isinstance(model, FunctionModel)
    assert model.profile.default_structured_output_mode == "prompted"
    assert model.profile.supports_json_object_output is False
    assert model.profile.supports_json_schema_output is False


def test_resolve_model_config_carries_explicit_openai_profile_flags() -> None:
    settings = Settings(
        reader_ask_replan_model_profile="glm-replan",
        model_profiles_json=_catalog(
            {
                "glm-replan": {
                    "model_name": "glm-5.1",
                    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                    "api_key": "test-key",
                    "provider_openai_profile": {
                        "openai_chat_thinking_field": "reasoning_content",
                        "openai_chat_send_back_thinking_parts": "field",
                        "openai_supports_tool_choice_required": False,
                    },
                }
            }
        ),
    )

    config = resolve_model_config(settings, MODEL_ROUTE_READER_ASK_REPLAN)

    assert config is not None
    assert config.openai_profile is not None
    assert config.openai_profile.openai_supports_tool_choice_required is False


def test_resolve_model_config_supports_decoupled_provider_model_catalog(
    monkeypatch,
) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-test-key")
    settings = Settings(
        annotation_model_profile="workflow-qwen37max",
        model_profiles_json=json.dumps(
            {
                "providers": {
                    "dashscope": {
                        "adapter": "openai_compatible",
                        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                        "api_key_env": "DASHSCOPE_API_KEY",
                        "openai_profile": {
                            "openai_chat_thinking_field": "reasoning_content",
                            "openai_chat_send_back_thinking_parts": "field",
                            "openai_supports_tool_choice_required": False,
                        },
                    }
                },
                "models": {
                    "qwen37-max": {
                        "provider": "dashscope",
                        "model_name": "qwen3.7-max",
                    }
                },
                "profiles": {
                    "workflow-qwen37max": {
                        "model": "qwen37-max",
                        "model_settings": {
                            "extra_body": {
                                "enable_thinking": False,
                            }
                        },
                    },
                    "ask-qwen37max": {
                        "model": "qwen37-max",
                        "model_settings": {
                            "extra_body": {
                                "enable_thinking": True,
                            }
                        },
                    },
                },
            }
        ),
    )

    workflow_config = resolve_model_config(
        settings,
        MODEL_ROUTE_ANNOTATION_GENERATION,
        ModelSelection(default_profile="workflow-qwen37max"),
    )
    ask_config = resolve_model_config(
        settings,
        MODEL_ROUTE_ANNOTATION_GENERATION,
        ModelSelection(default_profile="ask-qwen37max"),
    )

    assert workflow_config is not None
    assert workflow_config.provider == "dashscope"
    assert workflow_config.adapter == "openai_compatible"
    assert workflow_config.model_name == "qwen3.7-max"
    assert workflow_config.api_key == "dashscope-test-key"
    assert workflow_config.model_settings is not None
    assert workflow_config.model_settings.extra_body == {"enable_thinking": False}
    assert workflow_config.openai_profile is not None
    assert workflow_config.openai_profile.openai_supports_tool_choice_required is False

    assert ask_config is not None
    assert ask_config.provider == "dashscope"
    assert ask_config.model_name == "qwen3.7-max"
    assert ask_config.model_settings is not None
    assert ask_config.model_settings.extra_body == {"enable_thinking": True}


def test_resolve_model_config_reads_provider_key_from_local_env_loader(
    monkeypatch,
) -> None:
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.setattr(
        settings_module,
        "_load_local_env_values",
        lambda: {"DASHSCOPE_API_KEY": "dotenv-dashscope-key"},
    )
    settings = Settings(
        annotation_model_profile="workflow-qwen36-plus",
        model_profiles_json=json.dumps(
            {
                "providers": {
                    "dashscope": {
                        "adapter": "openai_compatible",
                        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                        "api_key_env": "DASHSCOPE_API_KEY",
                    }
                },
                "models": {
                    "qwen36-plus": {
                        "provider": "dashscope",
                        "model_name": "qwen3.6-plus-2026-04-02",
                    }
                },
                "profiles": {
                    "workflow-qwen36-plus": {
                        "model": "qwen36-plus",
                    }
                },
            }
        ),
    )

    config = resolve_model_config(settings, MODEL_ROUTE_ANNOTATION_GENERATION)

    assert config is not None
    assert config.api_key == "dotenv-dashscope-key"


def test_dashscope_native_provider_is_configured_without_base_url() -> None:
    """dashscope_native adapter does not require base_url; api_key suffices."""
    from app.llm.types import ModelProviderConfig

    native_with_key = ModelProviderConfig(
        adapter="dashscope_native",
        api_key="test-key",
    )
    assert native_with_key.is_configured() is True

    native_with_env = ModelProviderConfig(
        adapter="dashscope_native",
        api_key_env="DASHSCOPE_API_KEY",
    )
    assert native_with_env.is_configured() is True

    native_no_key = ModelProviderConfig(
        adapter="dashscope_native",
    )
    assert native_no_key.is_configured() is False


def test_resolve_model_config_with_dashscope_native_provider(
    monkeypatch,
) -> None:
    """Route resolution should work with dashscope_native provider that has no base_url."""
    monkeypatch.setenv("DASHSCOPE_API_KEY", "native-test-key")
    settings = Settings(
        ask_claread_profile="ask-native",
        model_profiles_json=json.dumps(
            {
                "providers": {
                    "dashscope": {
                        "adapter": "dashscope_native",
                        "api_key_env": "DASHSCOPE_API_KEY",
                    },
                },
                "models": {
                    "qwen37-max-native": {
                        "provider": "dashscope",
                        "model_name": "qwen3.7-max",
                    },
                },
                "profiles": {
                    "ask-native": {
                        "model": "qwen37-max-native",
                        "model_settings": {
                            "extra_body": {"enable_thinking": True},
                        },
                    },
                },
            }
        ),
    )

    config = resolve_model_config(
        settings,
        MODEL_ROUTE_ANNOTATION_GENERATION,
        ModelSelection(default_profile="ask-native"),
    )

    assert config is not None
    assert config.adapter == "dashscope_native"
    assert config.provider == "dashscope"
    assert config.model_name == "qwen3.7-max"
    assert config.api_key == "native-test-key"
    assert config.base_url == ""


def test_dashscope_native_and_compat_coexist_in_same_registry(
    monkeypatch,
) -> None:
    """Both dashscope (native) and dashscope_compat can coexist in the same config."""
    monkeypatch.setenv("DASHSCOPE_API_KEY", "native-test-key")
    settings = Settings(
        annotation_model_profile="workflow-compat",
        ask_claread_profile="ask-native",
        model_profiles_json=json.dumps(
            {
                "providers": {
                    "dashscope": {
                        "adapter": "dashscope_native",
                        "api_key_env": "DASHSCOPE_API_KEY",
                    },
                    "dashscope_compat": {
                        "adapter": "openai_compatible",
                        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                        "api_key_env": "DASHSCOPE_API_KEY",
                    },
                },
                "models": {
                    "qwen37-max-native": {
                        "provider": "dashscope",
                        "model_name": "qwen3.7-max",
                    },
                    "qwen37-max-compat": {
                        "provider": "dashscope_compat",
                        "model_name": "qwen3.7-max",
                    },
                },
                "profiles": {
                    "ask-native": {
                        "model": "qwen37-max-native",
                        "model_settings": {
                            "extra_body": {"enable_thinking": True},
                        },
                    },
                    "workflow-compat": {
                        "model": "qwen37-max-compat",
                        "model_settings": {
                            "extra_body": {"enable_thinking": False},
                        },
                    },
                },
            }
        ),
    )

    ask_config = resolve_model_config(
        settings,
        MODEL_ROUTE_ANNOTATION_GENERATION,
        ModelSelection(default_profile="ask-native"),
    )
    workflow_config = resolve_model_config(settings, MODEL_ROUTE_ANNOTATION_GENERATION)

    assert ask_config is not None
    assert ask_config.adapter == "dashscope_native"
    assert ask_config.model_settings is not None
    assert ask_config.model_settings.extra_body == {"enable_thinking": True}

    assert workflow_config is not None
    assert workflow_config.adapter == "openai_compatible"
    assert workflow_config.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert workflow_config.model_settings is not None
    assert workflow_config.model_settings.extra_body == {"enable_thinking": False}


def test_validate_model_selection_buildable_catches_unbuildable_config(
    monkeypatch,
) -> None:
    """validate_model_selection with buildable=True should catch configs that
    resolve but cannot be built."""
    from app.llm.router import validate_model_selection

    settings = Settings(
        annotation_model_profile="test-profile",
        model_profiles_json=json.dumps(
            {
                "providers": {
                    "test-provider": {
                        "adapter": "openai_compatible",
                        "base_url": "https://example.test/v1",
                        "api_key": "test-key",
                    },
                },
                "models": {
                    "test-model": {
                        "provider": "test-provider",
                        "model_name": "test-model",
                    },
                },
                "profiles": {
                    "test-profile": {
                        "model": "test-model",
                    },
                },
            }
        ),
    )

    # Simulate build_model_instance returning None (unbuildable)
    monkeypatch.setattr(
        "app.llm.router.build_model_instance",
        lambda config: None,
    )

    with pytest.raises(ModelSelectionError, match="unbuildable model"):
        validate_model_selection(
            settings,
            ModelSelection(default_profile="test-profile"),
            (MODEL_ROUTE_ANNOTATION_GENERATION,),
            buildable=True,
        )


def test_validate_model_selection_buildable_converts_model_provider_error(
    monkeypatch,
) -> None:
    from app.llm.provider_factory import ModelProviderError
    from app.llm.router import validate_model_selection

    settings = Settings(
        annotation_model_profile="test-profile",
        model_profiles_json=json.dumps(
            {
                "providers": {
                    "test-provider": {
                        "adapter": "openai_compatible",
                        "base_url": "https://example.test/v1",
                        "api_key": "test-key",
                    },
                },
                "models": {
                    "test-model": {
                        "provider": "test-provider",
                        "model_name": "test-model",
                    },
                },
                "profiles": {
                    "test-profile": {
                        "model": "test-model",
                    },
                },
            }
        ),
    )

    def _raise_provider_error(config):
        raise ModelProviderError("Unsupported model adapter: test")

    monkeypatch.setattr(
        "app.llm.router.build_model_instance",
        _raise_provider_error,
    )

    with pytest.raises(ModelSelectionError, match="unbuildable adapter"):
        validate_model_selection(
            settings,
            ModelSelection(default_profile="test-profile"),
            (MODEL_ROUTE_ANNOTATION_GENERATION,),
            buildable=True,
        )


def test_validate_model_selection_buildable_catches_unbuildable_fallback_config(
    monkeypatch,
) -> None:
    from app.llm.router import validate_model_selection

    settings = Settings(
        annotation_model_profile="primary-profile",
        model_profiles_json=json.dumps(
            {
                "providers": {
                    "test-provider": {
                        "adapter": "openai_compatible",
                        "base_url": "https://example.test/v1",
                        "api_key": "test-key",
                    },
                },
                "models": {
                    "primary-model": {
                        "provider": "test-provider",
                        "model_name": "primary-model",
                    },
                    "fallback-model": {
                        "provider": "test-provider",
                        "model_name": "fallback-model",
                    },
                },
                "profiles": {
                    "primary-profile": {
                        "model": "primary-model",
                    },
                    "fallback-profile": {
                        "model": "fallback-model",
                    },
                },
            }
        ),
    )

    monkeypatch.setattr(
        "app.llm.router.build_model_instance",
        lambda config: None if config.model_name == "fallback-model" else object(),
    )

    with pytest.raises(ModelSelectionError, match="fallback profile 'fallback-profile'"):
        validate_model_selection(
            settings,
            ModelSelection(
                routes={
                    MODEL_ROUTE_ANNOTATION_GENERATION: RouteModelSelection(
                        profile="primary-profile",
                        fallback_profiles=["fallback-profile"],
                    )
                }
            ),
            (MODEL_ROUTE_ANNOTATION_GENERATION,),
            buildable=True,
        )


def test_validate_model_selection_resolve_only_allows_unbuildable_config(
    monkeypatch,
) -> None:
    """validate_model_selection with buildable=False (default) should only
    check resolution, not buildability."""
    from app.llm.router import validate_model_selection

    settings = Settings(
        annotation_model_profile="test-profile",
        model_profiles_json=json.dumps(
            {
                "providers": {
                    "test-provider": {
                        "adapter": "openai_compatible",
                        "base_url": "https://example.test/v1",
                        "api_key": "test-key",
                    },
                },
                "models": {
                    "test-model": {
                        "provider": "test-provider",
                        "model_name": "test-model",
                    },
                },
                "profiles": {
                    "test-profile": {
                        "model": "test-model",
                    },
                },
            }
        ),
    )

    # Even if build_model_instance returns None, resolve-only validation passes
    monkeypatch.setattr(
        "app.llm.router.build_model_instance",
        lambda config: None,
    )

    # Should NOT raise — resolve-only is the default
    validate_model_selection(
        settings,
        ModelSelection(default_profile="test-profile"),
        (MODEL_ROUTE_ANNOTATION_GENERATION,),
    )


def test_explicit_openai_profile_overrides_provider_options_hint() -> None:
    """When both openai_profile and provider_options.profile are set,
    the explicit openai_profile takes priority."""
    from app.llm.types import OpenAIProfileConfig

    model = build_model_instance(
        ResolvedModelConfig(
            route=MODEL_ROUTE_ANNOTATION_GENERATION,
            profile_name="custom",
            provider="dashscope_compat",
            adapter="openai_compatible",
            model_name="qwen3.7-max",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key="test-key",
            provider_options={"profile": "deepseek_v4"},
            openai_profile=OpenAIProfileConfig(
                openai_supports_tool_choice_required=True,
                openai_chat_thinking_field="reasoning_content",
                openai_chat_send_back_thinking_parts="field",
            ),
        )
    )

    assert model is not None
    # Explicit openai_profile sets tool_choice_required=True;
    # deepseek_v4 hint would have set it to False — explicit wins.
    assert model.profile.openai_supports_tool_choice_required is True


def test_no_profile_hint_uses_default_behavior() -> None:
    """When neither openai_profile nor provider_options.profile is set,
    OpenAIChatModel uses pydantic-ai defaults (no custom profile)."""
    model = build_model_instance(
        ResolvedModelConfig(
            route=MODEL_ROUTE_ANNOTATION_GENERATION,
            profile_name="generic",
            provider="generic_provider",
            adapter="openai_compatible",
            model_name="some-model",
            base_url="https://api.example.com/v1",
            api_key="test-key",
            provider_options={},
        )
    )

    assert model is not None
    # Default profile should not have reasoning_content fields set
    assert model.profile.openai_chat_thinking_field is None


def test_moonshot_profile_hint_resolves() -> None:
    """provider_options.profile='moonshot' should resolve to MoonshotAIProvider's profile."""
    model = build_model_instance(
        ResolvedModelConfig(
            route=MODEL_ROUTE_ANNOTATION_GENERATION,
            profile_name="moonshot-v1",
            provider="moonshot",
            adapter="openai_compatible",
            model_name="moonshot-v1-8k",
            base_url="https://api.moonshot.cn/v1",
            api_key="test-key",
            provider_options={"profile": "moonshot"},
        )
    )

    assert model is not None
    # MoonshotAIProvider.model_profile sets specific fields
    assert model.profile.openai_supports_tool_choice_required is False


def test_resolve_model_config_uses_moonshot_provider_hint_from_registry() -> None:
    settings = Settings(
        annotation_model_profile="workflow-kimi-k26",
        model_profiles_json=json.dumps(
            {
                "providers": {
                    "moonshot": {
                        "adapter": "openai_compatible",
                        "base_url": "https://api.moonshot.cn/v1",
                        "api_key": "test-key",
                        "provider_options": {
                            "profile": "moonshot",
                        },
                    }
                },
                "models": {
                    "kimi-k26": {
                        "provider": "moonshot",
                        "model_name": "kimi-k2.6",
                    }
                },
                "profiles": {
                    "workflow-kimi-k26": {
                        "model": "kimi-k26",
                    }
                },
            }
        ),
    )

    config = resolve_model_config(settings, MODEL_ROUTE_ANNOTATION_GENERATION)

    assert config is not None
    assert config.provider == "moonshot"
    assert config.provider_options.get("profile") == "moonshot"

    model = build_model_instance(config)
    assert model is not None
    assert model.profile.openai_supports_tool_choice_required is False


def test_resolved_model_config_adapter_is_model_adapter_type() -> None:
    """ResolvedModelConfig.adapter should be ModelAdapter, not arbitrary str."""
    from app.llm.types import ModelAdapter

    config = ResolvedModelConfig(
        route=MODEL_ROUTE_ANNOTATION_GENERATION,
        profile_name="test",
        provider="test",
        adapter="openai_compatible",
        model_name="test-model",
        base_url="https://api.example.com/v1",
        api_key="test-key",
    )
    assert isinstance(config.adapter, str)
    assert config.adapter in ("openai_compatible", "dashscope_native")

    # Invalid adapter should fail validation
    with pytest.raises(Exception):
        ResolvedModelConfig(
            route=MODEL_ROUTE_ANNOTATION_GENERATION,
            profile_name="test",
            provider="test",
            adapter="invalid_adapter",
            model_name="test-model",
            base_url="https://api.example.com/v1",
            api_key="test-key",
        )
