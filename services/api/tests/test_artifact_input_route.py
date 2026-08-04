# task-history: D6-I3J (renamed from test_d6_i3j_artifact_input_route.py)
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import asyncpg
from fastapi import FastAPI
from fastapi.testclient import TestClient

import pytest
from app.api.routes import reader_orchestration
from app.services.reader_orchestration.artifact_input_application_service import (
    ArtifactInputApplicationConflictError,
    ArtifactInputApplicationError,
    ArtifactInputApplicationNotFoundError,
    ArtifactInputApplicationResult,
)

pytestmark = [
    pytest.mark.chain_reader_parse,
    pytest.mark.seam_api_contract,
    pytest.mark.life_permanent_regression,
]

AUTH_HEADERS = {"Authorization": "Bearer test-token"}

USER_ID = UUID("00000000-0000-0000-0000-000000000b01")
ARTIFACT_ID = UUID("00000000-0000-0000-0000-000000000b02")
READING_RECORD_ID = UUID("00000000-0000-0000-0000-000000000b03")
ORIGINAL_INPUT_ID = UUID("00000000-0000-0000-0000-000000000b04")
EXTRACTION_JOB_ID = UUID("00000000-0000-0000-0000-000000000b05")
EXTRACTION_JOB_STATUS = "queued"
CONTENT_SHA256 = "a" * 64
BUCKET = "claread-dev"
ENDPOINT = "https://oss-cn-shenzhen.aliyuncs.com"
SOURCE_FILENAME = "report.pdf"
OBJECT_KEY = f"dev/original-inputs/{USER_ID}/{ARTIFACT_ID}/{SOURCE_FILENAME}"


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(reader_orchestration.router)
    return app


def _route_path(artifact_id: UUID = ARTIFACT_ID) -> str:
    return f"/reader/source-artifacts/{artifact_id}/submit-input"


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


def _build_result(
    *,
    source_type: str = "pdf",
    input_type: str = "file_ref",
    title: str = "Uploaded PDF",
    language: str | None = "en",
) -> ArtifactInputApplicationResult:
    return ArtifactInputApplicationResult(
        reading_record_id=READING_RECORD_ID,
        original_input_id=ORIGINAL_INPUT_ID,
        artifact_id=ARTIFACT_ID,
        record_generation=1,
        source_type=source_type,  # type: ignore[arg-type]
        input_type=input_type,  # type: ignore[arg-type]
        product_state="processing",
        readiness_state="submitted",
        title=title,
        language=language,
        bucket=BUCKET,
        endpoint=ENDPOINT,
        object_key=OBJECT_KEY,
        content_type="application/pdf",
        byte_size=4096,
        content_sha256=CONTENT_SHA256,
        source_filename=SOURCE_FILENAME,
        extraction_job_id=EXTRACTION_JOB_ID,
        extraction_job_status=EXTRACTION_JOB_STATUS,
    )


def _application_error_with_cause(
    cause: Exception,
    message: str = "artifact input submission failed",
) -> ArtifactInputApplicationError:
    error = ArtifactInputApplicationError(message)
    error.__cause__ = cause
    return error


def _mock_artifact_input_service(
    *,
    result: ArtifactInputApplicationResult | None = None,
    side_effect: Exception | None = None,
) -> tuple[patch, SimpleNamespace]:
    service = SimpleNamespace()
    service.submit_available_artifact_as_input = AsyncMock(
        return_value=result,
        side_effect=side_effect,
    )
    return (
        patch(
            "app.api.routes.reader_orchestration.ArtifactInputApplicationService",
            return_value=service,
        ),
        service,
    )


