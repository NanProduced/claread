"""B-3: publish audit, retry-to-draft, pipeline alerts, security placeholder cleanup."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.daily_reader.discovery import DiscoveredArticle
from app.services.daily_reader.pipeline import (
    PipelineResult,
    _assemble_payload,
    _store_daily_reader,
    collect_pipeline_alert_reasons,
    emit_pipeline_alerts,
    run_workflow_only,
)
from app.services.daily_reader.scoring import ArticleScore

client = TestClient(app)
ADMIN_HEADERS = {"x-admin-api-key": "secret"}


@pytest.fixture
def admin_settings(monkeypatch):
    monkeypatch.setattr(
        "app.api.routes.daily_reader_admin.get_settings",
        lambda: SimpleNamespace(daily_reader_admin_api_key="secret"),
    )


def _mock_pool(mock_pool, *, execute_result="UPDATE 1"):
    mock_conn = AsyncMock()
    mock_conn.execute.return_value = execute_result
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_conn


class TestPublishRequiresOperator:
    def test_publish_without_operator_is_422(self, admin_settings):
        response = client.post(
            "/daily-reader/admin/publish",
            json={"id": "daily_2026_08_19_001"},
            headers=ADMIN_HEADERS,
        )
        assert response.status_code == 422
        assert "operator" in response.text

    @pytest.mark.parametrize("operator", ["", "   "])
    def test_publish_blank_operator_is_422(self, admin_settings, operator):
        response = client.post(
            "/daily-reader/admin/publish",
            json={"id": "daily_2026_08_19_001", "operator": operator},
            headers=ADMIN_HEADERS,
        )
        assert response.status_code == 422

    def test_unpublish_without_operator_is_422(self, admin_settings):
        response = client.post(
            "/daily-reader/admin/unpublish",
            json={"id": "daily_2026_08_19_001"},
            headers=ADMIN_HEADERS,
        )
        assert response.status_code == 422
        assert "operator" in response.text

    @patch("app.services.daily_reader.service.db_connection.DB_POOL")
    def test_publish_writes_audit_fields(self, mock_pool, admin_settings):
        mock_conn = _mock_pool(mock_pool)
        response = client.post(
            "/daily-reader/admin/publish",
            json={"id": "daily_2026_08_19_001", "operator": "alice"},
            headers=ADMIN_HEADERS,
        )
        assert response.status_code == 200
        assert response.json()["status"] == "published"
        sql, article_id, operator = mock_conn.execute.call_args[0]
        assert article_id == "daily_2026_08_19_001"
        assert operator == "alice"
        assert "review_status = 'approved'" in sql
        assert "reviewed_by = $2" in sql
        assert "reviewed_at = NOW()" in sql

    @patch("app.services.daily_reader.service.db_connection.DB_POOL")
    def test_unpublish_writes_operator(self, mock_pool, admin_settings):
        mock_conn = _mock_pool(mock_pool)
        response = client.post(
            "/daily-reader/admin/unpublish",
            json={"id": "daily_2026_08_19_001", "operator": "bob"},
            headers=ADMIN_HEADERS,
        )
        assert response.status_code == 200
        sql, article_id, operator = mock_conn.execute.call_args[0]
        assert article_id == "daily_2026_08_19_001"
        assert operator == "bob"
        assert "reviewed_by = $2" in sql
        assert "status = 'draft'" in sql


@pytest.mark.anyio
async def test_retry_resets_status_to_draft():
    mock_conn = AsyncMock()
    mock_conn.fetchrow.return_value = {
        "id": "daily_2026_08_19_001",
        "title": "Published piece",
        "subtitle": "sub",
        "source": "BBC News",
        "source_url": "https://example.com/a",
        "cover_image_url": None,
        "tags": ["news"],
        "difficulty": "B2",
        "read_time_minutes": 5,
        "pipeline_source": "bbc_rss",
        "pipeline_meta": {},
        "original_text": "Enough original text to retry.",
    }
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    graph = MagicMock()
    graph.ainvoke = AsyncMock(
        return_value={
            "abort": False,
            "body_json": {"paragraphs": []},
            "highlights_json": [],
            "paragraph_notes_json": {},
            "takeaways_json": {},
            "usage_summary": None,
        }
    )

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
        result = await run_workflow_only("daily_2026_08_19_001")

    assert result is not None
    assert result["status"] == "retry_completed"
    sql = mock_conn.execute.call_args[0][0]
    assert "status = 'draft'" in sql
    assert "published_at = NULL" in sql
    assert "review_status = 'pending'" in sql


class TestPipelineAlerts:
    def test_zero_output_reason(self):
        reasons = collect_pipeline_alert_reasons(PipelineResult())
        assert reasons == ["zero_output"]

    def test_workflow_failure_reason(self):
        result = PipelineResult(
            articles=[{"id": "a"}],
            errors=["Workflow failed for 'X': boom"],
        )
        assert collect_pipeline_alert_reasons(result) == ["workflow_failure"]

    def test_all_candidates_filtered_reason(self):
        result = PipelineResult(candidates_found=8, candidates_scored=0)
        reasons = collect_pipeline_alert_reasons(result)
        assert "zero_output" in reasons
        assert "all_candidates_filtered" in reasons

    def test_healthy_run_has_no_alert(self):
        result = PipelineResult(
            articles=[{"id": "a"}],
            candidates_found=5,
            candidates_scored=3,
        )
        assert collect_pipeline_alert_reasons(result) == []

    @pytest.mark.anyio
    async def test_zero_output_posts_webhook_and_logs(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.daily_reader.pipeline.get_settings",
            lambda: SimpleNamespace(
                daily_reader_alert_webhook_url="https://hooks.example/alert"
            ),
        )
        mock_client = AsyncMock()
        mock_client.post = AsyncMock()
        with (
            patch("app.services.daily_reader.pipeline.httpx.AsyncClient") as client_cls,
            patch("app.services.daily_reader.pipeline.logger") as mock_logger,
        ):
            client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            await emit_pipeline_alerts(PipelineResult(), run_id="pr_zero")

        mock_logger.error.assert_called()
        log_args = mock_logger.error.call_args.args
        assert log_args[1] == "pr_zero"
        assert "zero_output" in log_args[2]
        mock_client.post.assert_awaited_once()
        posted_url = mock_client.post.await_args.args[0]
        posted_json = mock_client.post.await_args.kwargs["json"]
        assert posted_url == "https://hooks.example/alert"
        assert posted_json["run_id"] == "pr_zero"
        assert "zero_output" in posted_json["reasons"]

    @pytest.mark.anyio
    async def test_alert_without_webhook_logs_only(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.daily_reader.pipeline.get_settings",
            lambda: SimpleNamespace(daily_reader_alert_webhook_url=""),
        )
        with (
            patch("app.services.daily_reader.pipeline.httpx.AsyncClient") as client_cls,
            patch("app.services.daily_reader.pipeline.logger") as mock_logger,
        ):
            await emit_pipeline_alerts(PipelineResult(), run_id="pr_log_only")
        client_cls.assert_not_called()
        mock_logger.error.assert_called()
        log_args = mock_logger.error.call_args.args
        assert log_args[1] == "pr_log_only"
        assert "zero_output" in log_args[2]


@pytest.mark.anyio
async def test_assemble_payload_omits_content_sec_placeholder():
    article = DiscoveredArticle(
        title="A",
        url="https://example.com/a",
        source="BBC News",
        description="sub",
        text="word " * 50,
        word_count=50,
    )
    score = ArticleScore(score=8.0, difficulty="B2", tags=["news"])
    with patch(
        "app.services.daily_reader.pipeline._next_sequence_number",
        AsyncMock(return_value=1),
    ):
        payload = await _assemble_payload(article, score, {})
    assert "content_sec_check" not in payload
    assert payload["status"] == "draft"


def test_store_sql_does_not_write_content_sec_check():
    import inspect

    source = inspect.getsource(_store_daily_reader)
    assert "content_sec_check" not in source


def test_content_security_module_removed():
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "app/services/daily_reader/content_security.py"
    assert not path.exists()


@pytest.fixture
def anyio_backend():
    return "asyncio"
