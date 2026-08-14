# task-history: (renamed from test_d6_i2e_stable_document_route.py)
from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

import pytest
from app.api.routes import reader_orchestration
from app.services.reader_orchestration.stable_document_query_service import (
    StableDocumentProjectionAnchorSegment,
    StableDocumentProjectionBase,
    StableDocumentProjectionBlock,
    StableDocumentProjectionResult,
    StableDocumentProjectionStableDocument,
    StableDocumentQueryError,
)

pytestmark = [
    pytest.mark.chain_reader_parse,
    pytest.mark.seam_api_contract,
    pytest.mark.life_permanent_regression,
]

AUTH_HEADERS = {"Authorization": "Bearer test-token"}

RECORD_ID = UUID("00000000-0000-0000-0000-000000000301")
USER_ID = UUID("00000000-0000-0000-0000-000000000302")
BASE_ID = UUID("00000000-0000-0000-0000-000000000303")
STABLE_DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000000304")


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(reader_orchestration.router)
    return app


def _route_path(record_id: UUID = RECORD_ID) -> str:
    return f"/reader/records/{record_id}/stable-document"


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


def _build_result() -> StableDocumentProjectionResult:
    return StableDocumentProjectionResult(
        reading_record_id=RECORD_ID,
        record_generation=5,
        active_base_id=BASE_ID,
        base=StableDocumentProjectionBase(
            base_id=BASE_ID,
            content_sha256="a" * 64,
            content_utf16_length=321,
            canonicalizer_version="canon-v1",
            builder_version="builder-v1",
            segmenter_version="segmenter-v1",
            language="en",
            title_snapshot="Base Title",
            navigation={"units": [{"unit_id": "u1"}]},
            text="\nHeading\nHello stable doc.\n",
        ),
        stable_document=StableDocumentProjectionStableDocument(
            stable_document_id=STABLE_DOCUMENT_ID,
            document_version=3,
            title="Stable Title",
            language="en",
            source_profile={"source_refs": {"url": "https://example.com"}},
            content_sha256="b" * 64,
            status="active",
        ),
        blocks=(
            StableDocumentProjectionBlock(
                block_id="heading-1",
                parent_block_id=None,
                order_index=0,
                block_type="heading",
                text_content="Heading",
                payload={"level": 2},
                source_refs={"page": 1},
                quality={"warnings": []},
                canonical_text_start_utf16=0,
                canonical_text_end_utf16=7,
                interpretation_policy={
                    "allowed_source_scope": ["heading"],
                    "default_route": "main_reading",
                    "rag_eligible": True,
                },
            ),
            StableDocumentProjectionBlock(
                block_id="paragraph-1",
                parent_block_id=None,
                order_index=1,
                block_type="paragraph",
                text_content="Hello stable doc.",
                payload={"kind": "body"},
                source_refs={"page": 1, "line": 2},
                quality={"score": 0.98},
                canonical_text_start_utf16=9,
                canonical_text_end_utf16=26,
                interpretation_policy={
                    "allowed_source_scope": ["main_reading_text"],
                    "default_route": "main_reading",
                    "rag_eligible": True,
                },
            ),
        ),
        anchor_segments=(
            StableDocumentProjectionAnchorSegment(
                anchor_segment_id="as-heading",
                unit_id="u1",
                order_index=1,
                segment_type="sentence",
                base_start_utf16=0,
                base_end_utf16=7,
                text_hash="12345678",
            ),
            StableDocumentProjectionAnchorSegment(
                anchor_segment_id="as-paragraph",
                unit_id="u1",
                order_index=2,
                segment_type="sentence",
                base_start_utf16=9,
                base_end_utf16=26,
                text_hash="abcdef01",
            ),
        ),
    )


def test_get_reader_stable_document_requires_authentication() -> None:
    app = _build_app()

    with TestClient(app) as client:
        response = client.get(_route_path())

    assert response.status_code == 401
    assert response.json() == {"detail": "Missing authorization header"}


