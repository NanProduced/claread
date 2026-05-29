from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException

from app.schemas.reader_scene import ReaderSceneResponse
from app.services import reader_scene as reader_scene_svc
from app.services.auth.dependencies import AuthUserDep

router = APIRouter(prefix="/reader", tags=["reader"])


@router.get("/records/{record_id}/scene", response_model=ReaderSceneResponse, summary="阅读页专用 scene 视图")
async def get_reader_scene(
    current_user: AuthUserDep,
    record_id: UUID,
) -> ReaderSceneResponse:
    try:
        return await reader_scene_svc.get_reader_scene_by_id(UUID(current_user.user_id), record_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@router.get(
    "/records/by-client-id/{client_record_id}/scene",
    response_model=ReaderSceneResponse,
    summary="按客户端 ID 获取阅读页专用 scene 视图",
)
async def get_reader_scene_by_client_id(
    current_user: AuthUserDep,
    client_record_id: str,
) -> ReaderSceneResponse:
    try:
        return await reader_scene_svc.get_reader_scene_by_client_id(
            UUID(current_user.user_id),
            client_record_id,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Internal server error") from exc
