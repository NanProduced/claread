from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.routes import eval_debug
from app.config.settings import Settings
from app.eval_adapter import node_lab
from app.eval_adapter import node_lab_judge as node_lab_judge_adapter
from app.eval_adapter.schemas import (
    ArticleAnalysisNodeLabCompareResult,
    ArticleAnalysisNodeLabCompareRequest,
    ArticleAnalysisNodeLabRunRequest,
    ArticleAnalysisNodeLabRunResult,
    ModelIdentity,
    NodeLabJudgeAggregate,
    NodeLabJudgeCriterionScore,
    NodeLabJudgeExecuteRequest,
    NodeLabJudgeExecuteResult,
    NodeLabJudgeRunResult,
    NodeLabJudgeItemResult,
    NodeLabJudgeItemSummary,
    NodeLabJudgeSideResult,
    NodeLabPairwiseResult,
    NodeLabPairwiseReview,
    NodeLabRubricScoringResult,
    NodeLabBaselineConfigRequest,
    NodeLabResultEntry,
    PromptIdentity,
    RequestSnapshot,
    SchemaIdentity,
    WorkflowIdentity,
)
from app.schemas.internal.analysis import ContextGloss, GrammarNote, PhraseGloss, SpanRef, VocabHighlight
from app.schemas.internal.drafts import GrammarDraft, VocabularyDraft
from app.llm.routes import MODEL_ROUTE_ANNOTATION_GENERATION


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


def _judge_execute_result() -> NodeLabJudgeExecuteResult:
    side = NodeLabJudgeSideResult(
        items=[
            NodeLabJudgeItemResult(
                item_id="grammar_note:s1:focus",
                item_type="grammar_note",
                sentence_id="s1",
                label="focus",
                source_excerpt="Source sentence.",
                criteria=[
                    NodeLabJudgeCriterionScore(
                        criterion_id="GN1",
                        score=2,
                        reason="解释准确。",
                        evidence="命中原句结构。",
                    ),
                    NodeLabJudgeCriterionScore(
                        criterion_id="GN2",
                        score=1,
                        reason="针对性略弱。",
                        evidence="存在较多通用语法描述。",
                    ),
                ],
                item_summary=NodeLabJudgeItemSummary(passed=1, partial=1, failed=0),
            )
        ],
        aggregate=NodeLabJudgeAggregate(
            item_count=1,
            criteria_count=2,
            passed=1,
            partial=1,
            failed=0,
            pass_rate=0.75,
        ),
    )
    return NodeLabJudgeExecuteResult(
        request_id="judge-req-1",
        node_name="grammar",
        judge_strategy="grammar_item_review",
        judge_method="rubric_plus_pairwise",
        output_mode="rubric_scoring",
        output_schema_kind="grammar_item_scoring",
        status="succeeded",
        model_identity=ModelIdentity(
            route=MODEL_ROUTE_ANNOTATION_GENERATION,
            provider="fake",
            model_name="judge-model",
        ),
        rubric_scoring_result=NodeLabRubricScoringResult(
            strategy="grammar_item_review",
            method="rubric_plus_pairwise",
            baseline=side,
            candidate=side,
            meta={"preset_id": "grammar-default-v1"},
        ),
        runtime_summary={"latency_ms": 12},
    )


def _pairwise_result() -> NodeLabPairwiseResult:
    return NodeLabPairwiseResult(
        strategy="grammar_item_review",
        method="rubric_plus_pairwise",
        pairwise_review=NodeLabPairwiseReview(
            preferred_side="candidate",
            overall_judgment="Candidate 整体更适合当前场景。",
            baseline_strengths=["结构判断基本正确。"],
            candidate_strengths=["结构说明更聚焦。"],
            baseline_risks=["覆盖较弱。"],
            candidate_risks=[],
            manual_check_points=["复看复杂句的 few-shot 风格是否过度统一。"],
        ),
    )


def _judge_run_result() -> NodeLabJudgeRunResult:
    execute_result = _judge_execute_result()
    return NodeLabJudgeRunResult(
        judge_request_id="judge-req-1",
        trial_id="node-lab-trial-1",
        session_id=None,
        preset_id="grammar-default-v1",
        node_name="grammar",
        judge_method="rubric_plus_pairwise",
        judge_strategy="grammar_item_review",
        step_runs={
            "rubric": {"status": "succeeded", "runtime_summary": {"latency_ms": 12}},
            "pairwise": {"status": "succeeded", "runtime_summary": {"latency_ms": 9}},
            "probe": None,
        },
        rubric_scoring_result=execute_result.rubric_scoring_result,
        pairwise_result=_pairwise_result(),
    )


