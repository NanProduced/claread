"""Refinement agent for Daily Reader workflow.

Redesigned: refines highlights, paragraph_notes, and takeaways
instead of the old highlights, footer, and interpretation string.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from pydantic_ai import Agent

from app.schemas.internal.daily_drafts import DailyRefinementDraft
from app.services.analysis.prompting.daily_prompt_strategy import (
    DailyPromptStrategy,
    build_daily_prompt_sections,
    build_refinement_strategy,
)
from app.services.analysis.prompting.prompt_loader import load_agent_instructions

# Refinement receives original text, review issues, and current drafts together.
# These caps keep the prompt bounded while preserving enough local context for
# targeted fixes identified by the review agent.
MAX_REFINEMENT_ORIGINAL_TEXT_CHARS = 4000
MAX_REFINEMENT_HIGHLIGHTS_CHARS = 8000
MAX_REFINEMENT_PARAGRAPH_NOTES_CHARS = 9000
MAX_REFINEMENT_TAKEAWAYS_CHARS = 3000


@dataclass
class DailyRefinementAgentDeps:
    original_text: str
    review_issues: str
    current_highlights: str = ""
    current_paragraph_notes: str = ""
    current_takeaways: str = ""
    prompt_strategy: DailyPromptStrategy = field(default_factory=build_refinement_strategy)


def build_daily_refinement_prompt(deps: DailyRefinementAgentDeps) -> str:
    from app.services.analysis.prompting.prompt_composer import render_prompt_sections, PromptSection

    sections = build_daily_prompt_sections(deps.prompt_strategy)
    all_sections = list(sections) + [
        PromptSection("original_text", (deps.original_text[:MAX_REFINEMENT_ORIGINAL_TEXT_CHARS],)),
        PromptSection("review_issues", (deps.review_issues,)),
    ]
    if deps.current_highlights:
        all_sections.append(PromptSection("current_highlights", (deps.current_highlights[:MAX_REFINEMENT_HIGHLIGHTS_CHARS],)))
    if deps.current_paragraph_notes:
        all_sections.append(PromptSection("current_paragraph_notes", (deps.current_paragraph_notes[:MAX_REFINEMENT_PARAGRAPH_NOTES_CHARS],)))
    if deps.current_takeaways:
        all_sections.append(PromptSection("current_takeaways", (deps.current_takeaways[:MAX_REFINEMENT_TAKEAWAYS_CHARS],)))
    return render_prompt_sections(all_sections)


@lru_cache(maxsize=1)
def get_daily_refinement_agent() -> Agent[DailyRefinementAgentDeps, DailyRefinementDraft]:
    return Agent[DailyRefinementAgentDeps, DailyRefinementDraft](
        model=None,
        output_type=DailyRefinementDraft,
        deps_type=DailyRefinementAgentDeps,
        instructions=load_agent_instructions("daily_refinement"),
        name="daily_refinement_agent",
        retries=2,
        output_retries=3,
        instrument=False,
    )
