"""Scoring layer for Daily Reader article pipeline.

Evaluates candidate articles using LLM-based 4-dimension scoring,
CEFR difficulty estimation, and deduplication.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from langsmith import traceable

from app.services.daily_reader.discovery import DiscoveredArticle

logger = logging.getLogger(__name__)

MIN_WORD_COUNT = 400
MAX_WORD_COUNT = 2500
SCORE_THRESHOLD = 7.0
HEURISTIC_THRESHOLD = 6.0


@dataclass
class ArticleScore:
    score: float
    difficulty: str
    tags: list[str] = field(default_factory=list)
    language_richness: float = 0.0
    topic_interest: float = 0.0
    structure_clarity: float = 0.0
    cultural_value: float = 0.0


def filter_by_word_count(articles: list[DiscoveredArticle]) -> list[DiscoveredArticle]:
    return [a for a in articles if MIN_WORD_COUNT <= a.word_count <= MAX_WORD_COUNT]


async def score_article(article: DiscoveredArticle) -> ArticleScore | None:
    try:
        from app.llm.router import build_model_for_route
        from app.config.settings import get_settings

        settings = get_settings()
        model, model_config = build_model_for_route(settings, "daily_analysis")
        if model is None:
            logger.warning("daily_analysis model not available, using heuristic scoring")
            return heuristic_score(article)

        model_name = model_config.model_name if model_config else "unknown"
        profile_name = model_config.profile_name if model_config else "unknown"
        provider = model_config.provider if model_config else "unknown"

        metadata = {
            "workflow_name": "daily_reader",
            "workflow_version": "2.0.0",
            "node": "scoring",
            "article_title": article.title[:80],
            "article_source": article.source,
            "article_word_count": article.word_count,
            "model_profile": profile_name,
            "model_provider": provider,
            "model_name": model_name,
            "ls_provider": provider,
            "ls_model_name": model_name,
        }
        trace_result = await _score_article_llm_span(
            article=article,
            model=model,
            model_name=model_name,
            profile_name=profile_name,
            provider=provider,
            langsmith_extra={"metadata": metadata},
        )
        return trace_result.get("output")
    except Exception as e:
        logger.warning("LLM scoring failed, falling back to heuristic: %s", e)
        return heuristic_score(article)


@traceable(name="daily_scoring_llm_call", run_type="llm")
async def _score_article_llm_span(
    *,
    article: DiscoveredArticle,
    model: object,
    model_name: str,
    profile_name: str,
    provider: str,
) -> dict[str, Any]:
    from pydantic_ai import Agent
    from app.llm.agent_runner import extract_run_usage

    scoring_agent = Agent(
        model=model,
        output_type=_ScoringOutput,
        name="daily_scoring_agent",
        retries=1,
        output_retries=2,
        instrument=False,
    )

    prompt = _build_scoring_prompt(article)
    result = await scoring_agent.run(prompt)
    output = result.output
    usage = extract_run_usage(result)

    overall = (
        output.language_richness
        + output.topic_interest
        + output.structure_clarity
        + output.cultural_value
    ) / 4.0

    return {
        "output": ArticleScore(
            score=round(overall, 1),
            difficulty=output.difficulty,
            tags=output.tags,
            language_richness=output.language_richness,
            topic_interest=output.topic_interest,
            structure_clarity=output.structure_clarity,
            cultural_value=output.cultural_value,
        ),
        "usage_metadata": usage,
    }


def deduplicate(
    articles: list[DiscoveredArticle],
    existing_hashes: set[str] | None = None,
) -> list[DiscoveredArticle]:
    existing_hashes = existing_hashes or set()
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    result: list[DiscoveredArticle] = []

    for article in articles:
        if article.url in seen_urls:
            continue

        title_lower = article.title.lower().strip()
        if _fuzzy_title_match(title_lower, seen_titles):
            continue

        text_hash = hashlib.sha256(article.text.encode()).hexdigest()
        if text_hash in existing_hashes:
            continue

        seen_urls.add(article.url)
        seen_titles.add(title_lower)
        result.append(article)

    return result


def _fuzzy_title_match(title: str, existing: set[str], threshold: float = 0.85) -> bool:
    for existing_title in existing:
        if _similarity(title, existing_title) >= threshold:
            return True
    return False


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    set_a = set(a.split())
    set_b = set(b.split())
    intersection = set_a & set_b
    union = set_a | set_b
    if not union:
        return 0.0
    return len(intersection) / len(union)


def heuristic_score(article: DiscoveredArticle) -> ArticleScore:
    wc = article.word_count
    if wc < 400:
        score = 4.0
    elif wc < 600:
        score = 6.0
    elif wc < 1200:
        score = 7.5
    elif wc < 2000:
        score = 7.0
    else:
        score = 5.5

    if article.cover_image_url:
        score += 0.3

    difficulty = "B1"
    if wc > 1500:
        difficulty = "B2"
    if wc > 2000:
        difficulty = "C1"

    return ArticleScore(
        score=min(round(score, 1), 10.0),
        difficulty=difficulty,
        tags=article.tags,
    )


def _build_scoring_prompt(article: DiscoveredArticle) -> str:
    text_preview = article.text[:3000]
    return f"""Evaluate this English article for a daily close-reading feature targeting Chinese English learners.

Title: {article.title}
Source: {article.source}
Word count: {article.word_count}

Text preview:
{text_preview}

Score each dimension from 1-10:
- language_richness: Vocabulary richness for English learning (diverse vocabulary, useful expressions)
- topic_interest: General readability and interest level for a broad audience
- structure_clarity: Suitability for close reading (clear structure, logical flow)
- cultural_value: Knowledge/cultural insight value for Chinese learners

Also provide:
- difficulty: CEFR level (A2, B1, B2, C1)
- tags: 2-4 topic tags for categorization"""


class _ScoringOutput(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    language_richness: float = Field(description="Vocabulary richness score 1-10")
    topic_interest: float = Field(description="Topic interest score 1-10")
    structure_clarity: float = Field(description="Structure clarity score 1-10")
    cultural_value: float = Field(description="Cultural value score 1-10")
    difficulty: str = Field(description="CEFR level: A2, B1, B2, C1")
    tags: list[str] = Field(default_factory=list, description="2-4 topic tags")
