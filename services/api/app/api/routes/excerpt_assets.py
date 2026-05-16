from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from app.schemas.excerpt_assets import (
    ExcerptAnchorType,
    ExcerptAssetsResponse,
    ExcerptAssetState,
)
from app.services import excerpt_assets as svc
from app.services.auth.dependencies import AuthUserDep

router = APIRouter(prefix="/excerpt-assets", tags=["excerpt-assets"])


@router.get("", response_model=ExcerptAssetsResponse, summary="摘录资产聚合列表")
async def list_excerpt_assets(
    current_user: AuthUserDep,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    record_id: str | None = Query(default=None),
    asset_state: ExcerptAssetState = Query(default="all"),
    anchor_type: ExcerptAnchorType | None = Query(default=None),
) -> ExcerptAssetsResponse:
    try:
        return await svc.list_excerpt_assets(
            user_id=UUID(current_user.user_id),
            page=page,
            limit=limit,
            record_id=record_id,
            asset_state=asset_state,
            anchor_type=anchor_type,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to load excerpt assets") from exc
