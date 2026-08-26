"""冻结图片 source URL override 的 PUT/DELETE 路由。

API 最终形状 = PUT + DELETE（有意推迟 GET override
endpoint；raw override 的读取通道是 snapshot tree 的 ``override_url``
字段）。错误映射沿用 404 collapse 惯例。
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.schemas.reader_image_overrides import (
    ImageSourceOverrideMutationResponse,
    ImageSourceOverrideUpsertRequest,
)
from app.services.auth.dependencies import AuthUserDep
from app.services.reader_image_overrides import (
    ImageSourceOverrideError,
    StableImageSourceOverrideService,
)

router = APIRouter(prefix="/reader", tags=["reader"])

_ERROR_MESSAGES = {
    "not_found": "未找到对应的阅读记录或图片定位。",
    "stable_document_not_active": "目标 Stable Document 已被新版本取代。",
    "image_block_not_found": "未找到对应的图片块。",
    "image_target_not_found": "定位不是有效的图片目标。",
    "url_null_character_not_persistable": "URL 含不可持久化字符。",
    "block_id_null_character_not_persistable": "block_id 含不可持久化字符。",
    "url_not_representable_as_postgres_text": "URL 含不可表示字符。",
    "block_id_not_representable_as_postgres_text": "block_id 含不可表示字符。",
}


def _error_response(exc: ImageSourceOverrideError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "ok": False,
            "code": exc.code,
            "message": _ERROR_MESSAGES.get(exc.code, exc.code),
        },
    )


@router.put(
    "/records/{record_id}/image-source-overrides",
    response_model=ImageSourceOverrideMutationResponse,
    responses={
        401: {"description": "Unauthenticated (existing auth mechanism)."},
        404: {
            "description": "Collapsed: record not found / not owner / "
            "deleted / document not in record; or image_block_not_found."
        },
        409: {"description": "stable_document_not_active (superseded)."},
        422: {
            "description": "image_target_not_found / "
            "url_null_character_not_persistable / url_not_representable / "
            "block_id_not_representable / schema 层拒绝。"
        },
    },
    summary="Upsert the frozen image source URL override for a stable locator",
)
async def put_image_source_override(
    record_id: UUID,
    body: ImageSourceOverrideUpsertRequest,
    current_user: AuthUserDep,
) -> ImageSourceOverrideMutationResponse | JSONResponse:
    service = StableImageSourceOverrideService()
    try:
        sequence = await service.upsert_override(
            record_id=record_id,
            user_id=UUID(current_user.user_id),
            stable_document_id=body.stable_document_id,
            block_id=body.block_id,
            inline_ordinal=body.inline_ordinal,
            url=body.url,
        )
    except ImageSourceOverrideError as exc:
        return _error_response(exc)
    return ImageSourceOverrideMutationResponse(last_event_sequence=sequence)


@router.delete(
    "/records/{record_id}/image-source-overrides/{stable_document_id}/{block_id}",
    response_model=ImageSourceOverrideMutationResponse,
    responses={
        401: {"description": "Unauthenticated (existing auth mechanism)."},
        404: {
            "description": "Collapsed: record not found / not owner / "
            "deleted / document not in record; or image_block_not_found."
        },
        409: {"description": "stable_document_not_active (superseded)."},
        422: {
            "description": "image_target_not_found / block_id_not_representable / schema 层拒绝。"
        },
    },
    summary="Delete the image source URL override (query 缺省 = standalone)",
)
async def delete_image_source_override(
    record_id: UUID,
    stable_document_id: UUID,
    block_id: str,
    current_user: AuthUserDep,
    inline_ordinal: int | None = Query(default=None, ge=0),
) -> ImageSourceOverrideMutationResponse | JSONResponse:
    service = StableImageSourceOverrideService()
    try:
        sequence = await service.delete_override(
            record_id=record_id,
            user_id=UUID(current_user.user_id),
            stable_document_id=stable_document_id,
            block_id=block_id,
            inline_ordinal=inline_ordinal,
        )
    except ImageSourceOverrideError as exc:
        return _error_response(exc)
    return ImageSourceOverrideMutationResponse(last_event_sequence=sequence)
