"""Vocabulary highlight agent for Daily Reader workflow.

Redesigned: supports per-batch generation with batch_index/total_batches
to ensure full-paragraph coverage (fixes front-half bias).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from pydantic_ai import Agent

from app.schemas.internal.daily_drafts import DailyVocabDraft
from app.services.analysis.prompting.daily_prompt_strategy import (
    DailyPromptStrategy,
    build_daily_prompt_sections,
    build_vocab_highlight_strategy,
)
from app.services.analysis.prompting.prompt_loader import load_agent_instructions


@dataclass
class DailyVocabAgentDeps:
    paragraphs: list[dict[str, object]]
    batch_index: int = 0
    total_batches: int = 1
    prompt_strategy: DailyPromptStrategy = field(default_factory=build_vocab_highlight_strategy)


def build_daily_vocab_prompt(deps: DailyVocabAgentDeps) -> str:
    from app.services.analysis.prompting.prompt_composer import render_prompt_sections, PromptSection

    sections = build_daily_prompt_sections(deps.prompt_strategy)
    paragraph_lines = []
    for p in deps.paragraphs:
        pid = p.get("paragraph_id", "")
        text = p.get("text", "")
        paragraph_lines.append(f"{pid}: {text}")

    all_sections = list(sections) + [
        PromptSection("input_paragraphs", tuple(paragraph_lines)),
        PromptSection("batch_info", (
            f"当前批次：第 {deps.batch_index + 1} 批（共 {deps.total_batches} 批）",
            f"本批段落数：{len(deps.paragraphs)}",
            "请确保为输入的每一段都生成标注，不要遗漏任何段落。",
        )),
    ]
    return render_prompt_sections(all_sections)


@lru_cache(maxsize=1)
def get_daily_vocab_agent() -> Agent[DailyVocabAgentDeps, DailyVocabDraft]:
    return Agent[DailyVocabAgentDeps, DailyVocabDraft](
        model=None,
        output_type=DailyVocabDraft,
        deps_type=DailyVocabAgentDeps,
        instructions=load_agent_instructions("daily_vocab"),
        name="daily_vocab_agent",
        retries=2,
        output_retries=3,
        instrument=False,
    )
