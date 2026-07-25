from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal
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
    ReaderAskThreadListResponse,
    ReaderAskThreadSummary,
    ReaderRecordAskActionConfirmRequest,
    ReaderRecordAskMessageRequest,
)
from app.services.ask_runtime import action_service, stream_service, thread_service
from app.services.reader_orchestration.anchor_gate import load_validated_reading_record_anchor
from app.services.reader_orchestration.repository import ReaderOrchestrationRepository
from app.services.reader_record_ask.execution_config import (
    ReaderRecordAskExecutionConfig,
    ReaderRecordAskExecutionUnavailable,
    resolve_reader_record_ask_execution,
)

logger = logging.getLogger(__name__)


# Retry mode determined during preflight. ``"agentic"`` → use the
# unified execution config + production_stream retry; ``"legacy"`` → use
# stream_service.retry_thread_message. Resolving this before the
# StreamingResponse starts prevents branch drift mid-stream.
RetryMode = Literal["agentic", "legacy"]


@dataclass(slots=True, frozen=True)
class RetryPreparedResult:
    """Result of the retry preflight (mirrors Send's prepared tuple).

    Carries everything ``retry_reading_record_ask_message`` needs to
    stream the retry without re-resolving the persisted option or
    re-building the model. The route awaits the preflight coroutine
    before constructing the StreamingResponse so a config-unavailable
    option surfaces as a real HTTP 503 instead of an SSE error frame.

    For legacy retry (agentic flag off), ``facts`` and ``execution``
    are both ``None`` — ``stream_service.retry_thread_message`` loads
    its own state.
    """

    reading_record_id: UUID
    mode: RetryMode
    facts: object | None
    execution: ReaderRecordAskExecutionConfig | None


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
):
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
):
    parsed_record_id = _parse_uuid(reading_record_id, field="reading_record_id")
    facts = await _load_snapshot_facts(user_id=user_id, reading_record_id=parsed_record_id)
    return await thread_service.reset_reading_record_thread(
        user_id,
        thread_id,
        reading_record_id=parsed_record_id,
        title=facts.record.title or "Ask Claread",
    )


async def _resolve_agentic_execution(option) -> ReaderRecordAskExecutionConfig:
    """Compile a persisted option into a unified execution config.

    ASK-M1: replaces the old ``_build_agentic_model_for_option``. Both
    send and retry paths now call this so the persisted option is the
    single source of truth for the model, the provider completion cap,
    and the host usage limit. Fail-closed: raises a typed 503 — never
    silently substitutes the global default model.
    """
    try:
        return resolve_reader_record_ask_execution(option)
    except ReaderRecordAskExecutionUnavailable as exc:
        logger.warning(
            "agentic_execution_unavailable code=model_unconfigured "
            "model_key=%s reason=%s",
            exc.option_key,
            exc.reason,
        )
        raise HTTPException(
            status_code=503,
            detail={
                "code": "model_unconfigured",
                "message": "Ask Claread model is not configured for the selected option.",
                "model_key": exc.option_key,
            },
        ) from None


async def prepare_reading_record_ask_message(
    *,
    user_id: UUID,
    reading_record_id: str,
    request: ReaderRecordAskMessageRequest,
    thread_id: UUID | None = None,
) -> tuple[UUID, UUID, ReaderRecordAskExecutionConfig | None]:
    """Validate anchor/thread and resolve execution config before StreamingResponse.

    Returns ``(reading_record_id, thread_id, execution)``. Raising here
    yields a real HTTP 4xx/503 (e.g. unknown model key, unconfigured
    provider) instead of an SSE error frame. For the legacy path,
    execution is None — stream_service resolves its own model.
    """
    parsed_record_id = _parse_uuid(reading_record_id, field="reading_record_id")
    await _validate_reading_record_anchor(
        user_id=user_id,
        reading_record_id=parsed_record_id,
        request=request,
    )
    if thread_id is None:
        thread = await _ensure_default_thread(
            user_id=user_id,
            reading_record_id=parsed_record_id,
        )
        resolved_thread_id = _parse_uuid(thread["id"], field="thread id is invalid")
    else:
        resolved_thread_id = thread_id

    if not get_settings().reader_record_ask_agentic_enabled:
        return parsed_record_id, resolved_thread_id, None

    option = await thread_service.resolve_and_persist_thread_model_option(
        user_id=user_id,
        thread_id=resolved_thread_id,
        requested_key=request.model,
        reading_record_id=parsed_record_id,
    )
    execution = await _resolve_agentic_execution(option)
    return parsed_record_id, resolved_thread_id, execution


