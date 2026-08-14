"""Route-level secure citation navigation tests (ASK-PROV-)."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes.reader_record_ask import router as reader_record_ask_router
from app.services.reader_record_ask.citation_navigation import LiveDocumentFence

USER_ID = "00000000-0000-0000-0000-000000000001"
OTHER_USER_ID = "00000000-0000-0000-0000-000000000099"
RECORD_ID = "00000000-0000-0000-0000-0000000000a6"
BASE_ID = "00000000-0000-0000-0000-0000000000b6"
DOC_ID = "00000000-0000-0000-0000-0000000000d6"
MESSAGE_ID = "00000000-0000-0000-0000-0000000000e6"
AUTH_HEADERS = {"Authorization": "Bearer test_token"}


def _mock_auth(*, user_id: str = USER_ID):
    return patch(
        "app.services.auth.dependencies.validate_session",
        new_callable=AsyncMock,
        return_value=type(
            "SessionInfo",
            (),
            {
                "user_id": UUID(user_id),
                "session_id": uuid4(),
            },
        )(),
    )


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(reader_record_ask_router)
    return TestClient(app)


def _path(
    *,
    record_id: str = RECORD_ID,
    message_id: str = MESSAGE_ID,
    citation_id: str = "c1",
) -> str:
    return (
        f"/reader/records/{record_id}/ask/messages/{message_id}"
        f"/citations/{citation_id}/navigate"
    )


def _fence(
    *,
    base_id: str = BASE_ID,
    generation: int = 1,
    stable_document_id: str | None = DOC_ID,
) -> LiveDocumentFence:
    return LiveDocumentFence(
        reading_record_id=RECORD_ID,
        base_id=base_id,
        record_generation=generation,
        stable_document_id=stable_document_id,
    )


def _restricted(
    *,
    base_id: str = BASE_ID,
    generation: int = 1,
    stable_document_id: str = DOC_ID,
    citation_id: str = "c1",
    handle_id: str = "evh_" + ("ab" * 16),
) -> list[dict[str, Any]]:
    return [
        {
            "citation_id": citation_id,
            "handle_id": handle_id,
            "kind": "search_hit",
            "source_tool": "search_current_article",
            "snippet": "climate impacts",
            "unit_id": "u1",
            "anchor_segment_id": "s1",
            "evidence_scope": {
                "reading_record_id": RECORD_ID,
                "base_id": base_id,
                "record_generation": generation,
                "stable_document_id": stable_document_id,
            },
            "rag_citation": {
                "stable_document_id": stable_document_id,
                "base_id": base_id,
                "record_generation": generation,
                "unit_ids": ["u1"],
                "anchor_segment_ids": ["s1"],
                "canonical_text_start_utf16": 0,
                "canonical_text_end_utf16": 12,
            },
        }
    ]


def _message_row(
    *,
    evidence: list[dict[str, Any]] | None = None,
    final_status: str = "ok",
) -> dict[str, Any]:
    return {
        "message_id": MESSAGE_ID,
        "thread_id": str(uuid4()),
        "reading_record_id": RECORD_ID,
        "resolved_evidence_json": evidence if evidence is not None else _restricted(),
        "final_status": final_status,
        "execution_version": "reader_record_ask_agentic_v2",
    }


def _assert_no_evh(payload: Any) -> None:
    blob = json.dumps(payload, ensure_ascii=False)
    for forbidden in (
        "evh_",
        "handle_id",
        "envelope_fingerprint",
        "rag_substrate",
        "evidence_scope",
        "base_id",
        "stable_document_id",
        "record_generation",
        "reading_record_id",
    ):
        # Public response may contain status/reason strings only.
        # Forbid identity fields and handles in the whole JSON.
        if forbidden in {"base_id", "stable_document_id", "record_generation", "reading_record_id"}:
            # These must not appear as response keys or values of fence identity.
            assert f'"{forbidden}"' not in blob
        else:
            assert forbidden not in blob


def test_navigate_requires_auth() -> None:
    client_no_auth = _client()
    response = client_no_auth.post(_path())
    assert response.status_code == 401


@_mock_auth()
def test_navigate_success_returns_typed_location_only(mock_auth) -> None:
    del mock_auth
    client = _client()
    with (
        patch(
            "app.api.routes.reader_record_ask.load_live_document_fence",
            new_callable=AsyncMock,
            return_value=_fence(),
        ),
        patch(
            "app.api.routes.reader_record_ask.ReaderRecordAskRepository."
            "get_message_restricted_evidence_for_navigation",
            new_callable=AsyncMock,
            return_value=_message_row(),
        ),
    ):
        response = client.post(_path(), headers=AUTH_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["location"] == {
        "unit_id": "u1",
        "anchor_segment_id": "s1",
        "canonical_text_start_utf16": 0,
        "canonical_text_end_utf16": 12,
    }
    assert body.get("reason") is None
    _assert_no_evh(body)


@_mock_auth()
def test_navigate_message_not_found(mock_auth) -> None:
    del mock_auth
    client = _client()
    with (
        patch(
            "app.api.routes.reader_record_ask.load_live_document_fence",
            new_callable=AsyncMock,
            return_value=_fence(),
        ),
        patch(
            "app.api.routes.reader_record_ask.ReaderRecordAskRepository."
            "get_message_restricted_evidence_for_navigation",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        response = client.post(_path(), headers=AUTH_HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "not_found"
    assert body["location"] is None
    _assert_no_evh(body)


@_mock_auth()
def test_navigate_stale_generation(mock_auth) -> None:
    del mock_auth
    client = _client()
    with (
        patch(
            "app.api.routes.reader_record_ask.load_live_document_fence",
            new_callable=AsyncMock,
            return_value=_fence(generation=2),
        ),
        patch(
            "app.api.routes.reader_record_ask.ReaderRecordAskRepository."
            "get_message_restricted_evidence_for_navigation",
            new_callable=AsyncMock,
            return_value=_message_row(evidence=_restricted(generation=1)),
        ),
    ):
        response = client.post(_path(), headers=AUTH_HEADERS)
    body = response.json()
    assert body["status"] == "stale_generation"
    assert body["location"] is None
    _assert_no_evh(body)


@_mock_auth()
def test_navigate_base_mismatch(mock_auth) -> None:
    del mock_auth
    client = _client()
    other_base = "00000000-0000-0000-0000-0000000000ff"
    with (
        patch(
            "app.api.routes.reader_record_ask.load_live_document_fence",
            new_callable=AsyncMock,
            return_value=_fence(base_id=other_base),
        ),
        patch(
            "app.api.routes.reader_record_ask.ReaderRecordAskRepository."
            "get_message_restricted_evidence_for_navigation",
            new_callable=AsyncMock,
            return_value=_message_row(evidence=_restricted(base_id=BASE_ID)),
        ),
    ):
        response = client.post(_path(), headers=AUTH_HEADERS)
    body = response.json()
    assert body["status"] == "identity_mismatch"
    assert body["reason"] == "base"
    assert body["location"] is None
    _assert_no_evh(body)


@_mock_auth()
def test_navigate_stable_document_mismatch(mock_auth) -> None:
    del mock_auth
    client = _client()
    other_doc = "00000000-0000-0000-0000-0000000000ee"
    with (
        patch(
            "app.api.routes.reader_record_ask.load_live_document_fence",
            new_callable=AsyncMock,
            return_value=_fence(stable_document_id=other_doc),
        ),
        patch(
            "app.api.routes.reader_record_ask.ReaderRecordAskRepository."
            "get_message_restricted_evidence_for_navigation",
            new_callable=AsyncMock,
            return_value=_message_row(
                evidence=_restricted(stable_document_id=DOC_ID),
            ),
        ),
    ):
        response = client.post(_path(), headers=AUTH_HEADERS)
    body = response.json()
    assert body["status"] == "identity_mismatch"
    assert body["reason"] == "stable_document"
    _assert_no_evh(body)


@_mock_auth()
def test_navigate_client_cannot_override_fence_via_body(mock_auth) -> None:
    """Body fence fields are ignored / rejected; server fence wins."""
    del mock_auth
    client = _client()
    with (
        patch(
            "app.api.routes.reader_record_ask.load_live_document_fence",
            new_callable=AsyncMock,
            return_value=_fence(generation=1),
        ) as fence_loader,
        patch(
            "app.api.routes.reader_record_ask.ReaderRecordAskRepository."
            "get_message_restricted_evidence_for_navigation",
            new_callable=AsyncMock,
            return_value=_message_row(evidence=_restricted(generation=1)),
        ),
    ):
        # Attempt to smuggle a different generation/base via body.
        response = client.post(
            _path(),
            headers=AUTH_HEADERS,
            json={
                "base_id": "client-forged-base",
                "record_generation": 99,
                "stable_document_id": "client-forged-doc",
            },
        )
    # No body schema: FastAPI may 422 on unexpected body, or ignore.
    # Either way fence_loader must have been called without client fields,
    # and a forged generation must not produce ok against gen=1 evidence
    # when live fence is gen=1.
    assert response.status_code in {200, 422}
    if response.status_code == 200:
        body = response.json()
        assert body["status"] == "ok"
        _assert_no_evh(body)
    # Server fence loader never received client body kwargs.
    assert fence_loader.await_args is not None
    kwargs = fence_loader.await_args.kwargs
    assert "base_id" not in kwargs
    assert "record_generation" not in kwargs
    assert "stable_document_id" not in kwargs


@_mock_auth(user_id=OTHER_USER_ID)
def test_navigate_ownership_fail_closed(mock_auth) -> None:
    """Wrong owner: message load returns None (repo enforces user_id)."""
    del mock_auth
    client = _client()
    with (
        patch(
            "app.api.routes.reader_record_ask.load_live_document_fence",
            new_callable=AsyncMock,
            return_value=None,  # no owned record fence
        ),
        patch(
            "app.api.routes.reader_record_ask.ReaderRecordAskRepository."
            "get_message_restricted_evidence_for_navigation",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        response = client.post(_path(), headers=AUTH_HEADERS)
    body = response.json()
    assert body["status"] in {"unavailable", "not_found"}
    assert body["location"] is None
    _assert_no_evh(body)


@_mock_auth()
def test_navigate_record_fence_unavailable(mock_auth) -> None:
    del mock_auth
    client = _client()
    with patch(
        "app.api.routes.reader_record_ask.load_live_document_fence",
        new_callable=AsyncMock,
        return_value=None,
    ):
        response = client.post(_path(), headers=AUTH_HEADERS)
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["reason"] == "record_fence_unavailable"
    _assert_no_evh(body)


@_mock_auth()
def test_navigate_live_stable_missing_when_scope_claims_stable(mock_auth) -> None:
    del mock_auth
    client = _client()
    with (
        patch(
            "app.api.routes.reader_record_ask.load_live_document_fence",
            new_callable=AsyncMock,
            return_value=_fence(stable_document_id=None),
        ),
        patch(
            "app.api.routes.reader_record_ask.ReaderRecordAskRepository."
            "get_message_restricted_evidence_for_navigation",
            new_callable=AsyncMock,
            return_value=_message_row(
                evidence=_restricted(stable_document_id=DOC_ID),
            ),
        ),
    ):
        response = client.post(_path(), headers=AUTH_HEADERS)
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["reason"] == "live_stable_document_missing"
    assert body["location"] is None
    _assert_no_evh(body)


@_mock_auth()
def test_navigate_scope_stable_mismatch_route(mock_auth) -> None:
    del mock_auth
    client = _client()
    with (
        patch(
            "app.api.routes.reader_record_ask.load_live_document_fence",
            new_callable=AsyncMock,
            return_value=_fence(stable_document_id="doc-live"),
        ),
        patch(
            "app.api.routes.reader_record_ask.ReaderRecordAskRepository."
            "get_message_restricted_evidence_for_navigation",
            new_callable=AsyncMock,
            return_value=_message_row(
                evidence=_restricted(stable_document_id="doc-stored"),
            ),
        ),
    ):
        response = client.post(_path(), headers=AUTH_HEADERS)
    body = response.json()
    assert body["status"] == "identity_mismatch"
    assert body["reason"] == "stable_document"
    _assert_no_evh(body)
