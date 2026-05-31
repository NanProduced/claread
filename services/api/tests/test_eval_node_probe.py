from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import eval_debug
from app.config.settings import Settings
from app.eval_adapter import node_probe
from app.eval_adapter.schemas import (
    ArticleAnalysisNodeProbeRequest,
    ArticleAnalysisNodeProbeResult,
    PromptIdentity,
    RequestSnapshot,
    SchemaIdentity,
    WorkflowIdentity,
)
from app.schemas.internal.analysis import GrammarNote, SpanRef
from app.schemas.internal.drafts import GrammarDraft, TranslationDraft, VocabularyDraft


def _settings() -> Settings:
    return Settings(
        default_model_profile="",
        annotation_model_profile="",
        model_profiles_json=json.dumps(
            {
                "eval-profile": {
                    "provider": "openai_compatible",
                    "model_name": "eval-model",
                    "base_url": "https://example.invalid/v1",
                    "api_key": "secret-key",
                    "model_settings": {"temperature": 0.2},
                }
            }
        ),
    )


def _admin_settings() -> Settings:
    settings = _settings()
    settings.daily_reader_admin_api_key = "admin-key"
    return settings


def _eval_admin_settings() -> Settings:
    settings = _admin_settings()
    settings.eval_admin_api_key = "eval-key"
    return settings


def _fake_probe_result() -> ArticleAnalysisNodeProbeResult:
    return ArticleAnalysisNodeProbeResult(
        status="succeeded",
        request_snapshot=RequestSnapshot(
            request_id="eval:probe",
            source_text_hash="abc",
            source_char_count=9,
            reading_goal="daily_reading",
            reading_variant="intermediate_reading",
            source_type="user_input",
            extended=False,
            rag_mode="off",
            trace_scope="off",
        ),
        workflow_identity=WorkflowIdentity(
            workflow_name="article_analysis.node_probe",
            workflow_version="1.0.0",
            topology_mode="learning",
        ),
        schema_identity=SchemaIdentity(
            schema_version="article-analysis-node-probe-v1",
            topology_mode="learning",
        ),
        prompt_identity=PromptIdentity(prompt_version="test"),
        node_name="grammar",
    )


@pytest.mark.anyio
async def test_grammar_node_probe_dry_run_builds_prompt_without_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(node_probe, "get_settings", _settings)
    run_mock = AsyncMock(side_effect=AssertionError("LLM should not run"))
    monkeypatch.setattr(node_probe, "run_grammar_agent", run_mock)

    result = await node_probe.run_article_analysis_node_probe(
        ArticleAnalysisNodeProbeRequest(
            text="Although the plan looked simple, it required careful coordination.",
            model_selection={"default_profile": "eval-profile"},
            prompt_variant_id="no-few-shot",
            prompt_override={
                "variant_id": "no-few-shot",
                "few_shot_mode": "off",
            },
            dry_run=True,
        )
    )

    assert result.status == "succeeded"
    assert result.node_name == "grammar"
    assert result.node_output is None
    assert result.prompt_preview
    assert "Although the plan looked simple" in result.prompt_preview
    assert result.agent_instructions
    assert result.prepared_sentences[0]["sentence_id"] == "s1"
    assert result.example_summary is not None
    assert result.example_summary["selection_mode"] == "off"
    assert result.example_summary["example_count"] == 0
    assert result.prompt_identity.prompt_variant_id == "no-few-shot"
    run_mock.assert_not_awaited()


