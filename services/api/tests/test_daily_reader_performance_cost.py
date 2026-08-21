"""A-5P performance, cost, and usage-observability regressions."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.llm.routes import (
    MODEL_ROUTE_DAILY_TAKEAWAYS,
    MODEL_ROUTE_DAILY_TRANSLATION,
)
from app.schemas.internal.daily_drafts import (
    CloseReadingTakeaways,
    DailyParagraphDraft,
    DailyReviewDraft,
    DailyVocabDraft,
    DailyVocabHighlight,
    ParagraphNotesDraft,
    ParagraphReadingNote,
)
from app.services.daily_reader.discovery import DiscoveredArticle
from app.services.daily_reader.pipeline import _run_workflow_and_store, run_daily_pipeline
from app.services.daily_reader.scoring import ArticleScore
from app.services.daily_reader.workflow import (
    build_daily_reader_graph,
    close_reading_takeaways_node,
    highlight_by_paragraph_batches_node,
    paragraph_guides_and_translations_node,
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
