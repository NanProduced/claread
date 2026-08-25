"""P-5A: learning_fit as the fifth scoring dimension.

Locks the expanded scoring contract:

- the LLM output model REQUIRES learning_fit (missing key is a pydantic
  validation error, not a silent zero);
- the overall score is the FIVE-dimension mean;
- pipeline_meta.score_details carries learning_fit;
- the inline prompt declares the fifth dimension with the agreed wording
  and records its own version tag for provenance;
- the heuristic fallback path fills a neutral learning_fit.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pydantic
import pytest

from app.services.daily_reader.discovery import DiscoveredArticle
from app.services.daily_reader.scoring import (
    SCORING_PROMPT_VERSION,
    ArticleScore,
    _build_scoring_prompt,
    _overall_score,
    _ScoringOutput,
    heuristic_score,
)

# 任务书文案基线（原文逐字，implicit concat 拼接）
_LEARNING_FIT_LINE = (
    "- learning_fit: 评估文章的英语学习适配性：可迁移语言密度、篇幅适配、"
    "教学价值；过易或低适配文章不得高分"
)


def _article() -> DiscoveredArticle:
    return DiscoveredArticle(
        title="Test article",
        url="https://example.com/test",
        source="BBC News",
        description="Desc",
        tags=["science"],
        text="Word " * 300,
        word_count=900,
    )


def _output(**over) -> _ScoringOutput:
    values = {
        "language_richness": 8.0,
        "topic_interest": 7.0,
        "structure_clarity": 9.0,
        "cultural_value": 6.0,
        "learning_fit": 5.0,
        "difficulty": "B1",
        "tags": ["education"],
    }
    values.update(over)
    return _ScoringOutput(**values)


def test_learning_fit_is_required_in_llm_output():
    with pytest.raises(pydantic.ValidationError):
        _ScoringOutput(
            language_richness=8.0,
            topic_interest=7.0,
            structure_clarity=9.0,
            cultural_value=6.0,
            difficulty="B1",
        )


def test_overall_score_is_five_dimension_mean():
    assert _overall_score(_output()) == (8.0 + 7.0 + 9.0 + 6.0 + 5.0) / 5.0


def test_low_learning_fit_drags_overall_down():
    # 过易/低适配样本：低 learning_fit 直接拉低综合分（其余四维满分）
    easy = _output(
        language_richness=10.0,
        topic_interest=10.0,
        structure_clarity=10.0,
        cultural_value=10.0,
        learning_fit=3.0,
    )
    assert round(_overall_score(easy), 1) == 8.6
    fit = _output(
        language_richness=10.0,
        topic_interest=10.0,
        structure_clarity=10.0,
        cultural_value=10.0,
        learning_fit=10.0,
    )
    assert _overall_score(easy) < _overall_score(fit)


def test_prompt_declares_learning_fit_with_agreed_wording():
    prompt = _build_scoring_prompt(_article())
    assert _LEARNING_FIT_LINE in prompt
    for dim in ("language_richness", "topic_interest", "structure_clarity", "cultural_value"):
        assert f"- {dim}:" in prompt


def test_heuristic_fallback_uses_neutral_learning_fit():
    score = heuristic_score(_article())
    assert score.learning_fit == 6.0


def test_article_score_defaults_keep_topic_selection_compat():
    score = ArticleScore(score=8.0, difficulty="B1", tags=["t"])
    assert score.learning_fit == 0.0


@pytest.mark.anyio
async def test_score_details_carry_learning_fit():
    import inspect

    from app.services.daily_reader.pipeline import (
        _assemble_payload,
        _run_workflow_and_store,
    )

    # the workflow input assembly feeds all five dims into pipeline_meta
    src = inspect.getsource(_run_workflow_and_store)
    assert '"learning_fit": score.learning_fit' in src

    # and the stored payload keeps the key through _assemble_payload
    score = ArticleScore(
        score=8.0,
        difficulty="B2",
        tags=["news"],
        language_richness=8.0,
        topic_interest=7.0,
        structure_clarity=9.0,
        cultural_value=6.0,
        learning_fit=6.5,
    )
    state = {
        "pipeline_meta": {
            "score": 8.0,
            "score_details": {
                "language_richness": 8.0,
                "topic_interest": 7.0,
                "structure_clarity": 9.0,
                "cultural_value": 6.0,
                "learning_fit": 6.5,
            },
        }
    }
    with patch(
        "app.services.daily_reader.pipeline._next_sequence_number",
        AsyncMock(return_value=1),
    ):
        payload = await _assemble_payload(_article(), score, state)
    details = payload["pipeline_meta"]["score_details"]
    assert details["learning_fit"] == 6.5
    assert set(details) == {
        "language_richness",
        "topic_interest",
        "structure_clarity",
        "cultural_value",
        "learning_fit",
    }


def test_scoring_prompt_version_constant_is_recorded():
    # provenance: usage events and LangSmith metadata reference this tag;
    # bumping _build_scoring_prompt without bumping it breaks the contract.
    assert SCORING_PROMPT_VERSION == "1.1.0"


@pytest.fixture
def anyio_backend():
    return "asyncio"
