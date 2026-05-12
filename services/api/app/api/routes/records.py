"""分析记录管理接口。"""

from __future__ import annotations

from logging import getLogger
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from app.schemas.user_assets.records import (
    RecordCreateRequest,
    RecordDeleteResponse,
    RecordListResponse,
    RecordResponse,
    RecordUpdateRequest,
    RecordUpsertResponse,
)
from app.services.auth.dependencies import AuthUserDep
from app.services.user_assets import records as records_svc

logger = getLogger("app.api")

router = APIRouter(prefix="/records", tags=["records"])


@router.post("", response_model=RecordUpsertResponse, summary="保存分析记录")
async def create_record(
    current_user: AuthUserDep,
    body: RecordCreateRequest,
) -> RecordUpsertResponse:
    """保存分析记录，按 client_record_id 去重更新。"""
    try:
        reading_goal = body.reading_goal or body.request_payload_json.get("reading_goal")
        reading_variant = body.reading_variant or body.request_payload_json.get("reading_variant")
        extended = body.extended or body.request_payload_json.get("extended", False)

        record_id, created, updated_at = await records_svc.upsert_record(
            user_id=UUID(current_user.user_id),
            client_record_id=body.client_record_id,
            source_type=body.source_type,
            title=body.title,
            source_text=body.source_text,
            source_text_hash=body.source_text_hash,
            reading_goal=reading_goal,
            reading_variant=reading_variant,
            extended=extended,
            user_facing_state=body.user_facing_state,
            analysis_status=body.analysis_status,
            render_scene_json=body.render_scene_json,
            page_state_json=body.page_state_json,
            workflow_version=body.workflow_version,
            schema_version=body.schema_version,
        )
        return RecordUpsertResponse(
            id=record_id,
            client_record_id=body.client_record_id,
            created=created,
            updated_at=updated_at,
        )
    except Exception as e:
        logger.error("create_record failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.get("", response_model=RecordListResponse, summary="分析记录列表")
async def list_records(
    current_user: AuthUserDep,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    include_render_scene: bool = Query(default=False),
) -> RecordListResponse:
    """分页获取当前用户的分析记录列表。"""
    try:
        items, total = await records_svc.list_records(
            user_id=UUID(current_user.user_id),
            page=page,
            limit=limit,
            include_content=include_render_scene,
        )
        return RecordListResponse(
            items=[RecordResponse(**row) for row in items],
            total=total,
            page=page,
            limit=limit,
        )
    except Exception as e:
        logger.error("list_records failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.get("/by-client-id/{client_record_id}", response_model=RecordResponse, summary="按客户端ID查询记录")
async def get_record_by_client_id(
    current_user: AuthUserDep,
    client_record_id: str,
) -> RecordResponse:
    """根据 client_record_id 获取单条分析记录。"""
    try:
        record = await records_svc.get_record_by_client_id(
            user_id=UUID(current_user.user_id),
            client_record_id=client_record_id,
        )
        if record is None:
            raise HTTPException(status_code=404, detail="Record not found")
        return RecordResponse(**record)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("get_record_by_client_id failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.get("/{record_id}", response_model=RecordResponse, summary="分析记录详情")
async def get_record(
    current_user: AuthUserDep,
    record_id: UUID,
) -> RecordResponse:
    """根据 ID 获取单条分析记录。"""
    try:
        record = await records_svc.get_record_by_id(
            user_id=UUID(current_user.user_id),
            record_id=record_id,
        )
        if record is None:
            raise HTTPException(status_code=404, detail="Record not found")
        return RecordResponse(**record)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("get_record failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.patch("/{record_id}", response_model=RecordResponse, summary="更新分析记录")
async def update_record(
    current_user: AuthUserDep,
    record_id: UUID,
    body: RecordUpdateRequest,
) -> RecordResponse:
    """部分更新分析记录的字段。"""
    try:
        updated = await records_svc.update_record(
            user_id=UUID(current_user.user_id),
            record_id=record_id,
            **body.model_dump(exclude_none=True),
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="Record not found")
        return RecordResponse(**updated)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("update_record failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.delete("/{record_id}", response_model=RecordDeleteResponse, summary="删除分析记录")
async def delete_record(
    current_user: AuthUserDep,
    record_id: UUID,
) -> dict:
    """删除一条分析记录（幂等：已删除的记录重复删除返回成功）。"""
    try:
        result = await records_svc.delete_record(
            user_id=UUID(current_user.user_id),
            record_id=record_id,
        )
        if result == "missing":
            raise HTTPException(status_code=404, detail="Record not found")
        return {"deleted": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("delete_record failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error") from e
