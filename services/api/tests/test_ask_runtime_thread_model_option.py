"""Direct unit tests for ask_runtime thread model selection helper."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from app.services.ask_runtime import thread_service

USER_ID = UUID("00000000-0000-0000-0000-000000000001")
THREAD_ID = UUID("00000000-0000-0000-0000-0000000000c6")
READING_RECORD_ID = UUID("00000000-0000-0000-0000-0000000000a6")
OTHER_RECORD_ID = UUID("00000000-0000-0000-0000-0000000000a7")


def _option(
    *,
    key: str,
    used_fallback: bool = False,
    requested_key: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        key=key,
        used_fallback=used_fallback,
        requested_key=requested_key,
        selection=object(),
    )


@pytest.mark.asyncio
async def test_history_glm_standard_persists_flash() -> None:
    thread = {
        "id": str(THREAD_ID),
        "selected_model_key": "glm-standard",
        "record_scope": "reading_record",
        "reading_record_id": str(READING_RECORD_ID),
    }
    flash = _option(
        key="deepseek-v4-flash",
        used_fallback=True,
        requested_key="glm-standard",
    )
    update = AsyncMock(return_value={**thread, "selected_model_key": "deepseek-v4-flash"})

    with (
        patch(
            "app.services.ask_runtime.thread_service.repo.get_thread",
            new_callable=AsyncMock,
            return_value=thread,
        ),
        patch(
            "app.services.ask_runtime.thread_service._resolve_reader_ask_model_option_or_422",
            return_value=flash,
        ) as mock_resolve,
        patch(
            "app.services.ask_runtime.thread_service.repo.update_thread_selected_model",
            update,
        ),
    ):
        option = await thread_service.resolve_and_persist_thread_model_option(
            user_id=USER_ID,
            thread_id=THREAD_ID,
            requested_key=None,
            reading_record_id=READING_RECORD_ID,
        )

    assert option.key == "deepseek-v4-flash"
    mock_resolve.assert_called_once_with(
        selected_key="glm-standard",
        strict=False,
    )
    update.assert_awaited_once_with(
        USER_ID,
        THREAD_ID,
        selected_model_key="deepseek-v4-flash",
    )


@pytest.mark.asyncio
async def test_explicit_deepseek_pro_persists() -> None:
    thread = {
        "id": str(THREAD_ID),
        "selected_model_key": "deepseek-v4-flash",
        "record_scope": "reading_record",
        "reading_record_id": str(READING_RECORD_ID),
    }
    pro = _option(key="deepseek-pro")
    update = AsyncMock(return_value={**thread, "selected_model_key": "deepseek-pro"})

    with (
        patch(
            "app.services.ask_runtime.thread_service.repo.get_thread",
            new_callable=AsyncMock,
            return_value=thread,
        ),
        patch(
            "app.services.ask_runtime.thread_service._resolve_reader_ask_model_option_or_422",
            return_value=pro,
        ) as mock_resolve,
        patch(
            "app.services.ask_runtime.thread_service.repo.update_thread_selected_model",
            update,
        ),
    ):
        option = await thread_service.resolve_and_persist_thread_model_option(
            user_id=USER_ID,
            thread_id=THREAD_ID,
            requested_key="deepseek-pro",
            reading_record_id=READING_RECORD_ID,
        )

    assert option.key == "deepseek-pro"
    mock_resolve.assert_called_once_with(
        selected_key="deepseek-pro",
        strict=True,
    )
    update.assert_awaited_once_with(
        USER_ID,
        THREAD_ID,
        selected_model_key="deepseek-pro",
    )


@pytest.mark.asyncio
async def test_same_key_does_not_rewrite() -> None:
    thread = {
        "id": str(THREAD_ID),
        "selected_model_key": "deepseek-v4-flash",
        "record_scope": "reading_record",
        "reading_record_id": str(READING_RECORD_ID),
    }
    flash = _option(key="deepseek-v4-flash")
    update = AsyncMock()

    with (
        patch(
            "app.services.ask_runtime.thread_service.repo.get_thread",
            new_callable=AsyncMock,
            return_value=thread,
        ),
        patch(
            "app.services.ask_runtime.thread_service._resolve_reader_ask_model_option_or_422",
            return_value=flash,
        ),
        patch(
            "app.services.ask_runtime.thread_service.repo.update_thread_selected_model",
            update,
        ),
    ):
        option = await thread_service.resolve_and_persist_thread_model_option(
            user_id=USER_ID,
            thread_id=THREAD_ID,
            requested_key=None,
            reading_record_id=READING_RECORD_ID,
        )

    assert option.key == "deepseek-v4-flash"
    update.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_same_key_does_not_rewrite() -> None:
    thread = {
        "id": str(THREAD_ID),
        "selected_model_key": "deepseek-pro",
        "record_scope": "reading_record",
        "reading_record_id": str(READING_RECORD_ID),
    }
    pro = _option(key="deepseek-pro")
    update = AsyncMock()

    with (
        patch(
            "app.services.ask_runtime.thread_service.repo.get_thread",
            new_callable=AsyncMock,
            return_value=thread,
        ),
        patch(
            "app.services.ask_runtime.thread_service._resolve_reader_ask_model_option_or_422",
            return_value=pro,
        ),
        patch(
            "app.services.ask_runtime.thread_service.repo.update_thread_selected_model",
            update,
        ),
    ):
        option = await thread_service.resolve_and_persist_thread_model_option(
            user_id=USER_ID,
            thread_id=THREAD_ID,
            requested_key="deepseek-pro",
            reading_record_id=READING_RECORD_ID,
        )

    assert option.key == "deepseek-pro"
    update.assert_not_awaited()


@pytest.mark.asyncio
async def test_reading_record_scope_mismatch_does_not_update() -> None:
    thread = {
        "id": str(THREAD_ID),
        "selected_model_key": "glm-standard",
        "record_scope": "reading_record",
        "reading_record_id": str(OTHER_RECORD_ID),
    }
    update = AsyncMock()

    with (
        patch(
            "app.services.ask_runtime.thread_service.repo.get_thread",
            new_callable=AsyncMock,
            return_value=thread,
        ),
        patch(
            "app.services.ask_runtime.thread_service.repo.update_thread_selected_model",
            update,
        ),
    ):
        with pytest.raises(HTTPException) as excinfo:
            await thread_service.resolve_and_persist_thread_model_option(
                user_id=USER_ID,
                thread_id=THREAD_ID,
                requested_key=None,
                reading_record_id=READING_RECORD_ID,
            )

    assert excinfo.value.status_code == 404
    update.assert_not_awaited()


@pytest.mark.asyncio
async def test_analysis_scope_thread_rejected_for_reading_record() -> None:
    thread = {
        "id": str(THREAD_ID),
        "selected_model_key": "deepseek-v4-flash",
        "record_scope": "analysis",
        "reading_record_id": None,
        "record_id": str(uuid4()),
    }
    update = AsyncMock()

    with (
        patch(
            "app.services.ask_runtime.thread_service.repo.get_thread",
            new_callable=AsyncMock,
            return_value=thread,
        ),
        patch(
            "app.services.ask_runtime.thread_service.repo.update_thread_selected_model",
            update,
        ),
    ):
        with pytest.raises(HTTPException) as excinfo:
            await thread_service.resolve_and_persist_thread_model_option(
                user_id=USER_ID,
                thread_id=THREAD_ID,
                requested_key="deepseek-pro",
                reading_record_id=READING_RECORD_ID,
            )

    assert excinfo.value.status_code == 404
    update.assert_not_awaited()
