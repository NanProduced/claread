from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Header, HTTPException

from app.config.settings import get_settings
from app.eval_adapter.article_analysis import run_article_analysis_eval
from app.eval_adapter.node_lab import (
    compare_article_analysis_node_lab,
    get_node_lab_baseline_config,
    run_article_analysis_node_lab,
)
from app.eval_adapter.node_lab_judge import execute_node_lab_judge, run_node_lab_judge
from app.eval_adapter.node_probe import run_article_analysis_node_probe
from app.eval_adapter.shared import list_model_profile_summaries
from app.eval_adapter.workflow_lab import get_workflow_lab_baseline_bundle
from app.eval_adapter.example_lab import generate_rag_fields
from app.eval_adapter.schemas import (
    ArticleAnalysisEvalRequest,
    ArticleAnalysisEvalResult,
    ArticleAnalysisNodeLabCompareRequest,
    ArticleAnalysisNodeLabCompareResult,
    ArticleAnalysisNodeLabRunRequest,
    ArticleAnalysisNodeLabRunResult,
    ArticleAnalysisNodeProbeRequest,
    ArticleAnalysisNodeProbeResult,
    ExampleLabGenerateRagFieldsRequest,
    ExampleLabGenerateRagFieldsResult,
    ModelProfileSummary,
    NodeLabBaselineConfig,
    NodeLabBaselineConfigRequest,
    NodeLabJudgeExecuteRequest,
    NodeLabJudgeExecuteResult,
    NodeLabJudgeRunRequest,
    NodeLabJudgeRunResult,
    WorkflowLabBaselineBundle,
    WorkflowLabBaselineBundleRequest,
)

router = APIRouter(prefix="/eval", tags=["eval"])


async def verify_eval_api_key(x_admin_api_key: str = Header(...)) -> str:
    settings = get_settings()
    eval_api_key = settings.eval_admin_api_key
    if not eval_api_key:
        raise HTTPException(status_code=503, detail="Eval API not configured")
    if not secrets.compare_digest(x_admin_api_key, eval_api_key):
        raise HTTPException(status_code=401, detail="Invalid eval API key")
    return x_admin_api_key


@router.post(
    "/article-analysis/workflow",
    response_model=ArticleAnalysisEvalResult,
    summary="Run eval-only article analysis workflow",
)
async def article_analysis_workflow_eval(
    request: ArticleAnalysisEvalRequest,
    _auth: str = Depends(verify_eval_api_key),
) -> ArticleAnalysisEvalResult:
    return await run_article_analysis_eval(request)


@router.post(
    "/article-analysis/node-probe",
    response_model=ArticleAnalysisNodeProbeResult,
    summary="Run eval-only article analysis node probe",
)
async def article_analysis_node_probe(
    request: ArticleAnalysisNodeProbeRequest,
    _auth: str = Depends(verify_eval_api_key),
) -> ArticleAnalysisNodeProbeResult:
    return await run_article_analysis_node_probe(request)


@router.get(
    "/article-analysis/model-profiles",
    response_model=list[ModelProfileSummary],
    summary="List safe model profile summaries for eval-only tooling",
)
async def article_analysis_model_profiles(
    _auth: str = Depends(verify_eval_api_key),
) -> list[ModelProfileSummary]:
    return list_model_profile_summaries(settings=get_settings())


@router.post(
    "/article-analysis/node-lab/baseline",
    response_model=NodeLabBaselineConfig,
    summary="Resolve baseline node config for Node Lab",
)
async def article_analysis_node_lab_baseline(
    request: NodeLabBaselineConfigRequest,
    _auth: str = Depends(verify_eval_api_key),
) -> NodeLabBaselineConfig:
    return get_node_lab_baseline_config(request)


@router.post(
    "/article-analysis/workflow-lab/baseline-bundle",
    response_model=WorkflowLabBaselineBundle,
    summary="Resolve baseline workflow prompt bundle for Workflow Lab",
)
async def article_analysis_workflow_lab_baseline_bundle(
    request: WorkflowLabBaselineBundleRequest,
    _auth: str = Depends(verify_eval_api_key),
) -> WorkflowLabBaselineBundle:
    return get_workflow_lab_baseline_bundle(request)


@router.post(
    "/article-analysis/node-lab/run",
    response_model=ArticleAnalysisNodeLabRunResult,
    summary="Run eval-only article analysis Node Lab single run",
)
async def article_analysis_node_lab_run(
    request: ArticleAnalysisNodeLabRunRequest,
    _auth: str = Depends(verify_eval_api_key),
) -> ArticleAnalysisNodeLabRunResult:
    return await run_article_analysis_node_lab(request)


@router.post(
    "/article-analysis/node-lab/compare",
    response_model=ArticleAnalysisNodeLabCompareResult,
    summary="Run eval-only article analysis Node Lab baseline compare",
)
async def article_analysis_node_lab_compare(
    request: ArticleAnalysisNodeLabCompareRequest,
    _auth: str = Depends(verify_eval_api_key),
) -> ArticleAnalysisNodeLabCompareResult:
    return await compare_article_analysis_node_lab(request)


@router.post(
    "/article-analysis/node-lab/judge-execute",
    response_model=NodeLabJudgeExecuteResult,
    summary="Execute eval-only Node Lab judge call",
)
async def article_analysis_node_lab_judge_execute(
    request: NodeLabJudgeExecuteRequest,
    _auth: str = Depends(verify_eval_api_key),
) -> NodeLabJudgeExecuteResult:
    return await execute_node_lab_judge(request)


@router.post(
    "/article-analysis/node-lab/judge-run",
    response_model=NodeLabJudgeRunResult,
    summary="Run eval-only Node Lab judge flow from compare result evidence",
)
async def article_analysis_node_lab_judge_run(
    request: NodeLabJudgeRunRequest,
    _auth: str = Depends(verify_eval_api_key),
) -> NodeLabJudgeRunResult:
    return await run_node_lab_judge(request)


@router.post(
    "/article-analysis/example-lab/generate-rag-fields",
    response_model=ExampleLabGenerateRagFieldsResult,
    summary="Generate RAG fields for Example Lab entries",
)
async def example_lab_generate_rag_fields(
    request: ExampleLabGenerateRagFieldsRequest,
    _auth: str = Depends(verify_eval_api_key),
) -> ExampleLabGenerateRagFieldsResult:
    return await generate_rag_fields(
        sentence_text=request.sentence_text,
        output_fragment=request.output_fragment,
        reading_variant=request.reading_variant,
        model_profile=request.model_profile,
        timeout_seconds=request.timeout_seconds or 30.0,
    )
