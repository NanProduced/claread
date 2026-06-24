from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from app.schemas.reader_ask import (
    ReaderRecordAskActionConfirmRequest,
    ReaderRecordAskMessageRequest,
    ReaderRecordAskPendingResponse,
)
from app.services.auth.dependencies import AuthUserDep
from app.services.reader_record_ask import service as rr_ask_svc

router = APIRouter(tags=["reader-record-ask"])


@router.post(
    "/reader/records/{reading_record_id}/ask/messages",
    status_code=409,
    response_model=ReaderRecordAskPendingResponse,
    summary="Send an Ask message on a Reading Record (D6-A6 spike)",
)
async def send_reading_record_ask_message(
    reading_record_id: str,
    body: ReaderRecordAskMessageRequest,
    current_user: AuthUserDep,
) -> ReaderRecordAskPendingResponse:
    return await rr_ask_svc.send_reading_record_ask_message(
        user_id=UUID(current_user.user_id),
        reading_record_id=reading_record_id,
        request=body,
    )


@router.post(
    "/reader/records/{reading_record_id}/ask/actions/{action_id}/confirm",
    status_code=409,
    response_model=ReaderRecordAskPendingResponse,
    summary="Confirm a Reading Record Ask action proposal (D6-A6 spike)",
)
async def confirm_reading_record_ask_action(
    reading_record_id: str,
    action_id: str,
    body: ReaderRecordAskActionConfirmRequest,
    current_user: AuthUserDep,
) -> ReaderRecordAskPendingResponse:
    return await rr_ask_svc.confirm_reading_record_ask_action(
        user_id=UUID(current_user.user_id),
        reading_record_id=reading_record_id,
        action_id=action_id,
        request=body,
    )
