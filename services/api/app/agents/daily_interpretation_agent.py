"""Close reading takeaways agent for Daily Reader workflow.

Redesigned: generates CloseReadingTakeaways (article_takeaway,
key_expressions, sentence_notes, writing_moves, discussion_questions)
instead of the old DailyInterpretationDraft (full_article_analysis string).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from pydantic_ai import Agent

from app.schemas.internal.daily_drafts import CloseReadingTakeaways
from app.services.prompting.daily_prompt_strategy import (
    DailyPromptStrategy,
    build_daily_prompt_sections,
    build_close_reading_takeaways_strategy,
    difficulty_prompt_section,
)
from app.services.prompting.prompt_loader import load_agent_instructions


@dataclass
class DailyInterpretationAgentDeps:
    full_text: str
    title: str
    highlights_summary: str = ""
    paragraph_translations: str = ""
    difficulty: str = ""
    prompt_strategy: DailyPromptStrategy = field(default_factory=build_close_reading_takeaways_strategy)


def build_daily_interpretation_prompt(deps: DailyInterpretationAgentDeps) -> str:
    from app.services.prompting.prompt_composer import render_prompt_sections, PromptSection

    sections = build_daily_prompt_sections(deps.prompt_strategy)
    all_sections = list(sections)
    difficulty_section = difficulty_prompt_section("daily_interpretation", deps.difficulty)
    if difficulty_section is not None:
        all_sections.append(difficulty_section)
    all_sections += [
        PromptSection("article_info", (f"Title: {deps.title}",)),
        PromptSection("full_text", (deps.full_text[:6000],)),
    ]
    if deps.highlights_summary:
        all_sections.append(PromptSection("highlights_context", (deps.highlights_summary,)))
    if deps.paragraph_translations:
        all_sections.append(PromptSection("paragraph_translations", (deps.paragraph_translations,)))
    return render_prompt_sections(all_sections)


@lru_cache(maxsize=1)
def get_daily_interpretation_agent() -> Agent[DailyInterpretationAgentDeps, CloseReadingTakeaways]:
    return Agent[DailyInterpretationAgentDeps, CloseReadingTakeaways](
        model=None,
        output_type=CloseReadingTakeaways,
        deps_type=DailyInterpretationAgentDeps,
        instructions=load_agent_instructions("daily_interpretation"),
        name="daily_takeaways_agent",
        retries=2,
        output_retries=3,
        instrument=False,
    )
