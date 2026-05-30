from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config.settings import Settings
from app.eval_adapter import article_analysis as eval_article_analysis
from app.eval_adapter.schemas import ArticleAnalysisEvalRequest
from app.llm.types import ModelSelection
from app.schemas.analysis import AnalyzeRequestMeta, ArticleStructure, RenderSceneModel
from app.services.analysis.prompting.runtime_context import is_grammar_rag_enabled
from app.workflow import analyze_nodes


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
                    "model_settings": {
                        "temperature": 0.2,
                        "extra_headers": {"Authorization": "Bearer secret"},
                        "extra_body": {"thinking": {"type": "disabled"}},
                    },
                }
            }
        ),
    )


def _render_scene(request_id: str = "eval:req") -> RenderSceneModel:
    return RenderSceneModel(
        request=AnalyzeRequestMeta(
            request_id=request_id,
            source_type="user_input",
            reading_goal="daily_reading",
            reading_variant="intermediate_reading",
            profile_id="daily_intermediate",
        ),
        article=ArticleStructure(
            source_type="user_input",
            source_text="Sentence one.",
            render_text="Sentence one.",
            paragraphs=[],
            sentences=[],
        ),
        translations=[],
        inline_marks=[],
        sentence_entries=[],
        warnings=[],
    )


@pytest.mark.anyio
async def test_eval_adapter_returns_sanitized_success_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    monkeypatch.setattr(eval_article_analysis, "get_settings", lambda: settings)
    monkeypatch.setattr(
        "app.services.analysis.debug_snapshots.get_settings",
        lambda: settings,
    )

    async def _fake_workflow(payload):
        assert payload.request_id == "eval:run-1:case-1"
        assert payload.model_selection.default_profile == "eval-profile"
        assert is_grammar_rag_enabled(settings) is False
        return {
            "render_scene": _render_scene(payload.request_id),
            "usage_summary": {
                "available": True,
                "per_agent": {},
                "aggregate": {
                    "input_tokens": 1,
                    "output_tokens": 2,
                    "total_tokens": 3,
                },
            },
            "warnings": [],
        }

    workflow_mock = AsyncMock(side_effect=_fake_workflow)
    monkeypatch.setattr(
        eval_article_analysis,
        "run_article_analysis_with_state",
        workflow_mock,
    )
    monkeypatch.setattr(eval_article_analysis, "get_prompt_version", lambda: "prompt-test")

    result = await eval_article_analysis.run_article_analysis_eval(
        ArticleAnalysisEvalRequest(
            case_id="case-1",
            run_id="run-1",
            text="Sentence one.",
            model_selection={"default_profile": "eval-profile"},
            rag_mode="off",
            prompt_variant_id="variant-a",
            prompt_override={
                "variant_id": "variant-a",
                "few_shot_mode": "off",
            },
        )
    )

    assert result.status == "succeeded"
    assert result.error is None
    assert result.request_snapshot.request_id == "eval:run-1:case-1"
    assert result.model_identity is not None
    assert result.model_identity.profile_name == "eval-profile"
    assert result.model_identity.model_name == "eval-model"
    assert result.prompt_identity.prompt_variant_id == "variant-a"
    assert result.prompt_identity.prompt_snapshot_hash is not None
    assert result.prompt_identity.prompt_snapshot_hash != "hash-from-evals"
    assert "extra_headers" not in result.model_identity.model_settings
    assert result.runtime_summary is not None
    assert "billed_points" not in result.runtime_summary
    dumped = result.model_dump(mode="json")
    assert "secret-key" not in json.dumps(dumped)
    assert "example.invalid" not in json.dumps(dumped)
    workflow_mock.assert_awaited_once()


