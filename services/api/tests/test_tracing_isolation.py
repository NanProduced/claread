"""Safety tests for the neutral per-call tracing primitives."""

from __future__ import annotations

import asyncio
import os
from contextlib import contextmanager
from typing import Any

import pytest

from app.observability import tracing_context as tc


def _env_snapshot() -> dict[str, str | None]:
    keys = (
        "LANGSMITH_TRACING",
        "LANGSMITH_TRACING_V2",
        "LANGSMITH_PROJECT",
        "LANGSMITH_API_KEY",
    )
    return {key: os.environ.get(key) for key in keys}


def test_disabled_tracing_does_not_mutate_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_PROJECT", "claread-dev-sentinel")
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_TRACING_V2", "true")
    before = _env_snapshot()
    with tc.disabled_tracing():
        during = _env_snapshot()
    after = _env_snapshot()
    assert before == during == after


def test_disabled_tracing_delegates_to_langsmith_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    @contextmanager
    def fake_tracing_context(**kwargs: Any):
        captured.update(kwargs)
        yield

    import langsmith.run_helpers as run_helpers

    monkeypatch.setattr(run_helpers, "tracing_context", fake_tracing_context)
    with tc.disabled_tracing():
        pass
    assert captured == {"enabled": False}


def test_get_trace_surface_uses_product_default_when_unset() -> None:
    assert tc.get_trace_surface("daily_reader_pipeline") == "daily_reader_pipeline"


def test_set_trace_surface_round_trip() -> None:
    with tc.set_trace_surface("reader_orchestration"):
        assert tc.get_trace_surface("daily_reader_pipeline") == "reader_orchestration"
    assert tc.get_trace_surface("daily_reader_pipeline") == "daily_reader_pipeline"


def test_set_trace_surface_propagates_into_async_task() -> None:
    seen: list[str] = []

    async def _probe() -> None:
        seen.append(tc.get_trace_surface("daily_reader_pipeline"))

    async def _outer() -> None:
        with tc.set_trace_surface("reader_orchestration"):
            await asyncio.create_task(_probe())

    asyncio.run(_outer())
    assert seen == ["reader_orchestration"]


def test_concurrent_async_tasks_do_not_cross_contaminate_surface() -> None:
    seen: dict[str, str] = {}

    async def _task(label: str, surface: str) -> None:
        with tc.set_trace_surface(surface):
            await asyncio.sleep(0)
            seen[label] = tc.get_trace_surface("daily_reader_pipeline")

    async def _outer() -> None:
        await asyncio.gather(
            _task("reader", "reader_orchestration"),
            _task("daily", "daily_reader_pipeline"),
        )

    asyncio.run(_outer())
    assert seen == {
        "reader": "reader_orchestration",
        "daily": "daily_reader_pipeline",
    }


def test_workflow_root_tags_keep_neutral_surface_metadata() -> None:
    from app.observability.workflow_tracing import build_workflow_root_tags

    tags = build_workflow_root_tags(
        "daily_reader",
        ["qwen"],
        surface="daily_reader_pipeline",
    )
    assert tags == ["workflow", "daily_reader", "surface:daily_reader_pipeline", "qwen"]


def test_workflow_root_metadata_includes_reader_surface() -> None:
    from app.observability.workflow_tracing import build_workflow_root_metadata

    metadata = build_workflow_root_metadata(
        workflow_name="reader_orchestration",
        workflow_version="1.0.0",
        schema_version="1.0.0",
        request_id="req-1",
        source_type="worker",
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        profile_id="reader_layer",
        surface="reader_orchestration",
    )
    assert metadata["surface"] == "reader_orchestration"


def test_llm_trace_metadata_includes_daily_surface() -> None:
    from app.observability.workflow_tracing import build_llm_trace_metadata

    metadata = build_llm_trace_metadata(
        workflow_name="daily_reader",
        workflow_version="1.0.0",
        request_id="req-1",
        source_type="pipeline",
        reading_goal="daily_reading",
        reading_variant="standard",
        profile_id="daily_reader",
        model_name="qwen",
        model_provider="openai_compatible",
        surface="daily_reader_pipeline",
    )
    assert metadata["surface"] == "daily_reader_pipeline"