def test_submit_source_artifact_as_input_happy_path_calls_service_and_serializes_response() -> None:
    app = _build_app()
    result = _build_result()
    service_patch, service = _mock_artifact_input_service(result=result)

    with _mock_auth(), service_patch, TestClient(app) as client:
        response = client.post(
            _route_path(),
            headers=AUTH_HEADERS,
            json={
                "title": " Uploaded PDF ",
                "language": " en ",
                "client_record_id": " client-artifact-001 ",
                "source_metadata": {"origin": "ios"},
            },
        )

    assert response.status_code == 200
    service.submit_available_artifact_as_input.assert_awaited_once_with(
        user_id=USER_ID,
        artifact_id=ARTIFACT_ID,
        title="Uploaded PDF",
        language="en",
        client_record_id="client-artifact-001",
        source_metadata={"origin": "ios"},
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
    )
    assert response.json() == {
        "reading_record_id": str(READING_RECORD_ID),
        "original_input_id": str(ORIGINAL_INPUT_ID),
        "artifact_id": str(ARTIFACT_ID),
        "record_generation": 1,
        "source_type": "pdf",
        "input_type": "file_ref",
        "product_state": "processing",
        "readiness_state": "submitted",
        "title": "Uploaded PDF",
        "language": "en",
        "extraction_required": True,
        "bucket": BUCKET,
        "endpoint": ENDPOINT,
        "object_key": OBJECT_KEY,
        "content_type": "application/pdf",
        "byte_size": 4096,
        "content_sha256": CONTENT_SHA256,
        "source_filename": SOURCE_FILENAME,
        "extraction_job_id": str(EXTRACTION_JOB_ID),
        "extraction_job_status": EXTRACTION_JOB_STATUS,
    }


def test_submit_source_artifact_as_input_trims_empty_client_record_id_to_none() -> None:
    app = _build_app()
    result = _build_result()
    service_patch, service = _mock_artifact_input_service(result=result)

    with _mock_auth(), service_patch, TestClient(app) as client:
        response = client.post(
            _route_path(),
            headers=AUTH_HEADERS,
            json={"client_record_id": "   "},
        )

    assert response.status_code == 200
    assert (
        service.submit_available_artifact_as_input.await_args.kwargs["client_record_id"]
        is None
    )


def test_submit_source_artifact_as_input_maps_missing_or_wrong_user_to_404() -> None:
    app = _build_app()
    service_patch, service = _mock_artifact_input_service(
        side_effect=ArtifactInputApplicationNotFoundError("source artifact not found"),
    )

    with _mock_auth(), service_patch, TestClient(app) as client:
        response = client.post(
            _route_path(),
            headers=AUTH_HEADERS,
            json={},
        )

    assert response.status_code == 404
    assert response.json() == {"detail": "source artifact not found"}
    service.submit_available_artifact_as_input.assert_awaited_once()


def test_submit_source_artifact_as_input_maps_status_conflict_to_409() -> None:
    app = _build_app()
    service_patch, service = _mock_artifact_input_service(
        side_effect=ArtifactInputApplicationConflictError(
            "source artifact is already bound to a reading_record/original_input"
        ),
    )

    with _mock_auth(), service_patch, TestClient(app) as client:
        response = client.post(
            _route_path(),
            headers=AUTH_HEADERS,
            json={},
        )

    assert response.status_code == 409
    assert "already bound" in response.json()["detail"]
    service.submit_available_artifact_as_input.assert_awaited_once()


def test_submit_source_artifact_as_input_maps_client_record_id_unique_conflict_to_409() -> None:
    app = _build_app()
    unique_violation = asyncpg.UniqueViolationError("duplicate client_record_id")
    unique_violation.constraint_name = "uq_reading_records_user_client_active"
    wrapped_cause = RuntimeError("reading record insert failed")
    wrapped_cause.__cause__ = unique_violation
    service_patch, service = _mock_artifact_input_service(
        side_effect=_application_error_with_cause(
            wrapped_cause,
            message="Failed to persist the artifact-backed input envelope",
        ),
    )

    with _mock_auth(), service_patch, TestClient(app) as client:
        response = client.post(
            _route_path(),
            headers=AUTH_HEADERS,
            json={"client_record_id": "duplicate-id"},
        )

    assert response.status_code == 409
    assert response.json() == {
        "detail": "client_record_id already exists for this user"
    }
    service.submit_available_artifact_as_input.assert_awaited_once()


def test_submit_source_artifact_as_input_rejects_user_id_body_field_without_calling_service() -> None:
    app = _build_app()

    with (
        _mock_auth(),
        patch(
            "app.api.routes.reader_orchestration.ArtifactInputApplicationService"
        ) as service_cls,
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(),
            headers=AUTH_HEADERS,
            json={"user_id": str(uuid4())},
        )

    assert response.status_code == 422
    service_cls.assert_not_called()


def test_submit_source_artifact_as_input_rejects_unknown_extra_field_without_calling_service() -> None:
    app = _build_app()

    with (
        _mock_auth(),
        patch(
            "app.api.routes.reader_orchestration.ArtifactInputApplicationService"
        ) as service_cls,
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(),
            headers=AUTH_HEADERS,
            json={"unexpected": True},
        )

    assert response.status_code == 422
    service_cls.assert_not_called()
