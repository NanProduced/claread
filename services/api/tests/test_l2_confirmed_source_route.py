"""L2 — Confirmed Source GET/PUT 端点路由接线与错误映射测试。

服务层行为由 ``test_l2_confirmed_source_lifecycle_db.py``（真实 DB）
封锁；本文件只验证路由层：200 DTO 形状、404 collapse、409
root-level 错误合同（code / resolution / current_revision）、以及
confirm 端点新增的 stale_candidate_revision 映射（旧 409 映射不变）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import reader_orchestration
from app.schemas.reader_documents import ConfirmedSourceDocument
from app.services.reader_orchestration.candidate_document_confirm_application_service import (
    CandidateDocumentConfirmApplicationError,
    StaleCandidateRevisionApplicationError,
)
from app.services.reader_orchestration.confirmed_source_application_service import (
    ConfirmedSourceConflictError,
    ConfirmedSourceGetResult,
    ConfirmedSourceNotFoundError,
    ConfirmedSourceUpdateResult,
)

AUTH_HEADERS = {"Authorization": "Bearer test-token"}
NOW = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)

RECORD_ID = UUID("00000000-0000-0000-0000-000000000701")
USER_ID = UUID("00000000-0000-0000-0000-000000000702")
SOURCE_ID = UUID("00000000-0000-0000-0000-000000000703")
CANDIDATE_ID = UUID("00000000-0000-0000-0000-000000000704")
ORIGINAL_INPUT_ID = UUID("00000000-0000-0000-0000-000000000705")
SOURCE_HASH = "c" * 64


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


def _source_model(revision: int = 1) -> ConfirmedSourceDocument:
    return ConfirmedSourceDocument(
        id=str(SOURCE_ID),
        reading_record_id=str(RECORD_ID),
        user_id=str(USER_ID),
        record_generation=1,
        original_input_id=str(ORIGINAL_INPUT_ID),
        markdown_text="## Title\n\nBody text.",
        revision=revision,
        content_sha256=SOURCE_HASH,
        status="draft",
        edit_source="initial",
    )


def _get_patch_target(suffix: str) -> str:
    return (
        "app.services.reader_orchestration."
        f"confirmed_source_application_service."
        f"ConfirmedSourceApplicationService.{suffix}"
    )


# ---------------------------------------------------------------------------
# GET /records/{id}/confirmed-source
# ---------------------------------------------------------------------------


def test_get_confirmed_source_returns_draft_body() -> None:
    app = _build_app()
    get_mock = AsyncMock(
        return_value=ConfirmedSourceGetResult(
            source=_source_model(),
            updated_at=NOW,
            candidate=None,
            quality={"candidate_creation_version": "candidate_creation_v1"},
            adaptation_notice=[
                {
                    "code": "raw_html_block",
                    "message": "m",
                    "classification": "adaptation_notice",
                }
            ],
            content_check=[
                {
                    "code": "image_ocr_uncertain",
                    "message": "m",
                    "classification": "content_check",
                }
            ],
        )
    )
    with (
        _mock_auth(),
        patch.object(
            reader_orchestration.ConfirmedSourceApplicationService,
            "__init__",
            return_value=None,
        ),
        patch(
            _get_patch_target("get_confirmed_source"),
            new=get_mock,
        ),
        TestClient(app) as client,
    ):
        response = client.get(
            f"/reader/records/{RECORD_ID}/confirmed-source",
            headers=AUTH_HEADERS,
        )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["source_document_id"] == str(SOURCE_ID)
    assert body["revision"] == 1
    assert body["status"] == "draft"
    assert body["markdown_text"] == "## Title\n\nBody text."
    assert body["content_sha256"] == SOURCE_HASH
    assert body["candidate"] is None
    # L2 联调：三级分类字段映射（与 PUT 响应语义一致）。
    assert body["quality"] == {
        "candidate_creation_version": "candidate_creation_v1"
    }
    assert body["adaptation_notice"][0]["code"] == "raw_html_block"
    assert body["adaptation_notice"][0]["classification"] == "adaptation_notice"
    assert body["content_check"][0]["code"] == "image_ocr_uncertain"
    assert body["content_check"][0]["classification"] == "content_check"


def test_get_confirmed_source_404_collapse() -> None:
    app = _build_app()
    get_mock = AsyncMock(side_effect=ConfirmedSourceNotFoundError("gone"))
    with (
        _mock_auth(),
        patch.object(
            reader_orchestration.ConfirmedSourceApplicationService,
            "__init__",
            return_value=None,
        ),
        patch(
            _get_patch_target("get_confirmed_source"),
            new=get_mock,
        ),
        TestClient(app) as client,
    ):
        response = client.get(
            f"/reader/records/{RECORD_ID}/confirmed-source",
            headers=AUTH_HEADERS,
        )
    assert response.status_code == 404
    body = response.json()
    assert body["ok"] is False
    assert body["code"] == "not_found"


def test_get_confirmed_source_409_record_state_advanced() -> None:
    app = _build_app()
    get_mock = AsyncMock(
        side_effect=ConfirmedSourceConflictError(
            "source frozen",
            code="record_state_advanced",
            resolution="open_reader",
            current_revision=3,
        )
    )
    with (
        _mock_auth(),
        patch.object(
            reader_orchestration.ConfirmedSourceApplicationService,
            "__init__",
            return_value=None,
        ),
        patch(
            _get_patch_target("get_confirmed_source"),
            new=get_mock,
        ),
        TestClient(app) as client,
    ):
        response = client.get(
            f"/reader/records/{RECORD_ID}/confirmed-source",
            headers=AUTH_HEADERS,
        )
    assert response.status_code == 409
    body = response.json()
    assert body == {
        "ok": False,
        "code": "record_state_advanced",
        "resolution": "open_reader",
        "message": "source frozen",
        "current_revision": 3,
    }


# ---------------------------------------------------------------------------
# PUT /records/{id}/confirmed-source
# ---------------------------------------------------------------------------


def _update_result(outcome: str = "candidate_document_required") -> ConfirmedSourceUpdateResult:
    return ConfirmedSourceUpdateResult(
        revision=2,
        content_sha256=SOURCE_HASH,
        outcome=outcome,
        candidate=None,
        quality={"candidate_creation_version": "candidate_creation_v1"},
        adaptation_notice=[
            {
                "code": "raw_html_block",
                "message": "m",
                "classification": "adaptation_notice",
            }
        ],
        content_check=[
            {
                "code": "image_ocr_uncertain",
                "message": "m",
                "classification": "content_check",
            }
        ],
        snapshot=None,
    )


def test_put_confirmed_source_returns_update_response() -> None:
    app = _build_app()
    put_mock = AsyncMock(return_value=_update_result())
    with (
        _mock_auth(),
        patch.object(
            reader_orchestration.ConfirmedSourceApplicationService,
            "__init__",
            return_value=None,
        ),
        patch(
            _get_patch_target("update_confirmed_source"),
            new=put_mock,
        ),
        TestClient(app) as client,
    ):
        response = client.put(
            f"/reader/records/{RECORD_ID}/confirmed-source",
            headers=AUTH_HEADERS,
            json={
                "expected_revision": 1,
                "markdown_text": "## Edited\n\nBody text.",
                "edit_source": "source_mode",
            },
        )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["revision"] == 2
    assert body["outcome"] == "candidate_document_required"
    assert body["adaptation_notice"][0]["code"] == "raw_html_block"
    assert body["content_check"][0]["code"] == "image_ocr_uncertain"
    put_mock.assert_awaited_once()
    kwargs = put_mock.await_args.kwargs
    assert kwargs["expected_revision"] == 1
    assert kwargs["markdown_text"] == "## Edited\n\nBody text."
    assert kwargs["edit_source"] == "source_mode"


def test_put_confirmed_source_409_stale_source_revision() -> None:
    app = _build_app()
    put_mock = AsyncMock(
        side_effect=ConfirmedSourceConflictError(
            "revision is 2, expected 1",
            code="stale_source_revision",
            resolution="reload",
            current_revision=2,
        )
    )
    with (
        _mock_auth(),
        patch.object(
            reader_orchestration.ConfirmedSourceApplicationService,
            "__init__",
            return_value=None,
        ),
        patch(
            _get_patch_target("update_confirmed_source"),
            new=put_mock,
        ),
        TestClient(app) as client,
    ):
        response = client.put(
            f"/reader/records/{RECORD_ID}/confirmed-source",
            headers=AUTH_HEADERS,
            json={"expected_revision": 1, "markdown_text": "x"},
        )
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "stale_source_revision"
    assert body["resolution"] == "reload"
    assert body["current_revision"] == 2


def test_put_confirmed_source_409_source_frozen() -> None:
    app = _build_app()
    put_mock = AsyncMock(
        side_effect=ConfirmedSourceConflictError(
            "frozen",
            code="source_frozen",
            resolution="open_reader",
        )
    )
    with (
        _mock_auth(),
        patch.object(
            reader_orchestration.ConfirmedSourceApplicationService,
            "__init__",
            return_value=None,
        ),
        patch(
            _get_patch_target("update_confirmed_source"),
            new=put_mock,
        ),
        TestClient(app) as client,
    ):
        response = client.put(
            f"/reader/records/{RECORD_ID}/confirmed-source",
            headers=AUTH_HEADERS,
            json={"expected_revision": 1, "markdown_text": "x"},
        )
    assert response.status_code == 409
    assert response.json()["code"] == "source_frozen"
    assert response.json()["resolution"] == "open_reader"


def test_put_confirmed_source_404_collapse() -> None:
    app = _build_app()
    put_mock = AsyncMock(side_effect=ConfirmedSourceNotFoundError("gone"))
    with (
        _mock_auth(),
        patch.object(
            reader_orchestration.ConfirmedSourceApplicationService,
            "__init__",
            return_value=None,
        ),
        patch(
            _get_patch_target("update_confirmed_source"),
            new=put_mock,
        ),
        TestClient(app) as client,
    ):
        response = client.put(
            f"/reader/records/{RECORD_ID}/confirmed-source",
            headers=AUTH_HEADERS,
            json={"expected_revision": 1, "markdown_text": "x"},
        )
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


# ---------------------------------------------------------------------------
# confirm 端点：stale_candidate_revision 新映射（旧映射不变）
# ---------------------------------------------------------------------------


def test_confirm_maps_stale_candidate_revision_to_structured_409() -> None:
    app = _build_app()
    confirm_mock = AsyncMock(
        side_effect=StaleCandidateRevisionApplicationError(
            "candidate references revision 1, current is 2",
            current_revision=2,
            current_content_sha256=SOURCE_HASH,
        )
    )
    with (
        _mock_auth(),
        patch.object(
            reader_orchestration.CandidateDocumentConfirmApplicationService,
            "__init__",
            return_value=None,
        ),
        patch(
            "app.services.reader_orchestration."
            "candidate_document_confirm_application_service."
            "CandidateDocumentConfirmApplicationService."
            "confirm_candidate_document_and_load_snapshot",
            new=confirm_mock,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            f"/reader/records/{RECORD_ID}/candidate-documents/{CANDIDATE_ID}/confirm",
            headers=AUTH_HEADERS,
            json={},
        )
    assert response.status_code == 409
    body = response.json()
    assert body == {
        "ok": False,
        "code": "stale_candidate_revision",
        "resolution": "reload",
        "message": "确认内容已过期，请重新加载最新待确认版本。",
        "current_revision": 2,
    }


def test_confirm_generic_error_keeps_legacy_409_mapping() -> None:
    app = _build_app()
    confirm_mock = AsyncMock(
        side_effect=CandidateDocumentConfirmApplicationError("some failure")
    )
    with (
        _mock_auth(),
        patch.object(
            reader_orchestration.CandidateDocumentConfirmApplicationService,
            "__init__",
            return_value=None,
        ),
        patch(
            "app.services.reader_orchestration."
            "candidate_document_confirm_application_service."
            "CandidateDocumentConfirmApplicationService."
            "confirm_candidate_document_and_load_snapshot",
            new=confirm_mock,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            f"/reader/records/{RECORD_ID}/candidate-documents/{CANDIDATE_ID}/confirm",
            headers=AUTH_HEADERS,
            json={},
        )
    assert response.status_code == 409
    assert "some failure" in response.json()["detail"]
