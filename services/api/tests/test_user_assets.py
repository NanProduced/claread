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
from app.services.user_assets.vocabulary import _merge_payload_on_conflict, SOURCE_REFS_MAX
from app.schemas.user_assets.vocabulary import VocabularyPayload

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
        refs = [{"client_record_id": f"rec{i}", "source_sentence_id": f"s{i}"} for i in range(SOURCE_REFS_MAX + 5)]
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
    @_mock_auth()
    @patch("app.services.user_assets.favorites.db_connection.DB_POOL")
    def test_add_favorite(self, mock_pool, mock_auth):
        fav_id = uuid4()
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"id": fav_id}
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.post(
            "/favorites",
            json={
                "target_type": "sentence",
                "target_key": "sent_001",
                "payload": {"text": "Hello world"},
            },
            headers=AUTH_HEADERS,
        )
        assert response.status_code in (200, 201)

    @_mock_auth()
    @patch("app.services.user_assets.favorites.db_connection.DB_POOL")
    def test_list_favorites(self, mock_pool, mock_auth):
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.get("/favorites", headers=AUTH_HEADERS)
        assert response.status_code == 200

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
    def test_remove_favorite_by_target(self, mock_pool, mock_auth):
        mock_conn = AsyncMock()
        mock_conn.execute.return_value = "UPDATE 1"
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.delete(
            "/favorites/target?target_type=daily_reader_article&target_key=daily_2026_04_28_001",
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 200
        assert response.json()["deleted"] is True


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
