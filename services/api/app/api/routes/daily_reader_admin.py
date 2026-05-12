"""每日精读管理端接口。"""

from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Header

from app.config.settings import get_settings
from app.schemas.daily_reader import (
    ArticleActionResponse,
    DailyReaderGenerateRequest,
    DailyReaderGenerateResponse,
    DailyReaderListResponse,
    DailyReaderPublishRequest,
    DailyReaderRetryRequest,
    DailyReaderUnpublishRequest,
    RetryWorkflowResponse,
)
from app.services.daily_reader import service

logger = logging.getLogger("app.api")

router = APIRouter(prefix="/daily-reader/admin", tags=["daily-reader-admin"])


async def verify_admin_api_key(x_admin_api_key: str = Header(...)) -> str:
    settings = get_settings()
    if not settings.daily_reader_admin_api_key:
        raise HTTPException(status_code=503, detail="Admin API not configured")
    if not secrets.compare_digest(x_admin_api_key, settings.daily_reader_admin_api_key):
        raise HTTPException(status_code=401, detail="Invalid admin API key")
    return x_admin_api_key


@router.post("/generate", response_model=DailyReaderGenerateResponse, summary="生成精读文章")
async def generate_articles(
    request: DailyReaderGenerateRequest,
    _auth: str = Depends(verify_admin_api_key),
) -> DailyReaderGenerateResponse:
    """执行精读文章生成流水线（异步），立即返回 task_id，可通过 /status 查询进度。"""
    import asyncio

    from app.services.daily_reader.pipeline import run_daily_pipeline
    from app.services.daily_reader.pipeline_tracker import PipelineRunTracker

    tracker = PipelineRunTracker()
    await tracker.start()

    async def _run():
        try:
            await run_daily_pipeline(
                max_count=request.max_count,
                force=request.force,
                tracker=tracker,
            )
        except Exception as e:
            logger.error("Async pipeline failed for run %s: %s", tracker.run_id, e, exc_info=True)
            await tracker.fail("pipeline", str(e))

    asyncio.create_task(_run())

    return DailyReaderGenerateResponse(
        task_id=tracker.run_id,
        status="running",
        message="Pipeline started, use /status to track progress",
    )


@router.get("/status/{run_id}", summary="查询 pipeline 执行进度")
async def pipeline_status(
    run_id: str,
    _auth: str = Depends(verify_admin_api_key),
) -> dict:
    """查询 pipeline 异步任务的执行进度。"""
    from app.database import connection as db_connection

    pool = db_connection.DB_POOL
    if pool is None:
        raise HTTPException(status_code=503, detail="Database not available")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM pipeline_runs WHERE id = $1",
            run_id,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return {
        "id": row["id"],
        "status": row["status"],
        "stage": row["stage"],
        "stage_detail": row["stage_detail"],
        "candidates_found": row["candidates_found"],
        "candidates_extracted": row["candidates_extracted"],
        "candidates_scored": row["candidates_scored"],
        "articles_generated": row["articles_generated"],
        "errors": row["errors"],
        "started_at": str(row["started_at"]) if row["started_at"] else None,
        "finished_at": str(row["finished_at"]) if row["finished_at"] else None,
    }


@router.post("/publish", response_model=ArticleActionResponse, summary="发布精读文章")
async def publish_article(
    request: DailyReaderPublishRequest,
    _auth: str = Depends(verify_admin_api_key),
) -> dict:
    """将草稿状态的精读文章发布上线。"""
    success = await service.publish_article(request.id)
    if not success:
        raise HTTPException(status_code=404, detail="Article not found or not in draft status")
    return {"status": "published"}


@router.post("/unpublish", response_model=ArticleActionResponse, summary="下架精读文章")
async def unpublish_article(
    request: DailyReaderUnpublishRequest,
    _auth: str = Depends(verify_admin_api_key),
) -> dict:
    """将已发布的精读文章下架为草稿。"""
    success = await service.unpublish_article(request.id)
    if not success:
        raise HTTPException(status_code=404, detail="Article not found or not published")
    return {"status": "unpublished"}


@router.delete("/{article_id}", response_model=ArticleActionResponse, summary="删除精读文章")
async def delete_article(
    article_id: str,
    _auth: str = Depends(verify_admin_api_key),
) -> dict:
    """删除草稿状态的精读文章。"""
    success = await service.delete_article(article_id)
    if not success:
        raise HTTPException(status_code=404, detail="Article not found or not in draft status")
    return {"status": "deleted"}


@router.get("/drafts", response_model=DailyReaderListResponse, summary="草稿列表")
async def list_drafts(
    limit: int = 20,
    _auth: str = Depends(verify_admin_api_key),
) -> DailyReaderListResponse:
    """获取草稿状态的精读文章列表。"""
    items = await service.get_draft_articles(limit=limit)
    return DailyReaderListResponse(items=items, has_more=False)


@router.post("/retry", response_model=RetryWorkflowResponse, summary="重试工作流")
async def retry_workflow(
    request: DailyReaderRetryRequest,
    _auth: str = Depends(verify_admin_api_key),
) -> dict:
    """对已有素材的精读文章重新执行 AI 工作流。"""
    from app.services.daily_reader.pipeline import run_workflow_only

    article = await service.get_article_by_id(request.id)
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")

    try:
        result = await run_workflow_only(request.id)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except Exception as e:
        logger.error("retry_workflow failed for %s: %s", request.id, e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Workflow execution failed",
        ) from e

    if result is None:
        return {"status": "retry_aborted", "message": "Workflow aborted; content may not be suitable"}

    return {"status": "retry_completed", "message": "Workflow re-executed successfully; content updated"}
