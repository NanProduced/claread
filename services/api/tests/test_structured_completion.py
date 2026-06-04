"""Tests for the unified structured-completion helper."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

# pytest-asyncio is in auto mode (see services/api/pyproject.toml), so
# coroutine test functions run without needing explicit decoration.

from app.config.settings import Settings
from app.llm.routes import MODEL_ROUTE_ANNOTATION_GENERATION
from app.llm.structured_completion import (
    StructuredCompletionError,
    _parse_json_object,
    run_structured_completion,
)
from app.llm.types import ModelSelection, RouteModelSelection


def _settings_with_profile() -> Settings:
    return Settings(
        default_model_profile="primary",
        model_profiles_json=json.dumps(
            {
                "primary": {
                    "provider": "openai_compatible",
                    "model_name": "primary-model",
                    "base_url": "https://example.invalid/v1",
                    "api_key": "primary-key",
                }
            }
        ),
    )


def _selection_with_profile(profile: str) -> ModelSelection:
    return ModelSelection(
        default_profile=profile,
        routes={MODEL_ROUTE_ANNOTATION_GENERATION: RouteModelSelection(profile=profile)},
    )


def _build_response(payload: dict[str, Any]) -> MagicMock:
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = payload
    response.text = json.dumps(payload)
    response.raise_for_status = MagicMock()
    return response


def test_parse_json_object_handles_plain_object() -> None:
    parsed = _parse_json_object('{"verdict": "candidate_preferred"}')
    assert parsed == {"verdict": "candidate_preferred"}


def test_parse_json_object_strips_code_fence() -> None:
    parsed = _parse_json_object('```json\n{"verdict": "tie"}\n```')
    assert parsed == {"verdict": "tie"}


def test_parse_json_object_extracts_embedded_object() -> None:
    parsed = _parse_json_object('prefix noise {"verdict": "baseline_preferred"} trailing')
    assert parsed == {"verdict": "baseline_preferred"}


def test_parse_json_object_rejects_non_object() -> None:
    with pytest.raises(ValueError):
        _parse_json_object("just a string")


async def test_run_structured_completion_returns_parsed_payload() -> None:
    settings = _settings_with_profile()
    response = _build_response(
        {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {"verdict": "candidate_preferred", "summary": "ok"}
                        )
                    }
                }
            ]
        }
    )

    captured: dict[str, Any] = {}

    class _FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            captured["timeout"] = kwargs.get("timeout")

        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def post(self, url: str, *, headers: dict[str, str], json: dict[str, Any]) -> MagicMock:
            captured["url"] = url
            captured["headers"] = headers
            captured["body"] = json
            return response

    with patch("app.llm.structured_completion.httpx.AsyncClient", _FakeAsyncClient):
        result = await run_structured_completion(
            settings=settings,
            route=MODEL_ROUTE_ANNOTATION_GENERATION,
            selection=_selection_with_profile("primary"),
            system_prompt="judge",
            user_prompt="packet",
            timeout_seconds=12.0,
        )

    assert result.parsed == {"verdict": "candidate_preferred", "summary": "ok"}
    assert result.model_name == "primary-model"
    assert result.profile_name == "primary"
    assert captured["url"] == "https://example.invalid/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer primary-key"
    body = captured["body"]
    assert body["model"] == "primary-model"
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][1]["role"] == "user"
    assert body["temperature"] == 0.0


async def test_run_structured_completion_handles_code_fence() -> None:
    settings = _settings_with_profile()
    response = _build_response(
        {
            "choices": [
                {
                    "message": {
                        "content": '```json\n{"grammar_tags": ["relative_clause"]}\n```'
                    }
                }
            ]
        }
    )

    class _FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def post(self, *args: Any, **kwargs: Any) -> MagicMock:
            return response

    with patch("app.llm.structured_completion.httpx.AsyncClient", _FakeAsyncClient):
        result = await run_structured_completion(
            settings=settings,
            route=MODEL_ROUTE_ANNOTATION_GENERATION,
            selection=_selection_with_profile("primary"),
            system_prompt="judge",
            user_prompt="packet",
        )

    assert result.parsed == {"grammar_tags": ["relative_clause"]}


async def test_run_structured_completion_rejects_unconfigured_profile() -> None:
    settings = _settings_with_profile()

    with pytest.raises(StructuredCompletionError, match="not configured"):
        await run_structured_completion(
            settings=settings,
            route=MODEL_ROUTE_ANNOTATION_GENERATION,
            selection=_selection_with_profile("does-not-exist"),
            system_prompt="judge",
            user_prompt="packet",
        )


async def test_run_structured_completion_raises_on_http_error() -> None:
    settings = _settings_with_profile()
    error_response = MagicMock()
    error_response.status_code = 502
    error_response.text = "upstream down"
    http_error = httpx.HTTPStatusError(
        "boom", request=MagicMock(), response=error_response
    )
    error_response.raise_for_status.side_effect = http_error

    class _FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def post(self, *args: Any, **kwargs: Any) -> MagicMock:
            return error_response

    with patch("app.llm.structured_completion.httpx.AsyncClient", _FakeAsyncClient):
        with pytest.raises(StructuredCompletionError, match="HTTP 502"):
            await run_structured_completion(
                settings=settings,
                route=MODEL_ROUTE_ANNOTATION_GENERATION,
                selection=_selection_with_profile("primary"),
                system_prompt="judge",
                user_prompt="packet",
            )


async def test_run_structured_completion_raises_on_empty_content() -> None:
    settings = _settings_with_profile()
    response = _build_response({"choices": [{"message": {"content": ""}}]})

    class _FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def post(self, *args: Any, **kwargs: Any) -> MagicMock:
            return response

    with patch("app.llm.structured_completion.httpx.AsyncClient", _FakeAsyncClient):
        with pytest.raises(StructuredCompletionError, match="empty"):
            await run_structured_completion(
                settings=settings,
                route=MODEL_ROUTE_ANNOTATION_GENERATION,
                selection=_selection_with_profile("primary"),
                system_prompt="judge",
                user_prompt="packet",
            )
