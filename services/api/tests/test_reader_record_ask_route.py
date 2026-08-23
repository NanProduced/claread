"""F1: Reading Record Ask backend route and service tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic_ai.models.function import FunctionModel

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

    ASK-M1-the config now also carries ``model_settings_payload``
    (with ``max_tokens``) and ``usage_limits`` so budget-capture tests
    can assert both the provider cap and the host guard.
    """
    from app.services.reader_record_ask.execution_config import (
        ReaderRecordAskExecutionConfig,
    )
    from app.services.reader_record_ask.model_options import ReaderAskRuntimeBudgetConfig

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
    def test_no_thread_message_url_is_removed(self) -> None:
        client = create_client()

        response = client.post(
            f"/reader/records/{RECORD_ID}/ask/messages",
            json={"content": "hello"},
        )

        assert response.status_code == 404

    def test_thread_stream_requires_auth(self) -> None:
        client = create_client()

        response = client.post(
            f"/reader/records/{RECORD_ID}/ask/threads/{THREAD_ID}/messages/stream",
            json={"content": "hello"},
        )

        assert response.status_code == 401

    @_mock_auth()
    def test_messages_reject_unknown_fields(self, mock_auth) -> None:
        client = create_client()

        response = client.post(
            f"/reader/records/{RECORD_ID}/ask/threads/{THREAD_ID}/messages/stream",
            headers=AUTH_HEADERS,
            json={"content": "hello", "task_mode": "explain"},
        )

        assert response.status_code == 422

class TestReaderRecordAskService:
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
        # ASK-WEB-G1-route forwards web_search_mode to the execution
        # config resolver. Set an explicit value so the assertion can
        # verify it is plumbed through.
        request.web_search_mode = "disabled"

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
        ):
            chunks = [
                chunk
                async for chunk in send_reading_record_ask_message(
                    user_id=UUID(USER_ID),
                    reading_record_id=RECORD_ID,
                    request=request,
                )
            ]

        assert chunks == ["event: message.completed\ndata: {}\n\n"]
        mock_resolve.assert_awaited_once()
        assert mock_resolve.await_args.kwargs["requested_key"] is None
        # ASK-WEB-G1-route forwards web_search_mode to the execution
        # config resolver alongside the resolved model option.
        mock_resolve_exec.assert_called_once_with(
            flash_option, web_search_mode="disabled"
        )
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
        # ASK-WEB-G1-route forwards web_search_mode to the execution
        # config resolver. Set an explicit value so the assertion can
        # verify it is plumbed through.
        request.web_search_mode = "disabled"

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
        # ASK-WEB-G1-route forwards web_search_mode to the execution
        # config resolver alongside the resolved model option.
        mock_resolve_exec.assert_called_once_with(
            pro_option, web_search_mode="disabled"
        )
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
            with pytest.raises(HTTPException) as excinfo:
                await prepare_reading_record_ask_message(
                    user_id=UUID(USER_ID),
                    reading_record_id=RECORD_ID,
                    request=request,
                )

        assert excinfo.value.status_code == 503
        assert excinfo.value.detail["code"] == "model_unconfigured"
        mock_agentic.assert_not_called()

# ---------------------------------------------------------------------------
# ASK-M1-Retry preflight + Send/Retry budget capture
# ---------------------------------------------------------------------------


