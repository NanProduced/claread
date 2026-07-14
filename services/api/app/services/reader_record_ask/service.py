from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import HTTPException

from app.config.settings import get_settings
from app.contracts.anchor_validation import (
    ANCHOR_RECORD_ID_MISMATCH,
    READING_RECORD_NOT_FOUND,
    READING_RECORD_SNAPSHOT_INVALID,
    AnchorValidationError,
)
from app.database import connection as db_connect
from app.schemas.reader_ask import (
    ReaderAskActionConfirmRequest,
    ReaderAskActionConfirmResponse,
    ReaderAskDeleteSupplementResponse,
    ReaderAskMessageRetryRequest,
    ReaderAskThreadDetail,
    ReaderAskThreadListResponse,
    ReaderAskThreadSummary,
    ReaderRecordAskActionConfirmRequest,
    ReaderRecordAskMessageRequest,
)
from app.services.ask_runtime import action_service, stream_service, thread_service
from app.services.reader_orchestration.anchor_gate import load_validated_reading_record_anchor
from app.services.reader_orchestration.repository import ReaderOrchestrationRepository


def _parse_uuid(value: str, *, field: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_uuid",
                "field": field,
                "message": f"{field} must be a UUID",
            },
        ) from exc


def _reading_record_error(
    *,
    code: str,
    field: str,
    message: str,
) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={
            "code": code,
            "field": field,
            "message": message,
        },
    )


async def _load_snapshot_facts_raw(
    *,
    user_id: UUID,
    reading_record_id: UUID,
) -> object:
    repository = ReaderOrchestrationRepository()
    async with db_connect.acquire_connection() as conn:
        return await repository.load_snapshot_facts(
            conn,
            record_id=reading_record_id,
            user_id=user_id,
        )


async def _load_snapshot_facts(
    *,
    user_id: UUID,
    reading_record_id: UUID,
) -> object:
    try:
        return await _load_snapshot_facts_raw(
            user_id=user_id,
            reading_record_id=reading_record_id,
        )
    except LookupError as exc:
        raise _reading_record_error(
            code=READING_RECORD_NOT_FOUND,
            field="reading_record_id",
            message=str(exc),
        ) from exc
    except ValueError as exc:
        raise _reading_record_error(
            code=READING_RECORD_SNAPSHOT_INVALID,
            field="reading_record_id",
            message=str(exc),
        ) from exc


async def _load_validated_anchor_raw(
    *,
    user_id: UUID,
    anchor: object,
) -> None:
    repository = ReaderOrchestrationRepository()
    async with db_connect.acquire_connection() as conn:
        await load_validated_reading_record_anchor(
            conn,
            repository=repository,
            user_id=user_id,
            anchor=anchor,
        )


async def _validate_reading_record_anchor(
    *,
    user_id: UUID,
    reading_record_id: UUID,
    request: ReaderRecordAskMessageRequest,
) -> None:
    if request.anchor is None:
        await _load_snapshot_facts(user_id=user_id, reading_record_id=reading_record_id)
        return
    anchor_record_id = _parse_uuid(request.anchor.record_id, field="anchor.record_id")
    if anchor_record_id != reading_record_id:
        raise _reading_record_error(
            code=ANCHOR_RECORD_ID_MISMATCH,
            field="anchor.record_id",
            message="anchor.record_id does not match the route reading_record_id",
        )
    try:
        await _load_validated_anchor_raw(
            user_id=user_id,
            anchor=request.anchor,
        )
    except AnchorValidationError as exc:
        raise _reading_record_error(
            code=exc.code,
            field="anchor",
            message=exc.message,
        ) from exc


async def _ensure_default_thread(
    *,
    user_id: UUID,
    reading_record_id: UUID,
) -> dict[str, str]:
    facts = await _load_snapshot_facts(user_id=user_id, reading_record_id=reading_record_id)
    thread = await thread_service.ensure_default_reading_record_thread(
        user_id,
        reading_record_id,
        title=facts.record.title or "Ask Claread",
    )
    return {"id": str(thread["id"]), "title": str(thread.get("title") or "Ask Claread")}


async def list_reading_record_ask_threads(
    *,
    user_id: UUID,
    reading_record_id: str,
) -> ReaderAskThreadListResponse:
    parsed_record_id = _parse_uuid(reading_record_id, field="reading_record_id")
    await _load_snapshot_facts(user_id=user_id, reading_record_id=parsed_record_id)
    return await thread_service.list_reading_record_threads(user_id, parsed_record_id)


async def create_default_reading_record_ask_thread(
    *,
    user_id: UUID,
    reading_record_id: str,
) -> ReaderAskThreadSummary:
    parsed_record_id = _parse_uuid(reading_record_id, field="reading_record_id")
    facts = await _load_snapshot_facts(user_id=user_id, reading_record_id=parsed_record_id)
    thread = await thread_service.ensure_default_reading_record_thread(
        user_id,
        parsed_record_id,
        title=facts.record.title or "Ask Claread",
    )
    return ReaderAskThreadSummary.model_validate(thread_service._thread_summary_payload(thread))


async def get_reading_record_ask_thread(
    *,
    user_id: UUID,
    reading_record_id: str,
    thread_id: UUID,
) -> ReaderAskThreadDetail:
    parsed_record_id = _parse_uuid(reading_record_id, field="reading_record_id")
    return await thread_service.get_reading_record_thread_detail(
        user_id,
        thread_id,
        reading_record_id=parsed_record_id,
    )


