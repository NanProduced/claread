"""A-5P performance, cost, and usage-observability regressions (teaching v2)."""

from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from daily_reader_teaching_v2_fixtures import (
    _blueprint_span,
    _language_support_span,
    graph_input_state,
    make_blueprint,
    make_usage,
    v2_happy_path,
)
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage

from app.llm.types import ResolvedModelConfig
from app.services.ai_usage import STATUS_SKIPPED, STATUS_SUCCEEDED
from app.services.daily_reader import workflow as workflow_module
from app.services.daily_reader.discovery import DiscoveredArticle
from app.services.daily_reader.pipeline import (
    _run_workflow_and_store,
    run_daily_pipeline,
    run_workflow_only,
)
from app.services.daily_reader.scoring import ArticleScore
from app.services.daily_reader.workflow import (
    build_daily_reader_graph,
    refinement_node,
    translation_node,
)

WORKFLOW_MODULE = "app.services.daily_reader.workflow"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_daily_model_preset_declares_every_daily_cost_tier() -> None:
    preset_path = Path(__file__).parent.parent / "config" / "model-presets.example.json"
    config = json.loads(preset_path.read_text(encoding="utf-8"))

    assert config["daily_reader"]["routes"] == {
        "daily_annotation": {"profile": "workflow-deepseek-v4-flash"},
        "daily_translation": {"profile": "workflow-deepseek-v4-flash"},
        "daily_analysis": {"profile": "workflow-deepseek-v4-flash"},
        "daily_takeaways": {"profile": "workflow-deepseek-v4-pro"},
        "daily_review": {"profile": "workflow-deepseek-v4-pro"},
    }


@pytest.mark.anyio
async def test_graph_preserves_per_stage_usage_snapshot() -> None:
    with v2_happy_path():
        final_state = await build_daily_reader_graph().ainvoke(graph_input_state())

    assert final_state["usage_summary"] == {
        "available": True,
        "per_agent": {
            "blueprint": make_usage("blueprint"),
            "language_support": make_usage("language_support"),
            "translation": make_usage("translation"),
            "semantic_review": make_usage("semantic_review"),
        },
        "aggregate": {
            "input_tokens": 40,
            "output_tokens": 8,
            "total_tokens": 48,
            "model_requests": 4,
            "tool_calls": 0,
        },
    }


def test_teaching_stages_use_their_declared_routes() -> None:
    """P-4E route mapping frozen into the production spans:
    blueprint→daily_analysis, language_support→daily_annotation,
    translation→daily_translation, semantic_review/refinement→daily_review."""
    source_by_span = {
        "blueprint": inspect.getsource(workflow_module._run_blueprint_llm_span),
        "language_support": inspect.getsource(workflow_module._run_language_support_llm_span),
        "translation": inspect.getsource(workflow_module._run_translation_llm_span),
        "semantic_review": inspect.getsource(workflow_module._run_semantic_review_llm_span),
        "refinement": inspect.getsource(workflow_module._run_teaching_refinement_llm_span),
    }
    assert "route=MODEL_ROUTE_DAILY_ANALYSIS" in source_by_span["blueprint"]
    assert "route=MODEL_ROUTE_DAILY_ANNOTATION" in source_by_span["language_support"]
    assert "route=MODEL_ROUTE_DAILY_TRANSLATION" in source_by_span["translation"]
    assert "route=MODEL_ROUTE_DAILY_REVIEW" in source_by_span["semantic_review"]
    assert "route=MODEL_ROUTE_DAILY_REVIEW" in source_by_span["refinement"]
    # the takeaways route (v1) must not appear anywhere in the v2 workflow
    assert "MODEL_ROUTE_DAILY_TAKEAWAYS" not in inspect.getsource(workflow_module)


@pytest.mark.anyio
async def test_pipeline_runs_two_articles_concurrently_but_returns_score_order() -> None:
    candidates = [
        DiscoveredArticle(
            url=f"u{index}",
            title=f"Candidate {index}",
            source=f"source-{index}",
            description="A substantive candidate article.",
            text=(f"Unique body {index} with substantive analysis and context. " * 30),
            tags=[f"section-{index}"],
            word_count=800,
            needs_extraction=False,
        )
        for index in range(4)
    ]
    scores = {
        article.url: ArticleScore(
            score=9.0 - index / 10,
            difficulty="B2",
            tags=[f"topic-{index}"],
        )
        for index, article in enumerate(candidates)
    }
    active = 0
    max_active = 0

    async def fake_score(article):
        return scores[article.url]

    async def fake_workflow(article, score, **_kwargs):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        try:
            await asyncio.sleep(0.02 if article.url == "u0" else 0)
            return {"url": article.url, "score": score.score}
        finally:
            active -= 1

    with (
        patch(
            "app.services.daily_reader.pipeline.discover_guardian",
            new=AsyncMock(return_value=candidates),
        ),
        patch(
            "app.services.daily_reader.pipeline.discover_rss_sources",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.daily_reader.pipeline.probe_cover_eligible",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "app.services.daily_reader.pipeline._get_existing_text_hashes",
            new=AsyncMock(return_value=set()),
        ),
        patch("app.services.daily_reader.pipeline.score_article", new=fake_score),
        patch("app.services.daily_reader.pipeline._run_workflow_and_store", new=fake_workflow),
        patch(
            "app.services.daily_reader.pipeline.emit_pipeline_alerts",
            new=AsyncMock(),
        ),
    ):
        result = await run_daily_pipeline(max_count=3)

    assert max_active == 2
    assert [article["url"] for article in result.articles] == ["u0", "u1", "u2"]


