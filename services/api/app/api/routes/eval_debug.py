from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Header, HTTPException

from app.config.settings import get_settings
from app.eval_adapter.article_analysis import run_article_analysis_eval
from app.eval_adapter.node_probe import run_article_analysis_node_probe
from app.eval_adapter.shared import list_model_profile_summaries
from app.eval_adapter.schemas import (
    ArticleAnalysisEvalRequest,
    ArticleAnalysisEvalResult,
    ArticleAnalysisNodeProbeRequest,
    ArticleAnalysisNodeProbeResult,
    ModelProfileSummary,
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
