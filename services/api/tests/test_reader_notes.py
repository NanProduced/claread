"""Post-exit reader note contract tests.

DATA-LEGACY-IDENTITY-EXIT: the Reading Record anchor is the only note
contract. These tests lock the exited surface:

- create requests require `anchor` (no legacy analysis_record/render_scene
  fields exist anymore);
- list is filtered by `reading_record_id` and ordered by unit offsets;
- note text update stays intact;
- responses never expose legacy analysis identity.

Anchor-branch persistence semantics are locked by
`test_d6_a5_dual_contract_spike.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.schemas.reader_notes import ReaderNoteCreateRequest, ReaderNoteUpdateRequest

client = TestClient(app)

USER_ID = "00000000-0000-0000-0000-000000000011"
RECORD_ID = str(uuid4())
AUTH_HEADERS = {"Authorization": "Bearer test_token"}


def _mock_auth(user_id: str = USER_ID):
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


def _mock_db_pool():
    mock_conn = AsyncMock()
    # conn.transaction() must return an async context manager, not a coroutine.
    mock_conn.transaction = MagicMock(return_value=AsyncMock())
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_pool, mock_conn


def _anchor_payload() -> dict:
    return {
        "record_id": RECORD_ID,
        "base_id": str(uuid4()),
        "generation": 1,
        "unit_id": "unit-1",
        "anchor_segment_id": "seg-1",
        "start_offset": 0,
        "end_offset": 4,
        "selected_text": "text",
        "text_hash": "hash",
        "hash_algorithm": "fnv1a32-utf16",
    }


def _make_row(**overrides):
    now = datetime.now(UTC)
    defaults = {
        "id": uuid4(),
        "quote_mode": "text_range",
        "target_key": f"reading-record:{RECORD_ID}:unit:unit-1:segment:seg-1",
        "paragraph_id": None,
        "sentence_id": None,
        "selected_text": "text",
        "start_offset": None,
        "end_offset": None,
        "text_hash": None,
        "note_text": "Need revisit.",
        "payload_json": {},
        "created_at": now,
        "updated_at": now,
        "reading_record_id": UUID(RECORD_ID),
        "base_id": uuid4(),
        "generation": 1,
        "unit_id": "unit-1",
        "anchor_segment_id": "seg-1",
        "unit_start_utf16": 0,
        "unit_end_utf16": 4,
    }
    defaults.update(overrides)
    return defaults


class TestSchemaValidation:
    def test_create_request_requires_anchor(self):
        with pytest.raises(ValidationError):
            ReaderNoteCreateRequest(  # type: ignore[call-arg]
                selected_text="text",
                note_text="note",
            )

    def test_create_request_rejects_selected_text_mismatch(self):
        with pytest.raises(ValidationError):
            ReaderNoteCreateRequest(
                anchor=_anchor_payload(),  # type: ignore[arg-type]
                selected_text="other",
                note_text="note",
            )

    def test_update_note_accepts_text_only(self):
        assert ReaderNoteUpdateRequest(note_text="new").note_text == "new"


class TestRoutes:
    @_mock_auth()
    @patch("app.services.reader_notes.db_connect.acquire_connection")
    def test_list_reader_notes_filters_by_reading_record_id(
        self, mock_acquire, _mock_session
    ):
        mock_pool, mock_conn = _mock_db_pool()
        mock_conn.fetch = AsyncMock(return_value=[_make_row()])
        mock_acquire.return_value = mock_pool.acquire.return_value

        response = client.get(
            f"/reader-notes?reading_record_id={RECORD_ID}",
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        sql = mock_conn.fetch.await_args.args[0]
        assert "reading_record_id = $2" in sql
        assert "unit_start_utf16 ASC" in sql
        item = response.json()["items"][0]
        assert "analysis_record_id" not in item
        assert item["reading_record_id"] == RECORD_ID

    @_mock_auth()
    @patch("app.services.reader_notes.db_connect.acquire_connection")
    def test_update_reader_note(self, mock_acquire, _mock_session):
        row = _make_row()
        updated_row = _make_row(id=row["id"], note_text="Updated note.")
        mock_pool, mock_conn = _mock_db_pool()
        mock_conn.fetchrow = AsyncMock(side_effect=[row, updated_row])
        mock_acquire.return_value = mock_pool.acquire.return_value
        with patch("app.services.reader_notes.ReaderEventRuntime") as mock_runtime:
            mock_runtime.return_value.is_active_fence = AsyncMock(return_value=False)

            response = client.patch(
                f"/reader-notes/{row['id']}",
                json={"note_text": "Updated note."},
                headers=AUTH_HEADERS,
            )

        assert response.status_code == 200
        assert response.json()["note_text"] == "Updated note."
