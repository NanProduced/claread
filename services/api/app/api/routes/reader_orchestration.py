from __future__ import annotations

from typing import get_args
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Query

from app.schemas.reader_orchestration import (
    ReaderCandidateDocumentConfirmRequest,
    ReaderCandidateDocumentConfirmResponse,
    ReaderEventPollResponse,
    ReaderEventResponse,
    ReaderPlainTextSubmitRequest,
    ReaderPlainTextSubmitResponse,
    ReaderPlateSnapshot,
    ReaderStableDocumentBase,
    ReaderStableDocumentBlock,
    ReaderStableDocumentMetadata,
    ReaderStableDocumentResponse,
    ReaderRecordListItem,
    ReaderRecordListResponse,
    ReadingRecordProductState,
)
from app.services.auth.dependencies import AuthUserDep
from app.services.reader_orchestration.article_ready_service import (
    ArticleReadyPersistenceService,
    PlainTextArticleReadySubmitRequest,
)
from app.services.reader_orchestration.base_builder import (
    DETERMINISTIC_READING_BASE_BUILDER_VERSION,
    DETERMINISTIC_SEGMENTER_VERSION,
    EXACT_CANONICAL_TEXT_VERSION,
)
from app.services.reader_orchestration.candidate_document_confirm_application_service import (
    CandidateDocumentConfirmApplicationError,
    CandidateDocumentConfirmApplicationService,
)
from app.services.reader_orchestration.event_runtime import ReaderEventRuntime
from app.services.reader_orchestration.orchestrator import ReaderOrchestrator
from app.services.reader_orchestration.repository import ReaderOrchestrationRepository
from app.services.reader_orchestration.stable_document_query_service import (
    StableDocumentQueryError,
    StableDocumentQueryService,
)

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


@router.post(
    "/records/{record_id}/candidate-documents/{candidate_document_id}/confirm",
    response_model=ReaderCandidateDocumentConfirmResponse,
    summary="Confirm a candidate document and reload the ReaderPlateSnapshot",
)
async def confirm_candidate_document(
    record_id: UUID,
    candidate_document_id: UUID,
    body: ReaderCandidateDocumentConfirmRequest,
    current_user: AuthUserDep,
) -> ReaderCandidateDocumentConfirmResponse:
    service = CandidateDocumentConfirmApplicationService()
    try:
        result = await service.confirm_candidate_document_and_load_snapshot(
            candidate_document_id=candidate_document_id,
            reading_record_id=record_id,
            user_id=UUID(current_user.user_id),
            canonicalizer_version=EXACT_CANONICAL_TEXT_VERSION,
            builder_version=DETERMINISTIC_READING_BASE_BUILDER_VERSION,
            segmenter_version=DETERMINISTIC_SEGMENTER_VERSION,
            language=body.language,
        )
    except CandidateDocumentConfirmApplicationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return ReaderCandidateDocumentConfirmResponse(
        reading_record_id=str(result.reading_record_id),
        candidate_document_id=str(result.candidate_document_id),
        stable_document_id=str(result.stable_document_id),
        base_id=str(result.base_id),
        record_generation=result.record_generation,
        document_version=result.document_version,
        content_sha256=result.content_sha256,
        canonical_text_sha256=result.canonical_text_sha256,
        block_count=result.block_count,
        candidate_confirmed=result.candidate_confirmed,
        freeze_idempotent_noop=result.freeze_idempotent_noop,
        article_ready_event_id=str(result.article_ready_event_id),
        article_ready_sequence=result.article_ready_sequence,
        snapshot=result.snapshot,
    )


@router.get(
    "/records/{record_id}/stable-document",
    response_model=ReaderStableDocumentResponse,
    summary="Load the active stable document facts for Plate projection",
)
async def get_reader_stable_document(
    record_id: UUID,
    current_user: AuthUserDep,
) -> ReaderStableDocumentResponse:
    service = StableDocumentQueryService()
    try:
        result = await service.load_active_stable_document(
            record_id=record_id,
            user_id=UUID(current_user.user_id),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Reader record not found") from exc
    except StableDocumentQueryError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return ReaderStableDocumentResponse(
        reading_record_id=str(result.reading_record_id),
        record_generation=result.record_generation,
        active_base_id=str(result.active_base_id),
        base=ReaderStableDocumentBase(
            base_id=str(result.base.base_id),
            content_sha256=result.base.content_sha256,
            content_utf16_length=result.base.content_utf16_length,
            canonicalizer_version=result.base.canonicalizer_version,
            builder_version=result.base.builder_version,
            segmenter_version=result.base.segmenter_version,
            language=result.base.language,
            title_snapshot=result.base.title_snapshot,
            navigation=result.base.navigation,
        ),
        stable_document=ReaderStableDocumentMetadata(
            stable_document_id=str(result.stable_document.stable_document_id),
            document_version=result.stable_document.document_version,
            title=result.stable_document.title,
            language=result.stable_document.language,
            source_profile=result.stable_document.source_profile,
            content_sha256=result.stable_document.content_sha256,
            status=result.stable_document.status,
        ),
        blocks=[
            ReaderStableDocumentBlock(
                block_id=block.block_id,
                parent_block_id=block.parent_block_id,
                order_index=block.order_index,
                block_type=block.block_type,
                text_content=block.text_content,
                payload=block.payload,
                source_refs=block.source_refs,
                quality=block.quality,
                canonical_text_start_utf16=block.canonical_text_start_utf16,
                canonical_text_end_utf16=block.canonical_text_end_utf16,
                interpretation_policy=block.interpretation_policy,
            )
            for block in result.blocks
        ],
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
    query: str | None = Query(default=None),
    product_state: str | None = Query(default=None),
) -> ReaderRecordListResponse:
    normalized_query = query.strip() if query and query.strip() else None
    normalized_product_states: tuple[str, ...] | None = None
    if product_state is not None:
        raw_values = [value.strip() for value in product_state.split(",")]
        product_states = tuple(value for value in raw_values if value)
        if not product_states:
            raise HTTPException(status_code=422, detail="product_state must not be empty")
        allowed_product_states = set(get_args(ReadingRecordProductState))
        invalid_product_states = sorted(
            value for value in product_states if value not in allowed_product_states
        )
        if invalid_product_states:
            raise HTTPException(
                status_code=422,
                detail=(
                    "invalid product_state value(s): "
                    + ", ".join(invalid_product_states)
                ),
            )
        normalized_product_states = product_states
    repository = ReaderOrchestrationRepository()
    summaries, total = await repository.list_user_records(
        user_id=UUID(current_user.user_id),
        limit=limit,
        query=normalized_query,
        product_states=normalized_product_states,
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
