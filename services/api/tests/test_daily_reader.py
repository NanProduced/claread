"""T-01: Daily Reader workflow/pipeline critical path tests.

Covers: today articles, article list, article detail, 404, pipeline selection logic.
All DB interactions are mocked.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

MOCK_ARTICLE_ROW = {
    "id": "daily_2026_04_28_001",
    "title": "Test Article",
    "subtitle": "A subtitle",
    "source": "The Guardian",
    "source_url": "https://example.com/article",
    "publish_date": date(2026, 4, 28),
    "difficulty": "intermediate",
    "read_time_minutes": 5,
    "tags": ["science", "technology"],
    "cover_image_url": "https://cdn.example.com/cover.jpg",
    "cover_theme": "editorial_warm",
    "body_json": {"paragraphs": [{"text": "Hello world"}]},
    "highlights_json": [{"text": "key point"}],
    "paragraph_notes_json": {"article_summary": "test summary", "notes": []},
    "takeaways_json": {"article_takeaway": "test takeaway"},
}

MOCK_LIST_ROW = {
    "id": "daily_2026_04_28_001",
    "title": "Test Article",
    "subtitle": "A subtitle",
    "source": "The Guardian",
    "publish_date": date(2026, 4, 28),
    "difficulty": "intermediate",
    "read_time_minutes": 5,
    "tags": ["science"],
    "cover_image_url": None,
    "cover_theme": "editorial_warm",
}


class TestTodayArticles:
    @patch("app.services.daily_reader.service.db_connection.DB_POOL")
    def test_today_articles_returns_published(self, mock_pool):
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [MOCK_ARTICLE_ROW]
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.get("/daily-reader/today")
        assert response.status_code == 200
        data = response.json()
        assert "articles" in data
        assert len(data["articles"]) == 1
        assert data["articles"][0]["id"] == "daily_2026_04_28_001"

    @patch("app.services.daily_reader.service.db_connection.DB_POOL")
    def test_today_articles_empty(self, mock_pool):
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.get("/daily-reader/today")
        assert response.status_code == 200
        data = response.json()
        assert data["articles"] == []


class TestArticleList:
    @patch("app.services.daily_reader.service.db_connection.DB_POOL")
    def test_article_list_default(self, mock_pool):
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [MOCK_LIST_ROW]
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.get("/daily-reader")
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "cursor" in data
        assert "has_more" in data

    @patch("app.services.daily_reader.service.db_connection.DB_POOL")
    def test_article_list_with_cursor(self, mock_pool):
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [MOCK_LIST_ROW]
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.get("/daily-reader?cursor=2026-04-27&limit=5")
        assert response.status_code == 200

    @patch("app.services.daily_reader.service.db_connection.DB_POOL")
    def test_article_list_has_more(self, mock_pool):
        mock_conn = AsyncMock()
        rows = [MOCK_LIST_ROW, MOCK_LIST_ROW]
        mock_conn.fetch.return_value = rows
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.get("/daily-reader?limit=1")
        assert response.status_code == 200
        data = response.json()
        assert data["has_more"] is True


class TestArticleDetail:
    @patch("app.services.daily_reader.service.db_connection.DB_POOL")
    def test_article_detail_found(self, mock_pool):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = MOCK_ARTICLE_ROW
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.get("/daily-reader/daily_2026_04_28_001")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "daily_2026_04_28_001"
        assert data["title"] == "Test Article"
        assert "body" in data

    @patch("app.services.daily_reader.service.db_connection.DB_POOL")
    def test_article_detail_not_found(self, mock_pool):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = None
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.get("/daily-reader/nonexistent_id")
        assert response.status_code == 404


class TestPipelineSelection:
    def test_select_diverse_candidates_basic(self):
        from app.services.daily_reader.pipeline import select_diverse_candidates
        from app.services.daily_reader.discovery import DiscoveredArticle
        from app.services.daily_reader.scoring import ArticleScore

        articles = []
        for i in range(5):
            a = DiscoveredArticle(
                title=f"Article {i}",
                url=f"https://example.com/{i}",
                source="The Guardian",
                description=f"Desc {i}",
                tags=["science"] if i % 2 == 0 else ["politics"],
                text=f"Text content {i} " * 50,
                word_count=200 + i * 10,
            )
            s = ArticleScore(
                score=8.0 - i * 0.5,
                difficulty="intermediate",
                tags=["science"] if i % 2 == 0 else ["politics"],
                language_richness=7.0,
                topic_interest=8.0,
                structure_clarity=7.0,
                cultural_value=6.0,
            )
            articles.append((a, s))

        result = select_diverse_candidates(articles, max_count=3)
        assert len(result) <= 3
        assert len(result) > 0

    def test_select_diverse_candidates_source_limit(self):
        from app.services.daily_reader.pipeline import select_diverse_candidates
        from app.services.daily_reader.discovery import DiscoveredArticle
        from app.services.daily_reader.scoring import ArticleScore

        articles = []
        for i in range(5):
            a = DiscoveredArticle(
                title=f"Article {i}",
                url=f"https://example.com/{i}",
                source="SameSource",
                description=f"Desc {i}",
                tags=["topic_a"],
                text=f"Text {i} " * 50,
                word_count=200,
            )
            s = ArticleScore(
                score=9.0 - i * 0.5,
                difficulty="intermediate",
                tags=["topic_a"],
                language_richness=7.0,
                topic_interest=8.0,
                structure_clarity=7.0,
                cultural_value=6.0,
            )
            articles.append((a, s))

        result = select_diverse_candidates(articles, max_count=5, max_same_source=2)
        sources = [a.source for a, _ in result]
        assert sources.count("SameSource") <= 2


@pytest.mark.anyio
async def test_score_article_records_skipped_event_when_model_unavailable():
    from app.services.daily_reader.discovery import DiscoveredArticle
    from app.services.daily_reader.scoring import score_article

    article = DiscoveredArticle(
        title="Test article",
        url="https://example.com/test",
        source="The Guardian",
        description="Desc",
        tags=["science"],
        text="This is a sufficiently long test article. " * 40,
        word_count=500,
    )

    with (
        patch("app.config.settings.get_settings", return_value=MagicMock()),
        patch("app.llm.router.build_model_for_route", return_value=(None, None)),
        patch(
            "app.services.daily_reader.scoring.record_ai_usage_event",
            AsyncMock(return_value=True),
        ) as usage_mock,
        patch("app.services.daily_reader.scoring.get_prompt_version", return_value="test-prompts"),
    ):
        score = await score_article(article)

    assert score is not None
    event = usage_mock.await_args.args[0]
    assert event.status == "skipped"
    assert event.usage_scope == "system_internal"


@pytest.fixture
def anyio_backend():
    return "asyncio"
