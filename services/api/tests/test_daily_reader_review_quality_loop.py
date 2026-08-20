"""A-5Q deterministic quality gate and bounded refinement regressions."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.daily_refinement_agent import (
    DailyRefinementAgentDeps,
    build_daily_refinement_prompt,
)
from app.agents.daily_review_agent import DailyReviewAgentDeps, build_daily_review_prompt
from app.services.daily_reader.workflow import quality_review_node, refinement_node


def _state() -> dict:
    paragraphs = [
        {"paragraph_id": "p_0", "text": "First substantive reading unit. " * 6},
        {"paragraph_id": "p_1", "text": "Second substantive reading unit. " * 6},
    ]
    return {
        "original_text": "\n\n".join(p["text"] for p in paragraphs),
        "normalized_paragraphs": paragraphs,
        "difficulty": "B2",
        "highlights_json": [
            {"paragraph_id": "p_0", "text": "substantive", "gloss": "实质性的"},
        ],
        "paragraph_notes_json": {
            "notes": [
                {
                    "paragraph_id": p["paragraph_id"],
                    "focus_question": "What matters here?",
                    "micro_summary": "摘要",
                    "translation": "段落译文。",
                }
                for p in paragraphs
            ]
        },
        "takeaways_json": {
            "title_zh": "测试标题",
            "subtitle_zh": "测试副标题",
            "tags_zh": ["测试", "质量"],
            "article_takeaway": "核心结论",
            "key_expressions": [],
            "sentence_notes": [],
            "writing_moves": [],
            "discussion_questions": ["Question one?", "Question two?"],
        },
    }


@pytest.mark.anyio
async def test_required_highlight_gap_fails_before_semantic_review():
    review_span = AsyncMock(return_value={"output": None})
    with patch(
        "app.services.daily_reader.workflow._run_daily_review_llm_span",
        new=review_span,
    ):
        result = await quality_review_node(_state())

    assert result["review_result"] == {
        "passed": False,
        "reason": "deterministic_quality_gate",
        "issues": [
            {
                "dimension": "highlight_coverage",
                "severity": "major",
                "description": "有实质内容的 reading unit 缺少高亮: p_1",
                "suggestion": "仅为这些 reading unit 补充 1 个准确且有学习价值的高亮",
            }
        ],
    }
    review_span.assert_not_awaited()


@pytest.mark.anyio
async def test_exhausted_highlight_retry_aborts_without_second_compensation():
    state = _state()
    state["highlight_retry_exhausted"] = True
    state["highlight_retry_missing_paragraph_ids"] = ["p_1"]
    review_span = AsyncMock(return_value={"output": None})

    with patch(
        "app.services.daily_reader.workflow._run_daily_review_llm_span",
        new=review_span,
    ):
        result = await quality_review_node(state)

    assert result["abort"] is True
    assert result["review_result"]["reason"] == "deterministic_retry_exhausted"
    assert result["review_result"]["issues"][0]["dimension"] == "highlight_coverage"
    review_span.assert_not_awaited()


@pytest.mark.anyio
async def test_density_and_required_note_gap_are_deterministic_issues():
    state = _state()
    state["highlights_json"] = [
        {"paragraph_id": "p_0", "text": text, "gloss": "释义"}
        for text in ("First", "substantive", "reading", "unit")
    ] + [{"paragraph_id": "p_1", "text": "Second", "gloss": "第二"}]
    state["paragraph_notes_json"]["notes"] = state["paragraph_notes_json"]["notes"][:1]

    review_span = AsyncMock(return_value={"output": None})
    with patch(
        "app.services.daily_reader.workflow._run_daily_review_llm_span",
        new=review_span,
    ):
        result = await quality_review_node(state)

    assert result["review_result"]["issues"] == [
        {
            "dimension": "highlight_density",
            "severity": "major",
            "description": "reading unit 高亮密度超过 B2 上限 3: p_0=4",
            "suggestion": "仅保留各超量 reading unit 中最有学习价值的高亮",
        },
        {
            "dimension": "paragraph_note_coverage",
            "severity": "major",
            "description": "有实质内容的 reading unit 缺少完整透读 note: p_1",
            "suggestion": "仅为这些 reading unit 补充导读、摘要和段落译文",
        },
    ]
    review_span.assert_not_awaited()


@pytest.mark.anyio
async def test_safe_local_repairs_precede_semantic_review():
    state = _state()
    state["highlights_json"] = [
        {"paragraph_id": "p_0", "text": "substantive", "gloss": "实质性的"},
        {"paragraph_id": "wrong", "text": "Second", "gloss": "第二"},
        {"paragraph_id": "p_1", "text": "not in the article", "gloss": "无效"},
    ]
    state["paragraph_notes_json"]["notes"][0]["translation"] = (
        "甲是第一句。乙是更长的第二句。丙是最后一句。"
    )
    state["takeaways_json"].update({
        "key_expressions": [{"expression": f"expression-{i}"} for i in range(8)],
        "sentence_notes": [{
            "sentence": "First substantive reading unit.",
            "paragraph_id": "p_0",
            "translation": "重新翻译。",
        }],
        "writing_moves": [{"move_type": f"move-{i}"} for i in range(3)],
        "discussion_questions": ["Q1?", "Q2?", "Q3?"],
    })
    review_output = MagicMock()
    review_output.model_dump.return_value = {
        "passed": True,
        "overall_score": 9.0,
        "issues": [],
    }
    review_span = AsyncMock(return_value={"output": review_output})

    with patch(
        "app.services.daily_reader.workflow._run_daily_review_llm_span",
        new=review_span,
    ):
        result = await quality_review_node(state)

    assert [h["paragraph_id"] for h in result["highlights_json"]] == ["p_0", "p_1"]
    repaired = result["takeaways_json"]
    assert len(repaired["key_expressions"]) == 7
    assert len(repaired["writing_moves"]) == 2
    assert repaired["discussion_questions"] == ["Q1?", "Q2?"]
    assert repaired["sentence_notes"][0]["translation"] in (
        state["paragraph_notes_json"]["notes"][0]["translation"]
    )
    review = result["review_result"]
    assert review["passed"] is True
    assert [issue["dimension"] for issue in review["issues"]] == [
        "highlight_anchor",
        "translation_consistency",
        "key_expressions_count",
        "writing_moves_count",
        "discussion_questions_count",
    ]
    assert all(set(issue) == {"dimension", "severity", "description", "suggestion"}
               for issue in review["issues"])
    review_span.assert_awaited_once()


@pytest.mark.anyio
async def test_missing_discussion_question_requires_targeted_refinement():
    state = _state()
    state["highlights_json"].append(
        {"paragraph_id": "p_1", "text": "Second", "gloss": "第二"}
    )
    state["takeaways_json"]["discussion_questions"] = ["Only one?"]

    review_span = AsyncMock(return_value={"output": None})
    with patch(
        "app.services.daily_reader.workflow._run_daily_review_llm_span",
        new=review_span,
    ):
        result = await quality_review_node(state)

    assert result["review_result"]["issues"] == [{
        "dimension": "discussion_questions_count",
        "severity": "major",
        "description": "讨论问题必须恰好 2 项，当前 1 项",
        "suggestion": "仅补充缺失的讨论问题，不重写其他文末内容",
    }]
    review_span.assert_not_awaited()


@pytest.mark.anyio
async def test_single_refinement_aborts_when_requirement_is_still_missing():
    state = _state()
    state["highlights_json"].append(
        {"paragraph_id": "p_1", "text": "Second", "gloss": "第二"}
    )
    state["takeaways_json"]["discussion_questions"] = ["Still only one?"]
    state["review_result"] = {
        "passed": False,
        "reason": "deterministic_quality_gate",
        "issues": [{
            "dimension": "discussion_questions_count",
            "severity": "major",
            "description": "讨论问题必须恰好 2 项，当前 1 项",
            "suggestion": "仅补充缺失的讨论问题，不重写其他文末内容",
        }],
    }
    refined_takeaways = MagicMock()
    refined_takeaways.model_dump.return_value = state["takeaways_json"]
    refinement_output = MagicMock()
    refinement_output.abort = False
    refinement_output.refined_highlights = None
    refinement_output.refined_paragraph_notes = None
    refinement_output.refined_takeaways = refined_takeaways
    refinement_output.model_dump.return_value = {"abort": False}
    refinement_span = AsyncMock(return_value={"output": refinement_output})

    with patch(
        "app.services.daily_reader.workflow._run_daily_refinement_llm_span",
        new=refinement_span,
    ):
        result = await refinement_node(state)

    assert result["abort"] is True
    assert result["refinement_result"]["remaining_issues"][0]["dimension"] == (
        "discussion_questions_count"
    )
    refinement_span.assert_awaited_once()


@pytest.mark.anyio
async def test_single_refinement_can_satisfy_missing_requirement():
    state = _state()
    state["highlights_json"].append(
        {"paragraph_id": "p_1", "text": "Second", "gloss": "第二"}
    )
    state["takeaways_json"]["discussion_questions"] = ["First question?"]
    state["review_result"] = {
        "passed": False,
        "reason": "deterministic_quality_gate",
        "issues": [{
            "dimension": "discussion_questions_count",
            "severity": "major",
            "description": "讨论问题必须恰好 2 项，当前 1 项",
            "suggestion": "仅补充缺失的讨论问题，不重写其他文末内容",
        }],
    }
    repaired_takeaways = {
        **state["takeaways_json"],
        "discussion_questions": ["First question?", "Second question?"],
    }
    refined_takeaways = MagicMock()
    refined_takeaways.model_dump.return_value = repaired_takeaways
    refinement_output = MagicMock()
    refinement_output.abort = False
    refinement_output.refined_highlights = None
    refinement_output.refined_paragraph_notes = None
    refinement_output.refined_takeaways = refined_takeaways
    refinement_output.model_dump.return_value = {"abort": False}
    refinement_span = AsyncMock(return_value={"output": refinement_output})

    with patch(
        "app.services.daily_reader.workflow._run_daily_refinement_llm_span",
        new=refinement_span,
    ):
        result = await refinement_node(state)

    assert result.get("abort") is not True
    assert result["takeaways_json"]["discussion_questions"] == [
        "First question?",
        "Second question?",
    ]
    refinement_span.assert_awaited_once()


@pytest.mark.anyio
async def test_single_refinement_aborts_when_boilerplate_remains():
    state = _state()
    state["highlights_json"].append(
        {"paragraph_id": "p_1", "text": "Second", "gloss": "第二"}
    )
    state["review_result"] = {
        "passed": False,
        "reason": "boilerplate_leak",
        "issues": [{
            "dimension": "boilerplate",
            "severity": "major",
            "description": "疑似脏数据仍在",
            "suggestion": "删除脏数据",
        }],
    }
    still_dirty = {
        **state["takeaways_json"],
        "article_takeaway": "Copyright © 2026 NPR. All rights reserved.",
    }
    refined_takeaways = MagicMock()
    refined_takeaways.model_dump.return_value = still_dirty
    refinement_output = MagicMock()
    refinement_output.abort = False
    refinement_output.refined_highlights = None
    refinement_output.refined_paragraph_notes = None
    refinement_output.refined_takeaways = refined_takeaways
    refinement_output.model_dump.return_value = {"abort": False}

    with patch(
        "app.services.daily_reader.workflow._run_daily_refinement_llm_span",
        new=AsyncMock(return_value={"output": refinement_output}),
    ):
        result = await refinement_node(state)

    assert result["abort"] is True
    assert result["refinement_result"]["remaining_issues"][0]["dimension"] == (
        "boilerplate"
    )


@pytest.mark.anyio
async def test_semantic_review_failure_aborts_instead_of_passing_open():
    state = _state()
    state["highlights_json"].append(
        {"paragraph_id": "p_1", "text": "Second", "gloss": "第二"}
    )

    with patch(
        "app.services.daily_reader.workflow._run_daily_review_llm_span",
        new=AsyncMock(side_effect=RuntimeError("provider unavailable")),
    ):
        result = await quality_review_node(state)

    assert result["abort"] is True
    assert result["review_result"] == {
        "passed": False,
        "reason": "semantic_review_unavailable",
        "issues": [{
            "dimension": "semantic_review",
            "severity": "major",
            "description": "语义质量审核执行失败",
            "suggestion": "保留草稿并由后续重试或人工审核处理",
        }],
    }


def test_review_prompt_sees_article_tail_and_excludes_mechanical_dimensions():
    prompt = build_daily_review_prompt(DailyReviewAgentDeps(
        original_text="A" * 5000 + "TAIL_EVIDENCE",
        highlights_json="[]",
        paragraph_notes_json="{}",
        takeaways_json="{}",
        difficulty="C1",
    ))

    assert "TAIL_EVIDENCE" in prompt
    assert "C1 语义审核" in prompt
    for mechanical_dimension in (
        "highlight_coverage",
        "highlight_density",
        "note_density",
        "writing_moves 超过",
        "discussion_questions 少于",
        "表达超过 7 个",
    ):
        assert mechanical_dimension not in prompt

    bounded_prompt = build_daily_review_prompt(DailyReviewAgentDeps(
        original_text="A" * 20_000 + "OUTSIDE_REVIEW_BOUND",
        highlights_json="[]",
        paragraph_notes_json="{}",
        takeaways_json="{}",
        difficulty="C1",
    ))
    assert "OUTSIDE_REVIEW_BOUND" not in bounded_prompt


def test_targeted_refinement_prompt_sees_article_tail():
    prompt = build_daily_refinement_prompt(DailyRefinementAgentDeps(
        original_text="A" * 5000 + "TAIL_TO_REPAIR",
        review_issues='[{"dimension":"highlight_coverage"}]',
    ))

    assert "TAIL_TO_REPAIR" in prompt