@pytest.mark.anyio
async def test_aborted_workflow_records_usage_snapshot_and_diagnostics() -> None:
    article = DiscoveredArticle(
        url="https://example.test/aborted",
        title="Aborted candidate",
        source="source",
        description="Description",
        text="Substantive text " * 50,
        tags=["section"],
        word_count=800,
        needs_extraction=False,
    )
    graph = SimpleNamespace(
        ainvoke=AsyncMock(
            return_value={
                "abort": True,
                "abort_reason": "teaching_v2_hard_gates_failed",
                "abort_diagnostics": {"failed_gates": ["anchors_resolve"]},
                "blueprint_usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
                "semantic_review_usage": {
                    "input_tokens": 20,
                    "output_tokens": 4,
                    "total_tokens": 24,
                },
            }
        )
    )
    record_event = AsyncMock()

    with (
        patch(
            "app.services.daily_reader.workflow.build_daily_reader_graph",
            return_value=graph,
        ),
        patch(
            "app.services.daily_reader.pipeline._record_daily_pipeline_event",
            new=record_event,
        ),
        patch(
            "app.services.daily_reader.pipeline._store_daily_reader",
            new=AsyncMock(),
        ),
        patch(
            "app.services.daily_reader.pipeline._next_sequence_number",
            new=AsyncMock(return_value=1),
        ),
        patch(
            "app.services.daily_reader.pipeline.process_article_covers",
            new=AsyncMock(return_value=SimpleNamespace(cover_url=None, image_blocks=[], meta={})),
        ),
    ):
        result = await _run_workflow_and_store(
            article,
            ArticleScore(score=8.0, difficulty="B2", tags=["topic"]),
        )

    assert result is not None
    assert result["status"] == "draft"
    assert record_event.await_args.kwargs["usage_data"] == {
        "available": True,
        "per_agent": {
            "blueprint": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
            "semantic_review": {"input_tokens": 20, "output_tokens": 4, "total_tokens": 24},
        },
        "aggregate": {
            "input_tokens": 30,
            "output_tokens": 6,
            "total_tokens": 36,
            "model_requests": 0,
            "tool_calls": 0,
        },
    }
    # defense line 4: stop diagnostics land in the usage event
    metadata = record_event.await_args.kwargs["metadata_json"]
    assert metadata["abort_diagnostics"]["failed_gates"] == ["anchors_resolve"]
    assert metadata["abort_reason"] == "teaching_v2_hard_gates_failed"


# ---------------------------------------------------------------------------
# P-3C: structured-output failure must keep confirmed provider usage
# ---------------------------------------------------------------------------

_FAILED_REQUEST_INPUT_TOKENS = 11
_FAILED_REQUEST_OUTPUT_TOKENS = 7
_FAILED_USAGE_TOTAL = {
    "input_tokens": 44,
    "output_tokens": 28,
    "total_tokens": 72,
    "model_requests": 4,
    "tool_calls": 0,
}


def _fake_resolved_config(route: str) -> ResolvedModelConfig:
    return ResolvedModelConfig(
        route=route,  # type: ignore[arg-type]
        profile_name="test-function",
        provider="dashscope",
        adapter="openai_compatible",
        model_name="function-model",
        api_key="",
    )


def _invalid_structured_model() -> FunctionModel:
    calls = {"n": 0}

    async def model_fn(messages, info: AgentInfo):
        del messages
        calls["n"] += 1
        usage = RequestUsage(
            input_tokens=_FAILED_REQUEST_INPUT_TOKENS,
            output_tokens=_FAILED_REQUEST_OUTPUT_TOKENS,
        )
        if info.output_tools:
            tool_name = info.output_tools[0].name
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name=tool_name,
                        args="definitely-not-json",
                        tool_call_id=f"bad-{calls['n']}",
                    )
                ],
                usage=usage,
                finish_reason="stop",
                model_name="function-model",
            )
        return ModelResponse(
            parts=[TextPart(content='{"translations":')],
            usage=usage,
            finish_reason="stop",
            model_name="function-model",
        )

    return FunctionModel(model_fn)


