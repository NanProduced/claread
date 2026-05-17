from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.agents.dict_context_explain_agent import (
    DictContextExplainAgentDeps,
    build_dict_context_explain_prompt,
    get_dict_context_explain_agent,
)
from app.agents.dict_missing_fallback_agent import (
    DictMissingFallbackAgentDeps,
    build_dict_missing_fallback_prompt,
    get_dict_missing_fallback_agent,
)
from app.llm.agent_runner import extract_run_usage, run_agent_with_route
from app.llm.routes import MODEL_ROUTE_DICT_AI
from app.llm.types import ResolvedModelConfig
from app.services.dictionary import DictionaryService, get_service as get_dictionary_service
from app.services.dictionary.errors import WordNotFoundError
from app.services.dictionary.schemas import DictionaryLookupRequest
from app.services.dictionary_ai.schemas import (
    DictionaryAIContextExplainRequest,
    DictionaryAIMissingFallbackRequest,
    DictionaryAIResponse,
)


class CanonicalDictionaryAvailableError(RuntimeError):
    """Raised when missing_fallback should not proceed because the canonical dictionary can answer."""


class DictionaryAIEntryMismatchError(RuntimeError):
    """Raised when a context_explain query does not match the selected canonical entry."""


@dataclass(slots=True)
class DictionaryAIRunResult:
    response: DictionaryAIResponse
    usage_data: dict[str, object] | None
    model_config: ResolvedModelConfig | None


class DictionaryAIService:
    def __init__(self, dictionary_service: DictionaryService | None = None) -> None:
        self._dictionary_service = dictionary_service or get_dictionary_service()

    async def run_context_explain(
        self,
        request: DictionaryAIContextExplainRequest,
    ) -> DictionaryAIRunResult:
        entry_result = await self._dictionary_service.lookup_entry(request.entry_id)
        entry_payload = entry_result["entry"]
        self._validate_context_explain_target(request.query, entry_result)

        deps = DictContextExplainAgentDeps(
            query=request.query,
            query_type=request.query_type,
            context_sentence=request.context_sentence,
            occurrence=request.occurrence,
            entry_payload=entry_payload,
        )
        result = await run_agent_with_route(
            agent=get_dict_context_explain_agent(),
            prompt=build_dict_context_explain_prompt(deps),
            deps=deps,
            route=MODEL_ROUTE_DICT_AI,
        )
        response = result.output if hasattr(result, "output") else result
        return DictionaryAIRunResult(
            response=response,
            usage_data=extract_run_usage(result),
            model_config=getattr(result, "_resolved_model_config", None),
        )

    async def run_missing_fallback(
        self,
        request: DictionaryAIMissingFallbackRequest,
    ) -> DictionaryAIRunResult:
        lookup_request = DictionaryLookupRequest(
            query=request.query,
            query_type=request.query_type,
            context_sentence=request.context_sentence,
            occurrence=request.occurrence,
        )
        try:
            await self._dictionary_service.lookup(lookup_request)
        except WordNotFoundError:
            pass
        else:
            raise CanonicalDictionaryAvailableError(request.query)

        deps = DictMissingFallbackAgentDeps(
            query=request.query,
            query_type=request.query_type,
            context_sentence=request.context_sentence,
            occurrence=request.occurrence,
        )
        result = await run_agent_with_route(
            agent=get_dict_missing_fallback_agent(),
            prompt=build_dict_missing_fallback_prompt(deps),
            deps=deps,
            route=MODEL_ROUTE_DICT_AI,
        )
        response = result.output if hasattr(result, "output") else result
        return DictionaryAIRunResult(
            response=response,
            usage_data=extract_run_usage(result),
            model_config=getattr(result, "_resolved_model_config", None),
        )

    def normalize_query(self, query: str) -> str:
        return self._dictionary_service.normalize_query(query)

    def _validate_context_explain_target(
        self,
        query: str,
        entry_result: dict[str, Any],
    ) -> None:
        entry_payload = entry_result.get("entry")
        if not isinstance(entry_payload, dict):
            raise DictionaryAIEntryMismatchError("Dictionary entry payload is invalid.")

        normalized_query = self.normalize_query(query)
        candidate_forms = {
            self.normalize_query(str(value))
            for value in (
                entry_result.get("query"),
                entry_payload.get("word"),
                entry_payload.get("base_word"),
            )
            if value
        }
        if normalized_query not in candidate_forms:
            raise DictionaryAIEntryMismatchError(
                "The selected dictionary entry no longer matches the current query."
            )


_service: DictionaryAIService | None = None


def get_service() -> DictionaryAIService:
    global _service
    if _service is None:
        _service = DictionaryAIService()
    return _service
