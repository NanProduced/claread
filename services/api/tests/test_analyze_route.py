from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.api.routes.analyze import analyze
from app.schemas.analysis import AnalyzeRequest


class DummyRenderScene:
    def __init__(self, request_id: str) -> None:
        self.request = {"request_id": request_id}
        self.schema_version = "3.0.0"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_analyze_records_anonymous_trial_usage_event():
    payload = AnalyzeRequest(
        text="Hello world",
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
    )

    with (
        patch(
            "app.api.routes.analyze.resolve_model_metadata",
            return_value={
                "model_route": "annotation_generation",
                "model_profile": "default_profile",
                "model_provider": "openai_compatible",
                "model_name": "test-model",
            },
        ),
        patch(
            "app.api.routes.analyze.run_article_analysis_with_state",
            AsyncMock(
                return_value={
                    "render_scene": DummyRenderScene("req-anon"),
                    "usage_summary": {
                        "aggregate": {
                            "input_tokens": 100,
                            "output_tokens": 50,
                            "total_tokens": 150,
                        }
                    },
                }
            ),
        ),
        patch(
            "app.api.routes.analyze.record_ai_usage_event",
            AsyncMock(return_value=True),
        ) as usage_mock,
        patch("app.api.routes.analyze.get_prompt_version", return_value="test-prompts"),
    ):
        result = await analyze(payload)

    assert isinstance(result, DummyRenderScene)
    event = usage_mock.await_args.args[0]
    assert event.usage_scope == "anonymous_trial"
    assert event.billing_mode == "trial"
    assert event.status == "succeeded"
    assert event.request_id == "req-anon"


@pytest.mark.anyio
async def test_analyze_records_eval_debug_usage_event():
    payload = AnalyzeRequest(
        text="Hello world",
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        model_selection={"preset": "quality_eval"},
    )

    with (
        patch(
            "app.api.routes.analyze.resolve_model_metadata",
            return_value={
                "model_route": "annotation_generation",
                "model_profile": "debug_profile",
                "model_provider": "openai_compatible",
                "model_name": "debug-model",
            },
        ),
        patch(
            "app.api.routes.analyze.run_article_analysis_with_state",
            AsyncMock(
                return_value={
                    "render_scene": DummyRenderScene("req-debug"),
                    "usage_summary": {
                        "aggregate": {
                            "input_tokens": 200,
                            "output_tokens": 80,
                            "total_tokens": 280,
                        }
                    },
                }
            ),
        ),
        patch(
            "app.api.routes.analyze.record_ai_usage_event",
            AsyncMock(return_value=True),
        ) as usage_mock,
        patch("app.api.routes.analyze.get_prompt_version", return_value="test-prompts"),
    ):
        await analyze(payload)

    event = usage_mock.await_args.args[0]
    assert event.usage_scope == "eval_debug"
    assert event.billing_mode == "no_charge"
    assert event.metadata_json["runtime_model_selection"] is True
