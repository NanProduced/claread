from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException

from app.contracts.anchor_validation import (
    ANCHOR_RECORD_ID_MISMATCH,
    READING_RECORD_NOT_FOUND,
    READING_RECORD_SNAPSHOT_INVALID,
    AnchorValidationError,
)
from app.database import connection as db_connect
from app.schemas.reader_ask import (
    ReaderRecordAskActionConfirmRequest,
    ReaderRecordAskMessageRequest,
    ReaderRecordAskPendingResponse,
)
from app.services.reader_orchestration.anchor_gate import (
    load_validated_reading_record_anchor,
)
from app.services.reader_orchestration.repository import (
    ReaderOrchestrationRepository,
)


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


async def _ensure_reading_record_snapshot_access(
    *,
    conn,
    repository: ReaderOrchestrationRepository,
    user_id: UUID,
    reading_record_id: UUID,
) -> None:
    try:
        await repository.load_snapshot_facts(
            conn,
            record_id=reading_record_id,
            user_id=user_id,
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


async def send_reading_record_ask_message(
    *,
    user_id: UUID,
    reading_record_id: str,
    request: ReaderRecordAskMessageRequest,
) -> ReaderRecordAskPendingResponse:
    """D6-A6: validate a Reading Record Ask message, but do not execute it.

    The anchor is validated against the current Reading Record / base / unit /
    anchor segment facts when provided. The legacy ``reader_ask_threads`` and
    ``analysis_record_id`` paths are never touched.
    """
    parsed_record_id = _parse_uuid(reading_record_id, field="reading_record_id")
    anchor_record_id: UUID | None = None
    if request.anchor is not None:
        anchor_record_id = _parse_uuid(
            request.anchor.record_id,
            field="anchor.record_id",
        )
        if anchor_record_id != parsed_record_id:
            raise _reading_record_error(
                code=ANCHOR_RECORD_ID_MISMATCH,
                field="anchor.record_id",
                message=(
                    "anchor.record_id does not match the route reading_record_id"
                ),
            )

    repository = ReaderOrchestrationRepository()

    async with db_connect.acquire_connection() as conn:
        if request.anchor is None:
            await _ensure_reading_record_snapshot_access(
                conn=conn,
                repository=repository,
                user_id=user_id,
                reading_record_id=parsed_record_id,
            )
        else:
            try:
                await load_validated_reading_record_anchor(
                    conn,
                    repository=repository,
                    user_id=user_id,
                    anchor=request.anchor,
                )
            except AnchorValidationError as exc:
                raise _reading_record_error(
                    code=exc.code,
                    field="anchor",
                    message=exc.message,
                ) from exc

    # D6-A6 spike: execution is intentionally disabled.
    return ReaderRecordAskPendingResponse(
        status="pending",
        code="reader_record_ask_execution_pending",
        message="Reading Record Ask execution is not enabled yet.",
        reading_record_id=reading_record_id,
    )


async def confirm_reading_record_ask_action(
    *,
    user_id: UUID,
    reading_record_id: str,
    action_id: str,
    request: ReaderRecordAskActionConfirmRequest,
) -> ReaderRecordAskPendingResponse:
    """D6-A6: stable pending response for Reading Record Ask action confirm.

    This intentionally does NOT call the legacy ``reader_ask.service``
    ``confirm_action`` and does NOT write to ``reader_ask_threads``,
    ``reader_ask_turn_runs`` or ``reader_ask_supplements``.
    """
    _ = user_id, request
    _parse_uuid(reading_record_id, field="reading_record_id")

    return ReaderRecordAskPendingResponse(
        status="pending",
        code="reader_record_ask_confirm_pending",
        message="Reading Record Ask action confirmation is not enabled yet.",
        reading_record_id=reading_record_id,
        action_id=action_id,
    )
