from __future__ import annotations

import asyncio
import logging
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langsmith import traceable

from app.agents.academic_translation_agent import AcademicTranslationAgentDeps
from app.agents.term_agent import TermAgentDeps
from app.agents.understanding_agent import UnderstandingAgentDeps
from app.config.settings import get_settings
from app.llm.agent_runner import extract_run_usage
from app.llm.router import resolve_model_config
from app.llm.routes import MODEL_ROUTE_ANNOTATION_GENERATION
from app.llm.runtime import get_model_selection
from app.llm.types import ModelSelection
from app.schemas.analysis import (
    AcademicRenderSceneModel,
    AnalyzeRequestMeta,
    ArticleStructure,
    Warning,
)
from app.schemas.internal.analysis import PreparedSentence
from app.services.analysis.postprocess.academic_normalize import academic_normalize_and_ground
from app.services.analysis.postprocess.academic_projection import project_to_academic_render_scene
from app.services.analysis.preprocess.input_preparation import prepare_input
from app.services.analysis.prompting.example_strategy import ExampleEntry
from app.services.analysis.prompting.prompt_loader import load_examples, load_policy_lines
from app.services.analysis.prompting.prompt_strategy import (
    PromptStrategy,
)
from app.services.analysis.runtime.academic_runners import (
    run_academic_translation_agent,
    run_term_agent,
    run_understanding_agent,
)
from app.workflow.academic_state import AcademicState
from app.workflow.tracing import build_llm_trace_metadata

logger = logging.getLogger(__name__)

ACADEMIC_WORKFLOW_NAME = "academic_article_analysis"
ACADEMIC_WORKFLOW_VERSION = "1.0.0"


def _model_selection(config: RunnableConfig | None) -> ModelSelection | None:
    return get_model_selection(config)


def _aggregate_usage_summary(
    usages: dict[str, dict[str, object] | None],
) -> dict[str, object]:
    per_agent = {name: usage for name, usage in usages.items() if usage}
    if not per_agent:
        return {
            "available": False,
            "per_agent": {},
            "aggregate": {
                "input_tokens": None,
                "output_tokens": None,
                "total_tokens": None,
            },
        }

    def _sum_token(field: str) -> int:
        return sum(int(usage.get(field, 0) or 0) for usage in per_agent.values())

    return {
        "available": True,
        "per_agent": per_agent,
        "aggregate": {
            "input_tokens": _sum_token("input_tokens"),
            "output_tokens": _sum_token("output_tokens"),
            "total_tokens": _sum_token("total_tokens"),
        },
    }


def _empty_academic_result(
    *,
    request_id: str,
    payload: Any,
    profile_id: str,
) -> AcademicRenderSceneModel:
    return AcademicRenderSceneModel(
        request=AnalyzeRequestMeta(
            request_id=request_id,
            source_type=payload.source_type,
            reading_goal=payload.reading_goal,
            reading_variant=payload.reading_variant,
            profile_id=profile_id,
        ),
        article=ArticleStructure(
            source_type=payload.source_type,
            source_text=payload.text,
            render_text="",
            paragraphs=[],
            sentences=[],
        ),
        translations=[],
        inline_marks=[],
        sentence_entries=[],
        warnings=[],
    )


def _build_agent_trace_metadata(
    state: AcademicState,
    node_name: str,
    model_selection: ModelSelection | None = None,
) -> dict[str, object]:
    payload = state["payload"]
    plan = state["goal_execution_plan"]
    model_config = resolve_model_config(
        get_settings(), MODEL_ROUTE_ANNOTATION_GENERATION, model_selection
    )
    return build_llm_trace_metadata(
        workflow_name=ACADEMIC_WORKFLOW_NAME,
        workflow_version=ACADEMIC_WORKFLOW_VERSION,
        request_id=payload.request_id or "",
        source_type=payload.source_type,
        reading_goal=payload.reading_goal,
        reading_variant=payload.reading_variant,
        profile_id=plan.prompt_profile,
        model_name=model_config.model_name if model_config else "unconfigured",
        model_provider=model_config.provider if model_config else "unconfigured",
        extra={
            "node": node_name,
            "model_profile": model_config.profile_name if model_config else "unconfigured",
            "sentence_count": len(state["prepared_input"].sentences),
        },
    )


