from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import dict as dict_route
from app.api.routes.dict import router as dict_router
from app.services.analysis.credit_service import CreditReservation, LEDGER_ENTRY_TYPE_AI_CAPABILITY_DEDUCT
from app.services.dictionary.errors import WordNotFoundError
from app.services.dictionary_ai.service import (
    CanonicalDictionaryAvailableError,
    DictionaryAIEntryMismatchError,
    DictionaryAIRunResult,
)
from app.services.dictionary_ai.schemas import (
    DictionaryAIContextExplainResponse,
    DictionaryAIMissingFallbackEntryResponse,
    DictionaryAIMissingFallbackUnresolvedResponse,
)

USER_ID = "00000000-0000-0000-0000-000000000001"
AUTH_HEADERS = {"Authorization": "Bearer test_token"}


def _mock_auth():
    return patch(
        "app.services.auth.dependencies.validate_session",
        new_callable=AsyncMock,
        return_value=type("SessionInfo", (), {
            "user_id": UUID(USER_ID),
            "session_id": uuid4(),
        })(),
    )


class StubDictionaryService:
    async def lookup(self, request) -> dict[str, object]:
        return {
            "result_type": "entry",
            "query": request.query,
            "provider": "tecd3",
            "cached": False,
            "entry": {
                "id": 7,
                "word": request.query,
                "base_word": request.query,
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

    async def lookup_entry(self, entry_id: int) -> dict[str, object]:
        if entry_id == 404:
            raise WordNotFoundError("Entry not found")
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


class StubDictionaryAIService:
    async def run_context_explain(self, request):
        return DictionaryAIRunResult(
            response=DictionaryAIContextExplainResponse(
                query=request.query,
                summary="这里更接近“被指控”。",
                confidence="high",
            ),
            usage_data={
                "aggregate": {
                    "input_tokens": 1200,
                    "output_tokens": 200,
                    "total_tokens": 1400,
                }
            },
            model_config=None,
        )

    async def run_missing_fallback(self, request):
        if request.query == "noise":
            response = DictionaryAIMissingFallbackUnresolvedResponse(
                query=request.query,
                classification="unrecognized_noise",
                summary="这更像无效字符串。",
                confidence="low",
                reason="缺少稳定词形特征。",
            )
        else:
            response = DictionaryAIMissingFallbackEntryResponse(
                query=request.query,
                classification="slang_or_informal",
                summary="这里指不停刷负面信息流。",
                confidence="medium",
                entry={
                    "word": request.query,
                    "base_word": "doomscroll",
                    "meanings": [
                        {
                            "part_of_speech": "n./v-ing",
                            "definitions": [{"meaning": "不停刷负面内容"}],
                        }
                    ],
                },
                suggested_query=["doomscroll"],
            )
        return DictionaryAIRunResult(
            response=response,
            usage_data={
                "aggregate": {
                    "input_tokens": 800,
                    "output_tokens": 150,
                    "total_tokens": 950,
                }
            },
            model_config=None,
        )

    def normalize_query(self, query: str) -> str:
        return query.lower()


def create_client() -> TestClient:
    app = FastAPI()
    app.include_router(dict_router)
    dict_route._service = StubDictionaryService()
    dict_route._dict_ai_service = StubDictionaryAIService()
    return TestClient(app)


class TestDictAIRoute:
    def test_dict_ai_requires_auth(self) -> None:
        client = create_client()
        response = client.post(
            "/dict/ai",
            json={
                "mode": "context_explain",
                "query": "charge",
                "query_type": "word",
                "context_sentence": "He was charged with leading the group.",
                "entry_id": 7,
            },
        )

        assert response.status_code == 401

    @_mock_auth()
    @patch("app.api.routes.dict.ensure_credit_account", new_callable=AsyncMock)
    @patch("app.api.routes.dict.check_quota", new_callable=AsyncMock)
    def test_dict_ai_returns_402_when_quota_exhausted(self, mock_quota, mock_ensure, mock_auth) -> None:
        client = create_client()
        mock_quota.return_value = 0

        response = client.post(
            "/dict/ai",
            headers=AUTH_HEADERS,
            json={
                "mode": "context_explain",
                "query": "charge",
                "query_type": "word",
                "context_sentence": "He was charged with leading the group.",
                "entry_id": 7,
            },
        )

        assert response.status_code == 402

    @_mock_auth()
    @patch("app.api.routes.dict.record_ai_usage_event", new_callable=AsyncMock)
    @patch("app.api.routes.dict.reserve_points", new_callable=AsyncMock)
    @patch("app.api.routes.dict.check_quota", new_callable=AsyncMock)
    @patch("app.api.routes.dict.ensure_credit_account", new_callable=AsyncMock)
    def test_context_explain_success_deducts_points_and_audits(
        self,
        mock_ensure,
        mock_quota,
        mock_reserve,
        mock_usage,
        mock_auth,
    ) -> None:
        client = create_client()
        mock_quota.return_value = 100
        mock_reserve.return_value = CreditReservation(
            total_points=5,
            deducted_from_daily=5,
            deducted_from_bonus=0,
        )
        mock_usage.return_value = uuid4()

        response = client.post(
            "/dict/ai",
            headers=AUTH_HEADERS,
            json={
                "mode": "context_explain",
                "query": "charge",
                "query_type": "word",
                "context_sentence": "He was charged with leading the group.",
                "entry_id": 7,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["mode"] == "context_explain"
        assert data["summary"]

        reserve_kwargs = mock_reserve.await_args.kwargs
        assert reserve_kwargs["entry_type"] == LEDGER_ENTRY_TYPE_AI_CAPABILITY_DEDUCT

        usage_event = mock_usage.await_args.args[0]
        assert usage_event.capability_code == "dict_ai_lookup"
        assert usage_event.billed_points == 5

    @_mock_auth()
    @patch("app.api.routes.dict.insert_candidate_entry", new_callable=AsyncMock)
    @patch("app.api.routes.dict.record_ai_usage_event", new_callable=AsyncMock)
    @patch("app.api.routes.dict.reserve_points", new_callable=AsyncMock)
    @patch("app.api.routes.dict.check_quota", new_callable=AsyncMock)
    @patch("app.api.routes.dict.ensure_credit_account", new_callable=AsyncMock)
    def test_missing_fallback_ai_entry_persists_candidate(
        self,
        mock_ensure,
        mock_quota,
        mock_reserve,
        mock_usage,
        mock_candidate,
        mock_auth,
    ) -> None:
        client = create_client()
        mock_quota.return_value = 100
        mock_reserve.return_value = CreditReservation(
            total_points=5,
            deducted_from_daily=5,
            deducted_from_bonus=0,
        )
        mock_usage.return_value = uuid4()

        response = client.post(
            "/dict/ai",
            headers=AUTH_HEADERS,
            json={
                "mode": "missing_fallback",
                "query": "doomscrolling",
                "query_type": "word",
                "context_sentence": "She spent the whole night doomscrolling.",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["result_kind"] == "ai_entry"
        assert data["verified"] is False
        mock_candidate.assert_awaited_once()

    @_mock_auth()
    @patch("app.api.routes.dict.insert_candidate_entry", new_callable=AsyncMock)
    @patch("app.api.routes.dict.record_ai_usage_event", new_callable=AsyncMock)
    @patch("app.api.routes.dict.reserve_points", new_callable=AsyncMock)
    @patch("app.api.routes.dict.check_quota", new_callable=AsyncMock)
    @patch("app.api.routes.dict.ensure_credit_account", new_callable=AsyncMock)
    def test_missing_fallback_ai_unresolved_still_deducts_and_persists_candidate(
        self,
        mock_ensure,
        mock_quota,
        mock_reserve,
        mock_usage,
        mock_candidate,
        mock_auth,
    ) -> None:
        client = create_client()
        mock_quota.return_value = 100
        mock_reserve.return_value = CreditReservation(
            total_points=5,
            deducted_from_daily=5,
            deducted_from_bonus=0,
        )
        mock_usage.return_value = uuid4()

        response = client.post(
            "/dict/ai",
            headers=AUTH_HEADERS,
            json={
                "mode": "missing_fallback",
                "query": "noise",
                "query_type": "word",
                "context_sentence": "noise",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["result_kind"] == "ai_unresolved"
        mock_candidate.assert_awaited_once()
        usage_event = mock_usage.await_args.args[0]
        assert usage_event.billed_points == 5

    @_mock_auth()
    @patch("app.api.routes.dict.refund_reserved_points", new_callable=AsyncMock)
    @patch("app.api.routes.dict.reserve_points", new_callable=AsyncMock)
    @patch("app.api.routes.dict.check_quota", new_callable=AsyncMock)
    @patch("app.api.routes.dict.ensure_credit_account", new_callable=AsyncMock)
    def test_missing_fallback_returns_409_when_canonical_dictionary_available(
        self,
        mock_ensure,
        mock_quota,
        mock_reserve,
        mock_refund,
        mock_auth,
    ):
        class CanonicalAvailableAIService(StubDictionaryAIService):
            async def run_missing_fallback(self, request):
                raise CanonicalDictionaryAvailableError(request.query)

        client = create_client()
        dict_route._dict_ai_service = CanonicalAvailableAIService()
        mock_quota.return_value = 100
        mock_reserve.return_value = CreditReservation(
            total_points=5,
            deducted_from_daily=5,
            deducted_from_bonus=0,
        )

        response = client.post(
            "/dict/ai",
            headers=AUTH_HEADERS,
            json={
                "mode": "missing_fallback",
                "query": "knownword",
                "query_type": "word",
                "context_sentence": "knownword",
            },
        )

        assert response.status_code == 409
        mock_refund.assert_awaited_once()

    @_mock_auth()
    @patch("app.api.routes.dict.refund_reserved_points", new_callable=AsyncMock)
    @patch("app.api.routes.dict.reserve_points", new_callable=AsyncMock)
    @patch("app.api.routes.dict.check_quota", new_callable=AsyncMock)
    @patch("app.api.routes.dict.ensure_credit_account", new_callable=AsyncMock)
    def test_context_explain_returns_409_when_entry_and_query_mismatch(
        self,
        mock_ensure,
        mock_quota,
        mock_reserve,
        mock_refund,
        mock_auth,
    ):
        class MismatchAIService(StubDictionaryAIService):
            async def run_context_explain(self, request):
                raise DictionaryAIEntryMismatchError("The selected dictionary entry no longer matches the current query.")

        client = create_client()
        dict_route._dict_ai_service = MismatchAIService()
        mock_quota.return_value = 100
        mock_reserve.return_value = CreditReservation(
            total_points=5,
            deducted_from_daily=5,
            deducted_from_bonus=0,
        )

        response = client.post(
            "/dict/ai",
            headers=AUTH_HEADERS,
            json={
                "mode": "context_explain",
                "query": "charges",
                "query_type": "word",
                "context_sentence": "He was charged with leading the group.",
                "entry_id": 7,
            },
        )

        assert response.status_code == 409
        assert response.json()["error"] == "ENTRY_QUERY_MISMATCH"
        mock_refund.assert_awaited_once()

    @_mock_auth()
    @patch("app.api.routes.dict.refund_reserved_points", new_callable=AsyncMock)
    @patch("app.api.routes.dict.reserve_points", new_callable=AsyncMock)
    @patch("app.api.routes.dict.check_quota", new_callable=AsyncMock)
    @patch("app.api.routes.dict.ensure_credit_account", new_callable=AsyncMock)
    def test_context_explain_returns_404_when_entry_not_found(
        self,
        mock_ensure,
        mock_quota,
        mock_reserve,
        mock_refund,
        mock_auth,
    ):
        class MissingEntryAIService(StubDictionaryAIService):
            async def run_context_explain(self, request):
                raise WordNotFoundError("Entry not found")

        client = create_client()
        dict_route._dict_ai_service = MissingEntryAIService()
        mock_quota.return_value = 100
        mock_reserve.return_value = CreditReservation(
            total_points=5,
            deducted_from_daily=5,
            deducted_from_bonus=0,
        )

        response = client.post(
            "/dict/ai",
            headers=AUTH_HEADERS,
            json={
                "mode": "context_explain",
                "query": "charge",
                "query_type": "word",
                "context_sentence": "He was charged with leading the group.",
                "entry_id": 404,
            },
        )

        assert response.status_code == 404
        mock_refund.assert_awaited_once()

    @_mock_auth()
    def test_context_explain_missing_entry_id_returns_422(self, mock_auth) -> None:
        client = create_client()
        response = client.post(
            "/dict/ai",
            headers=AUTH_HEADERS,
            json={
                "mode": "context_explain",
                "query": "charge",
                "query_type": "word",
                "context_sentence": "He was charged with leading the group.",
            },
        )

        assert response.status_code == 422
