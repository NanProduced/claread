from __future__ import annotations

import json
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict

from app.config.settings import get_settings
from app.schemas.reader_ask import (
    ReaderAskActionConfirmResponse,
    ReaderAskDeleteSupplementResponse,
    ReaderAskMessageRetryRequest,
    ReaderAskThreadListResponse,
    ReaderAskThreadSummary,
    ReaderRecordAskActionConfirmRequest,
    ReaderRecordAskMessageRequest,
)
from app.schemas.reader_record_ask_stream import ReaderRecordAskThreadDetail
from app.services.auth.dependencies import AuthUserDep
from app.services.reader_record_ask import service as rr_ask_svc
from app.services.reader_record_ask.citation_navigation import (
    load_live_document_fence,
    resolve_citation_navigation,
)
from app.services.reader_record_ask.repository import ReaderRecordAskRepository

router = APIRouter(tags=["reader-record-ask"])


class CitationNavigateLocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unit_id: str | None = None
    anchor_segment_id: str | None = None
    canonical_text_start_utf16: int | None = None
    canonical_text_end_utf16: int | None = None


class CitationNavigateResponse(BaseModel):
    """Public navigate response — typed location only, no handles/identity."""

    model_config = ConfigDict(extra="forbid")

    status: str
    location: CitationNavigateLocation | None = None
    reason: str | None = None


def _is_dev_error_mode() -> bool:
    return get_settings().app_env != "production"


def _streaming_response(generator) -> StreamingResponse:
    async def event_stream():
        try:
            async for chunk in generator:
                yield chunk
        except HTTPException as exc:
            yield (
                f"event: error\ndata: {json.dumps({'code': str(exc.status_code), 'detail': exc.detail}, ensure_ascii=False)}\n\n"
            )
        except Exception as exc:
            if _is_dev_error_mode():
                payload = {"code": "READER_ASK_FAILED", "detail": str(exc)}
            else:
                payload = {
                    "code": "READER_ASK_FAILED",
                    "detail": "Ask Claread 暂时不可用。",
                    "user_message": "Ask Claread 暂时不可用。",
                }
            yield (
                "event: error\ndata: "
                f"{json.dumps(payload, ensure_ascii=False)}\n\n"
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    "/reader/records/{reading_record_id}/ask/threads",
    response_model=ReaderAskThreadListResponse,
    summary="List Reading Record Ask threads",
)
async def list_reading_record_ask_threads(
    reading_record_id: str,
    current_user: AuthUserDep,
) -> ReaderAskThreadListResponse:
    return await rr_ask_svc.list_reading_record_ask_threads(
        user_id=UUID(current_user.user_id),
        reading_record_id=reading_record_id,
    )


@router.post(
    "/reader/records/{reading_record_id}/ask/threads/default",
    response_model=ReaderAskThreadSummary,
    summary="Create or get the default Reading Record Ask thread",
)
async def create_default_reading_record_ask_thread(
    reading_record_id: str,
    current_user: AuthUserDep,
) -> ReaderAskThreadSummary:
    return await rr_ask_svc.create_default_reading_record_ask_thread(
        user_id=UUID(current_user.user_id),
        reading_record_id=reading_record_id,
    )


@router.get(
    "/reader/records/{reading_record_id}/ask/threads/{thread_id}",
    response_model=ReaderRecordAskThreadDetail,
    response_model_exclude_none=True,
    summary="Get Reading Record Ask thread detail",
)
async def get_reading_record_ask_thread(
    reading_record_id: str,
    thread_id: UUID,
    current_user: AuthUserDep,
) -> ReaderRecordAskThreadDetail:
    return await rr_ask_svc.get_reading_record_ask_thread(
        user_id=UUID(current_user.user_id),
        reading_record_id=reading_record_id,
        thread_id=thread_id,
    )


@router.post(
    "/reader/records/{reading_record_id}/ask/messages/{message_id}/citations/{citation_id}/navigate",
    response_model=CitationNavigateResponse,
    summary="Securely resolve a public citation_id for article navigation",
)
async def navigate_reading_record_ask_citation(
    reading_record_id: str,
    message_id: UUID,
    citation_id: str,
    current_user: AuthUserDep,
) -> CitationNavigateResponse:
    """Client path submits only message_id + citation_id.

    LiveDocumentFence is loaded from authoritative reading-record /
    stable-document snapshot data. Client bodies cannot supply or override
    base_id / generation / stable_document_id. Response never includes
    handles, internal identity, or raw evidence.
    """
    user_id = UUID(current_user.user_id)
    record_id = UUID(reading_record_id)

    live_fence = await load_live_document_fence(
        user_id=user_id,
        reading_record_id=record_id,
    )
    if live_fence is None:
        return CitationNavigateResponse(
            status="unavailable",
            reason="record_fence_unavailable",
        )

    repo = ReaderRecordAskRepository()
    row = await repo.get_message_restricted_evidence_for_navigation(
        user_id=user_id,
        reading_record_id=record_id,
        message_id=message_id,
    )
    if row is None:
        return CitationNavigateResponse(status="not_found", reason="message_not_found")
    if row.get("final_status") != "ok":
        return CitationNavigateResponse(
            status="unavailable",
            reason="message_not_completed",
        )

    result = resolve_citation_navigation(
        citation_id=citation_id,
        restricted_evidence=row.get("resolved_evidence_json"),
        live_fence=live_fence,
    )
    location = None
    if result.location is not None:
        location = CitationNavigateLocation(
            unit_id=result.location.unit_id,
            anchor_segment_id=result.location.anchor_segment_id,
            canonical_text_start_utf16=result.location.canonical_text_start_utf16,
            canonical_text_end_utf16=result.location.canonical_text_end_utf16,
        )
    return CitationNavigateResponse(
        status=result.status,
        location=location,
        reason=result.reason,
    )


