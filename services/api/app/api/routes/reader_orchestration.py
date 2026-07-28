from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from typing import Any, get_args
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Query
from starlette.responses import JSONResponse

from app.schemas.reader_input_adapter import InputSuitabilityRequest
from app.schemas.reader_orchestration import (
    ReaderCandidateDocumentConfirmRequest,
    ReaderCandidateDocumentConfirmResponse,
    ReaderCandidateDocumentConflictResponseDto,
    ReaderCandidateDocumentNotFoundResponseDto,
    ReaderCandidateDocumentReadResponseDto,
    ReaderConfirmedSourceConflictResponse,
    ReaderConfirmedSourceGetResponse,
    ReaderConfirmedSourceUpdateRequest,
    ReaderConfirmedSourceUpdateResponse,
    ReaderEventPollResponse,
    ReaderEventResponse,
    ReaderPlainTextSubmitRequest,
    ReaderPlainTextSubmitResponse,
    ReaderPlateSnapshot,
    ReaderSectionTranslationOutcome,
    ReaderSectionTranslationRequest,
    ReaderSectionTranslationResponse,
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
    ReaderStableDocumentAnchorSegment,
    ReaderStableDocumentBase,
    ReaderStableDocumentBlock,
    ReaderStableDocumentMetadata,
    ReaderStableDocumentResponse,
    ReaderRecordListItem,
    ReaderRecordListResponse,
    ReaderRecordOpenedResponse,
    ReadingRecordProductState,
    ReaderArtifactPipelineArtifactSummary,
    ReaderArtifactPipelineCandidateDocument,
    ReaderArtifactPipelineJobSummary,
    ReaderArtifactPipelineOriginalInputSummary,
    ReaderArtifactPipelineRecordSummary,
    ReaderArtifactPipelineStableDocument,
    ReaderArtifactPipelineStatusResponse,
    ReaderArticleRagIndexEnsureRequest,
    ReaderArticleRagIndexEnsureResponse,
    ReaderArticleRagIndexStatusResponse,
)
from app.services.auth.dependencies import AuthUserDep
from app.services.reader_orchestration.article_rag_index_lifecycle_service import (
    ENSURE_STATUS_RECORD_NOT_FOUND,
    STATUS_UNAVAILABLE,
    ArticleRagIndexEnsureResult,
    ArticleRagIndexLifecycleService,
    ArticleRagIndexLifecycleStatus,
)
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
from app.services.reader_orchestration.artifact_input_status_query_service import (
    ArtifactInputStatusQueryError,
    ArtifactInputStatusResult,
    ArtifactPipelineStatusQueryService,
)
from app.services.reader_orchestration.base_builder import (
    AUTO_SEGMENTER_POLICY,
    DETERMINISTIC_READING_BASE_BUILDER_VERSION,
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
    StaleCandidateRevisionApplicationError,
)
from app.services.reader_orchestration.candidate_document_read_service import (
    CandidateDocumentReadConflict,
    CandidateDocumentReadError,
    CandidateDocumentReadService,
)
from app.services.reader_orchestration.confirmed_source_application_service import (
    ConfirmedSourceApplicationError,
    ConfirmedSourceApplicationService,
    ConfirmedSourceConflictError,
    ConfirmedSourceNotFoundError,
)
from app.services.reader_orchestration.event_runtime import ReaderEventRuntime
from app.services.reader_orchestration.input_document_normalizer import (
    InputDocumentNormalizationError,
)
from app.services.reader_orchestration.input_suitability_gate import (
    evaluate_input_suitability,
)
from app.services.reader_orchestration.markdown_source_parser import (
    MarkdownSourceParser,
)
from app.services.reader_orchestration.orchestrator import ReaderOrchestrator
from app.services.reader_orchestration.repository import ReaderOrchestrationRepository
from app.services.reader_orchestration.section_request_planner import (
    ExplicitSectionIntent,
    SectionRequestTrigger,
)
from app.services.reader_orchestration.section_translation_bootstrap import (
    REASON_ALREADY_QUEUED,
    SectionBootstrapOutcome,
    SectionTranslationBootstrapService,
)
from app.services.reader_orchestration.section_translation_drain import (
    SectionDrainOutcome,
    SectionTranslationDrainService,
)
from app.services.reader_orchestration.stable_ready_input_application_service import (
    StableReadyInputApplicationError,
    StableReadyInputApplicationResult,
    StableReadyInputApplicationService,
)
from app.services.reader_orchestration.stable_document_query_service import (
    StableDocumentProjectionResult,
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
from app.services.reader_orchestration.oss_presigner import (
    NullPresigner,
    PresignedUpload,
    Presigner,
    build_default_presigner,
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
    presigned: PresignedUpload | None = None,
) -> ReaderSourceArtifactUploadInitResponse:
    if presigned is not None:
        upload_method = "oss_put_object_presigned"
        presigned_url = presigned.url
        presigned_method = presigned.method
        presigned_expires_at = presigned.expires_at
        # D6-I3Q: in presigned mode the headers MUST come from the presigner
        # so the client sends exactly the headers that were signed. Returning
        # the non-signed hints here would cause the client to upload with
        # ``content-sha256`` instead of the signed ``x-oss-content-sha256``,
        # breaking OSS signature validation.
        headers = dict(presigned.headers)
    else:
        upload_method = "oss_put_object_pending_credentials"
        presigned_url = None
        presigned_method = None
        presigned_expires_at = None
        # Pending-credentials path: provide non-signed hints the client
        # should include when uploading with its own credentials.
        headers = _build_source_artifact_upload_headers(
            content_type=result.content_type,
            content_sha256=result.content_sha256,
        )
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
        upload_method=upload_method,
        headers=headers,
        presigned_url=presigned_url,
        presigned_method=presigned_method,
        presigned_expires_at=presigned_expires_at,
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
        extraction_job_id=str(result.extraction_job_id),
        extraction_job_status=result.extraction_job_status,
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
                reading_goal=body.reading_goal,
                reading_variant=body.reading_variant,
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
    # L2/A4 — 每请求只解析一次：路由预检 gate、stable-ready freeze 与
    # candidate 创建共用同一份 MarkdownParseResult；内容格式
    # （detected_format）由这同一份解析结果决定，与输入来源解耦。
    preparsed = MarkdownSourceParser().parse(
        body.text.replace("\r\n", "\n").replace("\r", "\n")
    )
    suitability = evaluate_input_suitability(
        InputSuitabilityRequest(
            source_type=body.source_type,
            text=body.text,
            filename=body.filename,
            source_metadata=body.source_metadata or {},
        ),
        preparsed=preparsed,
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
                reading_goal=body.reading_goal,
                reading_variant=body.reading_variant,
                preparsed=preparsed,
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
                reading_goal=body.reading_goal,
                reading_variant=body.reading_variant,
                preparsed=preparsed,
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

    # D6-I3Q: build a presigned PUT URL if a presigner is configured.
    # build_default_presigner() returns NullPresigner when credentials are
    # missing or presigning is disabled — the response then falls back to
    # ``oss_put_object_pending_credentials`` with ``presigned_url=None``.
    #
    # Security contract: the AccessKey secret never leaves the server. The
    # presigned URL may include the AccessKey id (``OSSAccessKeyId=...``) in
    # the query string per the standard OSS presigned-URL model — the id is
    # not a secret.
    presigner: Presigner = build_default_presigner()
    presigned: PresignedUpload | None = None
    if not isinstance(presigner, NullPresigner):
        try:
            presigned = presigner.presign_put_object(
                bucket=object_ref["bucket"],
                endpoint=object_ref["endpoint"],
                object_key=result.object_key,
                content_type=result.content_type,
                content_sha256=result.content_sha256,
                expires_in=timedelta(
                    seconds=_get_presign_expires_seconds(),
                ),
            )
        except Exception:
            # Presigner failure must not break init-upload; fall back to
            # pending-credentials semantic. The client can retry with its
            # own credentials.
            presigned = None

    return _build_source_artifact_upload_init_response(
        result=result,
        bucket=object_ref["bucket"],
        endpoint=object_ref["endpoint"],
        presigned=presigned,
    )


def _get_presign_expires_seconds() -> int:
    """Read presign expiry from settings (lazy to avoid import cycles)."""
    from app.config.settings import get_settings

    return get_settings().aliyun_oss_presign_expires_seconds


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
            reading_goal=body.reading_goal,
            reading_variant=body.reading_variant,
        )
    except ArtifactInputApplicationError as exc:
        _raise_artifact_input_application_error(exc)
        raise AssertionError("unreachable")

    return _build_source_artifact_submit_input_response(result)


def _build_artifact_pipeline_job_summary(
    job: Any,
) -> ReaderArtifactPipelineJobSummary:
    return ReaderArtifactPipelineJobSummary(
        job_id=str(job.job_id),
        status=job.status,
        attempt_count=job.attempt_count,
        max_attempts=job.max_attempts,
        failure_class=job.failure_class,
        failure_code=job.failure_code,
        rationale_code=job.rationale_code,
        available_at=job.available_at,
        updated_at=job.updated_at,
    )


def _build_artifact_pipeline_status_response(
    result: ArtifactInputStatusResult,
) -> ReaderArtifactPipelineStatusResponse:
    artifact_summary = ReaderArtifactPipelineArtifactSummary(
        artifact_id=str(result.artifact.artifact_id),
        status=result.artifact.status,
        artifact_kind=result.artifact.artifact_kind,
        storage_provider=result.artifact.storage_provider,
        bucket=result.artifact.bucket,
        endpoint=result.artifact.endpoint,
        object_key=result.artifact.object_key,
        content_type=result.artifact.content_type,
        byte_size=result.artifact.byte_size,
        content_sha256=result.artifact.content_sha256,
        source_filename=result.artifact.source_filename,
        reading_record_id=(
            str(result.artifact.reading_record_id)
            if result.artifact.reading_record_id is not None
            else None
        ),
        original_input_id=(
            str(result.artifact.original_input_id)
            if result.artifact.original_input_id is not None
            else None
        ),
    )

    record_summary: ReaderArtifactPipelineRecordSummary | None = None
    if result.record is not None:
        record_summary = ReaderArtifactPipelineRecordSummary(
            reading_record_id=str(result.record.reading_record_id),
            generation=result.record.generation,
            product_state=result.record.product_state,
            readiness_state=result.record.readiness_state,
            active_base_id=(
                str(result.record.active_base_id)
                if result.record.active_base_id is not None
                else None
            ),
            source_type=result.record.source_type,
            title=result.record.title,
            language=result.record.language,
        )

    original_input_summary: (
        ReaderArtifactPipelineOriginalInputSummary | None
    ) = None
    if result.original_input is not None:
        original_input_summary = ReaderArtifactPipelineOriginalInputSummary(
            original_input_id=str(result.original_input.original_input_id),
            input_type=result.original_input.input_type,
            content_sha256=result.original_input.content_sha256,
            has_source_text=result.original_input.has_source_text,
            has_confirmed_source=result.original_input.has_confirmed_source,
            extraction_status=result.original_input.extraction_status,
            metadata=result.original_input.metadata,
        )

    extraction_job_summary: ReaderArtifactPipelineJobSummary | None = None
    if result.extraction_job is not None:
        extraction_job_summary = _build_artifact_pipeline_job_summary(
            result.extraction_job
        )

    materialization_job_summary: ReaderArtifactPipelineJobSummary | None = None
    if result.materialization_job is not None:
        materialization_job_summary = _build_artifact_pipeline_job_summary(
            result.materialization_job
        )

    candidate_summary: ReaderArtifactPipelineCandidateDocument | None = None
    if result.candidate_document is not None:
        candidate_summary = ReaderArtifactPipelineCandidateDocument(
            candidate_document_id=str(result.candidate_document.candidate_document_id),
            record_generation=result.candidate_document.record_generation,
            canonical_text_preview=result.candidate_document.canonical_text_preview,
        )

    stable_summary: ReaderArtifactPipelineStableDocument | None = None
    if result.stable_document is not None:
        stable_summary = ReaderArtifactPipelineStableDocument(
            stable_document_id=str(result.stable_document.stable_document_id),
            base_id=str(result.stable_document.base_id),
            record_generation=result.stable_document.record_generation,
            content_sha256=result.stable_document.content_sha256,
            canonical_text_sha256=result.stable_document.canonical_text_sha256,
        )

    return ReaderArtifactPipelineStatusResponse(
        artifact=artifact_summary,
        record=record_summary,
        original_input=original_input_summary,
        extraction_job=extraction_job_summary,
        materialization_job=materialization_job_summary,
        candidate_document=candidate_summary,
        stable_document=stable_summary,
        outcome=result.outcome,
        next_action=result.next_action,
    )


@router.get(
    "/source-artifacts/{artifact_id}/pipeline-status",
    response_model=ReaderArtifactPipelineStatusResponse,
    summary="Load read-only artifact input pipeline status (D6-I3V)",
)
async def get_reader_source_artifact_pipeline_status(
    artifact_id: UUID,
    current_user: AuthUserDep,
) -> ReaderArtifactPipelineStatusResponse:
    service = ArtifactPipelineStatusQueryService()
    try:
        result = await service.load_pipeline_status(
            artifact_id=artifact_id,
            user_id=UUID(current_user.user_id),
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=404, detail="Source artifact not found"
        ) from exc
    except ArtifactInputStatusQueryError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return _build_artifact_pipeline_status_response(result)


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
            reading_goal=body.reading_goal,
            reading_variant=body.reading_variant,
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
            segmenter_version=AUTO_SEGMENTER_POLICY,
            language=body.language,
        )
    except StaleCandidateRevisionApplicationError as exc:
        # L2 插入点 A：candidate 引用过期 source revision —— 409 可恢复
        # （重取 confirmed-source 获得基于最新 revision 的 candidate）。
        return JSONResponse(
            status_code=409,
            content=ReaderConfirmedSourceConflictResponse(
                ok=False,
                code="stale_candidate_revision",
                resolution="reload",
                message="确认内容已过期，请重新加载最新待确认版本。",
                current_revision=exc.current_revision,
            ).model_dump(mode="json"),
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


def _confirmed_source_not_found_response() -> JSONResponse:
    # 404 collapse：not found / not owner / deleted / 无 draft source，
    # 不区分原因（Q6 沿用 GET candidate-document 的 collapse 模式）。
    return JSONResponse(
        status_code=404,
        content={
            "ok": False,
            "code": "not_found",
            "message": "未找到可编辑的原文，可能已在其他设备处理。",
        },
    )


def _confirmed_source_conflict_response(
    exc: ConfirmedSourceConflictError,
) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content=ReaderConfirmedSourceConflictResponse(
            ok=False,
            code=exc.code,
            resolution=exc.resolution,
            message=str(exc),
            current_revision=exc.current_revision,
        ).model_dump(mode="json"),
    )


