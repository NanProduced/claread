"""D6-A6: Reading Record Ask route / contract spike tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.routes.reader_record_ask import router as reader_record_ask_router
from app.contracts.anchor_validation import (
    ANCHOR_RECORD_ID_MISMATCH,
    READING_RECORD_NOT_FOUND,
    AnchorValidationError,
)
from app.contracts.annotation import compute_text_range_hash
from app.schemas.reader_ask import ReaderRecordAskPendingResponse

USER_ID = "00000000-0000-0000-0000-000000000001"
RECORD_ID = "00000000-0000-0000-0000-0000000000a6"
BASE_ID = "00000000-0000-0000-0000-0000000000b6"
AUTH_HEADERS = {"Authorization": "Bearer test_token"}


def _mock_auth():
    return patch(
        "app.services.auth.dependencies.validate_session",
        new_callable=AsyncMock,
        return_value=type(
            "SessionInfo",
            (),
            {
                "user_id": UUID(USER_ID),
                "session_id": uuid4(),
            },
        )(),
    )


def _anchor(**overrides: object) -> dict[str, object]:
    selected = "🧠"
    defaults: dict[str, object] = {
        "record_id": RECORD_ID,
        "base_id": BASE_ID,
        "generation": 1,
        "unit_id": "u1",
        "anchor_segment_id": "s1",
        "start_offset": 6,
        "end_offset": 8,
        "selected_text": selected,
        "text_hash": compute_text_range_hash(selected),
    }
    defaults.update(overrides)
    return defaults


def _mock_db_connect():
    mock_conn = AsyncMock()
    mock_pool = MagicMock()
    mock_pool.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.__aexit__ = AsyncMock(return_value=False)
    return patch(
        "app.services.reader_record_ask.service.db_connect.acquire_connection",
        return_value=mock_pool,
    )


def create_client() -> TestClient:
    app = FastAPI()
    app.include_router(reader_record_ask_router)
    return TestClient(app)


class TestReaderRecordAskRoute:
    def test_messages_require_auth(self) -> None:
        client = create_client()

        response = client.post(
            f"/reader/records/{RECORD_ID}/ask/messages",
            json={"content": "hello"},
        )

        assert response.status_code == 401

    @_mock_auth()
    def test_messages_reject_unknown_fields(self, mock_auth) -> None:
        client = create_client()

        response = client.post(
            f"/reader/records/{RECORD_ID}/ask/messages",
            headers=AUTH_HEADERS,
            json={"content": "hello", "task_mode": "explain"},
        )

        assert response.status_code == 422

    @_mock_auth()
    @_mock_db_connect()
    @patch(
        "app.services.reader_record_ask.service.ReaderOrchestrationRepository.load_snapshot_facts",
        new_callable=AsyncMock,
    )
    def test_messages_without_anchor_still_validate_reading_record_snapshot(
        self,
        mock_load_snapshot_facts,
        mock_db_connect,
        mock_auth,
    ) -> None:
        client = create_client()
        mock_load_snapshot_facts.return_value = MagicMock()

        response = client.post(
            f"/reader/records/{RECORD_ID}/ask/messages",
            headers=AUTH_HEADERS,
            json={"content": "Explain the article"},
        )

        assert response.status_code == 409
        data = response.json()
        assert data["code"] == "reader_record_ask_execution_pending"
        assert data["reading_record_id"] == RECORD_ID
        mock_load_snapshot_facts.assert_awaited_once()

    @_mock_auth()
    @_mock_db_connect()
    @patch(
        "app.services.reader_record_ask.service.load_validated_reading_record_anchor",
        new_callable=AsyncMock,
    )
    def test_valid_anchor_returns_typed_pending(
        self,
        mock_load_anchor,
        mock_db_connect,
        mock_auth,
    ) -> None:
        client = create_client()
        mock_load_anchor.return_value = MagicMock()

        response = client.post(
            f"/reader/records/{RECORD_ID}/ask/messages",
            headers=AUTH_HEADERS,
            json={"content": "Explain this", "anchor": _anchor()},
        )

        assert response.status_code == 409
        data = response.json()
        assert data["status"] == "pending"
        assert data["code"] == "reader_record_ask_execution_pending"
        assert data["reading_record_id"] == RECORD_ID
        mock_load_anchor.assert_awaited_once()

    @_mock_auth()
    def test_anchor_record_id_mismatch_returns_stable_error(self, mock_auth) -> None:
        client = create_client()

        response = client.post(
            f"/reader/records/{RECORD_ID}/ask/messages",
            headers=AUTH_HEADERS,
            json={"content": "Explain this", "anchor": _anchor(record_id=str(uuid4()))},
        )

        assert response.status_code == 400
        detail = response.json()["detail"]
        assert detail["code"] == ANCHOR_RECORD_ID_MISMATCH
        assert detail["field"] == "anchor.record_id"

    @_mock_auth()
    @_mock_db_connect()
    @patch(
        "app.services.reader_record_ask.service.load_validated_reading_record_anchor",
        new_callable=AsyncMock,
    )
    def test_legacy_analysis_record_id_not_accepted_as_record_id(
        self,
        mock_load_anchor,
        mock_db_connect,
        mock_auth,
    ) -> None:
        client = create_client()
        legacy_analysis_record_id = str(uuid4())
        mock_load_anchor.side_effect = AnchorValidationError(
            READING_RECORD_NOT_FOUND,
            "reading record not found",
        )

        response = client.post(
            f"/reader/records/{legacy_analysis_record_id}/ask/messages",
            headers=AUTH_HEADERS,
            json={
                "content": "Explain this",
                "anchor": _anchor(record_id=legacy_analysis_record_id),
            },
        )

        assert response.status_code == 400
        detail = response.json()["detail"]
        assert detail["code"] == READING_RECORD_NOT_FOUND

    @_mock_auth()
    @_mock_db_connect()
    @patch(
        "app.services.reader_record_ask.service.load_validated_reading_record_anchor",
        new_callable=AsyncMock,
    )
    def test_anchor_gate_failure_returns_typed_error(
        self,
        mock_load_anchor,
        mock_db_connect,
        mock_auth,
    ) -> None:
        client = create_client()
        mock_load_anchor.side_effect = AnchorValidationError(
            "unit_not_found",
            "unit does not exist",
        )

        response = client.post(
            f"/reader/records/{RECORD_ID}/ask/messages",
            headers=AUTH_HEADERS,
            json={"content": "Explain this", "anchor": _anchor(unit_id="u99")},
        )

        assert response.status_code == 400
        detail = response.json()["detail"]
        assert detail["code"] == "unit_not_found"

    @_mock_auth()
    @patch(
        "app.services.reader_ask.service.confirm_action",
        new_callable=AsyncMock,
    )
    def test_confirm_returns_pending_without_touching_legacy_tables(
        self,
        mock_legacy_confirm,
        mock_auth,
    ) -> None:
        client = create_client()

        response = client.post(
            f"/reader/records/{RECORD_ID}/ask/actions/act-1/confirm",
            headers=AUTH_HEADERS,
            json={"confirmed": True},
        )

        assert response.status_code == 409
        data = response.json()
        assert data["status"] == "pending"
        assert data["code"] == "reader_record_ask_confirm_pending"
        assert data["reading_record_id"] == RECORD_ID
        assert data["action_id"] == "act-1"
        mock_legacy_confirm.assert_not_awaited()


class TestReaderRecordAskService:
    @pytest.mark.asyncio
    async def test_send_message_without_anchor_pending_after_snapshot_validation(self) -> None:
        from app.services.reader_record_ask.service import (
            send_reading_record_ask_message,
        )

        request = MagicMock()
        request.anchor = None
        request.content = "hello"
        request.entry_action = "ask_about_this"
        request.model = None

        with (
            _mock_db_connect(),
            patch(
                "app.services.reader_record_ask.service.ReaderOrchestrationRepository.load_snapshot_facts",
                new_callable=AsyncMock,
            ) as mock_load_snapshot_facts,
        ):
            mock_load_snapshot_facts.return_value = MagicMock()
            result = await send_reading_record_ask_message(
                user_id=UUID(USER_ID),
                reading_record_id=RECORD_ID,
                request=request,
            )

        assert isinstance(result, ReaderRecordAskPendingResponse)
        assert result.code == "reader_record_ask_execution_pending"

    @pytest.mark.asyncio
    async def test_send_message_without_anchor_returns_not_found(self) -> None:
        from app.services.reader_record_ask.service import (
            send_reading_record_ask_message,
        )

        request = MagicMock()
        request.anchor = None
        request.content = "hello"
        request.entry_action = "ask_about_this"
        request.model = None

        with (
            _mock_db_connect(),
            patch(
                "app.services.reader_record_ask.service.ReaderOrchestrationRepository.load_snapshot_facts",
                new_callable=AsyncMock,
            ) as mock_load_snapshot_facts,
        ):
            mock_load_snapshot_facts.side_effect = LookupError(
                "reading record not visible",
            )
            with pytest.raises(HTTPException) as excinfo:
                await send_reading_record_ask_message(
                    user_id=UUID(USER_ID),
                    reading_record_id=RECORD_ID,
                    request=request,
                )

        assert excinfo.value.status_code == 400
        assert excinfo.value.detail["code"] == READING_RECORD_NOT_FOUND
        assert excinfo.value.detail["field"] == "reading_record_id"

    @pytest.mark.asyncio
    async def test_confirm_action_pending(self) -> None:
        from app.services.reader_record_ask.service import (
            confirm_reading_record_ask_action,
        )

        request = MagicMock()
        request.confirmed = True

        result = await confirm_reading_record_ask_action(
            user_id=UUID(USER_ID),
            reading_record_id=RECORD_ID,
            action_id="act-1",
            request=request,
        )

        assert isinstance(result, ReaderRecordAskPendingResponse)
        assert result.code == "reader_record_ask_confirm_pending"
