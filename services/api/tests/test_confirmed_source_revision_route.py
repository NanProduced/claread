"""R8 Commit 2 — confirmed-source revision history route wiring tests.

Service mocked (no DB). Verifies: list / get / restore DTO shapes,
404 collapse, and the 409 root-level conflict contract
(``stale_source_revision`` with ``current_revision`` / ``source_frozen``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import reader_orchestration
from app.services.reader_orchestration.confirmed_source_application_service import (
    ConfirmedSourceConflictError,
)
from app.services.reader_orchestration.confirmed_source_revision_service import (
    ConfirmedSourceRestoreResult,
    ConfirmedSourceRevisionListResult,
    ConfirmedSourceRevisionNotFoundError,
)

AUTH_HEADERS = {"Authorization": "Bearer test-token"}
NOW = datetime(2026, 8, 29, 11, 0, tzinfo=UTC)

RECORD_ID = UUID("00000000-0000-0000-0000-000000000721")
USER_ID = UUID("00000000-0000-0000-0000-000000000722")


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


def _patch_service_method(method: str, return_value) -> tuple:
    init_patch = patch.object(
        reader_orchestration.ConfirmedSourceRevisionService,
        "__init__",
        return_value=None,
    )
    method_patch = patch(
        f"app.services.reader_orchestration."
        f"confirmed_source_revision_service."
        f"ConfirmedSourceRevisionService.{method}",
        new=AsyncMock(return_value=return_value),
    )
    return init_patch, method_patch


def _patch_service_error(method: str, exc: Exception) -> tuple:
    init_patch = patch.object(
        reader_orchestration.ConfirmedSourceRevisionService,
        "__init__",
        return_value=None,
    )
    method_patch = patch(
        f"app.services.reader_orchestration."
        f"confirmed_source_revision_service."
        f"ConfirmedSourceRevisionService.{method}",
        new=AsyncMock(side_effect=exc),
    )
    return init_patch, method_patch


def test_list_revisions_returns_metadata() -> None:
    app = _build_app()
    result = ConfirmedSourceRevisionListResult(
        revisions=[
            {
                "revision": 2,
                "snapshot_reason": "save",
                "edit_source": "wysiwyg",
                "content_sha256": "b" * 64,
                "created_at": NOW,
            },
            {
                "revision": 1,
                "snapshot_reason": "initial",
                "edit_source": "initial",
                "content_sha256": "a" * 64,
                "created_at": NOW,
            },
        ]
    )
    init_patch, method_patch = _patch_service_method("list_revisions", result)
    with (
        _mock_auth(),
        init_patch,
        method_patch,
        TestClient(app) as client,
    ):
        response = client.get(
            f"/reader/records/{RECORD_ID}/confirmed-source/revisions",
            headers=AUTH_HEADERS,
        )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["revisions"][0]["revision"] == 2
    assert body["revisions"][0]["snapshot_reason"] == "save"
    assert "markdown_text" not in body["revisions"][0]


def test_get_revision_returns_full_snapshot() -> None:
    app = _build_app()
    full = {
        "revision": 1,
        "snapshot_reason": "initial",
        "edit_source": "initial",
        "markdown_text": "## Original body",
        "content_sha256": "a" * 64,
        "created_at": NOW,
    }

    class _Result:
        revision = full

    init_patch, method_patch = _patch_service_method("get_revision", _Result())
    with (
        _mock_auth(),
        init_patch,
        method_patch,
        TestClient(app) as client,
    ):
        response = client.get(
            f"/reader/records/{RECORD_ID}/confirmed-source/revisions/1",
            headers=AUTH_HEADERS,
        )
    assert response.status_code == 200
    assert response.json()["markdown_text"] == "## Original body"
    assert response.json()["snapshot_reason"] == "initial"


def test_get_revision_404_collapses() -> None:
    app = _build_app()
    init_patch, method_patch = _patch_service_error(
        "get_revision", ConfirmedSourceRevisionNotFoundError("x")
    )
    with (
        _mock_auth(),
        init_patch,
        method_patch,
        TestClient(app) as client,
    ):
        response = client.get(
            f"/reader/records/{RECORD_ID}/confirmed-source/revisions/99",
            headers=AUTH_HEADERS,
        )
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_restore_returns_new_revision() -> None:
    app = _build_app()
    result = ConfirmedSourceRestoreResult(
        revision=4,
        content_sha256="d" * 64,
        markdown_text="## Restored body",
        restored_to=1,
    )
    init_patch, method_patch = _patch_service_method("restore_revision", result)
    with (
        _mock_auth(),
        init_patch,
        method_patch,
        TestClient(app) as client,
    ):
        response = client.post(
            f"/reader/records/{RECORD_ID}/confirmed-source/restore",
            headers=AUTH_HEADERS,
            json={"expected_revision": 3, "target_revision": 1},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["revision"] == 4
    assert body["restored_to"] == 1
    assert body["markdown_text"] == "## Restored body"


def test_restore_stale_source_revision_409() -> None:
    app = _build_app()
    conflict = ConfirmedSourceConflictError(
        "expected revision 3 but current revision is 5",
        code="stale_source_revision",
        resolution="reload",
        current_revision=5,
    )
    init_patch, method_patch = _patch_service_error("restore_revision", conflict)
    with (
        _mock_auth(),
        init_patch,
        method_patch,
        TestClient(app) as client,
    ):
        response = client.post(
            f"/reader/records/{RECORD_ID}/confirmed-source/restore",
            headers=AUTH_HEADERS,
            json={"expected_revision": 3, "target_revision": 1},
        )
    assert response.status_code == 409
    body = response.json()
    assert body["ok"] is False
    assert body["code"] == "stale_source_revision"
    assert body["resolution"] == "reload"
    assert body["current_revision"] == 5


def test_restore_frozen_source_409() -> None:
    app = _build_app()
    conflict = ConfirmedSourceConflictError(
        "confirmed source is frozen",
        code="source_frozen",
        resolution="open_reader",
        current_revision=1,
    )
    init_patch, method_patch = _patch_service_error("restore_revision", conflict)
    with (
        _mock_auth(),
        init_patch,
        method_patch,
        TestClient(app) as client,
    ):
        response = client.post(
            f"/reader/records/{RECORD_ID}/confirmed-source/restore",
            headers=AUTH_HEADERS,
            json={"expected_revision": 1, "target_revision": 1},
        )
    assert response.status_code == 409
    assert response.json()["code"] == "source_frozen"
