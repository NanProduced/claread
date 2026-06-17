from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def _fake_settings() -> SimpleNamespace:
    return SimpleNamespace(
        zilliz_uri="https://zilliz.example.com",
        zilliz_token="test-token",
        zilliz_collection_grammar_note="grammar_note_examples",
        zilliz_collection_sentence_analysis="sentence_analysis_examples",
    )


def _fake_record(output_type: str = "grammar_note") -> dict:
    return {
        "example_id": "grammar-gaokao-001",
        "variant": "default",
        "output_type": output_type,
        "tags": ["subjunctive"],
        "retrieval_text": "example retrieval text",
        "label": "example",
        "source_sentence": "source sentence",
        "output_fragment": "{}",
        "quality_score": 0.9,
        "approved": True,
    }


@pytest.mark.anyio
async def test_ingest_grammar_seed_dry_run_uses_resolved_embedding_dimension(monkeypatch):
    import scripts.ingest_grammar_seed as script

    captured: list[int] = []

    def fake_map(record: dict, vector: list[float]) -> dict:
        captured.append(len(vector))
        return {
            "example_id": record["example_id"],
            "reading_variant": record["variant"],
            "label": record["label"],
        }

    monkeypatch.setattr(script, "_load_seed", lambda path: [_fake_record()])
    monkeypatch.setattr(script, "_map_record_to_zilliz", fake_map)
    monkeypatch.setattr("app.config.settings.get_settings", lambda: _fake_settings())
    monkeypatch.setattr(
        "app.infra.bailian_embedding.resolve_embedding_config",
        lambda: ("text-embedding-v5", 1536, ""),
    )

    await script._run_ingestion(
        seed_file=Path("unused.jsonl"),
        batch_size=10,
        dry_run=True,
        force_recreate=False,
    )

    assert captured == [1536]


@pytest.mark.anyio
async def test_ingest_grammar_seed_live_path_uses_resolved_embedding_config(monkeypatch):
    import scripts.ingest_grammar_seed as script

    init_zilliz = AsyncMock()
    create_collection = AsyncMock()
    drop_collection = AsyncMock()
    query = AsyncMock(return_value=[])
    insert = AsyncMock()
    close_zilliz = AsyncMock()
    embed_texts = AsyncMock(return_value=[[0.1, 0.2, 0.3]])

    monkeypatch.setattr(script, "_load_seed", lambda path: [_fake_record()])
    monkeypatch.setattr("app.config.settings.get_settings", lambda: _fake_settings())
    monkeypatch.setattr(
        "app.infra.bailian_embedding.resolve_embedding_config",
        lambda: ("text-embedding-v5", 1536, "registry-key"),
    )
    monkeypatch.setattr("app.infra.bailian_embedding.embed_texts", embed_texts)
    monkeypatch.setattr("app.infra.zilliz_client.init_zilliz", init_zilliz)
    monkeypatch.setattr("app.infra.zilliz_client.zilliz_create_collection", create_collection)
    monkeypatch.setattr("app.infra.zilliz_client.zilliz_drop_collection", drop_collection)
    monkeypatch.setattr("app.infra.zilliz_client.zilliz_query", query)
    monkeypatch.setattr("app.infra.zilliz_client.zilliz_insert", insert)
    monkeypatch.setattr("app.infra.zilliz_client.close_zilliz", close_zilliz)

    await script._run_ingestion(
        seed_file=Path("unused.jsonl"),
        batch_size=10,
        dry_run=False,
        force_recreate=False,
    )

    create_collection.assert_awaited_once_with("grammar_note_examples", dimension=1536)
    embed_texts.assert_awaited_once_with(
        ["example retrieval text"],
        model="text-embedding-v5",
        dimension=1536,
    )
    drop_collection.assert_not_awaited()
    close_zilliz.assert_awaited_once()
