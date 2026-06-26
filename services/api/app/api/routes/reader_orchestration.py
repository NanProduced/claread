from __future__ import annotations

from collections.abc import Iterator
from typing import get_args
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Query

from app.schemas.reader_input_adapter import InputSuitabilityRequest
from app.schemas.reader_orchestration import (
    ReaderCandidateDocumentConfirmRequest,
    ReaderCandidateDocumentConfirmResponse,
    ReaderEventPollResponse,
    ReaderEventResponse,
    ReaderPlainTextSubmitRequest,
    ReaderPlainTextSubmitResponse,
    ReaderPlateSnapshot,
    ReaderSourceArtifactUploadCompleteRequest,
    ReaderSourceArtifactUploadCompleteResponse,
    ReaderSourceArtifactUploadInitRequest,
    ReaderSourceArtifactUploadInitResponse,
    ReaderSourceArtifactSubmitInputRequest,
    ReaderSourceArtifactSubmitInputResponse,
    ReaderStableReadyInputSubmitRequest,
    ReaderStableReadyInputSubmitResponse,
    ReaderUnifiedInputSubmitCandidateResponse,
    ReaderUnifiedInputSubmitRejectedResponse,
    ReaderUnifiedInputSubmitRequest,
    ReaderUnifiedInputSubmitResponse,
    ReaderUnifiedInputSubmitStableResponse,
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
from app.services.reader_orchestration.artifact_input_application_service import (
    ArtifactInputApplicationConflictError,
    ArtifactInputApplicationError,
    ArtifactInputApplicationNotFoundError,
    ArtifactInputApplicationResult,
    ArtifactInputApplicationService,
)
from app.services.reader_orchestration.base_builder import (
    DETERMINISTIC_READING_BASE_BUILDER_VERSION,
    DETERMINISTIC_SEGMENTER_VERSION,
    EXACT_CANONICAL_TEXT_VERSION,
)
from app.services.reader_orchestration.candidate_document_creation_service import (
    CandidateDocumentCreationError,
    CandidateDocumentCreationResult,
    CandidateDocumentCreationService,
)
from app.services.reader_orchestration.candidate_document_confirm_application_service import (
    CandidateDocumentConfirmApplicationError,
    CandidateDocumentConfirmApplicationService,
)
from app.services.reader_orchestration.event_runtime import ReaderEventRuntime
from app.services.reader_orchestration.input_document_normalizer import (
    InputDocumentNormalizationError,
)
from app.services.reader_orchestration.input_suitability_gate import (
    evaluate_input_suitability,
)
from app.services.reader_orchestration.orchestrator import ReaderOrchestrator
from app.services.reader_orchestration.repository import ReaderOrchestrationRepository
from app.services.reader_orchestration.stable_ready_input_application_service import (
    StableReadyInputApplicationError,
    StableReadyInputApplicationResult,
    StableReadyInputApplicationService,
)
from app.services.reader_orchestration.stable_document_query_service import (
    StableDocumentQueryError,
    StableDocumentQueryService,
)
from app.services.reader_orchestration.source_artifact_service import (
    SourceArtifactCompletionResult,
    SourceArtifactConflictError,
    SourceArtifactError,
    SourceArtifactNotFoundError,
    SourceArtifactRegistrationResult,
    SourceArtifactService,
)

router = APIRouter(prefix="/reader", tags=["reader"])
_CLIENT_RECORD_ID_UNIQUE_CONSTRAINT = "uq_reading_records_user_client_active"


def _iter_exception_chain(exc: BaseException) -> Iterator[BaseException]:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _find_input_document_normalization_error(
    exc: BaseException,
) -> InputDocumentNormalizationError | None:
    for cause in _iter_exception_chain(exc):
        if isinstance(cause, InputDocumentNormalizationError):
            return cause
    return None


def _has_user_client_record_unique_violation(exc: BaseException) -> bool:
    for cause in _iter_exception_chain(exc):
        if (
            isinstance(cause, asyncpg.UniqueViolationError)
            and getattr(cause, "constraint_name", None)
            == _CLIENT_RECORD_ID_UNIQUE_CONSTRAINT
        ):
            return True
    return False


def _raise_stable_ready_input_application_error(
    exc: StableReadyInputApplicationError,
) -> None:
    normalization_error = _find_input_document_normalization_error(exc)
    if normalization_error is not None:
        raise HTTPException(
            status_code=422,
            detail=(
                "Stable-ready input normalization failed: "
                f"outcome={normalization_error.outcome}, "
                f"flags={normalization_error.flags}, "
                f"reasons={normalization_error.reasons}"
            ),
        ) from exc
    if _has_user_client_record_unique_violation(exc):
        raise HTTPException(
            status_code=409,
            detail="client_record_id already exists for this user",
        ) from exc
    raise HTTPException(status_code=409, detail=str(exc)) from exc


def _raise_candidate_document_creation_error(
    exc: CandidateDocumentCreationError,
) -> None:
    if _has_user_client_record_unique_violation(exc):
        raise HTTPException(
            status_code=409,
            detail="client_record_id already exists for this user",
        ) from exc
    raise HTTPException(status_code=409, detail=str(exc)) from exc


def _raise_source_artifact_complete_error(exc: SourceArtifactError) -> None:
    if isinstance(exc, SourceArtifactNotFoundError):
        raise HTTPException(status_code=404, detail="source artifact not found") from exc
    if isinstance(exc, SourceArtifactConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise HTTPException(status_code=422, detail=str(exc)) from exc


def _raise_artifact_input_application_error(exc: ArtifactInputApplicationError) -> None:
    if isinstance(exc, ArtifactInputApplicationNotFoundError):
        raise HTTPException(status_code=404, detail="source artifact not found") from exc
    if _has_user_client_record_unique_violation(exc):
        raise HTTPException(
            status_code=409,
            detail="client_record_id already exists for this user",
        ) from exc
    if isinstance(exc, ArtifactInputApplicationConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise HTTPException(status_code=422, detail=str(exc)) from exc


def _build_stable_ready_submit_response(
    result: StableReadyInputApplicationResult,
) -> ReaderStableReadyInputSubmitResponse:
    return ReaderStableReadyInputSubmitResponse(
        reading_record_id=str(result.reading_record_id),
        stable_document_id=str(result.stable_document_id),
        base_id=str(result.base_id),
        record_generation=result.record_generation,
        document_version=result.document_version,
        title=result.title,
        content_sha256=result.content_sha256,
        canonical_text_sha256=result.canonical_text_sha256,
        block_count=result.block_count,
        article_ready_event_id=str(result.article_ready_event_id),
        article_ready_sequence=result.article_ready_sequence,
        suitability=result.suitability,
        snapshot=result.snapshot,
    )


def _build_unified_stable_ready_submit_response(
    result: StableReadyInputApplicationResult,
) -> ReaderUnifiedInputSubmitStableResponse:
    return ReaderUnifiedInputSubmitStableResponse(
        outcome="stable_document_ready",
        reading_record_id=str(result.reading_record_id),
        stable_document_id=str(result.stable_document_id),
        base_id=str(result.base_id),
        record_generation=result.record_generation,
        document_version=result.document_version,
        title=result.title,
        content_sha256=result.content_sha256,
        canonical_text_sha256=result.canonical_text_sha256,
        block_count=result.block_count,
        article_ready_event_id=str(result.article_ready_event_id),
        article_ready_sequence=result.article_ready_sequence,
        suitability=result.suitability,
        snapshot=result.snapshot,
    )


def _build_unified_candidate_submit_response(
    result: CandidateDocumentCreationResult,
) -> ReaderUnifiedInputSubmitCandidateResponse:
    return ReaderUnifiedInputSubmitCandidateResponse(
        outcome="candidate_document_required",
        reading_record_id=str(result.reading_record_id),
        candidate_document_id=str(result.candidate_document_id),
        record_generation=result.record_generation,
        status=result.status,
        title=result.title,
        block_count=result.block_count,
        source_type=result.source_type,
        filename=result.filename,
        original_input_id=str(result.original_input_id),
        suitability=result.suitability,
    )


def _build_source_artifact_upload_headers(
    *,
    content_type: str | None,
    content_sha256: str | None,
) -> dict[str, str]:
    headers = {"content-type": content_type or "application/octet-stream"}
    if content_sha256 is not None:
        headers["content-sha256"] = content_sha256
    return headers


def _build_source_artifact_upload_init_response(
    *,
    result: SourceArtifactRegistrationResult,
    bucket: str,
    endpoint: str,
) -> ReaderSourceArtifactUploadInitResponse:
    return ReaderSourceArtifactUploadInitResponse(
        artifact_id=str(result.artifact_id),
        artifact_kind=result.artifact_kind,
        storage_provider=result.storage_provider,
        bucket=result.bucket or bucket,
        endpoint=endpoint,
        object_key=result.object_key,
        status=result.status,
        content_type=result.content_type,
        byte_size=result.byte_size,
        content_sha256=result.content_sha256,
        source_filename=result.source_filename,
        upload_method="oss_put_object_pending_credentials",
        headers=_build_source_artifact_upload_headers(
            content_type=result.content_type,
            content_sha256=result.content_sha256,
        ),
    )


def _build_source_artifact_upload_complete_response(
    result: SourceArtifactCompletionResult,
) -> ReaderSourceArtifactUploadCompleteResponse:
    return ReaderSourceArtifactUploadCompleteResponse(
        artifact_id=str(result.artifact_id),
        artifact_kind=result.artifact_kind,
        storage_provider=result.storage_provider,
        bucket=result.bucket,
        endpoint=result.endpoint,
        object_key=result.object_key,
        status=result.status,
        content_type=result.content_type,
        byte_size=result.byte_size,
        content_sha256=result.content_sha256,
        source_filename=result.source_filename,
        upload_completed=True,
        idempotent_noop=result.idempotent_noop,
    )


def _build_source_artifact_submit_input_response(
    result: ArtifactInputApplicationResult,
) -> ReaderSourceArtifactSubmitInputResponse:
    return ReaderSourceArtifactSubmitInputResponse(
        reading_record_id=str(result.reading_record_id),
        original_input_id=str(result.original_input_id),
        artifact_id=str(result.artifact_id),
        record_generation=result.record_generation,
        source_type=result.source_type,
        input_type=result.input_type,
        product_state=result.product_state,
        readiness_state=result.readiness_state,
        title=result.title,
        language=result.language,
        extraction_required=True,
        bucket=result.bucket,
        endpoint=result.endpoint,
        object_key=result.object_key,
        content_type=result.content_type,
        byte_size=result.byte_size,
        content_sha256=result.content_sha256,
        source_filename=result.source_filename,
    )


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
    "/records/input",
    response_model=ReaderUnifiedInputSubmitResponse,
    summary="Submit reader input and route it to stable freeze, candidate creation, or action-required",
)
async def submit_reader_input(
    body: ReaderUnifiedInputSubmitRequest,
    current_user: AuthUserDep,
) -> ReaderUnifiedInputSubmitResponse:
    user_id = UUID(current_user.user_id)
    suitability = evaluate_input_suitability(
        InputSuitabilityRequest(
            source_type=body.source_type,
            text=body.text,
            filename=body.filename,
            source_metadata=body.source_metadata or {},
        )
    )

    if suitability.outcome == "stable_document_ready":
        service = StableReadyInputApplicationService()
        try:
            result = await service.freeze_stable_ready_input_and_load_snapshot(
                user_id=user_id,
                source_type=body.source_type,
                text=body.text,
                filename=body.filename,
                source_metadata=body.source_metadata,
                client_record_id=body.client_record_id,
                language=body.language,
            )
        except StableReadyInputApplicationError as exc:
            _raise_stable_ready_input_application_error(exc)
            raise AssertionError("unreachable")
        return _build_unified_stable_ready_submit_response(result)

    if suitability.outcome == "candidate_document_required":
        service = CandidateDocumentCreationService()
        try:
            result = await service.create_candidate_document_from_input(
                user_id=user_id,
                source_type=body.source_type,
                text=body.text,
                filename=body.filename,
                source_metadata=body.source_metadata,
                client_record_id=body.client_record_id,
                language=body.language,
            )
        except CandidateDocumentCreationError as exc:
            _raise_candidate_document_creation_error(exc)
            raise AssertionError("unreachable")
        return _build_unified_candidate_submit_response(result)

    return ReaderUnifiedInputSubmitRejectedResponse(
        outcome="input_rejected_or_action_required",
        suitability=suitability,
    )


@router.post(
    "/source-artifacts/init-upload",
    response_model=ReaderSourceArtifactUploadInitResponse,
    summary="Register source artifact upload metadata and return an OSS object target",
)
async def init_reader_source_artifact_upload(
    body: ReaderSourceArtifactUploadInitRequest,
    current_user: AuthUserDep,
) -> ReaderSourceArtifactUploadInitResponse:
    user_id = UUID(current_user.user_id)
    service = SourceArtifactService()
    try:
        result = await service.register_source_artifact(
            user_id=user_id,
            artifact_kind=body.artifact_kind,
            reading_record_id=body.reading_record_id,
            original_input_id=body.original_input_id,
            storage_provider="oss",
            content_type=body.content_type,
            byte_size=body.byte_size,
            content_sha256=body.content_sha256,
            source_filename=body.source_filename,
            status="pending",
            source_refs_json=body.source_refs or {},
            metadata_json=body.metadata or {},
            quality_json=body.quality or {},
        )
        object_ref = service.build_oss_object_ref(
            user_id=user_id,
            artifact_id=result.artifact_id,
            source_filename=result.source_filename,
            artifact_kind=result.artifact_kind,
        )
    except SourceArtifactError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return _build_source_artifact_upload_init_response(
        result=result,
        bucket=object_ref["bucket"],
        endpoint=object_ref["endpoint"],
    )


@router.post(
    "/source-artifacts/{artifact_id}/complete-upload",
    response_model=ReaderSourceArtifactUploadCompleteResponse,
    summary="Mark an OSS-backed original_upload source artifact as available",
)
async def complete_reader_source_artifact_upload(
    artifact_id: UUID,
    body: ReaderSourceArtifactUploadCompleteRequest,
    current_user: AuthUserDep,
) -> ReaderSourceArtifactUploadCompleteResponse:
    user_id = UUID(current_user.user_id)
    service = SourceArtifactService()
    try:
        result = await service.complete_source_artifact_upload(
            user_id=user_id,
            artifact_id=artifact_id,
            content_type=body.content_type,
            byte_size=body.byte_size,
            content_sha256=body.content_sha256,
            metadata_json=body.metadata,
            quality_json=body.quality,
        )
    except SourceArtifactError as exc:
        _raise_source_artifact_complete_error(exc)
        raise AssertionError("unreachable")

    return _build_source_artifact_upload_complete_response(result)


@router.post(
    "/source-artifacts/{artifact_id}/submit-input",
    response_model=ReaderSourceArtifactSubmitInputResponse,
    summary="Bind an available uploaded source artifact into a reader input shell",
)
async def submit_reader_source_artifact_as_input(
    artifact_id: UUID,
    body: ReaderSourceArtifactSubmitInputRequest,
    current_user: AuthUserDep,
) -> ReaderSourceArtifactSubmitInputResponse:
    service = ArtifactInputApplicationService()
    try:
        result = await service.submit_available_artifact_as_input(
            user_id=UUID(current_user.user_id),
            artifact_id=artifact_id,
            title=body.title,
            language=body.language,
            client_record_id=body.client_record_id,
            source_metadata=body.source_metadata,
        )
    except ArtifactInputApplicationError as exc:
        _raise_artifact_input_application_error(exc)
        raise AssertionError("unreachable")

    return _build_source_artifact_submit_input_response(result)


@router.post(
    "/records/stable-ready-input",
    response_model=ReaderStableReadyInputSubmitResponse,
    summary="Freeze stable-ready input into a reader record and reload the snapshot",
)
async def submit_reader_stable_ready_input(
    body: ReaderStableReadyInputSubmitRequest,
    current_user: AuthUserDep,
) -> ReaderStableReadyInputSubmitResponse:
    service = StableReadyInputApplicationService()
    try:
        result = await service.freeze_stable_ready_input_and_load_snapshot(
            user_id=UUID(current_user.user_id),
            source_type=body.source_type,
            text=body.text,
            filename=body.filename,
            source_metadata=body.source_metadata,
            client_record_id=body.client_record_id,
            language=body.language,
        )
    except StableReadyInputApplicationError as exc:
        _raise_stable_ready_input_application_error(exc)
        raise AssertionError("unreachable")

    return _build_stable_ready_submit_response(result)


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
