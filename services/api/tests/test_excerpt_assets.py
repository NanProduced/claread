from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.user_assets.records import delete_record

client = TestClient(app)

USER_ID = "00000000-0000-0000-0000-000000000001"
AUTH_HEADERS = {"Authorization": "Bearer test_token"}


def _mock_auth():
    return patch(
        "app.services.auth.dependencies.validate_session",
        new_callable=AsyncMock,
        return_value=type(
            "SessionInfo",
            (),
            {
                "user_id": UUID(USER_ID),
                "session_id": uuid4(),
            },
        )(),
    )


def _mock_db_pool():
    mock_conn = AsyncMock()
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    mock_tx = MagicMock()
    mock_tx.__aenter__ = AsyncMock(return_value=None)
    mock_tx.__aexit__ = AsyncMock(return_value=False)
    mock_conn.transaction = MagicMock(return_value=mock_tx)
    return mock_pool, mock_conn


def _favorite_row(**overrides):
    now = datetime(2026, 5, 17, 9, 0, tzinfo=UTC)
    record_id = uuid4()
    row = {
        "id": uuid4(),
        "target_type": "sentence",
        "target_key": f"record:{record_id}:sentence:s1",
        "analysis_record_id": record_id,
        "payload_json": {
            "client_record_id": "web-rec-1",
            "sentence_id": "s1",
            "text": "Hello excerpt.",
            "translation": "你好摘录。",
            "article_title": "Hello Article",
            "article_title_zh": "你好文章",
        },
        "note": None,
        "created_at": now,
        "updated_at": now,
    }
    row.update(overrides)
    return row


def _annotation_row(**overrides):
    now = datetime(2026, 5, 17, 10, 0, tzinfo=UTC)
    record_id = uuid4()
    row = {
        "id": uuid4(),
        "analysis_record_id": record_id,
        "annotation_type": "note",
        "anchor_type": "sentence",
        "target_key": f"record:{record_id}:sentence:s1",
        "paragraph_id": "p1",
        "sentence_id": "s1",
        "selected_text": "Hello excerpt.",
        "start_offset": None,
        "end_offset": None,
        "text_hash": None,
        "color": "soft_blue",
        "note": "Need revisit.",
        "payload_json": {
            "client_record_id": "web-rec-1",
            "translation": "你好摘录。",
        },
        "created_at": now,
        "updated_at": now,
    }
    row.update(overrides)
    return row


def _record_row(record_id: UUID, render_scene_json: dict | None = None, **overrides):
    row = {
        "id": record_id,
        "client_record_id": "web-rec-1",
        "title": "Hello Article",
        "source_text": "Hello excerpt.\nSecond sentence.",
        "render_scene_json": render_scene_json if render_scene_json is not None else {
            "translations": [
                {"sentence_id": "s1", "translation_zh": "你好摘录。"},
            ],
            "sentence_entries": [
                {
                    "id": "entry-1",
                    "sentence_id": "s1",
                    "entry_type": "grammar_note",
                    "title": "句法提示",
                    "content": "这里是语法说明。",
                }
            ],
        },
    }
    row.update(overrides)
    return row


