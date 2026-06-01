from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import eval_debug
from app.config.settings import Settings
from app.eval_adapter import node_lab
from app.eval_adapter.schemas import (
    ArticleAnalysisNodeLabCompareResult,
    ArticleAnalysisNodeLabRunResult,
    ModelIdentity,
    NodeLabResultEntry,
    PromptIdentity,
    RequestSnapshot,
    SchemaIdentity,
    WorkflowIdentity,
)
from app.schemas.internal.analysis import GrammarNote, SpanRef
from app.schemas.internal.drafts import GrammarDraft


def _settings() -> Settings:
    return Settings(
        default_model_profile="baseline-profile",
        annotation_model_profile="annotation-profile",
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


def _eval_admin_settings() -> Settings:
    settings = _settings()
    settings.daily_reader_admin_api_key = "admin-key"
    settings.eval_admin_api_key = "eval-key"
    return settings


def _run_result() -> ArticleAnalysisNodeLabRunResult:
    return ArticleAnalysisNodeLabRunResult(
        node_name="grammar",
        request_snapshot=RequestSnapshot(
            request_id="node-lab-run",
            source_text_hash="abc",
            source_char_count=8,
            reading_goal="daily_reading",
            reading_variant="intermediate_reading",
            source_type="user_input",
            extended=False,
            rag_mode="off",
            trace_scope="off",
        ),
        workflow_identity=WorkflowIdentity(
            workflow_name="article_analysis.node_lab",
            workflow_version="1.0.0",
            topology_mode="learning",
        ),
        schema_identity=SchemaIdentity(
            schema_version="article-analysis-node-lab-v1",
            topology_mode="learning",
        ),
        run=NodeLabResultEntry(
            participant_label="candidate",
            status="succeeded",
            prompt_identity=PromptIdentity(prompt_version="test"),
        ),
    )


def _compare_result() -> ArticleAnalysisNodeLabCompareResult:
    entry = NodeLabResultEntry(
        participant_label="baseline",
        status="succeeded",
        prompt_identity=PromptIdentity(prompt_version="test"),
    )
    return ArticleAnalysisNodeLabCompareResult(
        node_name="grammar",
        request_snapshot=RequestSnapshot(
            request_id="node-lab-compare",
            source_text_hash="abc",
            source_char_count=8,
            reading_goal="daily_reading",
            reading_variant="intermediate_reading",
            source_type="user_input",
            extended=False,
            rag_mode="off",
            trace_scope="off",
        ),
        workflow_identity=WorkflowIdentity(
            workflow_name="article_analysis.node_lab",
            workflow_version="1.0.0",
            topology_mode="learning",
        ),
        schema_identity=SchemaIdentity(
            schema_version="article-analysis-node-lab-v1",
            topology_mode="learning",
        ),
        baseline=entry.model_copy(update={"participant_label": "baseline"}),
        candidate=entry.model_copy(update={"participant_label": "candidate"}),
        compare_summary={},
    )


@pytest.mark.anyio
async def test_node_lab_dry_run_uses_instruction_policy_and_example_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(node_lab, "get_settings", _settings)

    result = await node_lab.run_article_analysis_node_lab(
        node_lab.ArticleAnalysisNodeLabRunRequest(
            node_name="grammar",
            text="Although the plan looked simple, it required careful coordination.",
            dry_run=True,
            candidate_override={
                "candidate_id": "cand-a",
                "node_name": "grammar",
                "instruction_override": {
                    "mode": "override_text",
                    "text": "You are a stricter grammar coach.",
                },
                "policy_override": {
                    "mode": "override_lines",
                    "lines": ["Only annotate the most instructionally useful structure."],
                },
                "few_shot_override": {
                    "few_shot_mode": "candidate",
                    "examples": [
                        {
                            "example_type": "grammar",
                            "sentence_text": "Candidate example sentence.",
                            "output_fragment": "{\"label\":\"candidate\"}",
                        }
                    ],
                },
                "model_selection": {"default_profile": "eval-profile"},
                "snapshot_hash": "snap-1",
            },
        )
    )

    assert result.run.status == "succeeded"
    assert result.run.agent_instructions == "You are a stricter grammar coach."
    assert "Only annotate the most instructionally useful structure." in (result.run.prompt_preview or "")
    assert "Candidate example sentence." in (result.run.prompt_preview or "")
    assert result.run.example_summary is not None
    assert result.run.example_summary["selection_mode"] == "candidate"
    assert result.run.prompt_identity.prompt_snapshot_hash == "snap-1"


@pytest.mark.anyio
async def test_node_lab_real_run_uses_dynamic_agent_when_instruction_overridden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(node_lab, "get_settings", _settings)
    dynamic_run_mock = AsyncMock(
        return_value=SimpleNamespace(
            output=GrammarDraft(
                grammar_notes=[
                    GrammarNote(
                        sentence_id="s1",
                        spans=[SpanRef(text="Although", role="subordinator")],
                        label="Clause focus",
                        note_zh="测试",
                    )
                ],
                sentence_analyses=[],
            )
        )
    )
    monkeypatch.setattr(node_lab, "_run_dynamic_agent", dynamic_run_mock)

    result = await node_lab.run_article_analysis_node_lab(
        node_lab.ArticleAnalysisNodeLabRunRequest(
            node_name="grammar",
            text="Although the plan looked simple, it required careful coordination.",
            candidate_override={
                "candidate_id": "cand-a",
                "node_name": "grammar",
                "instruction_override": {
                    "mode": "override_text",
                    "text": "You are a stricter grammar coach.",
                },
                "model_selection": {"default_profile": "eval-profile"},
            },
        )
    )

    assert result.run.status == "succeeded"
    assert result.run.node_output is not None
    assert result.run.node_output["grammar_notes"][0]["label"] == "Clause focus"
    assert result.run.quick_validation is not None
    assert result.run.quick_validation["status"] == "pass"
    dynamic_run_mock.assert_awaited_once()


@pytest.mark.anyio
async def test_node_lab_grammar_quick_validation_reports_anchor_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(node_lab, "get_settings", _settings)
    dynamic_run_mock = AsyncMock(
        return_value=SimpleNamespace(
            output=GrammarDraft(
                grammar_notes=[
                    GrammarNote(
                        sentence_id="s1",
                        spans=[SpanRef(text="Imaginary anchor", role="subordinator")],
                        label="Clause focus",
                        note_zh="测试",
                    )
                ],
                sentence_analyses=[],
            )
        )
    )
    monkeypatch.setattr(node_lab, "_run_dynamic_agent", dynamic_run_mock)

    result = await node_lab.run_article_analysis_node_lab(
        node_lab.ArticleAnalysisNodeLabRunRequest(
            node_name="grammar",
            text="Although the plan looked simple, it required careful coordination.",
            candidate_override={
                "candidate_id": "cand-a",
                "node_name": "grammar",
                "model_selection": {"default_profile": "eval-profile"},
            },
        )
    )

    assert result.run.quick_validation is not None
    assert result.run.quick_validation["status"] == "warning"
    assert result.run.quick_validation["warning_count"] == 1
    assert result.run.quick_validation["warnings"][0]["code"] == "grammar_span_not_found"


@pytest.mark.anyio
async def test_node_lab_compare_returns_baseline_and_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(node_lab, "get_settings", _settings)
    run_mock = AsyncMock(
        side_effect=[
            NodeLabResultEntry(
                participant_label="baseline",
                status="succeeded",
                prompt_identity=PromptIdentity(prompt_version="test"),
                prompt_preview="baseline prompt",
                runtime_summary={"latency_ms": 10, "aggregate": {"total_tokens": 20}},
            ),
            NodeLabResultEntry(
                participant_label="candidate",
                candidate_id="cand-a",
                snapshot_hash="snap-1",
                status="succeeded",
                prompt_identity=PromptIdentity(prompt_version="test", prompt_snapshot_hash="snap-1"),
                prompt_preview="candidate prompt",
                runtime_summary={"latency_ms": 25, "aggregate": {"total_tokens": 42}},
            ),
        ]
    )
    monkeypatch.setattr(node_lab, "_run_node_lab_once", run_mock)

    result = await node_lab.compare_article_analysis_node_lab(
        node_lab.ArticleAnalysisNodeLabCompareRequest(
            node_name="grammar",
            text="Although the plan looked simple, it required careful coordination.",
            candidate_override={
                "candidate_id": "cand-a",
                "node_name": "grammar",
            },
        )
    )

    assert result.baseline.participant_label == "baseline"
    assert result.candidate.participant_label == "candidate"
    assert result.compare_summary["prompt_changed"] is True
    assert result.compare_summary["token_delta"] == 22
    assert result.compare_summary["baseline_latency_ms"] == 10
    assert result.compare_summary["candidate_latency_ms"] == 25


@pytest.mark.anyio
async def test_node_lab_compare_real_path_does_not_require_dry_run_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    settings.annotation_model_profile = "eval-profile"
    monkeypatch.setattr(node_lab, "get_settings", lambda: settings)
    dynamic_run_mock = AsyncMock(
        return_value=SimpleNamespace(
            output=GrammarDraft(
                grammar_notes=[
                    GrammarNote(
                        sentence_id="s1",
                        spans=[SpanRef(text="Although", role="subordinator")],
                        label="Clause focus",
                        note_zh="测试",
                    )
                ],
                sentence_analyses=[],
            )
        )
    )
    monkeypatch.setattr(node_lab, "_run_dynamic_agent", dynamic_run_mock)

    result = await node_lab.compare_article_analysis_node_lab(
        node_lab.ArticleAnalysisNodeLabCompareRequest(
            node_name="grammar",
            text="Although the plan looked simple, it required careful coordination.",
            candidate_override={
                "candidate_id": "cand-a",
                "node_name": "grammar",
                "model_selection": {"default_profile": "eval-profile"},
            },
        )
    )

    assert result.baseline.status == "succeeded"
    assert result.candidate.status == "succeeded"
    assert dynamic_run_mock.await_count == 2


def test_node_lab_baseline_config_uses_raw_policy_loader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        node_lab,
        "load_policy_lines_raw",
        lambda *_args, **_kwargs: ["baseline policy line"],
    )
    monkeypatch.setattr(
        node_lab,
        "load_policy_lines",
        lambda *_args, **_kwargs: ["contaminated override line"],
    )

    result = node_lab.get_node_lab_baseline_config(
        node_lab.NodeLabBaselineConfigRequest(node_name="grammar")
    )

    assert result.policy_lines == ["baseline policy line"]


