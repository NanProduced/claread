from __future__ import annotations

import asyncio
from time import perf_counter
from typing import Any

from app.config.settings import get_settings
from app.eval_adapter.schemas import (
    ArticleAnalysisEvalRequest,
    ArticleAnalysisEvalResult,
    EvalError,
    ModelIdentity,
    SchemaIdentity,
    WorkflowIdentity,
)
from app.eval_adapter.shared import (
    model_identity as build_model_identity,
)
from app.eval_adapter.shared import (
    prompt_identity as build_prompt_identity,
)
from app.eval_adapter.shared import (
    rag_override,
    trace_scope,
)
from app.eval_adapter.shared import (
    request_id as build_request_id,
)
from app.eval_adapter.shared import (
    request_snapshot as build_request_snapshot,
)
from app.llm.router import ModelSelectionError, validate_model_selection
from app.llm.routes import MODEL_ROUTE_ANNOTATION_GENERATION
from app.observability import SURFACE_EVAL_WORKFLOW_LAB, set_trace_surface
from app.schemas.analysis import AnalyzeRequest
from app.services.analysis.debug_snapshots import (
    build_academic_quality,
    build_drop_log_summary,
    build_normalize_summary,
    build_preprocess_summary,
    build_runtime_summary,
    build_trace_refs,
    resolve_workflow_identity,
)
from app.services.analysis.planning.goal_planner import build_goal_execution_plan
from app.services.analysis.prompting.prompt_loader import get_prompt_version
from app.services.analysis.prompting.runtime_context import (
    grammar_rag_enabled_override,
    prompt_runtime_override,
)
from app.workflow.analyze import ANALYZE_SCHEMA_VERSION, run_article_analysis_with_state


def _topology_mode(request: ArticleAnalysisEvalRequest) -> str:
    try:
        plan = build_goal_execution_plan(request.reading_goal, request.reading_variant)
    except Exception:
        return "unknown"
    topology = getattr(plan, "topology_mode", "unknown")
    return topology if topology in {"learning", "academic"} else "unknown"


def _render_schema_version(render_scene: Any) -> str | None:
    if render_scene is None:
        return None
    if isinstance(render_scene, dict):
        value = render_scene.get("schema_version")
    else:
        value = getattr(render_scene, "schema_version", None)
    return str(value) if value else None


def _warnings(result: dict[str, Any] | None) -> list[dict[str, Any]]:
    raw_warnings = result.get("warnings") if result else []
    warnings: list[dict[str, Any]] = []
    for warning in raw_warnings or []:
        if hasattr(warning, "model_dump"):
            warnings.append(warning.model_dump(mode="json"))
        elif isinstance(warning, dict):
            warnings.append(warning)
    return warnings


def _workflow_identity(result: dict[str, Any] | None, topology_mode: str) -> WorkflowIdentity:
    workflow_name, workflow_version = resolve_workflow_identity(result)
    return WorkflowIdentity(
        workflow_name=workflow_name,
        workflow_version=workflow_version,
        topology_mode=topology_mode if topology_mode in {"learning", "academic"} else "unknown",
    )


def _schema_identity(render_scene: Any, topology_mode: str) -> SchemaIdentity:
    return SchemaIdentity(
        schema_version=ANALYZE_SCHEMA_VERSION,
        render_schema_version=_render_schema_version(render_scene),
        topology_mode=topology_mode if topology_mode in {"learning", "academic"} else "unknown",
    )


def _runtime_summary(result: dict[str, Any] | None, *, latency_ms: int) -> dict[str, Any]:
    summary = build_runtime_summary(
        result.get("usage_summary") if result else None,
        latency_ms=latency_ms,
        billed_points=0,
    )
    summary.pop("billed_points", None)
    return summary