async def prepare_input_node(state: AcademicState) -> AcademicState:
    payload = state["payload"]
    prepared_input = prepare_input(payload.text)
    warnings: list[Any] = []

    if not prepared_input.render_text.strip():
        return {
            "prepared_input": prepared_input,
            "warnings": warnings,
            "render_scene": _empty_academic_result(
                request_id=payload.request_id or "",
                payload=payload,
                profile_id="unresolved",
            ),
        }

    if prepared_input.english_ratio < 0.45 or not prepared_input.sentences:
        warnings.append(
            Warning(
                code="LOW_ENGLISH_RATIO",
                level="warning",
                message=f"英文占比 {prepared_input.english_ratio:.0%} 低于阈值，或无有效句子。",
            )
        )

    return {"prepared_input": prepared_input, "warnings": warnings}


async def derive_user_config_node(state: AcademicState) -> AcademicState:
    existing_plan = state.get("goal_execution_plan")
    if existing_plan is not None:
        return {}
    payload = state["payload"]
    from app.services.analysis.planning.goal_planner import build_goal_execution_plan
    plan = build_goal_execution_plan(payload.reading_goal, payload.reading_variant)
    return {"goal_execution_plan": plan}


@traceable(name="term_llm_call", run_type="llm")
async def _run_term_llm_span(
    *,
    deps: TermAgentDeps,
    metadata: dict[str, object],
    model_selection: ModelSelection | None = None,
) -> dict[str, Any]:
    result = await run_term_agent(deps, model_selection=model_selection)
    usage = extract_run_usage(result)
    return {"output": result.output if hasattr(result, "output") else result, "usage_metadata": usage}


@traceable(name="academic_translation_llm_call", run_type="llm")
async def _run_academic_translation_llm_span(
    *,
    deps: AcademicTranslationAgentDeps,
    metadata: dict[str, object],
    model_selection: ModelSelection | None = None,
) -> dict[str, Any]:
    result = await run_academic_translation_agent(deps, model_selection=model_selection)
    usage = extract_run_usage(result)
    return {"output": result.output if hasattr(result, "output") else result, "usage_metadata": usage}


async def parallel_term_translation_node(
    state: AcademicState, config: RunnableConfig
) -> AcademicState:
    prepared_input = state["prepared_input"]
    plan = state["goal_execution_plan"]
    model_sel = _model_selection(config)

    sentences_data = [
        {"sentence_id": s.sentence_id, "text": s.text}
        for s in prepared_input.sentences
    ]

    term_strategy = _build_term_prompt_strategy(plan)
    translation_strategy = _build_academic_translation_prompt_strategy(plan)

    term_deps = TermAgentDeps(
        sentences=sentences_data,
        prompt_strategy=term_strategy,
        examples=_get_term_examples(plan),
    )
    translation_deps = AcademicTranslationAgentDeps(
        sentences=sentences_data,
        prompt_strategy=translation_strategy,
        examples=_get_academic_translation_examples(plan),
    )

    term_meta = _build_agent_trace_metadata(state, "term_agent", model_sel)
    translation_meta = _build_agent_trace_metadata(state, "academic_translation_agent", model_sel)

    term_task = _run_term_llm_span(
        deps=term_deps, metadata=term_meta, model_selection=model_sel
    )
    translation_task = _run_academic_translation_llm_span(
        deps=translation_deps, metadata=translation_meta, model_selection=model_sel
    )

    results = await asyncio.gather(term_task, translation_task, return_exceptions=True)

    errors: list[Warning] = []
    if isinstance(results[0], Exception):
        logger.exception("term_agent 调用失败")
        errors.append(Warning(code="TERM_AGENT_FAILED", level="error", message=f"term agent 调用失败: {results[0]}"))
    if isinstance(results[1], Exception):
        logger.exception("academic_translation_agent 调用失败")
        errors.append(Warning(code="ACADEMIC_TRANSLATION_AGENT_FAILED", level="error", message=f"academic translation agent 调用失败: {results[1]}"))

    term_result = results[0] if not isinstance(results[0], Exception) else None
    translation_result = results[1] if not isinstance(results[1], Exception) else None

    term_output = term_result.get("output") if term_result else None
    translation_output = translation_result.get("output") if translation_result else None
    term_usage = term_result.get("usage_metadata") if term_result else None
    translation_usage = translation_result.get("usage_metadata") if translation_result else None

    usage_summary = _aggregate_usage_summary({
        "term": term_usage,
        "translation": translation_usage,
    })

    return {
        "term_draft": term_output,
        "translation_draft": translation_output,
        "term_usage": term_usage,
        "translation_usage": translation_usage,
        "usage_summary": usage_summary,
        "warnings": [*state.get("warnings", []), *errors],
    }


