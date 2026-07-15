from __future__ import annotations

import json

import pytest

from app.config.settings import Settings
from app.services.reader_ask import model_options as model_options_svc


@pytest.fixture(autouse=True)
def _clear_catalog_cache():
    """Clear the lru_cache on _build_catalog_cached between tests to prevent
    cross-test cache pollution when Settings differ."""
    model_options_svc._build_catalog_cached.cache_clear()
    yield
    model_options_svc._build_catalog_cached.cache_clear()


def _catalog(profile_map: dict[str, str]) -> str:
    return json.dumps(
        {
            "providers": {
                "test-provider": {
                    "adapter": "openai_compatible",
                    "base_url": "https://example.test/v1",
                    "api_key": "test-key",
                }
            },
            "models": {
                f"{profile_name}__model": {
                    "provider": "test-provider",
                    "model_name": model_name,
                }
                for profile_name, model_name in profile_map.items()
            },
            "profiles": {
                profile_name: {"model": f"{profile_name}__model"}
                for profile_name in profile_map
            },
        }
    )


def test_list_reader_ask_model_options_resolves_stage_model_names(monkeypatch) -> None:
    # Avoid constructing real OpenAIProvider / httpx.AsyncClient during
    # catalog validation — we only need the resolved model names.
    monkeypatch.setattr(model_options_svc, "build_model_instance", lambda config: object())

    settings = Settings(
        annotation_model_profile="annotation",
        ask_claread_profile="ask-default",
        reader_ask_replan_model_profile="replan-default",
        model_profiles_json=_catalog(
            {
                "annotation": "annotation-model",
                "ask-default": "ask-default-model",
                "replan-default": "replan-default-model",
                "ask-pro": "glm-5.1",
                "replan-pro": "glm-5.1",
            }
        ),
        reader_ask_model_options_json=json.dumps(
            {
                "default_option": "glm-fast",
                "billing_defaults": {
                    "reserved_points": 12,
                    "tokens_per_point": 800,
                    "billing_policy_version": "analysis_weighted_tokens_v1",
                },
                "runtime_defaults": {
                    "max_input_tokens": 28000,
                    "max_output_tokens": 3600,
                    "prompt_buffer_tokens": 900,
                },
                "options": {
                    "glm-fast": {
                        "label": "GLM-5.1",
                        "description": "默认 Ask 模型",
                        "selection": {
                            "routes": {
                                "reader_ask": {"profile": "ask-pro"},
                                "reader_ask_replan": {"profile": "replan-pro"},
                            }
                        },
                        "price_multiplier": 1.5,
                        "runtime_budget": {
                            "max_output_tokens": 4200
                        },
                    }
                },
            }
        ),
    )

    items, default_key = model_options_svc.list_reader_ask_model_options(settings)

    assert default_key == "glm-fast"
    assert len(items) == 1
    assert items[0].main_model_name == "glm-5.1"
    assert items[0].replan_model_name == "glm-5.1"
    assert items[0].billing.reserved_points == 12
    assert items[0].billing.price_multiplier == 1.5
    assert items[0].runtime_budget.max_input_tokens == 28000
    assert items[0].runtime_budget.max_output_tokens == 4200
    assert items[0].runtime_budget.prompt_buffer_tokens == 900


def test_resolve_reader_ask_model_option_falls_back_for_stale_thread_key(monkeypatch) -> None:
    monkeypatch.setattr(model_options_svc, "build_model_instance", lambda config: object())

    settings = Settings(
        annotation_model_profile="annotation",
        ask_claread_profile="ask-default",
        reader_ask_replan_model_profile="replan-default",
        model_profiles_json=_catalog(
            {
                "annotation": "annotation-model",
                "ask-default": "ask-default-model",
                "replan-default": "replan-default-model",
            }
        ),
        reader_ask_model_options_json=json.dumps({}),
    )

    option = model_options_svc.resolve_reader_ask_model_option(
        settings,
        "missing-key",
        strict=False,
    )

    assert option.key == "default"
    assert option.used_fallback is True
    assert option.requested_key == "missing-key"
    assert option.main_model_name == "ask-default-model"