@router.get(
    "/records/{record_id}/confirmed-source",
    response_model=ReaderConfirmedSourceGetResponse,
    responses={
        401: {"description": "Unauthenticated (existing auth mechanism)."},
        404: {"description": "Collapsed: not found / not owner / deleted / "
              "no draft confirmed source."},
        409: {"model": ReaderConfirmedSourceConflictResponse,
              "description": "record_state_advanced (source frozen or "
              "record advanced)."},
    },
    summary="Load the draft confirmed source for editing / resume",
)
async def get_reader_confirmed_source(
    record_id: UUID,
    current_user: AuthUserDep,
) -> ReaderConfirmedSourceGetResponse | JSONResponse:
    """L2 设计文档 §4.1：draft 读取 / resume 入口（编辑入口，返回正文）。"""
    service = ConfirmedSourceApplicationService()
    try:
        result = await service.get_confirmed_source(
            record_id=record_id,
            user_id=UUID(current_user.user_id),
        )
    except ConfirmedSourceNotFoundError:
        return _confirmed_source_not_found_response()
    except ConfirmedSourceConflictError as exc:
        return _confirmed_source_conflict_response(exc)

    candidate = result.candidate
    return ReaderConfirmedSourceGetResponse(
        source_document_id=result.source.id,
        record_generation=result.source.record_generation,
        revision=result.source.revision,
        status="draft",
        markdown_text=result.source.markdown_text,
        content_sha256=result.source.content_sha256,
        edit_source=result.source.edit_source,
        updated_at=result.updated_at,
        candidate=(
            {
                "candidate_document_id": str(candidate.candidate_document_id),
                "status": candidate.status,
                "canonical_text_preview": candidate.canonical_text_preview,
            }
            if candidate is not None
            else None
        ),
        quality=result.quality,
        adaptation_notice=result.adaptation_notice,
        content_check=result.content_check,
    )


