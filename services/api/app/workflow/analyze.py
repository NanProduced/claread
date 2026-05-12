"""V3 article_analysis workflow 入口。

负责根据 GoalExecutionPlan 的 topology_mode 进行拓扑分流。
"""

from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

from app.config.settings import get_settings
from app.llm.router import resolve_model_config, validate_model_selection
from app.llm.routes import MODEL_ROUTE_ANNOTATION_GENERATION
from app.llm.runtime import dump_model_selection
from app.llm.types import ModelSelection, parse_model_selection
from app.schemas.analysis import AcademicRenderSceneModel, AnalyzeRequest, AnyRenderSceneModel, RenderSceneModel
from app.services.analysis.planning.goal_planner import build_goal_execution_plan
from app.workflow.academic_workflow import build_academic_graph
from app.workflow.analyze_nodes import (
    WORKFLOW_NAME,
    WORKFLOW_VERSION,
)
from app.workflow.learning_workflow import build_learning_graph
from app.workflow.tracing import build_workflow_root_metadata, build_workflow_root_tags

ANALYZE_SCHEMA_VERSION = "3.0.0"


def _collect_model_names(settings: Any, model_selection: ModelSelection | None) -> list[str]:
    model_config = resolve_model_config(
        settings,
        MODEL_ROUTE_ANNOTATION_GENERATION,
        model_selection,
    )
    if model_config and model_config.model_name:
        return [model_config.model_name]
    return []


def _get_graph_for_plan(plan: Any) -> Any:
    """根据执行计划选择对应的图形实例。"""
    if plan.topology_mode == "learning":
        return build_learning_graph()
    elif plan.topology_mode == "academic":
        return build_academic_graph()
    else:
        raise ValueError(f"Unknown topology mode: {plan.topology_mode}")


async def _invoke_article_analysis(payload: AnalyzeRequest) -> dict[str, Any]:
    request_id = payload.request_id or str(uuid4())
    normalized_payload = (
        payload
        if payload.request_id
        else payload.model_copy(update={"request_id": request_id})
    )

    # 提前计算计划，并复用于整条工作流链路
    plan = build_goal_execution_plan(
        normalized_payload.reading_goal,
        normalized_payload.reading_variant,
    )

    graph = _get_graph_for_plan(plan)

    model_selection = parse_model_selection(normalized_payload.model_selection)
    validate_model_selection(
        get_settings(),
        model_selection,
        (MODEL_ROUTE_ANNOTATION_GENERATION,),
    )

    settings = get_settings()
    model_names = _collect_model_names(settings, model_selection)
    result = await graph.ainvoke(
        {
            "payload": normalized_payload,
            "goal_execution_plan": plan,
        },
        config={
            "run_name": WORKFLOW_NAME,
            "tags": build_workflow_root_tags(WORKFLOW_NAME, model_names),
            "configurable": {
                "model_selection": dump_model_selection(model_selection),
            },
            "metadata": build_workflow_root_metadata(
                workflow_name=WORKFLOW_NAME,
                workflow_version=WORKFLOW_VERSION,
                schema_version=ANALYZE_SCHEMA_VERSION,
                request_id=request_id,
                source_type=normalized_payload.source_type,
                reading_goal=normalized_payload.reading_goal,
                reading_variant=normalized_payload.reading_variant,
                profile_id=plan.prompt_profile,
                extra={
                    "model_preset": model_selection.preset if model_selection else None,
                    "runtime_model_selection": bool(model_selection),
                },
            ),
        },
    )
    return cast(dict[str, Any], result)


async def run_article_analysis(payload: AnalyzeRequest) -> AnyRenderSceneModel:
    result = await _invoke_article_analysis(payload)
    return cast(AnyRenderSceneModel, result["render_scene"])


async def run_article_analysis_with_state(payload: AnalyzeRequest) -> dict[str, Any]:
    return await _invoke_article_analysis(payload)


__all__ = [
    "ANALYZE_SCHEMA_VERSION",
    "WORKFLOW_NAME",
    "WORKFLOW_VERSION",
    "run_article_analysis",
    "run_article_analysis_with_state",
]
