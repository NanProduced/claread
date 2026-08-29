"""R8 Commit 3 — source artifact preview route wiring tests.

Service mocked (no DB): 200 shape (short-lived URL, never object_key /
bucket / endpoint / credentials fields), safe degrade (``preview_url``
null), and 404 collapse (same envelope as pipeline-status siblings).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import reader_orchestration
from app.services.reader_orchestration.source_preview_service import (
    SourceArtifactPreviewNotFoundError,
    SourceArtifactPreviewResult,
)

AUTH_HEADERS = {"Authorization": "Bearer test-token"}
NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
ARTIFACT_ID = UUID("00000000-0000-0000-0000-000000000731")
USER_ID = UUID("00000000-0000-0000-0000-000000000732")


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(reader_orchestration.router)
    return app


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


def _patch_preview(result) -> tuple:
    init_patch = patch.object(
        reader_orchestration.SourceArtifactPreviewService,
        "__init__",
        return_value=None,
    )
    method_patch = patch(
        "app.services.reader_orchestration.source_preview_service."
        "SourceArtifactPreviewService.create_preview",
        new=AsyncMock(return_value=result),
    )
    return init_patch, method_patch


def _patch_preview_error(exc: Exception) -> tuple:
    init_patch = patch.object(
        reader_orchestration.SourceArtifactPreviewService,
        "__init__",
        return_value=None,
    )
    method_patch = patch(
        "app.services.reader_orchestration.source_preview_service."
        "SourceArtifactPreviewService.create_preview",
        new=AsyncMock(side_effect=exc),
    )
    return init_patch, method_patch


def test_preview_returns_short_lived_url_without_storage_fields() -> None:
    app = _build_app()
    result = SourceArtifactPreviewResult(
        preview_url=(
            "https://fake-oss-signer.test/claread-dev.oss-cn-shenzhen."
            "aliyuncs.com/original-inputs/u/a/f.pdf?Expires=123&Signature=fake"
        ),
        expires_at=NOW + timedelta(seconds=900),
        content_type="application/pdf",
        degraded=False,
    )
    init_patch, method_patch = _patch_preview(result)
    with (
        _mock_auth(),
        init_patch,
        method_patch,
        TestClient(app) as client,
    ):
        response = client.get(
            f"/reader/source-artifacts/{ARTIFACT_ID}/preview",
            headers=AUTH_HEADERS,
        )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["preview_url"].startswith("https://fake-oss-signer.test/")
    assert "Signature=fake" in body["preview_url"]
    assert body["expires_at"] is not None
    assert body["content_type"] == "application/pdf"
    assert body["degraded"] is False
    # The response has NO independent storage-coordinate fields; the
    # presigned URL value itself is a sensitive temporary delivery value
    # (Web consumers must not write it into ordinary DOM — controlled
    # same-origin BFF / Blob URL delivery is the frozen contract).
    for leaked in ("object_key", "bucket", "endpoint", "secret"):
        assert leaked not in body


def test_preview_degrades_without_url_when_presigner_unavailable() -> None:
    app = _build_app()
    result = SourceArtifactPreviewResult(
        preview_url=None,
        expires_at=None,
        content_type="application/pdf",
        degraded=True,
    )
    init_patch, method_patch = _patch_preview(result)
    with (
        _mock_auth(),
        init_patch,
        method_patch,
        TestClient(app) as client,
    ):
        response = client.get(
            f"/reader/source-artifacts/{ARTIFACT_ID}/preview",
            headers=AUTH_HEADERS,
        )
    assert response.status_code == 200
    body = response.json()
    assert body["preview_url"] is None
    assert body["degraded"] is True


def test_preview_404_collapses() -> None:
    app = _build_app()
    init_patch, method_patch = _patch_preview_error(SourceArtifactPreviewNotFoundError("not found"))
    with (
        _mock_auth(),
        init_patch,
        method_patch,
        TestClient(app) as client,
    ):
        response = client.get(
            f"/reader/source-artifacts/{ARTIFACT_ID}/preview",
            headers=AUTH_HEADERS,
        )
    assert response.status_code == 404
    assert response.json()["detail"] == "Source artifact not found"
