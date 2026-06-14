from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import eval_debug
from app.config.settings import Settings
from app.eval_adapter import article_analysis as eval_article_analysis
from app.eval_adapter.schemas import (
    ArticleAnalysisEvalRequest,
    ArticleAnalysisEvalResult,
    ModelIdentity,
    PromptIdentity,
    RequestSnapshot,
    SchemaIdentity,
    WorkflowIdentity,
)
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
                "providers": {
                    "eval-provider": {
                        "adapter": "openai_compatible",
                        "base_url": "https://example.invalid/v1",
                        "api_key": "secret-key",
                    }
                },
                "models": {
                    "eval-model": {
                        "provider": "eval-provider",
                        "model_name": "eval-model",
                        "model_settings": {
                            "temperature": 0.2,
                            "extra_headers": {"Authorization": "Bearer secret"},
                            "extra_body": {"thinking": {"type": "disabled"}},
                        },
                    }
                },
                "profiles": {
                    "eval-profile": {
                        "model": "eval-model",
                    }
                },
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


def _admin_settings() -> Settings:
    return Settings(
        daily_reader_admin_api_key="admin-key",
        eval_admin_api_key="eval-key",
        default_model_profile="",
        annotation_model_profile="",
    )


def _fake_eval_result() -> ArticleAnalysisEvalResult:
    return ArticleAnalysisEvalResult(
        status="succeeded",
        request_snapshot=RequestSnapshot(
            request_id="eval:run:case",
            source_text_hash="hash",
            source_char_count=9,
            reading_goal="daily_reading",
            reading_variant="intermediate_reading",
            source_type="user_input",
            extended=False,
            rag_mode="off",
            trace_scope="off",
        ),
        workflow_identity=WorkflowIdentity(
            workflow_name="article_analysis",
            workflow_version="3.0.0",
            topology_mode="learning",
        ),
        schema_identity=SchemaIdentity(
            schema_version="3.0.0",
            render_schema_version="3.0.0",
            topology_mode="learning",
        ),
        prompt_identity=PromptIdentity(prompt_version="prompt-test"),
        model_identity=ModelIdentity(route="annotation_generation"),
        render_scene=_render_scene("eval:run:case"),
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
    monkeypatch.setattr(
        eval_article_analysis, "validate_model_selection", lambda *a, **kw: None
    )

    async def _fake_workflow(payload, *, repair_mode=None):
        assert payload.request_id == "eval:run-1:case-1"
        assert payload.model_selection.default_profile == "eval-profile"
        assert is_grammar_rag_enabled(settings) is False
        assert repair_mode == "full_result"
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
    monkeypatch.setattr(
        eval_article_analysis, "validate_model_selection", lambda *a, **kw: None
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

    async def _slow_workflow(_payload, *, repair_mode=None):
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


def test_eval_workflow_route_is_admin_key_protected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(eval_debug, "get_settings", _admin_settings)
    run_mock = AsyncMock(return_value=_fake_eval_result())
    monkeypatch.setattr(eval_debug, "run_article_analysis_eval", run_mock)

    app = FastAPI()
    app.include_router(eval_debug.router)
    client = TestClient(app)

    denied = client.post(
        "/eval/article-analysis/workflow",
        headers={"x-admin-api-key": "wrong"},
        json={"text": "Sentence."},
    )
    denied_with_daily_key = client.post(
        "/eval/article-analysis/workflow",
        headers={"x-admin-api-key": "admin-key"},
        json={"text": "Sentence.", "rag_mode": "off"},
    )
    allowed = client.post(
        "/eval/article-analysis/workflow",
        headers={"x-admin-api-key": "eval-key"},
        json={"text": "Sentence.", "rag_mode": "off"},
    )

    assert denied.status_code == 401
    assert denied_with_daily_key.status_code == 401
    assert allowed.status_code == 200
    assert allowed.json()["workflow_identity"]["workflow_name"] == "article_analysis"
    run_mock.assert_awaited_once()


def test_eval_workflow_route_rejects_academic_goal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Eval Center v1 is learning-only; /eval/article-analysis/workflow must
    reject reading_goal='academic' with 422."""
    monkeypatch.setattr(eval_debug, "get_settings", _admin_settings)

    app = FastAPI()
    app.include_router(eval_debug.router)
    client = TestClient(app)

    response = client.post(
        "/eval/article-analysis/workflow",
        headers={"x-admin-api-key": "eval-key"},
        json={
            "text": "Academic text.",
            "reading_goal": "academic",
            "reading_variant": "academic_general",
        },
    )

    assert response.status_code == 422
    body = response.json()
    # Pydantic validation error wraps the ValueError message
    error_detail = body.get("detail", [])
    messages = [e.get("msg", "") for e in error_detail if isinstance(e, dict)]
    assert any(
        "learning topology" in m for m in messages
    ), f"Expected 'learning topology' in error messages, got: {messages}"


def test_eval_node_probe_route_rejects_academic_goal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Eval Center v1 is learning-only; /eval/article-analysis/node-probe must
    reject reading_goal='academic' with 422."""
    monkeypatch.setattr(eval_debug, "get_settings", _admin_settings)

    app = FastAPI()
    app.include_router(eval_debug.router)
    client = TestClient(app)

    response = client.post(
        "/eval/article-analysis/node-probe",
        headers={"x-admin-api-key": "eval-key"},
        json={
            "text": "Academic text.",
            "reading_goal": "academic",
            "reading_variant": "academic_general",
        },
    )

    assert response.status_code == 422
    body = response.json()
    error_detail = body.get("detail", [])
    messages = [e.get("msg", "") for e in error_detail if isinstance(e, dict)]
    assert any(
        "learning topology" in m for m in messages
    ), f"Expected 'learning topology' in error messages, got: {messages}"


def test_eval_workflow_route_allows_learning_goal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Learning goal must still work after the academic rejection guard."""
    monkeypatch.setattr(eval_debug, "get_settings", _admin_settings)
    run_mock = AsyncMock(return_value=_fake_eval_result())
    monkeypatch.setattr(eval_debug, "run_article_analysis_eval", run_mock)

    app = FastAPI()
    app.include_router(eval_debug.router)
    client = TestClient(app)

    response = client.post(
        "/eval/article-analysis/workflow",
        headers={"x-admin-api-key": "eval-key"},
        json={
            "text": "Learning text.",
            "reading_goal": "daily_reading",
            "reading_variant": "intermediate_reading",
        },
    )

    assert response.status_code == 200
    run_mock.assert_awaited_once()


def test_list_model_profile_summaries_skips_broken_profiles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A profile referencing a provider that doesn't exist in the registry
    must not crash the entire summary list — it should be silently skipped."""
    from app.eval_adapter.shared import list_model_profile_summaries

    # Two profiles: one valid, one referencing a model whose provider
    # is absent from the providers dict — resolve_model_config raises
    # ModelSelectionError for the broken one.
    settings = Settings(
        default_model_profile="eval-profile",
        annotation_model_profile="eval-profile",
        model_profiles_json=json.dumps(
            {
                "providers": {
                    "eval-provider": {
                        "adapter": "openai_compatible",
                        "base_url": "https://example.invalid/v1",
                        "api_key": "secret-key",
                    },
                },
                "models": {
                    "eval-model": {
                        "provider": "eval-provider",
                        "model_name": "eval-model",
                    },
                    "broken-model": {
                        "provider": "missing-provider",
                        "model_name": "broken-model",
                    },
                },
                "profiles": {
                    "eval-profile": {
                        "model": "eval-model",
                    },
                    "broken-profile": {
                        "model": "broken-model",
                    },
                },
            }
        ),
    )

    summaries = list_model_profile_summaries(settings=settings)
    # Only the working profile should appear; broken-profile must be skipped
    names = [s.profile_name for s in summaries]
    assert "eval-profile" in names
    assert "broken-profile" not in names


def test_list_model_profile_summaries_skips_unknown_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A profile referencing a provider that doesn't even exist in the
    registry must not crash the summary list."""
    from app.eval_adapter.shared import list_model_profile_summaries

    settings = Settings(
        default_model_profile="good-profile",
        annotation_model_profile="good-profile",
        model_profiles_json=json.dumps(
            {
                "providers": {
                    "eval-provider": {
                        "adapter": "openai_compatible",
                        "base_url": "https://example.invalid/v1",
                        "api_key": "secret-key",
                    },
                },
                "models": {
                    "eval-model": {
                        "provider": "eval-provider",
                        "model_name": "eval-model",
                    },
                    "ghost-model": {
                        "provider": "nonexistent-provider",
                        "model_name": "ghost-model",
                    },
                },
                "profiles": {
                    "good-profile": {
                        "model": "eval-model",
                    },
                    "ghost-profile": {
                        "model": "ghost-model",
                    },
                },
            }
        ),
    )

    summaries = list_model_profile_summaries(settings=settings)
    names = [s.profile_name for s in summaries]
    assert "good-profile" in names
    assert "ghost-profile" not in names


def test_list_model_profile_summaries_propagates_unexpected_errors() -> None:
    """Non-ModelSelectionError exceptions must NOT be silently swallowed."""
    from app.eval_adapter.shared import list_model_profile_summaries

    settings = Settings(
        default_model_profile="eval-profile",
        annotation_model_profile="eval-profile",
        model_profiles_json=json.dumps(
            {
                "providers": {
                    "eval-provider": {
                        "adapter": "openai_compatible",
                        "base_url": "https://example.invalid/v1",
                        "api_key": "secret-key",
                    },
                },
                "models": {
                    "eval-model": {
                        "provider": "eval-provider",
                        "model_name": "eval-model",
                    },
                },
                "profiles": {
                    "eval-profile": {
                        "model": "eval-model",
                    },
                },
            }
        ),
    )

    with (
        pytest.raises(RuntimeError, match="unexpected bug"),
    ):
        # Patch resolve_model_config to raise a programming error
        # that must propagate, not be caught.
        with patch(
            "app.eval_adapter.shared.resolve_model_config",
            side_effect=RuntimeError("unexpected bug"),
        ):
            list_model_profile_summaries(settings=settings)


def test_list_model_profile_summaries_is_resolve_only_not_buildability() -> None:
    """list_model_profile_summaries is a resolve-only catalog. It should NOT
    crash even if a profile resolves but cannot be built (e.g. missing api_key
    at build time). It only checks resolution, not buildability."""
    from app.eval_adapter.shared import list_model_profile_summaries

    # dashscope_native provider with api_key set — resolves successfully,
    # but if we tried to build it, it might fail for other reasons.
    # The point is: list_model_profile_summaries should not call
    # build_model_instance at all.
    settings = Settings(
        default_model_profile="native-profile",
        annotation_model_profile="native-profile",
        model_profiles_json=json.dumps(
            {
                "providers": {
                    "dashscope": {
                        "adapter": "dashscope_native",
                        "api_key": "test-key",
                    },
                },
                "models": {
                    "native-model": {
                        "provider": "dashscope",
                        "model_name": "qwen3.7-max",
                    },
                },
                "profiles": {
                    "native-profile": {
                        "model": "native-model",
                    },
                },
            }
        ),
    )

    summaries = list_model_profile_summaries(settings=settings)
    assert len(summaries) >= 1
    assert summaries[0].profile_name == "native-profile"
    assert summaries[0].provider == "dashscope"


def test_eval_entry_guards_use_buildable_true() -> None:
    """Article analysis and real node_probe execution should call
    validate_model_selection with buildable=True, not just resolve-only.
    This test verifies the call signature by patching
    validate_model_selection and checking the buildable kwarg."""
    from app.eval_adapter import article_analysis, node_probe

    # Patch validate_model_selection to capture the buildable kwarg
    calls: list[dict] = []

    def _fake_validate(settings, selection, routes, *, buildable=False):
        calls.append({"buildable": buildable})
        # Don't actually validate — just capture the call

    async def _fake_workflow(_payload, **_kw):
        return {"render_scene": None}

    with (
        patch(
            "app.eval_adapter.article_analysis.validate_model_selection",
            side_effect=_fake_validate,
        ),
        patch(
            "app.eval_adapter.article_analysis.build_model_identity",
            return_value=None,
        ),
        patch(
            "app.eval_adapter.article_analysis.run_article_analysis_with_state",
            _fake_workflow,
        ),
    ):
        asyncio.run(
            article_analysis.run_article_analysis_eval(
                article_analysis.ArticleAnalysisEvalRequest(
                    text="hello",
                    reading_goal="daily_reading",
                    reading_variant="intermediate_reading",
                )
            )
        )

    assert len(calls) == 1
    assert calls[0]["buildable"] is True, (
        "article_analysis entry guard should use buildable=True"
    )

    calls.clear()

    async def _fake_agent(**_kw):
        return None

    with (
        patch("app.eval_adapter.node_probe.validate_model_selection", side_effect=_fake_validate),
        patch("app.eval_adapter.node_probe.build_model_identity", return_value=None),
        patch("app.eval_adapter.node_probe.prepare_input"),
        patch("app.eval_adapter.node_probe.run_vocabulary_agent", _fake_agent),
        patch("app.eval_adapter.node_probe.run_grammar_agent", _fake_agent),
        patch("app.eval_adapter.node_probe.run_translation_agent", _fake_agent),
    ):
        asyncio.run(
            node_probe.run_article_analysis_node_probe(
                node_probe.ArticleAnalysisNodeProbeRequest(
                    text="hello",
                    reading_goal="daily_reading",
                    reading_variant="intermediate_reading",
                )
            )
        )

    assert len(calls) == 1
    assert calls[0]["buildable"] is True, (
        "node_probe entry guard should use buildable=True"
    )


def test_eval_dry_run_entry_guards_remain_resolve_only() -> None:
    """Dry-run paths only build prompt/debug artifacts and should not require
    a buildable model instance."""
    from app.eval_adapter import node_lab, node_probe

    calls: list[dict] = []

    def _fake_validate(settings, selection, routes, *, buildable=False):
        calls.append({"buildable": buildable})

    with (
        patch("app.eval_adapter.node_probe.validate_model_selection", side_effect=_fake_validate),
        patch("app.eval_adapter.node_probe.build_model_identity", return_value=None),
    ):
        asyncio.run(
            node_probe.run_article_analysis_node_probe(
                node_probe.ArticleAnalysisNodeProbeRequest(
                    text="hello",
                    reading_goal="daily_reading",
                    reading_variant="intermediate_reading",
                    dry_run=True,
                )
            )
        )

    assert len(calls) == 1
    assert calls[0]["buildable"] is False, (
        "node_probe dry-run should remain resolve-only"
    )

    calls.clear()

    with (
        patch("app.eval_adapter.node_lab.validate_model_selection", side_effect=_fake_validate),
        patch("app.eval_adapter.node_lab.build_model_identity", return_value=None),
    ):
        asyncio.run(
            node_lab.run_article_analysis_node_lab(
                node_lab.ArticleAnalysisNodeLabRunRequest(
                    node_name="grammar",
                    text="hello",
                    dry_run=True,
                    candidate_override={
                        "candidate_id": "cand-a",
                        "node_name": "grammar",
                    },
                )
            )
        )

    assert len(calls) == 1
    assert calls[0]["buildable"] is False, (
        "node_lab dry-run should remain resolve-only"
    )


@pytest.mark.anyio
async def test_eval_result_includes_node_timings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    monkeypatch.setattr(eval_article_analysis, "get_settings", lambda: settings)
    monkeypatch.setattr(
        "app.services.analysis.debug_snapshots.get_settings",
        lambda: settings,
    )
    monkeypatch.setattr(
        eval_article_analysis,
        "run_article_analysis_with_state",
        AsyncMock(
            return_value={
                "render_scene": _render_scene("eval:req"),
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
                "node_timings": {
                    "prepare_input": 0.1,
                    "parallel_agents": 5.0,
                    "normalize_and_ground": 0.2,
                },
            }
        ),
    )
    monkeypatch.setattr(eval_article_analysis, "get_prompt_version", lambda: "prompt-test")

    result = await eval_article_analysis.run_article_analysis_eval(
        ArticleAnalysisEvalRequest(text="Sentence one.")
    )

    assert result.node_timings is not None
    assert "prepare_input" in result.node_timings
    assert "parallel_agents" in result.node_timings


@pytest.mark.anyio
async def test_eval_result_includes_repair_stats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    monkeypatch.setattr(eval_article_analysis, "get_settings", lambda: settings)
    monkeypatch.setattr(
        "app.services.analysis.debug_snapshots.get_settings",
        lambda: settings,
    )
    monkeypatch.setattr(
        eval_article_analysis,
        "run_article_analysis_with_state",
        AsyncMock(
            return_value={
                "render_scene": _render_scene("eval:req"),
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
                "node_timings": {
                    "prepare_input": 0.1,
                    "parallel_agents": 5.0,
                    "normalize_and_ground": 0.2,
                },
                "repair_stats": {
                    "repair_triggered": False,
                    "trigger_threshold": 0.35,
                    "trigger_reason": None,
                    "pre_repair_annotation_count": 3,
                    "post_repair_annotation_count": None,
                    "repair_elapsed_s": None,
                    "repair_succeeded": None,
                },
            }
        ),
    )
    monkeypatch.setattr(eval_article_analysis, "get_prompt_version", lambda: "prompt-test")

    result = await eval_article_analysis.run_article_analysis_eval(
        ArticleAnalysisEvalRequest(text="Sentence one.")
    )

    assert result.repair_stats is not None
    assert result.repair_stats["repair_triggered"] is False
    assert "trigger_threshold" in result.repair_stats


@pytest.mark.anyio
async def test_eval_result_includes_canonical_drop_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    monkeypatch.setattr(eval_article_analysis, "get_settings", lambda: settings)
    monkeypatch.setattr(
        "app.services.analysis.debug_snapshots.get_settings",
        lambda: settings,
    )
    monkeypatch.setattr(
        eval_article_analysis,
        "run_article_analysis_with_state",
        AsyncMock(
            return_value={
                "render_scene": _render_scene("eval:req"),
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
                "node_timings": {
                    "prepare_input": 0.1,
                    "parallel_agents": 5.0,
                    "normalize_and_ground": 0.2,
                },
                "repair_stats": {
                    "repair_triggered": False,
                    "trigger_threshold": 0.35,
                    "trigger_reason": None,
                    "pre_repair_annotation_count": 3,
                    "post_repair_annotation_count": None,
                    "repair_elapsed_s": None,
                    "repair_succeeded": None,
                },
                "canonical_drop_log": [
                    {
                        "source_agent": "vocabulary",
                        "annotation_type": "phrase_gloss",
                        "sentence_id": "s1",
                        "anchor_text": "missing",
                        "drop_reason": "quote_not_found",
                        "drop_stage": "grounding",
                        "dropped_at": "2025-01-01T00:00:00Z",
                    }
                ],
                "drop_log": [],
            }
        ),
    )
    monkeypatch.setattr(eval_article_analysis, "get_prompt_version", lambda: "prompt-test")

    result = await eval_article_analysis.run_article_analysis_eval(
        ArticleAnalysisEvalRequest(text="Sentence one.")
    )

    assert isinstance(result.canonical_drop_log, list)
    assert len(result.canonical_drop_log) == 1
    assert result.canonical_drop_log[0]["drop_reason"] == "quote_not_found"


@pytest.mark.anyio
async def test_eval_result_includes_llm_config_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    monkeypatch.setattr(eval_article_analysis, "get_settings", lambda: settings)
    monkeypatch.setattr(
        "app.services.analysis.debug_snapshots.get_settings",
        lambda: settings,
    )
    monkeypatch.setattr(
        eval_article_analysis,
        "run_article_analysis_with_state",
        AsyncMock(
            return_value={
                "render_scene": _render_scene("eval:req"),
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
        ),
    )
    monkeypatch.setattr(eval_article_analysis, "get_prompt_version", lambda: "prompt-test")
    # validate_model_selection(buildable=True) triggers build_model_instance
    # which creates httpx.AsyncClient blocked by conftest; mock it to skip build.
    monkeypatch.setattr(
        eval_article_analysis,
        "validate_model_selection",
        lambda *a, **kw: None,
    )

    result = await eval_article_analysis.run_article_analysis_eval(
        ArticleAnalysisEvalRequest(
            text="Sentence one.",
            model_selection={"default_profile": "eval-profile"},
        )
    )

    assert result.status == "succeeded"
    assert result.llm_config_snapshot is not None
    snapshot = result.llm_config_snapshot
    assert snapshot["profile_name"] == "eval-profile"
    assert snapshot["provider"] == "eval-provider"
    assert snapshot["adapter"] == "openai_compatible"
    assert snapshot["model_name"] == "eval-model"
    assert "structured_output" in snapshot
    # No explicit openai_profile → resolved profile is None → PydanticAI
    # defaults apply: tool_choice_required=True, mode=tool → "required"
    assert snapshot["structured_output"]["expected_tool_choice"] == "required"
    assert snapshot["structured_output"]["openai_supports_tool_choice_required"] is True
    # Verify inferred defaults are filled (not null)
    assert snapshot["structured_output"]["default_structured_output_mode"] == "tool"
    assert snapshot["structured_output"]["supports_json_schema_output"] is False
    assert snapshot["structured_output"]["supports_json_object_output"] is False
    assert snapshot["structured_output"]["expected_response_format"] is None
    # Verify JSON serialization round-trips
    dumped = json.dumps(snapshot)
    assert isinstance(json.loads(dumped), dict)


def test_eval_schema_accepts_default_repair_mode() -> None:
    """ArticleAnalysisEvalRequest defaults repair_mode to 'full_result'."""
    req = ArticleAnalysisEvalRequest(text="Hello world.")
    assert req.repair_mode == "full_result"


def test_eval_schema_accepts_explicit_patch_mode() -> None:
    """ArticleAnalysisEvalRequest accepts repair_mode='patch'."""
    req = ArticleAnalysisEvalRequest(text="Hello world.", repair_mode="patch")
    assert req.repair_mode == "patch"


@pytest.mark.anyio
async def test_eval_adapter_passes_repair_mode_to_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_article_analysis_eval passes repair_mode to
    run_article_analysis_with_state."""
    settings = _settings()
    monkeypatch.setattr(eval_article_analysis, "get_settings", lambda: settings)
    monkeypatch.setattr(
        "app.services.analysis.debug_snapshots.get_settings",
        lambda: settings,
    )

    captured_kwargs: dict = {}

    async def _fake_workflow(payload, *, repair_mode=None):
        captured_kwargs["repair_mode"] = repair_mode
        return {
            "render_scene": _render_scene("eval:req"),
            "usage_summary": {
                "available": True,
                "per_agent": {},
                "aggregate": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
            },
            "warnings": [],
        }

    monkeypatch.setattr(
        eval_article_analysis,
        "run_article_analysis_with_state",
        _fake_workflow,
    )
    monkeypatch.setattr(eval_article_analysis, "get_prompt_version", lambda: "prompt-test")

    # Default: full_result
    await eval_article_analysis.run_article_analysis_eval(
        ArticleAnalysisEvalRequest(text="Sentence one.")
    )
    assert captured_kwargs["repair_mode"] == "full_result"

    # Explicit: patch
    await eval_article_analysis.run_article_analysis_eval(
        ArticleAnalysisEvalRequest(text="Sentence one.", repair_mode="patch")
    )
    assert captured_kwargs["repair_mode"] == "patch"


def test_request_snapshot_includes_repair_mode() -> None:
    """RequestSnapshot records repair_mode."""
    from app.eval_adapter.shared import request_snapshot

    req = ArticleAnalysisEvalRequest(text="Hello world.", repair_mode="patch")
    snap = request_snapshot(req, request_id_value="test-id")
    assert snap.repair_mode == "patch"

    # Default
    req2 = ArticleAnalysisEvalRequest(text="Hello world.")
    snap2 = request_snapshot(req2, request_id_value="test-id")
    assert snap2.repair_mode == "full_result"
