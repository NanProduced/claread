"""Tests for the DashScope native SDK adapter and Ask-routing decisions."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.tools import ToolDefinition

from app.llm.provider_factory import (
    PROVIDER_BUILDERS,
    _build_dashscope_native_model,
    _dashscope_native_profile,
    build_model_instance,
)
from app.llm.router import resolve_model_config
from app.llm.routes import (
    MODEL_ROUTE_READER_ASK,
    MODEL_ROUTE_READER_ASK_PLANNER,
)
from app.llm.types import (
    ModelProviderConfig,
    ModelSelection,
    ResolvedModelConfig,
    RunModelSettings,
)
from app.config.settings import Settings


def _native_settings(api_key: str = "k") -> Settings:
    return Settings(
        ask_claread_profile="ask-main-qwen37-max-native",
        model_profiles_json=json.dumps(
            {
                "providers": {
                    "dashscope": {
                        "adapter": "openai_compatible",
                        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                        "api_key_env": "DASHSCOPE_API_KEY",
                    },
                    "dashscope_native": {
                        "adapter": "dashscope_native",
                        "api_key_env": "DASHSCOPE_API_KEY",
                    },
                },
                "models": {
                    "qwen37-max": {
                        "provider": "dashscope",
                        "model_name": "qwen3.7-max",
                    },
                    "qwen37-max-native": {
                        "provider": "dashscope_native",
                        "model_name": "qwen3.7-max",
                        "model_settings": {
                            "extra_body": {"enable_thinking": True},
                        },
                    },
                    "glm51-native": {
                        "provider": "dashscope_native",
                        "model_name": "glm-5.1",
                        "model_settings": {
                            "extra_body": {"enable_thinking": True},
                        },
                    },
                    "qwen36-plus": {
                        "provider": "dashscope",
                        "model_name": "qwen3.6-plus-2026-04-02",
                    },
                },
                "profiles": {
                    "ask-main-qwen37-max-native": {
                        "model": "qwen37-max-native",
                    },
                    "ask-replan-qwen37-max-native": {
                        "model": "qwen37-max-native",
                    },
                    "ask-main-glm51-native": {
                        "model": "glm51-native",
                    },
                    "ask-replan-glm51-native": {
                        "model": "glm51-native",
                    },
                    "ask-planner-qwen36-plus": {
                        "model": "qwen36-plus",
                    },
                },
            }
        ),
        reader_ask_model_options_json=json.dumps(
            {
                "default_option": "qwen-max",
                "options": {
                    "qwen-max": {
                        "label": "Qwen 3.7 Max",
                        "selection": {
                            "routes": {
                                "reader_ask": {"profile": "ask-main-qwen37-max-native"},
                                "reader_ask_planner": {"profile": "ask-planner-qwen36-plus"},
                                "reader_ask_replan": {"profile": "ask-replan-qwen37-max-native"},
                            }
                        },
                        "price_multiplier": 1.0,
                    },
                    "glm-standard": {
                        "label": "GLM-5.1",
                        "selection": {
                            "routes": {
                                "reader_ask": {"profile": "ask-main-glm51-native"},
                                "reader_ask_planner": {"profile": "ask-planner-qwen36-plus"},
                                "reader_ask_replan": {"profile": "ask-replan-glm51-native"},
                            }
                        },
                        "price_multiplier": 1.0,
                    },
                },
            }
        ),
    )


def test_provider_builders_registry_contains_dashscope_native() -> None:
    assert "dashscope_native" in PROVIDER_BUILDERS
    assert PROVIDER_BUILDERS["dashscope_native"] is _build_dashscope_native_model


def test_dashscope_native_builder_returns_function_model() -> None:
    config = ResolvedModelConfig(
        route=MODEL_ROUTE_READER_ASK,
        profile_name="ask-main-qwen37-max-native",
        provider="dashscope_native",
        adapter="dashscope_native",
        model_name="qwen3.7-max",
        api_key="k",
    )
    model = _build_dashscope_native_model(config)
    assert isinstance(model, FunctionModel)
    assert model.model_name == "qwen3.7-max"
    assert model.function is not None
    assert model.stream_function is not None


def test_dashscope_native_builder_returns_none_when_missing_credentials() -> None:
    config = ResolvedModelConfig(
        route=MODEL_ROUTE_READER_ASK,
        profile_name="ask-main-qwen37-max-native",
        provider="dashscope_native",
        adapter="dashscope_native",
        model_name="qwen3.7-max",
    )
    assert _build_dashscope_native_model(config) is None


def test_dashscope_native_profile_marks_prompted_structured_output() -> None:
    profile = _dashscope_native_profile()
    assert profile.supports_json_object_output is False
    assert profile.supports_json_schema_output is False
    assert profile.default_structured_output_mode == "prompted"
    # supports_thinking must be True so the agent graph forwards ThinkingPart
    # events emitted by the native FunctionModel stream.
    assert profile.supports_thinking is True


def test_model_provider_config_native_does_not_require_base_url() -> None:
    provider = ModelProviderConfig(
        adapter="dashscope_native",
        api_key_env="DASHSCOPE_API_KEY",
    )
    assert provider.is_configured() is True
    # and even with explicit empty base_url
    provider2 = ModelProviderConfig(adapter="dashscope_native", api_key_env="")
    assert provider2.is_configured() is False
    # empty api_key + empty api_key_env is unconfigured
    provider3 = ModelProviderConfig(adapter="dashscope_native")
    assert provider3.is_configured() is False


def test_resolve_ask_main_routes_to_native_adapter() -> None:
    settings = _native_settings()
    config = resolve_model_config(
        settings,
        MODEL_ROUTE_READER_ASK,
        ModelSelection(default_profile="ask-main-qwen37-max-native"),
    )
    assert config is not None
    assert config.adapter == "dashscope_native"
    assert config.provider == "dashscope_native"
    assert config.model_name == "qwen3.7-max"
    assert config.model_settings is not None
    assert config.model_settings.extra_body == {"enable_thinking": True}


def test_resolve_ask_replan_routes_to_native_adapter() -> None:
    settings = _native_settings()
    config = resolve_model_config(
        settings,
        MODEL_ROUTE_READER_ASK,
        ModelSelection(default_profile="ask-replan-qwen37-max-native"),
    )
    assert config is not None
    assert config.adapter == "dashscope_native"
    assert config.model_name == "qwen3.7-max"
    model = build_model_instance(config)
    assert isinstance(model, FunctionModel)
    assert model.function is not None


def test_resolve_ask_planner_stays_compat() -> None:
    settings = _native_settings()
    config = resolve_model_config(
        settings,
        MODEL_ROUTE_READER_ASK_PLANNER,
        ModelSelection(default_profile="ask-planner-qwen36-plus"),
    )
    assert config is not None
    assert config.adapter == "openai_compatible"
    assert config.model_name == "qwen3.6-plus-2026-04-02"


def test_resolve_ask_glm_standard_routes_to_native_adapter() -> None:
    settings = _native_settings()
    config = resolve_model_config(
        settings,
        MODEL_ROUTE_READER_ASK,
        ModelSelection(default_profile="ask-main-glm51-native"),
    )
    assert config is not None
    assert config.adapter == "dashscope_native"
    assert config.model_name == "glm-5.1"


def test_build_model_instance_dispatches_to_native_builder() -> None:
    config = ResolvedModelConfig(
        route=MODEL_ROUTE_READER_ASK,
        profile_name="ask-main-qwen37-max-native",
        provider="dashscope_native",
        adapter="dashscope_native",
        model_name="qwen3.7-max",
        api_key="k",
    )
    model = build_model_instance(config)
    assert isinstance(model, FunctionModel)


@pytest.mark.asyncio
async def test_dashscope_native_builder_forwards_agent_runtime_settings_and_tools() -> None:
    config = ResolvedModelConfig(
        route=MODEL_ROUTE_READER_ASK,
        profile_name="ask-main-qwen37-max-native",
        provider="dashscope_native",
        adapter="dashscope_native",
        model_name="qwen3.7-max",
        api_key="k",
    )
    model = _build_dashscope_native_model(config)
    assert model is not None
    assert model.function is not None
    assert model.stream_function is not None

    tool = ToolDefinition(
        name="get_record_context",
        description="Load record context",
        parameters_json_schema={"type": "object", "properties": {}},
    )
    agent_info = SimpleNamespace(
        model_settings={"max_tokens": 256, "extra_body": {"enable_thinking": True}},
        function_tools=[tool],
        output_tools=[],
        allow_text_output=True,
    )

    with patch("app.llm.provider_factory.request_dashscope_chat", new=AsyncMock()) as mock_request:
        mock_request.return_value = MagicMock()
        await model.function([], agent_info)

    kwargs = mock_request.await_args.kwargs
    assert kwargs["model_settings"] == {"max_tokens": 256, "extra_body": {"enable_thinking": True}}
    assert kwargs["function_tools"] == [tool]
    assert kwargs["allow_text_output"] is True

    captured_stream_kwargs: dict[str, object] = {}

    async def _fake_stream_dashscope_chat(**kwargs):
        captured_stream_kwargs.update(kwargs)
        if False:
            yield None

    with patch("app.llm.provider_factory.stream_dashscope_chat", _fake_stream_dashscope_chat):
        emitted = [part async for part in model.stream_function([], agent_info)]

    assert emitted == []
    stream_kwargs = captured_stream_kwargs
    assert stream_kwargs["model_settings"] == {"max_tokens": 256, "extra_body": {"enable_thinking": True}}
    assert stream_kwargs["function_tools"] == [tool]
    assert stream_kwargs["allow_text_output"] is True
