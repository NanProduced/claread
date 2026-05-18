from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes.reader_ask import router as reader_ask_router
from app.schemas.reader_ask import (
    ReaderAskActionConfirmResponse,
    ReaderAskThreadDetail,
    ReaderAskThreadListResponse,
    ReaderAskThreadSummary,
)

USER_ID = "00000000-0000-0000-0000-000000000001"
THREAD_ID = "10000000-0000-0000-0000-000000000001"
RECORD_ID = "20000000-0000-0000-0000-000000000001"
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


def _thread_summary() -> ReaderAskThreadSummary:
    return ReaderAskThreadSummary(
        id=THREAD_ID,
        record_id=RECORD_ID,
        title="Ask Claread",
        is_default=True,
        archived_at=None,
        created_at="2026-05-18T10:00:00+00:00",
        updated_at="2026-05-18T10:00:00+00:00",
        last_message_at=None,
    )


def create_client() -> TestClient:
    app = FastAPI()
    app.include_router(reader_ask_router)
    return TestClient(app)


class TestReaderAskRoute:
    def test_threads_require_auth(self) -> None:
        client = create_client()

        response = client.get(f"/reader-ask/threads?record_id={RECORD_ID}")

        assert response.status_code == 401

    @_mock_auth()
    @patch("app.api.routes.reader_ask.ask_svc.list_threads", new_callable=AsyncMock)
    def test_list_threads(self, mock_list_threads, mock_auth) -> None:
        client = create_client()
        mock_list_threads.return_value = ReaderAskThreadListResponse(items=[_thread_summary()])

        response = client.get(f"/reader-ask/threads?record_id={RECORD_ID}", headers=AUTH_HEADERS)

        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["record_id"] == RECORD_ID

    @_mock_auth()
    @patch("app.api.routes.reader_ask.ask_svc.create_thread", new_callable=AsyncMock)
    def test_create_default_thread(self, mock_create_thread, mock_auth) -> None:
        client = create_client()
        mock_create_thread.return_value = _thread_summary()

        response = client.post(
            "/reader-ask/threads",
            headers=AUTH_HEADERS,
            json={"record_id": RECORD_ID, "mode": "default"},
        )

        assert response.status_code == 200
        assert response.json()["is_default"] is True

    @_mock_auth()
    @patch("app.api.routes.reader_ask.ask_svc.get_thread_detail", new_callable=AsyncMock)
    def test_get_thread_detail(self, mock_get_thread_detail, mock_auth) -> None:
        client = create_client()
        mock_get_thread_detail.return_value = ReaderAskThreadDetail(
            **_thread_summary().model_dump(mode="json"),
            messages=[],
        )

        response = client.get(f"/reader-ask/threads/{THREAD_ID}", headers=AUTH_HEADERS)

        assert response.status_code == 200
        assert response.json()["id"] == THREAD_ID

    @_mock_auth()
    def test_stream_message_returns_sse_events(self, mock_auth) -> None:
        client = create_client()

        async def fake_stream(user_id: UUID, thread_id: UUID, body):
            del user_id, thread_id, body
            yield "event: thread.ready\ndata: {\"thread_id\": \"100\"}\n\n"
            yield "event: message.started\ndata: {\"message_id\": \"200\"}\n\n"
            yield "event: message.delta\ndata: {\"delta\": \"hello\"}\n\n"
            yield "event: message.completed\ndata: {\"content_md\": \"hello\"}\n\n"

        with patch("app.api.routes.reader_ask.ask_svc.stream_thread_message", new=fake_stream):
            response = client.post(
                f"/reader-ask/threads/{THREAD_ID}/messages/stream",
                headers=AUTH_HEADERS,
                json={"content": "Explain this sentence"},
            )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert "event: thread.ready" in response.text
        assert "event: message.delta" in response.text
        assert "\"delta\": \"hello\"" in response.text

    @_mock_auth()
    def test_stream_message_wraps_unexpected_errors_as_sse_error(self, mock_auth) -> None:
        client = create_client()

        async def fake_stream(user_id: UUID, thread_id: UUID, body):
            del user_id, thread_id, body
            raise RuntimeError("boom")
            yield ""  # pragma: no cover

        with patch("app.api.routes.reader_ask.ask_svc.stream_thread_message", new=fake_stream):
            response = client.post(
                f"/reader-ask/threads/{THREAD_ID}/messages/stream",
                headers=AUTH_HEADERS,
                json={"content": "Explain this sentence"},
            )

        assert response.status_code == 200
        assert "event: error" in response.text
        assert "\"code\": \"READER_ASK_FAILED\"" in response.text

    @_mock_auth()
    @patch("app.api.routes.reader_ask.ask_svc.confirm_action", new_callable=AsyncMock)
    def test_confirm_action(self, mock_confirm_action, mock_auth) -> None:
        client = create_client()
        mock_confirm_action.return_value = ReaderAskActionConfirmResponse(
            ok=True,
            action_id="act-1",
            status="executed",
            result={"favorite_id": "fav-1"},
        )

        response = client.post(
            f"/reader-ask/threads/{THREAD_ID}/actions/act-1/confirm",
            headers=AUTH_HEADERS,
            json={"confirmed": True},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "executed"