def test_get_reader_stable_document_success_calls_service_and_serializes_response() -> None:
    app = _build_app()
    result = _build_result()
    mock_load = AsyncMock(return_value=result)

    with (
        _mock_auth(),
        patch(
            "app.services.reader_orchestration.stable_document_query_service."
            "StableDocumentQueryService.load_active_stable_document",
            new=mock_load,
        ),
        TestClient(app) as client,
    ):
        response = client.get(
            _route_path(),
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 200
    mock_load.assert_awaited_once_with(
        record_id=RECORD_ID,
        user_id=USER_ID,
    )
    assert response.json() == {
        "reading_record_id": str(RECORD_ID),
        "record_generation": 5,
        "active_base_id": str(BASE_ID),
        "base": {
            "base_id": str(BASE_ID),
            "content_sha256": "a" * 64,
            "content_utf16_length": 321,
            "canonicalizer_version": "canon-v1",
            "builder_version": "builder-v1",
            "segmenter_version": "segmenter-v1",
            "language": "en",
            "title_snapshot": "Base Title",
            "navigation": {"units": [{"unit_id": "u1"}]},
            "text": "\nHeading\nHello stable doc.\n",
        },
        "stable_document": {
            "stable_document_id": str(STABLE_DOCUMENT_ID),
            "document_version": 3,
            "title": "Stable Title",
            "language": "en",
            "source_profile": {"source_refs": {"url": "https://example.com"}},
            "content_sha256": "b" * 64,
            "status": "active",
        },
        "blocks": [
            {
                "block_id": "heading-1",
                "parent_block_id": None,
                "order_index": 0,
                "block_type": "heading",
                "text_content": "Heading",
                "payload": {"level": 2},
                "source_refs": {"page": 1},
                "quality": {"warnings": []},
                "canonical_text_start_utf16": 0,
                "canonical_text_end_utf16": 7,
                "interpretation_policy": {
                    "allowed_source_scope": ["heading"],
                    "default_route": "main_reading",
                    "rag_eligible": True,
                },
            },
            {
                "block_id": "paragraph-1",
                "parent_block_id": None,
                "order_index": 1,
                "block_type": "paragraph",
                "text_content": "Hello stable doc.",
                "payload": {"kind": "body"},
                "source_refs": {"page": 1, "line": 2},
                "quality": {"score": 0.98},
                "canonical_text_start_utf16": 9,
                "canonical_text_end_utf16": 26,
                "interpretation_policy": {
                    "allowed_source_scope": ["main_reading_text"],
                    "default_route": "main_reading",
                    "rag_eligible": True,
                },
            },
        ],
        "anchor_segments": [
            {
                "anchor_segment_id": "as-heading",
                "unit_id": "u1",
                "order_index": 1,
                "segment_type": "sentence",
                "base_start_utf16": 0,
                "base_end_utf16": 7,
                "text_hash": "12345678",
            },
            {
                "anchor_segment_id": "as-paragraph",
                "unit_id": "u1",
                "order_index": 2,
                "segment_type": "sentence",
                "base_start_utf16": 9,
                "base_end_utf16": 26,
                "text_hash": "abcdef01",
            },
        ],
    }


def test_get_reader_stable_document_lookup_error_maps_to_404() -> None:
    app = _build_app()
    mock_load = AsyncMock(side_effect=LookupError("missing record"))

    with (
        _mock_auth(),
        patch(
            "app.services.reader_orchestration.stable_document_query_service."
            "StableDocumentQueryService.load_active_stable_document",
            new=mock_load,
        ),
        TestClient(app) as client,
    ):
        response = client.get(
            _route_path(),
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 404
    assert response.json() == {"detail": "Reader record not found"}
    assert "Traceback" not in response.text


def test_get_reader_stable_document_query_error_maps_to_409_without_traceback() -> None:
    app = _build_app()
    mock_load = AsyncMock(
        side_effect=StableDocumentQueryError("stable document facts incomplete")
    )

    with (
        _mock_auth(),
        patch(
            "app.services.reader_orchestration.stable_document_query_service."
            "StableDocumentQueryService.load_active_stable_document",
            new=mock_load,
        ),
        TestClient(app) as client,
    ):
        response = client.get(
            _route_path(),
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 409
    assert response.json() == {"detail": "stable document facts incomplete"}
    assert "Traceback" not in response.text


def test_route_response_includes_text_and_anchor_segments() -> None:
    from app.services.reader_orchestration.stable_document_query_service import (
        StableDocumentProjectionAnchorSegment,
        StableDocumentProjectionBase,
        StableDocumentProjectionBlock,
        StableDocumentProjectionStableDocument,
        StableDocumentProjectionResult,
    )
    from app.api.routes.reader_orchestration import _build_stable_document_route_response

    result = StableDocumentProjectionResult(
        reading_record_id=UUID(int=1),
        record_generation=1,
        active_base_id=UUID(int=2),
        base=StableDocumentProjectionBase(
            base_id=UUID(int=2),
            content_sha256="a" * 64,
            content_utf16_length=10,
            canonicalizer_version="c",
            builder_version="b",
            segmenter_version="s",
            language="en",
            title_snapshot=None,
            navigation={},
            text="\nhello\n",
        ),
        stable_document=StableDocumentProjectionStableDocument(
            stable_document_id=UUID(int=3),
            document_version=1,
            title=None,
            language="en",
            source_profile={},
            content_sha256="a" * 64,
            status="active",
        ),
        blocks=(
            StableDocumentProjectionBlock(
                block_id="b1",
                parent_block_id=None,
                order_index=0,
                block_type="paragraph",
                text_content="hello",
                payload={},
                source_refs={},
                quality={},
                canonical_text_start_utf16=1,
                canonical_text_end_utf16=6,
                interpretation_policy={},
            ),
        ),
        anchor_segments=(
            StableDocumentProjectionAnchorSegment(
                anchor_segment_id="as-1",
                unit_id="u1",
                order_index=1,
                segment_type="sentence",
                base_start_utf16=1,
                base_end_utf16=6,
                text_hash="12345678",
            ),
        ),
    )
    resp = _build_stable_document_route_response(result)
    assert resp.base.text == "\nhello\n"
    assert [a.anchor_segment_id for a in resp.anchor_segments] == ["as-1"]