class TestExcerptAssetsRoute:
    @_mock_auth()
    @patch("app.services.excerpt_assets.db_connection.DB_POOL")
    def test_aggregates_favorite_and_note_without_counting_note_as_highlight(self, mock_pool, mock_auth):
        pool, conn = _mock_db_pool()
        mock_pool.acquire = pool.acquire
        record_id = uuid4()
        favorite = _favorite_row(analysis_record_id=record_id, target_key=f"record:{record_id}:sentence:s1")
        annotation = _annotation_row(
            analysis_record_id=record_id,
            target_key=f"record:{record_id}:sentence:s1",
        )
        conn.fetch.side_effect = [
            [favorite],
            [annotation],
            [_record_row(record_id)],
        ]

        response = client.get("/excerpt-assets", headers=AUTH_HEADERS)
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_assets"] == 1
        assert payload["total_groups"] == 1
        group = payload["groups"][0]
        assert group["record_id"] == str(record_id)
        assert group["client_record_id"] == "web-rec-1"
        assert group["title"] == "你好文章"
        item = group["items"][0]
        assert item["is_favorited"] is True
        assert item["is_highlighted"] is False
        assert item["is_noted"] is True
        assert item["note"] == "Need revisit."
        assert item["translation"] == "你好摘录。"
        assert item["annotation_type"] == "note"
        assert item["annotation_color"] == "soft_blue"
        assert item["insights"][0]["type"] == "grammar"

    @_mock_auth()
    @patch("app.services.excerpt_assets.db_connection.DB_POOL")
    def test_filters_by_asset_state_anchor_type_and_record(self, mock_pool, mock_auth):
        pool, conn = _mock_db_pool()
        mock_pool.acquire = pool.acquire
        record_id = uuid4()
        other_record_id = uuid4()
        favorite = _favorite_row(
            analysis_record_id=record_id,
            target_type="text_range",
            target_key=f"record:{record_id}:range:s1:0:5:hash1",
            payload_json={
                "client_record_id": "web-rec-1",
                "sentence_id": "s1",
                "selected_text": "Hello",
                "start_offset": 0,
                "end_offset": 5,
                "text_hash": "hash1",
                "translation": "你好",
            },
        )
        annotation = _annotation_row(
            analysis_record_id=other_record_id,
            target_key=f"record:{other_record_id}:sentence:s2",
            sentence_id="s2",
            selected_text="Another sentence.",
            payload_json={"client_record_id": "web-rec-2"},
            note=None,
            annotation_type="highlight",
            color="warm_yellow",
        )
        conn.fetch.side_effect = [
            [favorite],
            [annotation],
            [_record_row(record_id), _record_row(other_record_id, title="Other Article", client_record_id="web-rec-2")],
        ]

        response = client.get(
            f"/excerpt-assets?asset_state=favorite&anchor_type=text_range&record_id={record_id}",
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_assets"] == 1
        group = payload["groups"][0]
        assert group["record_id"] == str(record_id)
        item = group["items"][0]
        assert item["anchor_type"] == "text_range"
        assert item["is_favorited"] is True
        assert item["start_offset"] == 0
        assert item["end_offset"] == 5

    @_mock_auth()
    @patch("app.services.excerpt_assets.db_connection.DB_POOL")
    def test_uses_review_assets_fallback_for_insight_filter(self, mock_pool, mock_auth):
        pool, conn = _mock_db_pool()
        mock_pool.acquire = pool.acquire
        record_id = uuid4()
        favorite = _favorite_row(
            analysis_record_id=record_id,
            payload_json={
                "client_record_id": "web-rec-1",
                "sentence_id": "s1",
                "text": "Hello excerpt.",
                "review_assets": [
                    {
                        "id": "rv-1",
                        "type": "sentence_analysis",
                        "title": "句子拆解",
                        "summary": "这是回退复习摘要。",
                    }
                ],
            },
        )
        conn.fetch.side_effect = [
            [favorite],
            [],
            [_record_row(record_id, render_scene_json={})],
        ]

        response = client.get("/excerpt-assets?asset_state=insight", headers=AUTH_HEADERS)
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_assets"] == 1
        item = payload["groups"][0]["items"][0]
        assert item["insights"][0]["type"] == "sentence"
        assert item["insights"][0]["detail"] == "这是回退复习摘要。"


class TestDeleteRecordCascadesAnnotations:
    @pytest.mark.anyio
    @patch("app.services.user_assets.records.db_connection.DB_POOL")
    async def test_delete_record_soft_deletes_annotations_alongside_favorites(self, mock_pool):
        pool, conn = _mock_db_pool()
        mock_pool.acquire = pool.acquire
        record_id = uuid4()
        user_id = uuid4()
        conn.fetchrow.return_value = {"deleted_at": None}
        conn.execute.return_value = "UPDATE 1"

        status = await delete_record(user_id=user_id, record_id=record_id)

        assert status == "deleted"
        sql_calls = [call.args[0] for call in conn.execute.await_args_list]
        assert any("UPDATE favorite_records" in sql for sql in sql_calls)
        assert any("UPDATE user_annotations" in sql for sql in sql_calls)


@pytest.fixture
def anyio_backend():
    return "asyncio"
