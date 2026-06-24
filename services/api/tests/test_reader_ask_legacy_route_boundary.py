from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes.reader_ask import router as reader_ask_router

USER_ID = "00000000-0000-0000-0000-000000000001"
THREAD_ID = "10000000-0000-0000-0000-000000000001"
RECORD_ID = "20000000-0000-0000-0000-000000000001"
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


def _stream_body() -> dict[str, object]:
    return {
        "content": "Explain this sentence",
        "page_identity": {
            "record_id": RECORD_ID,
            "title": "Ask Claread",
            "surface": "reader",
            "source": "reader_2_0",
            "available_context_capabilities": ["record_context"],
            "has_article_overview": True,
            "has_sentence_entries": True,
            "has_annotations": True,
            "has_reader_notes": True,
        },
        "attachments": [],
        "entry_action": "ask_about_this",
    }


def create_client() -> TestClient:
    app = FastAPI()
    app.include_router(reader_ask_router)
    return TestClient(app)


class TestReaderAskLegacyRouteBoundary:
    @_mock_auth()
    def test_stream_message_rejects_reading_record_anchor_field(
        self,
        mock_auth,
    ) -> None:
        client = create_client()
        body = _stream_body()
        body["anchor"] = {
            "record_id": str(uuid4()),
            "base_id": str(uuid4()),
            "generation": 1,
            "unit_id": "unit-1",
            "anchor_segment_id": "segment-1",
            "start_offset": 0,
            "end_offset": 5,
            "selected_text": "hello",
            "text_hash": "4f9f2cab",
        }

        response = client.post(
            f"/reader-ask/threads/{THREAD_ID}/messages/stream",
            headers=AUTH_HEADERS,
            json=body,
        )

        assert response.status_code == 422
