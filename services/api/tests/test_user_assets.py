from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.schemas.user_assets.favorites import FavoriteCreateRequest
from app.services.user_assets.vocabulary import SOURCE_REFS_MAX, _merge_payload_on_conflict

client = TestClient(app)

USER_ID = "00000000-0000-0000-0000-000000000021"
RECORD_ID = uuid4()
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


def _make_favorite_row(**overrides):
    now = datetime.now(UTC)
    defaults = {
        "id": uuid4(),
        "user_id": UUID(USER_ID),
        "target_type": "analysis_record",
        "target_key": f"analysis_record:{RECORD_ID}",
        "analysis_record_id": RECORD_ID,
        "payload_json": {"title": "Test article"},
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(overrides)
    return defaults


class TestVocabularyMergeLogic:
    def test_merge_appends_source_refs(self):
        existing = {"source_refs": [{"client_record_id": "r1", "source_sentence_id": "s1"}]}
        incoming = {"source_refs": [{"client_record_id": "r2", "source_sentence_id": "s2"}]}
        merged = _merge_payload_on_conflict(existing, incoming, "policy")
        assert merged["source_refs"] == [
            {"client_record_id": "r1", "source_sentence_id": "s1"},
            {"client_record_id": "r2", "source_sentence_id": "s2"},
        ]

    def test_merge_truncates_source_refs(self):
        existing = {
            "source_refs": [
                {"client_record_id": f"r{i}", "source_sentence_id": f"s{i}"}
                for i in range(SOURCE_REFS_MAX)
            ]
        }
        incoming = {"source_refs": [{"client_record_id": "overflow", "source_sentence_id": "sx"}]}
        merged = _merge_payload_on_conflict(existing, incoming, "policy")
        assert len(merged["source_refs"]) == SOURCE_REFS_MAX


class TestFavoriteSchema:
    def test_analysis_record_requires_record_id(self):
        with pytest.raises(ValidationError):
            FavoriteCreateRequest(
                target_type="analysis_record",
                target_key="analysis_record:missing",
            )

    def test_rejects_text_range_target_type(self):
        with pytest.raises(ValidationError):
            FavoriteCreateRequest(  # type: ignore[arg-type]
                target_type="text_range",
                target_key="range:s1:0:4",
            )

    def test_daily_reader_article_does_not_require_analysis_record_id(self):
        req = FavoriteCreateRequest(
            target_type="daily_reader_article",
            target_key="daily_reader_article:2026-05-21",
        )
        assert req.analysis_record_id is None


class TestFavoriteRoutes:
    @_mock_auth()
    @patch("app.services.user_assets.favorites.db_connection.DB_POOL")
    def test_add_analysis_record_favorite(self, mock_pool, _mock_session):
        pool, conn = _mock_db_pool()
        mock_pool.acquire = pool.acquire
        favorite_id = uuid4()
        conn.fetchrow.return_value = {"id": favorite_id}

        response = client.post(
            "/favorites",
            json={
                "target_type": "analysis_record",
                "target_key": f"analysis_record:{RECORD_ID}",
                "analysis_record_id": str(RECORD_ID),
                "payload_json": {"title": "Test article"},
            },
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        assert response.json() == {"id": str(favorite_id), "ok": True}

    @_mock_auth()
    @patch("app.services.user_assets.favorites.db_connection.DB_POOL")
    def test_add_daily_reader_article_favorite(self, mock_pool, _mock_session):
        pool, conn = _mock_db_pool()
        mock_pool.acquire = pool.acquire
        favorite_id = uuid4()
        conn.fetchrow.return_value = {"id": favorite_id}

        response = client.post(
            "/favorites",
            json={
                "target_type": "daily_reader_article",
                "target_key": "daily_reader_article:2026-05-21",
                "payload_json": {"article_id": "2026-05-21"},
            },
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        assert response.json() == {"id": str(favorite_id), "ok": True}

    @_mock_auth()
    @patch("app.services.user_assets.favorites.db_connection.DB_POOL")
    def test_list_favorites(self, mock_pool, _mock_session):
        pool, conn = _mock_db_pool()
        mock_pool.acquire = pool.acquire
        conn.fetch.return_value = [_make_favorite_row()]

        response = client.get("/favorites", headers=AUTH_HEADERS)

        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 1
        assert payload["items"][0]["target_type"] == "analysis_record"

    @_mock_auth()
    @patch("app.services.user_assets.favorites.db_connection.DB_POOL")
    def test_remove_favorite_by_target(self, mock_pool, _mock_session):
        pool, conn = _mock_db_pool()
        mock_pool.acquire = pool.acquire
        conn.execute.return_value = "UPDATE 1"

        response = client.delete(
            f"/favorites/target?target_type=analysis_record&target_key=analysis_record:{RECORD_ID}",
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        assert response.json() == {"deleted": True}

    @_mock_auth()
    @patch("app.services.user_assets.favorites.db_connection.DB_POOL")
    def test_remove_favorite_by_analysis_record(self, mock_pool, _mock_session):
        pool, conn = _mock_db_pool()
        mock_pool.acquire = pool.acquire
        conn.execute.return_value = "UPDATE 1"

        response = client.delete(f"/favorites/{RECORD_ID}", headers=AUTH_HEADERS)

        assert response.status_code == 200
        assert response.json() == {"deleted": True}
