from __future__ import annotations

import sys
from pathlib import Path
from time import perf_counter
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent

from app.config.settings import get_settings
from app.eval_adapter.schemas import (
    ArticleAnalysisNodeLabCompareResult,
    EvalError,
    ModelIdentity,
    NodeLabJudgeAggregate,
    NodeLabJudgeCriterionScore,
    NodeLabJudgeExecuteRequest,
    NodeLabJudgeExecuteResult,
    NodeLabJudgeItemResult,
    NodeLabJudgeItemSummary,
    NodeLabJudgeRunRequest,
    NodeLabJudgeRunResult,
    NodeLabJudgeSideResult,
    NodeLabPairwiseResult,
    NodeLabPairwiseReview,
    NodeLabProbeQuestionResult,
    NodeLabProbeAppendixResult,
    NodeLabRubricScoringResult,
)
from app.eval_adapter.shared import model_identity as build_model_identity
from app.llm.agent_runner import extract_run_usage, run_agent_with_route
from app.llm.router import ModelSelectionError, validate_model_selection
from app.llm.routes import MODEL_ROUTE_ANNOTATION_GENERATION
from app.llm.types import ModelSelection, RouteModelSelection, RunModelSettings
from app.services.analysis.debug_snapshots import build_runtime_summary, build_trace_refs