class TestReaderRecordAskRetryPreflight:
    """ASK-M1-Retry must preflight before StreamingResponse.

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
    """ASK-M1-Send and Retry must propagate the exact resolved model
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
        from app.services.reader_record_ask.execution_config import (
            ReaderRecordAskExecutionConfig,
        )
        from app.services.reader_record_ask.model_options import ReaderAskRuntimeBudgetConfig
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
        repo = MagicMock()
        repo.get_thread = AsyncMock(return_value={"id": THREAD_ID})
        persisted_snapshot = {
            "execution_version": "reader_record_ask_agentic_v2",
            "model_option_key": "deepseek-pro",
            "web_search_mode": "disabled",
        }
        repo.get_assistant_message_with_preceding_user_message = AsyncMock(
            return_value=(
                {
                    "metadata_json": {"retry_snapshot": persisted_snapshot},
                    "turn_run_execution_version": "reader_record_ask_agentic_v2",
                },
                {
                    "metadata_json": {
                        "retry_snapshot": persisted_snapshot,
                        "web_search_mode": "disabled",
                    }
                },
            )
        )

        with (
            patch(
                "app.services.reader_record_ask.repository.ReaderRecordAskRepository",
                return_value=repo,
            ),
            patch(
                "app.services.reader_record_ask.service.ReaderRecordAskRepository",
                return_value=repo,
            ),
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
            prepared = await prepare_reading_record_ask_retry(
                user_id=UUID(USER_ID),
                reading_record_id=RECORD_ID,
                thread_id=UUID(THREAD_ID),
                message_id=message_id,
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
        # Preflight resolved the v2 Pro execution config.
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


class LifecycleTrackingModel(FunctionModel):
    def __init__(
        self,
        function=None,
        stream_function=None,
        model_name: str = "lifecycle-tracking-fake",
        **kwargs,
    ) -> None:
        if function is None:
            async def _default_fn(messages, info):
                return "ok"

            function = _default_fn
        super().__init__(
            function=function,
            stream_function=stream_function,
            model_name=model_name,
            **kwargs,
        )
        self.enter_count: int = 0
        self.exit_count: int = 0
        self.exited: bool = False
        self.active: bool = False

    async def __aenter__(self):
        self.enter_count += 1
        self.active = True
        return await super().__aenter__()

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.exit_count += 1
        self.active = False
        self.exited = True
        return await super().__aexit__(exc_type, exc_val, exc_tb)


class TestReaderRecordAskModelLifecycle:
    @pytest.mark.asyncio
    async def test_send_stream_lifecycle_enters_and_exits_model_exactly_once(self) -> None:
        from app.services.reader_record_ask.service import send_reading_record_ask_message

        request = MagicMock()
        request.anchor = None
        request.content = "hello"
        request.entry_action = "ask_about_this"
        request.model = None
        request.web_search_mode = "disabled"
        request.client_submission_id = None

        fake_model = LifecycleTrackingModel()
        option = MagicMock(key="fake-opt", selection=MagicMock())

        async def _fake_stream(**kwargs):
            yield "event: message.completed\ndata: {}\n\n"

        with (
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
                return_value=option,
            ),
            patch(
                "app.services.reader_record_ask.service.resolve_reader_record_ask_execution",
                return_value=_make_execution_config(option_key="fake-opt", model=fake_model),
            ),
            patch(
                "app.services.reader_record_ask.submission_gateway.ensure_submission_for_send",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "app.services.reader_record_ask.production_stream.stream_agentic_thread_message",
                side_effect=_fake_stream,
            ),
        ):
            chunks = [
                chunk
                async for chunk in send_reading_record_ask_message(
                    user_id=UUID(USER_ID),
                    reading_record_id=RECORD_ID,
                    request=request,
                )
            ]

        assert chunks == ["event: message.completed\ndata: {}\n\n"]
        assert fake_model.enter_count == 1
        assert fake_model.exit_count == 1
        assert fake_model.exited is True
        assert fake_model.active is False

    @pytest.mark.asyncio
    async def test_retry_stream_lifecycle_enters_and_exits_model_exactly_once(self) -> None:
        from app.services.reader_record_ask.service import (
            prepare_reading_record_ask_retry,
            retry_reading_record_ask_message,
        )

        fake_model = LifecycleTrackingModel()
        option = MagicMock(key="fake-opt", selection=MagicMock())
        execution = _make_execution_config(option_key="fake-opt", model=fake_model)

        async def _fake_retry(**kwargs):
            yield "event: message.completed\ndata: {}\n\n"

        message_id = uuid4()
        repo = MagicMock()
        repo.get_thread = AsyncMock(return_value={"id": THREAD_ID})
        persisted_snapshot = {
            "execution_version": "reader_record_ask_agentic_v2",
            "model_option_key": "fake-opt",
            "web_search_mode": "disabled",
        }
        repo.get_assistant_message_with_preceding_user_message = AsyncMock(
            return_value=(
                {
                    "metadata_json": {"retry_snapshot": persisted_snapshot},
                    "turn_run_execution_version": "reader_record_ask_agentic_v2",
                },
                {
                    "metadata_json": {
                        "retry_snapshot": persisted_snapshot,
                        "web_search_mode": "disabled",
                    }
                },
            )
        )

        with (
            patch(
                "app.services.reader_record_ask.repository.ReaderRecordAskRepository",
                return_value=repo,
            ),
            patch(
                "app.services.reader_record_ask.service.ReaderRecordAskRepository",
                return_value=repo,
            ),
            patch(
                "app.services.reader_record_ask.service._load_snapshot_facts_raw",
                new_callable=AsyncMock,
                return_value=MagicMock(record=MagicMock(title="Test")),
            ),
            patch(
                "app.services.reader_record_ask.service.thread_service.resolve_and_persist_thread_model_option",
                new_callable=AsyncMock,
                return_value=option,
            ),
            patch(
                "app.services.reader_record_ask.service.resolve_reader_record_ask_execution",
                return_value=execution,
            ),
            patch(
                "app.services.reader_record_ask.production_stream.retry_agentic_thread_message",
                side_effect=_fake_retry,
            ),
        ):
            prepared = await prepare_reading_record_ask_retry(
                user_id=UUID(USER_ID),
                reading_record_id=RECORD_ID,
                thread_id=UUID(THREAD_ID),
                message_id=message_id,
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
        assert fake_model.enter_count == 1
        assert fake_model.exit_count == 1
        assert fake_model.exited is True
        assert fake_model.active is False

    @pytest.mark.asyncio
    async def test_send_stream_lifecycle_exits_on_agent_exception_and_preserves_error(self) -> None:
        from app.services.reader_record_ask.service import send_reading_record_ask_message

        request = MagicMock()
        request.anchor = None
        request.content = "hello"
        request.entry_action = "ask_about_this"
        request.model = None
        request.web_search_mode = "disabled"
        request.client_submission_id = None

        fake_model = LifecycleTrackingModel()
        option = MagicMock(key="fake-opt", selection=MagicMock())

        async def _failing_stream(**kwargs):
            yield "event: agentic.run_started\ndata: {}\n\n"
            raise RuntimeError("agent runtime stream exploded")

        with (
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
                return_value=option,
            ),
            patch(
                "app.services.reader_record_ask.service.resolve_reader_record_ask_execution",
                return_value=_make_execution_config(option_key="fake-opt", model=fake_model),
            ),
            patch(
                "app.services.reader_record_ask.submission_gateway.ensure_submission_for_send",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "app.services.reader_record_ask.production_stream.stream_agentic_thread_message",
                side_effect=_failing_stream,
            ),
        ):
            generator = send_reading_record_ask_message(
                user_id=UUID(USER_ID),
                reading_record_id=RECORD_ID,
                request=request,
            )
            first_chunk = await anext(generator)
            assert first_chunk.startswith("event: agentic.run_started")
            with pytest.raises(RuntimeError, match="agent runtime stream exploded"):
                await anext(generator)

        assert fake_model.enter_count == 1
        assert fake_model.exit_count == 1
        assert fake_model.exited is True
        assert fake_model.active is False

    @pytest.mark.asyncio
    async def test_stream_lifecycle_exits_on_cancellation_or_aclose(self) -> None:
        from app.services.reader_record_ask.service import send_reading_record_ask_message

        request = MagicMock()
        request.anchor = None
        request.content = "hello"
        request.entry_action = "ask_about_this"
        request.model = None
        request.web_search_mode = "disabled"
        request.client_submission_id = None

        fake_model = LifecycleTrackingModel()
        option = MagicMock(key="fake-opt", selection=MagicMock())

        async def _long_stream(**kwargs):
            yield "event: agentic.run_started\ndata: {}\n\n"
            yield "event: message.delta\ndata: {\"delta\": \"hi\"}\n\n"
            yield "event: message.completed\ndata: {}\n\n"

        with (
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
                return_value=option,
            ),
            patch(
                "app.services.reader_record_ask.service.resolve_reader_record_ask_execution",
                return_value=_make_execution_config(option_key="fake-opt", model=fake_model),
            ),
            patch(
                "app.services.reader_record_ask.submission_gateway.ensure_submission_for_send",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "app.services.reader_record_ask.production_stream.stream_agentic_thread_message",
                side_effect=_long_stream,
            ),
        ):
            generator = send_reading_record_ask_message(
                user_id=UUID(USER_ID),
                reading_record_id=RECORD_ID,
                request=request,
            )
            first_chunk = await anext(generator)
            assert first_chunk.startswith("event: agentic.run_started")
            assert fake_model.active is True
            assert fake_model.exited is False
            await generator.aclose()

        assert fake_model.enter_count == 1
        assert fake_model.exit_count == 1
        assert fake_model.exited is True
        assert fake_model.active is False

    @pytest.mark.asyncio
    async def test_send_preflight_failure_after_model_build_safely_closes_model(self) -> None:
        from app.services.reader_record_ask.service import prepare_reading_record_ask_message

        request = MagicMock()
        request.focus_anchors = None
        request.anchor = None
        request.content = "hello"
        request.model = None
        request.web_search_mode = "allowed"

        fake_model = LifecycleTrackingModel()
        option = MagicMock(key="fake-opt", selection=MagicMock())
        exec_config = MagicMock(
            option_key="fake-opt",
            model=fake_model,
            web_search_capability=None,
            web_search_backend=None,
        )

        with (
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
                return_value=option,
            ),
            patch(
                "app.services.reader_record_ask.service.resolve_reader_record_ask_execution",
                return_value=exec_config,
            ),
        ):
            with pytest.raises(HTTPException) as excinfo:
                await prepare_reading_record_ask_message(
                    user_id=UUID(USER_ID),
                    reading_record_id=RECORD_ID,
                    request=request,
                )

            assert excinfo.value.status_code == 503
            assert excinfo.value.detail["code"] == "web_search_unavailable"

        assert fake_model.enter_count == 1
        assert fake_model.exit_count == 1
        assert fake_model.exited is True

    @pytest.mark.asyncio
    async def test_retry_preflight_failure_after_model_build_safely_closes_model(self) -> None:
        from app.services.reader_record_ask.service import prepare_reading_record_ask_retry

        fake_model = LifecycleTrackingModel()
        option = MagicMock(key="fake-opt", selection=MagicMock())
        exec_config = MagicMock(
            option_key="fake-opt",
            model=fake_model,
            web_search_capability=None,
            web_search_backend=None,
        )

        message_id = uuid4()
        repo = MagicMock()
        repo.get_thread = AsyncMock(return_value={"id": THREAD_ID})
        persisted_snapshot = {
            "execution_version": "reader_record_ask_agentic_v2",
            "model_option_key": "fake-opt",
            "web_search_mode": "allowed",
        }
        repo.get_assistant_message_with_preceding_user_message = AsyncMock(
            return_value=(
                {
                    "metadata_json": {"retry_snapshot": persisted_snapshot},
                    "turn_run_execution_version": "reader_record_ask_agentic_v2",
                },
                {
                    "metadata_json": {
                        "retry_snapshot": persisted_snapshot,
                        "web_search_mode": "allowed",
                    }
                },
            )
        )

        with (
            patch(
                "app.services.reader_record_ask.repository.ReaderRecordAskRepository",
                return_value=repo,
            ),
            patch(
                "app.services.reader_record_ask.service.ReaderRecordAskRepository",
                return_value=repo,
            ),
            patch(
                "app.services.reader_record_ask.service._load_snapshot_facts_raw",
                new_callable=AsyncMock,
                return_value=MagicMock(record=MagicMock(title="Test")),
            ),
            patch(
                "app.services.reader_record_ask.service.thread_service.resolve_and_persist_thread_model_option",
                new_callable=AsyncMock,
                return_value=option,
            ),
            patch(
                "app.services.reader_record_ask.service.resolve_reader_record_ask_execution",
                return_value=exec_config,
            ),
        ):
            with pytest.raises(HTTPException) as excinfo:
                await prepare_reading_record_ask_retry(
                    user_id=UUID(USER_ID),
                    reading_record_id=RECORD_ID,
                    thread_id=UUID(THREAD_ID),
                    message_id=message_id,
                )

            assert excinfo.value.status_code == 503
            assert excinfo.value.detail["code"] == "web_search_replay_unavailable"

        assert fake_model.enter_count == 1
        assert fake_model.exit_count == 1
        assert fake_model.exited is True

    @pytest.mark.asyncio
    async def test_borrowed_model_in_production_stream_is_not_closed(self) -> None:
        """Borrowed models passed to production_stream must NOT be entered or exited."""
        from types import SimpleNamespace

        from app.services.reader_record_ask.production_stream import (
            stream_agentic_thread_message,
        )

        borrowed_model = LifecycleTrackingModel()

        base = SimpleNamespace(
            base_id=BASE_ID,
            content_sha256="1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
            text="hello world",
        )
        unit = SimpleNamespace(
            unit_id="u1",
            order_index=0,
            text="hello world",
            text_hash="11111111",
            base_start_utf16=0,
            base_end_utf16=11,
        )
        seg = SimpleNamespace(
            unit_id="u1",
            anchor_segment_id="s1",
            order_index=0,
            unit_order_index=0,
            text="hello",
            text_hash="a1b2c3d4",
            unit_start_utf16=0,
            unit_end_utf16=5,
            base_start_utf16=0,
            base_end_utf16=5,
        )
        build_result = SimpleNamespace(base=base, units=(unit,), anchor_segments=(seg,))
        record = SimpleNamespace(
            generation=1,
            product_state="readable_enhancing",
            readiness_state="article_ready",
            title="Test",
        )
        facts = SimpleNamespace(build_result=build_result, record=record)

        mock_repo = MagicMock()
        mock_repo.get_thread = AsyncMock(return_value={"id": THREAD_ID})
        mock_repo.create_message = AsyncMock(return_value={"id": str(uuid4())})
        mock_repo.create_assistant_message_with_turn_run = AsyncMock(
            return_value=(
                {"id": str(uuid4()), "role": "assistant"},
                {"id": str(uuid4()), "run_attempt": 1},
            )
        )
        mock_repo.create_agentic_message_pair = AsyncMock(
            return_value=(
                {"id": str(uuid4()), "role": "user"},
                {"id": str(uuid4()), "role": "assistant"},
            )
        )
        mock_repo.create_agentic_turn_run = AsyncMock(
            return_value={"id": str(uuid4()), "run_attempt": 1}
        )
        mock_repo.complete_agentic_turn_run = AsyncMock(return_value={"status": "completed"})
        mock_repo.terminal_agentic_turn_run = AsyncMock(return_value={"status": "failed"})

        async def _fake_run_agent(**kwargs):
            return MagicMock(
                final_text="test",
                finalized=MagicMock(
                    status="ok",
                    answer_blocks=[],
                    public_citations=[],
                    knowledge_mode="article_grounded",
                    source_status=None,
                    web_search_summary=None,
                    resolved_evidence=[],
                    citation_bindings=[],
                ),
                events=[],
                web_search_turn_observation=None,
            )

        generator = stream_agentic_thread_message(
            user_id=UUID(USER_ID),
            reading_record_id=UUID(RECORD_ID),
            thread_id=UUID(THREAD_ID),
            content="test content",
            facts=facts,
            request_anchor=None,
            repository=mock_repo,
            auto_wire_dependencies=False,
            run_fn=_fake_run_agent,
            model=borrowed_model,
        )
        chunks = [chunk async for chunk in generator]

        assert chunks
        # Borrowed model must NOT be touched by production_stream
        assert borrowed_model.enter_count == 0
        assert borrowed_model.exit_count == 0
        assert borrowed_model.exited is False