@router.put(
    "/records/{record_id}/confirmed-source",
    response_model=ReaderConfirmedSourceUpdateResponse,
    responses={
        401: {"description": "Unauthenticated (existing auth mechanism)."},
        404: {"description": "Collapsed: not found / not owner / deleted / "
              "no draft confirmed source."},
        409: {"model": ReaderConfirmedSourceConflictResponse,
              "description": "source_frozen / stale_source_revision / "
              "record_state_advanced."},
    },
    summary="Replace the confirmed source body and reparse "
    "(optimistic concurrency via expected_revision)",
)
async def put_reader_confirmed_source(
    record_id: UUID,
    body: ReaderConfirmedSourceUpdateRequest,
    current_user: AuthUserDep,
) -> ReaderConfirmedSourceUpdateResponse | JSONResponse:
    """L2 设计文档 §4.2：整篇更新 + reparse（revision 乐观并发、同 hash
    幂等 no-op、三级分类、版本化 candidate supersede、stable 镜像自动
    freeze 并同事务冻结 source）。"""
    service = ConfirmedSourceApplicationService()
    try:
        result = await service.update_confirmed_source(
            record_id=record_id,
            user_id=UUID(current_user.user_id),
            expected_revision=body.expected_revision,
            markdown_text=body.markdown_text,
            edit_source=body.edit_source,
        )
    except ConfirmedSourceNotFoundError:
        return _confirmed_source_not_found_response()
    except ConfirmedSourceConflictError as exc:
        return _confirmed_source_conflict_response(exc)
    except ConfirmedSourceApplicationError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    candidate = result.candidate
    return ReaderConfirmedSourceUpdateResponse(
        revision=result.revision,
        content_sha256=result.content_sha256,
        outcome=result.outcome,  # type: ignore[arg-type]
        candidate=(
            {
                "candidate_document_id": str(candidate.candidate_document_id),
                "status": candidate.status,
                "canonical_text_preview": candidate.canonical_text_preview,
            }
            if candidate is not None
            else None
        ),
        quality=result.quality,
        adaptation_notice=result.adaptation_notice,
        content_check=result.content_check,
        snapshot=result.snapshot,
    )