@pytest.mark.anyio
async def test_eval_adapter_uses_provided_prompt_snapshot_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    monkeypatch.setattr(eval_article_analysis, "get_settings", lambda: settings)
    monkeypatch.setattr(
        eval_article_analysis,
        "run_article_analysis_with_state",
        AsyncMock(return_value={"render_scene": _render_scene("eval:req")}),
    )

    result = await eval_article_analysis.run_article_analysis_eval(
        ArticleAnalysisEvalRequest(
            text="Sentence one.",
            rag_mode="off",
            prompt_variant_id="variant-a",
            prompt_override={
                "variant_id": "variant-a",
                "few_shot_mode": "off",
                "prompt_snapshot_hash": "hash-from-evals",
            },
        )
    )

    assert result.status == "succeeded"
    assert result.prompt_identity.prompt_snapshot_hash == "hash-from-evals"


@pytest.mark.anyio
async def test_eval_adapter_returns_structured_model_selection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    monkeypatch.setattr(eval_article_analysis, "get_settings", lambda: settings)
    workflow_mock = AsyncMock()
    monkeypatch.setattr(
        eval_article_analysis,
        "run_article_analysis_with_state",
        workflow_mock,
    )

    result = await eval_article_analysis.run_article_analysis_eval(
        ArticleAnalysisEvalRequest(
            text="Sentence one.",
            model_selection={"default_profile": "missing-profile"},
        )
    )

    assert result.status == "failed"
    assert result.error is not None
    assert result.error.code == "ModelSelectionError"
    workflow_mock.assert_not_awaited()


@pytest.mark.anyio
async def test_eval_adapter_returns_structured_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    monkeypatch.setattr(eval_article_analysis, "get_settings", lambda: settings)

    async def _slow_workflow(_payload):
        await asyncio.sleep(0.05)
        return {"render_scene": _render_scene("eval:req")}

    monkeypatch.setattr(
        eval_article_analysis,
        "run_article_analysis_with_state",
        _slow_workflow,
    )

    result = await eval_article_analysis.run_article_analysis_eval(
        ArticleAnalysisEvalRequest(
            text="Sentence one.",
            timeout_seconds=0.001,
        )
    )

    assert result.status == "timeout"
    assert result.error is not None
    assert result.error.code == "TimeoutError"
    assert result.render_scene is None


@pytest.mark.anyio
async def test_eval_adapter_does_not_call_business_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    monkeypatch.setattr(eval_article_analysis, "get_settings", lambda: settings)
    monkeypatch.setattr(
        eval_article_analysis,
        "run_article_analysis_with_state",
        AsyncMock(return_value={"render_scene": _render_scene("eval:req")}),
    )
    side_effect_mocks = [
        AsyncMock(side_effect=AssertionError("record_ai_usage_event called")),
        AsyncMock(side_effect=AssertionError("record_rag_usage_events_from_result called")),
        AsyncMock(side_effect=AssertionError("upsert_debug_snapshot called")),
        AsyncMock(side_effect=AssertionError("deduct_credits called")),
    ]
    monkeypatch.setattr(
        "app.services.ai_usage.service.record_ai_usage_event",
        side_effect_mocks[0],
    )
    monkeypatch.setattr(
        "app.services.analysis.rag_usage_events.record_rag_usage_events_from_result",
        side_effect_mocks[1],
    )
    monkeypatch.setattr(
        "app.services.analysis.debug_snapshots.upsert_debug_snapshot",
        side_effect_mocks[2],
    )
    monkeypatch.setattr(
        "app.services.analysis.credit_service.deduct_credits",
        side_effect_mocks[3],
    )

    result = await eval_article_analysis.run_article_analysis_eval(
        ArticleAnalysisEvalRequest(text="Sentence one.")
    )

    assert result.status == "succeeded"
    for mock in side_effect_mocks:
        mock.assert_not_called()


def test_repair_llm_span_forwards_model_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def _fake_run_agent_with_route(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(output="ok", usage=lambda: None)

    monkeypatch.setattr(
        "app.llm.agent_runner.run_agent_with_route",
        _fake_run_agent_with_route,
    )
    selection = ModelSelection(default_profile="eval-profile")

    result = asyncio.run(
        analyze_nodes._run_repair_llm_span(
            deps=analyze_nodes.RepairAgentDeps(sentences=[], original_drafts={}),
            metadata={},
            error_context="repair",
            model_selection=selection,
        )
    )

    assert result["output"] == "ok"
    assert captured["model_selection"] == selection
