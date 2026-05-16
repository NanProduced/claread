from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.schemas.user_annotations import (
    UserAnnotationCreateRequest,
    UserAnnotationUpdateRequest,
    UserAnnotationResponse,
    UserAnnotationListResponse,
)
from app.contracts.annotation import build_multi_text_target_key, compute_text_range_hash
from app.services.user_annotations import _build_target_key, _row_to_response

client = TestClient(app)

USER_ID = "00000000-0000-0000-0000-000000000001"
OTHER_USER_ID = "00000000-0000-0000-0000-000000000002"
RECORD_ID = str(uuid4())
AUTH_HEADERS = {"Authorization": "Bearer test_token"}


def _mock_auth(user_id: str = USER_ID):
    return patch(
        "app.services.auth.dependencies.validate_session",
        new_callable=AsyncMock,
        return_value=type("SessionInfo", (), {
            "user_id": UUID(user_id),
            "session_id": uuid4(),
        })(),
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
        "annotation_type": "highlight",
        "anchor_type": "sentence",
        "target_key": f"record:{RECORD_ID}:sentence:s1",
        "paragraph_id": "p1",
        "sentence_id": "s1",
        "selected_text": "Test text",
        "start_offset": None,
        "end_offset": None,
        "text_hash": None,
        "color": "soft_green",
        "note": None,
        "payload_json": {},
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(overrides)
    return defaults


class TestSchemaValidation:
    def test_create_request_defaults(self):
        req = UserAnnotationCreateRequest(selected_text="hello", sentence_id="s1")
        assert req.annotation_type == "highlight"
        assert req.anchor_type == "sentence"
        assert req.color == "soft_green"
        assert req.payload_json == {}

    def test_create_request_accepts_current_colors(self):
        assert UserAnnotationCreateRequest(selected_text="hello", sentence_id="s1", color="soft_green").color == "soft_green"
        assert UserAnnotationCreateRequest(selected_text="hello", sentence_id="s1", color="soft_purple").color == "soft_purple"

    def test_create_request_rejects_bad_annotation_type(self):
        with pytest.raises(ValidationError):
            UserAnnotationCreateRequest(selected_text="hello", annotation_type="invalid")

    def test_create_request_rejects_bad_anchor_type(self):
        with pytest.raises(ValidationError):
            UserAnnotationCreateRequest(selected_text="hello", anchor_type="invalid")

    def test_create_request_rejects_bad_color(self):
        with pytest.raises(ValidationError):
            UserAnnotationCreateRequest(selected_text="hello", color="red")

    def test_create_request_requires_sentence_id_for_sentence_anchor(self):
        with pytest.raises(ValidationError):
            UserAnnotationCreateRequest(selected_text="hello", anchor_type="sentence")

    def test_create_request_requires_paragraph_id_for_paragraph_anchor(self):
        with pytest.raises(ValidationError):
            UserAnnotationCreateRequest(selected_text="hello", anchor_type="paragraph")

    def test_create_request_accepts_valid_text_range_anchor(self):
        req = UserAnnotationCreateRequest(
            selected_text="hello",
            anchor_type="text_range",
            analysis_record_id=RECORD_ID,
            sentence_id="s1",
            start_offset=0,
            end_offset=5,
            text_hash=compute_text_range_hash("hello"),
        )
        assert req.anchor_type == "text_range"
        assert req.start_offset == 0
        assert req.end_offset == 5

    def test_create_request_accepts_valid_multi_text_anchor(self):
        first_text = "hello"
        second_text = "world"
        req = UserAnnotationCreateRequest(
            selected_text="hello world",
            anchor_type="multi_text",
            analysis_record_id=RECORD_ID,
            segments=[
                {
                    "paragraph_id": "p1",
                    "sentence_id": "s1",
                    "selected_text": first_text,
                    "start_offset": 0,
                    "end_offset": 5,
                    "text_hash": compute_text_range_hash(first_text),
                },
                {
                    "paragraph_id": "p1",
                    "sentence_id": "s2",
                    "selected_text": second_text,
                    "start_offset": 1,
                    "end_offset": 6,
                    "text_hash": compute_text_range_hash(second_text),
                },
            ],
        )
        assert req.anchor_type == "multi_text"
        assert len(req.segments) == 2

    def test_create_request_rejects_incomplete_text_range_anchor(self):
        with pytest.raises(ValidationError):
            UserAnnotationCreateRequest(
                selected_text="hello",
                anchor_type="text_range",
                analysis_record_id=RECORD_ID,
                sentence_id="s1",
                start_offset=7,
                end_offset=2,
                text_hash=compute_text_range_hash("hello"),
            )

    def test_create_request_rejects_text_range_hash_mismatch(self):
        with pytest.raises(ValidationError):
            UserAnnotationCreateRequest(
                selected_text="hello",
                anchor_type="text_range",
                analysis_record_id=RECORD_ID,
                sentence_id="s1",
                start_offset=0,
                end_offset=5,
                text_hash="deadbeef",
            )

    def test_update_request_allows_none(self):
        req = UserAnnotationUpdateRequest()
        assert req.color is None
        assert req.note is None

    def test_update_request_rejects_bad_color(self):
        with pytest.raises(ValidationError):
            UserAnnotationUpdateRequest(color="invalid")

    def test_response_sentence_id_optional(self):
        row = _make_row(sentence_id=None, paragraph_id="p1", anchor_type="paragraph")
        resp = _row_to_response(row)
        assert resp.sentence_id is None

    def test_response_parses_payload_json_string(self):
        row = _make_row(payload_json='{"key": "val"}')
        resp = _row_to_response(row)
        assert resp.payload_json == {"key": "val"}

    def test_response_handles_null_payload_json(self):
        row = _make_row(payload_json=None)
        resp = _row_to_response(row)
        assert resp.payload_json == {}

    def test_response_parses_multi_text_segments(self):
        row = _make_row(
            anchor_type="multi_text",
            sentence_id="s1",
            start_offset=None,
            end_offset=None,
            text_hash=None,
            payload_json={
                "segments": [
                    {
                        "paragraph_id": "p1",
                        "sentence_id": "s1",
                        "selected_text": "hello",
                        "start_offset": 0,
                        "end_offset": 5,
                        "text_hash": compute_text_range_hash("hello"),
                    },
                    {
                        "paragraph_id": "p1",
                        "sentence_id": "s2",
                        "selected_text": "world",
                        "start_offset": 1,
                        "end_offset": 6,
                        "text_hash": compute_text_range_hash("world"),
                    },
                ]
            },
        )
        resp = _row_to_response(row)
        assert len(resp.segments) == 2
        assert resp.segments[1].sentence_id == "s2"


class TestBuildTargetKey:
    def test_explicit_target_key(self):
        req = UserAnnotationCreateRequest(
            selected_text="hello",
            target_key="custom:key",
            sentence_id="s1",
        )
        assert _build_target_key(req) == "custom:key"

    def test_sentence_anchor(self):
        req = UserAnnotationCreateRequest(
            selected_text="hello",
            anchor_type="sentence",
            analysis_record_id=RECORD_ID,
            sentence_id="s2",
        )
        assert _build_target_key(req) == f"record:{RECORD_ID}:sentence:s2"

    def test_paragraph_anchor(self):
        req = UserAnnotationCreateRequest(
            selected_text="hello",
            anchor_type="paragraph",
            analysis_record_id=RECORD_ID,
            paragraph_id="p1",
        )
        assert _build_target_key(req) == f"record:{RECORD_ID}:paragraph:p1"

    def test_text_range_anchor(self):
        req = UserAnnotationCreateRequest(
            selected_text="hello",
            anchor_type="text_range",
            analysis_record_id=RECORD_ID,
            sentence_id="s3",
            start_offset=0,
            end_offset=5,
            text_hash=compute_text_range_hash("hello"),
        )
        assert _build_target_key(req) == f"record:{RECORD_ID}:range:s3:0:5:{compute_text_range_hash('hello')}"

    def test_multi_text_anchor(self):
        req = UserAnnotationCreateRequest(
            selected_text="hello world",
            anchor_type="multi_text",
            analysis_record_id=RECORD_ID,
            segments=[
                {
                    "paragraph_id": "p1",
                    "sentence_id": "s1",
                    "selected_text": "hello",
                    "start_offset": 0,
                    "end_offset": 5,
                    "text_hash": compute_text_range_hash("hello"),
                },
                {
                    "paragraph_id": "p2",
                    "sentence_id": "s2",
                    "selected_text": "world",
                    "start_offset": 0,
                    "end_offset": 5,
                    "text_hash": compute_text_range_hash("world"),
                },
            ],
        )
        assert _build_target_key(req) == build_multi_text_target_key(
            RECORD_ID,
            [segment.model_dump(mode="python") for segment in req.segments],
        )

    def test_no_record_id_uses_local(self):
        req = UserAnnotationCreateRequest(
            selected_text="hello",
            anchor_type="sentence",
            sentence_id="s1",
        )
        assert _build_target_key(req) == "record:local:sentence:s1"


class TestCreateAnnotation:
    @_mock_auth()
    @patch("app.services.user_annotations.db_connect.DB_POOL")
    def test_create_highlight(self, mock_pool, mock_auth):
        pool, conn = _mock_db_pool()
        mock_pool.acquire = pool.acquire
        row = _make_row()
        conn.fetchrow.return_value = row

        resp = client.post(
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
        assert resp.status_code == 200
        data = resp.json()
        assert data["annotation_type"] == "highlight"
        assert data["target_key"] == f"record:{RECORD_ID}:sentence:s1"
        args = conn.fetchrow.call_args.args
        assert args[14] == {}

    @_mock_auth()
    @patch("app.services.user_annotations.db_connect.DB_POOL")
    def test_create_with_note_upgrades_to_note_type(self, mock_pool, mock_auth):
        pool, conn = _mock_db_pool()
        mock_pool.acquire = pool.acquire
        row = _make_row(annotation_type="note", note="my note")
        conn.fetchrow.return_value = row

        resp = client.post(
            "/user-annotations",
            json={
                "analysis_record_id": RECORD_ID,
                "annotation_type": "highlight",
                "anchor_type": "sentence",
                "sentence_id": "s1",
                "selected_text": "Test text",
                "note": "my note",
            },
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["annotation_type"] == "note"

    @_mock_auth()
    @patch("app.services.user_annotations.db_connect.DB_POOL")
    def test_create_text_range_validates_render_scene(self, mock_pool, mock_auth):
        pool, conn = _mock_db_pool()
        mock_pool.acquire = pool.acquire
        selected_text = "range text"
        text_hash = compute_text_range_hash(selected_text)
        row = _make_row(
            annotation_type="highlight",
            anchor_type="text_range",
            selected_text=selected_text,
            start_offset=2,
            end_offset=12,
            text_hash=text_hash,
            target_key=f"record:{RECORD_ID}:range:s1:2:12:{text_hash}",
        )
        conn.fetchrow.side_effect = [
            {
                "render_scene_json": {
                    "article": {
                        "sentences": [
                            {
                                "sentence_id": "s1",
                                "paragraph_id": "p1",
                                "text": "a range text here",
                            }
                        ]
                    }
                }
            },
            row,
        ]

        resp = client.post(
            "/user-annotations",
            json={
                "analysis_record_id": RECORD_ID,
                "anchor_type": "text_range",
                "sentence_id": "s1",
                "selected_text": selected_text,
                "start_offset": 2,
                "end_offset": 12,
                "text_hash": text_hash,
                "color": "warm_yellow",
            },
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["anchor_type"] == "text_range"

    @_mock_auth()
    @patch("app.services.user_annotations.db_connect.DB_POOL")
    def test_create_text_range_rejects_render_scene_mismatch(self, mock_pool, mock_auth):
        pool, conn = _mock_db_pool()
        mock_pool.acquire = pool.acquire
        selected_text = "range text"
        conn.fetchrow.return_value = {
            "render_scene_json": {
                "article": {
                    "sentences": [
                        {
                            "sentence_id": "s1",
                            "paragraph_id": "p1",
                            "text": "a different text here",
                        }
                    ]
                }
            }
        }

        resp = client.post(
            "/user-annotations",
            json={
                "analysis_record_id": RECORD_ID,
                "anchor_type": "text_range",
                "sentence_id": "s1",
                "selected_text": selected_text,
                "start_offset": 2,
                "end_offset": 12,
                "text_hash": compute_text_range_hash(selected_text),
            },
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 400

    @_mock_auth()
    @patch("app.services.user_annotations.db_connect.DB_POOL")
    def test_create_multi_text_validates_render_scene(self, mock_pool, mock_auth):
        pool, conn = _mock_db_pool()
        mock_pool.acquire = pool.acquire
        first_text = "range"
        second_text = "text"
        row = _make_row(
            anchor_type="multi_text",
            sentence_id="s1",
            selected_text="range text",
            start_offset=None,
            end_offset=None,
            text_hash=None,
            target_key=build_multi_text_target_key(
                RECORD_ID,
                [
                    {
                        "paragraph_id": "p1",
                        "sentence_id": "s1",
                        "start_offset": 2,
                        "end_offset": 7,
                        "text_hash": compute_text_range_hash(first_text),
                    },
                    {
                        "paragraph_id": "p2",
                        "sentence_id": "s2",
                        "start_offset": 0,
                        "end_offset": 4,
                        "text_hash": compute_text_range_hash(second_text),
                    },
                ],
            ),
            payload_json={
                "segments": [
                    {
                        "paragraph_id": "p1",
                        "sentence_id": "s1",
                        "selected_text": first_text,
                        "start_offset": 2,
                        "end_offset": 7,
                        "text_hash": compute_text_range_hash(first_text),
                    },
                    {
                        "paragraph_id": "p2",
                        "sentence_id": "s2",
                        "selected_text": second_text,
                        "start_offset": 0,
                        "end_offset": 4,
                        "text_hash": compute_text_range_hash(second_text),
                    },
                ]
            },
        )
        conn.fetchrow.side_effect = [
            {
                "render_scene_json": {
                    "article": {
                        "sentences": [
                            {
                                "sentence_id": "s1",
                                "paragraph_id": "p1",
                                "text": "a range here",
                            },
                            {
                                "sentence_id": "s2",
                                "paragraph_id": "p2",
                                "text": "text only",
                            },
                        ]
                    }
                }
            },
            row,
        ]

        resp = client.post(
            "/user-annotations",
            json={
                "analysis_record_id": RECORD_ID,
                "anchor_type": "multi_text",
                "sentence_id": "s1",
                "selected_text": "range text",
                "segments": [
                    {
                        "paragraph_id": "p1",
                        "sentence_id": "s1",
                        "selected_text": first_text,
                        "start_offset": 2,
                        "end_offset": 7,
                        "text_hash": compute_text_range_hash(first_text),
                    },
                    {
                        "paragraph_id": "p2",
                        "sentence_id": "s2",
                        "selected_text": second_text,
                        "start_offset": 0,
                        "end_offset": 4,
                        "text_hash": compute_text_range_hash(second_text),
                    },
                ],
                "color": "soft_blue",
            },
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["anchor_type"] == "multi_text"
        assert len(resp.json()["segments"]) == 2

    def test_create_requires_auth(self):
        resp = client.post(
            "/user-annotations",
            json={"selected_text": "hello"},
        )
        assert resp.status_code == 401 or resp.status_code == 403


class TestListAnnotations:
    @_mock_auth()
    @patch("app.services.user_annotations.db_connect.DB_POOL")
    def test_list_all(self, mock_pool, mock_auth):
        pool, conn = _mock_db_pool()
        mock_pool.acquire = pool.acquire
        conn.fetch.return_value = [_make_row()]

        resp = client.get("/user-annotations", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert len(data["items"]) == 1

    @_mock_auth()
    @patch("app.services.user_annotations.db_connect.DB_POOL")
    def test_list_by_analysis_record_id(self, mock_pool, mock_auth):
        pool, conn = _mock_db_pool()
        mock_pool.acquire = pool.acquire
        conn.fetch.return_value = [_make_row()]

        resp = client.get(
            f"/user-annotations?analysis_record_id={RECORD_ID}",
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 1
        conn.fetch.assert_called_once()
        call_args = conn.fetch.call_args
        assert UUID(RECORD_ID) in call_args.args

    @_mock_auth()
    @patch("app.services.user_annotations.db_connect.DB_POOL")
    def test_list_empty(self, mock_pool, mock_auth):
        pool, conn = _mock_db_pool()
        mock_pool.acquire = pool.acquire
        conn.fetch.return_value = []

        resp = client.get("/user-annotations", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    def test_list_requires_auth(self):
        resp = client.get("/user-annotations")
        assert resp.status_code == 401 or resp.status_code == 403


class TestUpdateAnnotation:
    @_mock_auth()
    @patch("app.services.user_annotations.db_connect.DB_POOL")
    def test_update_color(self, mock_pool, mock_auth):
        pool, conn = _mock_db_pool()
        mock_pool.acquire = pool.acquire
        ann_id = uuid4()
        row = _make_row(id=ann_id, color="soft_blue")
        conn.fetchrow.return_value = row

        resp = client.patch(
            f"/user-annotations/{ann_id}",
            json={"color": "soft_blue"},
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["color"] == "soft_blue"

    @_mock_auth()
    @patch("app.services.user_annotations.db_connect.DB_POOL")
    def test_update_note_keeps_existing_annotation_type(self, mock_pool, mock_auth):
        pool, conn = _mock_db_pool()
        mock_pool.acquire = pool.acquire
        ann_id = uuid4()
        row = _make_row(id=ann_id, annotation_type="highlight", note="saved note")
        conn.fetchrow.return_value = row

        resp = client.patch(
            f"/user-annotations/{ann_id}",
            json={"note": "saved note"},
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["annotation_type"] == "highlight"
        sql = conn.fetchrow.call_args.args[0]
        assert "annotation_type = CASE" not in sql

    @_mock_auth()
    @patch("app.services.user_annotations.db_connect.DB_POOL")
    def test_clear_note_keeps_highlight_type(self, mock_pool, mock_auth):
        pool, conn = _mock_db_pool()
        mock_pool.acquire = pool.acquire
        ann_id = uuid4()
        row = _make_row(id=ann_id, annotation_type="highlight", note=None)
        conn.fetchrow.return_value = row

        resp = client.patch(
            f"/user-annotations/{ann_id}",
            json={"note": None},
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["annotation_type"] == "highlight"

    @_mock_auth()
    @patch("app.services.user_annotations.db_connect.DB_POOL")
    def test_update_not_found(self, mock_pool, mock_auth):
        pool, conn = _mock_db_pool()
        mock_pool.acquire = pool.acquire
        conn.fetchrow.return_value = None

        ann_id = uuid4()
        resp = client.patch(
            f"/user-annotations/{ann_id}",
            json={"color": "soft_blue"},
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 404


class TestDeleteAnnotation:
    @_mock_auth()
    @patch("app.services.user_annotations.db_connect.DB_POOL")
    def test_soft_delete(self, mock_pool, mock_auth):
        pool, conn = _mock_db_pool()
        mock_pool.acquire = pool.acquire
        conn.execute.return_value = "UPDATE 1"

        ann_id = uuid4()
        resp = client.delete(
            f"/user-annotations/{ann_id}",
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    @_mock_auth()
    @patch("app.services.user_annotations.db_connect.DB_POOL")
    def test_delete_not_found(self, mock_pool, mock_auth):
        pool, conn = _mock_db_pool()
        mock_pool.acquire = pool.acquire
        conn.execute.return_value = "UPDATE 0"

        ann_id = uuid4()
        resp = client.delete(
            f"/user-annotations/{ann_id}",
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 404


class TestUserIsolation:
    @_mock_auth(user_id=USER_ID)
    @patch("app.services.user_annotations.db_connect.DB_POOL")
    def test_update_uses_user_id_in_where(self, mock_pool, mock_auth):
        pool, conn = _mock_db_pool()
        mock_pool.acquire = pool.acquire
        conn.fetchrow.return_value = None

        ann_id = uuid4()
        client.patch(
            f"/user-annotations/{ann_id}",
            json={"color": "soft_blue"},
            headers=AUTH_HEADERS,
        )
        call_args = conn.fetchrow.call_args
        assert UUID(USER_ID) in call_args.args

    @_mock_auth(user_id=USER_ID)
    @patch("app.services.user_annotations.db_connect.DB_POOL")
    def test_delete_uses_user_id_in_where(self, mock_pool, mock_auth):
        pool, conn = _mock_db_pool()
        mock_pool.acquire = pool.acquire
        conn.execute.return_value = "UPDATE 0"

        ann_id = uuid4()
        client.delete(
            f"/user-annotations/{ann_id}",
            headers=AUTH_HEADERS,
        )
        call_args = conn.execute.call_args
        assert UUID(USER_ID) in call_args.args


class TestUpsertBehavior:
    def test_upsert_clears_deleted_at_on_conflict(self):
        row = _make_row()
        resp = _row_to_response(row)
        assert resp.target_key == row["target_key"]

    @_mock_auth()
    @patch("app.services.user_annotations.db_connect.DB_POOL")
    def test_create_uses_on_conflict_upsert(self, mock_pool, mock_auth):
        pool, conn = _mock_db_pool()
        mock_pool.acquire = pool.acquire
        row = _make_row()
        conn.fetchrow.return_value = row

        resp = client.post(
            "/user-annotations",
            json={
                "analysis_record_id": RECORD_ID,
                "anchor_type": "sentence",
                "sentence_id": "s1",
                "selected_text": "Test text",
            },
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 200
        sql = conn.fetchrow.call_args.args[0]
        assert "ON CONFLICT" in sql
        assert "DO UPDATE" in sql
        assert "deleted_at = NULL" in sql
        assert "deleted_by = NULL" in sql


class TestRowToResponse:
    def test_converts_datetime_to_isoformat(self):
        now = datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC)
        row = _make_row(created_at=now, updated_at=now)
        resp = _row_to_response(row)
        assert resp.created_at == "2026-05-09T12:00:00+00:00"

    def test_handles_dict_payload_json(self):
        row = _make_row(payload_json={"source": "test"})
        resp = _row_to_response(row)
        assert resp.payload_json == {"source": "test"}

    def test_handles_string_payload_json(self):
        row = _make_row(payload_json='{"source": "test"}')
        resp = _row_to_response(row)
        assert resp.payload_json == {"source": "test"}