@traceable(name="understanding_llm_call", run_type="llm")
async def _run_understanding_llm_span(
    *,
    deps: UnderstandingAgentDeps,
    metadata: dict[str, object],
    model_selection: ModelSelection | None = None,
) -> dict[str, Any]:
    result = await run_understanding_agent(deps, model_selection=model_selection)
    usage = extract_run_usage(result)
    return {"output": result.output if hasattr(result, "output") else result, "usage_metadata": usage}


async def understanding_agent_node(
    state: AcademicState, config: RunnableConfig
) -> AcademicState:
    prepared_input = state["prepared_input"]
    plan = state["goal_execution_plan"]
    model_sel = _model_selection(config)
    term_draft = state.get("term_draft")

    sentences_data = [
        {"sentence_id": s.sentence_id, "text": s.text}
        for s in prepared_input.sentences
    ]

    understanding_strategy = _build_understanding_prompt_strategy(plan)

    term_draft_json = term_draft.model_dump(mode="json") if term_draft else None

    deps = UnderstandingAgentDeps(
        sentences=sentences_data,
        prompt_strategy=understanding_strategy,
        examples=_get_understanding_examples(plan),
        term_draft_json=term_draft_json,
    )

    meta = _build_agent_trace_metadata(state, "understanding_agent", model_sel)

    try:
        result = await _run_understanding_llm_span(
            deps=deps, metadata=meta, model_selection=model_sel
        )
        output = result.get("output")
        usage = result.get("usage_metadata")
        usage_summary = _aggregate_usage_summary({
            "term": state.get("term_usage"),
            "translation": state.get("translation_usage"),
            "understanding": usage,
        })
        return {
            "understanding_draft": output,
            "understanding_usage": usage,
            "usage_summary": usage_summary,
        }
    except Exception:
        logger.exception("understanding_agent 调用失败")
        usage_summary = _aggregate_usage_summary({
            "term": state.get("term_usage"),
            "translation": state.get("translation_usage"),
        })
        return {
            "understanding_draft": None,
            "warnings": [
                *state.get("warnings", []),
                Warning(code="UNDERSTANDING_AGENT_FAILED", level="error", message="understanding agent 调用失败"),
            ],
            "usage_summary": usage_summary,
        }