def test_resolve_reader_ask_model_option_falls_back_for_deleted_glm_standard(
    monkeypatch,
) -> None:
    """Historical selected_model_key='glm-standard' must soft-fallback to Flash.

    After GLM-5.1 product config removal, threads that still store glm-standard
    must resolve via strict=False to the new DeepSeek V4 Flash default without
    attempting to build any GLM profile.
    """
    monkeypatch.setattr(model_options_svc, "build_model_instance", lambda config: object())

    settings = Settings(
        annotation_model_profile="annotation",
        ask_claread_profile="ask-main-deepseek-v4-flash",
        reader_ask_replan_model_profile="ask-replan-deepseek-v4-flash",
        model_profiles_json=_catalog(
            {
                "annotation": "annotation-model",
                "ask-main-deepseek-v4-flash": "deepseek-v4-flash",
                "ask-replan-deepseek-v4-flash": "deepseek-v4-flash",
                "ask-main-deepseek-v4-pro": "deepseek-v4-pro",
                "ask-replan-deepseek-v4-pro": "deepseek-v4-pro",
            }
        ),
        reader_ask_model_options_json=json.dumps(
            {
                "default_option": "deepseek-v4-flash",
                "options": {
                    "deepseek-v4-flash": {
                        "label": "DeepSeek V4 Flash",
                        "description": "默认档位：快速、低成本。",
                        "selection": {
                            "routes": {
                                "reader_ask": {
                                    "profile": "ask-main-deepseek-v4-flash"
                                },
                                "reader_ask_replan": {
                                    "profile": "ask-replan-deepseek-v4-flash"
                                },
                            }
                        },
                    },
                    "deepseek-pro": {
                        "label": "DeepSeek V4 Pro",
                        "description": "高质量备选档位。",
                        "selection": {
                            "routes": {
                                "reader_ask": {
                                    "profile": "ask-main-deepseek-v4-pro"
                                },
                                "reader_ask_replan": {
                                    "profile": "ask-replan-deepseek-v4-pro"
                                },
                            }
                        },
                    },
                },
            }
        ),
    )

    option = model_options_svc.resolve_reader_ask_model_option(
        settings,
        "glm-standard",
        strict=False,
    )

    assert option.key == "deepseek-v4-flash"
    assert option.used_fallback is True
    assert option.requested_key == "glm-standard"
    assert option.is_default is True
    assert option.main_model_name == "deepseek-v4-flash"
    assert option.replan_model_name == "deepseek-v4-flash"
    assert option.label == "DeepSeek V4 Flash"
    # Selection must point only at Flash profiles — never a deleted GLM profile.
    assert option.selection is not None
    assert option.selection.routes is not None
    assert option.selection.routes["reader_ask"].profile == "ask-main-deepseek-v4-flash"
    assert (
        option.selection.routes["reader_ask_replan"].profile
        == "ask-replan-deepseek-v4-flash"
    )
    assert "glm" not in (option.main_model_name or "").lower()
    assert "glm" not in (option.replan_model_name or "").lower()


def test_resolve_reader_ask_model_option_rejects_explicit_deleted_glm_standard(
    monkeypatch,
) -> None:
    """Explicit user selection of a deleted key must still hard-reject under strict=True."""
    monkeypatch.setattr(model_options_svc, "build_model_instance", lambda config: object())

    settings = Settings(
        annotation_model_profile="annotation",
        ask_claread_profile="ask-main-deepseek-v4-flash",
        reader_ask_replan_model_profile="ask-replan-deepseek-v4-flash",
        model_profiles_json=_catalog(
            {
                "annotation": "annotation-model",
                "ask-main-deepseek-v4-flash": "deepseek-v4-flash",
                "ask-replan-deepseek-v4-flash": "deepseek-v4-flash",
            }
        ),
        reader_ask_model_options_json=json.dumps(
            {
                "default_option": "deepseek-v4-flash",
                "options": {
                    "deepseek-v4-flash": {
                        "label": "DeepSeek V4 Flash",
                        "selection": {
                            "routes": {
                                "reader_ask": {
                                    "profile": "ask-main-deepseek-v4-flash"
                                },
                                "reader_ask_replan": {
                                    "profile": "ask-replan-deepseek-v4-flash"
                                },
                            }
                        },
                    }
                },
            }
        ),
    )

    with pytest.raises(
        model_options_svc.ReaderAskModelOptionError,
        match="Unknown Ask Claread model option",
    ):
        model_options_svc.resolve_reader_ask_model_option(
            settings, "glm-standard", strict=True
        )


