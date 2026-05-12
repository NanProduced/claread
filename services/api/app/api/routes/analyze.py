from logging import getLogger

from fastapi import APIRouter, HTTPException

from app.llm.router import ModelSelectionError
from app.schemas.analysis import AnalyzeRequest, AnyRenderSceneModel
from app.workflow.analyze import run_article_analysis

logger = getLogger("app.api")

router = APIRouter(prefix="/analyze", tags=["analyze"])


@router.post("", response_model=AnyRenderSceneModel, summary="分析文章")
async def analyze(payload: AnalyzeRequest) -> AnyRenderSceneModel:
    """提交文章分析任务，返回标注结果。"""
    try:
        return await run_article_analysis(payload)
    except ModelSelectionError as exc:
        logger.error("analyze ModelSelectionError: %s", exc, exc_info=True)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("analyze unexpected error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