class _NodeLabJudgeExecuteDeps(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metadata: dict[str, Any] = Field(default_factory=dict)


class _RubricItemPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str
    item_type: str
    sentence_id: str | None = None
    label: str | None = None
    source_excerpt: str | None = None
    criteria: list[NodeLabJudgeCriterionScore] = Field(default_factory=list)


class _RubricSidePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[_RubricItemPayload] = Field(default_factory=list)
    output_level_scores: list[NodeLabJudgeCriterionScore] = Field(default_factory=list)


class _RubricScoringPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    baseline: _RubricSidePayload
    candidate: _RubricSidePayload
    meta: dict[str, Any] = Field(default_factory=dict)


class _PairwisePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preferred_side: Literal["baseline", "candidate", "mixed", "inconclusive"]
    overall_judgment: str
    baseline_strengths: list[str] = Field(default_factory=list)
    candidate_strengths: list[str] = Field(default_factory=list)
    baseline_risks: list[str] = Field(default_factory=list)
    candidate_risks: list[str] = Field(default_factory=list)
    manual_check_points: list[str] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


class _ProbeAppendixPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    questions: list[NodeLabProbeQuestionResult] = Field(default_factory=list)
    summary: str | None = None


def _score_counts(criteria: list[NodeLabJudgeCriterionScore]) -> tuple[int, int, int]:
    passed = sum(1 for criterion in criteria if criterion.score == 2)
    partial = sum(1 for criterion in criteria if criterion.score == 1)
    failed = sum(1 for criterion in criteria if criterion.score == 0)
    return passed, partial, failed


def _build_item_summary(criteria: list[NodeLabJudgeCriterionScore]) -> NodeLabJudgeItemSummary:
    passed, partial, failed = _score_counts(criteria)
    return NodeLabJudgeItemSummary(
        passed=passed,
        partial=partial,
        failed=failed,
    )


def _build_side_result(
    payload: _RubricSidePayload,
    *,
    strategy: str,
) -> NodeLabJudgeSideResult:
    items = [
        NodeLabJudgeItemResult(
            item_id=item.item_id,
            item_type=item.item_type,
            sentence_id=item.sentence_id,
            label=item.label,
            source_excerpt=item.source_excerpt,
            criteria=item.criteria,
            item_summary=_build_item_summary(item.criteria),
        )
        for item in payload.items
    ]
    output_level_scores = payload.output_level_scores
    item_criteria_count = sum(len(item.criteria) for item in items)
    output_criteria_count = len(output_level_scores)
    passed_items = sum(item.item_summary.passed for item in items)
    partial_items = sum(item.item_summary.partial for item in items)
    failed_items = sum(item.item_summary.failed for item in items)
    passed_output, partial_output, failed_output = _score_counts(output_level_scores)
    criteria_count = item_criteria_count + output_criteria_count
    passed = passed_items + passed_output
    partial = partial_items + partial_output
    failed = failed_items + failed_output
    weighted_total = (passed * 2) + partial
    denominator = criteria_count * 2
    pass_rate = (weighted_total / denominator) if denominator else 0.0
    return NodeLabJudgeSideResult(
        items=items,
        output_level_scores=output_level_scores,
        aggregate=NodeLabJudgeAggregate(
            item_count=None if strategy == "translation_output_review" else len(items),
            criteria_count=criteria_count,
            passed=passed,
            partial=partial,
            failed=failed,
            pass_rate=pass_rate,
        ),
    )


def _request_id(request: NodeLabJudgeExecuteRequest) -> str:
    return request.request_id or f"node-lab-judge:{uuid4()}"


def _model_selection(request: NodeLabJudgeExecuteRequest) -> ModelSelection:
    route_selection = RouteModelSelection(profile=request.judger_model_profile)
    if request.judger_model_settings:
        route_selection.model_settings = RunModelSettings.model_validate(request.judger_model_settings)
    return ModelSelection(
        default_profile=request.judger_model_profile,
        routes={MODEL_ROUTE_ANNOTATION_GENERATION: route_selection},
    )


def _runtime_summary(usage: dict[str, Any] | None, *, latency_ms: int) -> dict[str, Any]:
    return build_runtime_summary(
        {
            "available": bool(usage),
            "per_agent": {"judge": usage} if usage else {},
            "aggregate": usage or {},
        },
        latency_ms=latency_ms,
        billed_points=0,
    )


def _output_type_for_request(request: NodeLabJudgeExecuteRequest) -> type[BaseModel]:
    if request.output_mode == "rubric_scoring":
        return _RubricScoringPayload
    if request.output_mode == "pairwise":
        return _PairwisePayload
    return _ProbeAppendixPayload


def _agent_name_for_request(request: NodeLabJudgeExecuteRequest) -> str:
    return f"node_lab_judge_{request.judge_strategy}_{request.output_mode}"


def _ensure_claread_eval_runtime() -> dict[str, Any]:
    evals_root = Path(__file__).resolve().parents[4] / "evals"
    evals_root_str = str(evals_root)
    if evals_root_str not in sys.path:
        sys.path.append(evals_root_str)
    from claread_eval.node_lab_judge.config_loader import load_node_lab_judge_catalog
    from claread_eval.node_lab_judge.packet_builder import (
        build_pairwise_packet,
        build_probe_packet,
        build_rubric_packet,
    )
    from claread_eval.node_lab_judge.prompt_assembler import (
        build_pairwise_prompts,
        build_probe_prompts,
        build_rubric_prompts,
    )
    from claread_eval.node_lab_judge.schemas import NodeLabRubricScoringResult as RuntimeRubricResult

    return {
        "load_node_lab_judge_catalog": load_node_lab_judge_catalog,
        "build_rubric_packet": build_rubric_packet,
        "build_pairwise_packet": build_pairwise_packet,
        "build_probe_packet": build_probe_packet,
        "build_rubric_prompts": build_rubric_prompts,
        "build_pairwise_prompts": build_pairwise_prompts,
        "build_probe_prompts": build_probe_prompts,
        "RuntimeRubricResult": RuntimeRubricResult,
    }


def _step_run_from_response(response: dict[str, Any]) -> dict[str, Any]:
    status = response.get("status") or ("failed" if response.get("error") else "succeeded")
    return {
        "status": status,
        "runtime_summary": response.get("runtime_summary") or None,
        "model_identity": response.get("model_identity") or None,
        "error": response.get("error") or None,
        "output_mode": response.get("output_mode") or None,
        "output_schema_kind": response.get("output_schema_kind") or None,
    }


def _error_payload(exc: Exception) -> dict[str, str]:
    return {
        "code": type(exc).__name__,
        "message": str(exc),
    }


def _response_payload(response: dict[str, Any], key: str) -> Any:
    if key not in response:
        raise RuntimeError(f"Judge execute response missing required key: {key}")
    return response.get(key)


def _resolve_reading(compare_result: ArticleAnalysisNodeLabCompareResult) -> tuple[str, str]:
    snapshot = compare_result.request_snapshot
    return str(snapshot.reading_goal), str(snapshot.reading_variant)


def _resolve_preset(catalog: Any, config_snapshot: dict[str, Any], *, node_name: str) -> Any:
    preset_id = str(config_snapshot.get("preset_id") or "").strip()
    if not preset_id:
        raise RuntimeError("judge_config_snapshot.preset_id is required for Node Lab judge.")
    preset = catalog.presets.get(preset_id)
    if preset is None:
        raise RuntimeError(f"Node Lab judge preset not found: {preset_id}")
    if preset.node_name != node_name:
        raise RuntimeError("Judge preset node_name does not match compare trial node_name.")
    return preset


def _resolve_context(catalog: Any, *, reading_goal: str, reading_variant: str) -> Any:
    by_goal = catalog.contexts.get(reading_goal)
    if by_goal is None:
        raise RuntimeError(f"Resolved judge context not found for reading_goal={reading_goal}")
    context = by_goal.get(reading_variant)
    if context is None:
        raise RuntimeError(
            f"Resolved judge context not found for reading_variant={reading_variant} under reading_goal={reading_goal}"
        )
    return context


def _judger_profile(config_snapshot: dict[str, Any]) -> str:
    models = config_snapshot.get("judger_models_json") or []
    if isinstance(models, list):
        for item in models:
            if isinstance(item, dict) and item.get("profile_name"):
                return str(item["profile_name"])
    raise RuntimeError("At least one judger_models_json.profile_name is required.")


def _judge_settings(config_snapshot: dict[str, Any]) -> dict[str, Any]:
    raw = config_snapshot.get("parameters_json")
    return raw if isinstance(raw, dict) else {}


async def _execute_high_level_call(
    *,
    node_name: str,
    preset: Any,
    reading_goal: str,
    reading_variant: str,
    judger_model_profile: str,
    judger_model_settings: dict[str, Any],
    output_mode: str,
    output_schema_kind: str,
    system_prompt: str,
    user_prompt: str,
    metadata: dict[str, Any],
    timeout_seconds: float | None,
) -> dict[str, Any]:
    response = await execute_node_lab_judge(
        NodeLabJudgeExecuteRequest(
            node_name=node_name,
            judge_strategy=preset.strategy,
            judge_method=preset.method,
            reading_goal=reading_goal,
            reading_variant=reading_variant,
            judger_model_profile=judger_model_profile,
            judger_model_settings=judger_model_settings,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            output_mode=output_mode,
            output_schema_kind=output_schema_kind,
            metadata=metadata,
            timeout_seconds=timeout_seconds,
        )
    )
    return response.model_dump(mode="json")


async def execute_node_lab_judge(
    request: NodeLabJudgeExecuteRequest,
) -> NodeLabJudgeExecuteResult:
    request_id = _request_id(request)
    started_at = perf_counter()
    settings = get_settings()
    selection = _model_selection(request)
    model_identity: ModelIdentity | None = None

    try:
        # Execution entry guard: the model must be buildable, not just
        # resolvable, because we are about to actually call the LLM.
        validate_model_selection(
            settings,
            selection,
            (MODEL_ROUTE_ANNOTATION_GENERATION,),
            buildable=True,
        )
        model_identity = build_model_identity(selection, settings=settings)
    except ModelSelectionError as exc:
        latency_ms = int((perf_counter() - started_at) * 1000)
        return NodeLabJudgeExecuteResult(
            request_id=request_id,
            node_name=request.node_name,
            judge_strategy=request.judge_strategy,
            judge_method=request.judge_method,
            output_mode=request.output_mode,
            output_schema_kind=request.output_schema_kind,
            status="failed",
            error=EvalError(code=type(exc).__name__, message=str(exc)),
            model_identity=model_identity,
            runtime_summary={"latency_ms": latency_ms},
            trace_refs=build_trace_refs(request_id=request_id),
        )

    try:
        output_type = _output_type_for_request(request)
        agent = Agent[_NodeLabJudgeExecuteDeps, BaseModel](
            model=None,
            output_type=output_type,
            deps_type=_NodeLabJudgeExecuteDeps,
            instructions=request.system_prompt,
            name=_agent_name_for_request(request),
            retries=1,
            output_retries=2,
            instrument=False,
        )
        result = await run_agent_with_route(
            agent=agent,
            prompt=request.user_prompt,
            deps=_NodeLabJudgeExecuteDeps(metadata=request.metadata),
            route=MODEL_ROUTE_ANNOTATION_GENERATION,
            model_selection=selection,
        )
        output = result.output
        usage = extract_run_usage(result)
    except Exception as exc:
        latency_ms = int((perf_counter() - started_at) * 1000)
        return NodeLabJudgeExecuteResult(
            request_id=request_id,
            node_name=request.node_name,
            judge_strategy=request.judge_strategy,
            judge_method=request.judge_method,
            output_mode=request.output_mode,
            output_schema_kind=request.output_schema_kind,
            status="failed",
            error=EvalError(code=type(exc).__name__, message=str(exc)),
            model_identity=model_identity,
            runtime_summary=_runtime_summary(None, latency_ms=latency_ms),
            trace_refs=build_trace_refs(request_id=request_id),
        )

    latency_ms = int((perf_counter() - started_at) * 1000)
    response = NodeLabJudgeExecuteResult(
        request_id=request_id,
        node_name=request.node_name,
        judge_strategy=request.judge_strategy,
        judge_method=request.judge_method,
        output_mode=request.output_mode,
        output_schema_kind=request.output_schema_kind,
        status="succeeded",
        model_identity=model_identity,
        runtime_summary=_runtime_summary(usage, latency_ms=latency_ms),
        trace_refs=build_trace_refs(request_id=request_id),
    )
    if request.output_mode == "rubric_scoring":
        payload = _RubricScoringPayload.model_validate(output)
        response.rubric_scoring_result = NodeLabRubricScoringResult(
            strategy=request.judge_strategy,
            method=request.judge_method,
            baseline=_build_side_result(payload.baseline, strategy=request.judge_strategy),
            candidate=_build_side_result(payload.candidate, strategy=request.judge_strategy),
            meta=payload.meta,
        )
    elif request.output_mode == "pairwise":
        payload = _PairwisePayload.model_validate(output)
        response.pairwise_result = NodeLabPairwiseResult(
            strategy=request.judge_strategy,
            method=request.judge_method,
            pairwise_review=NodeLabPairwiseReview(
                preferred_side=payload.preferred_side,
                overall_judgment=payload.overall_judgment,
                baseline_strengths=payload.baseline_strengths,
                candidate_strengths=payload.candidate_strengths,
                baseline_risks=payload.baseline_risks,
                candidate_risks=payload.candidate_risks,
                manual_check_points=payload.manual_check_points,
            ),
            meta=payload.meta,
        )
    else:
        payload = _ProbeAppendixPayload.model_validate(output)
        response.probe_appendix_result = NodeLabProbeAppendixResult(
            probe_type=request.metadata.get("probe_type", "anti_template_probe"),
            questions=payload.questions,
            summary=payload.summary,
        )
    return response


async def run_node_lab_judge(
    request: NodeLabJudgeRunRequest,
) -> NodeLabJudgeRunResult:
    runtime = _ensure_claread_eval_runtime()
    catalog = runtime["load_node_lab_judge_catalog"]()
    compare_result = request.compare_result
    compare_payload = compare_result.model_dump(mode="json")
    reading_goal, reading_variant = _resolve_reading(compare_result)
    preset = _resolve_preset(
        catalog,
        request.judge_config_snapshot,
        node_name=request.node_name,
    )
    context = _resolve_context(
        catalog,
        reading_goal=reading_goal,
        reading_variant=reading_variant,
    )
    strategy_spec = catalog.rubrics[preset.node_name]
    judger_model_profile = _judger_profile(request.judge_config_snapshot)
    judger_model_settings = _judge_settings(request.judge_config_snapshot)
    timeout_seconds = request.timeout_seconds
    judge_request_id = request.judge_request_id or request.request_id or f"node-lab-judge:{uuid4()}"

    result_payload: dict[str, Any] = {
        "eval_adapter_schema_version": "article-analysis-node-lab-judge-v1",
        "judge_request_id": judge_request_id,
        "trial_id": request.trial_id,
        "session_id": request.session_id,
        "preset_id": preset.preset_id,
        "node_name": request.node_name,
        "judge_method": preset.method,
        "judge_strategy": preset.strategy,
        "step_runs": {
            "rubric": None,
            "pairwise": None,
            "probe": None,
        },
    }

    if preset.method == "anti_template_probe":
        probe_packet = runtime["build_probe_packet"](
            compare_payload=compare_payload,
            preset=preset,
            context=context,
            reading_goal=reading_goal,
            reading_variant=reading_variant,
        )
        system_prompt, user_prompt = runtime["build_probe_prompts"](probe_packet)
        probe_response = await _execute_high_level_call(
            node_name=request.node_name,
            preset=preset,
            reading_goal=reading_goal,
            reading_variant=reading_variant,
            judger_model_profile=judger_model_profile,
            judger_model_settings=judger_model_settings,
            output_mode="probe_appendix",
            output_schema_kind="probe_appendix",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            metadata={
                "preset_id": preset.preset_id,
                "probe_type": preset.preset_id,
                "trial_id": request.trial_id,
                "judge_request_id": judge_request_id,
            },
            timeout_seconds=timeout_seconds,
        )
        result_payload["step_runs"]["probe"] = _step_run_from_response(probe_response)
        result_payload["probe_appendix_result"] = _response_payload(
            probe_response,
            "probe_appendix_result",
        )
    else:
        rubric_packet = runtime["build_rubric_packet"](
            compare_payload=compare_payload,
            preset=preset,
            context=context,
            strategy_spec=strategy_spec,
            reading_goal=reading_goal,
            reading_variant=reading_variant,
        )
        system_prompt, user_prompt = runtime["build_rubric_prompts"](rubric_packet)
        rubric_response = await _execute_high_level_call(
            node_name=request.node_name,
            preset=preset,
            reading_goal=reading_goal,
            reading_variant=reading_variant,
            judger_model_profile=judger_model_profile,
            judger_model_settings=judger_model_settings,
            output_mode="rubric_scoring",
            output_schema_kind=(
                "translation_output_scoring"
                if preset.strategy == "translation_output_review"
                else "vocabulary_item_scoring"
                if preset.strategy == "vocabulary_item_review"
                else "grammar_item_scoring"
            ),
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            metadata={
                "preset_id": preset.preset_id,
                "trial_id": request.trial_id,
                "judge_request_id": judge_request_id,
            },
            timeout_seconds=timeout_seconds,
        )
        result_payload["step_runs"]["rubric"] = _step_run_from_response(rubric_response)
        rubric_result = _response_payload(rubric_response, "rubric_scoring_result")
        result_payload["rubric_scoring_result"] = rubric_result

        if preset.method == "rubric_plus_pairwise" and preset.pairwise and preset.pairwise.enabled:
            try:
                runtime_rubric_result = runtime["RuntimeRubricResult"].model_validate(rubric_result)
                pairwise_packet = runtime["build_pairwise_packet"](
                    compare_payload=compare_payload,
                    preset=preset,
                    context=context,
                    reading_goal=reading_goal,
                    reading_variant=reading_variant,
                    rubric_result=runtime_rubric_result,
                )
                system_prompt, user_prompt = runtime["build_pairwise_prompts"](pairwise_packet)
                pairwise_response = await _execute_high_level_call(
                    node_name=request.node_name,
                    preset=preset,
                    reading_goal=reading_goal,
                    reading_variant=reading_variant,
                    judger_model_profile=judger_model_profile,
                    judger_model_settings=judger_model_settings,
                    output_mode="pairwise",
                    output_schema_kind="pairwise_review",
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    metadata={
                        "preset_id": preset.preset_id,
                        "trial_id": request.trial_id,
                        "judge_request_id": judge_request_id,
                    },
                    timeout_seconds=timeout_seconds,
                )
                result_payload["step_runs"]["pairwise"] = _step_run_from_response(pairwise_response)
                if pairwise_response.get("pairwise_result"):
                    result_payload["pairwise_result"] = _response_payload(pairwise_response, "pairwise_result")
                elif pairwise_response.get("error"):
                    result_payload["pairwise_error"] = EvalError.model_validate(pairwise_response["error"])
            except Exception as exc:
                result_payload["step_runs"]["pairwise"] = {
                    "status": "failed",
                    "runtime_summary": None,
                    "model_identity": None,
                    "error": _error_payload(exc),
                    "output_mode": None,
                    "output_schema_kind": None,
                }
                result_payload["pairwise_error"] = EvalError(
                    code=type(exc).__name__,
                    message=str(exc),
                )
        elif result_payload["step_runs"].get("pairwise") is None:
            result_payload["step_runs"]["pairwise"] = None

    return NodeLabJudgeRunResult.model_validate(result_payload)
