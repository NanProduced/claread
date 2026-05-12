"""用户额度与积分接口。"""

from __future__ import annotations

from datetime import date, datetime, timezone
from logging import getLogger
from uuid import UUID

from fastapi import APIRouter, HTTPException

from app.schemas.quota import (
    AnonymousQuotaResponse,
    LedgerEntryResponse,
    LedgerListResponse,
    QuotaCheckRequest,
    QuotaCheckResponse,
    QuotaResponse,
)
from app.services.analysis.credit_service import ensure_credit_account, get_quota_info
from app.services.auth.dependencies import AuthUserDep, OptionalAuthUserDep
from app.services.quota import get_anonymous_quota_info, check_and_consume_anonymous_trial
from app.services.quota.ledger import get_credit_ledger

logger = getLogger("app.api")

router = APIRouter(prefix="/me", tags=["user"])


@router.get("/quota", response_model=QuotaResponse, summary="用户额度")
async def get_user_quota(
    current_user: AuthUserDep,
) -> QuotaResponse:
    """获取当前用户的积分额度信息。"""
    try:
        user_id = UUID(current_user.user_id)
        await ensure_credit_account(user_id)
        info = await get_quota_info(user_id)
        return QuotaResponse(**info)
    except Exception as e:
        logger.error("get_user_quota failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.get("/quota/anonymous", response_model=AnonymousQuotaResponse, summary="游客额度")
async def get_anonymous_quota(
    anonymous_id: str,
) -> AnonymousQuotaResponse:
    """获取匿名游客的试用额度信息。"""
    try:
        info = await get_anonymous_quota_info(anonymous_id)
        return AnonymousQuotaResponse(**info)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.post("/quota/check", response_model=QuotaCheckResponse, summary="额度检查")
async def check_quota(
    current_user: OptionalAuthUserDep = None,
    body: QuotaCheckRequest | None = None,
) -> QuotaCheckResponse:
    """检查用户是否有剩余额度，支持登录用户和游客。"""
    if current_user is not None:
        try:
            user_id = UUID(current_user.user_id)
            await ensure_credit_account(user_id)
            info = await get_quota_info(user_id)
            return QuotaCheckResponse(
                allowed=info["remaining_points"] > 0,
                remaining=info["remaining_points"],
                reset_at=_get_next_reset_iso(),
                quota_type="authenticated",
            )
        except Exception as e:
            logger.error("check_quota authenticated failed: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail="Internal server error") from e

    if body is None or not body.anonymous_id:
        raise HTTPException(status_code=400, detail="anonymous_id required for guest users")

    try:
        remaining, reset_at = await check_and_consume_anonymous_trial(body.anonymous_id)
        return QuotaCheckResponse(
            allowed=remaining > 0,
            remaining=remaining,
            reset_at=reset_at,
            quota_type="anonymous",
        )
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail="Internal server error") from e
    except Exception as e:
        logger.error("check_quota anonymous failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error") from e


def _get_next_reset_iso() -> str:
    tomorrow = date.today()
    return datetime.combine(tomorrow, datetime.min.time()).astimezone(timezone.utc).isoformat()


@router.get("/credit/ledger", response_model=LedgerListResponse, summary="积分流水")
async def get_credit_ledger_endpoint(
    current_user: AuthUserDep,
    cursor: str | None = None,
    limit: int = 20,
) -> LedgerListResponse:
    """分页获取当前用户的积分变动流水记录。"""
    try:
        user_id = UUID(current_user.user_id)
        cursor_uuid = UUID(cursor) if cursor else None
        result = await get_credit_ledger(user_id, cursor=cursor_uuid, limit=limit)
        return LedgerListResponse(
            items=[LedgerEntryResponse(**item) for item in result["items"]],
            cursor=result["cursor"],
            has_more=result["has_more"],
        )
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail="Internal server error") from e
