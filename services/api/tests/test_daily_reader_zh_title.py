"""A-3: Chinese headline / original_title / subtitle_zh / tags_zh.

Covers: takeaways schema validation, _assemble_payload projection,
retry UPDATE coverage, and the incremental migration script
(reentrant up, old-row backfill, down).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import asyncpg
import pytest
from pydantic import ValidationError

from app.schemas.internal.daily_drafts import CloseReadingTakeaways
from app.services.daily_reader.discovery import DiscoveredArticle
from app.services.daily_reader.pipeline import _assemble_payload, run_workflow_only
from app.services.daily_reader.scoring import ArticleScore

REPO_ROOT = Path(__file__).resolve().parents[3]
BASELINE_SQL = (REPO_ROOT / "infra" / "migrations" / "0001_initial.sql").read_text(
    encoding="utf-8"
)
UP_SQL = (
    REPO_ROOT / "infra" / "scripts" / "alter_daily_readers_zh_title.sql"
).read_text(encoding="utf-8")
DOWN_SQL = (
    REPO_ROOT / "infra" / "scripts" / "alter_daily_readers_zh_title_down.sql"
).read_text(encoding="utf-8")


def _takeaways_dict(**overrides) -> dict:
    base = {
        "title_zh": "蜂群衰退下的静默危机",
        "subtitle_zh": "野生蜂数量下滑正改变传粉格局",
        "tags_zh": ["生态", "农业"],
        "article_takeaway": "一句话总结",
        "discussion_questions": ["Q1?", "Q2?"],
    }
    base.update(overrides)
    return base


def _article() -> DiscoveredArticle:
    return DiscoveredArticle(
        title="Bumblebees in decline, scientists warn",
        url="https://example.com/a",
        source="BBC News",
        description="English description",
        text="word " * 50,
        word_count=50,
        tags=["source-tag"],
    )


def _score() -> ArticleScore:
    return ArticleScore(score=8.0, difficulty="B2", tags=["news", "science"])


class TestTakeawaysSchema:
    def test_zh_fields_required(self) -> None:
        data = _takeaways_dict()
        for key in ("title_zh", "subtitle_zh", "tags_zh"):
            missing = {k: v for k, v in data.items() if k != key}
            with pytest.raises(ValidationError):
                CloseReadingTakeaways(**missing)

    def test_zh_fields_accepted(self) -> None:
        takeaways = CloseReadingTakeaways(**_takeaways_dict())
        assert takeaways.title_zh == "蜂群衰退下的静默危机"
        assert takeaways.subtitle_zh == "野生蜂数量下滑正改变传粉格局"
        assert takeaways.tags_zh == ["生态", "农业"]

    def test_title_zh_rejects_blank(self) -> None:
        with pytest.raises(ValidationError):
            CloseReadingTakeaways(**_takeaways_dict(title_zh="   "))

    def test_tags_zh_bounds(self) -> None:
        with pytest.raises(ValidationError):
            CloseReadingTakeaways(**_takeaways_dict(tags_zh=["只有一个"]))
        with pytest.raises(ValidationError):
            CloseReadingTakeaways(**_takeaways_dict(tags_zh=["一", "二", "三", "四", "五"]))
        assert CloseReadingTakeaways(**_takeaways_dict(tags_zh=["一", "二", "三", "四"]))


@pytest.mark.anyio
class TestAssemblePayload:
    async def test_zh_headline_lands_in_payload(self) -> None:
        state = {
            "takeaways_json": _takeaways_dict(
                title_zh=" 中文主标题 ",
                subtitle_zh=" 一句话点题 ",
                tags_zh=["人工智能", "教育"],
            ),
            "pipeline_meta": {"score": 7.5},
        }
        with patch(
            "app.services.daily_reader.pipeline._next_sequence_number",
            AsyncMock(return_value=1),
        ):
            payload = await _assemble_payload(_article(), _score(), state)

        assert payload["title"] == "中文主标题"
        assert payload["original_title"] == "Bumblebees in decline, scientists warn"
        assert payload["subtitle_zh"] == "一句话点题"
        assert payload["subtitle"] == "English description"  # English description unchanged
        assert payload["tags"] == ["人工智能", "教育"]
        # score.tags demoted to pipeline_meta reference only
        assert payload["pipeline_meta"]["score"] == 7.5
        assert payload["pipeline_meta"]["score_tags"] == ["news", "science"]

    async def test_falls_back_to_english_when_takeaways_missing(self) -> None:
        with patch(
            "app.services.daily_reader.pipeline._next_sequence_number",
            AsyncMock(return_value=1),
        ):
            payload = await _assemble_payload(_article(), _score(), {})

        assert payload["title"] == "Bumblebees in decline, scientists warn"
        assert payload["original_title"] == "Bumblebees in decline, scientists warn"
        assert payload["subtitle_zh"] is None
        # fallback keeps source tags, never score.tags
        assert payload["tags"] == ["source-tag"]
        assert payload["pipeline_meta"]["score_tags"] == ["news", "science"]


def _retry_row(**overrides) -> dict:
    base = {
        "id": "daily_2026_08_20_001",
        "title": "旧中文标题",
        "original_title": "English Headline",
        "subtitle": "sub",
        "subtitle_zh": "旧副标题",
        "source": "BBC News",
        "source_url": "https://example.com/a",
        "cover_image_url": None,
        "tags": ["旧标签"],
        "difficulty": "B2",
        "read_time_minutes": 5,
        "pipeline_source": "bbc_rss",
        "pipeline_meta": {},
        "original_text": "Enough original text to retry.",
    }
    base.update(overrides)
    return base


def _retry_env(row: dict, final_state: dict) -> tuple[MagicMock, MagicMock, AsyncMock]:
    mock_conn = AsyncMock()
    mock_conn.fetchrow.return_value = row
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    graph = MagicMock()
    graph.ainvoke = AsyncMock(return_value=final_state)
    return mock_pool, graph, mock_conn


@pytest.mark.anyio
class TestRetryWorkflow:
    async def test_retry_overwrites_zh_columns(self) -> None:
        row = _retry_row()
        final_state = {
            "abort": False,
            "body_json": {"paragraphs": []},
            "highlights_json": [],
            "paragraph_notes_json": {},
            "takeaways_json": _takeaways_dict(
                title_zh="新中文标题", subtitle_zh="新副标题", tags_zh=["科技", "健康"]
            ),
            "usage_summary": None,
        }
        mock_pool, graph, mock_conn = _retry_env(row, final_state)

        with (
            patch("app.services.daily_reader.pipeline.db_connection.DB_POOL", mock_pool),
            patch(
                "app.services.daily_reader.workflow.build_daily_reader_graph",
                return_value=graph,
            ),
            patch(
                "app.services.daily_reader.pipeline._record_daily_pipeline_event",
                AsyncMock(),
            ),
        ):
            result = await run_workflow_only("daily_2026_08_20_001")

        assert result is not None and result["status"] == "retry_completed"

        # workflow prompts must see the English original headline
        input_state = graph.ainvoke.call_args[0][0]
        assert input_state["title"] == "English Headline"

        sql, *params = mock_conn.execute.call_args[0]
        for fragment in (
            "title = $6",
            "original_title = $7",
            "subtitle_zh = $8",
            "tags = $9",
        ):
            assert fragment in sql
        assert params[5] == "新中文标题"
        assert params[6] == "English Headline"
        assert params[7] == "新副标题"
        assert params[8] == ["科技", "健康"]

    async def test_retry_keeps_stored_values_when_takeaways_missing_zh(self) -> None:
        row = _retry_row()
        final_state = {
            "abort": False,
            "body_json": {"paragraphs": []},
            "highlights_json": [],
            "paragraph_notes_json": {},
            "takeaways_json": {"article_takeaway": "只有总结"},
            "usage_summary": None,
        }
        mock_pool, graph, mock_conn = _retry_env(row, final_state)

        with (
            patch("app.services.daily_reader.pipeline.db_connection.DB_POOL", mock_pool),
            patch(
                "app.services.daily_reader.workflow.build_daily_reader_graph",
                return_value=graph,
            ),
            patch(
                "app.services.daily_reader.pipeline._record_daily_pipeline_event",
                AsyncMock(),
            ),
        ):
            await run_workflow_only("daily_2026_08_20_001")

        sql, *params = mock_conn.execute.call_args[0]
        # keep stored Chinese headline / tags; original_title stays English
        assert params[5] == "旧中文标题"
        assert params[6] == "English Headline"
        assert params[7] is None
        assert params[8] == ["旧标签"]

    async def test_retry_old_row_uses_title_as_english_original(self) -> None:
        # pre-A-3 row: original_title missing entirely
        row = _retry_row(
            title="English Legacy Headline",
            original_title=None,
            subtitle_zh=None,
        )
        final_state = {
            "abort": False,
            "body_json": {"paragraphs": []},
            "highlights_json": [],
            "paragraph_notes_json": {},
            "takeaways_json": _takeaways_dict(),
            "usage_summary": None,
        }
        mock_pool, graph, mock_conn = _retry_env(row, final_state)

        with (
            patch("app.services.daily_reader.pipeline.db_connection.DB_POOL", mock_pool),
            patch(
                "app.services.daily_reader.workflow.build_daily_reader_graph",
                return_value=graph,
            ),
            patch(
                "app.services.daily_reader.pipeline._record_daily_pipeline_event",
                AsyncMock(),
            ),
        ):
            await run_workflow_only("daily_2026_08_20_001")

        input_state = graph.ainvoke.call_args[0][0]
        assert input_state["title"] == "English Legacy Headline"

        sql, *params = mock_conn.execute.call_args[0]
        assert params[5] == _takeaways_dict()["title_zh"]
        assert params[6] == "English Legacy Headline"


def test_baseline_declares_zh_title_columns() -> None:
    assert "    original_title text," in BASELINE_SQL
    assert "    subtitle_zh text," in BASELINE_SQL
    assert "A-3 起存中文主标题" in BASELINE_SQL


async def test_alter_script_backfills_reentrant_and_rolls_back() -> None:
    from tests.test_reader_orchestration_schema_baseline import DATABASE_URL

    schema_name = f"test_dr_zh_title_{uuid4().hex}"
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await conn.execute(f'SET search_path TO "{schema_name}", public')
        await conn.execute(
            """
            CREATE TABLE daily_readers (
                id text PRIMARY KEY,
                title text NOT NULL,
                subtitle text,
                tags jsonb DEFAULT '[]'::jsonb NOT NULL,
                status text NOT NULL
            )
            """
        )
        await conn.execute(
            """
            INSERT INTO daily_readers (id, title, status) VALUES
            ('daily_old_1', 'English Old Headline', 'published'),
            ('daily_old_2', 'Another English Headline', 'draft')
            """
        )

        await conn.execute(UP_SQL)

        old_rows = await conn.fetch(
            "SELECT id, title, original_title, subtitle_zh FROM daily_readers ORDER BY id"
        )
        assert [r["original_title"] for r in old_rows] == [
            "English Old Headline",
            "Another English Headline",
        ]
        assert all(r["subtitle_zh"] is None for r in old_rows)

        # re-run on a volume that already has a new-style row: the Chinese
        # title must survive and its original_title must not be clobbered.
        await conn.execute(
            """
            INSERT INTO daily_readers (id, title, status, original_title, subtitle_zh)
            VALUES ('daily_new_1', '中文新标题', 'draft', 'English New Headline', '一句话点题')
            """
        )
        await conn.execute(UP_SQL)

        rows = {
            r["id"]: r
            for r in await conn.fetch(
                "SELECT id, title, original_title, subtitle_zh FROM daily_readers"
            )
        }
        assert rows["daily_new_1"]["title"] == "中文新标题"
        assert rows["daily_new_1"]["original_title"] == "English New Headline"
        assert rows["daily_new_1"]["subtitle_zh"] == "一句话点题"
        assert rows["daily_old_1"]["original_title"] == "English Old Headline"

        await conn.execute(DOWN_SQL)

        columns = await conn.fetch(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = $1 AND table_name = 'daily_readers'
            """,
            schema_name,
        )
        names = {row["column_name"] for row in columns}
        assert "original_title" not in names
        assert "subtitle_zh" not in names
        assert "title" in names
        title = await conn.fetchval(
            "SELECT title FROM daily_readers WHERE id = 'daily_new_1'"
        )
        assert title == "中文新标题"
    finally:
        await conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await conn.close()


def test_daily_reader_service_imports_clean() -> None:
    # guard: mapper module must keep exposing the response schemas used by
    # the routes (cheap smoke that the A-3 field additions didn't break it)
    from app.schemas.daily_reader import DailyReaderArticleResponse, DailyReaderListItem

    article = DailyReaderArticleResponse(
        id="a", title="t", source="s", source_url="u",
        publish_date=__import__("datetime").date(2026, 8, 20),
        difficulty="B2", read_time_minutes=3,
    )
    assert article.original_title is None
    assert article.subtitle_zh is None
    item = DailyReaderListItem(
        id="a", title="t", source="s",
        publish_date=__import__("datetime").date(2026, 8, 20),
        difficulty="B2", read_time_minutes=3,
    )
    assert item.original_title is None
    assert item.subtitle_zh is None


@pytest.fixture
def anyio_backend():
    return "asyncio"
