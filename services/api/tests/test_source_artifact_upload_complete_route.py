# task-history: (renamed from test_d6_i3i_source_artifact_upload_complete_route.py)
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

import pytest
from app.api.routes import reader_orchestration
from app.services.reader_orchestration.source_artifact_service import (
    SourceArtifactCompletionResult,
    SourceArtifactConflictError,
    SourceArtifactNotFoundError,
)

pytestmark = [
    pytest.mark.chain_reader_parse,
    pytest.mark.seam_api_contract,
    pytest.mark.life_permanent_regression,
]

AUTH_HEADERS = {"Authorization": "Bearer test-token"}

USER_ID = UUID("00000000-0000-0000-0000-000000000901")
ARTIFACT_ID = UUID("00000000-0000-0000-0000-000000000902")
CONTENT_SHA256 = "a" * 64
BUCKET = "claread-dev"
ENDPOINT = "https://oss-cn-shenzhen.aliyuncs.com"
SOURCE_FILENAME = "chapter-01.pdf"
OBJECT_KEY = f"dev/original-inputs/{USER_ID}/{ARTIFACT_ID}/{SOURCE_FILENAME}"


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(reader_orchestration.router)
    return app


def _route_path(artifact_id: UUID = ARTIFACT_ID) -> str:
    return f"/reader/source-artifacts/{artifact_id}/complete-upload"


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
    content_type: str | None = "application/pdf",
    byte_size: int | None = 4096,
    content_sha256: str | None = CONTENT_SHA256,
    idempotent_noop: bool = False,
) -> SourceArtifactCompletionResult:
    return SourceArtifactCompletionResult(
        artifact_id=ARTIFACT_ID,
        artifact_kind="original_upload",
        storage_provider="oss",
        bucket=BUCKET,
        endpoint=ENDPOINT,
        object_key=OBJECT_KEY,
        status="available",
        content_type=content_type,
        byte_size=byte_size,
        content_sha256=content_sha256,
        source_filename=SOURCE_FILENAME,
        idempotent_noop=idempotent_noop,
    )


def _mock_source_artifact_service(
    *,
    result: SourceArtifactCompletionResult | None = None,
    side_effect: Exception | None = None,
) -> tuple[patch, SimpleNamespace]:
    service = SimpleNamespace()
    service.complete_source_artifact_upload = AsyncMock(
        side_effect=side_effect,
        return_value=result,
    )
    return (
        patch(
            "app.api.routes.reader_orchestration.SourceArtifactService",
            return_value=service,
        ),
        service,
    )


def test_complete_source_artifact_upload_happy_path_calls_service_with_auth_user() -> None:
    app = _build_app()
    result = _build_result()
    service_patch, service = _mock_source_artifact_service(result=result)

    with _mock_auth(), service_patch, TestClient(app) as client:
        response = client.post(
            _route_path(),
            headers=AUTH_HEADERS,
            json={
                "content_type": "application/pdf",
                "byte_size": 4096,
                "content_sha256": CONTENT_SHA256,
                "metadata": {"scanner": "ios"},
                "quality": {"confidence": "high"},
            },
        )

    assert response.status_code == 200
    service.complete_source_artifact_upload.assert_awaited_once_with(
        user_id=USER_ID,
        artifact_id=ARTIFACT_ID,
        content_type="application/pdf",
        byte_size=4096,
        content_sha256=CONTENT_SHA256,
        metadata_json={"scanner": "ios"},
        quality_json={"confidence": "high"},
    )
    assert response.json() == {
        "artifact_id": str(ARTIFACT_ID),
        "artifact_kind": "original_upload",
        "storage_provider": "oss",
        "bucket": BUCKET,
        "endpoint": ENDPOINT,
        "object_key": OBJECT_KEY,
        "status": "available",
        "content_type": "application/pdf",
        "byte_size": 4096,
        "content_sha256": CONTENT_SHA256,
        "source_filename": SOURCE_FILENAME,
        "upload_completed": True,
        "idempotent_noop": False,
    }


def test_complete_source_artifact_upload_supports_idempotent_noop_response() -> None:
    app = _build_app()
    result = _build_result(idempotent_noop=True)
    service_patch, service = _mock_source_artifact_service(result=result)

    with _mock_auth(), service_patch, TestClient(app) as client:
        response = client.post(
            _route_path(),
            headers=AUTH_HEADERS,
            json={},
        )

    assert response.status_code == 200
    assert response.json()["idempotent_noop"] is True


def test_complete_source_artifact_upload_maps_missing_or_wrong_user_to_404() -> None:
    app = _build_app()
    service_patch, service = _mock_source_artifact_service(
        side_effect=SourceArtifactNotFoundError("source artifact not found"),
    )

    with _mock_auth(), service_patch, TestClient(app) as client:
        response = client.post(
            _route_path(),
            headers=AUTH_HEADERS,
            json={},
        )

    assert response.status_code == 404
    assert response.json() == {"detail": "source artifact not found"}
    service.complete_source_artifact_upload.assert_awaited_once()


def test_complete_source_artifact_upload_maps_state_conflicts_to_409() -> None:
    app = _build_app()
    service_patch, service = _mock_source_artifact_service(
        side_effect=SourceArtifactConflictError(
            "content_sha256 does not match the initialized artifact metadata"
        ),
    )

    with _mock_auth(), service_patch, TestClient(app) as client:
        response = client.post(
            _route_path(),
            headers=AUTH_HEADERS,
            json={"content_sha256": CONTENT_SHA256},
        )

    assert response.status_code == 409
    assert "content_sha256" in response.json()["detail"]
    service.complete_source_artifact_upload.assert_awaited_once()


def test_complete_source_artifact_upload_rejects_unknown_extra_field() -> None:
    app = _build_app()

    with (
        _mock_auth(),
        patch("app.api.routes.reader_orchestration.SourceArtifactService") as service_cls,
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(),
            headers=AUTH_HEADERS,
            json={"unexpected": True},
        )

    assert response.status_code == 422
    service_cls.assert_not_called()


def test_complete_source_artifact_upload_rejects_invalid_content_sha256() -> None:
    app = _build_app()

    with (
        _mock_auth(),
        patch("app.api.routes.reader_orchestration.SourceArtifactService") as service_cls,
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(),
            headers=AUTH_HEADERS,
            json={"content_sha256": "ABC123"},
        )

    assert response.status_code == 422
    service_cls.assert_not_called()


def test_complete_source_artifact_upload_rejects_negative_byte_size() -> None:
    app = _build_app()

    with (
        _mock_auth(),
        patch("app.api.routes.reader_orchestration.SourceArtifactService") as service_cls,
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(),
            headers=AUTH_HEADERS,
            json={"byte_size": -1},
        )

    assert response.status_code == 422
    service_cls.assert_not_called()


def test_complete_source_artifact_upload_does_not_accept_user_id_in_request_body() -> None:
    app = _build_app()

    with (
        _mock_auth(),
        patch("app.api.routes.reader_orchestration.SourceArtifactService") as service_cls,
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(),
            headers=AUTH_HEADERS,
            json={"user_id": str(uuid4())},
        )

    assert response.status_code == 422
    service_cls.assert_not_called()
