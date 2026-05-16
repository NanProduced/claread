"""T-02: User assets (records/vocabulary/favorites) tests.

Covers: vocabulary merge logic, favorites add/list/remove, records CRUD routes.
All DB interactions are mocked.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.contracts.annotation import build_multi_text_target_key, compute_text_range_hash
from app.schemas.user_assets.favorites import FavoriteCreateRequest
from app.services.user_assets.vocabulary import SOURCE_REFS_MAX, _merge_payload_on_conflict

client = TestClient(app)

USER_ID = "00000000-0000-0000-0000-000000000001"
AUTH_HEADERS = {"Authorization": "Bearer test_token"}


def _mock_auth():
    return patch(
        "app.services.auth.dependencies.validate_session",
        new_callable=AsyncMock,
        return_value=type("SessionInfo", (), {
            "user_id": UUID(USER_ID),
            "session_id": uuid4(),
        })(),
    )


class TestVocabularyMergeLogic:
    def test_merge_empty_existing(self):
        result = _merge_payload_on_conflict(
            existing_payload={},
            incoming_payload={"source_refs": [], "collected_forms": ["run"]},
            incoming_display_word="running",
        )
        assert "running" in result["collected_forms"]

    def test_merge_appends_source_refs(self):
        existing = {
            "source_refs": [
                {"client_record_id": "rec1", "source_sentence_id": "s1"},
            ],
            "collected_forms": ["run"],
        }
        incoming = {
            "source_refs": [
                {"client_record_id": "rec2", "source_sentence_id": "s2"},
            ],
            "collected_forms": ["running"],
        }
        result = _merge_payload_on_conflict(existing, incoming, "running")
        assert len(result["source_refs"]) == 2

    def test_merge_dedup_source_refs(self):
        existing = {
            "source_refs": [
                {"client_record_id": "rec1", "source_sentence_id": "s1"},
            ],
            "collected_forms": [],
        }
        incoming = {
            "source_refs": [
                {"client_record_id": "rec1", "source_sentence_id": "s1"},
            ],
            "collected_forms": [],
        }
        result = _merge_payload_on_conflict(existing, incoming, "test")
        assert len(result["source_refs"]) == 1

    def test_merge_truncates_source_refs(self):
        refs = [
            {"client_record_id": f"rec{i}", "source_sentence_id": f"s{i}"}
            for i in range(SOURCE_REFS_MAX + 5)
        ]
        existing = {"source_refs": refs[:SOURCE_REFS_MAX + 5], "collected_forms": []}
        incoming = {"source_refs": [], "collected_forms": []}
        result = _merge_payload_on_conflict(existing, incoming, "test")
        assert len(result["source_refs"]) <= SOURCE_REFS_MAX

    def test_merge_dedup_collected_forms_case_insensitive(self):
        existing = {"source_refs": [], "collected_forms": ["Run"]}
        incoming = {"source_refs": [], "collected_forms": ["run"]}
        result = _merge_payload_on_conflict(existing, incoming, "run")
        assert len(result["collected_forms"]) == 1

    def test_merge_preserves_audio_url(self):
        existing = {"source_refs": [], "collected_forms": [], "audio_url": "https://old.url"}
        incoming = {"source_refs": [], "collected_forms": [], "audio_url": "https://new.url"}
        result = _merge_payload_on_conflict(existing, incoming, "test")
        assert result["audio_url"] == "https://old.url"

    def test_merge_adds_audio_url_if_missing(self):
        existing = {"source_refs": [], "collected_forms": []}
        incoming = {"source_refs": [], "collected_forms": [], "audio_url": "https://new.url"}
        result = _merge_payload_on_conflict(existing, incoming, "test")
        assert result["audio_url"] == "https://new.url"


class TestVocabularyRoutes:
    @_mock_auth()
    @patch("app.services.daily_reader.service.db_connection.DB_POOL")
    def test_list_vocabulary_unauthorized(self, mock_pool, mock_auth):
        response = client.get("/vocabulary")
        assert response.status_code == 403 or response.status_code == 401

    @_mock_auth()
    @patch("app.services.user_assets.vocabulary.db_connection.DB_POOL")
    def test_list_vocabulary_empty(self, mock_pool, mock_auth):
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        mock_conn.fetchval.return_value = 0
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.get("/vocabulary", headers=AUTH_HEADERS)
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert data["total"] == 0


class TestFavoritesRoutes:
    @pytest.mark.parametrize("target_type", ["analysis_record", "sentence", "paragraph"])
    def test_favorite_create_request_keeps_legacy_target_types(self, target_type):
        req = FavoriteCreateRequest(target_type=target_type, target_key="legacy_target")
        assert req.target_type == target_type

    def test_text_range_favorite_validates_payload_hash(self):
        record_id = uuid4()
        with pytest.raises(ValueError):
            FavoriteCreateRequest(
                analysis_record_id=record_id,
                target_type="text_range",
                target_key="range_target",
                payload_json={
                    "sentence_id": "s1",
                    "start_offset": 0,
                    "end_offset": 5,
                    "selected_text": "hello",
                    "text_hash": "deadbeef",
                },
            )

    def test_multi_text_favorite_validates_payload_hash(self):
        record_id = uuid4()
        with pytest.raises(ValueError):
            FavoriteCreateRequest(
                analysis_record_id=record_id,
                target_type="multi_text",
                target_key="multi_target",
                payload_json={
                    "segments": [
                        {
                            "paragraph_id": "p1",
                            "sentence_id": "s1",
                            "start_offset": 0,
                            "end_offset": 5,
                            "selected_text": "hello",
                            "text_hash": compute_text_range_hash("hello"),
                        },
                        {
                            "paragraph_id": "p2",
                            "sentence_id": "s2",
                            "start_offset": 0,
                            "end_offset": 5,
                            "selected_text": "world",
                            "text_hash": "deadbeef",
                        },
                    ]
                },
            )

    @_mock_auth()
    @patch("app.services.user_assets.favorites.db_connection.DB_POOL")
    def test_add_text_range_favorite(self, mock_pool, mock_auth):
        fav_id = uuid4()
        mock_conn = AsyncMock()
        mock_conn.fetchrow.side_effect = [
            {
                "render_scene_json": {
                    "article": {
                        "sentences": [
                            {
                                "sentence_id": "s_001",
                                "paragraph_id": "p_001",
                                "text": "hello range text world",
                            }
                        ]
                    }
                }
            },
            {"id": fav_id},
        ]
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        record_id = uuid4()
        text_hash = compute_text_range_hash("range text")
        range_key = f"{record_id}:p_001:s_001:6:16:{text_hash}"
        response = client.post(
            "/favorites",
            json={
                "analysis_record_id": str(record_id),
                "target_type": "text_range",
                "target_key": range_key,
                "payload_json": {
                    "paragraph_id": "p_001",
                    "sentence_id": "s_001",
                    "start_offset": 6,
                    "end_offset": 16,
                    "selected_text": "range text",
                    "text_hash": text_hash,
                },
            },
            headers=AUTH_HEADERS,
        )
        assert response.status_code in (200, 201)
        assert response.json() == {"id": str(fav_id), "ok": True}

        args = mock_conn.fetchrow.call_args.args
        assert args[2] == "text_range"
        assert args[3] == range_key
        assert args[4] == record_id
        assert args[5]["selected_text"] == "range text"

    @_mock_auth()
    @patch("app.services.user_assets.favorites.db_connection.DB_POOL")
    def test_add_multi_text_favorite(self, mock_pool, mock_auth):
        fav_id = uuid4()
        record_id = uuid4()
        first_hash = compute_text_range_hash("range")
        second_hash = compute_text_range_hash("text")
        target_key = build_multi_text_target_key(
            str(record_id),
            [
                {
                    "paragraph_id": "p_001",
                    "sentence_id": "s_001",
                    "start_offset": 6,
                    "end_offset": 11,
                    "text_hash": first_hash,
                },
                {
                    "paragraph_id": "p_002",
                    "sentence_id": "s_002",
                    "start_offset": 0,
                    "end_offset": 4,
                    "text_hash": second_hash,
                },
            ],
        )
        mock_conn = AsyncMock()
        mock_conn.fetchrow.side_effect = [
            {
                "render_scene_json": {
                    "article": {
                        "sentences": [
                            {
                                "sentence_id": "s_001",
                                "paragraph_id": "p_001",
                                "text": "hello range world",
                            },
                            {
                                "sentence_id": "s_002",
                                "paragraph_id": "p_002",
                                "text": "text after",
                            },
                        ]
                    }
                }
            },
            {"id": fav_id},
        ]
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.post(
            "/favorites",
            json={
                "analysis_record_id": str(record_id),
                "target_type": "multi_text",
                "target_key": target_key,
                "payload_json": {
                    "selected_text": "range text",
                    "segments": [
                        {
                            "paragraph_id": "p_001",
                            "sentence_id": "s_001",
                            "start_offset": 6,
                            "end_offset": 11,
                            "selected_text": "range",
                            "text_hash": first_hash,
                        },
                        {
                            "paragraph_id": "p_002",
                            "sentence_id": "s_002",
                            "start_offset": 0,
                            "end_offset": 4,
                            "selected_text": "text",
                            "text_hash": second_hash,
                        },
                    ],
                },
            },
            headers=AUTH_HEADERS,
        )
        assert response.status_code in (200, 201)
        assert response.json() == {"id": str(fav_id), "ok": True}

    @_mock_auth()
    @patch("app.services.user_assets.favorites.db_connection.DB_POOL")
    def test_list_text_range_favorites(self, mock_pool, mock_auth):
        fav_id = uuid4()
        record_id = uuid4()
        now = datetime.now(UTC)
        text_hash = compute_text_range_hash("range text")
        range_key = f"{record_id}:p_001:s_001:6:16:{text_hash}"
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [
            {
                "id": fav_id,
                "user_id": UUID(USER_ID),
                "target_type": "text_range",
                "target_key": range_key,
                "analysis_record_id": record_id,
                "payload_json": {
                    "paragraph_id": "p_001",
                    "sentence_id": "s_001",
                    "start_offset": 6,
                    "end_offset": 16,
                    "selected_text": "range text",
                    "text_hash": text_hash,
                },
                "note": None,
                "created_at": now,
                "updated_at": now,
            },
        ]
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.get("/favorites", headers=AUTH_HEADERS)
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["target_type"] == "text_range"
        assert data["items"][0]["target_key"] == range_key
        assert data["items"][0]["payload_json"]["selected_text"] == "range text"

    @_mock_auth()
    @patch("app.services.user_assets.favorites.db_connection.DB_POOL")
    def test_remove_favorite(self, mock_pool, mock_auth):
        mock_conn = AsyncMock()
        mock_conn.execute.return_value = "UPDATE 1"
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        record_id = str(uuid4())
        response = client.delete(
            f"/favorites/{record_id}",
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 200

    @_mock_auth()
    @patch("app.services.user_assets.favorites.db_connection.DB_POOL")
    def test_remove_text_range_favorite_by_target(self, mock_pool, mock_auth):
        mock_conn = AsyncMock()
        mock_conn.execute.return_value = "UPDATE 1"
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        record_id = uuid4()
        text_hash = compute_text_range_hash("range text")
        range_key = f"{record_id}:p_001:s_001:6:16:{text_hash}"
        response = client.delete(
            f"/favorites/target?target_type=text_range&target_key={range_key}",
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 200
        assert response.json()["deleted"] is True

        args = mock_conn.execute.call_args.args
        assert args[2] == "text_range"
        assert args[3] == range_key


class TestRecordsRoutes:
    @_mock_auth()
    @patch("app.services.user_assets.records.db_connection.DB_POOL")
    def test_list_records(self, mock_pool, mock_auth):
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        mock_conn.fetchval.return_value = 0
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.get("/records", headers=AUTH_HEADERS)
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data

    @_mock_auth()
    @patch("app.services.user_assets.records.db_connection.DB_POOL")
    def test_get_record_not_found(self, mock_pool, mock_auth):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = None
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.get(f"/records/{uuid4()}", headers=AUTH_HEADERS)
        assert response.status_code == 404