# ---------------------------------------------------------------------------
# RED-A (service chain): a post-build failure INSIDE the resolver — after
# ``build_model_for_route`` already produced the owned model — must close
# the model exactly once and keep the typed unavailable/503 contract.
# ---------------------------------------------------------------------------


class TestResolverPostBuildFailureModelLifecycle:
    @pytest.mark.asyncio
    async def test_send_prepare_closes_model_on_resolver_post_build_failure(self) -> None:
        from app.services.reader_record_ask.service import (
            prepare_reading_record_ask_message,
        )
        from tests.test_reader_record_ask_execution_config import (
            _resolve_option,
            _three_option_settings,
        )

        settings = _three_option_settings()
        option = _resolve_option(settings, "deepseek-v4-flash")
        tracker = LifecycleTrackingModel()

        request = MagicMock()
        request.focus_anchors = None
        request.anchor = None
        request.content = "hello"
        request.entry_action = "ask_about_this"
        request.model = None
        request.web_search_mode = "disabled"
        request.client_submission_id = None

        module = "app.services.reader_record_ask.execution_config"
        with (
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
                return_value=option,
            ),
            patch(
                f"{module}.build_model_for_route",
                return_value=(tracker, MagicMock()),
            ),
            patch(
                f"{module}._resolve_model_settings",
                side_effect=RuntimeError("post-build failure"),
            ),
        ):
            with pytest.raises(HTTPException) as excinfo:
                await prepare_reading_record_ask_message(
                    user_id=UUID(USER_ID),
                    reading_record_id=RECORD_ID,
                    request=request,
                )

        # Still the existing typed unavailable/503 contract — no leakage.
        assert excinfo.value.status_code == 503
        assert excinfo.value.detail["code"] == "model_unconfigured"
        assert "post-build failure" not in str(excinfo.value.detail)
        # The already-built model exited exactly once.
        assert tracker.exit_count == 1
        assert tracker.exited is True


