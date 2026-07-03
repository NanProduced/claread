from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.contracts.annotation import compute_text_range_hash, utf16_code_unit_length
from app.main import app
from app.schemas.user_annotations import (
    UserAnnotationCreateRequest,
    UserAnnotationSegment,
    UserAnnotationUpdateRequest,
)
from app.services.user_annotations import (
    _build_target_key,
    _compute_merged_range,
    _resolve_merged_color,
    _resolve_single_sentence_conflict,
    _row_to_response,
    _SingleSentenceRange,
    create_user_annotation,
    list_user_annotations,
)

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
        "color": "warm_yellow",
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
        assert req.color == "warm_yellow"

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

    def test_create_request_rejects_text_range_hash_mismatch(self):
        with pytest.raises(ValidationError, match="text_hash must match selected_text"):
            UserAnnotationCreateRequest(
                analysis_record_id=RECORD_ID,
                anchor_type="text_range",
                sentence_id="s1",
                selected_text="policy choices",
                start_offset=0,
                end_offset=14,
                text_hash=compute_text_range_hash("policy choicex"),
            )

    def test_create_request_rejects_incomplete_multi_text(self):
        with pytest.raises(ValidationError):
            UserAnnotationCreateRequest(
                analysis_record_id=RECORD_ID,
                anchor_type="multi_text",
                selected_text="one ... two",
                segments=[],
            )

    def test_update_request_only_accepts_color(self):
        req = UserAnnotationUpdateRequest(color="soft_mint")
        assert req.color == "soft_mint"

    @pytest.mark.parametrize("color", ["warm_yellow", "soft_mint", "soft_rose"])
    def test_create_request_accepts_supported_colors(self, color):
        req = UserAnnotationCreateRequest(
            selected_text="Sentence",
            sentence_id="s1",
            color=color,
        )
        assert req.color == color

    @pytest.mark.parametrize("color", ["soft_green", "sage_green", "soft_blue", "soft_purple"])
    def test_create_request_rejects_legacy_colors(self, color):
        with pytest.raises(ValidationError):
            UserAnnotationCreateRequest(
                selected_text="Sentence",
                sentence_id="s1",
                color=color,
            )

    @pytest.mark.parametrize("color", ["warm_yellow", "soft_mint", "soft_rose"])
    def test_update_request_accepts_supported_colors(self, color):
        req = UserAnnotationUpdateRequest(color=color)
        assert req.color == color

    @pytest.mark.parametrize("color", ["soft_green", "sage_green", "soft_blue", "soft_purple"])
    def test_update_request_rejects_legacy_colors(self, color):
        with pytest.raises(ValidationError):
            UserAnnotationUpdateRequest(color=color)


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

    def test_build_target_key_multi_text_uses_segment_signature(self):
        req = UserAnnotationCreateRequest(
            analysis_record_id=RECORD_ID,
            anchor_type="multi_text",
            selected_text="alpha ... beta",
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

        reversed_req = req.model_copy(
            update={
                "segments": [
                    req.segments[1],
                    req.segments[0],
                ]
            }
        )
        assert _build_target_key(reversed_req) != _build_target_key(req)

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
    async def test_resolve_single_sentence_subset_preserves_existing_highlight(self):
        existing = _make_row(
            anchor_type="sentence",
            target_key=f"record:{RECORD_ID}:sentence:s1",
            sentence_id="s1",
            selected_text="Institutional memory shapes policy choices.",
            start_offset=None,
            end_offset=None,
            text_hash=None,
            color="warm_yellow",
        )
        updated = _make_row(
            id=existing["id"],
            anchor_type="sentence",
            target_key=existing["target_key"],
            sentence_id="s1",
            selected_text=existing["selected_text"],
            color="warm_yellow",
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
            color="soft_mint",
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
        # Subset preserves existing color, not request color
        assert response.color == "warm_yellow"

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
            color="warm_yellow",
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
        # Superset preserves existing color
        assert response.color == "warm_yellow"

    def test_compute_merged_range(self):
        existing_rows = [
            _make_row(anchor_type="text_range", start_offset=5, end_offset=15),
            _make_row(anchor_type="text_range", start_offset=20, end_offset=30),
        ]
        request_range = _SingleSentenceRange(10, 25)
        merged = _compute_merged_range(existing_rows, request_range)
        assert merged.start_offset == 5
        assert merged.end_offset == 30

    def test_resolve_merged_color_consistent(self):
        rows = [
            _make_row(color="soft_mint"),
            _make_row(color="soft_mint"),
        ]
        assert _resolve_merged_color(rows, "warm_yellow") == "soft_mint"

    def test_resolve_merged_color_inconsistent(self):
        rows = [
            _make_row(color="soft_mint"),
            _make_row(color="warm_yellow"),
        ]
        assert _resolve_merged_color(rows, "warm_yellow") == "warm_yellow"

    @pytest.mark.anyio
    async def test_resolve_partial_overlap_merges_to_union(self):
        # Sentence: "Institutional memory shapes policy choices."
        # Existing: [10, 25), Request: [18, 35)
        # Union:    [10, 35)
        sentence_text = "Institutional memory shapes policy choices."
        existing_text = sentence_text[10:25]
        request_text = sentence_text[18:35]
        merged_text = sentence_text[10:35]
        existing = _make_row(
            anchor_type="text_range",
            target_key=f"record:{RECORD_ID}:range:s1:10:25:{compute_text_range_hash(existing_text)}",
            sentence_id="s1",
            selected_text=existing_text,
            start_offset=10,
            end_offset=25,
            text_hash=compute_text_range_hash(existing_text),
            color="soft_mint",
        )
        merged_hash = compute_text_range_hash(merged_text)
        updated = _make_row(
            id=existing["id"],
            anchor_type="text_range",
            target_key=f"record:{RECORD_ID}:range:s1:10:35:{merged_hash}",
            sentence_id="s1",
            selected_text=merged_text,
            start_offset=10,
            end_offset=35,
            text_hash=merged_hash,
            color="soft_mint",
        )
        conn = AsyncMock()
        conn.fetch.return_value = [existing]
        conn.fetchrow.return_value = updated
        conn.execute.return_value = "UPDATE 0"

        render_scene = {
            "article": {
                "sentences": [
                    {"sentence_id": "s1", "paragraph_id": "p1", "text": sentence_text},
                ],
            },
        }

        req = UserAnnotationCreateRequest(
            analysis_record_id=RECORD_ID,
            anchor_type="text_range",
            sentence_id="s1",
            selected_text=request_text,
            start_offset=18,
            end_offset=35,
            text_hash=compute_text_range_hash(request_text),
            color="warm_yellow",
        )

        response = await _resolve_single_sentence_conflict(
            conn,
            user_id=UUID(USER_ID),
            record_id=UUID(RECORD_ID),
            req=req,
            target_key=f"record:{RECORD_ID}:range:s1:18:35:{compute_text_range_hash(request_text)}",
            render_scene=render_scene,
        )

        assert response is not None
        assert response.anchor_type == "text_range"
        assert response.start_offset == 10
        assert response.end_offset == 35
        # Partial overlap preserves existing color
        assert response.color == "soft_mint"

    @pytest.mark.anyio
    async def test_resolve_partial_overlap_upgrades_to_sentence(self):
        # Existing: [0, 20), request: [15, sentence_end)
        # Union covers whole sentence → upgrade to sentence
        sentence_text = "Institutional memory shapes policy choices."
        existing_text = "Institutional memory"
        existing = _make_row(
            anchor_type="text_range",
            target_key=f"record:{RECORD_ID}:range:s1:0:20:{compute_text_range_hash(existing_text)}",
            sentence_id="s1",
            selected_text=existing_text,
            start_offset=0,
            end_offset=20,
            text_hash=compute_text_range_hash(existing_text),
            color="soft_mint",
        )
        # request text = sentence[15:sentence_len]
        request_text = sentence_text[15:]
        request_len = utf16_code_unit_length(request_text)
        updated = _make_row(
            id=existing["id"],
            anchor_type="sentence",
            target_key=f"record:{RECORD_ID}:sentence:s1",
            sentence_id="s1",
            selected_text=sentence_text,
            start_offset=None,
            end_offset=None,
            text_hash=None,
            color="soft_mint",
        )
        conn = AsyncMock()
        conn.fetch.return_value = [existing]
        conn.fetchrow.return_value = updated
        conn.execute.return_value = "UPDATE 0"

        render_scene = {
            "article": {
                "sentences": [
                    {"sentence_id": "s1", "paragraph_id": "p1", "text": sentence_text},
                ],
            },
        }

        req = UserAnnotationCreateRequest(
            analysis_record_id=RECORD_ID,
            anchor_type="text_range",
            sentence_id="s1",
            selected_text=request_text,
            start_offset=15,
            end_offset=15 + request_len,
            text_hash=compute_text_range_hash(request_text),
            color="warm_yellow",
        )

        response = await _resolve_single_sentence_conflict(
            conn,
            user_id=UUID(USER_ID),
            record_id=UUID(RECORD_ID),
            req=req,
            target_key=(
                f"record:{RECORD_ID}:range:s1:15:"
                f"{15 + request_len}:{compute_text_range_hash(request_text)}"
            ),
            render_scene=render_scene,
        )

        assert response is not None
        assert response.anchor_type == "sentence"
        assert response.target_key == f"record:{RECORD_ID}:sentence:s1"
        assert response.color == "soft_mint"

    @pytest.mark.anyio
    async def test_resolve_multiple_overlaps_merges_all(self):
        # Sentence: "Institutional memory shapes policy choices today."
        # Two existing: [5, 15) and [20, 30), request: [10, 25)
        # Union: [5, 30)
        sentence_text = "Institutional memory shapes policy choices today."
        early_id = uuid4()
        late_id = uuid4()
        earlier_time = datetime(2024, 1, 1, tzinfo=UTC)
        later_time = datetime(2024, 1, 2, tzinfo=UTC)
        existing_1_text = sentence_text[5:15]
        existing_1 = _make_row(
            id=early_id,
            anchor_type="text_range",
            target_key=f"record:{RECORD_ID}:range:s1:5:15:{compute_text_range_hash(existing_1_text)}",
            sentence_id="s1",
            selected_text=existing_1_text,
            start_offset=5,
            end_offset=15,
            text_hash=compute_text_range_hash(existing_1_text),
            color="soft_mint",
            created_at=earlier_time,
        )
        existing_2_text = sentence_text[20:30]
        existing_2 = _make_row(
            id=late_id,
            anchor_type="text_range",
            target_key=f"record:{RECORD_ID}:range:s1:20:30:{compute_text_range_hash(existing_2_text)}",
            sentence_id="s1",
            selected_text=existing_2_text,
            start_offset=20,
            end_offset=30,
            text_hash=compute_text_range_hash(existing_2_text),
            color="soft_mint",
            created_at=later_time,
        )
        merged_text = sentence_text[5:30]
        merged_hash = compute_text_range_hash(merged_text)
        updated = _make_row(
            id=early_id,
            anchor_type="text_range",
            target_key=f"record:{RECORD_ID}:range:s1:5:30:{merged_hash}",
            sentence_id="s1",
            selected_text=merged_text,
            start_offset=5,
            end_offset=30,
            text_hash=merged_hash,
            color="soft_mint",
        )
        conn = AsyncMock()
        conn.fetch.return_value = [existing_1, existing_2]
        conn.fetchrow.return_value = updated
        conn.execute.return_value = "UPDATE 1"

        render_scene = {
            "article": {
                "sentences": [
                    {"sentence_id": "s1", "paragraph_id": "p1", "text": sentence_text},
                ],
            },
        }

        request_text = sentence_text[10:25]
        req = UserAnnotationCreateRequest(
            analysis_record_id=RECORD_ID,
            anchor_type="text_range",
            sentence_id="s1",
            selected_text=request_text,
            start_offset=10,
            end_offset=25,
            text_hash=compute_text_range_hash(request_text),
            color="warm_yellow",
        )

        response = await _resolve_single_sentence_conflict(
            conn,
            user_id=UUID(USER_ID),
            record_id=UUID(RECORD_ID),
            req=req,
            target_key=f"record:{RECORD_ID}:range:s1:10:25:{compute_text_range_hash(request_text)}",
            render_scene=render_scene,
        )

        assert response is not None
        assert response.start_offset == 5
        assert response.end_offset == 30
        # Both existing are soft_mint -> preserved
        assert response.color == "soft_mint"
        # The later row should be in superseded_ids
        assert late_id in response.superseded_ids
        assert early_id not in response.superseded_ids

    @pytest.mark.anyio
    async def test_resolve_multiple_overlaps_color_inconsistent(self):
        sentence_text = "Institutional memory shapes policy choices today."
        early_id = uuid4()
        late_id = uuid4()
        earlier_time = datetime(2024, 1, 1, tzinfo=UTC)
        later_time = datetime(2024, 1, 2, tzinfo=UTC)
        existing_1_text = sentence_text[5:15]
        existing_1 = _make_row(
            id=early_id,
            anchor_type="text_range",
            target_key=f"record:{RECORD_ID}:range:s1:5:15:{compute_text_range_hash(existing_1_text)}",
            sentence_id="s1",
            selected_text=existing_1_text,
            start_offset=5,
            end_offset=15,
            text_hash=compute_text_range_hash(existing_1_text),
            color="soft_mint",
            created_at=earlier_time,
        )
        existing_2_text = sentence_text[20:30]
        existing_2 = _make_row(
            id=late_id,
            anchor_type="text_range",
            target_key=f"record:{RECORD_ID}:range:s1:20:30:{compute_text_range_hash(existing_2_text)}",
            sentence_id="s1",
            selected_text=existing_2_text,
            start_offset=20,
            end_offset=30,
            text_hash=compute_text_range_hash(existing_2_text),
            color="warm_yellow",
            created_at=later_time,
        )
        merged_text = sentence_text[5:30]
        merged_hash = compute_text_range_hash(merged_text)
        updated = _make_row(
            id=early_id,
            anchor_type="text_range",
            target_key=f"record:{RECORD_ID}:range:s1:5:30:{merged_hash}",
            sentence_id="s1",
            selected_text=merged_text,
            start_offset=5,
            end_offset=30,
            text_hash=merged_hash,
            color="warm_yellow",
        )
        conn = AsyncMock()
        conn.fetch.return_value = [existing_1, existing_2]
        conn.fetchrow.return_value = updated
        conn.execute.return_value = "UPDATE 1"

        render_scene = {
            "article": {
                "sentences": [
                    {"sentence_id": "s1", "paragraph_id": "p1", "text": sentence_text},
                ],
            },
        }

        request_text = sentence_text[10:25]
        req = UserAnnotationCreateRequest(
            analysis_record_id=RECORD_ID,
            anchor_type="text_range",
            sentence_id="s1",
            selected_text=request_text,
            start_offset=10,
            end_offset=25,
            text_hash=compute_text_range_hash(request_text),
            color="warm_yellow",
        )

        response = await _resolve_single_sentence_conflict(
            conn,
            user_id=UUID(USER_ID),
            record_id=UUID(RECORD_ID),
            req=req,
            target_key=f"record:{RECORD_ID}:range:s1:10:25:{compute_text_range_hash(request_text)}",
            render_scene=render_scene,
        )

        assert response is not None
        # Colors inconsistent → use request color (warm_yellow)
        assert response.color == "warm_yellow"


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
                "color": "warm_yellow",
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
        conn.fetch.return_value = [_make_row(color="soft_mint")]

        response = client.get(
            f"/user-annotations?analysis_record_id={RECORD_ID}",
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        data = response.json()["items"]
        assert len(data) == 1
        assert data[0]["color"] == "soft_mint"

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


@pytest.mark.asyncio
class TestServiceCharacterization:
    @patch("app.services.user_annotations.load_render_scene", new_callable=AsyncMock)
    @patch("app.services.user_annotations.db_connect.DB_POOL")
    async def test_create_text_range_annotation_rejects_scene_quote_mismatch(
        self,
        mock_pool,
        mock_load_render_scene,
    ):
        pool, conn = _mock_db_pool()
        mock_pool.acquire = pool.acquire
        conn.fetch.return_value = []
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
        req = UserAnnotationCreateRequest(
            analysis_record_id=RECORD_ID,
            anchor_type="text_range",
            sentence_id="s1",
            paragraph_id="p1",
            selected_text="policy",
            start_offset=14,
            end_offset=20,
            text_hash=compute_text_range_hash("policy"),
        )

        with pytest.raises(HTTPException) as exc_info:
            await create_user_annotation(UUID(USER_ID), req)

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "selected_text does not match sentence offsets"
        conn.fetchrow.assert_not_awaited()

    @patch("app.services.user_annotations.db_connect.DB_POOL")
    async def test_list_user_annotations_orders_by_created_at_desc(self, mock_pool):
        pool, conn = _mock_db_pool()
        mock_pool.acquire = pool.acquire
        older = _make_row(sentence_id="s2", created_at=datetime(2024, 1, 1, tzinfo=UTC))
        newer = _make_row(sentence_id="s1", created_at=datetime(2024, 1, 2, tzinfo=UTC))
        conn.fetch.return_value = [newer, older]

        response = await list_user_annotations(UUID(USER_ID), RECORD_ID)

        assert [item.sentence_id for item in response] == ["s1", "s2"]
        query = conn.fetch.await_args.args[0]
        assert "ORDER BY created_at DESC" in query
