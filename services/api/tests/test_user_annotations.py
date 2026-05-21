from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.contracts.annotation import compute_text_range_hash
from app.main import app
from app.schemas.user_annotations import (
    UserAnnotationCreateRequest,
    UserAnnotationSegment,
    UserAnnotationUpdateRequest,
)
from app.services.user_annotations import _build_target_key, _resolve_single_sentence_conflict, _row_to_response

client = TestClient(app)

USER_ID = "00000000-0000-0000-0000-000000000001"
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
        "anchor_type": "sentence",
        "target_key": f"record:{RECORD_ID}:sentence:s1",
        "paragraph_id": "p1",
        "sentence_id": "s1",
        "selected_text": "Test text",
        "start_offset": None,
        "end_offset": None,
        "text_hash": None,
        "color": "soft_green",
        "payload_json": {},
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(overrides)
    return defaults


class TestSchemaValidation:
    def test_create_request_defaults(self):
        req = UserAnnotationCreateRequest(selected_text="Sentence", sentence_id="s1")
        assert req.anchor_type == "sentence"
        assert req.color == "soft_green"

    def test_create_request_validates_text_range_hash(self):
        text = "policy choices"
        req = UserAnnotationCreateRequest(
            analysis_record_id=RECORD_ID,
            anchor_type="text_range",
            sentence_id="s1",
            selected_text=text,
            start_offset=0,
            end_offset=14,
            text_hash=compute_text_range_hash(text),
        )
        assert req.text_hash == compute_text_range_hash(text)

    def test_create_request_rejects_incomplete_multi_text(self):
        with pytest.raises(ValidationError):
            UserAnnotationCreateRequest(
                analysis_record_id=RECORD_ID,
                anchor_type="multi_text",
                selected_text="one ... two",
                segments=[],
            )

    def test_update_request_only_accepts_color(self):
        req = UserAnnotationUpdateRequest(color="soft_blue")
        assert req.color == "soft_blue"


