"""Paragraph notes and translations agent for Daily Reader workflow.

Redesigned: generates ParagraphNotesDraft (article_summary, reading_focus,
per-paragraph focus_question/micro_summary/translation) instead of
the old DailyFooterDraft (summary/thesis/structure/key_expressions/...).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from pydantic_ai import Agent

from app.schemas.internal.daily_drafts import ParagraphNotesDraft
from app.services.analysis.prompting.daily_prompt_strategy import (
    DailyPromptStrategy,
    build_daily_prompt_sections,
    build_paragraph_notes_strategy,
)
from app.services.analysis.prompting.prompt_loader import load_agent_instructions

# Keep the paragraph-notes prompt comfortably below small/fast model windows.
# Full article text is only used for global context; exact paragraph text is
# supplied separately via paragraphs_info.
MAX_FOOTER_FULL_TEXT_CHARS = 8000


@dataclass
class DailyFooterAgentDeps:
    full_text: str
    title: str
    highlights_summary: str = ""
    paragraphs_info: str = ""
    prompt_strategy: DailyPromptStrategy = field(default_factory=build_paragraph_notes_strategy)


def build_daily_footer_prompt(deps: DailyFooterAgentDeps) -> str:
    from app.services.analysis.prompting.prompt_composer import render_prompt_sections, PromptSection

    sections = build_daily_prompt_sections(deps.prompt_strategy)
    all_sections = list(sections) + [
        PromptSection("article_info", (
            f"Title: {deps.title}",
        )),
        PromptSection("full_text", (deps.full_text[:MAX_FOOTER_FULL_TEXT_CHARS],)),
    ]
    if deps.paragraphs_info:
        all_sections.append(PromptSection("paragraphs_info", (deps.paragraphs_info,)))
    if deps.highlights_summary:
        all_sections.append(PromptSection("highlights_context", (deps.highlights_summary,)))
    return render_prompt_sections(all_sections)


@lru_cache(maxsize=1)
def get_daily_footer_agent() -> Agent[DailyFooterAgentDeps, ParagraphNotesDraft]:
    return Agent[DailyFooterAgentDeps, ParagraphNotesDraft](
        model=None,
        output_type=ParagraphNotesDraft,
        deps_type=DailyFooterAgentDeps,
        instructions=load_agent_instructions("daily_footer"),
        name="daily_paragraph_notes_agent",
        retries=2,
        output_retries=3,
        instrument=False,
    )
