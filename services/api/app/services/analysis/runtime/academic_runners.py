from __future__ import annotations

from typing import Any

from app.agents.academic_translation_agent import (
    AcademicTranslationAgentDeps,
    build_academic_translation_prompt,
    get_academic_translation_agent,
)
from app.agents.term_agent import (
    TermAgentDeps,
    build_term_prompt,
    get_term_agent,
)
from app.agents.understanding_agent import (
    UnderstandingAgentDeps,
    build_understanding_prompt,
    get_understanding_agent,
)
from app.llm.agent_runner import run_agent_with_route
from app.llm.routes import MODEL_ROUTE_ANNOTATION_GENERATION
from app.llm.types import ModelSelection


async def run_term_agent(
    deps: TermAgentDeps,
    model_selection: ModelSelection | None = None,
) -> Any:
    return await run_agent_with_route(
        agent=get_term_agent(),
        prompt=build_term_prompt(deps),
        deps=deps,
        route=MODEL_ROUTE_ANNOTATION_GENERATION,
        model_selection=model_selection,
    )


async def run_academic_translation_agent(
    deps: AcademicTranslationAgentDeps,
    model_selection: ModelSelection | None = None,
) -> Any:
    return await run_agent_with_route(
        agent=get_academic_translation_agent(),
        prompt=build_academic_translation_prompt(deps),
        deps=deps,
        route=MODEL_ROUTE_ANNOTATION_GENERATION,
        model_selection=model_selection,
    )


async def run_understanding_agent(
    deps: UnderstandingAgentDeps,
    model_selection: ModelSelection | None = None,
) -> Any:
    return await run_agent_with_route(
        agent=get_understanding_agent(),
        prompt=build_understanding_prompt(deps),
        deps=deps,
        route=MODEL_ROUTE_ANNOTATION_GENERATION,
        model_selection=model_selection,
    )