@router.get(
    "/records/{record_id}/candidate-document",
    response_model=ReaderCandidateDocumentReadResponseDto,
    responses={
        401: {"description": "Unauthenticated (existing auth mechanism)."},
        404: {
            "model": ReaderCandidateDocumentNotFoundResponseDto,
            "description": "Record not found / not owner / soft-deleted / "
            "no ready candidate (all collapsed, no leak).",
        },
        409: {
            "model": ReaderCandidateDocumentConflictResponseDto,
            "description": "Record state advanced or multiple ready "
            "candidates.",
        },
    },
    summary="Load the current ready candidate document for confirmation "
            "(S2: Candidate Recovery read model)",
)
async def get_reader_candidate_document(
    record_id: UUID,
    current_user: AuthUserDep,
) -> ReaderCandidateDocumentReadResponseDto | JSONResponse:
    """Load the current ``(record_id, generation)`` ready candidate as a
    safe typed projection.

    Returns 200 only when ``product_state='needs_confirmation'`` AND
    exactly one ``status='ready'`` candidate exists. All other cases
    return 404 (collapsed) or 409 (with ``code`` + ``resolution``).

    Error responses use the root-level contract shape
    (``{"ok": false, "code": ..., "message": ...}``) and are emitted
    via :class:`JSONResponse` so FastAPI does NOT wrap them into
    ``{"detail": ...}``.

    The response never leaks ``blocks_json`` / ``quality_json`` /
    ``source_refs_json`` / ``source_text`` / ``original_input_id``.
    """
    service = CandidateDocumentReadService()
    try:
        result = await service.load_candidate_document(
            record_id=record_id,
            user_id=UUID(current_user.user_id),
        )
    except CandidateDocumentReadError:
        # 404: all four causes collapsed (not found / not owner /
        # soft-deleted / no ready candidate). Generic message, no leak.
        # Use JSONResponse so the body is the root-level contract shape,
        # not the FastAPI-default {"detail": ...} envelope.
        return JSONResponse(
            status_code=404,
            content={
                "ok": False,
                "code": "not_found",
                "message": "未找到待确认内容，可能已在其他设备处理。",
            },
        )
    except CandidateDocumentReadConflict as exc:
        # 409: record_state_advanced or multiple_ready_candidates.
        # Root-level contract: ok / code / resolution / message.
        return JSONResponse(
            status_code=409,
            content=ReaderCandidateDocumentConflictResponseDto(
                ok=False,
                code=exc.code,
                resolution=exc.resolution,
                message=exc.message,
            ).model_dump(mode="json"),
        )

    return result.response


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

    return _build_stable_document_route_response(result)


