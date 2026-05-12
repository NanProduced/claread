"""Vocabulary agent for V3 workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from pydantic_ai import Agent

from app.schemas.internal.drafts import VocabularyDraft
from app.services.analysis.prompting.example_strategy import ExampleEntry
from app.services.analysis.prompting.prompt_composer import build_agent_prompt
from app.services.analysis.prompting.prompt_loader import load_agent_instructions
from app.services.analysis.prompting.prompt_strategy import PromptStrategy, build_prompt_sections


@dataclass
class VocabularyAgentDeps:
    """Vocabulary agent 依赖。"""

    sentences: list[dict[str, object]]
    prompt_strategy: PromptStrategy
    examples: list[ExampleEntry] = field(default_factory=list)


def build_vocabulary_prompt(deps: VocabularyAgentDeps) -> str:
    return build_agent_prompt(
        strategy_sections=build_prompt_sections(deps.prompt_strategy),
        examples=deps.examples,
        sentences=deps.sentences,
    )


@lru_cache(maxsize=1)
def get_vocabulary_agent() -> Agent[VocabularyAgentDeps, VocabularyDraft]:
    return Agent[VocabularyAgentDeps, VocabularyDraft](
        model=None,
        output_type=VocabularyDraft,
        deps_type=VocabularyAgentDeps,
        instructions=load_agent_instructions("vocabulary"),
        name="vocabulary_agent",
        retries=2,
        output_retries=3,
        instrument=False,
    )
