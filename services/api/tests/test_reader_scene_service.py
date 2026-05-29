from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.services import reader_scene as reader_scene_svc


@pytest.mark.anyio
async def test_get_reader_scene_by_id_merges_supplements() -> None:
    record_id = uuid4()
    user_id = uuid4()
    record = {
        "id": record_id,
        "client_record_id": "client-1",
        "title": "Reader title",
        "source_type": "user_input",
        "source_text": "Hello world.",
        "request_payload_json": {
            "reading_goal": "daily_reading",
            "reading_variant": "intermediate_reading",
            "source_type": "user_input",
            "url": "https://example.com/article",
        },
        "reading_goal": "daily_reading",
        "reading_variant": "intermediate_reading",
        "analysis_status": "ready",
        "user_facing_state": "normal",
        "workflow_version": "3.0.0",
        "schema_version": "3.0.0",
        "created_at": "2026-05-29T00:00:00Z",
        "updated_at": "2026-05-29T00:00:00Z",
        "render_scene_json": {
            "schema_version": "3.0.0",
            "request": {
                "request_id": "req-1",
                "source_type": "user_input",
                "reading_goal": "daily_reading",
                "reading_variant": "intermediate_reading",
                "profile_id": "daily",
            },
            "article": {
                "paragraphs": [{"paragraph_id": "p0", "sentence_ids": ["s0"]}],
                "sentences": [{"sentence_id": "s0", "paragraph_id": "p0", "text": "Hello world."}],
            },
            "user_facing_state": "normal",
            "translations": [],
            "inline_marks": [],
            "sentence_entries": [],
            "warnings": [],
        },
    }
    supplement = {
        "id": str(uuid4()),
        "supplement_type": "grammar_note",
        "sentence_id": "s0",
        "target_key": "sentence:s0",
        "paragraph_id": "p0",
        "title": "AI 补充语法旁注",
        "content_md": "supplement content",
        "created_from_turn_run_id": "turn-1",
        "schema_version": "reader-ask-supplement-v1",
    }

    with (
        patch("app.services.reader_scene.records_svc.get_record_by_id", AsyncMock(return_value=record)),
        patch("app.services.reader_scene.get_record_supplements", AsyncMock(return_value=[supplement])),
    ):
        response = await reader_scene_svc.get_reader_scene_by_id(user_id, record_id)

    assert response.record_meta.request_payload_json["url"] == "https://example.com/article"
    assert response.view_meta.supplements_merged is True
    assert response.view_meta.fallback_mode == "none"
    assert len(response.reader_scene.sentence_entries) == 1
    assert response.reader_scene.sentence_entries[0]["source_kind"] == "ask_supplement"


@pytest.mark.anyio
async def test_get_reader_scene_by_client_id_rebuilds_article_when_scene_missing() -> None:
    user_id = uuid4()
    record = {
        "id": uuid4(),
        "client_record_id": "client-2",
        "title": None,
        "source_type": "user_input",
        "source_text": "First sentence. Second sentence.",
        "request_payload_json": {
            "reading_goal": "daily_reading",
            "reading_variant": "intermediate_reading",
            "source_type": "user_input",
        },
        "reading_goal": "daily_reading",
        "reading_variant": "intermediate_reading",
        "analysis_status": "ready",
        "user_facing_state": "normal",
        "workflow_version": None,
        "schema_version": None,
        "created_at": "2026-05-29T00:00:00Z",
        "updated_at": "2026-05-29T00:00:00Z",
        "render_scene_json": {},
    }

    with (
        patch("app.services.reader_scene.records_svc.get_record_by_client_id", AsyncMock(return_value=record)),
        patch("app.services.reader_scene.get_record_supplements", AsyncMock(return_value=[])),
    ):
        response = await reader_scene_svc.get_reader_scene_by_client_id(user_id, "client-2")

    assert response.view_meta.data_source == "source_text_fallback"
    assert response.view_meta.fallback_mode == "scene_missing"
    assert response.view_meta.supplements_merged is False
    assert response.reader_scene.article.sentences[0].text == "First sentence."


@pytest.fixture
def anyio_backend():
    return "asyncio"
