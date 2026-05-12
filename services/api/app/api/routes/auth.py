"""
认证 Proxy API。

提供微信登录入口和会话管理。
"""

from __future__ import annotations

from logging import getLogger
from uuid import UUID as PyUUID

from fastapi import APIRouter, HTTPException, Request

from app.schemas.auth import (
    LogoutRequest,
    LogoutResponse,
    ProfileUpdateRequest,
    ProfileUpdateResponse,
    SessionInfoResponse,
    WeChatLoginRequest,
    WeChatLoginResponse,
)
from app.services.auth import (
    create_session,
    get_or_create_user_by_wechat,
    revoke_session,
)
from app.services.auth.dependencies import AuthUserDep
from app.services.auth.profile import get_user_profile, update_user_profile
from app.services.auth.wechat import WeChatAPIError, code2session

logger = getLogger("app.api")

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/wechat/login", response_model=WeChatLoginResponse, summary="微信登录")
async def wechat_login(
    request: Request,
    body: WeChatLoginRequest,
) -> dict:
    """微信小程序登录，校验 code 并返回 session_token。"""
    code = body.code

    # 微信 code2Session
    try:
        wechat_session = await code2session(code)
    except WeChatAPIError as e:
        logger.error("wechat_login code2session failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=502,
            detail=f"WeChat service error: {e.errmsg}",
        ) from e

    # 提取客户端信息
    client_ip: str | None = None
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        client_ip = forwarded.split(",")[0].strip()
    else:
        client_ip = request.client.host if request.client else None

    # 查找或创建用户
    auth_payload = {
        "session_key": wechat_session.session_key,
        "unionid": wechat_session.unionid,
    }
    user_id = await get_or_create_user_by_wechat(
        openid=wechat_session.openid,
        unionid=wechat_session.unionid,
        auth_payload=auth_payload,
    )

    # 创建业务 session
    token, expires_at = await create_session(
        user_id=user_id,
        provider="wechat_miniprogram",
        provider_user_id=wechat_session.openid,
        auth_payload=auth_payload,
        client_platform="wechat_miniprogram",
        ip_address=client_ip,
    )

    return {
        "user_id": str(user_id),
        "session_token": token,
        "expires_at": expires_at.isoformat(),
    }


@router.post("/session/logout", response_model=LogoutResponse, summary="登出")
async def logout(
    body: LogoutRequest,
) -> dict:
    """登出当前 session，幂等操作。"""
    token = body.session_token

    await revoke_session(token)
    return {"ok": True}


@router.get("/session/me", response_model=SessionInfoResponse, summary="获取当前用户信息")
async def get_current_session_info(
    current_user: AuthUserDep,
) -> dict:
    """获取当前登录用户的会话信息和资料。"""
    try:
        profile = await get_user_profile(PyUUID(current_user.user_id))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail="Internal server error") from e

    if profile is None:
        raise HTTPException(status_code=404, detail="User not found")

    return {
        "user_id": current_user.user_id,
        "session_id": current_user.session_id,
        "nickname": profile["nickname"],
        "avatar_url": profile["avatar_url"],
        "cumulative_article_count": profile["cumulative_article_count"],
        "settings": profile["settings"],
    }


@router.patch("/profile", response_model=ProfileUpdateResponse, summary="更新用户资料")
async def update_profile(
    current_user: AuthUserDep,
    body: ProfileUpdateRequest,
) -> dict:
    """更新当前用户的昵称、头像或设置。"""
    user_id = PyUUID(current_user.user_id)

    try:
        updated_fields = await update_user_profile(
            user_id=user_id,
            nickname=body.nickname,
            avatar_url=body.avatar_url,
            settings=body.settings,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail="Internal server error") from e

    if not updated_fields:
        raise HTTPException(status_code=400, detail="No fields to update")

    logger.info("profile updated for user %s: %s", current_user.user_id, updated_fields)

    return {"ok": True, "updated": updated_fields}