@pytest.mark.anyio
async def test_grammar_node_probe_variant_examples_are_visible_in_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(node_probe, "get_settings", _settings)

    result = await node_probe.run_article_analysis_node_probe(
        ArticleAnalysisNodeProbeRequest(
            text="The result was surprising because the dataset had seemed stable.",
            model_selection={"default_profile": "eval-profile"},
            prompt_variant_id="variant-examples",
            prompt_override={
                "variant_id": "variant-examples",
                "few_shot_mode": "variant",
                "examples": {
                    "grammar": {
                        "intermediate_reading": [
                            {
                                "example_type": "grammar",
                                "sentence_text": "Variant example sentence.",
                                "output_fragment": '{"label":"variant grammar note"}',
                            }
                        ]
                    }
                },
            },
            dry_run=True,
        )
    )

    assert result.status == "succeeded"
    assert result.example_summary is not None
    assert result.example_summary["selection_mode"] == "variant"
    assert result.example_summary["example_count"] == 1
    assert result.prompt_preview is not None
    assert "Variant example sentence." in result.prompt_preview


@pytest.mark.anyio
async def test_grammar_node_probe_returns_raw_grammar_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(node_probe, "get_settings", _settings)
    draft = GrammarDraft(
        grammar_notes=[
            GrammarNote(
                sentence_id="s1",
                spans=[SpanRef(text="Although", role="subordinator")],
                label="Adverbial clause",
                note_zh="Although 引导让步状语从句。",
            )
        ],
        sentence_analyses=[],
    )
    run_mock = AsyncMock(return_value=SimpleNamespace(output=draft))
    monkeypatch.setattr(node_probe, "run_grammar_agent", run_mock)

    result = await node_probe.run_article_analysis_node_probe(
        ArticleAnalysisNodeProbeRequest(
            text="Although the plan looked simple, it required careful coordination.",
            model_selection={"default_profile": "eval-profile"},
            dry_run=False,
        )
    )

    assert result.status == "succeeded"
    assert result.node_output is not None
    assert result.node_output["grammar_notes"][0]["label"] == "Adverbial clause"
    assert result.runtime_summary is not None
    assert result.runtime_summary["latency_ms"] >= 0
    run_mock.assert_awaited_once()


