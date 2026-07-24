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
    @patch(
        "app.api.routes.reader_record_ask.rr_ask_svc.prepare_reading_record_ask_message",
        new_callable=AsyncMock,
        return_value=(UUID(RECORD_ID), UUID(THREAD_ID), object()),
    )
    def test_message_alias_route_streams_service_chunks(
        self,
        mock_prepare,
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
        mock_prepare.assert_awaited_once()
        mock_send.assert_called_once()
        assert mock_send.call_args.kwargs["prepared"] == mock_prepare.return_value

    @_mock_auth()
    @patch(
        "app.api.routes.reader_record_ask.rr_ask_svc.prepare_reading_record_ask_message",
        new_callable=AsyncMock,
        side_effect=HTTPException(
            status_code=422,
            detail="Unknown Ask Claread model option: glm-standard",
        ),
    )
    @patch(
        "app.api.routes.reader_record_ask.rr_ask_svc.send_reading_record_ask_message",
        return_value=_stream_chunks("event: message.completed\ndata: {}\n\n"),
    )
    def test_explicit_deleted_glm_key_returns_typed_422(
        self,
        mock_send,
        mock_prepare,
        mock_auth,
    ) -> None:
        client = create_client()

        response = client.post(
            f"/reader/records/{RECORD_ID}/ask/messages",
            headers=AUTH_HEADERS,
            json={"content": "hello", "model": "glm-standard"},
        )

        assert response.status_code == 422
        assert "Unknown Ask Claread model option" in str(response.json()["detail"])
        mock_prepare.assert_awaited_once()
        mock_send.assert_not_called()

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

    # ------------------------------------------------------------------
    # H2: production-mode SSE error frame must use the fixed Chinese
    # fallback message and must not leak the raw exception text.
    # ------------------------------------------------------------------

    @_mock_auth()
    @patch(
        "app.api.routes.reader_record_ask.get_settings",
    )
    @patch(
        "app.api.routes.reader_record_ask.rr_ask_svc.prepare_reading_record_ask_message",
        new_callable=AsyncMock,
        return_value=(UUID(RECORD_ID), UUID(THREAD_ID), None),
    )
    @patch(
        "app.api.routes.reader_record_ask.rr_ask_svc.send_reading_record_ask_message",
    )
    def test_production_error_frame_uses_chinese_fallback_no_leak(
        self,
        mock_send,
        mock_prepare,
        mock_settings,
        mock_auth,
    ) -> None:
        """In production mode, a generic streaming exception yields a fixed
        Chinese fallback detail with a ``user_message`` field and never
        leaks the raw exception text.
        """

        async def _boom(**kwargs):
            raise RuntimeError("internal secret: connection refused to db")
            yield  # pragma: no cover - generator marker

        mock_send.return_value = _boom()
        prod_settings = MagicMock()
        prod_settings.app_env = "production"
        mock_settings.return_value = prod_settings

        client = create_client()
        response = client.post(
            f"/reader/records/{RECORD_ID}/ask/messages",
            headers=AUTH_HEADERS,
            json={"content": "hello"},
        )

        assert response.status_code == 200
        body = response.text
        assert "event: error" in body
        # Chinese fallback message is present.
        assert "Ask Claread 暂时不可用。" in body
        # user_message field is present.
        assert '"user_message"' in body
        # Raw exception text must NOT leak.
        assert "internal secret" not in body
        assert "connection refused to db" not in body
        assert "RuntimeError" not in body

    @_mock_auth()
    @patch(
        "app.api.routes.reader_record_ask.get_settings",
    )
    @patch(
        "app.api.routes.reader_record_ask.rr_ask_svc.prepare_reading_record_ask_message",
        new_callable=AsyncMock,
        return_value=(UUID(RECORD_ID), UUID(THREAD_ID), None),
    )
    @patch(
        "app.api.routes.reader_record_ask.rr_ask_svc.send_reading_record_ask_message",
    )
    def test_dev_error_frame_shows_raw_detail(
        self,
        mock_send,
        mock_prepare,
        mock_settings,
        mock_auth,
    ) -> None:
        """In non-production (dev) mode, the raw exception text is shown
        for debugging.  This preserves the existing dev-mode behavior.
        """

        async def _boom(**kwargs):
            raise RuntimeError("dev-only diagnostic detail")
            yield  # pragma: no cover - generator marker

        mock_send.return_value = _boom()
        dev_settings = MagicMock()
        dev_settings.app_env = "development"
        mock_settings.return_value = dev_settings

        client = create_client()
        response = client.post(
            f"/reader/records/{RECORD_ID}/ask/messages",
            headers=AUTH_HEADERS,
            json={"content": "hello"},
        )

        assert response.status_code == 200
        body = response.text
        assert "event: error" in body
        # Dev mode: raw detail is present for debugging.
        assert "dev-only diagnostic detail" in body


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
                "app.services.reader_record_ask.service.get_settings",
            ) as mock_settings,
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
            mock_settings.return_value.reader_record_ask_agentic_enabled = False
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
    async def test_agentic_path_injects_resolved_flash_model(self) -> None:
        """Thread selected_model_key drives agentic model, not global route default."""
        from app.services.reader_record_ask.service import send_reading_record_ask_message

        request = MagicMock()
        request.anchor = None
        request.content = "summarize"
        request.entry_action = "ask_about_this"
        request.model = None

        flash_option = MagicMock()
        flash_option.key = "deepseek-v4-flash"
        flash_option.selection = MagicMock()
        flash_model = object()

        captured: dict[str, object] = {}

        async def _fake_agentic(**kwargs):
            captured.update(kwargs)
            yield "event: message.completed\ndata: {}\n\n"

        with (
            patch(
                "app.services.reader_record_ask.service.get_settings",
            ) as mock_settings,
            patch(
                "app.services.reader_record_ask.service._load_snapshot_facts_raw",
                new_callable=AsyncMock,
                return_value=MagicMock(record=MagicMock(title="Test")),
            ),
            patch(
                "app.services.reader_record_ask.service.thread_service.ensure_default_reading_record_thread",
                new_callable=AsyncMock,
                return_value={
                    "id": THREAD_ID,
                    "title": "Test",
                    "selected_model_key": "deepseek-v4-flash",
                },
            ),
            patch(
                "app.services.reader_record_ask.service.thread_service.resolve_and_persist_thread_model_option",
                new_callable=AsyncMock,
                return_value=flash_option,
            ) as mock_resolve,
            patch(
                "app.services.reader_record_ask.service.build_model_for_route",
                return_value=(flash_model, MagicMock(model_name="deepseek-v4-flash")),
            ) as mock_build,
            patch(
                "app.services.reader_record_ask.production_stream.stream_agentic_thread_message",
                side_effect=_fake_agentic,
            ),
            patch(
                "app.services.reader_record_ask.service.stream_service.stream_thread_message",
            ) as mock_legacy,
        ):
            mock_settings.return_value.reader_record_ask_agentic_enabled = True
            chunks = [
                chunk
                async for chunk in send_reading_record_ask_message(
                    user_id=UUID(USER_ID),
                    reading_record_id=RECORD_ID,
                    request=request,
                )
            ]

        assert chunks == ["event: message.completed\ndata: {}\n\n"]
        mock_legacy.assert_not_called()
        mock_resolve.assert_awaited_once()
        assert mock_resolve.await_args.kwargs["requested_key"] is None
        mock_build.assert_called_once()
        assert mock_build.call_args.args[2] is flash_option.selection
        assert captured["model"] is flash_model

    @pytest.mark.asyncio
    async def test_agentic_path_history_glm_standard_falls_back_to_flash(self) -> None:
        from app.services.reader_record_ask.service import send_reading_record_ask_message

        request = MagicMock()
        request.anchor = None
        request.content = "hello"
        request.entry_action = "ask_about_this"
        request.model = None

        flash_option = MagicMock()
        flash_option.key = "deepseek-v4-flash"
        flash_option.used_fallback = True
        flash_option.requested_key = "glm-standard"
        flash_option.selection = MagicMock()
        flash_model = object()

        captured: dict[str, object] = {}

        async def _fake_agentic(**kwargs):
            captured.update(kwargs)
            yield "event: agentic.terminal\ndata: {}\n\n"

        with (
            patch(
                "app.services.reader_record_ask.service.get_settings",
            ) as mock_settings,
            patch(
                "app.services.reader_record_ask.service._load_snapshot_facts_raw",
                new_callable=AsyncMock,
                return_value=MagicMock(record=MagicMock(title="Test")),
            ),
            patch(
                "app.services.reader_record_ask.service.thread_service.ensure_default_reading_record_thread",
                new_callable=AsyncMock,
                return_value={
                    "id": THREAD_ID,
                    "title": "Test",
                    "selected_model_key": "glm-standard",
                },
            ),
            patch(
                "app.services.reader_record_ask.service.thread_service.resolve_and_persist_thread_model_option",
                new_callable=AsyncMock,
                return_value=flash_option,
            ) as mock_resolve,
            patch(
                "app.services.reader_record_ask.service.build_model_for_route",
                return_value=(flash_model, MagicMock(model_name="deepseek-v4-flash")),
            ),
            patch(
                "app.services.reader_record_ask.production_stream.stream_agentic_thread_message",
                side_effect=_fake_agentic,
            ),
        ):
            mock_settings.return_value.reader_record_ask_agentic_enabled = True
            chunks = [
                chunk
                async for chunk in send_reading_record_ask_message(
                    user_id=UUID(USER_ID),
                    reading_record_id=RECORD_ID,
                    request=request,
                )
            ]

        assert chunks
        mock_resolve.assert_awaited_once()
        assert mock_resolve.await_args.kwargs["requested_key"] is None
        assert captured["model"] is flash_model

    @pytest.mark.asyncio
    async def test_agentic_path_explicit_pro_builds_pro_model(self) -> None:
        from app.services.reader_record_ask.service import send_reading_record_ask_message

        request = MagicMock()
        request.anchor = None
        request.content = "deep analysis"
        request.entry_action = "ask_about_this"
        request.model = "deepseek-pro"

        pro_option = MagicMock()
        pro_option.key = "deepseek-pro"
        pro_option.selection = MagicMock()
        pro_model = object()
        captured: dict[str, object] = {}

        async def _fake_agentic(**kwargs):
            captured.update(kwargs)
            yield "event: message.completed\ndata: {}\n\n"

        with (
            patch(
                "app.services.reader_record_ask.service.get_settings",
            ) as mock_settings,
            patch(
                "app.services.reader_record_ask.service._load_snapshot_facts_raw",
                new_callable=AsyncMock,
                return_value=MagicMock(record=MagicMock(title="Test")),
            ),
            patch(
                "app.services.reader_record_ask.service.thread_service.ensure_default_reading_record_thread",
                new_callable=AsyncMock,
                return_value={"id": THREAD_ID, "title": "Test"},
            ),
            patch(
                "app.services.reader_record_ask.service.thread_service.resolve_and_persist_thread_model_option",
                new_callable=AsyncMock,
                return_value=pro_option,
            ) as mock_resolve,
            patch(
                "app.services.reader_record_ask.service.build_model_for_route",
                return_value=(pro_model, MagicMock(model_name="deepseek-v4-pro")),
            ) as mock_build,
            patch(
                "app.services.reader_record_ask.production_stream.stream_agentic_thread_message",
                side_effect=_fake_agentic,
            ),
        ):
            mock_settings.return_value.reader_record_ask_agentic_enabled = True
            chunks = [
                chunk
                async for chunk in send_reading_record_ask_message(
                    user_id=UUID(USER_ID),
                    reading_record_id=RECORD_ID,
                    request=request,
                )
            ]

        assert chunks
        assert mock_resolve.await_args.kwargs["requested_key"] == "deepseek-pro"
        assert mock_build.call_args.args[2] is pro_option.selection
        assert captured["model"] is pro_model

    @pytest.mark.asyncio
    async def test_agentic_explicit_deleted_glm_raises_before_stream(self) -> None:
        from app.services.reader_record_ask.service import prepare_reading_record_ask_message

        request = MagicMock()
        request.anchor = None
        request.content = "hello"
        request.entry_action = "ask_about_this"
        request.model = "glm-standard"

        with (
            patch(
                "app.services.reader_record_ask.service.get_settings",
            ) as mock_settings,
            patch(
                "app.services.reader_record_ask.service._load_snapshot_facts_raw",
                new_callable=AsyncMock,
                return_value=MagicMock(record=MagicMock(title="Test")),
            ),
            patch(
                "app.services.reader_record_ask.service.thread_service.ensure_default_reading_record_thread",
                new_callable=AsyncMock,
                return_value={"id": THREAD_ID, "title": "Test"},
            ),
            patch(
                "app.services.reader_record_ask.service.thread_service.resolve_and_persist_thread_model_option",
                new_callable=AsyncMock,
                side_effect=HTTPException(
                    status_code=422,
                    detail="Unknown Ask Claread model option: glm-standard",
                ),
            ),
            patch(
                "app.services.reader_record_ask.production_stream.stream_agentic_thread_message",
            ) as mock_agentic,
        ):
            mock_settings.return_value.reader_record_ask_agentic_enabled = True
            with pytest.raises(HTTPException) as excinfo:
                await prepare_reading_record_ask_message(
                    user_id=UUID(USER_ID),
                    reading_record_id=RECORD_ID,
                    request=request,
                )

        assert excinfo.value.status_code == 422
        mock_agentic.assert_not_called()

    @pytest.mark.asyncio
    async def test_model_build_provider_error_returns_typed_503_before_stream(self) -> None:
        from app.llm.provider_factory import ModelProviderError
        from app.services.reader_record_ask.service import prepare_reading_record_ask_message

        request = MagicMock()
        request.anchor = None
        request.content = "hello"
        request.entry_action = "ask_about_this"
        request.model = None

        flash_option = MagicMock()
        flash_option.key = "deepseek-v4-flash"
        flash_option.selection = MagicMock()

        with (
            patch(
                "app.services.reader_record_ask.service.get_settings",
            ) as mock_settings,
            patch(
                "app.services.reader_record_ask.service._load_snapshot_facts_raw",
                new_callable=AsyncMock,
                return_value=MagicMock(record=MagicMock(title="Test")),
            ),
            patch(
                "app.services.reader_record_ask.service.thread_service.ensure_default_reading_record_thread",
                new_callable=AsyncMock,
                return_value={"id": THREAD_ID, "title": "Test"},
            ),
            patch(
                "app.services.reader_record_ask.service.thread_service.resolve_and_persist_thread_model_option",
                new_callable=AsyncMock,
                return_value=flash_option,
            ),
            patch(
                "app.services.reader_record_ask.service.build_model_for_route",
                side_effect=ModelProviderError("secret provider details must not leak"),
            ),
            patch(
                "app.services.reader_record_ask.production_stream.stream_agentic_thread_message",
            ) as mock_agentic,
        ):
            mock_settings.return_value.reader_record_ask_agentic_enabled = True
            with pytest.raises(HTTPException) as excinfo:
                await prepare_reading_record_ask_message(
                    user_id=UUID(USER_ID),
                    reading_record_id=RECORD_ID,
                    request=request,
                )

        assert excinfo.value.status_code == 503
        assert excinfo.value.detail["code"] == "model_unconfigured"
        assert excinfo.value.detail["model_key"] == "deepseek-v4-flash"
        assert "secret" not in str(excinfo.value.detail)
        mock_agentic.assert_not_called()

    @pytest.mark.asyncio
    async def test_model_build_selection_error_returns_typed_503_before_stream(self) -> None:
        from app.llm.router import ModelSelectionError
        from app.services.reader_record_ask.service import prepare_reading_record_ask_message

        request = MagicMock()
        request.anchor = None
        request.content = "hello"
        request.entry_action = "ask_about_this"
        request.model = "deepseek-pro"

        pro_option = MagicMock()
        pro_option.key = "deepseek-pro"
        pro_option.selection = MagicMock()

        with (
            patch(
                "app.services.reader_record_ask.service.get_settings",
            ) as mock_settings,
            patch(
                "app.services.reader_record_ask.service._load_snapshot_facts_raw",
                new_callable=AsyncMock,
                return_value=MagicMock(record=MagicMock(title="Test")),
            ),
            patch(
                "app.services.reader_record_ask.service.thread_service.ensure_default_reading_record_thread",
                new_callable=AsyncMock,
                return_value={"id": THREAD_ID, "title": "Test"},
            ),
            patch(
                "app.services.reader_record_ask.service.thread_service.resolve_and_persist_thread_model_option",
                new_callable=AsyncMock,
                return_value=pro_option,
            ),
            patch(
                "app.services.reader_record_ask.service.build_model_for_route",
                side_effect=ModelSelectionError("Unknown model profile: secret-profile"),
            ),
            patch(
                "app.services.reader_record_ask.production_stream.stream_agentic_thread_message",
            ) as mock_agentic,
        ):
            mock_settings.return_value.reader_record_ask_agentic_enabled = True
            with pytest.raises(HTTPException) as excinfo:
                await prepare_reading_record_ask_message(
                    user_id=UUID(USER_ID),
                    reading_record_id=RECORD_ID,
                    request=request,
                )

        assert excinfo.value.status_code == 503
        assert excinfo.value.detail["code"] == "model_unconfigured"
        assert "secret-profile" not in str(excinfo.value.detail)
        mock_agentic.assert_not_called()

    @pytest.mark.asyncio
    async def test_model_build_none_returns_typed_503_before_stream(self) -> None:
        from app.services.reader_record_ask.service import prepare_reading_record_ask_message

        request = MagicMock()
        request.anchor = None
        request.content = "hello"
        request.entry_action = "ask_about_this"
        request.model = None

        flash_option = MagicMock()
        flash_option.key = "deepseek-v4-flash"
        flash_option.selection = MagicMock()

        with (
            patch(
                "app.services.reader_record_ask.service.get_settings",
            ) as mock_settings,
            patch(
                "app.services.reader_record_ask.service._load_snapshot_facts_raw",
                new_callable=AsyncMock,
                return_value=MagicMock(record=MagicMock(title="Test")),
            ),
            patch(
                "app.services.reader_record_ask.service.thread_service.ensure_default_reading_record_thread",
                new_callable=AsyncMock,
                return_value={"id": THREAD_ID, "title": "Test"},
            ),
            patch(
                "app.services.reader_record_ask.service.thread_service.resolve_and_persist_thread_model_option",
                new_callable=AsyncMock,
                return_value=flash_option,
            ),
            patch(
                "app.services.reader_record_ask.service.build_model_for_route",
                return_value=(None, MagicMock(profile_name="ask-main-deepseek-v4-flash")),
            ),
            patch(
                "app.services.reader_record_ask.production_stream.stream_agentic_thread_message",
            ) as mock_agentic,
        ):
            mock_settings.return_value.reader_record_ask_agentic_enabled = True
            with pytest.raises(HTTPException) as excinfo:
                await prepare_reading_record_ask_message(
                    user_id=UUID(USER_ID),
                    reading_record_id=RECORD_ID,
                    request=request,
                )

        assert excinfo.value.status_code == 503
        assert excinfo.value.detail["code"] == "model_unconfigured"
        mock_agentic.assert_not_called()

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