def _build_stable_document_route_response(
    result: StableDocumentProjectionResult,
) -> ReaderStableDocumentResponse:
    """Map the service's projection dataclass tree onto the HTTP response.

    Field-by-field mapping (no ``**`` spread) so the contract between the
    service layer and the API boundary stays explicit and reviewable.  New
    fields (e.g. ``base.text``, ``anchor_segments``) must be wired here.
    """
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
            text=result.base.text,
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
        anchor_segments=[
            ReaderStableDocumentAnchorSegment(
                anchor_segment_id=segment.anchor_segment_id,
                unit_id=segment.unit_id,
                order_index=segment.order_index,
                segment_type=segment.segment_type,
                base_start_utf16=segment.base_start_utf16,
                base_end_utf16=segment.base_end_utf16,
                text_hash=segment.text_hash,
            )
            for segment in result.anchor_segments
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
                last_opened_at=summary.last_opened_at,
                display_title=summary.display_title,
                source_label=summary.source_label,
            )
            for summary in summaries
        ],
        total=total,
        limit=limit,
    )


@router.post(
    "/records/{record_id}/opened",
    response_model=ReaderRecordOpenedResponse,
    summary="Stamp reading_records.last_opened_at when the user opens the new Reading Record page",
)
async def mark_reader_record_opened(
    record_id: UUID,
    current_user: AuthUserDep,
) -> ReaderRecordOpenedResponse:
    repository = ReaderOrchestrationRepository()
    new_value = await repository.mark_record_opened(
        record_id=record_id,
        user_id=UUID(current_user.user_id),
        opened_at=datetime.now(tz=timezone.utc),
    )
    if new_value is None:
        raise HTTPException(
            status_code=404, detail="Reader record not found"
        )
    return ReaderRecordOpenedResponse(
        record_id=str(record_id),
        last_opened_at=new_value,
    )


# ===========================================================================
# D6-I4T Article RAG Index Lifecycle API
#
# Thin route layer that delegates to ``ArticleRagIndexLifecycleService``.
# Both routes source ``user_id`` exclusively from ``AuthUserDep``; the
# request body and query string never carry identity.
#
# Status route is read-only: no transaction, no writes, no locks.
# Ensure route is caller-managed-transaction: the route opens the
# transaction and the service writes index_runs / reader_runs /
# reader_jobs inside it.
# ===========================================================================


