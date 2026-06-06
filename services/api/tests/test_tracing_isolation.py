"""Safety tests for the per-call LangSmith tracing primitives.

Focus: make sure the tracing-isolation pass actually achieves what it
claims — no global env mutation, contextvars propagate across asyncio
tasks, and ``disabled_tracing`` reaches the LangSmith SDK switch.
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from typing import Any

import pytest

from app.eval_adapter import shared as eval_shared
from app.eval_adapter.schemas import (
    ArticleAnalysisEvalRequest,
    ArticleAnalysisNodeLabCompareRequest,
    ArticleAnalysisNodeLabRunRequest,
    ArticleAnalysisNodeProbeRequest,
)
from app.observability import tracing_context as tc


# ---------------------------------------------------------------------------
# Schema defaults + isolated rejection
# ---------------------------------------------------------------------------


def test_article_analysis_eval_request_defaults_trace_scope_off() -> None:
    request = ArticleAnalysisEvalRequest(text="hello world")
    assert request.trace_scope == "off"


def test_node_lab_run_request_defaults_trace_scope_off() -> None:
    request = ArticleAnalysisNodeLabRunRequest(text="hello world")
    assert request.trace_scope == "off"


def test_node_lab_compare_request_defaults_trace_scope_off() -> None:
    request = ArticleAnalysisNodeLabCompareRequest(
        text="hello world",
        candidate_override={
            "node_name": "grammar",
            "candidate_id": "cand-1",
            "snapshot_hash": "h",
            "instruction_override": {"mode": "baseline"},
            "policy_override": {"mode": "baseline"},
            "few_shot_override": {"few_shot_mode": "baseline"},
        },
    )
    assert request.trace_scope == "off"


def test_node_probe_request_defaults_trace_scope_off() -> None:
    request = ArticleAnalysisNodeProbeRequest(text="hello world")
    assert request.trace_scope == "off"


def test_isolated_trace_scope_rejected_at_schema() -> None:
    with pytest.raises(ValueError):
        ArticleAnalysisEvalRequest(text="x", trace_scope="isolated")


# ---------------------------------------------------------------------------
# trace_scope context manager: no os.environ mutation
# ---------------------------------------------------------------------------


def _env_snapshot() -> dict[str, str | None]:
    keys = (
        "LANGSMITH_TRACING",
        "LANGSMITH_TRACING_V2",
        "LANGSMITH_PROJECT",
        "LANGSMITH_API_KEY",
    )
    return {key: os.environ.get(key) for key in keys}


def test_trace_scope_off_does_not_mutate_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_PROJECT", "claread-dev-sentinel")
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_TRACING_V2", "true")
    before = _env_snapshot()
    request = SimpleNamespace(trace_scope="off", trace_project="should-be-ignored")
    with eval_shared.trace_scope(request):
        during = _env_snapshot()
    after = _env_snapshot()
    assert before == during == after


def test_trace_scope_inherit_does_not_mutate_environ(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGSMITH_PROJECT", "claread-dev-sentinel")
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    before = _env_snapshot()
    request = SimpleNamespace(trace_scope="inherit", trace_project="should-be-ignored")
    with eval_shared.trace_scope(request):
        during = _env_snapshot()
    after = _env_snapshot()
    assert before == during == after


def test_trace_scope_off_invokes_disabled_tracing(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bool] = []

    from contextlib import contextmanager as _cm

    @_cm
    def fake_disabled() -> Any:
        calls.append(True)
        yield

    monkeypatch.setattr(eval_shared, "disabled_tracing", fake_disabled)
    request = SimpleNamespace(trace_scope="off", trace_project=None)
    with eval_shared.trace_scope(request):
        pass
    assert calls == [True]


def test_trace_scope_inherit_does_not_invoke_disabled_tracing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[bool] = []

    from contextlib import contextmanager as _cm

    @_cm
    def fake_disabled() -> Any:
        calls.append(True)
        yield

    monkeypatch.setattr(eval_shared, "disabled_tracing", fake_disabled)
    request = SimpleNamespace(trace_scope="inherit", trace_project=None)
    with eval_shared.trace_scope(request):
        pass
    assert calls == []


# ---------------------------------------------------------------------------
# set_trace_surface: contextvar semantics under asyncio
# ---------------------------------------------------------------------------


def test_get_trace_surface_default_when_unset() -> None:
    assert tc.get_trace_surface("analyze_direct") == "analyze_direct"


def test_set_trace_surface_round_trip() -> None:
    with tc.set_trace_surface("eval_workflow_lab"):
        assert tc.get_trace_surface("analyze_direct") == "eval_workflow_lab"
    assert tc.get_trace_surface("analyze_direct") == "analyze_direct"


def test_set_trace_surface_propagates_into_async_task() -> None:
    seen: list[str] = []

    async def _probe() -> None:
        seen.append(tc.get_trace_surface("analyze_direct"))

    async def _outer() -> None:
        with tc.set_trace_surface("eval_workflow_lab"):
            await asyncio.create_task(_probe())

    asyncio.run(_outer())
    assert seen == ["eval_workflow_lab"]


def test_concurrent_async_tasks_do_not_cross_contaminate_surface() -> None:
    """Two concurrent tasks with different surfaces must NOT collide.

    This is the exact failure mode the old ``os.environ`` based
    ``trace_scope`` exhibited and the reason the rewrite uses
    ``ContextVar`` instead.
    """
    seen: dict[str, str] = {}

    async def _task(label: str, surface: str) -> None:
        with tc.set_trace_surface(surface):
            # Yield so the other task gets to run between set+get.
            await asyncio.sleep(0)
            seen[label] = tc.get_trace_surface("analyze_direct")

    async def _outer() -> None:
        await asyncio.gather(
            _task("a", "eval_workflow_lab"),
            _task("b", "daily_reader_pipeline"),
        )

    asyncio.run(_outer())
    assert seen == {"a": "eval_workflow_lab", "b": "daily_reader_pipeline"}


# ---------------------------------------------------------------------------
# disabled_tracing wraps the langsmith SDK switch
# ---------------------------------------------------------------------------


def test_disabled_tracing_delegates_to_langsmith_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    from contextlib import contextmanager as _cm

    @_cm
    def fake_tracing_context(**kwargs: Any) -> Any:
        captured.update(kwargs)
        yield

    import langsmith.run_helpers as _rh

    monkeypatch.setattr(_rh, "tracing_context", fake_tracing_context)
    with tc.disabled_tracing():
        pass
    assert captured == {"enabled": False}


# ---------------------------------------------------------------------------
# workflow tracing builder: surface lands in tags + metadata
# ---------------------------------------------------------------------------


def test_build_workflow_root_tags_includes_surface() -> None:
    from app.workflow.tracing import build_workflow_root_tags

    tags = build_workflow_root_tags(
        "article_analysis", ["MiniMax-M2.7"], surface="eval_workflow_lab"
    )
    assert "workflow" in tags
    assert "article_analysis" in tags
    assert "surface:eval_workflow_lab" in tags
    assert "MiniMax-M2.7" in tags


def test_build_workflow_root_tags_omits_surface_when_none() -> None:
    from app.workflow.tracing import build_workflow_root_tags

    tags = build_workflow_root_tags("article_analysis", ["m"], surface=None)
    assert not any(tag.startswith("surface:") for tag in tags)


def test_build_workflow_root_metadata_includes_surface() -> None:
    from app.workflow.tracing import build_workflow_root_metadata

    metadata = build_workflow_root_metadata(
        workflow_name="article_analysis",
        workflow_version="3.0.0",
        schema_version="3.0.0",
        request_id="req-1",
        source_type="user_input",
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        profile_id="learning_general",
        surface="eval_workflow_lab",
    )
    assert metadata["surface"] == "eval_workflow_lab"


def test_build_llm_trace_metadata_includes_surface() -> None:
    from app.workflow.tracing import build_llm_trace_metadata

    metadata = build_llm_trace_metadata(
        workflow_name="article_analysis",
        workflow_version="3.0.0",
        request_id="req-1",
        source_type="user_input",
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        profile_id="learning_general",
        model_name="MiniMax",
        model_provider="openai_compatible",
        surface="overview_worker",
    )
    assert metadata["surface"] == "overview_worker"