@pytest.mark.anyio
async def test_execute_node_lab_judge_computes_ternary_summaries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(node_lab_judge_adapter, "get_settings", _settings)
    monkeypatch.setattr(
        node_lab_judge_adapter,
        "run_agent_with_route",
        AsyncMock(
            return_value=SimpleNamespace(
                output={
                    "baseline": {
                        "items": [
                            {
                                "item_id": "grammar_note:s1:focus",
                                "item_type": "grammar_note",
                                "sentence_id": "s1",
                                "label": "focus",
                                "source_excerpt": "Source sentence.",
                                "criteria": [
                                    {"criterion_id": "GN1", "score": 2, "reason": "结构判断准确。"},
                                    {"criterion_id": "GN2", "score": 1, "reason": "解释还可更贴句。"},
                                ],
                            }
                        ],
                        "output_level_scores": [],
                    },
                    "candidate": {
                        "items": [
                            {
                                "item_id": "grammar_note:s1:focus",
                                "item_type": "grammar_note",
                                "sentence_id": "s1",
                                "label": "focus",
                                "source_excerpt": "Source sentence.",
                                "criteria": [
                                    {"criterion_id": "GN1", "score": 0, "reason": "结构判断错误。"},
                                    {"criterion_id": "GN2", "score": 1, "reason": "解释仍偏模板化。"},
                                ],
                            }
                        ],
                        "output_level_scores": [],
                    },
                    "meta": {"preset_id": "grammar-default-v1"},
                }
            )
        ),
    )
    monkeypatch.setattr(
        node_lab_judge_adapter,
        "extract_run_usage",
        lambda _result: {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    )

    result = await node_lab_judge_adapter.execute_node_lab_judge(
        NodeLabJudgeExecuteRequest(
            node_name="grammar",
            judge_strategy="grammar_item_review",
            judge_method="rubric_plus_pairwise",
            reading_goal="daily_reading",
            reading_variant="intermediate_reading",
            judger_model_profile="eval-profile",
            system_prompt="system",
            user_prompt="user",
            output_mode="rubric_scoring",
            output_schema_kind="grammar_item_scoring",
            metadata={"preset_id": "grammar-default-v1"},
        )
    )

    assert result.status == "succeeded"
    assert result.rubric_scoring_result is not None
    assert result.rubric_scoring_result.baseline.items[0].item_summary.partial == 1
    assert result.rubric_scoring_result.baseline.aggregate.passed == 1
    assert result.rubric_scoring_result.baseline.aggregate.partial == 1
    assert result.rubric_scoring_result.baseline.aggregate.pass_rate == 0.75
    assert result.rubric_scoring_result.candidate.aggregate.failed == 1
    assert result.rubric_scoring_result.candidate.aggregate.partial == 1
    assert result.rubric_scoring_result.candidate.aggregate.pass_rate == 0.25


@pytest.mark.parametrize(
    ("factory", "kwargs"),
    [
        (
            NodeLabBaselineConfigRequest,
            {"node_name": "grammar", "reading_goal": "academic", "reading_variant": "academic_general"},
        ),
        (
            ArticleAnalysisNodeLabRunRequest,
            {
                "node_name": "grammar",
                "text": "Academic input.",
                "reading_goal": "academic",
                "reading_variant": "academic_general",
            },
        ),
        (
            ArticleAnalysisNodeLabCompareRequest,
            {
                "node_name": "grammar",
                "text": "Academic input.",
                "reading_goal": "academic",
                "reading_variant": "academic_general",
                "candidate_override": {"candidate_id": "cand-a", "node_name": "grammar"},
            },
        ),
    ],
)
def test_node_lab_rejects_academic_goal(factory, kwargs) -> None:
    with pytest.raises(ValidationError, match="node_lab v1 only supports learning topology"):
        factory(**kwargs)


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
async def test_node_lab_vocabulary_quick_validation_reports_duplicates_and_subsumption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(node_lab, "get_settings", _settings)
    dynamic_run_mock = AsyncMock(
        return_value=SimpleNamespace(
            output=VocabularyDraft(
                vocab_highlights=[
                    VocabHighlight(sentence_id="s1", text="settling"),
                    VocabHighlight(sentence_id="s1", text="range"),
                ],
                phrase_glosses=[
                    PhraseGloss(
                        sentence_id="s1",
                        text="settling down",
                        phrase_type="phrasal_verb",
                        zh="安定下来",
                    )
                ],
                context_glosses=[
                    ContextGloss(
                        sentence_id="s1",
                        text="range",
                        gloss="一系列",
                        reason="后面语境强调多个选择。",
                    )
                ],
            )
        )
    )
    monkeypatch.setattr(node_lab, "_run_dynamic_agent", dynamic_run_mock)

    result = await node_lab.run_article_analysis_node_lab(
        node_lab.ArticleAnalysisNodeLabRunRequest(
            node_name="vocabulary",
            text="They are settling down with a range of choices.",
            candidate_override={
                "candidate_id": "cand-vocab",
                "node_name": "vocabulary",
                "model_selection": {"default_profile": "eval-profile"},
            },
        )
    )

    assert result.run.quick_validation is not None
    assert result.run.quick_validation["status"] == "warning"
    warning_codes = {item["code"] for item in result.run.quick_validation["warnings"]}
    assert "vocabulary_same_text_cross_type" in warning_codes
    assert "vocab_highlight_subsumed_by_phrase_gloss" in warning_codes


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
    compare_mock = AsyncMock(return_value=_compare_result().model_dump(mode="json"))
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


def test_node_lab_judge_execute_route_is_eval_admin_protected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(eval_debug, "get_settings", _eval_admin_settings)
    judge_mock = AsyncMock(return_value=_judge_execute_result())
    monkeypatch.setattr(eval_debug, "execute_node_lab_judge", judge_mock)

    app = FastAPI()
    app.include_router(eval_debug.router)
    client = TestClient(app)

    payload = {
        "node_name": "grammar",
        "judge_strategy": "grammar_item_review",
        "judge_method": "rubric_plus_pairwise",
        "reading_goal": "daily_reading",
        "reading_variant": "intermediate_reading",
        "judger_model_profile": "eval-profile",
        "system_prompt": "system",
        "user_prompt": "user",
        "output_mode": "rubric_scoring",
        "output_schema_kind": "grammar_item_scoring",
        "metadata": {"preset_id": "grammar-default-v1"},
    }

    denied = client.post(
        "/eval/article-analysis/node-lab/judge-execute",
        headers={"x-admin-api-key": "admin-key"},
        json=payload,
    )
    allowed = client.post(
        "/eval/article-analysis/node-lab/judge-execute",
        headers={"x-admin-api-key": "eval-key"},
        json=payload,
    )

    assert denied.status_code == 401
    assert allowed.status_code == 200
    assert allowed.json()["request_id"] == "judge-req-1"


def test_node_lab_judge_run_route_is_eval_admin_protected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(eval_debug, "get_settings", _eval_admin_settings)
    judge_run_mock = AsyncMock(return_value=_judge_run_result())
    monkeypatch.setattr(eval_debug, "run_node_lab_judge", judge_run_mock)

    app = FastAPI()
    app.include_router(eval_debug.router)
    client = TestClient(app)

    payload = {
        "node_name": "grammar",
        "trial_id": "node-lab-trial-1",
        "judge_request_id": "judge-req-1",
        "judge_config_snapshot": {
            "preset_id": "grammar-default-v1",
            "judger_models_json": [{"profile_name": "eval-profile"}],
        },
        "compare_result": _compare_result().model_dump(mode="json"),
        "participants": {"baseline": "baseline", "candidate": "candidate"},
    }

    denied = client.post(
        "/eval/article-analysis/node-lab/judge-run",
        headers={"x-admin-api-key": "admin-key"},
        json=payload,
    )
    allowed = client.post(
        "/eval/article-analysis/node-lab/judge-run",
        headers={"x-admin-api-key": "eval-key"},
        json=payload,
    )

    assert denied.status_code == 401
    assert allowed.status_code == 200
    assert allowed.json()["judge_request_id"] == "judge-req-1"


def test_node_lab_judge_execute_route_rejects_academic_goal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(eval_debug, "get_settings", _eval_admin_settings)

    app = FastAPI()
    app.include_router(eval_debug.router)
    client = TestClient(app)

    response = client.post(
        "/eval/article-analysis/node-lab/judge-execute",
        headers={"x-admin-api-key": "eval-key"},
        json={
            "node_name": "grammar",
            "judge_strategy": "grammar_item_review",
            "judge_method": "rubric_plus_pairwise",
            "reading_goal": "academic",
            "reading_variant": "academic_general",
            "judger_model_profile": "eval-profile",
            "system_prompt": "system",
            "user_prompt": "user",
            "output_mode": "rubric_scoring",
            "output_schema_kind": "grammar_item_scoring",
            "metadata": {},
        },
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    ("payload", "expected_message"),
    [
        (
            {
                "node_name": "translation",
                "judge_strategy": "grammar_item_review",
                "judge_method": "rubric_plus_pairwise",
                "reading_goal": "exam",
                "reading_variant": "cet",
                "judger_model_profile": "eval-profile",
                "system_prompt": "system",
                "user_prompt": "user",
                "output_mode": "rubric_scoring",
                "output_schema_kind": "grammar_item_scoring",
                "metadata": {},
            },
            "judge_strategy is not compatible with node_name",
        ),
        (
            {
                "node_name": "translation",
                "judge_strategy": "translation_output_review",
                "judge_method": "anti_template_probe",
                "reading_goal": "exam",
                "reading_variant": "cet",
                "judger_model_profile": "eval-profile",
                "system_prompt": "system",
                "user_prompt": "user",
                "output_mode": "probe_appendix",
                "output_schema_kind": "probe_appendix",
                "metadata": {},
            },
            "anti_template_probe is only supported for grammar",
        ),
    ],
)
def test_node_lab_judge_execute_request_validates_strategy_matrix(payload, expected_message) -> None:
    with pytest.raises(ValidationError, match=expected_message):
        NodeLabJudgeExecuteRequest.model_validate(payload)