def _get_article_rag_index_lifecycle_service() -> ArticleRagIndexLifecycleService:
    """Factory for the lifecycle service.

    Tests can monkeypatch this to inject a fake service without touching
    the real bootstrap / vector / embedding path.
    """
    return ArticleRagIndexLifecycleService()


def _get_reader_pool() -> asyncpg.Pool:
    """Returns the DB pool. Tests can monkeypatch this to inject a fake pool."""
    return ReaderOrchestrationRepository().get_pool()


def _build_article_rag_index_status_response(
    result: ArticleRagIndexLifecycleStatus,
) -> ReaderArticleRagIndexStatusResponse:
    return ReaderArticleRagIndexStatusResponse(
        reading_record_id=str(result.reading_record_id),
        status=result.status,
        stable_document_id=(
            str(result.stable_document_id)
            if result.stable_document_id is not None
            else None
        ),
        base_id=(
            str(result.base_id) if result.base_id is not None else None
        ),
        record_generation=result.record_generation,
        index_run_id=(
            str(result.index_run_id)
            if result.index_run_id is not None
            else None
        ),
        plan_content_sha256=result.plan_content_sha256,
        chunk_count=result.chunk_count,
        reason_code=result.reason_code,
    )


def _build_article_rag_index_ensure_response(
    result: ArticleRagIndexEnsureResult,
) -> ReaderArticleRagIndexEnsureResponse:
    return ReaderArticleRagIndexEnsureResponse(
        reading_record_id=str(result.reading_record_id),
        status=result.status,
        reason_code=result.reason_code,
        idempotent_noop=result.idempotent_noop,
        stable_document_id=(
            str(result.stable_document_id)
            if result.stable_document_id is not None
            else None
        ),
        base_id=(
            str(result.base_id) if result.base_id is not None else None
        ),
        record_generation=result.record_generation,
        index_run_id=(
            str(result.index_run_id)
            if result.index_run_id is not None
            else None
        ),
        job_id=(
            str(result.job_id) if result.job_id is not None else None
        ),
    )


@router.get(
    "/records/{record_id}/article-rag-index/status",
    response_model=ReaderArticleRagIndexStatusResponse,
    summary="Load the Article RAG index lifecycle status (D6-I4T)",
)
async def get_reader_article_rag_index_status(
    record_id: UUID,
    current_user: AuthUserDep,
) -> ReaderArticleRagIndexStatusResponse:
    """Read-only Article RAG index lifecycle status query.

    Does NOT write, lock rows, or read chunk text / embedding vector /
    Plate JSON / Markdown / DOM / Slate / UI fields.  ``user_id`` comes
    only from ``AuthUserDep``.  Index identity is fixed server-side.

    HTTP mapping:
      * ``status=unavailable`` + ``reason_code=record_not_found`` → 404
      * All other typed statuses → 200 with the typed response
    """
    service = _get_article_rag_index_lifecycle_service()
    pool = _get_reader_pool()
    try:
        # Read-only: no transaction needed.
        async with pool.acquire() as conn:
            result = await service.load_article_rag_index_lifecycle_status(
                conn,
                reading_record_id=record_id,
                user_id=UUID(current_user.user_id),
            )
    except HTTPException:
        raise
    except Exception:
        # Unexpected (non-typed) failure: surface as 409 with a fixed,
        # leak-safe identifier — the underlying exception message, type
        # name, and traceback are NEVER echoed to the client (they may
        # contain tokens, URIs, chunk text, query text, or SDK
        # internals).  The exception is intentionally raised ``from
        # None`` so the cause chain cannot be introspected downstream
        # either — structured server-side logging should be wired
        # separately (e.g. via an exception middleware / handler) and
        # must redact the same fields before persisting.
        raise HTTPException(
            status_code=409,
            detail="article_rag_index_status_unexpected_error",
        ) from None

    if (
        result.status == STATUS_UNAVAILABLE
        and result.reason_code == "record_not_found"
    ):
        raise HTTPException(
            status_code=404, detail="Reader record not found"
        )
    return _build_article_rag_index_status_response(result)