async def _stream_legacy_or_agentic(
    *,
    user_id: UUID,
    reading_record_id: UUID,
    thread_id: UUID,
    request: ReaderRecordAskMessageRequest,
    execution: ReaderRecordAskExecutionConfig | None = None,
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

    model = execution.model if execution is not None else None
    model_settings = execution.model_settings() if execution is not None else None
    usage_limits = execution.usage_limits if execution is not None else None

    async for chunk in stream_agentic_thread_message(
        user_id=user_id,
        reading_record_id=reading_record_id,
        thread_id=thread_id,
        content=request.content,
        facts=facts,
        request_anchor=request.anchor,
        validated_anchor=None,
        stable_document_id=None,
        model=model,
        model_settings=model_settings,
        usage_limits=usage_limits,
    ):
        yield chunk


async def send_reading_record_ask_message(
    *,
    user_id: UUID,
    reading_record_id: str,
    request: ReaderRecordAskMessageRequest,
    prepared: tuple[UUID, UUID, ReaderRecordAskExecutionConfig | None] | None = None,
) -> AsyncIterator[str]:
    if prepared is None:
        prepared = await prepare_reading_record_ask_message(
            user_id=user_id,
            reading_record_id=reading_record_id,
            request=request,
        )
    parsed_record_id, resolved_thread_id, execution = prepared
    async for chunk in _stream_legacy_or_agentic(
        user_id=user_id,
        reading_record_id=parsed_record_id,
        thread_id=resolved_thread_id,
        request=request,
        execution=execution,
    ):
        yield chunk


async def stream_reading_record_ask_thread_message(
    *,
    user_id: UUID,
    reading_record_id: str,
    thread_id: UUID,
    request: ReaderRecordAskMessageRequest,
    prepared: tuple[UUID, UUID, ReaderRecordAskExecutionConfig | None] | None = None,
) -> AsyncIterator[str]:
    if prepared is None:
        prepared = await prepare_reading_record_ask_message(
            user_id=user_id,
            reading_record_id=reading_record_id,
            request=request,
            thread_id=thread_id,
        )
    parsed_record_id, resolved_thread_id, execution = prepared
    async for chunk in _stream_legacy_or_agentic(
        user_id=user_id,
        reading_record_id=parsed_record_id,
        thread_id=resolved_thread_id,
        request=request,
        execution=execution,
    ):
        yield chunk


async def prepare_reading_record_ask_retry(
    *,
    user_id: UUID,
    reading_record_id: str,
    thread_id: UUID,
) -> RetryPreparedResult:
    """Retry preflight — runs before the StreamingResponse starts.

    Mirrors :func:`prepare_reading_record_ask_message` for the retry
    path. Completes the same five preflight stages so a config-
    unavailable option surfaces as a typed HTTP 503 (or 422 / 400)
    before any SSE byte is written:

    1. ``reading_record_id`` UUID parsing;
    2. agentic feature flag decision (``mode``);
    3. snapshot facts load (only required for the agentic path);
    4. persisted option resolution via ``thread_service``;
    5. ``resolve_reader_record_ask_execution``.

    Legacy retry keeps the original ``stream_service.retry_thread_message``
    behavior, but ``mode`` is fixed here so the generator cannot drift
    branches after the response has started.
    """
    parsed_record_id = _parse_uuid(reading_record_id, field="reading_record_id")
    if not get_settings().reader_record_ask_agentic_enabled:
        # Legacy mode still loads facts for parity with the previous
        # behavior — retry_thread_message does not accept facts but
        # the prior generator called _load_snapshot_facts first to
        # produce the same 404/400 error before streaming.
        await _load_snapshot_facts(user_id=user_id, reading_record_id=parsed_record_id)
        return RetryPreparedResult(
            reading_record_id=parsed_record_id,
            mode="legacy",
            facts=None,
            execution=None,
        )

    # Agentic mode: preflight facts + option + execution config so the
    # generator never re-resolves any of them.
    facts = await _load_snapshot_facts(
        user_id=user_id,
        reading_record_id=parsed_record_id,
    )
    option = await thread_service.resolve_and_persist_thread_model_option(
        user_id=user_id,
        thread_id=thread_id,
        requested_key=None,
        reading_record_id=parsed_record_id,
    )
    execution = await _resolve_agentic_execution(option)
    return RetryPreparedResult(
        reading_record_id=parsed_record_id,
        mode="agentic",
        facts=facts,
        execution=execution,
    )


async def retry_reading_record_ask_message(
    *,
    user_id: UUID,
    reading_record_id: str,
    thread_id: UUID,
    message_id: UUID,
    request: ReaderAskMessageRetryRequest,
    prepared: RetryPreparedResult | None = None,
) -> AsyncIterator[str]:
    """Stream a retry. ``prepared`` must come from the route's preflight.

    The generator must not re-load facts, re-resolve the persisted
    option, or rebuild the execution config — the route has already
    done all three via :func:`prepare_reading_record_ask_retry`. This
    guarantees Send and Retry have identical fail-closed HTTP semantics
    (typed 503 before StreamingResponse) and identical model + budget
    propagation.
    """
    if prepared is None:
        prepared = await prepare_reading_record_ask_retry(
            user_id=user_id,
            reading_record_id=reading_record_id,
            thread_id=thread_id,
        )
    parsed_record_id = prepared.reading_record_id

    if prepared.mode == "legacy":
        async for chunk in stream_service.retry_thread_message(
            user_id=user_id,
            reading_record_id=parsed_record_id,
            thread_id=thread_id,
            message_id=message_id,
            retry_body=request,
        ):
            yield chunk
        return

    # Agentic mode — preflight has already resolved facts + execution.
    assert prepared.execution is not None, "agentic mode requires execution config"
    assert prepared.facts is not None, "agentic mode requires preflight facts"
    from app.services.reader_record_ask.production_stream import (
        retry_agentic_thread_message,
    )

    execution = prepared.execution
    async for chunk in retry_agentic_thread_message(
        user_id=user_id,
        reading_record_id=parsed_record_id,
        thread_id=thread_id,
        message_id=message_id,
        facts=prepared.facts,
        model=execution.model,
        model_settings=execution.model_settings(),
        usage_limits=execution.usage_limits,
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