def _timeout_before_response_model() -> FunctionModel:
    async def model_fn(messages, info: AgentInfo):
        del messages, info
        raise TimeoutError("no provider response")

    return FunctionModel(model_fn)


def _translation_stage_state() -> dict:
    from daily_reader_teaching_v2_fixtures import (
        READING_UNITS,
        make_blueprint,
        make_language_support,
    )

    blueprint = make_blueprint().model_dump()
    language_support = make_language_support().model_dump()
    return {
        "original_text": "\n\n".join(unit["text"] for unit in READING_UNITS),
        "reading_units": READING_UNITS,
        "lesson_blueprint": blueprint,
        "language_support": language_support,
        "source_url": "https://example.test/x",
    }


@pytest.mark.anyio
async def test_translation_structured_output_failure_aborts_with_usage() -> None:
    with patch(
        "app.llm.agent_runner.build_model_for_route",
        return_value=(_invalid_structured_model(), _fake_resolved_config("daily_translation")),
    ):
        out = await translation_node(_translation_stage_state())

    # v2 fail-closed: a stage that cannot produce a valid DTO aborts the run
    assert out["abort"] is True
    assert out["abort_reason"] == "translation_stage_failed"
    # full retry-cap request count survives for stage attribution
    assert out["translation_usage"] == _FAILED_USAGE_TOTAL


@pytest.mark.anyio
async def test_pre_provider_failure_does_not_fabricate_usage() -> None:
    with patch(
        "app.llm.agent_runner.build_model_for_route",
        return_value=(
            _timeout_before_response_model(),
            _fake_resolved_config("daily_translation"),
        ),
    ):
        out = await translation_node(_translation_stage_state())

    assert out["abort"] is True
    assert "translation_usage" not in out


@pytest.mark.anyio
async def test_failed_translation_usage_aggregates_with_succeeded_stages() -> None:
    # blueprint + language_support succeed; translation burns the retry cap
    # and fails closed — the run aborts with all three usages conserved.
    with (
        patch(f"{WORKFLOW_MODULE}._run_blueprint_llm_span", new=_blueprint_span),
        patch(f"{WORKFLOW_MODULE}._run_language_support_llm_span", new=_language_support_span),
        patch(
            "app.llm.agent_runner.build_model_for_route",
            return_value=(
                _invalid_structured_model(),
                _fake_resolved_config("daily_translation"),
            ),
        ),
    ):
        final_state = await build_daily_reader_graph().ainvoke(graph_input_state())

    assert final_state.get("abort") is True
    assert final_state["abort_reason"] == "translation_stage_failed"
    # aborted runs skip daily_projection_node: aggregate via the same
    # fallback the pipeline uses (P-3F conservation rule).
    usage_summary = workflow_module._aggregate_usage(final_state)
    per_agent = usage_summary["per_agent"]
    assert per_agent["translation"] == _FAILED_USAGE_TOTAL
    assert per_agent["blueprint"] == make_usage("blueprint")
    assert per_agent["language_support"] == make_usage("language_support")
    aggregate = usage_summary["aggregate"]
    assert aggregate["model_requests"] == 4 + 1 + 1
    assert aggregate["input_tokens"] == 44 + 10 + 10


# ---------------------------------------------------------------------------
# P-3F: offline failure attribution + usage closed loop
# ---------------------------------------------------------------------------


def _retry_row() -> dict:
    return {
        "id": "daily_2026_08_22_001",
        "title": "旧中文标题",
        "original_title": "English Headline",
        "subtitle": "sub",
        "source": "BBC News",
        "source_url": "https://example.com/a",
        "cover_image_url": None,
        "tags": ["旧标签"],
        "difficulty": "B2",
        "read_time_minutes": 5,
        "pipeline_source": "bbc_rss",
        "pipeline_meta": {},
        "original_text": "Enough original text to retry.",
    }


def _retry_env(row: dict, final_state: dict) -> tuple[MagicMock, MagicMock, AsyncMock]:
    mock_conn = AsyncMock()
    mock_conn.fetchrow.return_value = row
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    graph = MagicMock()
    graph.ainvoke = AsyncMock(return_value=final_state)
    return mock_pool, graph, mock_conn


def _patched_retry_env(row: dict, final_state: dict):
    mock_pool, graph, mock_conn = _retry_env(row, final_state)
    record_event = AsyncMock()
    return mock_pool, graph, mock_conn, record_event


