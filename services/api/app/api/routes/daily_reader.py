"""每日精读用户端接口。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.schemas.daily_reader import (
    DailyReaderArticleResponse,
    DailyReaderListResponse,
    DailyReaderTodayResponse,
)
from app.services.daily_reader.service import (
    get_article_by_id,
    get_today_articles,
    list_articles,
)

router = APIRouter(prefix="/daily-reader", tags=["daily-reader"])


@router.get("/today", response_model=DailyReaderTodayResponse, summary="获取今日精读")
async def today_articles() -> DailyReaderTodayResponse:
    """获取今日发布的精读文章列表。"""
    articles = await get_today_articles()
    return DailyReaderTodayResponse(articles=articles)


@router.get("", response_model=DailyReaderListResponse, summary="精读文章列表")
async def article_list(
    cursor: str | None = None,
    limit: int = 10,
) -> DailyReaderListResponse:
    """分页获取精读文章列表，支持游标翻页。"""
    return await list_articles(cursor=cursor, limit=limit)


@router.get("/{article_id}", response_model=DailyReaderArticleResponse, summary="精读文章详情")
async def article_detail(article_id: str) -> DailyReaderArticleResponse:
    """根据 ID 获取单篇精读文章详情。"""
    article = await get_article_by_id(article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    return article
