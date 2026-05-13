"""
认证 Proxy API。

提供微信登录入口和会话管理。
"""

from __future__ import annotations

from logging import getLogger
from uuid import UUID as PyUUID

from fastapi import APIRouter, HTTPException, Request

from app.schemas.auth import (
    IdentityBindResponse,
    LogoutRequest,
    LogoutResponse,
    PhoneBindRequest,
    PhoneCodeRequest,
    PhoneCodeResponse,
    PhoneVerifyRequest,
    ProfileUpdateRequest,
    ProfileUpdateResponse,
    SessionInfoResponse,
    WeChatBindRequest,
    WeChatLoginRequest,
    WeChatLoginResponse,
)
from app.services.auth import (
    PhoneAuthError,
    bind_phone_to_user,
    create_session,
    get_or_create_user_by_phone,
    get_or_create_user_by_wechat,
    request_phone_code,
    revoke_session,
    verify_phone_code,
)
from app.services.auth.dependencies import AuthUserDep
from app.services.auth.identity import IdentityConflictError, bind_identity_to_user
from app.services.auth.profile import get_user_profile, update_user_profile
from app.services.auth.wechat import WeChatAPIError, code2session

logger = getLogger("app.api")

router = APIRouter(prefix="/auth", tags=["auth"])


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def _identity_conflict_response(error: IdentityConflictError) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "error": "identity_conflict",
            "provider": error.provider,
            "message": "Identity is already bound to another Claread user.",
        },
    )


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
    client_ip = _client_ip(request)

    # 查找或创建用户
    auth_payload = {
        "session_key": wechat_session.session_key,
        "unionid": wechat_session.unionid,
    }
    try:
        user_id = await get_or_create_user_by_wechat(
            openid=wechat_session.openid,
            unionid=wechat_session.unionid,
            auth_payload=auth_payload,
        )
    except IdentityConflictError as e:
        raise _identity_conflict_response(e) from e

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


@router.post(
    "/phone/request-code",
    response_model=PhoneCodeResponse,
    summary="发送手机号验证码",
)
async def phone_request_code(body: PhoneCodeRequest) -> PhoneCodeResponse:
    """发送手机号验证码。开发期 provider=mock，验证码固定为 888888。"""
    try:
        result = await request_phone_code(body.phone)
    except PhoneAuthError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message) from e

    return PhoneCodeResponse(
        ok=True,
        message=result.message,
        normalized_phone=result.normalized_phone,
    )


@router.post(
    "/phone/verify-code",
    response_model=WeChatLoginResponse,
    summary="手机号验证码登录",
)
async def phone_verify_code(
    request: Request,
    body: PhoneVerifyRequest,
) -> WeChatLoginResponse:
    """验证码核验成功后创建/复用 phone identity，并创建 web session。"""
    try:
        result = await verify_phone_code(body.phone, body.code)
        user_id = await get_or_create_user_by_phone(result.normalized_phone)
        token, expires_at = await create_session(
            user_id=user_id,
            provider="phone",
            provider_user_id=result.normalized_phone,
            auth_payload={"phone_auth_provider": "phone"},
            client_platform="web",
            ip_address=_client_ip(request),
        )
    except PhoneAuthError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message) from e

    return WeChatLoginResponse(
        user_id=str(user_id),
        session_token=token,
        expires_at=expires_at.isoformat(),
    )


@router.post(
    "/phone/bind",
    response_model=IdentityBindResponse,
    summary="绑定手机号身份",
)
async def phone_bind(
    current_user: AuthUserDep,
    body: PhoneBindRequest,
) -> IdentityBindResponse:
    """将已验证手机号绑定到当前 Claread 用户。冲突时不静默合并账号。"""
    try:
        result = await verify_phone_code(body.phone, body.code)
        status = await bind_phone_to_user(
            user_id=PyUUID(current_user.user_id),
            normalized_phone=result.normalized_phone,
        )
    except PhoneAuthError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message) from e
    except IdentityConflictError as e:
        raise _identity_conflict_response(e) from e

    return IdentityBindResponse(
        ok=True,
        provider="phone",
        status=status,
        user_id=current_user.user_id,
    )


@router.post(
    "/wechat/bind",
    response_model=IdentityBindResponse,
    summary="绑定微信小程序身份",
)
async def wechat_bind(
    current_user: AuthUserDep,
    body: WeChatBindRequest,
) -> IdentityBindResponse:
    """将微信小程序身份绑定到当前 Claread 用户。冲突时不静默合并账号。"""
    try:
        wechat_session = await code2session(body.code)
    except WeChatAPIError as e:
        logger.error("wechat_bind code2session failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=502,
            detail=f"WeChat service error: {e.errmsg}",
        ) from e

    auth_payload = {
        "session_key": wechat_session.session_key,
        "unionid": wechat_session.unionid,
    }

    try:
        status = await bind_identity_to_user(
            user_id=PyUUID(current_user.user_id),
            provider="wechat_miniprogram",
            provider_user_id=wechat_session.openid,
            unionid=wechat_session.unionid,
            auth_payload=auth_payload,
        )
    except IdentityConflictError as e:
        raise _identity_conflict_response(e) from e

    return IdentityBindResponse(
        ok=True,
        provider="wechat_miniprogram",
        status=status,
        user_id=current_user.user_id,
    )


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
