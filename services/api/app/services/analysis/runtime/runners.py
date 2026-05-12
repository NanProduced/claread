from __future__ import annotations

from typing import Any

from app.agents.grammar_agent import GrammarAgentDeps, build_grammar_prompt, get_grammar_agent
from app.agents.translation_agent import (
    TranslationAgentDeps,
    build_translation_prompt,
    get_translation_agent,
)
from app.agents.vocabulary_agent import (
    VocabularyAgentDeps,
    build_vocabulary_prompt,
    get_vocabulary_agent,
)
from app.llm.agent_runner import run_agent_with_route
from app.llm.routes import MODEL_ROUTE_ANNOTATION_GENERATION
from app.llm.types import ModelSelection


async def run_vocabulary_agent(
    deps: VocabularyAgentDeps,
    model_selection: ModelSelection | None = None,
) -> Any:
    return await run_agent_with_route(
        agent=get_vocabulary_agent(),
        prompt=build_vocabulary_prompt(deps),
        deps=deps,
        route=MODEL_ROUTE_ANNOTATION_GENERATION,
        model_selection=model_selection,
    )


async def run_grammar_agent(
    deps: GrammarAgentDeps,
    model_selection: ModelSelection | None = None,
) -> Any:
    return await run_agent_with_route(
        agent=get_grammar_agent(),
        prompt=build_grammar_prompt(deps),
        deps=deps,
        route=MODEL_ROUTE_ANNOTATION_GENERATION,
        model_selection=model_selection,
    )


async def run_translation_agent(
    deps: TranslationAgentDeps,
    model_selection: ModelSelection | None = None,
) -> Any:
    return await run_agent_with_route(
        agent=get_translation_agent(),
        prompt=build_translation_prompt(deps),
        deps=deps,
        route=MODEL_ROUTE_ANNOTATION_GENERATION,
        model_selection=model_selection,
    )