def test_list_reader_ask_model_options_default_is_deepseek_v4_flash(monkeypatch) -> None:
    """Catalog listing exposes Flash as default and never surfaces GLM options."""
    monkeypatch.setattr(model_options_svc, "build_model_instance", lambda config: object())

    settings = Settings(
        annotation_model_profile="annotation",
        ask_claread_profile="ask-main-deepseek-v4-flash",
        reader_ask_replan_model_profile="ask-replan-deepseek-v4-flash",
        model_profiles_json=_catalog(
            {
                "annotation": "annotation-model",
                "ask-main-deepseek-v4-flash": "deepseek-v4-flash",
                "ask-replan-deepseek-v4-flash": "deepseek-v4-flash",
                "ask-main-deepseek-v4-pro": "deepseek-v4-pro",
                "ask-replan-deepseek-v4-pro": "deepseek-v4-pro",
            }
        ),
        reader_ask_model_options_json=json.dumps(
            {
                "default_option": "deepseek-v4-flash",
                "options": {
                    "deepseek-v4-flash": {
                        "label": "DeepSeek V4 Flash",
                        "description": "默认档位：快速、低成本。",
                        "selection": {
                            "routes": {
                                "reader_ask": {
                                    "profile": "ask-main-deepseek-v4-flash"
                                },
                                "reader_ask_replan": {
                                    "profile": "ask-replan-deepseek-v4-flash"
                                },
                            }
                        },
                    },
                    "deepseek-pro": {
                        "label": "DeepSeek V4 Pro",
                        "description": "高质量备选档位。",
                        "selection": {
                            "routes": {
                                "reader_ask": {
                                    "profile": "ask-main-deepseek-v4-pro"
                                },
                                "reader_ask_replan": {
                                    "profile": "ask-replan-deepseek-v4-pro"
                                },
                            }
                        },
                    },
                },
            }
        ),
    )

    items, default_key = model_options_svc.list_reader_ask_model_options(settings)

    assert default_key == "deepseek-v4-flash"
    assert [item.key for item in items] == ["deepseek-v4-flash", "deepseek-pro"]
    flash = items[0]
    assert flash.is_default is True
    assert flash.main_model_name == "deepseek-v4-flash"
    assert flash.replan_model_name == "deepseek-v4-flash"
    pro = items[1]
    assert pro.is_default is False
    assert pro.main_model_name == "deepseek-v4-pro"
    assert all("glm" not in item.key.lower() for item in items)
    assert all("glm" not in (item.label or "").lower() for item in items)


def test_flash_option_has_no_pro_fallback_profiles(monkeypatch) -> None:
    """Flash must not silently upgrade cost to Pro on provider failure."""
    monkeypatch.setattr(model_options_svc, "build_model_instance", lambda config: object())

    settings = Settings(
        annotation_model_profile="annotation",
        ask_claread_profile="ask-main-deepseek-v4-flash",
        reader_ask_replan_model_profile="ask-replan-deepseek-v4-flash",
        model_profiles_json=_catalog(
            {
                "annotation": "annotation-model",
                "ask-main-deepseek-v4-flash": "deepseek-v4-flash",
                "ask-replan-deepseek-v4-flash": "deepseek-v4-flash",
                "ask-main-deepseek-v4-pro": "deepseek-v4-pro",
                "ask-replan-deepseek-v4-pro": "deepseek-v4-pro",
            }
        ),
        reader_ask_model_options_json=json.dumps(
            {
                "default_option": "deepseek-v4-flash",
                "options": {
                    "deepseek-v4-flash": {
                        "label": "DeepSeek V4 Flash",
                        "selection": {
                            "routes": {
                                "reader_ask": {
                                    "profile": "ask-main-deepseek-v4-flash"
                                },
                                "reader_ask_replan": {
                                    "profile": "ask-replan-deepseek-v4-flash"
                                },
                            }
                        },
                    },
                    "deepseek-pro": {
                        "label": "DeepSeek V4 Pro",
                        "selection": {
                            "routes": {
                                "reader_ask": {
                                    "profile": "ask-main-deepseek-v4-pro",
                                    "fallback_profiles": [
                                        "ask-main-deepseek-v4-flash"
                                    ],
                                },
                                "reader_ask_replan": {
                                    "profile": "ask-replan-deepseek-v4-pro",
                                    "fallback_profiles": [
                                        "ask-replan-deepseek-v4-flash"
                                    ],
                                },
                            }
                        },
                    },
                },
            }
        ),
    )

    flash = model_options_svc.resolve_reader_ask_model_option(
        settings, "deepseek-v4-flash", strict=True
    )
    pro = model_options_svc.resolve_reader_ask_model_option(
        settings, "deepseek-pro", strict=True
    )

    assert flash.selection is not None and flash.selection.routes is not None
    assert flash.selection.routes["reader_ask"].fallback_profiles == []
    assert flash.selection.routes["reader_ask_replan"].fallback_profiles == []
    assert "pro" not in (flash.selection.routes["reader_ask"].profile or "")
    assert pro.selection is not None and pro.selection.routes is not None
    # Pro may degrade to Flash; Flash must never auto-upgrade to Pro.
    assert "ask-main-deepseek-v4-flash" in (
        pro.selection.routes["reader_ask"].fallback_profiles or []
    )