@pytest.mark.anyio
async def test_node_probe_rejects_academic_topology(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(node_probe, "get_settings", _settings)

    result = await node_probe.run_article_analysis_node_probe(
        ArticleAnalysisNodeProbeRequest(
            text="This paper proposes a compact evaluation protocol for discourse analysis.",
            reading_goal="academic",
            reading_variant="academic_general",
            model_selection={"default_profile": "eval-profile"},
            dry_run=True,
        )
    )

    assert result.status == "failed"
    assert result.error is not None
    assert result.error.code == "ValueError"
    assert "learning topology" in result.error.message


def test_eval_node_probe_route_is_admin_key_protected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(eval_debug, "get_settings", _eval_admin_settings)
    run_mock = AsyncMock(return_value=_fake_probe_result())
    monkeypatch.setattr(eval_debug, "run_article_analysis_node_probe", run_mock)

    app = FastAPI()
    app.include_router(eval_debug.router)
    client = TestClient(app)

    denied = client.post(
        "/eval/article-analysis/node-probe",
        headers={"x-admin-api-key": "wrong"},
        json={"text": "Sentence."},
    )
    denied_with_daily_key = client.post(
        "/eval/article-analysis/node-probe",
        headers={"x-admin-api-key": "admin-key"},
        json={"text": "Sentence.", "dry_run": True},
    )
    allowed = client.post(
        "/eval/article-analysis/node-probe",
        headers={"x-admin-api-key": "eval-key"},
        json={"text": "Sentence.", "dry_run": True},
    )

    assert denied.status_code == 401
    assert denied_with_daily_key.status_code == 401
    assert allowed.status_code == 200
    assert allowed.json()["node_name"] == "grammar"
    run_mock.assert_awaited_once()


def test_eval_node_probe_route_prefers_eval_admin_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(eval_debug, "get_settings", _eval_admin_settings)
    run_mock = AsyncMock(return_value=_fake_probe_result())
    monkeypatch.setattr(eval_debug, "run_article_analysis_node_probe", run_mock)

    app = FastAPI()
    app.include_router(eval_debug.router)
    client = TestClient(app)

    denied = client.post(
        "/eval/article-analysis/node-probe",
        headers={"x-admin-api-key": "admin-key"},
        json={"text": "Sentence."},
    )
    allowed = client.post(
        "/eval/article-analysis/node-probe",
        headers={"x-admin-api-key": "eval-key"},
        json={"text": "Sentence.", "dry_run": True},
    )

    assert denied.status_code == 401
    assert allowed.status_code == 200
    run_mock.assert_awaited_once()


@pytest.mark.anyio
async def test_vocabulary_node_probe_dry_run_builds_prompt_without_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(node_probe, "get_settings", _settings)
    vocab_run_mock = AsyncMock(side_effect=AssertionError("LLM should not run"))
    monkeypatch.setattr(node_probe, "run_vocabulary_agent", vocab_run_mock)

    result = await node_probe.run_article_analysis_node_probe(
        ArticleAnalysisNodeProbeRequest(
            text="Although the plan looked simple, it required careful coordination.",
            node_name="vocabulary",
            model_selection={"default_profile": "eval-profile"},
            dry_run=True,
        )
    )

    assert result.status == "succeeded"
    assert result.node_name == "vocabulary"
    assert result.node_output is None
    assert result.prompt_preview
    assert "Although the plan looked simple" in result.prompt_preview
    assert result.agent_instructions
    assert result.prepared_sentences[0]["sentence_id"] == "s1"
    assert result.example_summary is not None
    vocab_run_mock.assert_not_awaited()


@pytest.mark.anyio
async def test_translation_node_probe_dry_run_builds_prompt_without_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(node_probe, "get_settings", _settings)
    translation_run_mock = AsyncMock(side_effect=AssertionError("LLM should not run"))
    monkeypatch.setattr(node_probe, "run_translation_agent", translation_run_mock)

    result = await node_probe.run_article_analysis_node_probe(
        ArticleAnalysisNodeProbeRequest(
            text="Although the plan looked simple, it required careful coordination.",
            node_name="translation",
            model_selection={"default_profile": "eval-profile"},
            dry_run=True,
        )
    )

    assert result.status == "succeeded"
    assert result.node_name == "translation"
    assert result.node_output is None
    assert result.prompt_preview
    assert "Although the plan looked simple" in result.prompt_preview
    assert result.agent_instructions
    assert result.prepared_sentences[0]["sentence_id"] == "s1"
    assert result.example_summary is not None
    translation_run_mock.assert_not_awaited()


@pytest.mark.anyio
async def test_vocabulary_node_probe_variant_examples_are_visible_in_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(node_probe, "get_settings", _settings)

    result = await node_probe.run_article_analysis_node_probe(
        ArticleAnalysisNodeProbeRequest(
            text="Although the plan looked simple, it required careful coordination.",
            node_name="vocabulary",
            model_selection={"default_profile": "eval-profile"},
            prompt_variant_id="test-variant",
            prompt_override={
                "variant_id": "test-variant",
                "few_shot_mode": "variant",
                "examples": {
                    "vocabulary": {
                        "intermediate_reading": [
                            {
                                "example_type": "vocab",
                                "sentence_text": "Variant vocabulary sentence.",
                                "output_fragment": '{"term":"variant vocab"}',
                            }
                        ]
                    }
                },
            },
            dry_run=True,
        )
    )

    assert result.status == "succeeded"
    assert result.node_name == "vocabulary"
    assert result.warnings == []
    assert result.example_summary is not None
    assert result.example_summary["selection_mode"] == "variant"
    assert result.example_summary["example_count"] == 1
    assert result.prompt_preview is not None
    assert "Variant vocabulary sentence." in result.prompt_preview


@pytest.mark.anyio
async def test_translation_node_probe_variant_examples_are_visible_in_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(node_probe, "get_settings", _settings)

    result = await node_probe.run_article_analysis_node_probe(
        ArticleAnalysisNodeProbeRequest(
            text="Although the plan looked simple, it required careful coordination.",
            node_name="translation",
            model_selection={"default_profile": "eval-profile"},
            prompt_variant_id="test-variant",
            prompt_override={
                "variant_id": "test-variant",
                "few_shot_mode": "variant",
                "examples": {
                    "translation": {
                        "intermediate_reading": [
                            {
                                "example_type": "translation",
                                "sentence_text": "Variant translation sentence.",
                                "output_fragment": '{"translation_zh":"变体翻译"}',
                            }
                        ]
                    }
                },
            },
            dry_run=True,
        )
    )

    assert result.status == "succeeded"
    assert result.node_name == "translation"
    assert result.warnings == []
    assert result.example_summary is not None
    assert result.example_summary["selection_mode"] == "variant"
    assert result.example_summary["example_count"] == 1
    assert result.prompt_preview is not None
    assert "Variant translation sentence." in result.prompt_preview


@pytest.mark.anyio
async def test_vocabulary_node_probe_rejects_academic_topology(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(node_probe, "get_settings", _settings)

    result = await node_probe.run_article_analysis_node_probe(
        ArticleAnalysisNodeProbeRequest(
            text="This paper proposes a compact evaluation protocol for discourse analysis.",
            node_name="vocabulary",
            reading_goal="academic",
            reading_variant="academic_general",
            model_selection={"default_profile": "eval-profile"},
            dry_run=True,
        )
    )

    assert result.status == "failed"
    assert result.error is not None
    assert result.error.code == "ValueError"
    assert "learning topology" in result.error.message


@pytest.mark.anyio
async def test_translation_node_probe_rejects_academic_topology(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(node_probe, "get_settings", _settings)

    result = await node_probe.run_article_analysis_node_probe(
        ArticleAnalysisNodeProbeRequest(
            text="This paper proposes a compact evaluation protocol for discourse analysis.",
            node_name="translation",
            reading_goal="academic",
            reading_variant="academic_general",
            model_selection={"default_profile": "eval-profile"},
            dry_run=True,
        )
    )

    assert result.status == "failed"
    assert result.error is not None
    assert result.error.code == "ValueError"
    assert "learning topology" in result.error.message


@pytest.mark.anyio
async def test_vocabulary_node_probe_returns_raw_vocabulary_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(node_probe, "get_settings", _settings)
    draft = VocabularyDraft()
    run_mock = AsyncMock(return_value=SimpleNamespace(output=draft))
    monkeypatch.setattr(node_probe, "run_vocabulary_agent", run_mock)

    result = await node_probe.run_article_analysis_node_probe(
        ArticleAnalysisNodeProbeRequest(
            text="Although the plan looked simple, it required careful coordination.",
            node_name="vocabulary",
            model_selection={"default_profile": "eval-profile"},
            dry_run=False,
        )
    )

    assert result.status == "succeeded"
    assert result.node_name == "vocabulary"
    assert result.node_output is not None
    assert "vocab_highlights" in result.node_output
    assert result.runtime_summary is not None
    assert result.runtime_summary["latency_ms"] >= 0
    run_mock.assert_awaited_once()


@pytest.mark.anyio
async def test_translation_node_probe_returns_raw_translation_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(node_probe, "get_settings", _settings)
    draft = TranslationDraft(title="测试标题")
    run_mock = AsyncMock(return_value=SimpleNamespace(output=draft))
    monkeypatch.setattr(node_probe, "run_translation_agent", run_mock)

    result = await node_probe.run_article_analysis_node_probe(
        ArticleAnalysisNodeProbeRequest(
            text="Although the plan looked simple, it required careful coordination.",
            node_name="translation",
            model_selection={"default_profile": "eval-profile"},
            dry_run=False,
        )
    )

    assert result.status == "succeeded"
    assert result.node_name == "translation"
    assert result.node_output is not None
    assert result.node_output["title"] == "测试标题"
    assert "sentence_translations" in result.node_output
    assert result.runtime_summary is not None
    assert result.runtime_summary["latency_ms"] >= 0
    run_mock.assert_awaited_once()
