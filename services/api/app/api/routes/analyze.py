from logging import getLogger
from time import perf_counter

from fastapi import APIRouter, HTTPException

from app.config.settings import get_settings
from app.llm.router import ModelSelectionError
from app.llm.routes import MODEL_ROUTE_ANNOTATION_GENERATION
from app.llm.types import parse_model_selection
from app.schemas.analysis import AnalyzeRequest, AnyRenderSceneModel
from app.services.ai_usage import (
    AIUsageEventCreate,
    BILLING_MODE_NO_CHARGE,
    BILLING_MODE_TRIAL,
    CAPABILITY_ANALYSIS_FULL,
    STATUS_FAILED,
    STATUS_SUCCEEDED,
    USAGE_SCOPE_ANONYMOUS_TRIAL,
    USAGE_SCOPE_EVAL_DEBUG,
    extract_request_id_from_render_scene,
    extract_schema_version_from_render_scene,
    record_ai_usage_event,
    resolve_model_metadata,
)
from app.services.analysis.prompting.prompt_loader import get_prompt_version
from app.workflow.analyze import (
    ANALYZE_SCHEMA_VERSION,
    WORKFLOW_NAME,
    WORKFLOW_VERSION,
    run_article_analysis_with_state,
)

logger = getLogger("app.api")

router = APIRouter(prefix="/analyze", tags=["analyze"])


def _direct_usage_scope(payload: AnalyzeRequest) -> str:
    return USAGE_SCOPE_EVAL_DEBUG if payload.model_selection else USAGE_SCOPE_ANONYMOUS_TRIAL


def _direct_billing_mode(payload: AnalyzeRequest) -> str:
    return BILLING_MODE_NO_CHARGE if payload.model_selection else BILLING_MODE_TRIAL


def _direct_event_metadata(payload: AnalyzeRequest) -> dict[str, object]:
    return {
        "entrypoint": "/analyze",
        "contract_role": "anonymous_trial_or_debug_direct",
        "runtime_model_selection": bool(payload.model_selection),
        "source_type": payload.source_type,
        "reading_goal": payload.reading_goal,
        "reading_variant": payload.reading_variant,
        "extended": payload.extended,
    }


@router.post("", response_model=AnyRenderSceneModel, summary="Direct analysis compatibility route")
async def analyze(payload: AnalyzeRequest) -> AnyRenderSceneModel:
    """
    Compatibility route for anonymous trial and debug-only direct analysis.

    This path intentionally stays outside the authenticated task/quota pipeline.
    New user-facing AI capabilities should not expand on top of this route.
    """
    started_at = perf_counter()
    request_id = payload.request_id
    model_metadata = resolve_model_metadata(get_settings(), MODEL_ROUTE_ANNOTATION_GENERATION)
    event_metadata = _direct_event_metadata(payload)

    try:
        selection = parse_model_selection(payload.model_selection)
        model_metadata = resolve_model_metadata(
            get_settings(),
            MODEL_ROUTE_ANNOTATION_GENERATION,
            selection,
        )

        result = await run_article_analysis_with_state(payload)
        render_scene = result["render_scene"]
        request_id = extract_request_id_from_render_scene(render_scene) or request_id

        await record_ai_usage_event(
            AIUsageEventCreate(
                usage_scope=_direct_usage_scope(payload),
                capability_code=CAPABILITY_ANALYSIS_FULL,
                billing_mode=_direct_billing_mode(payload),
                status=STATUS_SUCCEEDED,
                request_id=request_id,
                workflow_name=WORKFLOW_NAME,
                workflow_version=WORKFLOW_VERSION,
                schema_version=extract_schema_version_from_render_scene(render_scene)
                or ANALYZE_SCHEMA_VERSION,
                prompt_version=get_prompt_version(),
                usage_data=result.get("usage_summary"),
                latency_ms=int((perf_counter() - started_at) * 1000),
                metadata_json=event_metadata,
                **model_metadata,
            )
        )
        return render_scene
    except ModelSelectionError as exc:
        await record_ai_usage_event(
            AIUsageEventCreate(
                usage_scope=_direct_usage_scope(payload),
                capability_code=CAPABILITY_ANALYSIS_FULL,
                billing_mode=_direct_billing_mode(payload),
                status=STATUS_FAILED,
                request_id=request_id,
                workflow_name=WORKFLOW_NAME,
                workflow_version=WORKFLOW_VERSION,
                schema_version=ANALYZE_SCHEMA_VERSION,
                prompt_version=get_prompt_version(),
                latency_ms=int((perf_counter() - started_at) * 1000),
                error_code=type(exc).__name__,
                error_message=str(exc),
                metadata_json=event_metadata,
                **model_metadata,
            )
        )
        logger.error("analyze ModelSelectionError: %s", exc, exc_info=True)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        await record_ai_usage_event(
            AIUsageEventCreate(
                usage_scope=_direct_usage_scope(payload),
                capability_code=CAPABILITY_ANALYSIS_FULL,
                billing_mode=_direct_billing_mode(payload),
                status=STATUS_FAILED,
                request_id=request_id,
                workflow_name=WORKFLOW_NAME,
                workflow_version=WORKFLOW_VERSION,
                schema_version=ANALYZE_SCHEMA_VERSION,
                prompt_version=get_prompt_version(),
                latency_ms=int((perf_counter() - started_at) * 1000),
                error_code=type(exc).__name__,
                error_message=str(exc),
                metadata_json=event_metadata,
                **model_metadata,
            )
        )
        logger.error("analyze unexpected error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
