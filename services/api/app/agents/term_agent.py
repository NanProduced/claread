from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from pydantic_ai import Agent

from app.schemas.internal.academic_drafts import TermDraft
from app.services.analysis.prompting.example_strategy import ExampleEntry
from app.services.analysis.prompting.prompt_composer import build_agent_prompt
from app.services.analysis.prompting.prompt_loader import load_agent_instructions
from app.services.analysis.prompting.prompt_strategy import PromptStrategy, build_prompt_sections


@dataclass
class TermAgentDeps:
    sentences: list[dict[str, object]]
    prompt_strategy: PromptStrategy
    examples: list[ExampleEntry] = field(default_factory=list)


def build_term_prompt(deps: TermAgentDeps) -> str:
    return build_agent_prompt(
        strategy_sections=build_prompt_sections(deps.prompt_strategy),
        examples=deps.examples,
        sentences=deps.sentences,
    )


@lru_cache(maxsize=1)
def get_term_agent() -> Agent[TermAgentDeps, TermDraft]:
    return Agent[TermAgentDeps, TermDraft](
        model=None,
        output_type=TermDraft,
        deps_type=TermAgentDeps,
        instructions=load_agent_instructions("term"),
        name="term_agent",
        retries=2,
        output_retries=2,
        instrument=False,
    )
