"""B-4 Daily Reader console-reserved admin API contracts."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
ADMIN_HEADERS = {"x-admin-api-key": "secret"}


@pytest.fixture
def admin_settings(monkeypatch):
    monkeypatch.setattr(
        "app.api.routes.daily_reader_admin.get_settings",
        lambda: SimpleNamespace(daily_reader_admin_api_key="secret"),
    )


def _mock_pool(mock_pool: MagicMock, *, rows: list[dict]) -> AsyncMock:
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = rows
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_conn


def _review_row() -> dict:
    return {
        "id": "daily_2026_08_20_001",
        "title": "中文主标题",
        "subtitle": "English source deck",
        "original_title": "Original headline",
        "subtitle_zh": "中文副标题",
        "source": "BBC News",
        "source_url": "https://example.com/article",
        "publish_date": date(2026, 8, 20),
        "difficulty": "B2",
        "read_time_minutes": 5,
        "tags": ["科技", "社会"],
        "cover_image_url": "/static/covers/hero.webp",
        "cover_theme": "editorial_warm",
        "score": 8.6,
        "status": "draft",
        "review_status": "pending",
        "reviewed_by": None,
        "reviewed_at": None,
        "created_at": datetime(2026, 8, 20, tzinfo=UTC),
        "updated_at": datetime(2026, 8, 20, tzinfo=UTC),
        "body_json": {"paragraphs": [{"text": "Copyright Acme. All rights reserved."}]},
        "highlights_json": [],
        "paragraph_notes_json": {},
        "takeaways_json": {},
        "pipeline_meta": {
            "score": 8.6,
            "cover": {
                "candidates": [
                    {
                        "url": "https://images.example/hero.jpg",
                        "upgraded_url": None,
                        "position": "meta",
                        "valid": True,
                        "reason": None,
                        "width": 1600,
                        "height": 900,
                    }
                ],
                "selected": {
                    "cover": {
                        "url": "/static/covers/hero.webp",
                        "source_url": "https://images.example/hero.jpg",
                        "width": 1600,
                        "height": 900,
                        "caption_zh": "中文图说",
                    }
                },
            },
        },
    }


class TestReviewQueue:
    def test_requires_admin_key(self, admin_settings):
        response = client.get("/daily-reader/admin/review-queue")
        assert response.status_code == 422

    def test_rejects_wrong_admin_key(self, admin_settings):
        response = client.get(
            "/daily-reader/admin/review-queue",
            headers={"x-admin-api-key": "wrong"},
        )
        assert response.status_code == 401

    @patch("app.services.daily_reader.service.db_connection.DB_POOL")
    def test_returns_persisted_review_data_and_machine_flags(
        self,
        mock_pool: MagicMock,
        admin_settings,
    ):
        mock_conn = _mock_pool(mock_pool, rows=[_review_row()])

        response = client.get(
            "/daily-reader/admin/review-queue?limit=20&offset=0",
            headers=ADMIN_HEADERS,
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["limit"] == 20
        assert payload["offset"] == 0
        assert payload["has_more"] is False
        item = payload["items"][0]
        assert item["title"] == "中文主标题"
        assert item["subtitle_zh"] == "中文副标题"
        assert item["selection_score"] == 8.6
        assert item["review_score"] is None
        assert item["review_score_available"] is False
        assert item["machine_flags"]["cover_quality"] == "qualified"
        assert item["machine_flags"]["cover_width"] == 1600
        assert item["machine_flags"]["cover_missing"] is False
        assert item["machine_flags"]["boilerplate_suspected"] is True
        assert item["cover_candidates"][0]["valid"] is True
        assert item["selected_cover"]["caption_zh"] == "中文图说"

        sql, limit, offset = mock_conn.fetch.call_args.args
        assert "status = 'draft'" in sql
        assert "review_status = 'pending'" in sql
        assert limit == 21
        assert offset == 0

    @patch("app.services.daily_reader.service.db_connection.DB_POOL")
    def test_limit_uses_one_extra_row_for_has_more(
        self,
        mock_pool: MagicMock,
        admin_settings,
    ):
        _mock_pool(mock_pool, rows=[_review_row(), _review_row()])

        response = client.get(
            "/daily-reader/admin/review-queue?limit=1&offset=3",
            headers=ADMIN_HEADERS,
        )

        assert response.status_code == 200
        assert len(response.json()["items"]) == 1
        assert response.json()["has_more"] is True


class TestDraftPatch:
    @patch("app.services.daily_reader.service.db_connection.DB_POOL")
    def test_updates_only_whitelisted_fields_and_resets_review(
        self,
        mock_pool: MagicMock,
        admin_settings,
    ):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {
            "id": "daily_2026_08_20_001",
            "review_status": "pending",
        }
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.patch(
            "/daily-reader/admin/daily_2026_08_20_001",
            headers=ADMIN_HEADERS,
            json={
                "title": "修订后的中文标题",
                "subtitle_zh": None,
                "cover_image_url": "https://cdn.example/new-cover.webp",
                "tags": [" 科技 ", "科技", "社会"],
            },
        )

        assert response.status_code == 200
        assert response.json() == {
            "id": "daily_2026_08_20_001",
            "status": "updated",
            "review_status": "pending",
        }
        sql, *args = mock_conn.fetchrow.call_args.args
        assert "WHERE id = $1 AND status = 'draft'" in sql
        assert "review_status = 'pending'" in sql
        assert "reviewed_by = NULL" in sql
        assert "reviewed_at = NULL" in sql
        assert "title" in sql
        assert "subtitle_zh" in sql
        assert "cover_image_url" in sql
        assert "tags" in sql
        assert args[0] == "daily_2026_08_20_001"
        assert args[1] is True
        assert args[2] == "修订后的中文标题"
        assert args[-1] == ["科技", "社会"]

    @patch("app.services.daily_reader.service.db_connection.DB_POOL")
    def test_published_article_is_conflict(
        self,
        mock_pool: MagicMock,
        admin_settings,
    ):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.side_effect = [None, {"status": "published"}]
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.patch(
            "/daily-reader/admin/daily_published",
            headers=ADMIN_HEADERS,
            json={"title": "不可修改"},
        )

        assert response.status_code == 409
        assert response.json()["detail"] == "Only draft articles can be edited"

    @patch("app.services.daily_reader.service.db_connection.DB_POOL")
    def test_unknown_article_is_not_found(
        self,
        mock_pool: MagicMock,
        admin_settings,
    ):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.side_effect = [None, None]
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.patch(
            "/daily-reader/admin/missing",
            headers=ADMIN_HEADERS,
            json={"title": "不存在"},
        )

        assert response.status_code == 404

    @patch("app.services.daily_reader.service.db_connection.DB_POOL")
    def test_noop_draft_does_not_rewrite_review_audit(
        self,
        mock_pool: MagicMock,
        admin_settings,
    ):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.side_effect = [None, {"status": "draft"}]
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.patch(
            "/daily-reader/admin/daily_same",
            headers=ADMIN_HEADERS,
            json={"title": "未发生变化"},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "unchanged"
        update_sql = mock_conn.fetchrow.call_args_list[0].args[0]
        assert "IS DISTINCT FROM" in update_sql

    @pytest.mark.parametrize(
        ("body", "field"),
        [
            ({}, "body"),
            ({"title": "   "}, "title"),
            ({"cover_image_url": "javascript:alert(1)"}, "cover_image_url"),
            ({"status": "published"}, "status"),
        ],
    )
    def test_rejects_invalid_or_non_whitelisted_fields(
        self,
        admin_settings,
        body: dict,
        field: str,
    ):
        response = client.patch(
            "/daily-reader/admin/daily_2026_08_20_001",
            headers=ADMIN_HEADERS,
            json=body,
        )
        assert response.status_code == 422
        assert field in response.text


class TestSingleGenerate:
    @pytest.mark.parametrize(
        ("body", "expected_count"),
        [
            ({"single": True, "max_count": 5}, 1),
            ({}, 3),
        ],
    )
    def test_uses_real_pipeline_count_seam(
        self,
        admin_settings,
        body: dict,
        expected_count: int,
    ):
        tracker = MagicMock(run_id="run-b4")
        tracker.start = AsyncMock()
        tracker.fail = AsyncMock()
        scheduled: list[object] = []

        def capture_task(coro):
            scheduled.append(coro)
            return MagicMock()

        pipeline = AsyncMock()
        with (
            patch(
                "app.services.daily_reader.pipeline_tracker.PipelineRunTracker",
                return_value=tracker,
            ),
            patch(
                "app.services.daily_reader.pipeline.run_daily_pipeline",
                pipeline,
            ),
            patch("asyncio.create_task", side_effect=capture_task),
        ):
            response = client.post(
                "/daily-reader/admin/generate",
                headers=ADMIN_HEADERS,
                json=body,
            )
            assert response.status_code == 200
            assert response.json()["task_id"] == "run-b4"
            assert len(scheduled) == 1
            asyncio.run(scheduled[0])

        pipeline.assert_awaited_once()
        assert pipeline.await_args.kwargs["max_count"] == expected_count


@pytest.fixture
def anyio_backend():
    return "asyncio"