@router.post(
    "/records/{record_id}/article-rag-index/ensure",
    response_model=ReaderArticleRagIndexEnsureResponse,
    summary="Ensure an Article RAG index build job exists for the record (D6-I4T)",
)
async def ensure_reader_article_rag_index_job(
    record_id: UUID,
    body: ReaderArticleRagIndexEnsureRequest,
    current_user: AuthUserDep,
) -> ReaderArticleRagIndexEnsureResponse:
    """Trigger Article RAG index build job creation with caller-managed tx.

    The route opens the transaction; the service writes ``index_runs`` /
    ``reader_runs`` / ``reader_jobs`` inside it.  ``user_id`` comes only
    from ``AuthUserDep``.  The Article RAG index is a single path —
    there is no version selection.

    HTTP mapping:
      * ``status=record_not_found`` → 404
      * All other typed statuses (including ``error``) → 200 with typed
        response, so callers can switch on ``status`` / ``reason_code``
        without parsing opaque details.
      * Unexpected service exception → 409.
    """
    service = _get_article_rag_index_lifecycle_service()
    pool = _get_reader_pool()

    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                result = (
                    await service.ensure_article_rag_index_job_in_transaction(
                        conn,
                        reading_record_id=record_id,
                        user_id=UUID(current_user.user_id),
                        expected_generation=body.expected_generation,
                    )
                )
    except HTTPException:
        raise
    except Exception:
        # Unexpected (non-typed) failure: surface as 409 with a fixed,
        # leak-safe identifier — the underlying exception message, type
        # name, and traceback are NEVER echoed to the client (they may
        # contain tokens, URIs, chunk text, query text, or SDK
        # internals).  The exception is intentionally raised ``from
        # None`` so the cause chain cannot be introspected downstream
        # either — structured server-side logging should be wired
        # separately (e.g. via an exception middleware / handler) and
        # must redact the same fields before persisting.
        raise HTTPException(
            status_code=409,
            detail="article_rag_index_ensure_unexpected_error",
        ) from None

    if result.status == ENSURE_STATUS_RECORD_NOT_FOUND:
        raise HTTPException(
            status_code=404, detail="Reader record not found"
        )
    return _build_article_rag_index_ensure_response(result)


# ===========================================================================
# T5.6c — Explicit section translation command
#
# Bounded synchronous orchestration of the existing
# ``SectionTranslationBootstrapService`` + ``SectionTranslationDrainService``
# behind an authenticated POST endpoint.
#
# Hard contracts:
#   * Identity comes only from ``AuthUserDep`` (user_id) and the path
#     (record_id). The body carries the full section range witness only.
#   * ``layer_family`` / ``record_id`` / ``base_id`` / ``generation`` are
#     server-authoritative and MUST NOT appear in the request body.
#   * The route calls only the public service entry points
#     (``request_section_translation`` and ``process_job_id``). It never
#     imports the ordinary enhancement worker loop, never schedules
#     background tasks, and never scans the section lane.
#   * No new ``job_type`` is introduced; section execution reuses the
#     existing ``TRANSLATION_BATCH_JOB_TYPE`` (translate_article /
#     unit_range_v1) via the bootstrap + drain services.
#   * Queued-recovery closure: when bootstrap returns NO_OP with
#     ``reason=section_job_already_queued`` and an existing ``job_id``, the
#     route MUST drain that job_id so a successful bootstrap write never
#     leaves a dead queue.
#   * Response shape is stable, minimal, and leak-safe (no prompt /
#     provider payload / envelope / secret is ever echoed).
# ===========================================================================


def _map_drain_outcome_to_response(
    *,
    outcome: SectionDrainOutcome,
    job_id: UUID | None,
) -> ReaderSectionTranslationResponse:
    """Map ``SectionDrainOutcome`` to the stable response shape."""
    if outcome is SectionDrainOutcome.SUCCEEDED:
        response_outcome: ReaderSectionTranslationOutcome = "succeeded"
    elif outcome is SectionDrainOutcome.RETRY_LATER:
        response_outcome = "retry_later"
    elif outcome is SectionDrainOutcome.FAILED:
        # Drain terminal failure → user-facing retry_later (not rejected).
        response_outcome = "retry_later"
    elif outcome is SectionDrainOutcome.ALREADY_CLAIMED:
        response_outcome = "already_covered_or_inflight"
    elif outcome is SectionDrainOutcome.BUDGET_DENIED:
        response_outcome = "budget_exhausted"
    elif outcome is SectionDrainOutcome.SUPERSEDED:
        response_outcome = "superseded"
    elif outcome is SectionDrainOutcome.REJECTED:
        response_outcome = "rejected"
    elif outcome is SectionDrainOutcome.NOT_FOUND:
        # Job disappeared between bootstrap and drain → no work to do.
        response_outcome = "rejected"
    else:  # pragma: no cover — defensive exhaustiveness
        response_outcome = "rejected"
    return ReaderSectionTranslationResponse(
        outcome=response_outcome,
        job_id=str(job_id) if job_id is not None else None,
        detail=None,
    )