def test_example_flash_option_has_no_pro_fallback() -> None:
    """Formal example catalog must not declare Flash → Pro fallback."""
    from pathlib import Path

    config_dir = Path(__file__).resolve().parents[1] / "config"
    example = config_dir / "reader-ask-model-options.example.json"
    payload = json.loads(example.read_text(encoding="utf-8"))
    flash = payload["options"]["deepseek-v4-flash"]
    routes = flash["selection"]["routes"]
    assert routes["reader_ask"]["profile"] == "ask-main-deepseek-v4-flash"
    assert routes["reader_ask_replan"]["profile"] == "ask-replan-deepseek-v4-flash"
    assert "fallback_profiles" not in routes["reader_ask"]
    assert "fallback_profiles" not in routes["reader_ask_replan"]
    assert payload["default_option"] == "deepseek-v4-flash"
    assert "deepseek-pro" in payload["options"]
    assert "glm-standard" not in payload["options"]


def test_resolve_reader_ask_model_option_rejects_invalid_explicit_key(monkeypatch) -> None:
    monkeypatch.setattr(model_options_svc, "build_model_instance", lambda config: object())

    settings = Settings(
        annotation_model_profile="annotation",
        ask_claread_profile="ask-default",
        reader_ask_replan_model_profile="replan-default",
        model_profiles_json=_catalog(
            {
                "annotation": "annotation-model",
                "ask-default": "ask-default-model",
                "replan-default": "replan-default-model",
            }
        ),
        reader_ask_model_options_json=json.dumps({}),
    )

    with pytest.raises(model_options_svc.ReaderAskModelOptionError, match="Unknown Ask Claread model option"):
        model_options_svc.resolve_reader_ask_model_option(settings, "missing-key", strict=True)


def test_build_reader_ask_model_catalog_rejects_unbuildable_enabled_option(
    monkeypatch,
) -> None:
    settings = Settings(
        annotation_model_profile="annotation",
        ask_claread_profile="ask-default",
        reader_ask_replan_model_profile="replan-default",
        model_profiles_json=json.dumps(
            {
                "providers": {
                    "dashscope-native": {
                        "adapter": "dashscope_native",
                        "api_key": "test-key",
                    },
                    "compat-provider": {
                        "adapter": "openai_compatible",
                        "base_url": "https://example.test/v1",
                        "api_key": "compat-key",
                    },
                },
                "models": {
                    "annotation-model": {
                        "provider": "compat-provider",
                        "model_name": "annotation-model",
                    },
                    "ask-model": {
                        "provider": "dashscope-native",
                        "model_name": "qwen3.7-max",
                    },
                    "replan-model": {
                        "provider": "dashscope-native",
                        "model_name": "qwen3.7-max",
                    },
                },
                "profiles": {
                    "annotation": {"model": "annotation-model"},
                    "ask-default": {"model": "ask-model"},
                    "replan-default": {"model": "replan-model"},
                    "ask-native": {"model": "ask-model"},
                    "replan-native": {"model": "replan-model"},
                },
            }
        ),
        reader_ask_model_options_json=json.dumps(
            {
                "default_option": "qwen-native",
                "options": {
                    "qwen-native": {
                        "label": "Qwen 3.7 Max (Native)",
                        "selection": {
                            "routes": {
                                "reader_ask": {"profile": "ask-native"},
                                "reader_ask_replan": {"profile": "replan-native"},
                            }
                        },
                    }
                },
            }
        ),
    )

    def _fail_native_build(model_config):
        if model_config.adapter == "dashscope_native":
            raise model_options_svc.ModelProviderError("Unsupported model adapter: dashscope_native")
        return object()

    monkeypatch.setattr(model_options_svc, "build_model_instance", _fail_native_build)

    with pytest.raises(model_options_svc.ModelSelectionError, match="unsupported adapter"):
        model_options_svc.build_reader_ask_model_catalog(settings)


def test_fallback_option_must_be_buildable_when_no_enabled_options_exist(
    monkeypatch,
) -> None:
    """When no catalog options are enabled, the route-default fallback must
    be buildable — otherwise the service would fail at request time."""
    settings = Settings(
        annotation_model_profile="annotation",
        ask_claread_profile="ask-default",
        reader_ask_replan_model_profile="replan-default",
        model_profiles_json=_catalog(
            {
                "annotation": "annotation-model",
                "ask-default": "ask-default-model",
                "replan-default": "replan-default-model",
            }
        ),
        reader_ask_model_options_json=json.dumps(
            {
                "options": {
                    "disabled-opt": {
                        "label": "Disabled",
                        "enabled": False,
                    }
                },
            }
        ),
    )

    # Make build_model_instance return None to simulate unbuildable config
    monkeypatch.setattr(
        model_options_svc,
        "build_model_instance",
        lambda config: None,
    )

    with pytest.raises(model_options_svc.ModelSelectionError, match="not buildable"):
        model_options_svc.build_reader_ask_model_catalog(settings)
