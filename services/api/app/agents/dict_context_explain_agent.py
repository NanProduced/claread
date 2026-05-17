from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from pydantic_ai import Agent

from app.services.analysis.prompting.prompt_loader import load_agent_instructions
from app.services.dictionary_ai.schemas import DictionaryAIContextExplainResponse


@dataclass
class DictContextExplainAgentDeps:
    query: str
    query_type: Literal["word", "phrase"]
    context_sentence: str
    occurrence: int | None
    entry_payload: dict[str, object]


def build_dict_context_explain_prompt(deps: DictContextExplainAgentDeps) -> str:
    payload = {
        "query": deps.query,
        "query_type": deps.query_type,
        "context_sentence": deps.context_sentence,
        "occurrence": deps.occurrence,
        "entry": deps.entry_payload,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


@lru_cache(maxsize=1)
def get_dict_context_explain_agent() -> Agent[DictContextExplainAgentDeps, DictionaryAIContextExplainResponse]:
    return Agent[DictContextExplainAgentDeps, DictionaryAIContextExplainResponse](
        model=None,
        output_type=DictionaryAIContextExplainResponse,
        deps_type=DictContextExplainAgentDeps,
        instructions=load_agent_instructions("dict_context_explain"),
        name="dict_context_explain_agent",
        retries=2,
        output_retries=3,
        instrument=False,
    )