def test_node_lab_rejects_rag_for_non_grammar_node() -> None:
    with pytest.raises(ValueError, match="few_shot_mode='rag' is only supported for grammar"):
        node_lab.ArticleAnalysisNodeLabRunRequest(
            node_name="translation",
            text="Sentence.",
            candidate_override={
                "candidate_id": "cand-a",
                "node_name": "translation",
                "few_shot_override": {
                    "few_shot_mode": "rag",
                },
            },
        )


def test_node_lab_routes_are_eval_admin_protected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(eval_debug, "get_settings", _eval_admin_settings)
    run_mock = AsyncMock(return_value=_run_result())
    compare_mock = AsyncMock(return_value=_compare_result())
    monkeypatch.setattr(eval_debug, "run_article_analysis_node_lab", run_mock)
    monkeypatch.setattr(eval_debug, "compare_article_analysis_node_lab", compare_mock)

    app = FastAPI()
    app.include_router(eval_debug.router)
    client = TestClient(app)

    denied = client.post(
        "/eval/article-analysis/node-lab/run",
        headers={"x-admin-api-key": "admin-key"},
        json={"node_name": "grammar", "text": "Sentence."},
    )
    allowed = client.post(
        "/eval/article-analysis/node-lab/run",
        headers={"x-admin-api-key": "eval-key"},
        json={"node_name": "grammar", "text": "Sentence."},
    )
    compare_allowed = client.post(
        "/eval/article-analysis/node-lab/compare",
        headers={"x-admin-api-key": "eval-key"},
        json={
            "node_name": "grammar",
            "text": "Sentence.",
            "candidate_override": {"candidate_id": "cand-a", "node_name": "grammar"},
        },
    )

    assert denied.status_code == 401
    assert allowed.status_code == 200
    assert compare_allowed.status_code == 200
