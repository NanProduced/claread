"""A-5P performance, cost, and usage-observability regressions."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage

from app.llm.routes import (
    MODEL_ROUTE_DAILY_TAKEAWAYS,
    MODEL_ROUTE_DAILY_TRANSLATION,
)
from app.llm.types import ResolvedModelConfig
from app.schemas.internal.daily_drafts import (
    CloseReadingTakeaways,
    DailyParagraphDraft,
    DailyRefinementDraft,
    DailyReviewDraft,
    DailyVocabDraft,
    DailyVocabHighlight,
    ParagraphNotesDraft,
    ParagraphReadingNote,
)
from app.services.ai_usage import STATUS_SKIPPED, STATUS_SUCCEEDED
from app.services.daily_reader.discovery import DiscoveredArticle
from app.services.daily_reader.pipeline import (
    _run_workflow_and_store,
    run_daily_pipeline,
    run_workflow_only,
)
from app.services.daily_reader.scoring import ArticleScore
from app.services.daily_reader.workflow import (
    build_daily_reader_graph,
    close_reading_takeaways_node,
    highlight_by_paragraph_batches_node,
    paragraph_guides_and_translations_node,
    refinement_node,
)


@pytest.mark.anyio
async def test_graph_preserves_per_agent_usage_snapshot() -> None:
    article = (
        "Substantive analysis explains a complex policy choice with evidence and "
        "context for readers who want to understand its wider consequences."
    )

    async def fake_highlight(*, deps, **_kwargs):
        paragraph = deps.paragraphs[0]
        text = str(paragraph["text"])
        anchor = "Substantive"
        start = text.index(anchor)
        return {
            "output": DailyVocabDraft(paragraphs=[DailyParagraphDraft(
                paragraph_id=str(paragraph["paragraph_id"]),
                text=text,
                highlights=[DailyVocabHighlight(
                    anchor=anchor,
                    start=start,
                    end=start + len(anchor),
                    type="vocab_highlight",
                    gloss="实质性的",
                )],
            )]),
            "usage_metadata": {
                "input_tokens": 10,
                "output_tokens": 2,
                "total_tokens": 12,
                "model_requests": 2,
                "tool_calls": 1,
            },
        }

    async def fake_notes(**_kwargs):
        return {
            "output": ParagraphNotesDraft(
                article_summary="文章摘要",
                reading_focus=["关注论证方式"],
                notes=[ParagraphReadingNote(
                    paragraph_id="p_0",
                    focus_question="作者如何论证？",
                    micro_summary="作者结合证据解释政策选择。",
                    translation="这篇分析用证据解释了一项复杂的政策选择及其广泛影响。",
                )],
                refined_difficulty="B2",
            ),
            "usage_metadata": {
                "input_tokens": 20,
                "output_tokens": 4,
                "total_tokens": 24,
                "model_requests": 1,
                "tool_calls": 0,
            },
        }

    async def fake_takeaways(**_kwargs):
        return {
            "output": CloseReadingTakeaways(
                title_zh="复杂政策如何影响读者",
                subtitle_zh="一篇以证据解释后果的分析",
                tags_zh=["公共政策", "论证"],
                article_takeaway="证据帮助读者理解复杂政策的后果。",
                key_expressions=[],
                sentence_notes=[],
                writing_moves=[],
                discussion_questions=["What evidence is strongest?", "What remains unclear?"],
            ),
            "usage_metadata": {
                "input_tokens": 30,
                "output_tokens": 6,
                "total_tokens": 36,
                "model_requests": 1,
                "tool_calls": 0,
            },
        }

    async def fake_review(**_kwargs):
        return {
            "output": DailyReviewDraft(passed=True, overall_score=0.95, issues=[]),
            "usage_metadata": {
                "input_tokens": 40,
                "output_tokens": 8,
                "total_tokens": 48,
                "model_requests": 1,
                "tool_calls": 0,
            },
        }

    with (
        patch(
            "app.services.daily_reader.workflow._run_daily_highlight_llm_span",
            new=fake_highlight,
        ),
        patch(
            "app.services.daily_reader.workflow._run_daily_paragraph_notes_llm_span",
            new=fake_notes,
        ),
        patch(
            "app.services.daily_reader.workflow._run_daily_takeaways_llm_span",
            new=fake_takeaways,
        ),
        patch("app.services.daily_reader.workflow._run_daily_review_llm_span", new=fake_review),
    ):
        final_state = await build_daily_reader_graph().ainvoke({
            "original_text": article,
            "title": "Policy analysis",
            "difficulty": "B2",
        })

    assert final_state["usage_summary"] == {
        "available": True,
        "per_agent": {
            "vocab": {
                "input_tokens": 10,
                "output_tokens": 2,
                "total_tokens": 12,
                "model_requests": 2,
                "tool_calls": 1,
            },
            "paragraph_notes": {
                "input_tokens": 20,
                "output_tokens": 4,
                "total_tokens": 24,
                "model_requests": 1,
                "tool_calls": 0,
            },
            "takeaways": {
                "input_tokens": 30,
                "output_tokens": 6,
                "total_tokens": 36,
                "model_requests": 1,
                "tool_calls": 0,
            },
            "review": {
                "input_tokens": 40,
                "output_tokens": 8,
                "total_tokens": 48,
                "model_requests": 1,
                "tool_calls": 0,
            },
        },
        "aggregate": {
            "input_tokens": 100,
            "output_tokens": 20,
            "total_tokens": 120,
            "model_requests": 5,
            "tool_calls": 1,
        },
    }


@pytest.mark.anyio
async def test_highlight_batches_are_bounded_concurrent_and_deterministic() -> None:
    paragraphs = [
        {
            "paragraph_id": f"p_{index}",
            "text": f"Token{index} " + ("substantive article context " * 6),
        }
        for index in range(12)
    ]
    active = 0
    max_active = 0
    call_count = 0

    async def fake_highlight(*, deps, **_kwargs):
        nonlocal active, max_active, call_count
        call_count += 1
        active += 1
        max_active = max(max_active, active)
        try:
            # Finish the first batch last to prove merge order is input order,
            # not task completion order.
            await asyncio.sleep(0.02 if deps.batch_index == 0 else 0)
            drafts = []
            for paragraph in deps.paragraphs:
                text = str(paragraph["text"])
                anchor = text.split()[0]
                drafts.append(DailyParagraphDraft(
                    paragraph_id=str(paragraph["paragraph_id"]),
                    text=text,
                    highlights=[DailyVocabHighlight(
                        anchor=anchor,
                        start=0,
                        end=len(anchor),
                        type="vocab_highlight",
                        gloss="测试释义",
                    )],
                ))
            return {
                "output": DailyVocabDraft(paragraphs=drafts),
                "usage_metadata": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
            }
        finally:
            active -= 1

    with patch(
        "app.services.daily_reader.workflow._run_daily_highlight_llm_span",
        new=fake_highlight,
    ):
        result = await highlight_by_paragraph_batches_node({
            "normalized_paragraphs": paragraphs,
            "difficulty": "B2",
        })

    assert call_count == 2
    assert max_active == 2
    assert [item["paragraph_id"] for item in result["highlights_json"]] == [
        f"p_{index}" for index in range(12)
    ]
    assert result["vocab_usage"] == {
        "input_tokens": 20,
        "output_tokens": 4,
        "total_tokens": 24,
        "model_requests": 0,
        "tool_calls": 0,
    }


@pytest.mark.anyio
async def test_translation_and_takeaways_use_distinct_explicit_routes() -> None:
    routes = []

    async def fake_run_agent_with_route(**kwargs):
        routes.append((kwargs["route"], kwargs["model_selection"].preset))
        if kwargs["route"] == MODEL_ROUTE_DAILY_TRANSLATION:
            output = ParagraphNotesDraft(
                article_summary="摘要",
                reading_focus=["关注论证"],
                notes=[ParagraphReadingNote(
                    paragraph_id="p_0",
                    focus_question="作者如何论证？",
                    micro_summary="作者给出了证据。",
                    translation="作者用证据解释了观点。",
                )],
                refined_difficulty="B2",
            )
        else:
            output = CloseReadingTakeaways(
                title_zh="证据如何支撑观点",
                subtitle_zh="一篇关注论证方式的文章",
                tags_zh=["论证", "写作"],
                article_takeaway="证据使观点更可信。",
                key_expressions=[],
                sentence_notes=[],
                writing_moves=[],
                discussion_questions=["What works?", "What is missing?"],
            )
        return SimpleNamespace(output=output, usage=None)

    paragraph = {
        "paragraph_id": "p_0",
        "text": "Evidence supports the argument with concrete examples.",
    }
    with (
        patch(
            "app.services.daily_reader.workflow.run_agent_with_route",
            new=fake_run_agent_with_route,
        ),
        patch("app.services.daily_reader.workflow.get_daily_footer_agent", return_value=object()),
        patch(
            "app.services.daily_reader.workflow.get_daily_interpretation_agent",
            return_value=object(),
        ),
    ):
        notes = await paragraph_guides_and_translations_node({
            "normalized_paragraphs": [paragraph],
            "title": "Evidence",
            "difficulty": "B2",
            "highlights_json": [],
        })
        await close_reading_takeaways_node({
            "normalized_paragraphs": [paragraph],
            "title": "Evidence",
            "difficulty": "B2",
            "highlights_json": [],
            "paragraph_notes_json": notes["paragraph_notes_json"],
        })

    assert routes == [
        (MODEL_ROUTE_DAILY_TRANSLATION, "daily_reader"),
        (MODEL_ROUTE_DAILY_TAKEAWAYS, "daily_reader"),
    ]


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
async def test_aborted_workflow_records_usage_snapshot() -> None:
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
    graph = SimpleNamespace(ainvoke=AsyncMock(return_value={
        "abort": True,
        "review_result": {"reason": "quality_review_rejected"},
        "vocab_usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
        "review_usage": {"input_tokens": 20, "output_tokens": 4, "total_tokens": 24},
    }))
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
    ):
        result = await _run_workflow_and_store(
            article,
            ArticleScore(score=8.0, difficulty="B2", tags=["topic"]),
        )

    assert result is None
    assert record_event.await_args.kwargs["usage_data"] == {
        "available": True,
        "per_agent": {
            "vocab": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
            "review": {"input_tokens": 20, "output_tokens": 4, "total_tokens": 24},
        },
        "aggregate": {
            "input_tokens": 30,
            "output_tokens": 6,
            "total_tokens": 36,
            "model_requests": 0,
            "tool_calls": 0,
        },
    }


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
                parts=[ToolCallPart(
                    tool_name=tool_name,
                    args="definitely-not-json",
                    tool_call_id=f"bad-{calls['n']}",
                )],
                usage=usage,
                finish_reason="stop",
                model_name="function-model",
            )
        return ModelResponse(
            parts=[TextPart(content='{"article_summary":')],
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


def _translation_node_state() -> dict:
    return {
        "normalized_paragraphs": [{
            "paragraph_id": "p_0",
            "text": (
                "Substantive analysis explains a complex policy choice with "
                "evidence and context for readers who want to understand it."
            ),
        }],
        "title": "Policy analysis",
        "difficulty": "B1",
        "highlights_json": [],
    }


@pytest.mark.anyio
async def test_translation_structured_output_failure_records_retry_usage() -> None:
    with patch(
        "app.llm.agent_runner.build_model_for_route",
        return_value=(_invalid_structured_model(), _fake_resolved_config("daily_translation")),
    ):
        out = await paragraph_guides_and_translations_node(_translation_node_state())

    assert out["paragraph_notes_json"] == {}
    assert out["paragraph_notes_usage"] == _FAILED_USAGE_TOTAL


@pytest.mark.anyio
async def test_pre_provider_failure_does_not_fabricate_usage() -> None:
    with patch(
        "app.llm.agent_runner.build_model_for_route",
        return_value=(
            _timeout_before_response_model(),
            _fake_resolved_config("daily_translation"),
        ),
    ):
        out = await paragraph_guides_and_translations_node(_translation_node_state())

    assert out["paragraph_notes_json"] == {}
    assert "paragraph_notes_usage" not in out


@pytest.mark.anyio
async def test_takeaways_structured_output_failure_records_retry_usage() -> None:
    with patch(
        "app.llm.agent_runner.build_model_for_route",
        return_value=(_invalid_structured_model(), _fake_resolved_config("daily_takeaways")),
    ):
        out = await close_reading_takeaways_node(_translation_node_state())

    assert out["takeaways_json"] == {}
    assert out["takeaways_usage"] == _FAILED_USAGE_TOTAL


@pytest.mark.anyio
async def test_failed_translation_usage_aggregates_with_refinement() -> None:
    article = (
        "Substantive analysis explains a complex policy choice with evidence and "
        "context for readers who want to understand its wider consequences."
    )
    notes = ParagraphNotesDraft(
        article_summary="文章摘要",
        reading_focus=["关注论证方式"],
        notes=[ParagraphReadingNote(
            paragraph_id="p_0",
            focus_question="作者如何论证？",
            micro_summary="作者结合证据解释政策选择。",
            translation="这篇分析用证据解释了一项复杂的政策选择及其广泛影响。",
        )],
        refined_difficulty="B1",
    )
    takeaways = CloseReadingTakeaways(
        title_zh="复杂政策如何影响读者",
        subtitle_zh="一篇以证据解释后果的分析",
        tags_zh=["公共政策", "论证"],
        article_takeaway="证据帮助读者理解复杂政策的后果。",
        key_expressions=[],
        sentence_notes=[],
        writing_moves=[],
        discussion_questions=["What evidence is strongest?", "What remains unclear?"],
    )

    async def fake_highlight(*, deps, **_kwargs):
        paragraph = deps.paragraphs[0]
        text = str(paragraph["text"])
        anchor = "Substantive"
        start = text.index(anchor)
        return {
            "output": DailyVocabDraft(paragraphs=[DailyParagraphDraft(
                paragraph_id=str(paragraph["paragraph_id"]),
                text=text,
                highlights=[DailyVocabHighlight(
                    anchor=anchor,
                    start=start,
                    end=start + len(anchor),
                    type="vocab_highlight",
                    gloss="实质性的",
                )],
            )]),
            "usage_metadata": {
                "input_tokens": 10,
                "output_tokens": 2,
                "total_tokens": 12,
                "model_requests": 1,
                "tool_calls": 0,
            },
        }

    async def fake_takeaways(**_kwargs):
        return {
            "output": takeaways,
            "usage_metadata": {
                "input_tokens": 30,
                "output_tokens": 6,
                "total_tokens": 36,
                "model_requests": 1,
                "tool_calls": 0,
            },
        }

    async def fake_refinement(**_kwargs):
        return {
            "output": DailyRefinementDraft(
                abort=False,
                refined_paragraph_notes=notes,
                refined_takeaways=takeaways,
            ),
            "usage_metadata": {
                "input_tokens": 40,
                "output_tokens": 8,
                "total_tokens": 48,
                "model_requests": 1,
                "tool_calls": 0,
            },
        }

    with (
        patch(
            "app.services.daily_reader.workflow._run_daily_highlight_llm_span",
            new=fake_highlight,
        ),
        patch(
            "app.services.daily_reader.workflow._run_daily_takeaways_llm_span",
            new=fake_takeaways,
        ),
        patch(
            "app.services.daily_reader.workflow._run_daily_refinement_llm_span",
            new=fake_refinement,
        ),
        patch(
            "app.llm.agent_runner.build_model_for_route",
            return_value=(
                _invalid_structured_model(),
                _fake_resolved_config("daily_translation"),
            ),
        ),
    ):
        final_state = await build_daily_reader_graph().ainvoke({
            "original_text": article,
            "title": "Policy analysis",
            "difficulty": "B1",
        })

    per_agent = final_state["usage_summary"]["per_agent"]
    assert per_agent["paragraph_notes"] == _FAILED_USAGE_TOTAL
    assert per_agent["refinement"]["model_requests"] == 1
    assert final_state["usage_summary"]["aggregate"]["model_requests"] == 7
    assert final_state["usage_summary"]["aggregate"]["input_tokens"] == 124
    assert final_state["paragraph_notes_json"]["article_summary"] == "文章摘要"

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
        "usage_summary": None,
        "review_result": {"passed": False, "reason": "quality_review_rejected"},
        "paragraph_notes_usage": {
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
            "paragraph_notes": final_state["paragraph_notes_usage"],
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


@pytest.mark.anyio
async def test_retry_success_records_aggregated_usage_when_projection_summary_missing() -> None:
    final_state = {
        "abort": False,
        "usage_summary": None,
        "body_json": {"paragraphs": []},
        "highlights_json": [],
        "paragraph_notes_json": {},
        "takeaways_json": {
            "title_zh": "新中文标题",
            "subtitle_zh": "新副标题",
            "tags_zh": ["科技"],
            "article_takeaway": "一句话总结",
        },
        "vocab_usage": {
            "input_tokens": 10,
            "output_tokens": 2,
            "total_tokens": 12,
            "model_requests": 1,
            "tool_calls": 0,
        },
        "review_usage": {
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
            "vocab": final_state["vocab_usage"],
            "review": final_state["review_usage"],
        },
        "aggregate": {
            "input_tokens": 30,
            "output_tokens": 6,
            "total_tokens": 36,
            "model_requests": 2,
            "tool_calls": 0,
        },
    }

    # DB business payload untouched by the usage fix
    _sql, *params = mock_conn.execute.call_args[0]
    assert params[0] == {"paragraphs": []}
    assert params[1] == []
    assert params[2] == {}
    assert params[3] == final_state["takeaways_json"]


@pytest.mark.anyio
async def test_paragraph_notes_failure_attribution_keeps_requests_without_abort() -> None:
    with patch(
        "app.llm.agent_runner.build_model_for_route",
        return_value=(_invalid_structured_model(), _fake_resolved_config("daily_translation")),
    ):
        out = await paragraph_guides_and_translations_node(_translation_node_state())

    assert out["paragraph_notes_json"] == {}
    # full retry-cap request count survives for stage attribution
    assert out["paragraph_notes_usage"]["model_requests"] == 4
    # this node degrades to empty notes; it never aborts the run itself
    assert "abort" not in out


@pytest.mark.anyio
async def test_refinement_failure_attribution_aborts_with_evidence_and_usage() -> None:
    state = {
        "original_text": "Substantive analysis explains a complex policy choice.",
        "normalized_paragraphs": [{
            "paragraph_id": "p_0",
            "text": (
                "Substantive analysis explains a complex policy choice with "
                "evidence and context."
            ),
        }],
        "review_result": {
            "passed": False,
            "issues": [{
                "dimension": "paragraph_note_coverage",
                "severity": "major",
                "description": "missing notes",
                "suggestion": "add notes",
            }],
        },
        "highlights_json": [],
        "paragraph_notes_json": {},
        "takeaways_json": {},
    }
    with patch(
        "app.llm.agent_runner.build_model_for_route",
        return_value=(_invalid_structured_model(), _fake_resolved_config("daily_review")),
    ):
        out = await refinement_node(state)

    assert out["abort"] is True
    remaining_issues = out["refinement_result"]["remaining_issues"]
    assert any(issue["dimension"] == "refinement_failed" for issue in remaining_issues)
    # full retry-cap request count survives for stage attribution
    assert out["refinement_usage"]["model_requests"] == 4
