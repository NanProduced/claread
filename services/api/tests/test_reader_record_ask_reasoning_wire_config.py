"""ASK-REASONING-R1 wire tests: thinking options survive the Ask config chain.

Proves, against the REAL production config files, that:

1. The Ask DeepSeek V4 Flash main/replan profiles enable thinking
   (previously ``disabled`` — conflicting with the product contract);
   DeepSeek Pro stays enabled and Qwen keeps its native thinking contract.
2. The execution resolver forwards provider thinking options transparently
   for all three product options (main route) — it never rewrites DeepSeek
   ``thinking.type`` / Qwen ``enable_thinking`` (the provider factory owns
   wire normalization; the resolver only merges the product output cap).
3. The same holds for the replan route profiles.

Wire-level request-body behavior (``thinking={"type":"enabled"}`` on the
request, tool_choice omission, reasoning_content send-back, DashScope
``enable_thinking``) is locked by the existing suites
``test_deepseek_direct_wire.py`` / ``test_dashscope_native_provider.py`` /
``test_thinking_capability.py``; these tests anchor the production config
chain to that wire contract.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic_ai.models.function import FunctionModel

from app.config.settings import Settings
from app.llm.deepseek_direct import DirectDeepSeekChatModel
from app.llm.router import build_model_for_route
from app.llm.routes import (
    MODEL_ROUTE_READER_ASK,
    MODEL_ROUTE_READER_ASK_REPLAN,
)
from app.services.reader_ask import model_options as model_options_svc
from app.services.reader_record_ask.execution_config import (
    resolve_reader_record_ask_execution,
)

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
_PROFILES_PATH = _CONFIG_DIR / "model-profiles.json"
_OPTIONS_PATH = _CONFIG_DIR / "reader-ask-model-options.json"


@pytest.fixture(autouse=True)
def _clear_caches():
    model_options_svc._build_catalog_cached.cache_clear()
    yield
    model_options_svc._build_catalog_cached.cache_clear()


@pytest.fixture()
def production_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Settings backed by the real production config catalogs.

    API keys are faked via env — the catalogs reference ``api_key_env``;
    no network call happens during model construction.
    """
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key-do-not-leak")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key-do-not-leak")
    return Settings(
        model_profiles_json=_PROFILES_PATH.read_text(encoding="utf-8"),
        reader_ask_model_options_json=_OPTIONS_PATH.read_text(encoding="utf-8"),
    )


def _model_settings_dict(model: object) -> dict:
    settings = getattr(model, "settings", None)
    assert isinstance(settings, dict), f"expected dict settings, got {type(settings)}"
    return settings


# ---------------------------------------------------------------------------
# 1. Production config literals
# ---------------------------------------------------------------------------


def test_production_profiles_declare_thinking_for_all_ask_options() -> None:
    profiles = json.loads(_PROFILES_PATH.read_text(encoding="utf-8"))["profiles"]
    # DeepSeek V4 Flash main + replan: thinking enabled (R1 correction).
    for name in ("ask-main-deepseek-v4-flash", "ask-replan-deepseek-v4-flash"):
        assert profiles[name]["model_settings"]["extra_body"]["thinking"] == {
            "type": "enabled"
        }, f"{name} must enable thinking"
    # DeepSeek Pro main + replan: thinking enabled (unchanged contract).
    for name in ("ask-main-deepseek-v4-pro", "ask-replan-deepseek-v4-pro"):
        assert profiles[name]["model_settings"]["extra_body"]["thinking"] == {
            "type": "enabled"
        }, f"{name} must keep thinking enabled"
    # Qwen native thinking contract (unchanged).
    for name in ("ask-main-qwen37-max", "ask-replan-qwen37-max"):
        assert profiles[name]["model_settings"]["extra_body"]["enable_thinking"] is True, (
            f"{name} must keep native enable_thinking"
        )


def test_production_options_route_all_three_products() -> None:
    options = json.loads(_OPTIONS_PATH.read_text(encoding="utf-8"))
    assert options["default_option"] == "deepseek-v4-flash"
    for key, main_profile, replan_profile in (
        ("deepseek-v4-flash", "ask-main-deepseek-v4-flash", "ask-replan-deepseek-v4-flash"),
        ("deepseek-pro", "ask-main-deepseek-v4-pro", "ask-replan-deepseek-v4-pro"),
        ("qwen-max", "ask-main-qwen37-max", "ask-replan-qwen37-max"),
    ):
        routes = options["options"][key]["selection"]["routes"]
        assert routes["reader_ask"]["profile"] == main_profile
        assert routes["reader_ask_replan"]["profile"] == replan_profile