@router.post(
    "/records/{record_id}/section-translation",
    response_model=ReaderSectionTranslationResponse,
    summary="Trigger synchronous explicit-section translation (T5.6c)",
)
async def submit_section_translation(
    record_id: UUID,
    body: ReaderSectionTranslationRequest,
    current_user: AuthUserDep,
) -> ReaderSectionTranslationResponse:
    """Bounded synchronous explicit-section translation command.

    The route constructs an :class:`ExplicitSectionIntent` with
    ``trigger=USER_EXPLICIT`` and ``layer_family='translation'`` from the
    authenticated user + body witness, calls
    :meth:`SectionTranslationBootstrapService.request_section_translation`,
    and (when admitted or when recovering an already-queued job) calls
    :meth:`SectionTranslationDrainService.process_job_id` with the
    bootstrap-returned ``job_id`` and the server-resolved fence.

    Bootstrap REJECT (forged range / source mismatch / non-trusted outline
    / node-only / family forge) and bootstrap NO_OP that is NOT a queued
    job (budget exhausted / already covered / range overlap) are returned
    directly without invoking drain.
    """
    user_id = UUID(current_user.user_id)
    intent = ExplicitSectionIntent(
        trigger=SectionRequestTrigger.USER_EXPLICIT,
        layer_family="translation",
        start_unit_id=body.start_unit_id,
        end_unit_id=body.end_unit_id,
        start_anchor_segment_id=body.start_anchor_segment_id,
        end_anchor_segment_id=body.end_anchor_segment_id,
        # node_id / outline_revision are audit-only; never sufficient for
        # admission. The planner ignores them for identity / fence.
        node_id=body.node_id,
        outline_revision=body.outline_revision,
    )

    bootstrap_service = SectionTranslationBootstrapService()
    try:
        bootstrap_result = await bootstrap_service.request_section_translation(
            record_id=record_id,
            user_id=user_id,
            intent=intent,
            authorized=True,
        )
    except LookupError:
        # Non-owner / missing record → 404 (no leak of internal detail).
        raise HTTPException(
            status_code=404, detail="Reader record not found"
        ) from None
    except ValueError:
        # Server-side fence conflict (e.g. stale generation) → 409 (no leak).
        raise HTTPException(
            status_code=409, detail="section_translation_fence_conflict"
        ) from None

    # Bootstrap REJECT → no drain (planner rejected the request).
    if bootstrap_result.outcome is SectionBootstrapOutcome.REJECT:
        return ReaderSectionTranslationResponse(
            outcome="rejected",
            job_id=(
                str(bootstrap_result.job_id)
                if bootstrap_result.job_id is not None
                else None
            ),
            detail=bootstrap_result.reason,
        )

    # Bootstrap NO_OP → only drain when recovering an already-queued job.
    # Other NO_OP reasons (budget exhausted / already covered / range
    # overlap) imply no claimable work and must NOT invoke drain.
    if bootstrap_result.outcome is SectionBootstrapOutcome.NO_OP:
        if (
            bootstrap_result.reason == REASON_ALREADY_QUEUED
            and bootstrap_result.job_id is not None
        ):
            # queued-recovery closure: drain the existing job so a
            # successful bootstrap write never leaves a dead queue.
            pass
        elif bootstrap_result.reason is not None and bootstrap_result.reason.startswith(
            "translation_budget_exhausted"
        ):
            return ReaderSectionTranslationResponse(
                outcome="budget_exhausted",
                job_id=None,
                detail=bootstrap_result.reason,
            )
        else:
            # already_covered / range_overlap / other planner NO_OP.
            return ReaderSectionTranslationResponse(
                outcome="already_covered_or_inflight",
                job_id=(
                    str(bootstrap_result.job_id)
                    if bootstrap_result.job_id is not None
                    else None
                ),
                detail=bootstrap_result.reason,
            )

    # ADMITTED or queued-recovery: drain the job_id with the
    # server-resolved fence from the bootstrap plan identity.
    job_id = bootstrap_result.job_id
    if job_id is None:
        # Defensive: bootstrap ADMITTED without a job_id is a contract
        # violation; surface as rejected (no leak).
        return ReaderSectionTranslationResponse(
            outcome="rejected",
            job_id=None,
            detail="bootstrap_admitted_without_job_id",
        )

    # The bootstrap plan's identity carries the server-resolved fence
    # (record_id / base_id / generation). For ADMITTED this is always
    # present; for queued-recovery it is present because the planner
    # still resolved the identity before observing the existing job.
    plan = bootstrap_result.plan
    expected_base_id: UUID | None = None
    expected_generation: int | None = None
    if plan is not None and getattr(plan, "identity", None) is not None:
        identity = plan.identity
        try:
            expected_base_id = UUID(identity.base_id)
        except (ValueError, AttributeError):
            expected_base_id = None
        try:
            expected_generation = int(identity.generation)
        except (ValueError, TypeError, AttributeError):
            expected_generation = None

    drain_service = SectionTranslationDrainService()
    drain_result = await drain_service.process_job_id(
        job_id=job_id,
        lease_owner="section_translation_route",
        expected_reading_record_id=record_id,
        expected_base_id=expected_base_id,
        expected_generation=expected_generation,
    )
    return _map_drain_outcome_to_response(
        outcome=drain_result.outcome,
        job_id=drain_result.job_id,
    )
