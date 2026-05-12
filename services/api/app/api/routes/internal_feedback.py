"""内部反馈管理接口，供云后台调用，使用 API Key 认证。"""

from __future__ import annotations

import secrets
from logging import getLogger
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException

from app.config.settings import get_settings
from app.schemas.feedback import (
    FeedbackRewardRequest,
    FeedbackRewardResponse,
    FeedbackStatsResponse,
    FeedbackStatusUpdateRequest,
    FeedbackStatusUpdateResponse,
)
from app.services.analysis.credit_service import grant_bonus_credits
from app.services.feedback import service as feedback_svc

logger = getLogger("app.api")

router = APIRouter(prefix="/internal/feedback", tags=["internal"])


def _verify_internal_key(x_internal_key: str | None) -> None:
    settings = get_settings()
    if not settings.internal_api_key:
        raise HTTPException(status_code=503, detail="Internal API not configured")
    if x_internal_key is None or not secrets.compare_digest(x_internal_key, settings.internal_api_key):
        raise HTTPException(status_code=403, detail="Invalid internal API key")


@router.patch("/{feedback_id}/status", response_model=FeedbackStatusUpdateResponse, summary="更新反馈状态")
async def update_feedback_status(
    feedback_id: UUID,
    body: FeedbackStatusUpdateRequest,
    x_internal_key: str | None = Header(default=None),
) -> dict:
    """更新反馈的处理状态（如标记为已采纳、已拒绝等）。"""
    _verify_internal_key(x_internal_key)
    row = await feedback_svc.update_feedback_status(
        feedback_id=feedback_id,
        status=body.status,
        admin_note=body.admin_note,
        reviewed_by=None,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Feedback not found")
    return row


@router.post("/{feedback_id}/reward", response_model=FeedbackRewardResponse, summary="奖励反馈")
async def reward_feedback(
    feedback_id: UUID,
    body: FeedbackRewardRequest,
    x_internal_key: str | None = Header(default=None),
) -> dict:
    """对已采纳的反馈发放积分奖励。"""
    _verify_internal_key(x_internal_key)
    row = await feedback_svc.reward_feedback(
        feedback_id=feedback_id,
        points=body.points,
    )
    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Feedback not found or already adopted",
        )

    user_id = row["user_id"]
    granted = await grant_bonus_credits(
        user_id=user_id,
        points=body.points,
        entry_type="feedback_reward",
        metadata={"feedback_id": str(feedback_id)},
    )
    return {
        "feedback_id": str(feedback_id),
        "user_id": str(user_id),
        "reward_points": body.points,
        "granted": granted,
    }


@router.get("/stats", response_model=FeedbackStatsResponse, summary="反馈统计")
async def get_feedback_stats(
    x_internal_key: str | None = Header(default=None),
) -> dict:
    """获取反馈的汇总统计数据。"""
    _verify_internal_key(x_internal_key)
    return await feedback_svc.get_feedback_stats()
