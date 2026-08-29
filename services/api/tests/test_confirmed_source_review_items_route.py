"""R8 Commit 1 — confirmed-source response carries structured review items.

Route-level contract tests (service mocked, no DB): the GET/PUT
``content_check`` array serializes the R8 Structured Review Item fields
(issue_id / tier / target_scope / source_anchor / anchor_hash /
evidence{excerpt_text, proposed_patch} / source_media_coordinate) and
rejects fabricated unknown fields (extra="forbid", fail closed).
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import reader_orchestration
from app.schemas.reader_documents import ConfirmedSourceDocument
from app.services.reader_orchestration.confirmed_source_application_service import (
    ConfirmedSourceCandidateSummary,
    ConfirmedSourceGetResult,
    ConfirmedSourceUpdateResult,
)

AUTH_HEADERS = {"Authorization": "Bearer test-token"}
NOW = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)

RECORD_ID = UUID("00000000-0000-0000-0000-000000000711")
USER_ID = UUID("00000000-0000-0000-0000-000000000712")
SOURCE_ID = UUID("00000000-0000-0000-0000-000000000713")
CANDIDATE_ID = UUID("00000000-0000-0000-0000-000000000714")
ORIGINAL_INPUT_ID = UUID("00000000-0000-0000-0000-000000000715")
SOURCE_HASH = "c" * 64

REVIEW_ITEM = {
    "code": "has_unclosed_fence",
    "message": "代码块缺少结束围栏",
    "classification": "content_check",
    "issue_id": "a1b2c3d4e5f6a7b8",
    "tier": "attention",
    "target_scope": "range",
    "source_anchor": {"block_id": None, "start_utf16": 3, "end_utf16": 12},
    "anchor_hash": "d" * 64,
    "evidence": {"excerpt_text": "```python", "proposed_patch": None},
    "source_media_coordinate": None,
}


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
        markdown_text="## A\n\n```python\nprint(1)\n",
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


def test_get_confirmed_source_serializes_structured_review_items() -> None:
    app = _build_app()
    get_mock = AsyncMock(
        return_value=ConfirmedSourceGetResult(
            source=_source_model(),
            updated_at=NOW,
            candidate=None,
            quality={"candidate_creation_version": "candidate_creation_v1"},
            adaptation_notice=[],
            content_check=[dict(REVIEW_ITEM)],
        )
    )
    with (
        _mock_auth(),
        patch.object(
            reader_orchestration.ConfirmedSourceApplicationService,
            "__init__",
            return_value=None,
        ),
        patch(_get_patch_target("get_confirmed_source"), new=get_mock),
        TestClient(app) as client,
    ):
        response = client.get(
            f"/reader/records/{RECORD_ID}/confirmed-source",
            headers=AUTH_HEADERS,
        )
    assert response.status_code == 200
    item = response.json()["content_check"][0]
    assert item["issue_id"] == "a1b2c3d4e5f6a7b8"
    assert item["tier"] == "attention"
    assert item["target_scope"] == "range"
    assert item["source_anchor"] == {
        "block_id": None,
        "start_utf16": 3,
        "end_utf16": 12,
    }
    assert item["anchor_hash"] == "d" * 64
    assert item["evidence"] == {
        "excerpt_text": "```python",
        "proposed_patch": None,
    }
    assert item["source_media_coordinate"] is None
    assert "code" in item and "classification" in item and "message" in item


def test_put_confirmed_source_serializes_structured_review_items() -> None:
    app = _build_app()
    put_mock = AsyncMock(
        return_value=ConfirmedSourceUpdateResult(
            revision=2,
            content_sha256=SOURCE_HASH,
            outcome="candidate_document_required",
            candidate=ConfirmedSourceCandidateSummary(
                candidate_document_id=CANDIDATE_ID,
                status="ready",
                canonical_text_preview="```python",
            ),
            quality={"candidate_creation_version": "candidate_creation_v1"},
            adaptation_notice=[
                {
                    "code": "raw_html_block",
                    "message": "m",
                    "classification": "adaptation_notice",
                }
            ],
            content_check=[dict(REVIEW_ITEM)],
            snapshot=None,
        )
    )
    with (
        _mock_auth(),
        patch.object(
            reader_orchestration.ConfirmedSourceApplicationService,
            "__init__",
            return_value=None,
        ),
        patch(_get_patch_target("update_confirmed_source"), new=put_mock),
        TestClient(app) as client,
    ):
        response = client.put(
            f"/reader/records/{RECORD_ID}/confirmed-source",
            headers=AUTH_HEADERS,
            json={
                "expected_revision": 1,
                "markdown_text": "## A\n\n```python\nprint(1)\n",
                "edit_source": "content_check",
            },
        )
    assert response.status_code == 200
    item = response.json()["content_check"][0]
    assert item["issue_id"] == "a1b2c3d4e5f6a7b8"
    assert item["evidence"]["excerpt_text"] == "```python"
    assert response.json()["adaptation_notice"][0]["code"] == "raw_html_block"


def test_review_item_with_unknown_field_fails_closed() -> None:
    """extra="forbid": a fabricated field must not serialize silently."""
    app = _build_app()
    bogus = dict(REVIEW_ITEM)
    bogus["fabricated_guess"] = "not-allowed"
    get_mock = AsyncMock(
        return_value=ConfirmedSourceGetResult(
            source=_source_model(),
            updated_at=NOW,
            candidate=None,
            quality={},
            adaptation_notice=[],
            content_check=[bogus],
        )
    )
    with (
        _mock_auth(),
        patch.object(
            reader_orchestration.ConfirmedSourceApplicationService,
            "__init__",
            return_value=None,
        ),
        patch(_get_patch_target("get_confirmed_source"), new=get_mock),
        # raise_server_exceptions=False: FastAPI's response-validation error
        # surfaces as HTTP 500 (fail closed) instead of a raised exception.
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        response = client.get(
            f"/reader/records/{RECORD_ID}/confirmed-source",
            headers=AUTH_HEADERS,
        )
    # Response contract cannot be built -> server-side fail closed (500),
    # never a partial/leaky payload.
    assert response.status_code == 500
