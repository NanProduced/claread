import json

from app.config.settings import Settings
from app.llm.provider_factory import build_model_instance
from app.llm.router import ModelSelectionError, resolve_model_config
from app.llm.routes import MODEL_ROUTE_ANNOTATION_GENERATION
from app.llm.types import ModelSelection, ResolvedModelConfig, RouteModelSelection, RunModelSettings


def test_resolve_model_config_uses_annotation_route_default() -> None:
    settings = Settings(
        annotation_model_profile="minimax_m27",
        model_profiles_json=json.dumps(
            {
                "minimax_m27": {
                    "provider": "openai_compatible",
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
        model_profiles_json=json.dumps(
            {
                "local_qwen": {
                    "provider": "openai_compatible",
                    "model_name": "Qwen/Qwen3-8B",
                    "base_url": "http://127.0.0.1:8000/v1",
                    "api_key": "",
                },
                "gpt4o_like": {
                    "provider": "openai_compatible",
                    "model_name": "gpt-4o-mini",
                    "base_url": "https://api.example.com/v1",
                    "api_key": "key",
                },
                "minimax_m27": {
                    "provider": "openai_compatible",
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


def test_resolve_model_config_uses_preset_when_no_route_override_exists() -> None:
    settings = Settings(
        annotation_model_profile="local_qwen",
        model_profiles_json=json.dumps(
            {
                "local_qwen": {
                    "provider": "openai_compatible",
                    "model_name": "Qwen/Qwen3-8B",
                    "base_url": "http://127.0.0.1:8000/v1",
                    "api_key": "",
                },
                "gpt4o_like": {
                    "provider": "openai_compatible",
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
            provider="openai_compatible",
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
