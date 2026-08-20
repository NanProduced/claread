"""Fuzzy/semantic quality review agent for the Daily Reader workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from pydantic_ai import Agent

from app.schemas.internal.daily_drafts import DailyReviewDraft
from app.services.prompting.daily_prompt_strategy import (
    DailyPromptStrategy,
    build_daily_prompt_sections,
    build_quality_review_strategy,
    difficulty_prompt_section,
)
from app.services.prompting.prompt_loader import load_agent_instructions

MAX_REVIEW_ORIGINAL_TEXT_CHARS = 20_000
MAX_REVIEW_ARTIFACT_CHARS = 3_000


@dataclass
class DailyReviewAgentDeps:
    original_text: str
    highlights_json: str
    paragraph_notes_json: str
    takeaways_json: str
    difficulty: str = ""
    prompt_strategy: DailyPromptStrategy = field(default_factory=build_quality_review_strategy)


def build_daily_review_prompt(deps: DailyReviewAgentDeps) -> str:
    from app.services.prompting.prompt_composer import PromptSection, render_prompt_sections

    sections = build_daily_prompt_sections(deps.prompt_strategy)
    all_sections = list(sections)
    difficulty_section = difficulty_prompt_section("daily_review", deps.difficulty)
    if difficulty_section is not None:
        all_sections.append(difficulty_section)
    if deps.difficulty:
        all_sections.append(PromptSection("article_difficulty", (f"文章难度：{deps.difficulty}",)))
    all_sections += [
        # Daily discovery accepts at most 2,500 English words; 20k chars
        # normally exposes the full article while bounding review cost.
        PromptSection("original_text", (deps.original_text[:MAX_REVIEW_ORIGINAL_TEXT_CHARS],)),
        PromptSection("highlights", (deps.highlights_json[:MAX_REVIEW_ARTIFACT_CHARS],)),
        PromptSection("paragraph_notes", (deps.paragraph_notes_json[:MAX_REVIEW_ARTIFACT_CHARS],)),
        PromptSection("takeaways", (deps.takeaways_json[:MAX_REVIEW_ARTIFACT_CHARS],)),
    ]
    return render_prompt_sections(all_sections)


@lru_cache(maxsize=1)
def get_daily_review_agent() -> Agent[DailyReviewAgentDeps, DailyReviewDraft]:
    return Agent[DailyReviewAgentDeps, DailyReviewDraft](
        model=None,
        output_type=DailyReviewDraft,
        deps_type=DailyReviewAgentDeps,
        instructions=load_agent_instructions("daily_review"),
        name="daily_review_agent",
        retries=2,
        output_retries=3,
        instrument=False,
    )
