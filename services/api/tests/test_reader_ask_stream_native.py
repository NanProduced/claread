"""Integration tests: native path skips the DashScope SSE header, compat keeps it."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.settings import Settings
from app.llm.types import ResolvedModelConfig
from app.services.reader_ask.agent_runner import prepare_stream_model_settings
from app.llm.types import RunModelSettings


def _compat_config() -> ResolvedModelConfig:
    return ResolvedModelConfig(
        route="reader_ask",
        profile_name="ask-main-qwen37-max",
        provider="dashscope",
        adapter="openai_compatible",
        model_name="qwen3.7-max",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key="k",
    )


def _native_config() -> ResolvedModelConfig:
    return ResolvedModelConfig(
        route="reader_ask",
        profile_name="ask-main-qwen37-max-native",
        provider="dashscope_native",
        adapter="dashscope_native",
        model_name="qwen3.7-max",
        api_key="k",
    )


def test_compat_path_still_injects_sse_header() -> None:
    out = prepare_stream_model_settings(
        RunModelSettings(max_tokens=2048),
        model_config=_compat_config(),
    )
    assert out.extra_headers == {"X-DashScope-SSE": "enable"}
    assert out.extra_body == {"incremental_output": True}


def test_native_path_skips_sse_header_and_incremental_output() -> None:
    out = prepare_stream_model_settings(
        RunModelSettings(max_tokens=2048),
        model_config=_native_config(),
    )
    assert out.extra_headers is None
    assert out.extra_body is None


def test_compat_non_dashscope_skips_sse_header() -> None:
    config = ResolvedModelConfig(
        route="reader_ask",
        profile_name="ask-main-deepseek",
        provider="deepseek",
        adapter="openai_compatible",
        model_name="deepseek-v4-pro",
        base_url="https://api.deepseek.com/v1",
        api_key="k",
    )
    out = prepare_stream_model_settings(RunModelSettings(), model_config=config)
    assert out.extra_headers is None
    assert out.extra_body is None


def test_no_model_config_does_not_inject() -> None:
    out = prepare_stream_model_settings(RunModelSettings())
    assert out.extra_headers is None
    assert out.extra_body is None
