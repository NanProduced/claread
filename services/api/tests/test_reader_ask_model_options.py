from __future__ import annotations

import json

import pytest

from app.config.settings import Settings
from app.services.reader_ask import model_options as model_options_svc


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


def test_list_reader_ask_model_options_resolves_stage_model_names() -> None:
    settings = Settings(
        annotation_model_profile="annotation",
        ask_claread_profile="ask-default",
        reader_ask_planner_model_profile="planner-default",
        reader_ask_replan_model_profile="replan-default",
        model_profiles_json=_catalog(
            {
                "annotation": "annotation-model",
                "ask-default": "ask-default-model",
                "planner-default": "planner-default-model",
                "replan-default": "replan-default-model",
                "ask-pro": "glm-5.1",
                "planner-pro": "qwen3.6-plus-2026-04-02",
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
                                "reader_ask_planner": {"profile": "planner-pro"},
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
    assert items[0].planner_model_name == "qwen3.6-plus-2026-04-02"
    assert items[0].replan_model_name == "glm-5.1"
    assert items[0].billing.reserved_points == 12
    assert items[0].billing.price_multiplier == 1.5
    assert items[0].runtime_budget.max_input_tokens == 28000
    assert items[0].runtime_budget.max_output_tokens == 4200
    assert items[0].runtime_budget.prompt_buffer_tokens == 900


def test_resolve_reader_ask_model_option_falls_back_for_stale_thread_key() -> None:
    settings = Settings(
        annotation_model_profile="annotation",
        ask_claread_profile="ask-default",
        reader_ask_planner_model_profile="planner-default",
        reader_ask_replan_model_profile="replan-default",
        model_profiles_json=_catalog(
            {
                "annotation": "annotation-model",
                "ask-default": "ask-default-model",
                "planner-default": "planner-default-model",
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


def test_resolve_reader_ask_model_option_rejects_invalid_explicit_key() -> None:
    settings = Settings(
        annotation_model_profile="annotation",
        ask_claread_profile="ask-default",
        reader_ask_planner_model_profile="planner-default",
        reader_ask_replan_model_profile="replan-default",
        model_profiles_json=_catalog(
            {
                "annotation": "annotation-model",
                "ask-default": "ask-default-model",
                "planner-default": "planner-default-model",
                "replan-default": "replan-default-model",
            }
        ),
        reader_ask_model_options_json=json.dumps({}),
    )

    with pytest.raises(model_options_svc.ReaderAskModelOptionError, match="Unknown Ask Claread model option"):
        model_options_svc.resolve_reader_ask_model_option(settings, "missing-key", strict=True)