def _failure_result(
    request: ArticleAnalysisEvalRequest,
    *,
    request_id: str,
    status: str,
    error: BaseException,
    latency_ms: int,
    model_identity: ModelIdentity | None = None,
) -> ArticleAnalysisEvalResult:
    topology_mode = _topology_mode(request)
    return ArticleAnalysisEvalResult(
        status=status,
        error=EvalError(code=type(error).__name__, message=str(error)),
        request_snapshot=build_request_snapshot(request, request_id_value=request_id),
        workflow_identity=WorkflowIdentity(
            workflow_name="article_analysis",
            workflow_version="3.0.0",
            topology_mode=topology_mode if topology_mode in {"learning", "academic"} else "unknown",
        ),
        schema_identity=SchemaIdentity(
            schema_version=ANALYZE_SCHEMA_VERSION,
            topology_mode=topology_mode if topology_mode in {"learning", "academic"} else "unknown",
        ),
        prompt_identity=build_prompt_identity(request, prompt_version=get_prompt_version()),
        model_identity=model_identity,
        runtime_summary={"latency_ms": latency_ms},
        trace_refs=build_trace_refs(request_id=request_id),
    )


async def run_article_analysis_eval(
    request: ArticleAnalysisEvalRequest,
) -> ArticleAnalysisEvalResult:
    started_at = perf_counter()
    request_id = build_request_id(request)
    model_selection = request.model_selection
    model_identity: ModelIdentity | None = None

    try:
        # Execution entry guard: the model must be buildable, not just
        # resolvable, because we are about to actually call the LLM.
        validate_model_selection(
            get_settings(),
            model_selection,
            (MODEL_ROUTE_ANNOTATION_GENERATION,),
            buildable=True,
        )
        model_identity = build_model_identity(model_selection, settings=get_settings())
    except ModelSelectionError as exc:
        latency_ms = int((perf_counter() - started_at) * 1000)
        return _failure_result(
            request,
            request_id=request_id,
            status="failed",
            error=exc,
            latency_ms=latency_ms,
        )

    payload = AnalyzeRequest(
        text=request.text,
        reading_goal=request.reading_goal,
        reading_variant=request.reading_variant,
        source_type=request.source_type,
        request_id=request_id,
        model_selection=model_selection,
        extended=request.extended,
    )

    try:
        with (
            set_trace_surface(SURFACE_EVAL_WORKFLOW_LAB),
            prompt_runtime_override(request.prompt_override),
            grammar_rag_enabled_override(rag_override(request)),
            trace_scope(request),
        ):
            if request.timeout_seconds is None:
                result = await run_article_analysis_with_state(payload)
            else:
                result = await asyncio.wait_for(
                    run_article_analysis_with_state(payload),
                    timeout=request.timeout_seconds,
                )
    except TimeoutError as exc:
        latency_ms = int((perf_counter() - started_at) * 1000)
        return _failure_result(
            request,
            request_id=request_id,
            status="timeout",
            error=exc,
            latency_ms=latency_ms,
            model_identity=model_identity,
        )
    except Exception as exc:
        latency_ms = int((perf_counter() - started_at) * 1000)
        return _failure_result(
            request,
            request_id=request_id,
            status="failed",
            error=exc,
            latency_ms=latency_ms,
            model_identity=model_identity,
        )

    latency_ms = int((perf_counter() - started_at) * 1000)
    render_scene = result.get("render_scene")
    topology_mode = _topology_mode(request)

    return ArticleAnalysisEvalResult(
        status="succeeded",
        request_snapshot=build_request_snapshot(request, request_id_value=request_id),
        workflow_identity=_workflow_identity(result, topology_mode),
        schema_identity=_schema_identity(render_scene, topology_mode),
        prompt_identity=build_prompt_identity(request, prompt_version=get_prompt_version()),
        model_identity=model_identity,
        render_scene=render_scene,
        preprocess_summary=build_preprocess_summary(request.text, result),
        normalize_summary=build_normalize_summary(result),
        drop_log_summary=build_drop_log_summary(result),
        runtime_summary=_runtime_summary(result, latency_ms=latency_ms),
        academic_quality=build_academic_quality(result),
        rag_debug=result.get("rag_debug"),
        trace_refs=build_trace_refs(request_id=request_id),
        warnings=_warnings(result),
    )