async def academic_normalize_node(state: AcademicState) -> AcademicState:
    payload = state["payload"]
    prepared_input = state["prepared_input"]
    term_draft = state.get("term_draft")
    translation_draft = state.get("translation_draft")
    understanding_draft = state.get("understanding_draft")
    plan = state.get("goal_execution_plan")

    if term_draft is None or translation_draft is None:
        profile_id = plan.prompt_profile if plan else "unresolved"
        return {
            "academic_normalized_result": None,
            "render_scene": _empty_academic_result(
                request_id=payload.request_id or "",
                payload=payload,
                profile_id=profile_id,
            ),
            "warnings": [
                *state.get("warnings", []),
                Warning(code="ACADEMIC_NORMALIZE_FAILED", level="error", message="term 或 translation agent 未返回有效结果，无法进行归一化"),
            ],
        }

    if understanding_draft is None:
        from app.schemas.internal.academic_drafts import UnderstandingDraft
        understanding_draft = UnderstandingDraft()

    sentences = [
        PreparedSentence.model_validate(s) if not isinstance(s, PreparedSentence) else s
        for s in prepared_input.sentences
    ]

    academic_policy = plan.academic_policy if plan and plan.academic_policy else None
    if academic_policy is None:
        from app.schemas.internal.execution_plan import AcademicGoalPolicy
        academic_policy = AcademicGoalPolicy()

    normalized_result = academic_normalize_and_ground(
        term_draft=term_draft,
        translation_draft=translation_draft,
        understanding_draft=understanding_draft,
        sentences=sentences,
        policy=academic_policy,
    )

    return {
        "academic_normalized_result": normalized_result,
        "drop_log": normalized_result.drop_log,
    }


async def academic_project_render_scene_node(state: AcademicState) -> AcademicState:
    payload = state["payload"]
    prepared_input = state["prepared_input"]
    normalized_result = state.get("academic_normalized_result")
    plan = state.get("goal_execution_plan")

    if normalized_result is None:
        return {
            "render_scene": _empty_academic_result(
                request_id=payload.request_id or "",
                payload=payload,
                profile_id=plan.prompt_profile if plan else "unresolved",
            )
        }

    projection_outcome = project_to_academic_render_scene(
        normalized_result=normalized_result,
        prepared_input=prepared_input,
        source_type=payload.source_type,
        reading_goal=payload.reading_goal,
        reading_variant=payload.reading_variant,
        profile_id=plan.prompt_profile if plan else "unknown",
        request_id=payload.request_id or "",
    )

    return {
        "render_scene": projection_outcome.result,
        "warnings": [*state.get("warnings", []), *[Warning(**w) for w in projection_outcome.warnings]],
    }


async def academic_assemble_result_node(state: AcademicState) -> AcademicState:
    render_scene = state.get("render_scene")
    normalized_result = state.get("academic_normalized_result")

    if render_scene is None:
        payload = state["payload"]
        plan = state.get("goal_execution_plan")
        return {
            "render_scene": _empty_academic_result(
                request_id=payload.request_id or "",
                payload=payload,
                profile_id=plan.prompt_profile if plan else "unresolved",
            )
        }

    existing_warnings = state.get("warnings", [])
    if existing_warnings and hasattr(render_scene, "warnings"):
        seen_keys = {(w.code, w.sentence_id) for w in render_scene.warnings}
        for w in existing_warnings:
            if (w.code, w.sentence_id) not in seen_keys:
                render_scene.warnings.append(w)
                seen_keys.add((w.code, w.sentence_id))

    heavy_failure_codes = {
        "TERM_AGENT_FAILED",
        "ACADEMIC_TRANSLATION_AGENT_FAILED",
        "UNDERSTANDING_AGENT_FAILED",
        "ACADEMIC_NORMALIZE_FAILED",
    }
    has_heavy_failure = any(w.code in heavy_failure_codes for w in render_scene.warnings)
    has_no_entries = len(render_scene.sentence_entries) == 0 and len(render_scene.inline_marks) == 0
    has_no_translations = len(render_scene.translations) == 0
    informational_codes = {"LOW_ENGLISH_RATIO", "HIGH_NOISE_RATIO", "UNSUPPORTED_TEXT_TYPE"}
    has_informational_only = len(render_scene.warnings) > 0 and all(
        w.code in informational_codes for w in render_scene.warnings
    )

    quality_degraded = (
        normalized_result is not None
        and normalized_result.quality_state == "degraded"
    )
    has_critical_quality_issue = (
        normalized_result is not None
        and any("translations_missing" in issue for issue in normalized_result.quality_issues)
    )

    if has_heavy_failure and (has_no_entries or has_no_translations):
        render_scene.user_facing_state = "degraded_heavy"
    elif has_critical_quality_issue:
        render_scene.user_facing_state = "degraded_heavy"
    elif quality_degraded:
        render_scene.user_facing_state = "degraded_light"
    elif len(render_scene.warnings) > 0 and not has_informational_only:
        render_scene.user_facing_state = "degraded_light"
    else:
        render_scene.user_facing_state = "normal"

    return {"render_scene": render_scene}


