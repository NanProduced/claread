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
    ReaderRecordListItem,
    ReaderRecordListResponse,
)
from app.services.auth.dependencies import AuthUserDep
from app.services.reader_orchestration.article_ready_service import (
    ArticleReadyPersistenceService,
    PlainTextArticleReadySubmitRequest,
)
from app.services.reader_orchestration.event_runtime import ReaderEventRuntime
from app.services.reader_orchestration.orchestrator import ReaderOrchestrator
from app.services.reader_orchestration.repository import ReaderOrchestrationRepository

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


@router.get(
    "/records",
    response_model=ReaderRecordListResponse,
    summary="List the current user's Reading Records",
)
async def list_reader_records(
    current_user: AuthUserDep,
    limit: int = Query(default=20, ge=1, le=100),
) -> ReaderRecordListResponse:
    repository = ReaderOrchestrationRepository()
    summaries, total = await repository.list_user_records(
        user_id=UUID(current_user.user_id),
        limit=limit,
    )
    return ReaderRecordListResponse(
        items=[
            ReaderRecordListItem(
                record_id=str(summary.record_id),
                title=summary.title,
                created_at=summary.created_at,
                source_type=summary.source_type,
                source_metadata=summary.source_metadata,
                product_state=summary.product_state,
                readiness_state=summary.readiness_state,
                last_event_sequence=summary.last_event_sequence,
            )
            for summary in summaries
        ],
        total=total,
        limit=limit,
    )