async def reset_reading_record_ask_thread(
    *,
    user_id: UUID,
    reading_record_id: str,
    thread_id: UUID,
) -> ReaderAskThreadDetail:
    parsed_record_id = _parse_uuid(reading_record_id, field="reading_record_id")
    facts = await _load_snapshot_facts(user_id=user_id, reading_record_id=parsed_record_id)
    return await thread_service.reset_reading_record_thread(
        user_id,
        thread_id,
        reading_record_id=parsed_record_id,
        title=facts.record.title or "Ask Claread",
    )


async def _stream_legacy_or_agentic(
    *,
    user_id: UUID,
    reading_record_id: UUID,
    thread_id: UUID,
    request: ReaderRecordAskMessageRequest,
) -> AsyncIterator[str]:
    """Dispatch to agentic path when flag is on; never fall back on agentic failure."""
    if not get_settings().reader_record_ask_agentic_enabled:
        async for chunk in stream_service.stream_thread_message(
            user_id=user_id,
            reading_record_id=reading_record_id,
            thread_id=thread_id,
            request=request,
        ):
            yield chunk
        return

    # Agentic path: re-load facts for envelope (validation already done).
    facts = await _load_snapshot_facts(
        user_id=user_id,
        reading_record_id=reading_record_id,
    )
    from app.services.reader_record_ask.production_stream import (
        stream_agentic_thread_message,
    )

    async for chunk in stream_agentic_thread_message(
        user_id=user_id,
        reading_record_id=reading_record_id,
        thread_id=thread_id,
        content=request.content,
        facts=facts,
        request_anchor=request.anchor,
        validated_anchor=None,
        stable_document_id=None,
    ):
        yield chunk


async def send_reading_record_ask_message(
    *,
    user_id: UUID,
    reading_record_id: str,
    request: ReaderRecordAskMessageRequest,
) -> AsyncIterator[str]:
    parsed_record_id = _parse_uuid(reading_record_id, field="reading_record_id")
    await _validate_reading_record_anchor(
        user_id=user_id,
        reading_record_id=parsed_record_id,
        request=request,
    )
    thread = await _ensure_default_thread(user_id=user_id, reading_record_id=parsed_record_id)
    async for chunk in _stream_legacy_or_agentic(
        user_id=user_id,
        reading_record_id=parsed_record_id,
        thread_id=_parse_uuid(thread["id"], field="thread id is invalid"),
        request=request,
    ):
        yield chunk


async def stream_reading_record_ask_thread_message(
    *,
    user_id: UUID,
    reading_record_id: str,
    thread_id: UUID,
    request: ReaderRecordAskMessageRequest,
) -> AsyncIterator[str]:
    parsed_record_id = _parse_uuid(reading_record_id, field="reading_record_id")
    await _validate_reading_record_anchor(
        user_id=user_id,
        reading_record_id=parsed_record_id,
        request=request,
    )
    async for chunk in _stream_legacy_or_agentic(
        user_id=user_id,
        reading_record_id=parsed_record_id,
        thread_id=thread_id,
        request=request,
    ):
        yield chunk


async def retry_reading_record_ask_message(
    *,
    user_id: UUID,
    reading_record_id: str,
    thread_id: UUID,
    message_id: UUID,
    request: ReaderAskMessageRetryRequest,
) -> AsyncIterator[str]:
    parsed_record_id = _parse_uuid(reading_record_id, field="reading_record_id")
    await _load_snapshot_facts(user_id=user_id, reading_record_id=parsed_record_id)
    async for chunk in stream_service.retry_thread_message(
        user_id=user_id,
        reading_record_id=parsed_record_id,
        thread_id=thread_id,
        message_id=message_id,
        retry_body=request,
    ):
        yield chunk


async def confirm_reading_record_ask_action(
    *,
    user_id: UUID,
    reading_record_id: str,
    action_id: str,
    request: ReaderRecordAskActionConfirmRequest,
) -> ReaderAskActionConfirmResponse:
    parsed_record_id = _parse_uuid(reading_record_id, field="reading_record_id")
    threads = await thread_service.list_reading_record_threads(user_id, parsed_record_id)
    target_thread_id = next((UUID(item.id) for item in threads.items if item.is_default), None)
    if target_thread_id is None:
        raise HTTPException(status_code=404, detail="Reader ask action proposal not found")
    return await action_service.confirm_action(
        user_id=user_id,
        thread_id=target_thread_id,
        action_id=action_id,
        body=ReaderAskActionConfirmRequest(confirmed=request.confirmed),
        expected_reading_record_id=parsed_record_id,
    )


async def confirm_reading_record_ask_thread_action(
    *,
    user_id: UUID,
    reading_record_id: str,
    thread_id: UUID,
    action_id: str,
    request: ReaderRecordAskActionConfirmRequest,
) -> ReaderAskActionConfirmResponse:
    parsed_record_id = _parse_uuid(reading_record_id, field="reading_record_id")
    return await action_service.confirm_action(
        user_id=user_id,
        thread_id=thread_id,
        action_id=action_id,
        body=ReaderAskActionConfirmRequest(confirmed=request.confirmed),
        expected_reading_record_id=parsed_record_id,
    )


async def delete_reading_record_ask_supplement(
    *,
    user_id: UUID,
    reading_record_id: str,
    supplement_id: UUID,
) -> ReaderAskDeleteSupplementResponse:
    parsed_record_id = _parse_uuid(reading_record_id, field="reading_record_id")
    return await action_service.delete_supplement(
        user_id=user_id,
        supplement_id=supplement_id,
        expected_reading_record_id=parsed_record_id,
    )
