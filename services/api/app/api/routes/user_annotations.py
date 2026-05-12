from uuid import UUID
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query

from app.services.auth.dependencies import AuthUserDep
from app.schemas.user_annotations import (
    UserAnnotationCreateRequest,
    UserAnnotationUpdateRequest,
    UserAnnotationResponse,
    UserAnnotationListResponse
)
from app.services import user_annotations as svc

router = APIRouter(prefix="/user-annotations", tags=["User Annotations"])


@router.post("", response_model=UserAnnotationResponse)
async def create_annotation(
    req: UserAnnotationCreateRequest,
    current_user: AuthUserDep
) -> UserAnnotationResponse:
    return await svc.create_user_annotation(UUID(current_user.user_id), req)


@router.get("", response_model=UserAnnotationListResponse)
async def list_annotations(
    current_user: AuthUserDep,
    record_id: Optional[str] = Query(None, alias="analysis_record_id", description="Filter by specific record ID"),
    limit: int = Query(50, ge=1, le=200, description="Max items to return"),
    offset: int = Query(0, ge=0, description="Number of items to skip"),
) -> UserAnnotationListResponse:
    items = await svc.list_user_annotations(UUID(current_user.user_id), record_id, limit, offset)
    return UserAnnotationListResponse(items=items)


@router.patch("/{annotation_id}", response_model=UserAnnotationResponse)
async def update_annotation(
    annotation_id: UUID,
    req: UserAnnotationUpdateRequest,
    current_user: AuthUserDep
) -> UserAnnotationResponse:
    return await svc.update_user_annotation(UUID(current_user.user_id), annotation_id, req)


@router.delete("/{annotation_id}")
async def delete_annotation(
    annotation_id: UUID,
    current_user: AuthUserDep
) -> dict[str, bool]:
    await svc.delete_user_annotation(UUID(current_user.user_id), annotation_id)
    return {"ok": True}
