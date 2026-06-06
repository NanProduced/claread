"""Grammar agent for V3 workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from pydantic_ai import Agent

from app.schemas.internal.drafts import GrammarDraft
from app.services.analysis.prompting.example_strategy import ExampleEntry
from app.services.analysis.prompting.prompt_composer import build_agent_prompt
from app.services.analysis.prompting.prompt_loader import load_agent_instructions
from app.services.analysis.prompting.prompt_strategy import PromptStrategy, build_prompt_sections
from app.services.analysis.prompting.runtime_context import is_prompt_override_active


@dataclass
class GrammarAgentDeps:
    """Grammar agent 依赖。"""

    sentences: list[dict[str, object]]
    prompt_strategy: PromptStrategy
    examples: list[ExampleEntry] = field(default_factory=list)
    focus_guidance: dict[str, object] | None = None


def build_grammar_prompt(deps: GrammarAgentDeps) -> str:
    return build_agent_prompt(
        strategy_sections=build_prompt_sections(deps.prompt_strategy),
        examples=deps.examples,
        sentences=deps.sentences,
        focus_guidance=deps.focus_guidance,
    )


def _build_grammar_agent() -> Agent[GrammarAgentDeps, GrammarDraft]:
    return Agent[GrammarAgentDeps, GrammarDraft](
        model=None,
        output_type=GrammarDraft,
        deps_type=GrammarAgentDeps,
        instructions=load_agent_instructions("grammar"),
        name="grammar_agent",
        retries=2,
        output_retries=3,
        instrument=False,
    )


@lru_cache(maxsize=1)
def _get_cached_grammar_agent() -> Agent[GrammarAgentDeps, GrammarDraft]:
    return _build_grammar_agent()


def get_grammar_agent() -> Agent[GrammarAgentDeps, GrammarDraft]:
    if is_prompt_override_active():
        return _build_grammar_agent()
    return _get_cached_grammar_agent()
