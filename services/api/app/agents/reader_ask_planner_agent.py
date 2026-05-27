from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache

from pydantic_ai import Agent

from app.schemas.reader_ask import ReaderAskPlannerDecision, ReaderAskPlannerInput
from app.services.analysis.prompting.prompt_loader import load_agent_instructions


@dataclass(slots=True)
class ReaderAskPlannerAgentDeps:
    planner_input: ReaderAskPlannerInput


def build_reader_ask_planner_prompt(deps: ReaderAskPlannerAgentDeps) -> str:
    return json.dumps(deps.planner_input.model_dump(mode="json"), ensure_ascii=False, indent=2)


@lru_cache(maxsize=1)
def get_reader_ask_planner_agent() -> Agent[ReaderAskPlannerAgentDeps, ReaderAskPlannerDecision]:
    return Agent[ReaderAskPlannerAgentDeps, ReaderAskPlannerDecision](
        model=None,
        output_type=ReaderAskPlannerDecision,
        deps_type=ReaderAskPlannerAgentDeps,
        instructions=load_agent_instructions("reader_ask_planner"),
        name="reader_ask_planner_agent",
        retries=1,
        output_retries=2,
        instrument=False,
    )
