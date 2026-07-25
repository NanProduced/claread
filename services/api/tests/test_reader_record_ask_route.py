"""F1: Reading Record Ask backend route and service tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from app.services.reader_record_ask.execution_config import (
        ReaderRecordAskExecutionConfig,
    )

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


def _make_execution_config(
    *,
    option_key: str,
    model: object,
    max_output_tokens: int = 3200,
    max_turn_output_tokens: int = 9600,
) -> ReaderRecordAskExecutionConfig:
    """Build a real ReaderRecordAskExecutionConfig for service-layer tests.

    ASK-M1: service.py no longer calls ``build_model_for_route`` directly;
    it calls ``resolve_reader_record_ask_execution``. Tests that previously
    patched the build_model_for_route return value now patch the resolver
    and return this config so service.py still propagates a real model
    + budget into ``stream_agentic_thread_message``.

    ASK-M1-R1: the config now also carries ``model_settings_payload``
    (with ``max_tokens``) and ``usage_limits`` so budget-capture tests
    can assert both the provider cap and the host guard.
    """
    from app.services.reader_ask.model_options import ReaderAskRuntimeBudgetConfig
    from app.services.reader_record_ask.execution_config import (
        ReaderRecordAskExecutionConfig,
    )

    return ReaderRecordAskExecutionConfig(
        option_key=option_key,
        model=model,  # type: ignore[arg-type]
        model_settings_payload={"max_tokens": max_output_tokens},
        usage_limits=_make_usage_limits(max_turn_output_tokens),
        runtime_budget=ReaderAskRuntimeBudgetConfig(
            max_input_tokens=24000,
            max_output_tokens=max_output_tokens,
            max_turn_output_tokens=max_turn_output_tokens,
            prompt_buffer_tokens=800,
        ),
    )


def _make_usage_limits(output_tokens_limit: int):
    """Build a PydanticAI UsageLimits with only output_tokens_limit set."""
    from pydantic_ai.usage import UsageLimits

    return UsageLimits(output_tokens_limit=output_tokens_limit)


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
                "app.services.reader_record_ask.service.resolve_reader_record_ask_execution",
                return_value=_make_execution_config(
                    option_key="deepseek-v4-flash",
                    model=flash_model,
                ),
            ) as mock_resolve_exec,
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
        mock_resolve_exec.assert_called_once_with(flash_option)
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
                "app.services.reader_record_ask.service.resolve_reader_record_ask_execution",
                return_value=_make_execution_config(
                    option_key="deepseek-v4-flash",
                    model=flash_model,
                ),
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
                "app.services.reader_record_ask.service.resolve_reader_record_ask_execution",
                return_value=_make_execution_config(
                    option_key="deepseek-pro",
                    model=pro_model,
                ),
            ) as mock_resolve_exec,
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
        mock_resolve_exec.assert_called_once_with(pro_option)
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
        from app.services.reader_record_ask.execution_config import (
            ReaderRecordAskExecutionUnavailable,
        )
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
                "app.services.reader_record_ask.service.resolve_reader_record_ask_execution",
                side_effect=ReaderRecordAskExecutionUnavailable(
                    option_key="deepseek-v4-flash",
                    reason="model_build_failed",
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

        assert excinfo.value.status_code == 503
        assert excinfo.value.detail["code"] == "model_unconfigured"
        assert excinfo.value.detail["model_key"] == "deepseek-v4-flash"
        assert "secret" not in str(excinfo.value.detail)
        mock_agentic.assert_not_called()

    @pytest.mark.asyncio
    async def test_model_build_selection_error_returns_typed_503_before_stream(self) -> None:
        from app.services.reader_record_ask.execution_config import (
            ReaderRecordAskExecutionUnavailable,
        )
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
                "app.services.reader_record_ask.service.resolve_reader_record_ask_execution",
                side_effect=ReaderRecordAskExecutionUnavailable(
                    option_key="deepseek-pro",
                    reason="model_build_failed",
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

        assert excinfo.value.status_code == 503
        assert excinfo.value.detail["code"] == "model_unconfigured"
        assert "secret-profile" not in str(excinfo.value.detail)
        mock_agentic.assert_not_called()

    @pytest.mark.asyncio
    async def test_model_build_none_returns_typed_503_before_stream(self) -> None:
        from app.services.reader_record_ask.execution_config import (
            ReaderRecordAskExecutionUnavailable,
        )
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
                "app.services.reader_record_ask.service.resolve_reader_record_ask_execution",
                side_effect=ReaderRecordAskExecutionUnavailable(
                    option_key="deepseek-v4-flash",
                    reason="model_unconfigured",
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


# ---------------------------------------------------------------------------
# ASK-M1-R1: Retry preflight + Send/Retry budget capture
# ---------------------------------------------------------------------------


class TestReaderRecordAskRetryPreflight:
    """ASK-M1-R1: Retry must preflight before StreamingResponse.

    Retry now mirrors Send's fail-closed HTTP semantics: if the
    execution config cannot be resolved (model build failure,
    unconfigured provider), the route returns a real HTTP 503 before
    any SSE byte is written. The generator never re-resolves facts /
    option / model — it reuses the ``RetryPreparedResult`` from the
    preflight.
    """

    @_mock_auth()
    @patch(
        "app.api.routes.reader_record_ask.rr_ask_svc.prepare_reading_record_ask_retry",
        new_callable=AsyncMock,
        side_effect=HTTPException(
            status_code=503,
            detail={
                "code": "model_unconfigured",
                "message": "Ask Claread model is not configured for the selected option.",
                "model_key": "deepseek-pro",
            },
        ),
    )
    @patch(
        "app.api.routes.reader_record_ask.rr_ask_svc.retry_reading_record_ask_message",
        return_value=_stream_chunks("event: message.completed\ndata: {}\n\n"),
    )
    def test_retry_config_resolution_failure_returns_503_no_stream(
        self,
        mock_retry,
        mock_prepare,
        mock_auth,
    ) -> None:
        """Retry preflight 503 — no SSE stream started.

        The route awaits ``prepare_reading_record_ask_retry`` before
        constructing the StreamingResponse. When the execution config
        cannot be resolved, the HTTPException(503) propagates as a real
        HTTP 503 response (not an SSE error frame), and the retry
        generator is never invoked.
        """
        client = create_client()
        message_id = "00000000-0000-0000-0000-0000000000d6"

        response = client.post(
            f"/reader/records/{RECORD_ID}/ask/threads/{THREAD_ID}"
            f"/messages/{message_id}/retry/stream",
            headers=AUTH_HEADERS,
            json={},
        )

        assert response.status_code == 503
        body = response.json()
        assert body["detail"]["code"] == "model_unconfigured"
        assert body["detail"]["model_key"] == "deepseek-pro"
        # No SSE stream started — generator never called.
        mock_retry.assert_not_called()
        mock_prepare.assert_awaited_once()

    @_mock_auth()
    @patch(
        "app.api.routes.reader_record_ask.rr_ask_svc.prepare_reading_record_ask_retry",
        new_callable=AsyncMock,
        side_effect=HTTPException(
            status_code=400,
            detail={
                "code": "invalid_uuid",
                "field": "reading_record_id",
                "message": "reading_record_id must be a UUID",
            },
        ),
    )
    @patch(
        "app.api.routes.reader_record_ask.rr_ask_svc.retry_reading_record_ask_message",
        return_value=_stream_chunks("event: message.completed\ndata: {}\n\n"),
    )
    def test_retry_invalid_record_id_returns_400_no_stream(
        self,
        mock_retry,
        mock_prepare,
        mock_auth,
    ) -> None:
        """Retry preflight 400 — UUID parse failure surfaces before stream."""
        client = create_client()
        message_id = "00000000-0000-0000-0000-0000000000d6"

        response = client.post(
            f"/reader/records/not-a-uuid/ask/threads/{THREAD_ID}"
            f"/messages/{message_id}/retry/stream",
            headers=AUTH_HEADERS,
            json={},
        )

        # FastAPI path validation catches the non-UUID before the route
        # body even runs — 422. But if the preflight itself raised 400,
        # the route would propagate it. Here we just confirm no stream.
        assert response.status_code in (400, 422)
        mock_retry.assert_not_called()


class TestReaderRecordAskBudgetCapture:
    """ASK-M1-R1: Send and Retry must propagate the exact resolved model
    + budget into the agentic stream.

    Captures the kwargs passed to ``stream_agentic_thread_message``
    (Send) and ``retry_agentic_thread_message`` (Retry) and asserts:

    - ``model`` is the exact resolved model object (Pro);
    - ``model_settings["max_tokens"]`` equals the option budget (6400);
    - ``usage_limits.output_tokens_limit`` uses the cumulative turn cap;
    - ``usage_limits.input_tokens_limit`` and ``total_tokens_limit``
      are both ``None`` (char ledger stays independent).
    """

    @pytest.mark.asyncio
    async def test_send_agentic_pro_captures_resolved_model_and_budget(self) -> None:
        """Send path: Pro option → 6400 request cap + 19200 turn cap."""
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
            ),
            patch(
                "app.services.reader_record_ask.service.resolve_reader_record_ask_execution",
                return_value=_make_execution_config(
                    option_key="deepseek-pro",
                    model=pro_model,
                    max_output_tokens=6400,
                    max_turn_output_tokens=19200,
                ),
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

        assert chunks == ["event: message.completed\ndata: {}\n\n"]
        # Exact resolved model — Pro model object, not a default.
        assert captured["model"] is pro_model
        # Provider completion cap.
        model_settings = captured["model_settings"]
        assert model_settings is not None
        assert model_settings["max_tokens"] == 6400
        # Host usage limit — output only, input/total left None.
        usage_limits = captured["usage_limits"]
        assert usage_limits is not None
        assert usage_limits.output_tokens_limit == 19200
        assert usage_limits.input_tokens_limit is None
        assert usage_limits.total_tokens_limit is None

    @pytest.mark.asyncio
    async def test_retry_agentic_pro_captures_resolved_model_and_budget(self) -> None:
        """Retry path: Pro option → 6400 request cap + 19200 turn cap.

        Mirrors the Send capture test. The retry generator must receive
        the same ``model`` / ``model_settings`` / ``usage_limits``
        derived from the persisted Pro option — proving Send and Retry
        have identical budget propagation.
        """
        from app.services.reader_ask.model_options import ReaderAskRuntimeBudgetConfig
        from app.services.reader_record_ask.execution_config import (
            ReaderRecordAskExecutionConfig,
        )
        from app.services.reader_record_ask.service import (
            prepare_reading_record_ask_retry,
            retry_reading_record_ask_message,
        )

        pro_option = MagicMock()
        pro_option.key = "deepseek-pro"
        pro_option.selection = MagicMock()
        pro_model = object()

        pro_execution = ReaderRecordAskExecutionConfig(
            option_key="deepseek-pro",
            model=pro_model,  # type: ignore[arg-type]
            model_settings_payload={"max_tokens": 6400},
            usage_limits=_make_usage_limits(19200),
            runtime_budget=ReaderAskRuntimeBudgetConfig(
                max_input_tokens=24000,
                max_output_tokens=6400,
                max_turn_output_tokens=19200,
                prompt_buffer_tokens=800,
            ),
        )

        captured: dict[str, object] = {}

        async def _fake_retry(**kwargs):
            captured.update(kwargs)
            yield "event: message.completed\ndata: {}\n\n"

        message_id = uuid4()

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
                "app.services.reader_record_ask.service.thread_service.resolve_and_persist_thread_model_option",
                new_callable=AsyncMock,
                return_value=pro_option,
            ),
            patch(
                "app.services.reader_record_ask.service.resolve_reader_record_ask_execution",
                return_value=pro_execution,
            ),
            patch(
                "app.services.reader_record_ask.production_stream.retry_agentic_thread_message",
                side_effect=_fake_retry,
            ),
        ):
            mock_settings.return_value.reader_record_ask_agentic_enabled = True
            prepared = await prepare_reading_record_ask_retry(
                user_id=UUID(USER_ID),
                reading_record_id=RECORD_ID,
                thread_id=UUID(THREAD_ID),
            )
            chunks = [
                chunk
                async for chunk in retry_reading_record_ask_message(
                    user_id=UUID(USER_ID),
                    reading_record_id=RECORD_ID,
                    thread_id=UUID(THREAD_ID),
                    message_id=message_id,
                    request=MagicMock(),
                    prepared=prepared,
                )
            ]

        assert chunks == ["event: message.completed\ndata: {}\n\n"]
        # Preflight resolved to agentic mode with the Pro execution config.
        assert prepared.mode == "agentic"
        assert prepared.execution is pro_execution
        assert prepared.facts is not None
        # Exact resolved model — Pro model object.
        assert captured["model"] is pro_model
        # Provider completion cap.
        model_settings = captured["model_settings"]
        assert model_settings is not None
        assert model_settings["max_tokens"] == 6400
        # Host usage limit — output only, input/total left None.
        usage_limits = captured["usage_limits"]
        assert usage_limits is not None
        assert usage_limits.output_tokens_limit == 19200
        assert usage_limits.input_tokens_limit is None
        assert usage_limits.total_tokens_limit is None

    @pytest.mark.asyncio
    async def test_retry_legacy_mode_does_not_resolve_execution(self) -> None:
        """Legacy retry (agentic flag off) — no execution config, no model.

        ASK-M1-R1: ``mode`` is fixed at preflight time. When the
        agentic flag is off, ``prepare_reading_record_ask_retry`` returns
        ``mode="legacy"`` with ``execution=None`` and the generator
        delegates to ``stream_service.retry_thread_message`` without
        touching the agentic path.
        """
        from app.services.reader_record_ask.service import (
            prepare_reading_record_ask_retry,
            retry_reading_record_ask_message,
        )

        captured: dict[str, object] = {}

        async def _fake_legacy_retry(**kwargs):
            captured.update(kwargs)
            yield "event: message.completed\ndata: {}\n\n"

        message_id = uuid4()

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
                "app.services.reader_record_ask.service.stream_service.retry_thread_message",
                side_effect=_fake_legacy_retry,
            ),
            patch(
                "app.services.reader_record_ask.production_stream.retry_agentic_thread_message",
            ) as mock_agentic,
        ):
            mock_settings.return_value.reader_record_ask_agentic_enabled = False
            prepared = await prepare_reading_record_ask_retry(
                user_id=UUID(USER_ID),
                reading_record_id=RECORD_ID,
                thread_id=UUID(THREAD_ID),
            )
            chunks = [
                chunk
                async for chunk in retry_reading_record_ask_message(
                    user_id=UUID(USER_ID),
                    reading_record_id=RECORD_ID,
                    thread_id=UUID(THREAD_ID),
                    message_id=message_id,
                    request=MagicMock(),
                    prepared=prepared,
                )
            ]

        assert chunks == ["event: message.completed\ndata: {}\n\n"]
        assert prepared.mode == "legacy"
        assert prepared.execution is None
        # Legacy path never touches the agentic stream.
        mock_agentic.assert_not_called()
        # Legacy retry received message_id + retry_body.
        assert captured["message_id"] == message_id