@router.post(
    "/reader/records/{reading_record_id}/ask/threads/{thread_id}/reset",
    response_model=ReaderRecordAskThreadDetail,
    response_model_exclude_none=True,
    summary="Reset the Reading Record Ask thread",
)
async def reset_reading_record_ask_thread(
    reading_record_id: str,
    thread_id: UUID,
    current_user: AuthUserDep,
) -> ReaderRecordAskThreadDetail:
    return await rr_ask_svc.reset_reading_record_ask_thread(
        user_id=UUID(current_user.user_id),
        reading_record_id=reading_record_id,
        thread_id=thread_id,
    )


@router.post(
    "/reader/records/{reading_record_id}/ask/messages",
    summary="Send an Ask message on a Reading Record",
)
async def send_reading_record_ask_message(
    reading_record_id: str,
    body: ReaderRecordAskMessageRequest,
    current_user: AuthUserDep,
) -> StreamingResponse:
    user_id = UUID(current_user.user_id)
    prepared = await rr_ask_svc.prepare_reading_record_ask_message(
        user_id=user_id,
        reading_record_id=reading_record_id,
        request=body,
    )
    return _streaming_response(
        rr_ask_svc.send_reading_record_ask_message(
            user_id=user_id,
            reading_record_id=reading_record_id,
            request=body,
            prepared=prepared,
        )
    )


@router.post(
    "/reader/records/{reading_record_id}/ask/threads/{thread_id}/messages/stream",
    summary="Stream a message on a Reading Record Ask thread",
)
async def stream_reading_record_ask_thread_message(
    reading_record_id: str,
    thread_id: UUID,
    body: ReaderRecordAskMessageRequest,
    current_user: AuthUserDep,
) -> StreamingResponse:
    user_id = UUID(current_user.user_id)
    prepared = await rr_ask_svc.prepare_reading_record_ask_message(
        user_id=user_id,
        reading_record_id=reading_record_id,
        request=body,
        thread_id=thread_id,
    )
    return _streaming_response(
        rr_ask_svc.stream_reading_record_ask_thread_message(
            user_id=user_id,
            reading_record_id=reading_record_id,
            thread_id=thread_id,
            request=body,
            prepared=prepared,
        )
    )


@router.post(
    "/reader/records/{reading_record_id}/ask/threads/{thread_id}/messages/{message_id}/retry/stream",
    summary="Retry a Reading Record Ask assistant message",
)
async def retry_reading_record_ask_message(
    reading_record_id: str,
    thread_id: UUID,
    message_id: UUID,
    body: ReaderAskMessageRetryRequest,
    current_user: AuthUserDep,
) -> StreamingResponse:
    # ASK-M1-R1: run the retry preflight BEFORE constructing the
    # StreamingResponse so a config-unavailable option (or unknown
    # model key, or missing record) surfaces as a real HTTP 503 / 422 /
    # 400 instead of an SSE error frame. The generator never re-
    # resolves facts / option / model — it reuses ``prepared``.
    prepared = await rr_ask_svc.prepare_reading_record_ask_retry(
        user_id=UUID(current_user.user_id),
        reading_record_id=reading_record_id,
        thread_id=thread_id,
    )
    return _streaming_response(
        rr_ask_svc.retry_reading_record_ask_message(
            user_id=UUID(current_user.user_id),
            reading_record_id=reading_record_id,
            thread_id=thread_id,
            message_id=message_id,
            request=body,
            prepared=prepared,
        )
    )


@router.post(
    "/reader/records/{reading_record_id}/ask/actions/{action_id}/confirm",
    response_model=ReaderAskActionConfirmResponse,
    summary="Confirm a Reading Record Ask action proposal",
)
async def confirm_reading_record_ask_action(
    reading_record_id: str,
    action_id: str,
    body: ReaderRecordAskActionConfirmRequest,
    current_user: AuthUserDep,
) -> ReaderAskActionConfirmResponse:
    return await rr_ask_svc.confirm_reading_record_ask_action(
        user_id=UUID(current_user.user_id),
        reading_record_id=reading_record_id,
        action_id=action_id,
        request=body,
    )


@router.post(
    "/reader/records/{reading_record_id}/ask/threads/{thread_id}/actions/{action_id}/confirm",
    response_model=ReaderAskActionConfirmResponse,
    summary="Confirm a Reading Record Ask thread action proposal",
)
async def confirm_reading_record_ask_thread_action(
    reading_record_id: str,
    thread_id: UUID,
    action_id: str,
    body: ReaderRecordAskActionConfirmRequest,
    current_user: AuthUserDep,
) -> ReaderAskActionConfirmResponse:
    return await rr_ask_svc.confirm_reading_record_ask_thread_action(
        user_id=UUID(current_user.user_id),
        reading_record_id=reading_record_id,
        thread_id=thread_id,
        action_id=action_id,
        request=body,
    )


@router.delete(
    "/reader/records/{reading_record_id}/ask/supplements/{supplement_id}",
    response_model=ReaderAskDeleteSupplementResponse,
    summary="Delete a Reading Record Ask supplement",
)
async def delete_reading_record_ask_supplement(
    reading_record_id: str,
    supplement_id: UUID,
    current_user: AuthUserDep,
) -> ReaderAskDeleteSupplementResponse:
    return await rr_ask_svc.delete_reading_record_ask_supplement(
        user_id=UUID(current_user.user_id),
        reading_record_id=reading_record_id,
        supplement_id=supplement_id,
    )
