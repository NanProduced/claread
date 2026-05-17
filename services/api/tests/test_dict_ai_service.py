from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.dictionary_ai.schemas import (
    DictionaryAIContextExplainRequest,
    DictionaryAIContextExplainResponse,
)
from app.services.dictionary_ai.service import (
    DictionaryAIEntryMismatchError,
    DictionaryAIService,
)


class StubDictionaryService:
    async def lookup_entry(self, entry_id: int) -> dict[str, object]:
        return {
            "result_type": "entry",
            "query": "charge",
            "provider": "tecd3",
            "cached": False,
            "entry": {
                "id": entry_id,
                "word": "charge",
                "base_word": "charge",
                "homograph_no": None,
                "phonetic": "/test/",
                "meanings": [],
                "examples": [],
                "phrases": [],
                "entry_kind": "entry",
                "exchange": [],
                "tags": [],
            },
        }

    def normalize_query(self, query: str) -> str:
        return query.strip().lower()


@pytest.mark.asyncio
async def test_run_context_explain_rejects_query_entry_mismatch() -> None:
    service = DictionaryAIService(dictionary_service=StubDictionaryService())
    request = DictionaryAIContextExplainRequest(
        query="charges",
        query_type="word",
        context_sentence="He was charged with leading the group.",
        entry_id=7,
    )

    with pytest.raises(DictionaryAIEntryMismatchError):
        await service.run_context_explain(request)


@pytest.mark.asyncio
async def test_run_context_explain_accepts_matching_query() -> None:
    service = DictionaryAIService(dictionary_service=StubDictionaryService())
    request = DictionaryAIContextExplainRequest(
        query="charge",
        query_type="word",
        context_sentence="He was charged with leading the group.",
        entry_id=7,
    )

    result = SimpleNamespace(
        output=DictionaryAIContextExplainResponse(
            query="charge",
            summary="这里更接近“被指控”。",
        ),
        _resolved_model_config=None,
    )

    with (
        patch(
            "app.services.dictionary_ai.service.run_agent_with_route",
            new=AsyncMock(return_value=result),
        ),
        patch(
            "app.services.dictionary_ai.service.extract_run_usage",
            return_value={"aggregate": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30}},
        ),
    ):
        run_result = await service.run_context_explain(request)

    assert run_result.response.summary == "这里更接近“被指控”。"
    assert run_result.usage_data == {
        "aggregate": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30}
    }
