from __future__ import annotations

from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Query

from app.schemas.reader_orchestration import (
    ReaderEventPollResponse,
    ReaderEventResponse,
    ReaderPlainTextSubmitRequest,
    ReaderPlainTextSubmitResponse,
    ReaderPlateSnapshot,
)
from app.services.auth.dependencies import AuthUserDep
from app.services.reader_orchestration.article_ready_service import (
    ArticleReadyPersistenceService,
    PlainTextArticleReadySubmitRequest,
)
from app.services.reader_orchestration.event_runtime import ReaderEventRuntime
from app.services.reader_orchestration.orchestrator import ReaderOrchestrator

router = APIRouter(prefix="/reader", tags=["reader"])


@router.post(
    "/records/plain-text",
    response_model=ReaderPlainTextSubmitResponse,
    summary="Create a reader record from low-risk plain text input",
)
async def submit_reader_plain_text(
    body: ReaderPlainTextSubmitRequest,
    current_user: AuthUserDep,
) -> ReaderPlainTextSubmitResponse:
    orchestrator = ReaderOrchestrator()
    try:
        result = await orchestrator.submit_plain_text_and_bootstrap_translation(
            PlainTextArticleReadySubmitRequest(
                user_id=UUID(current_user.user_id),
                plain_text=body.plain_text,
                title=body.title,
                language=body.language,
                source_metadata=body.source_metadata,
                client_record_id=body.client_record_id,
            )
        )
    except asyncpg.UniqueViolationError as exc:
        if exc.constraint_name == "uq_reading_records_user_client_active":
            raise HTTPException(
                status_code=409,
                detail="client_record_id already exists for this user",
            ) from exc
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return ReaderPlainTextSubmitResponse(
        record_id=str(result.record_id),
        base_id=str(result.base_id),
        article_ready_sequence=result.article_ready_sequence,
        snapshot=result.snapshot,
    )


@router.get(
    "/records/{record_id}/snapshot",
    response_model=ReaderPlateSnapshot,
    summary="Load the current ReaderPlateSnapshot from DB facts",
)
async def get_reader_snapshot(
    record_id: UUID,
    current_user: AuthUserDep,
) -> ReaderPlateSnapshot:
    service = ArticleReadyPersistenceService()
    try:
        return await service.load_snapshot(
            record_id=record_id,
            user_id=UUID(current_user.user_id),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Reader record not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/records/{record_id}/events",
    response_model=ReaderEventPollResponse,
    summary="Poll committed reader events after a sequence cursor",
)
async def poll_reader_events(
    record_id: UUID,
    current_user: AuthUserDep,
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
) -> ReaderEventPollResponse:
    runtime = ReaderEventRuntime()
    try:
        result = await runtime.poll_events(
            record_id=record_id,
            user_id=UUID(current_user.user_id),
            after_sequence=after_sequence,
            limit=limit,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Reader record not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return ReaderEventPollResponse(
        reading_record_id=str(result.reading_record_id),
        after_sequence=result.after_sequence,
        next_after_sequence=result.next_after_sequence,
        last_event_sequence=result.last_event_sequence,
        has_more=result.has_more,
        truncated=result.truncated,
        reload_required=result.reload_required,
        reload_reason=result.reload_reason,
        events=[
            ReaderEventResponse(
                id=str(event.event_id),
                reading_record_id=str(event.reading_record_id),
                sequence=event.sequence,
                event_type=event.event_type,
                payload=event.payload_json,
                source_run_id=(
                    str(event.source_run_id) if event.source_run_id is not None else None
                ),
                source_job_id=(
                    str(event.source_job_id) if event.source_job_id is not None else None
                ),
                source_layer_id=(
                    str(event.source_layer_id) if event.source_layer_id is not None else None
                ),
                created_at=event.created_at,
            )
            for event in result.events
        ],
    )
