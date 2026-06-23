from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.contracts.annotation import compute_text_range_hash
from app.main import app
from app.schemas.reader_notes import ReaderNoteCreateRequest, ReaderNoteUpdateRequest
from app.schemas.user_annotations import UserAnnotationSegment
from app.services.reader_notes import (
    _build_target_key,
    _row_to_response,
    _validate_quote_against_record,
    create_reader_note,
    list_reader_notes,
)

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
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_pool, mock_conn


def _make_row(**overrides):
    now = datetime.now(UTC)
    defaults = {
        "id": uuid4(),
        "analysis_record_id": UUID(RECORD_ID),
        "anchor_sentence_id": "s1",
        "quote_mode": "sentence",
        "target_key": f"record:{RECORD_ID}:sentence:s1",
        "paragraph_id": "p1",
        "sentence_id": "s1",
        "selected_text": "Full sentence.",
        "start_offset": None,
        "end_offset": None,
        "text_hash": None,
        "note_text": "Need revisit.",
        "payload_json": {},
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(overrides)
    return defaults


class TestSchemaValidation:
    def test_sentence_note_requires_anchor_sentence(self):
        req = ReaderNoteCreateRequest(
            analysis_record_id=RECORD_ID,
            quote_mode="sentence",
            anchor_sentence_id="s1",
            sentence_id="s1",
            selected_text="Full sentence.",
            note_text="Need revisit.",
        )
        assert req.quote_mode == "sentence"

    def test_text_range_note_requires_offsets(self):
        text = "policy choices"
        req = ReaderNoteCreateRequest(
            analysis_record_id=RECORD_ID,
            quote_mode="text_range",
            anchor_sentence_id="s1",
            sentence_id="s1",
            selected_text=text,
            start_offset=0,
            end_offset=14,
            text_hash=compute_text_range_hash(text),
            note_text="Explain this phrase.",
        )
        assert req.text_hash == compute_text_range_hash(text)

    def test_multi_text_note_requires_segments(self):
        with pytest.raises(ValidationError):
            ReaderNoteCreateRequest(
                analysis_record_id=RECORD_ID,
                quote_mode="multi_text",
                anchor_sentence_id="s1",
                selected_text="alpha ... beta",
                note_text="Need revisit.",
                segments=[],
            )

    def test_update_note_accepts_text_only(self):
        req = ReaderNoteUpdateRequest(note_text="Updated note")
        assert req.note_text == "Updated note"


class TestHelpers:
    def test_build_target_key_sentence(self):
        req = ReaderNoteCreateRequest(
            analysis_record_id=RECORD_ID,
            quote_mode="sentence",
            anchor_sentence_id="s1",
            sentence_id="s1",
            selected_text="Full sentence.",
            note_text="Need revisit.",
        )
        assert _build_target_key(req) == f"record:{RECORD_ID}:sentence:s1"

    def test_build_target_key_text_range(self):
        text = "policy choices"
        req = ReaderNoteCreateRequest(
            analysis_record_id=RECORD_ID,
            quote_mode="text_range",
            anchor_sentence_id="s1",
            sentence_id="s1",
            selected_text=text,
            start_offset=0,
            end_offset=14,
            text_hash=compute_text_range_hash(text),
            note_text="Need revisit.",
        )
        assert _build_target_key(req) == (
            f"record:{RECORD_ID}:range:s1:0:14:{compute_text_range_hash(text)}"
        )

    def test_build_target_key_multi_text_uses_segment_signature(self):
        req = ReaderNoteCreateRequest(
            analysis_record_id=RECORD_ID,
            quote_mode="multi_text",
            anchor_sentence_id="s1",
            selected_text="alpha ... beta",
            note_text="Need revisit.",
            segments=[
                UserAnnotationSegment(
                    paragraph_id="p1",
                    sentence_id="s1",
                    selected_text="alpha",
                    start_offset=0,
                    end_offset=5,
                    text_hash=compute_text_range_hash("alpha"),
                ),
                UserAnnotationSegment(
                    paragraph_id="p2",
                    sentence_id="s2",
                    selected_text="beta",
                    start_offset=0,
                    end_offset=4,
                    text_hash=compute_text_range_hash("beta"),
                ),
            ],
        )

        expected_signature = (
            f"0:p1:s1:0:5:{compute_text_range_hash('alpha')}"
            f"|1:p2:s2:0:4:{compute_text_range_hash('beta')}"
        )
        assert _build_target_key(req) == (
            f"record:{RECORD_ID}:multi_text:2:{compute_text_range_hash(expected_signature)}"
        )

    def test_row_to_response_parses_multi_text_segments(self):
        row = _make_row(
            quote_mode="multi_text",
            target_key=f"record:{RECORD_ID}:multi_text:abc",
            payload_json={
                "segments": [
                    {
                        "paragraph_id": "p1",
                        "sentence_id": "s1",
                        "selected_text": "alpha",
                        "start_offset": 0,
                        "end_offset": 5,
                        "text_hash": compute_text_range_hash("alpha"),
                    },
                    {
                        "paragraph_id": "p2",
                        "sentence_id": "s2",
                        "selected_text": "beta",
                        "start_offset": 0,
                        "end_offset": 4,
                        "text_hash": compute_text_range_hash("beta"),
                    },
                ]
            },
        )
        response = _row_to_response(row)
        assert len(response.segments) == 2
        assert response.segments[1] == UserAnnotationSegment(
            paragraph_id="p2",
            sentence_id="s2",
            selected_text="beta",
            start_offset=0,
            end_offset=4,
            text_hash=compute_text_range_hash("beta"),
        )


@pytest.mark.asyncio
class TestServiceBehavior:
    @patch("app.services.reader_notes.load_render_scene", new_callable=AsyncMock)
    async def test_validate_quote_against_record_rejects_sentence_quote_mismatch(
        self,
        mock_load_render_scene,
    ):
        mock_load_render_scene.return_value = {
            "article": {
                "sentences": [
                    {
                        "sentence_id": "s1",
                        "paragraph_id": "p1",
                        "text": "Full sentence.",
                    }
                ]
            }
        }
        req = ReaderNoteCreateRequest(
            analysis_record_id=RECORD_ID,
            quote_mode="sentence",
            anchor_sentence_id="s1",
            sentence_id="s1",
            selected_text="Different sentence.",
            note_text="Need revisit.",
        )

        with pytest.raises(HTTPException) as exc_info:
            await _validate_quote_against_record(AsyncMock(), UUID(USER_ID), UUID(RECORD_ID), req)

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "selected_text does not match full sentence text"

    @patch("app.services.reader_notes.load_render_scene", new_callable=AsyncMock)
    async def test_validate_quote_against_record_rejects_text_range_hash_mismatch(
        self,
        mock_load_render_scene,
    ):
        mock_load_render_scene.return_value = {
            "article": {
                "sentences": [
                    {
                        "sentence_id": "s1",
                        "paragraph_id": "p1",
                        "text": "Institutional memory shapes policy choices.",
                    }
                ]
            }
        }
        req = ReaderNoteCreateRequest(
            analysis_record_id=RECORD_ID,
            quote_mode="text_range",
            anchor_sentence_id="s1",
            paragraph_id="p1",
            sentence_id="s1",
            selected_text="memory",
            start_offset=14,
            end_offset=20,
            text_hash=compute_text_range_hash("policy"),
            note_text="Explain this phrase.",
        )

        with pytest.raises(HTTPException) as exc_info:
            await _validate_quote_against_record(AsyncMock(), UUID(USER_ID), UUID(RECORD_ID), req)

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "text_hash does not match selected_text"

    @patch("app.services.reader_notes._validate_quote_against_record", new_callable=AsyncMock)
    @patch("app.services.reader_notes.db_connect.DB_POOL")
    async def test_create_reader_note_reopens_exact_target_key(self, mock_pool, _mock_validate):
        pool, conn = _mock_db_pool()
        mock_pool.acquire = pool.acquire
        reopened_row = _make_row(note_text="Updated existing note")
        conn.fetchrow.return_value = reopened_row
        req = ReaderNoteCreateRequest(
            analysis_record_id=RECORD_ID,
            quote_mode="sentence",
            anchor_sentence_id="s1",
            sentence_id="s1",
            selected_text="Full sentence.",
            note_text="Updated existing note",
        )

        response = await create_reader_note(UUID(USER_ID), req)

        assert response.target_key == f"record:{RECORD_ID}:sentence:s1"
        query = conn.fetchrow.await_args.args[0]
        assert "ON CONFLICT (user_id, analysis_record_id, target_key) DO UPDATE" in query
        assert "deleted_at = NULL" in query
        assert "note_text = EXCLUDED.note_text" in query

    @patch("app.services.reader_notes._validate_quote_against_record", new_callable=AsyncMock)
    @patch("app.services.reader_notes.db_connect.DB_POOL")
    async def test_create_reader_note_keeps_overlap_but_not_exact_as_new_target(
        self,
        mock_pool,
        _mock_validate,
    ):
        pool, conn = _mock_db_pool()
        mock_pool.acquire = pool.acquire
        new_row = _make_row(
            quote_mode="text_range",
            target_key=f"record:{RECORD_ID}:range:s1:2:8:{compute_text_range_hash('ll sen')}",
            selected_text="ll sen",
            start_offset=2,
            end_offset=8,
            text_hash=compute_text_range_hash("ll sen"),
        )
        conn.fetchrow.return_value = new_row
        req = ReaderNoteCreateRequest(
            analysis_record_id=RECORD_ID,
            quote_mode="text_range",
            anchor_sentence_id="s1",
            sentence_id="s1",
            selected_text="ll sen",
            start_offset=2,
            end_offset=8,
            text_hash=compute_text_range_hash("ll sen"),
            note_text="New overlapping note",
        )

        response = await create_reader_note(UUID(USER_ID), req)

        expected_target_key = (
            f"record:{RECORD_ID}:range:s1:2:8:{compute_text_range_hash('ll sen')}"
        )
        assert response.target_key == expected_target_key
        insert_args = conn.fetchrow.await_args.args
        assert insert_args[5] == expected_target_key

    @patch("app.services.reader_notes.db_connect.DB_POOL")
    async def test_list_reader_notes_orders_by_quote_position_then_created_at(self, mock_pool):
        pool, conn = _mock_db_pool()
        mock_pool.acquire = pool.acquire
        conn.fetch.return_value = [
            _make_row(note_text="Sentence note", start_offset=None, end_offset=None),
            _make_row(
                note_text="Range note",
                quote_mode="text_range",
                start_offset=4,
                end_offset=10,
            ),
        ]

        response = await list_reader_notes(UUID(USER_ID), RECORD_ID)

        assert [item.note_text for item in response] == ["Sentence note", "Range note"]
        query = conn.fetch.await_args.args[0]
        assert (
            "ORDER BY anchor_sentence_id ASC, start_offset ASC NULLS FIRST, "
            "end_offset ASC NULLS FIRST, created_at ASC"
        ) in query
        assert "ORDER BY paragraph_id" not in query


class TestRoutes:
    @_mock_auth()
    @patch("app.services.reader_notes._validate_quote_against_record", new_callable=AsyncMock)
    @patch("app.services.reader_notes.db_connect.DB_POOL")
    def test_create_reader_note(self, mock_pool, _mock_validate, _mock_session):
        pool, conn = _mock_db_pool()
        mock_pool.acquire = pool.acquire
        conn.fetchrow.return_value = _make_row()

        response = client.post(
            "/reader-notes",
            json={
                "analysis_record_id": RECORD_ID,
                "quote_mode": "sentence",
                "anchor_sentence_id": "s1",
                "sentence_id": "s1",
                "selected_text": "Full sentence.",
                "note_text": "Need revisit.",
            },
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["quote_mode"] == "sentence"
        assert data["anchor_sentence_id"] == "s1"
        assert data["note_text"] == "Need revisit."

    @_mock_auth()
    @patch("app.services.reader_notes.db_connect.DB_POOL")
    def test_list_reader_notes(self, mock_pool, _mock_session):
        pool, conn = _mock_db_pool()
        mock_pool.acquire = pool.acquire
        conn.fetch.return_value = [_make_row(note_text="First note")]

        response = client.get(
            f"/reader-notes?analysis_record_id={RECORD_ID}",
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 1
        assert items[0]["note_text"] == "First note"

    @_mock_auth()
    @patch("app.services.reader_notes.db_connect.DB_POOL")
    def test_update_reader_note(self, mock_pool, _mock_session):
        pool, conn = _mock_db_pool()
        mock_pool.acquire = pool.acquire
        note_id = uuid4()
        conn.fetchrow.return_value = _make_row(id=note_id, note_text="Updated note")

        response = client.patch(
            f"/reader-notes/{note_id}",
            json={"note_text": "Updated note"},
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        assert response.json()["note_text"] == "Updated note"

    @_mock_auth()
    @patch("app.services.reader_notes.db_connect.DB_POOL")
    def test_delete_reader_note(self, mock_pool, _mock_session):
        pool, conn = _mock_db_pool()
        mock_pool.acquire = pool.acquire
        conn.execute.return_value = "UPDATE 1"
        note_id = uuid4()

        response = client.delete(
            f"/reader-notes/{note_id}",
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        assert response.json() == {"ok": True}