@pytest.mark.anyio
async def test_retry_abort_records_aggregated_usage_snapshot() -> None:
    final_state = {
        "abort": True,
        "abort_reason": "teaching_v2_after_review_fail",
        "abort_diagnostics": {"patch_rejected": True},
        "usage_summary": None,
        "translation_usage": {
            "input_tokens": 44,
            "output_tokens": 28,
            "total_tokens": 72,
            "model_requests": 4,
            "tool_calls": 0,
        },
        "refinement_usage": {
            "input_tokens": 20,
            "output_tokens": 10,
            "total_tokens": 30,
            "model_requests": 4,
            "tool_calls": 0,
        },
    }
    mock_pool, graph, _conn, record_event = _patched_retry_env(_retry_row(), final_state)

    with (
        patch("app.services.daily_reader.pipeline.db_connection.DB_POOL", mock_pool),
        patch(
            "app.services.daily_reader.workflow.build_daily_reader_graph",
            return_value=graph,
        ),
        patch(
            "app.services.daily_reader.pipeline._record_daily_pipeline_event",
            new=record_event,
        ),
    ):
        result = await run_workflow_only("daily_2026_08_22_001")

    assert result is None
    assert record_event.await_args.kwargs["status"] == STATUS_SKIPPED
    # abort path must emit the conserved aggregate, not a usage-less event
    assert record_event.await_args.kwargs["usage_data"] == {
        "available": True,
        "per_agent": {
            "translation": final_state["translation_usage"],
            "refinement": final_state["refinement_usage"],
        },
        "aggregate": {
            "input_tokens": 64,
            "output_tokens": 38,
            "total_tokens": 102,
            "model_requests": 8,
            "tool_calls": 0,
        },
    }
    assert record_event.await_args.kwargs["error_message"] == "teaching_v2_after_review_fail"


@pytest.mark.anyio
async def test_retry_success_records_aggregated_usage_when_projection_summary_missing() -> None:
    blueprint = make_blueprint().model_dump()
    final_state = {
        "abort": False,
        "usage_summary": None,
        "lesson_blueprint": blueprint,
        "lesson_v2": {
            "lesson_blueprint": blueprint,
            "learning_package": {},
            "source_assets": {"source_caption": ""},
            "run_meta": {"outcome": "cleaned_publish", "refinement_count": 0},
        },
        "body_json": {"paragraphs": [{"id": "u01", "text": "t"}]},
        "blueprint_usage": {
            "input_tokens": 10,
            "output_tokens": 2,
            "total_tokens": 12,
            "model_requests": 1,
            "tool_calls": 0,
        },
        "semantic_review_usage": {
            "input_tokens": 20,
            "output_tokens": 4,
            "total_tokens": 24,
            "model_requests": 1,
            "tool_calls": 0,
        },
    }
    mock_pool, graph, mock_conn, record_event = _patched_retry_env(_retry_row(), final_state)

    with (
        patch("app.services.daily_reader.pipeline.db_connection.DB_POOL", mock_pool),
        patch(
            "app.services.daily_reader.workflow.build_daily_reader_graph",
            return_value=graph,
        ),
        patch(
            "app.services.daily_reader.pipeline._record_daily_pipeline_event",
            new=record_event,
        ),
    ):
        result = await run_workflow_only("daily_2026_08_22_001")

    # success semantics unchanged
    assert result is not None
    assert result["status"] == "retry_completed"
    assert record_event.await_args.kwargs["status"] == STATUS_SUCCEEDED

    # fallback usage instead of a usage-less success event
    assert record_event.await_args.kwargs["usage_data"] == {
        "available": True,
        "per_agent": {
            "blueprint": final_state["blueprint_usage"],
            "semantic_review": final_state["semantic_review_usage"],
        },
        "aggregate": {
            "input_tokens": 30,
            "output_tokens": 6,
            "total_tokens": 36,
            "model_requests": 2,
            "tool_calls": 0,
        },
    }

    # DB business payload untouched by the usage fix: the v2 retry UPDATE
    # writes lesson_v2 + body_json + the promotion columns.
    _sql, *params = mock_conn.execute.call_args[0]
    assert params[0] == final_state["lesson_v2"]
    assert params[1] == final_state["body_json"]
    assert params[2] == "B1"  # effective_difficulty from the blueprint
    assert params[3] == blueprint["title_zh"]
    assert params[4] == "English Headline"
    assert params[5] == blueprint["subtitle_zh"]
    assert params[6] == blueprint["tags_zh"]


@pytest.mark.anyio
async def test_refinement_failure_attribution_aborts_with_evidence_and_usage() -> None:
    from daily_reader_teaching_v2_fixtures import make_review_fail
    from test_daily_reader_teaching_v2_workflow import _package_state

    state = _package_state()
    state["semantic_review_result"] = make_review_fail().model_dump()

    with patch(
        "app.llm.agent_runner.build_model_for_route",
        return_value=(_invalid_structured_model(), _fake_resolved_config("daily_review")),
    ):
        out = await refinement_node(state)

    assert out["abort"] is True
    assert out["abort_reason"] == "refinement_stage_failed"
    # full retry-cap request count survives for stage attribution
    assert out["refinement_usage"]["model_requests"] == 4
