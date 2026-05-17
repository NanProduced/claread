from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from pydantic_ai import Agent

from app.services.analysis.prompting.prompt_loader import load_agent_instructions
from app.services.dictionary_ai.schemas import (
    DictionaryAIMissingFallbackEntryResponse,
    DictionaryAIMissingFallbackUnresolvedResponse,
)


@dataclass
class DictMissingFallbackAgentDeps:
    query: str
    query_type: Literal["word", "phrase"]
    context_sentence: str
    occurrence: int | None


def build_dict_missing_fallback_prompt(deps: DictMissingFallbackAgentDeps) -> str:
    payload = {
        "query": deps.query,
        "query_type": deps.query_type,
        "context_sentence": deps.context_sentence,
        "occurrence": deps.occurrence,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


@lru_cache(maxsize=1)
def get_dict_missing_fallback_agent() -> Agent[
    DictMissingFallbackAgentDeps,
    DictionaryAIMissingFallbackEntryResponse | DictionaryAIMissingFallbackUnresolvedResponse,
]:
    return Agent[
        DictMissingFallbackAgentDeps,
        DictionaryAIMissingFallbackEntryResponse | DictionaryAIMissingFallbackUnresolvedResponse,
    ](
        model=None,
        output_type=DictionaryAIMissingFallbackEntryResponse | DictionaryAIMissingFallbackUnresolvedResponse,
        deps_type=DictMissingFallbackAgentDeps,
        instructions=load_agent_instructions("dict_missing_fallback"),
        name="dict_missing_fallback_agent",
        retries=2,
        output_retries=3,
        instrument=False,
    )
