"""收藏管理接口。"""

from __future__ import annotations

from logging import getLogger
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from app.schemas.user_assets.favorites import (
    FavoriteCreateRequest,
    FavoriteCreateResponse,
    FavoriteDeleteResponse,
    FavoriteListResponse,
    FavoriteResponse,
    FavoriteTargetType,
)
from app.services.auth.dependencies import AuthUserDep
from app.services.user_assets import favorites as fav_svc

logger = getLogger("app.api")

router = APIRouter(prefix="/favorites", tags=["favorites"])


@router.post("", response_model=FavoriteCreateResponse, summary="添加收藏")
async def add_favorite(
    current_user: AuthUserDep,
    body: FavoriteCreateRequest,
) -> dict:
    """收藏一个目标，按 target_type + target_key 去重。"""
    try:
        fav_id = await fav_svc.add_favorite(
            user_id=UUID(current_user.user_id),
            target_type=body.target_type,
            target_key=body.target_key,
            analysis_record_id=body.analysis_record_id,
            payload_json=body.payload_json,
            note=body.note,
        )
        return {"id": str(fav_id), "ok": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("add_favorite failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.get("", response_model=FavoriteListResponse, summary="收藏列表")
async def list_favorites(
    current_user: AuthUserDep,
) -> FavoriteListResponse:
    """获取当前用户的所有收藏记录。"""
    try:
        items = await fav_svc.list_favorites(
            user_id=UUID(current_user.user_id),
        )
        return FavoriteListResponse(
            items=[FavoriteResponse(**row) for row in items],
            total=len(items),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("list_favorites failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.delete("/target", response_model=FavoriteDeleteResponse, summary="按目标取消收藏")
async def remove_favorite_by_target(
    current_user: AuthUserDep,
    target_type: Annotated[FavoriteTargetType, Query()],
    target_key: str = Query(min_length=1, max_length=256),
) -> FavoriteDeleteResponse:
    """根据 target_type + target_key 取消收藏。"""
    try:
        deleted = await fav_svc.remove_favorite(
            user_id=UUID(current_user.user_id),
            target_type=target_type,
            target_key=target_key,
        )
        return FavoriteDeleteResponse(deleted=deleted)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("remove_favorite_by_target failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.delete("/{analysis_record_id}", response_model=FavoriteDeleteResponse, summary="取消收藏")
async def remove_favorite(
    current_user: AuthUserDep,
    analysis_record_id: UUID,
) -> FavoriteDeleteResponse:
    """根据分析记录 ID 取消收藏。"""
    try:
        count = await fav_svc.remove_favorite_by_analysis_record(
            user_id=UUID(current_user.user_id),
            analysis_record_id=analysis_record_id,
        )
        return FavoriteDeleteResponse(deleted=count > 0)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("remove_favorite failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error") from e