def _build_term_prompt_strategy(plan: Any) -> PromptStrategy:
    return PromptStrategy(
        profile_id=plan.prompt_profile,
        reading_goal=plan.goal_id,
        reading_variant=plan.variant_id,
        vocabulary_policy=plan.policy.vocabulary_focus,
        annotation_style="structural_and_academic",
        policy_lines=tuple(load_policy_lines("academic", "term")),
    )


def _build_academic_translation_prompt_strategy(plan: Any) -> PromptStrategy:
    return PromptStrategy(
        profile_id=plan.prompt_profile,
        reading_goal=plan.goal_id,
        reading_variant=plan.variant_id,
        translation_style="academic",
        policy_lines=tuple(load_policy_lines("academic", "academic_translation")),
    )


def _build_understanding_prompt_strategy(plan: Any) -> PromptStrategy:
    return PromptStrategy(
        profile_id=plan.prompt_profile,
        reading_goal=plan.goal_id,
        reading_variant=plan.variant_id,
        annotation_style="structural_and_academic",
        policy_lines=tuple(load_policy_lines("academic", "understanding")),
    )


def _get_term_examples(plan: Any) -> list[ExampleEntry]:
    raw = load_examples("academic", "term")
    return [ExampleEntry(example_type=e["example_type"], sentence_text=e["sentence_text"], output_fragment=e["output_fragment"]) for e in raw]


def _get_academic_translation_examples(plan: Any) -> list[ExampleEntry]:
    raw = load_examples("academic", "academic_translation")
    return [ExampleEntry(example_type=e["example_type"], sentence_text=e["sentence_text"], output_fragment=e["output_fragment"]) for e in raw]


def _get_understanding_examples(plan: Any) -> list[ExampleEntry]:
    raw = load_examples("academic", "understanding")
    return [ExampleEntry(example_type=e["example_type"], sentence_text=e["sentence_text"], output_fragment=e["output_fragment"]) for e in raw]


def build_academic_graph() -> Any:
    graph = StateGraph(AcademicState)

    graph.add_node("prepare_input", prepare_input_node)
    graph.add_node("derive_user_config", derive_user_config_node)
    graph.add_node("parallel_term_translation", parallel_term_translation_node)
    graph.add_node("understanding_agent", understanding_agent_node)
    graph.add_node("academic_normalize", academic_normalize_node)
    graph.add_node("academic_project_render_scene", academic_project_render_scene_node)
    graph.add_node("academic_assemble_result", academic_assemble_result_node)

    graph.add_edge(START, "prepare_input")
    graph.add_edge("prepare_input", "derive_user_config")
    graph.add_edge("derive_user_config", "parallel_term_translation")
    graph.add_edge("parallel_term_translation", "understanding_agent")
    graph.add_edge("understanding_agent", "academic_normalize")
    graph.add_edge("academic_normalize", "academic_project_render_scene")
    graph.add_edge("academic_project_render_scene", "academic_assemble_result")
    graph.add_edge("academic_assemble_result", END)

    return graph.compile()
