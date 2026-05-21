from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes.reader_ask import router as reader_ask_router
from app.schemas.reader_ask import (
    ReaderAskActionConfirmResponse,
    ReaderAskDeleteSupplementResponse,
    ReaderAskContextRecordSearchResponse,
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


def _thread_detail() -> ReaderAskThreadDetail:
    return ReaderAskThreadDetail(
        **_thread_summary().model_dump(mode="json"),
        messages=[],
    )


def _stream_body(**overrides):
    body = {
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
    body.update(overrides)
    return body


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
    @patch("app.api.routes.reader_ask.ask_svc.list_context_records", new_callable=AsyncMock)
    def test_list_context_records(self, mock_list_context_records, mock_auth) -> None:
        client = create_client()
        mock_list_context_records.return_value = ReaderAskContextRecordSearchResponse(
            items=[{"record_id": "record-2", "title": "Climate Policy", "updated_at": "2026-05-20T00:00:00Z"}]
        )

        response = client.get(
            f"/reader-ask/context-records?query=climate&exclude_record_id={RECORD_ID}",
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        assert response.json()["items"][0]["record_id"] == "record-2"

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
        mock_get_thread_detail.return_value = _thread_detail()

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
            yield (
                "event: message.completed\ndata: "
                "{\"content_md\": \"hello\", \"context_plan\": {}, \"resolved_context_input\": {}, "
                "\"run_info\": {\"turn_id\": \"turn-1\", \"run_id\": \"run-1\", \"run_attempt\": 1}, "
                "\"evidence\": [], \"trace_summary\": {\"planner_mode\": \"direct_answer\", "
                "\"reference_resolution_status\": \"not_needed\", \"working_set_mode\": \"anchor_local\", \"cross_record_context_allowed\": false, "
                "\"cross_record_context_used\": false, \"used_known_reference_resolution\": false, "
                "\"used_external_record_context\": false, \"used_structured_asset_lookup\": false, "
                "\"used_hitp_disambiguation\": false, \"used_external_asset_context\": false, "
                "\"used_hitp_asset_disambiguation\": false, \"supplement_generation_used\": false, "
                "\"supplement_persisted_count\": 0, \"supplement_deleted_count\": 0, "
                "\"tool_steps\": [], \"notes\": []}}\n\n"
            )

        with patch("app.api.routes.reader_ask.ask_svc.stream_thread_message", new=fake_stream):
            response = client.post(
                f"/reader-ask/threads/{THREAD_ID}/messages/stream",
                headers=AUTH_HEADERS,
                json=_stream_body(),
            )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert "event: thread.ready" in response.text
        assert "event: message.delta" in response.text
        assert "\"delta\": \"hello\"" in response.text
        assert "\"trace_summary\"" in response.text

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
                json=_stream_body(),
            )

        assert response.status_code == 200
        assert "event: error" in response.text
        assert "\"code\": \"READER_ASK_FAILED\"" in response.text

    @_mock_auth()
    def test_stream_message_rejects_legacy_fields(self, mock_auth) -> None:
        client = create_client()

        response = client.post(
            f"/reader-ask/threads/{THREAD_ID}/messages/stream",
            headers=AUTH_HEADERS,
            json=_stream_body(task_mode="explain"),
        )

        assert response.status_code == 422

    @_mock_auth()
    def test_stream_message_rejects_extra_page_identity_fields(self, mock_auth) -> None:
        client = create_client()

        response = client.post(
            f"/reader-ask/threads/{THREAD_ID}/messages/stream",
            headers=AUTH_HEADERS,
            json=_stream_body(
                page_identity={
                    **_stream_body()["page_identity"],
                    "workflow_version": "3.0.0",
                },
            ),
        )

        assert response.status_code == 422

    @_mock_auth()
    @patch("app.api.routes.reader_ask.ask_svc.reset_thread", new_callable=AsyncMock)
    def test_reset_thread(self, mock_reset_thread, mock_auth) -> None:
        client = create_client()
        mock_reset_thread.return_value = _thread_detail()

        response = client.post(
            f"/reader-ask/threads/{THREAD_ID}/reset",
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        assert response.json()["id"] == THREAD_ID

    @_mock_auth()
    @patch("app.api.routes.reader_ask.ask_svc.confirm_action", new_callable=AsyncMock)
    def test_confirm_action(self, mock_confirm_action, mock_auth) -> None:
        client = create_client()
        mock_confirm_action.return_value = ReaderAskActionConfirmResponse(
            ok=True,
            action_id="act-1",
            status="executed",
            result={
                "record_id": RECORD_ID,
                "supplement_projection": {
                    "id": "entry-1",
                    "sentence_id": "s1",
                    "entry_type": "grammar_note",
                },
                "persisted_supplement": {
                    "supplement_id": "supp-1",
                    "supplement_type": "grammar_note",
                    "lifecycle_status": "persisted",
                    "record_id": RECORD_ID,
                    "record_title": "Ask Claread",
                    "target_key": "record:r1:sentence:s1",
                    "sentence_id": "s1",
                    "paragraph_id": "p1",
                    "title": "语法旁注",
                    "content": "这里用了让步从句。",
                    "source_kind": "assistant_supplement",
                    "schema_version": "1.0",
                    "created_from_turn_run_id": "run-1",
                    "created_at": "2026-05-20T00:00:00Z",
                },
            },
        )

        response = client.post(
            f"/reader-ask/threads/{THREAD_ID}/actions/act-1/confirm",
            headers=AUTH_HEADERS,
            json={"confirmed": True},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "executed"
        assert response.json()["result"]["persisted_supplement"]["lifecycle_status"] == "persisted"

    @_mock_auth()
    @patch("app.api.routes.reader_ask.ask_svc.delete_supplement", new_callable=AsyncMock)
    def test_delete_supplement(self, mock_delete_supplement, mock_auth) -> None:
        client = create_client()
        mock_delete_supplement.return_value = ReaderAskDeleteSupplementResponse(
            deleted=True,
            supplement_id="supp-1",
            record_id=RECORD_ID,
            target_key="record:r1:sentence:s1",
            lifecycle_status="deleted",
            persisted_supplement={
                "supplement_id": "supp-1",
                "supplement_type": "grammar_note",
                "lifecycle_status": "deleted",
                "record_id": RECORD_ID,
                "record_title": "Ask Claread",
                "target_key": "record:r1:sentence:s1",
                "sentence_id": "s1",
                "paragraph_id": "p1",
                "title": "语法旁注",
                "content": "这里用了让步从句。",
                "source_kind": "assistant_supplement",
                "schema_version": "1.0",
                "created_from_turn_run_id": "run-1",
                "created_at": "2026-05-20T00:00:00Z",
            },
        )

        response = client.delete(
            "/reader-ask/supplements/30000000-0000-0000-0000-000000000001",
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        assert response.json()["deleted"] is True
        assert response.json()["lifecycle_status"] == "deleted"

    @_mock_auth()
    def test_retry_stream_message_returns_sse_events(self, mock_auth) -> None:
        client = create_client()

        async def fake_stream(user_id: UUID, thread_id: UUID, message_id: UUID):
            del user_id, thread_id, message_id
            yield "event: message.started\ndata: {\"message_id\": \"200\"}\n\n"
            yield "event: message.delta\ndata: {\"delta\": \"retry\"}\n\n"
            yield (
                "event: message.completed\ndata: "
                "{\"content_md\": \"retry\", \"context_plan\": {}, \"resolved_context_input\": {}, "
                "\"run_info\": {\"turn_id\": \"turn-1\", \"run_id\": \"run-2\", \"run_attempt\": 2}, "
                "\"evidence\": [], \"trace_summary\": {\"planner_mode\": \"direct_answer\", "
                "\"reference_resolution_status\": \"not_needed\", \"working_set_mode\": \"anchor_local\", \"cross_record_context_allowed\": false, "
                "\"cross_record_context_used\": false, \"used_known_reference_resolution\": false, "
                "\"used_external_record_context\": false, \"used_structured_asset_lookup\": false, "
                "\"used_hitp_disambiguation\": false, \"used_external_asset_context\": false, "
                "\"used_hitp_asset_disambiguation\": false, \"supplement_generation_used\": false, "
                "\"supplement_persisted_count\": 0, \"supplement_deleted_count\": 0, "
                "\"tool_steps\": [], \"notes\": []}}\n\n"
            )

        with patch("app.api.routes.reader_ask.ask_svc.retry_thread_message", new=fake_stream):
            response = client.post(
                f"/reader-ask/threads/{THREAD_ID}/messages/30000000-0000-0000-0000-000000000001/retry/stream",
                headers=AUTH_HEADERS,
            )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert "event: message.delta" in response.text
        assert "\"run_attempt\": 2" in response.text