# ---------------------------------------------------------------------------
# 2. Resolver transparency per option (main route)
# ---------------------------------------------------------------------------


def test_flash_option_builds_thinking_enabled_direct_model(
    production_settings: Settings,
) -> None:
    option = model_options_svc.resolve_reader_ask_model_option(
        production_settings, "deepseek-v4-flash", strict=True
    )
    execution = resolve_reader_record_ask_execution(
        option, settings=production_settings
    )
    model = execution.model
    assert isinstance(model, DirectDeepSeekChatModel)
    # Configured mode and effective wire mode are both enabled.
    assert model.deepseek_thinking_mode == "enabled"
    assert model.deepseek_effective_wire_mode == "enabled"
    # The built model settings carry the provider thinking payload,
    # untouched by the resolver; sampling params are stripped by the
    # dialect normalization (thinking enabled ⇒ no temperature).
    settings = _model_settings_dict(model)
    assert settings["extra_body"]["thinking"] == {"type": "enabled"}
    assert "temperature" not in settings
    # The resolver-level payload merges only the product output cap.
    payload = execution.model_settings()
    assert payload is not None
    assert payload.get("max_tokens") == option.runtime_budget.max_output_tokens
    assert payload["extra_body"]["thinking"] == {"type": "enabled"}


def test_pro_option_builds_thinking_enabled_direct_model(
    production_settings: Settings,
) -> None:
    option = model_options_svc.resolve_reader_ask_model_option(
        production_settings, "deepseek-pro", strict=True
    )
    execution = resolve_reader_record_ask_execution(
        option, settings=production_settings
    )
    model = execution.model
    assert isinstance(model, DirectDeepSeekChatModel)
    assert model.deepseek_effective_wire_mode == "enabled"
    settings = _model_settings_dict(model)
    assert settings["extra_body"]["thinking"] == {"type": "enabled"}


def test_qwen_option_builds_native_thinking_function_model(
    production_settings: Settings,
) -> None:
    option = model_options_svc.resolve_reader_ask_model_option(
        production_settings, "qwen-max", strict=True
    )
    execution = resolve_reader_record_ask_execution(
        option, settings=production_settings
    )
    model = execution.model
    # dashscope_native adapter builds a FunctionModel with the native
    # stream function; thinking rides in extra_body.enable_thinking.
    assert isinstance(model, FunctionModel)
    settings = _model_settings_dict(model)
    assert settings["extra_body"]["enable_thinking"] is True
    payload = execution.model_settings()
    assert payload is not None
    assert payload["extra_body"]["enable_thinking"] is True


# ---------------------------------------------------------------------------
# 3. Replan route profiles resolve identically
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("option_key", "route"),
    [
        ("deepseek-v4-flash", MODEL_ROUTE_READER_ASK_REPLAN),
        ("deepseek-pro", MODEL_ROUTE_READER_ASK_REPLAN),
        ("qwen-max", MODEL_ROUTE_READER_ASK_REPLAN),
    ],
)
def test_replan_route_keeps_thinking_enabled(
    production_settings: Settings,
    option_key: str,
    route: str,
) -> None:
    option = model_options_svc.resolve_reader_ask_model_option(
        production_settings, option_key, strict=True
    )
    model, model_config = build_model_for_route(
        production_settings, route, option.selection
    )
    assert model is not None and model_config is not None
    if option_key == "qwen-max":
        assert isinstance(model, FunctionModel)
        settings = _model_settings_dict(model)
        assert settings["extra_body"]["enable_thinking"] is True
    else:
        assert isinstance(model, DirectDeepSeekChatModel)
        assert model.deepseek_effective_wire_mode == "enabled"
        settings = _model_settings_dict(model)
        assert settings["extra_body"]["thinking"] == {"type": "enabled"}


def test_main_and_replan_flash_share_wire_thinking_contract(
    production_settings: Settings,
) -> None:
    """Send and replan are symmetric: both routes emit thinking on the wire."""
    option = model_options_svc.resolve_reader_ask_model_option(
        production_settings, "deepseek-v4-flash", strict=True
    )
    main_model, _ = build_model_for_route(
        production_settings, MODEL_ROUTE_READER_ASK, option.selection
    )
    replan_model, _ = build_model_for_route(
        production_settings, MODEL_ROUTE_READER_ASK_REPLAN, option.selection
    )
    assert isinstance(main_model, DirectDeepSeekChatModel)
    assert isinstance(replan_model, DirectDeepSeekChatModel)
    assert main_model.deepseek_effective_wire_mode == "enabled"
    assert replan_model.deepseek_effective_wire_mode == "enabled"
