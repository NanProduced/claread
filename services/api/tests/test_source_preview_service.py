"""R8 Commit 3 — source artifact preview gate (unit tests).

Pure fail-closed policy: only an owner's non-deleted, ``available``,
``oss``-stored artifact with an allowed preview MIME (PDF / images)
can be previewed; every other state collapses to denial (404 at the
route/service layer). No fuzzy or partial acceptance.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from app.services.reader_orchestration.source_preview_service import (
    PREVIEW_CONTENT_TYPES,
    evaluate_preview_gate,
)


def _owner() -> str:
    return str(uuid4())


def _other() -> str:
    return str(uuid4())


def test_allowed_pdf() -> None:
    owner = _owner()
    assert evaluate_preview_gate(
        owner_user_id=owner,
        artifact_user_id=owner,
        status="available",
        content_type="application/pdf",
        storage_provider="oss",
        deleted_at=None,
    )


def test_allowed_images() -> None:
    owner = _owner()
    for mime in ("image/png", "image/jpeg", "image/webp", "image/tiff"):
        assert evaluate_preview_gate(
            owner_user_id=owner,
            artifact_user_id=owner,
            status="available",
            content_type=mime,
            storage_provider="oss",
            deleted_at=None,
        ), mime


def test_cross_account_denied() -> None:
    assert not evaluate_preview_gate(
        owner_user_id=_owner(),
        artifact_user_id=_other(),
        status="available",
        content_type="application/pdf",
        storage_provider="oss",
        deleted_at=None,
    )


def test_deleted_artifact_denied() -> None:
    assert not evaluate_preview_gate(
        owner_user_id=_owner(),
        artifact_user_id=_owner(),
        status="available",
        content_type="application/pdf",
        storage_provider="oss",
        deleted_at=datetime.now(),
    )


def test_pending_and_failed_denied() -> None:
    for status in ("pending", "failed", "deleted"):
        assert not evaluate_preview_gate(
            owner_user_id=_owner(),
            artifact_user_id=_owner(),
            status=status,
            content_type="application/pdf",
            storage_provider="oss",
            deleted_at=None,
        ), status


def test_unsupported_mime_denied() -> None:
    for mime in ("text/plain", "text/markdown", "application/octet-stream"):
        assert not evaluate_preview_gate(
            owner_user_id=_owner(),
            artifact_user_id=_owner(),
            status="available",
            content_type=mime,
            storage_provider="oss",
            deleted_at=None,
        ), mime


def test_null_content_type_denied() -> None:
    assert not evaluate_preview_gate(
        owner_user_id=_owner(),
        artifact_user_id=_owner(),
        status="available",
        content_type=None,
        storage_provider="oss",
        deleted_at=None,
    )


def test_local_storage_denied() -> None:
    assert not evaluate_preview_gate(
        owner_user_id=_owner(),
        artifact_user_id=_owner(),
        status="available",
        content_type="application/pdf",
        storage_provider="local",
        deleted_at=None,
    )


def test_allowed_set_covers_pdf_and_images_only() -> None:
    assert "application/pdf" in PREVIEW_CONTENT_TYPES
    for mime in PREVIEW_CONTENT_TYPES:
        assert mime.startswith("image/") or mime == "application/pdf"