class TestHelpers:
    def test_build_target_key_sentence(self):
        req = UserAnnotationCreateRequest(
            analysis_record_id=RECORD_ID,
            anchor_type="sentence",
            sentence_id="s1",
            selected_text="Sentence",
        )
        assert _build_target_key(req) == f"record:{RECORD_ID}:sentence:s1"

    def test_build_target_key_text_range(self):
        text = "policy choices"
        req = UserAnnotationCreateRequest(
            analysis_record_id=RECORD_ID,
            anchor_type="text_range",
            sentence_id="s1",
            selected_text=text,
            start_offset=0,
            end_offset=14,
            text_hash=compute_text_range_hash(text),
        )
        assert _build_target_key(req) == (
            f"record:{RECORD_ID}:range:s1:0:14:{compute_text_range_hash(text)}"
        )

    def test_row_to_response_parses_multi_text_segments(self):
        row = _make_row(
            anchor_type="multi_text",
            target_key=f"record:{RECORD_ID}:multi_text:abc",
            sentence_id="s1",
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
        assert response.segments[0] == UserAnnotationSegment(
            paragraph_id="p1",
            sentence_id="s1",
            selected_text="alpha",
            start_offset=0,
            end_offset=5,
            text_hash=compute_text_range_hash("alpha"),
        )

    @pytest.mark.anyio
    async def test_resolve_single_sentence_subset_updates_existing_highlight(self):
        existing = _make_row(
            anchor_type="sentence",
            target_key=f"record:{RECORD_ID}:sentence:s1",
            sentence_id="s1",
            selected_text="Institutional memory shapes policy choices.",
            start_offset=None,
            end_offset=None,
            text_hash=None,
            color="soft_green",
        )
        updated = _make_row(
            id=existing["id"],
            anchor_type="sentence",
            target_key=existing["target_key"],
            sentence_id="s1",
            selected_text=existing["selected_text"],
            color="soft_blue",
        )
        conn = AsyncMock()
        conn.fetch.return_value = [existing]
        conn.fetchrow.return_value = updated
        req = UserAnnotationCreateRequest(
            analysis_record_id=RECORD_ID,
            anchor_type="text_range",
            sentence_id="s1",
            selected_text="memory",
            start_offset=14,
            end_offset=20,
            text_hash=compute_text_range_hash("memory"),
            color="soft_blue",
        )

        response = await _resolve_single_sentence_conflict(
            conn,
            user_id=UUID(USER_ID),
            record_id=UUID(RECORD_ID),
            req=req,
            target_key=f"record:{RECORD_ID}:range:s1:14:20:{compute_text_range_hash('memory')}",
        )

        assert response is not None
        assert response.target_key == existing["target_key"]
        assert response.color == "soft_blue"

    @pytest.mark.anyio
    async def test_resolve_single_sentence_superset_extends_existing_highlight(self):
        existing = _make_row(
            anchor_type="text_range",
            target_key=f"record:{RECORD_ID}:range:s1:14:20:{compute_text_range_hash('memory')}",
            sentence_id="s1",
            selected_text="memory",
            start_offset=14,
            end_offset=20,
            text_hash=compute_text_range_hash("memory"),
            color="soft_green",
        )
        updated = _make_row(
            id=existing["id"],
            anchor_type="sentence",
            target_key=f"record:{RECORD_ID}:sentence:s1",
            sentence_id="s1",
            selected_text="Institutional memory shapes policy choices.",
            start_offset=None,
            end_offset=None,
            text_hash=None,
            color="warm_yellow",
        )
        conn = AsyncMock()
        conn.fetch.return_value = [existing]
        conn.fetchrow.return_value = updated
        req = UserAnnotationCreateRequest(
            analysis_record_id=RECORD_ID,
            anchor_type="sentence",
            sentence_id="s1",
            selected_text="Institutional memory shapes policy choices.",
            color="warm_yellow",
        )

        response = await _resolve_single_sentence_conflict(
            conn,
            user_id=UUID(USER_ID),
            record_id=UUID(RECORD_ID),
            req=req,
            target_key=f"record:{RECORD_ID}:sentence:s1",
        )

        assert response is not None
        assert response.anchor_type == "sentence"
        assert response.target_key == f"record:{RECORD_ID}:sentence:s1"


class TestRoutes:
    @_mock_auth()
    @patch("app.services.user_annotations.db_connect.DB_POOL")
    def test_create_highlight(self, mock_pool, _mock_session):
        pool, conn = _mock_db_pool()
        mock_pool.acquire = pool.acquire
        conn.fetch.return_value = []
        conn.fetchrow.return_value = _make_row()

        response = client.post(
            "/user-annotations",
            json={
                "analysis_record_id": RECORD_ID,
                "anchor_type": "sentence",
                "sentence_id": "s1",
                "selected_text": "Test text",
                "color": "soft_green",
            },
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["anchor_type"] == "sentence"
        assert data["target_key"] == f"record:{RECORD_ID}:sentence:s1"
        assert "annotation_type" not in data
        assert "note" not in data

    @_mock_auth()
    @patch("app.services.user_annotations.db_connect.DB_POOL")
    def test_list_annotations(self, mock_pool, _mock_session):
        pool, conn = _mock_db_pool()
        mock_pool.acquire = pool.acquire
        conn.fetch.return_value = [_make_row(color="soft_blue")]

        response = client.get(
            f"/user-annotations?analysis_record_id={RECORD_ID}",
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        data = response.json()["items"]
        assert len(data) == 1
        assert data[0]["color"] == "soft_blue"

    @_mock_auth()
    @patch("app.services.user_annotations.db_connect.DB_POOL")
    def test_update_color(self, mock_pool, _mock_session):
        pool, conn = _mock_db_pool()
        mock_pool.acquire = pool.acquire
        annotation_id = uuid4()
        conn.fetchrow.return_value = _make_row(id=annotation_id, color="warm_yellow")

        response = client.patch(
            f"/user-annotations/{annotation_id}",
            json={"color": "warm_yellow"},
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        assert response.json()["color"] == "warm_yellow"

    @_mock_auth()
    @patch("app.services.user_annotations.db_connect.DB_POOL")
    def test_delete_annotation(self, mock_pool, _mock_session):
        pool, conn = _mock_db_pool()
        mock_pool.acquire = pool.acquire
        conn.execute.return_value = "UPDATE 1"
        annotation_id = uuid4()

        response = client.delete(
            f"/user-annotations/{annotation_id}",
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        assert response.json() == {"ok": True}