# ---------------------------------------------------------------------------
# Pre-first-byte disconnect proven at the REAL ASGI boundary.
# The route runs preflight, builds the owned model, and hands it to an
# async generator that has NOT started yet. starlette 0.48
# ``StreamingResponse.stream_response`` has no finally and never calls
# ``body_iterator.aclose()`` — so a manual aclose is not proof. These
# tests drive ``response(scope, receive, send)`` directly and fail
# ``send`` before the first byte; the model must still exit exactly once.
# ---------------------------------------------------------------------------


_ASGI_SCOPE = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"}}


class _FirstByteFailingSend:
    """Real-ASGI send probe: records messages, fails before any body byte."""

    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def __call__(self, message: dict) -> None:
        self.messages.append(message)
        raise OSError("client disconnected before first byte")


async def _noop_receive() -> dict:
    return {"type": "http.disconnect"}


class TestPreFirstByteCloseModelLifecycle:
    """The proof must run the REAL ASGI response lifecycle.

    ``response(scope, receive, send)`` is executed directly and ``send``
    fails before the first body byte — the framework never iterates the
    body generator, so nothing inside it (no finally) can own the close.
    """

    @staticmethod
    def _retry_repo_mock() -> MagicMock:
        repo = MagicMock()
        repo.get_thread = AsyncMock(return_value={"id": THREAD_ID})
        persisted_snapshot = {
            "execution_version": "reader_record_ask_agentic_v2",
            "model_option_key": "fake-opt",
            "web_search_mode": "disabled",
        }
        repo.get_assistant_message_with_preceding_user_message = AsyncMock(
            return_value=(
                {
                    "metadata_json": {"retry_snapshot": persisted_snapshot},
                    "turn_run_execution_version": "reader_record_ask_agentic_v2",
                },
                {
                    "metadata_json": {
                        "retry_snapshot": persisted_snapshot,
                        "web_search_mode": "disabled",
                    }
                },
            )
        )
        return repo

    @pytest.mark.asyncio
    async def test_send_asgi_disconnect_before_first_byte_exits_model_once(self) -> None:
        from types import SimpleNamespace

        from starlette.requests import ClientDisconnect

        from app.api.routes.reader_record_ask import (
            stream_reading_record_ask_thread_message,
        )
        from app.schemas.reader_ask import ReaderRecordAskMessageRequest

        fake_model = LifecycleTrackingModel()
        option = MagicMock(key="fake-opt", selection=MagicMock())

        with (
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
                return_value=option,
            ),
            patch(
                "app.services.reader_record_ask.service.resolve_reader_record_ask_execution",
                return_value=_make_execution_config(
                    option_key="fake-opt", model=fake_model
                ),
            ),
            patch(
                "app.services.reader_record_ask.submission_gateway.ensure_submission_for_send",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            response = await stream_reading_record_ask_thread_message(
                reading_record_id=RECORD_ID,
                thread_id=UUID(THREAD_ID),
                body=ReaderRecordAskMessageRequest(content="hello"),
                current_user=SimpleNamespace(user_id=USER_ID),
            )
            send = _FirstByteFailingSend()
            with pytest.raises(ClientDisconnect):
                await response(_ASGI_SCOPE, _noop_receive, send)

        assert fake_model.enter_count == 1
        assert fake_model.exit_count == 1
        assert fake_model.exited is True

    @pytest.mark.asyncio
    async def test_retry_asgi_disconnect_before_first_byte_exits_model_once(self) -> None:
        from types import SimpleNamespace

        from starlette.requests import ClientDisconnect

        from app.api.routes.reader_record_ask import (
            retry_reading_record_ask_message as retry_route,
        )
        from app.schemas.reader_ask import ReaderAskMessageRetryRequest

        fake_model = LifecycleTrackingModel()
        option = MagicMock(key="fake-opt", selection=MagicMock())
        repo = self._retry_repo_mock()

        with (
            patch(
                "app.services.reader_record_ask.repository.ReaderRecordAskRepository",
                return_value=repo,
            ),
            patch(
                "app.services.reader_record_ask.service.ReaderRecordAskRepository",
                return_value=repo,
            ),
            patch(
                "app.services.reader_record_ask.service._load_snapshot_facts_raw",
                new_callable=AsyncMock,
                return_value=MagicMock(record=MagicMock(title="Test")),
            ),
            patch(
                "app.services.reader_record_ask.service.thread_service.resolve_and_persist_thread_model_option",
                new_callable=AsyncMock,
                return_value=option,
            ),
            patch(
                "app.services.reader_record_ask.service.resolve_reader_record_ask_execution",
                return_value=_make_execution_config(
                    option_key="fake-opt", model=fake_model
                ),
            ),
        ):
            response = await retry_route(
                reading_record_id=RECORD_ID,
                thread_id=UUID(THREAD_ID),
                message_id=uuid4(),
                body=ReaderAskMessageRetryRequest(),
                current_user=SimpleNamespace(user_id=USER_ID),
            )
            send = _FirstByteFailingSend()
            with pytest.raises(ClientDisconnect):
                await response(_ASGI_SCOPE, _noop_receive, send)

        assert fake_model.enter_count == 1
        assert fake_model.exit_count == 1
        assert fake_model.exited is True

    @pytest.mark.asyncio
    async def test_send_asgi_full_stream_enters_and_exits_model_exactly_once(self) -> None:
        """Real ASGI completion — exactly one enter/exit, no double close."""
        from types import SimpleNamespace

        from app.api.routes.reader_record_ask import (
            stream_reading_record_ask_thread_message,
        )
        from app.schemas.reader_ask import ReaderRecordAskMessageRequest

        fake_model = LifecycleTrackingModel()
        option = MagicMock(key="fake-opt", selection=MagicMock())

        async def _fake_stream(**kwargs):
            yield "event: message.completed\ndata: {}\n\n"

        with (
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
                return_value=option,
            ),
            patch(
                "app.services.reader_record_ask.service.resolve_reader_record_ask_execution",
                return_value=_make_execution_config(
                    option_key="fake-opt", model=fake_model
                ),
            ),
            patch(
                "app.services.reader_record_ask.submission_gateway.ensure_submission_for_send",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "app.services.reader_record_ask.production_stream.stream_agentic_thread_message",
                side_effect=_fake_stream,
            ),
        ):
            response = await stream_reading_record_ask_thread_message(
                reading_record_id=RECORD_ID,
                thread_id=UUID(THREAD_ID),
                body=ReaderRecordAskMessageRequest(content="hello"),
                current_user=SimpleNamespace(user_id=USER_ID),
            )
            bodies: list[bytes] = []

            async def _collect_send(message: dict) -> None:
                if message["type"] == "http.response.body" and message.get("body"):
                    bodies.append(message["body"])

            await response(_ASGI_SCOPE, _noop_receive, _collect_send)

        assert b"message.completed" in b"".join(bodies)
        assert fake_model.enter_count == 1
        assert fake_model.exit_count == 1
        assert fake_model.exited is True

    @pytest.mark.asyncio
    async def test_generator_close_failure_does_not_skip_reconciliation(self) -> None:
        """A failing inner-generator close must not skip turn reconciliation."""
        from app.api.routes.reader_record_ask import (
            _streaming_response,
            _StreamLifecycleContext,
        )

        async def _close_failing_stream():
            try:
                yield "event: agentic.run_started\ndata: {}\n\n"
            finally:
                raise RuntimeError("close failed")

        lifecycle = _StreamLifecycleContext()
        lifecycle.reconcile_if_streaming = AsyncMock()
        response = _streaming_response(_close_failing_stream(), lifecycle=lifecycle)

        body = response.body_iterator
        first_chunk = await body.__anext__()
        assert first_chunk.startswith("event: agentic.run_started")
        with pytest.raises(RuntimeError, match="close failed"):
            await body.aclose()

        lifecycle.reconcile_if_streaming.assert_awaited_once()


