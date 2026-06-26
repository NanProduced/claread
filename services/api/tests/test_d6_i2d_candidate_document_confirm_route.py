from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import reader_orchestration
from app.schemas.reader_orchestration import ReaderSnapshotRecord
from app.services.reader_orchestration import (
    LowImpactReadingBaseBuildInput,
    build_low_impact_reading_base,
    build_reader_plate_snapshot,
)
from app.services.reader_orchestration.base_builder import (
    DETERMINISTIC_READING_BASE_BUILDER_VERSION,
    DETERMINISTIC_SEGMENTER_VERSION,
    EXACT_CANONICAL_TEXT_VERSION,
)
from app.services.reader_orchestration.candidate_document_confirm_application_service import (
    CandidateDocumentConfirmApplicationError,
    CandidateDocumentConfirmApplicationResult,
)

AUTH_HEADERS = {"Authorization": "Bearer test-token"}
NOW = datetime(2026, 6, 26, 12, 0, tzinfo=UTC)

READING_RECORD_ID = UUID("00000000-0000-0000-0000-000000000101")
CANDIDATE_DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000000102")
USER_ID = UUID("00000000-0000-0000-0000-000000000103")
STABLE_DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000000104")
BASE_ID = UUID("00000000-0000-0000-0000-000000000105")
ARTICLE_READY_EVENT_ID = UUID("00000000-0000-0000-0000-000000000106")

RECORD_GENERATION = 7
DOCUMENT_VERSION = 3
ARTICLE_READY_SEQUENCE = 17
CONTENT_SHA256 = "a" * 64
CANONICAL_TEXT_SHA256 = "b" * 64
BLOCK_COUNT = 2


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(reader_orchestration.router)
    return app


def _route_path(
    record_id: UUID = READING_RECORD_ID,
    candidate_document_id: UUID = CANDIDATE_DOCUMENT_ID,
) -> str:
    return (
        "/reader/records/"
        f"{record_id}/candidate-documents/{candidate_document_id}/confirm"
    )


def _session_info(user_id: UUID = USER_ID) -> object:
    return type(
        "SessionInfo",
        (),
        {
            "user_id": user_id,
            "session_id": uuid4(),
        },
    )()


def _mock_auth(user_id: UUID = USER_ID):
    return patch(
        "app.services.auth.dependencies.validate_session",
        new=AsyncMock(return_value=_session_info(user_id)),
    )


def _mock_application_service_init():
    return patch(
        "app.services.reader_orchestration."
        "candidate_document_confirm_application_service."
        "CandidateDocumentConfirmApplicationService.__init__",
        return_value=None,
    )


def _build_snapshot(
    *,
    record_id: UUID = READING_RECORD_ID,
    base_id: UUID = BASE_ID,
    generation: int = RECORD_GENERATION,
) -> object:
    build_result = build_low_impact_reading_base(
        LowImpactReadingBaseBuildInput(
            reading_record_id=str(record_id),
            base_id=str(base_id),
            source_text="First sentence.\n\nSecond paragraph.",
            title="Candidate Confirm Route",
            language="en",
        )
    )
    record = ReaderSnapshotRecord(
        title="Candidate Confirm Route",
        created_at=NOW,
        source_type="text",
        source_metadata={"source_kind": "route_test"},
        generation=generation,
        product_state="readable_enhancing",
        readiness_state="article_ready",
    )
    return build_reader_plate_snapshot(
        build_result,
        snapshot_taken_at=NOW,
        last_event_sequence=ARTICLE_READY_SEQUENCE,
        record=record,
        snapshot_id="snapshot-route-test",
    )


def _build_result(
    *,
    freeze_idempotent_noop: bool = False,
) -> CandidateDocumentConfirmApplicationResult:
    snapshot = _build_snapshot()
    return CandidateDocumentConfirmApplicationResult(
        reading_record_id=READING_RECORD_ID,
        candidate_document_id=CANDIDATE_DOCUMENT_ID,
        stable_document_id=STABLE_DOCUMENT_ID,
        base_id=BASE_ID,
        record_generation=RECORD_GENERATION,
        document_version=DOCUMENT_VERSION,
        content_sha256=CONTENT_SHA256,
        canonical_text_sha256=CANONICAL_TEXT_SHA256,
        block_count=BLOCK_COUNT,
        candidate_confirmed=True,
        freeze_idempotent_noop=freeze_idempotent_noop,
        article_ready_event_id=ARTICLE_READY_EVENT_ID,
        article_ready_sequence=ARTICLE_READY_SEQUENCE,
        snapshot=snapshot,
    )


