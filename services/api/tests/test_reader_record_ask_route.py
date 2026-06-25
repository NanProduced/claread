"""F1: Reading Record Ask backend route and service tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
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
from app.schemas.reader_ask import (
    ReaderAskActionConfirmResponse,
    ReaderAskActionConfirmResult,
    ReaderAskThreadListResponse,
    ReaderAskThreadSummary,
)

USER_ID = "00000000-0000-0000-0000-000000000001"
RECORD_ID = "00000000-0000-0000-0000-0000000000a6"
BASE_ID = "00000000-0000-0000-0000-0000000000b6"
THREAD_ID = "00000000-0000-0000-0000-0000000000c6"
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
    selected = "anthem"
    defaults: dict[str, object] = {
        "record_id": RECORD_ID,
        "base_id": BASE_ID,
        "generation": 1,
        "unit_id": "u1",
        "anchor_segment_id": "s1",
        "start_offset": 0,
        "end_offset": len(selected),
        "selected_text": selected,
        "text_hash": compute_text_range_hash(selected),
    }
    defaults.update(overrides)
    return defaults


def _stream_chunks(*chunks: str) -> AsyncIterator[str]:
    async def _gen() -> AsyncIterator[str]:
        for chunk in chunks:
            yield chunk

    return _gen()


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
    @patch(
        "app.api.routes.reader_record_ask.rr_ask_svc.send_reading_record_ask_message",
        return_value=_stream_chunks("event: message.completed\ndata: {}\n\n"),
    )
    def test_message_alias_route_streams_service_chunks(
        self,
        mock_send,
        mock_auth,
    ) -> None:
        client = create_client()

        response = client.post(
            f"/reader/records/{RECORD_ID}/ask/messages",
            headers=AUTH_HEADERS,
            json={"content": "Explain the article"},
        )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert "event: message.completed" in response.text
        mock_send.assert_called_once()

    @_mock_auth()
    @patch(
        "app.api.routes.reader_record_ask.rr_ask_svc.confirm_reading_record_ask_action",
        new_callable=AsyncMock,
    )
    def test_confirm_alias_route_uses_real_response_contract(
        self,
        mock_confirm,
        mock_auth,
    ) -> None:
        client = create_client()
        mock_confirm.return_value = ReaderAskActionConfirmResponse(
            ok=True,
            action_id="act-1",
            status="executed",
            result=ReaderAskActionConfirmResult(note_id="note-1"),
        )

        response = client.post(
            f"/reader/records/{RECORD_ID}/ask/actions/act-1/confirm",
            headers=AUTH_HEADERS,
            json={"confirmed": True},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "executed"
        assert response.json()["result"]["note_id"] == "note-1"
        mock_confirm.assert_awaited_once()


class TestReaderRecordAskService:
    @pytest.mark.asyncio
    async def test_send_message_without_anchor_validates_snapshot_and_delegates(self) -> None:
        from app.services.reader_record_ask.service import send_reading_record_ask_message

        request = MagicMock()
        request.anchor = None
        request.content = "hello"
        request.entry_action = "ask_about_this"
        request.model = None

        with (
            patch(
                "app.services.reader_record_ask.service._load_snapshot_facts_raw",
                new_callable=AsyncMock,
            ) as mock_load_snapshot_facts,
            patch(
                "app.services.reader_record_ask.service.thread_service.ensure_default_reading_record_thread",
                new_callable=AsyncMock,
                return_value={"id": THREAD_ID, "title": "Test"},
            ),
            patch(
                "app.services.reader_record_ask.service.stream_service.stream_thread_message",
                return_value=_stream_chunks("event: message.completed\ndata: {}\n\n"),
            ) as mock_stream,
        ):
            mock_load_snapshot_facts.return_value = MagicMock(
                record=MagicMock(title="Test"),
            )
            generator = send_reading_record_ask_message(
                user_id=UUID(USER_ID),
                reading_record_id=RECORD_ID,
                request=request,
            )
            chunks = [chunk async for chunk in generator]

        assert chunks == ["event: message.completed\ndata: {}\n\n"]
        assert mock_load_snapshot_facts.await_count >= 1
        mock_stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_message_anchor_record_mismatch_raises_typed_error(self) -> None:
        from app.services.reader_record_ask.service import send_reading_record_ask_message

        request = MagicMock()
        request.anchor = MagicMock(record_id=str(uuid4()))
        request.content = "hello"
        request.entry_action = "ask_about_this"
        request.model = None

        generator = send_reading_record_ask_message(
            user_id=UUID(USER_ID),
            reading_record_id=RECORD_ID,
            request=request,
        )

        with pytest.raises(HTTPException) as excinfo:
            await anext(generator)

        assert excinfo.value.status_code == 400
        assert excinfo.value.detail["code"] == ANCHOR_RECORD_ID_MISMATCH

    @pytest.mark.asyncio
    async def test_send_message_anchor_gate_failure_raises_typed_error(self) -> None:
        from app.services.reader_record_ask.service import send_reading_record_ask_message

        request = MagicMock()
        request.anchor = MagicMock(
            record_id=RECORD_ID,
            base_id=BASE_ID,
            generation=1,
            unit_id="u99",
            anchor_segment_id="s1",
            start_offset=0,
            end_offset=2,
            selected_text="hi",
            text_hash=compute_text_range_hash("hi"),
        )
        request.content = "hello"
        request.entry_action = "ask_about_this"
        request.model = None

        with patch(
            "app.services.reader_record_ask.service._load_validated_anchor_raw",
            new_callable=AsyncMock,
            side_effect=AnchorValidationError("unit_not_found", "unit does not exist"),
        ):
            generator = send_reading_record_ask_message(
                user_id=UUID(USER_ID),
                reading_record_id=RECORD_ID,
                request=request,
            )
            with pytest.raises(HTTPException) as excinfo:
                await anext(generator)

        assert excinfo.value.status_code == 400
        assert excinfo.value.detail["code"] == "unit_not_found"

    @pytest.mark.asyncio
    async def test_send_message_snapshot_not_found_raises_typed_error(self) -> None:
        from app.services.reader_record_ask.service import send_reading_record_ask_message

        request = MagicMock()
        request.anchor = None
        request.content = "hello"
        request.entry_action = "ask_about_this"
        request.model = None

        with patch(
            "app.services.reader_record_ask.service._load_snapshot_facts_raw",
            new_callable=AsyncMock,
            side_effect=LookupError("reading record not visible"),
        ):
            generator = send_reading_record_ask_message(
                user_id=UUID(USER_ID),
                reading_record_id=RECORD_ID,
                request=request,
            )
            with pytest.raises(HTTPException) as excinfo:
                await anext(generator)

        assert excinfo.value.status_code == 400
        assert excinfo.value.detail["code"] == READING_RECORD_NOT_FOUND

    @pytest.mark.asyncio
    async def test_confirm_action_uses_thread_scoped_runtime(self) -> None:
        from app.services.reader_record_ask.service import confirm_reading_record_ask_action

        with (
            patch(
                "app.services.reader_record_ask.service.thread_service.list_reading_record_threads",
                new_callable=AsyncMock,
                return_value=ReaderAskThreadListResponse(
                    items=[
                        ReaderAskThreadSummary(
                            id=THREAD_ID,
                            record_id=RECORD_ID,
                            title="Test",
                            is_default=True,
                            selected_model=None,
                            archived_at=None,
                            created_at="2026-06-25T00:00:00Z",
                            updated_at="2026-06-25T00:00:00Z",
                            last_message_at=None,
                        )
                    ]
                ),
            ),
            patch(
                "app.services.reader_record_ask.service.action_service.confirm_action",
                new_callable=AsyncMock,
                return_value=ReaderAskActionConfirmResponse(
                    ok=True,
                    action_id="act-1",
                    status="executed",
                    result=ReaderAskActionConfirmResult(note_id="note-1"),
                ),
            ) as mock_confirm,
        ):
            result = await confirm_reading_record_ask_action(
                user_id=UUID(USER_ID),
                reading_record_id=RECORD_ID,
                action_id="act-1",
                request=MagicMock(confirmed=True),
            )

        assert result.status == "executed"
        assert result.result.note_id == "note-1"
        mock_confirm.assert_awaited_once()