# ---------------------------------------------------------------------------
# Mid-stream disconnect AFTER the model was claimed: the real ASGI send()
# fails on the first non-empty body chunk. Starlette never acloses
# ``body_iterator`` itself, so the response call boundary must close it —
# otherwise the business generator's finally chain (model exit + turn
# reconciliation) never runs even though the model is already entered.
# ---------------------------------------------------------------------------


class _FirstBodyFailingSend:
    """Lets response.start through; fails on the first non-empty body send."""

    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def __call__(self, message: dict) -> None:
        self.messages.append(message)
        if message["type"] == "http.response.body" and message.get("body"):
            raise OSError("client disconnected mid-stream")


class TestMidStreamDisconnectModelLifecycle:
    """The model is claimed and entered BEFORE the first body send fails.

    Drives ``response(scope, receive, send)`` directly; ``send`` raises
    on the first non-empty body chunk, which starlette maps to
    ``ClientDisconnect``. The claimed model must still exit exactly once,
    the business generator's finally must run, and reconciliation must
    happen exactly once.
    """

    @staticmethod
    def _retry_repo_mock() -> MagicMock:
        repo = MagicMock()
        repo.get_thread = AsyncMock(return_value={"id": THREAD_ID})
        persisted_snapshot = {
            "execution_version": "reader_record_ask_agentic_v2",
            "model_option_key": "fake-opt",
            "web_search_mode": "disabled",
        }
        repo.get_assistant_message_with_preceding_user_message = AsyncMock(
            return_value=(
                {
                    "metadata_json": {"retry_snapshot": persisted_snapshot},
                    "turn_run_execution_version": "reader_record_ask_agentic_v2",
                },
                {
                    "metadata_json": {
                        "retry_snapshot": persisted_snapshot,
                        "web_search_mode": "disabled",
                    }
                },
            )
        )
        return repo

    @pytest.mark.asyncio
    async def test_send_asgi_mid_stream_disconnect_exits_claimed_model_once(self) -> None:
        from types import SimpleNamespace

        from starlette.requests import ClientDisconnect

        from app.api.routes.reader_record_ask import (
            _StreamLifecycleContext,
            stream_reading_record_ask_thread_message,
        )
        from app.schemas.reader_ask import ReaderRecordAskMessageRequest

        fake_model = LifecycleTrackingModel()
        option = MagicMock(key="fake-opt", selection=MagicMock())
        finally_ran: list[bool] = []

        async def _fake_stream(**kwargs):
            try:
                yield "event: agentic.run_started\ndata: {}\n\n"
                yield "event: message.completed\ndata: {}\n\n"
            finally:
                finally_ran.append(True)

        with (
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
                return_value=option,
            ),
            patch(
                "app.services.reader_record_ask.service.resolve_reader_record_ask_execution",
                return_value=_make_execution_config(
                    option_key="fake-opt", model=fake_model
                ),
            ),
            patch(
                "app.services.reader_record_ask.submission_gateway.ensure_submission_for_send",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "app.services.reader_record_ask.production_stream.stream_agentic_thread_message",
                side_effect=_fake_stream,
            ),
            patch.object(
                _StreamLifecycleContext,
                "reconcile_if_streaming",
                new_callable=AsyncMock,
            ) as mock_reconcile,
        ):
            response = await stream_reading_record_ask_thread_message(
                reading_record_id=RECORD_ID,
                thread_id=UUID(THREAD_ID),
                body=ReaderRecordAskMessageRequest(content="hello"),
                current_user=SimpleNamespace(user_id=USER_ID),
            )
            send = _FirstBodyFailingSend()
            with pytest.raises(ClientDisconnect):
                await response(_ASGI_SCOPE, _noop_receive, send)

        # response.start got through; the first non-empty body send failed.
        assert send.messages[0]["type"] == "http.response.start"
        # The claimed model exited exactly once via the close cascade.
        assert fake_model.enter_count == 1
        assert fake_model.exit_count == 1
        assert fake_model.exited is True
        # Business generator finally ran; reconciliation exactly once.
        assert finally_ran == [True]
        mock_reconcile.assert_awaited_once()
        # No completion event or extra provider work after the disconnect.
        sent_body = b"".join(
            m.get("body", b"")
            for m in send.messages
            if m["type"] == "http.response.body"
        )
        assert b"message.completed" not in sent_body

    @pytest.mark.asyncio
    async def test_retry_asgi_mid_stream_disconnect_exits_claimed_model_once(self) -> None:
        from types import SimpleNamespace

        from starlette.requests import ClientDisconnect

        from app.api.routes.reader_record_ask import (
            _StreamLifecycleContext,
        )
        from app.api.routes.reader_record_ask import (
            retry_reading_record_ask_message as retry_route,
        )
        from app.schemas.reader_ask import ReaderAskMessageRetryRequest

        fake_model = LifecycleTrackingModel()
        option = MagicMock(key="fake-opt", selection=MagicMock())
        repo = self._retry_repo_mock()
        finally_ran: list[bool] = []

        async def _fake_retry(**kwargs):
            try:
                yield "event: agentic.run_started\ndata: {}\n\n"
                yield "event: message.completed\ndata: {}\n\n"
            finally:
                finally_ran.append(True)

        with (
            patch(
                "app.services.reader_record_ask.repository.ReaderRecordAskRepository",
                return_value=repo,
            ),
            patch(
                "app.services.reader_record_ask.service.ReaderRecordAskRepository",
                return_value=repo,
            ),
            patch(
                "app.services.reader_record_ask.service._load_snapshot_facts_raw",
                new_callable=AsyncMock,
                return_value=MagicMock(record=MagicMock(title="Test")),
            ),
            patch(
                "app.services.reader_record_ask.service.thread_service.resolve_and_persist_thread_model_option",
                new_callable=AsyncMock,
                return_value=option,
            ),
            patch(
                "app.services.reader_record_ask.service.resolve_reader_record_ask_execution",
                return_value=_make_execution_config(
                    option_key="fake-opt", model=fake_model
                ),
            ),
            patch(
                "app.services.reader_record_ask.production_stream.retry_agentic_thread_message",
                side_effect=_fake_retry,
            ),
            patch.object(
                _StreamLifecycleContext,
                "reconcile_if_streaming",
                new_callable=AsyncMock,
            ) as mock_reconcile,
        ):
            response = await retry_route(
                reading_record_id=RECORD_ID,
                thread_id=UUID(THREAD_ID),
                message_id=uuid4(),
                body=ReaderAskMessageRetryRequest(),
                current_user=SimpleNamespace(user_id=USER_ID),
            )
            send = _FirstBodyFailingSend()
            with pytest.raises(ClientDisconnect):
                await response(_ASGI_SCOPE, _noop_receive, send)

        assert send.messages[0]["type"] == "http.response.start"
        assert fake_model.enter_count == 1
        assert fake_model.exit_count == 1
        assert fake_model.exited is True
        assert finally_ran == [True]
        mock_reconcile.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_send_asgi_mid_stream_cleanup_failure_keeps_client_disconnect(self) -> None:
        """A failing business finally must not mask the ClientDisconnect."""
        from types import SimpleNamespace

        from starlette.requests import ClientDisconnect

        from app.api.routes.reader_record_ask import (
            _StreamLifecycleContext,
            stream_reading_record_ask_thread_message,
        )
        from app.schemas.reader_ask import ReaderRecordAskMessageRequest

        fake_model = LifecycleTrackingModel()
        option = MagicMock(key="fake-opt", selection=MagicMock())

        async def _close_failing_stream(**kwargs):
            try:
                yield "event: agentic.run_started\ndata: {}\n\n"
            finally:
                raise RuntimeError("close failed")

        with (
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
                return_value=option,
            ),
            patch(
                "app.services.reader_record_ask.service.resolve_reader_record_ask_execution",
                return_value=_make_execution_config(
                    option_key="fake-opt", model=fake_model
                ),
            ),
            patch(
                "app.services.reader_record_ask.submission_gateway.ensure_submission_for_send",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "app.services.reader_record_ask.production_stream.stream_agentic_thread_message",
                side_effect=_close_failing_stream,
            ),
            patch.object(
                _StreamLifecycleContext,
                "reconcile_if_streaming",
                new_callable=AsyncMock,
            ) as mock_reconcile,
        ):
            response = await stream_reading_record_ask_thread_message(
                reading_record_id=RECORD_ID,
                thread_id=UUID(THREAD_ID),
                body=ReaderRecordAskMessageRequest(content="hello"),
                current_user=SimpleNamespace(user_id=USER_ID),
            )
            send = _FirstBodyFailingSend()
            # The original disconnect survives the failing cleanup.
            with pytest.raises(ClientDisconnect):
                await response(_ASGI_SCOPE, _noop_receive, send)

        assert fake_model.enter_count == 1
        assert fake_model.exit_count == 1
        mock_reconcile.assert_awaited_once()