def test_confirm_candidate_document_requires_authentication() -> None:
    app = _build_app()

    with TestClient(app) as client:
        response = client.post(_route_path(), json={"language": "en"})

    assert response.status_code == 401
    assert response.json() == {"detail": "Missing authorization header"}


def test_confirm_candidate_document_rejects_unknown_body_fields() -> None:
    app = _build_app()
    mock_confirm = AsyncMock()

    with (
        _mock_auth(),
        _mock_application_service_init(),
        patch(
            "app.services.reader_orchestration."
            "candidate_document_confirm_application_service."
            "CandidateDocumentConfirmApplicationService."
            "confirm_candidate_document_and_load_snapshot",
            new=mock_confirm,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(),
            headers=AUTH_HEADERS,
            json={
                "language": "en",
                "canonicalizer_version": "client-side-version",
            },
        )

    assert response.status_code == 422
    assert any(
        error["loc"] == ["body", "canonicalizer_version"]
        for error in response.json()["detail"]
    )
    mock_confirm.assert_not_awaited()


def test_confirm_candidate_document_calls_application_service_with_fixed_versions_and_serializes_response() -> None:
    app = _build_app()
    result = _build_result()
    mock_confirm = AsyncMock(return_value=result)

    with (
        _mock_auth(),
        _mock_application_service_init(),
        patch(
            "app.services.reader_orchestration."
            "candidate_document_confirm_application_service."
            "CandidateDocumentConfirmApplicationService."
            "confirm_candidate_document_and_load_snapshot",
            new=mock_confirm,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(),
            headers=AUTH_HEADERS,
            json={"language": "fr"},
        )

    assert response.status_code == 200
    mock_confirm.assert_awaited_once_with(
        candidate_document_id=CANDIDATE_DOCUMENT_ID,
        reading_record_id=READING_RECORD_ID,
        user_id=USER_ID,
        canonicalizer_version=EXACT_CANONICAL_TEXT_VERSION,
        builder_version=DETERMINISTIC_READING_BASE_BUILDER_VERSION,
        segmenter_version=DETERMINISTIC_SEGMENTER_VERSION,
        language="fr",
    )
    assert response.json() == {
        "reading_record_id": str(result.reading_record_id),
        "candidate_document_id": str(result.candidate_document_id),
        "stable_document_id": str(result.stable_document_id),
        "base_id": str(result.base_id),
        "record_generation": result.record_generation,
        "document_version": result.document_version,
        "content_sha256": result.content_sha256,
        "canonical_text_sha256": result.canonical_text_sha256,
        "block_count": result.block_count,
        "candidate_confirmed": result.candidate_confirmed,
        "freeze_idempotent_noop": result.freeze_idempotent_noop,
        "article_ready_event_id": str(result.article_ready_event_id),
        "article_ready_sequence": result.article_ready_sequence,
        "snapshot": result.snapshot.model_dump(mode="json"),
    }


def test_confirm_candidate_document_preserves_freeze_idempotent_noop_true() -> None:
    app = _build_app()
    mock_confirm = AsyncMock(return_value=_build_result(freeze_idempotent_noop=True))

    with (
        _mock_auth(),
        _mock_application_service_init(),
        patch(
            "app.services.reader_orchestration."
            "candidate_document_confirm_application_service."
            "CandidateDocumentConfirmApplicationService."
            "confirm_candidate_document_and_load_snapshot",
            new=mock_confirm,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(),
            headers=AUTH_HEADERS,
            json={"language": "en"},
        )

    assert response.status_code == 200
    assert response.json()["freeze_idempotent_noop"] is True


def test_confirm_candidate_document_maps_application_error_to_conflict_without_traceback() -> None:
    app = _build_app()
    mock_confirm = AsyncMock(
        side_effect=CandidateDocumentConfirmApplicationError("candidate conflict")
    )

    with (
        _mock_auth(),
        _mock_application_service_init(),
        patch(
            "app.services.reader_orchestration."
            "candidate_document_confirm_application_service."
            "CandidateDocumentConfirmApplicationService."
            "confirm_candidate_document_and_load_snapshot",
            new=mock_confirm,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(),
            headers=AUTH_HEADERS,
            json={"language": "en"},
        )

    assert response.status_code == 409
    assert response.json() == {"detail": "candidate conflict"}
    assert "Traceback" not in response.text
