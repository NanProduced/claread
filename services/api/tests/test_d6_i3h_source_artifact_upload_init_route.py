from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import reader_orchestration
from app.services.reader_orchestration.source_artifact_service import (
    SourceArtifactError,
    SourceArtifactRegistrationResult,
)

AUTH_HEADERS = {"Authorization": "Bearer test-token"}

USER_ID = UUID("00000000-0000-0000-0000-000000000801")
ARTIFACT_ID = UUID("00000000-0000-0000-0000-000000000802")
READING_RECORD_ID = UUID("00000000-0000-0000-0000-000000000803")
ORIGINAL_INPUT_ID = UUID("00000000-0000-0000-0000-000000000804")
CONTENT_SHA256 = "a" * 64
BUCKET = "claread-dev"
ENDPOINT = "https://oss-cn-shenzhen.aliyuncs.com"
SOURCE_FILENAME = "chapter-01.pdf"
OBJECT_KEY = f"dev/original-inputs/{USER_ID}/{ARTIFACT_ID}/{SOURCE_FILENAME}"


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(reader_orchestration.router)
    return app


def _route_path() -> str:
    return "/reader/source-artifacts/init-upload"


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
    source_filename: str = SOURCE_FILENAME,
    content_type: str | None = "application/pdf",
    byte_size: int | None = 4096,
    content_sha256: str | None = CONTENT_SHA256,
) -> SourceArtifactRegistrationResult:
    return SourceArtifactRegistrationResult(
        artifact_id=ARTIFACT_ID,
        storage_provider="oss",
        bucket=BUCKET,
        object_key=f"dev/original-inputs/{USER_ID}/{ARTIFACT_ID}/{source_filename}",
        artifact_kind="original_upload",
        content_type=content_type,
        byte_size=byte_size,
        content_sha256=content_sha256,
        source_filename=source_filename,
        status="pending",
    )


def _build_object_ref(
    *,
    source_filename: str = SOURCE_FILENAME,
) -> dict[str, str]:
    return {
        "storage_provider": "oss",
        "bucket": BUCKET,
        "endpoint": ENDPOINT,
        "object_key": f"dev/original-inputs/{USER_ID}/{ARTIFACT_ID}/{source_filename}",
    }


def _mock_source_artifact_service(
    *,
    result: SourceArtifactRegistrationResult | None = None,
    register_side_effect: Exception | None = None,
    object_ref: dict[str, str] | None = None,
) -> tuple[patch, SimpleNamespace]:
    service = SimpleNamespace()
    service.register_source_artifact = AsyncMock(
        side_effect=register_side_effect,
        return_value=result,
    )
    service.build_oss_object_ref = Mock(return_value=object_ref or _build_object_ref())
    return (
        patch(
            "app.api.routes.reader_orchestration.SourceArtifactService",
            return_value=service,
        ),
        service,
    )


def test_init_source_artifact_upload_happy_path_calls_service_with_auth_user_and_pending_oss() -> None:
    app = _build_app()
    result = _build_result()
    service_patch, service = _mock_source_artifact_service(result=result)

    with _mock_auth(), service_patch, TestClient(app) as client:
        response = client.post(
            _route_path(),
            headers=AUTH_HEADERS,
            json={
                "artifact_kind": "original_upload",
                "source_filename": SOURCE_FILENAME,
                "content_type": "application/pdf",
                "byte_size": 4096,
                "content_sha256": CONTENT_SHA256,
                "reading_record_id": str(READING_RECORD_ID),
                "original_input_id": str(ORIGINAL_INPUT_ID),
                "source_refs": {"page_hint": 1},
                "metadata": {"origin": "ios"},
                "quality": {"dpi": 300},
            },
        )

    assert response.status_code == 200
    service.register_source_artifact.assert_awaited_once_with(
        user_id=USER_ID,
        artifact_kind="original_upload",
        reading_record_id=READING_RECORD_ID,
        original_input_id=ORIGINAL_INPUT_ID,
        storage_provider="oss",
        content_type="application/pdf",
        byte_size=4096,
        content_sha256=CONTENT_SHA256,
        source_filename=SOURCE_FILENAME,
        status="pending",
        source_refs_json={"page_hint": 1},
        metadata_json={"origin": "ios"},
        quality_json={"dpi": 300},
    )


def test_init_source_artifact_upload_response_includes_upload_target_without_credentials() -> None:
    app = _build_app()
    result = _build_result()
    object_ref = _build_object_ref()
    service_patch, service = _mock_source_artifact_service(
        result=result,
        object_ref=object_ref,
    )

    with _mock_auth(), service_patch, TestClient(app) as client:
        response = client.post(
            _route_path(),
            headers=AUTH_HEADERS,
            json={
                "artifact_kind": "original_upload",
                "source_filename": SOURCE_FILENAME,
                "content_type": "application/pdf",
                "byte_size": 4096,
                "content_sha256": CONTENT_SHA256,
            },
        )

    assert response.status_code == 200
    service.build_oss_object_ref.assert_called_once_with(
        user_id=USER_ID,
        artifact_id=ARTIFACT_ID,
        source_filename=SOURCE_FILENAME,
        artifact_kind="original_upload",
    )
    assert response.json() == {
        "artifact_id": str(ARTIFACT_ID),
        "artifact_kind": "original_upload",
        "storage_provider": "oss",
        "bucket": BUCKET,
        "endpoint": ENDPOINT,
        "object_key": OBJECT_KEY,
        "status": "pending",
        "content_type": "application/pdf",
        "byte_size": 4096,
        "content_sha256": CONTENT_SHA256,
        "source_filename": SOURCE_FILENAME,
        "upload_method": "oss_put_object_pending_credentials",
        "headers": {
            "content-type": "application/pdf",
            "content-sha256": CONTENT_SHA256,
        },
    }
    assert "authorization" not in {
        key.lower() for key in response.json()["headers"].keys()
    }


