"""
认证依赖注入。

提供 FastAPI Depends，用于从请求头解析当前登录用户。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.services.auth.session import SessionInfo, validate_session

__all__ = ["get_current_user", "get_optional_user", "AuthUser", "AuthUserDep", "OptionalAuthUserDep"]

security = HTTPBearer(auto_error=False)


class AuthUser:
    """当前登录用户（从 session token 解析）"""

    __slots__ = ("user_id", "session_id")

    def __init__(self, user_id: str, session_id: str) -> None:
        self.user_id = user_id
        self.session_id = session_id


async def get_current_user(
    authorization: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
) -> AuthUser:
    """
    FastAPI Depends：从 Authorization header 解析当前用户。

    Header 格式: Authorization: Bearer <session_token>

    Returns:
        AuthUser（user_id, session_id）

    Raises:
        HTTPException(401): token 无效、过期或缺失
    """
    if authorization is None:
        raise HTTPException(status_code=401, detail="Missing authorization header") from None

    token = authorization.credentials
    if not token:
        raise HTTPException(status_code=401, detail="Empty token") from None

    session_info: SessionInfo | None = await validate_session(token)
    if session_info is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session") from None

    return AuthUser(
        user_id=str(session_info.user_id),
        session_id=str(session_info.session_id),
    )


async def get_optional_user(
    authorization: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
) -> AuthUser | None:
    """
    FastAPI Depends：可选的当前用户解析。
    如果不带 token，返回 None；如果带了无效 token，仍然抛 401。
    """
    if authorization is None or not authorization.credentials:
        return None

    session_info: SessionInfo | None = await validate_session(authorization.credentials)
    if session_info is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session") from None

    return AuthUser(
        user_id=str(session_info.user_id),
        session_id=str(session_info.session_id),
    )


# 类型别名，方便 route 使用
AuthUserDep = Annotated[AuthUser, Depends(get_current_user)]
OptionalAuthUserDep = Annotated[AuthUser | None, Depends(get_optional_user)]