def test_init_source_artifact_upload_allows_pre_record_upload_with_null_ids() -> None:
    app = _build_app()
    result = _build_result()
    service_patch, service = _mock_source_artifact_service(result=result)

    with _mock_auth(), service_patch, TestClient(app) as client:
        response = client.post(
            _route_path(),
            headers=AUTH_HEADERS,
            json={
                "artifact_kind": "original_upload",
                "reading_record_id": None,
                "original_input_id": None,
            },
        )

    assert response.status_code == 200
    service.register_source_artifact.assert_awaited_once_with(
        user_id=USER_ID,
        artifact_kind="original_upload",
        reading_record_id=None,
        original_input_id=None,
        storage_provider="oss",
        content_type=None,
        byte_size=None,
        content_sha256=None,
        source_filename=None,
        status="pending",
        source_refs_json={},
        metadata_json={},
        quality_json={},
    )


def test_init_source_artifact_upload_passes_through_record_ids_when_provided() -> None:
    app = _build_app()
    result = _build_result()
    service_patch, service = _mock_source_artifact_service(result=result)

    with _mock_auth(), service_patch, TestClient(app) as client:
        response = client.post(
            _route_path(),
            headers=AUTH_HEADERS,
            json={
                "artifact_kind": "original_upload",
                "reading_record_id": str(READING_RECORD_ID),
                "original_input_id": str(ORIGINAL_INPUT_ID),
            },
        )

    assert response.status_code == 200
    service.register_source_artifact.assert_awaited_once()
    assert service.register_source_artifact.await_args.kwargs["reading_record_id"] == READING_RECORD_ID
    assert service.register_source_artifact.await_args.kwargs["original_input_id"] == ORIGINAL_INPUT_ID


def test_init_source_artifact_upload_rejects_invalid_content_sha256() -> None:
    app = _build_app()

    with (
        _mock_auth(),
        patch("app.api.routes.reader_orchestration.SourceArtifactService") as service_cls,
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(),
            headers=AUTH_HEADERS,
            json={
                "artifact_kind": "original_upload",
                "content_sha256": "ABC123",
            },
        )

    assert response.status_code == 422
    service_cls.assert_not_called()


def test_init_source_artifact_upload_rejects_negative_byte_size() -> None:
    app = _build_app()

    with (
        _mock_auth(),
        patch("app.api.routes.reader_orchestration.SourceArtifactService") as service_cls,
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(),
            headers=AUTH_HEADERS,
            json={
                "artifact_kind": "original_upload",
                "byte_size": -1,
            },
        )

    assert response.status_code == 422
    service_cls.assert_not_called()


def test_init_source_artifact_upload_rejects_unknown_extra_field() -> None:
    app = _build_app()

    with (
        _mock_auth(),
        patch("app.api.routes.reader_orchestration.SourceArtifactService") as service_cls,
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(),
            headers=AUTH_HEADERS,
            json={
                "artifact_kind": "original_upload",
                "unexpected": True,
            },
        )

    assert response.status_code == 422
    service_cls.assert_not_called()


def test_init_source_artifact_upload_rejects_non_original_upload_kind_without_calling_service() -> None:
    app = _build_app()

    with (
        _mock_auth(),
        patch("app.api.routes.reader_orchestration.SourceArtifactService") as service_cls,
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(),
            headers=AUTH_HEADERS,
            json={
                "artifact_kind": "ocr_result",
            },
        )

    assert response.status_code == 422
    service_cls.assert_not_called()


def test_init_source_artifact_upload_maps_source_artifact_error_to_422() -> None:
    app = _build_app()
    service_patch, service = _mock_source_artifact_service(
        register_side_effect=SourceArtifactError("invalid artifact metadata"),
    )

    with _mock_auth(), service_patch, TestClient(app) as client:
        response = client.post(
            _route_path(),
            headers=AUTH_HEADERS,
            json={
                "artifact_kind": "original_upload",
            },
        )

    assert response.status_code == 422
    assert response.json() == {"detail": "invalid artifact metadata"}
    service.build_oss_object_ref.assert_not_called()


def test_init_source_artifact_upload_does_not_accept_user_id_in_request_body() -> None:
    app = _build_app()

    with (
        _mock_auth(),
        patch("app.api.routes.reader_orchestration.SourceArtifactService") as service_cls,
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(),
            headers=AUTH_HEADERS,
            json={
                "artifact_kind": "original_upload",
                "user_id": str(uuid4()),
            },
        )

    assert response.status_code == 422
    service_cls.assert_not_called()
