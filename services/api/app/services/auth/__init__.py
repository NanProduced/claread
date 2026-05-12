"""认证服务层。

包含微信登录和业务会话管理。
"""

from __future__ import annotations

from app.services.auth.dependencies import AuthUser, AuthUserDep, get_current_user
from app.services.auth.session import (
    SessionInfo,
    create_session,
    get_or_create_user_by_wechat,
    revoke_session,
    validate_session,
)
from app.services.auth.wechat import WeChatAPIError, WeChatSession, code2session

__all__ = [
    "WeChatAPIError",
    "WeChatSession",
    "SessionInfo",
    "AuthUser",
    "AuthUserDep",
    "code2session",
    "create_session",
    "validate_session",
    "revoke_session",
    "get_or_create_user_by_wechat",
    "get_current_user",
]
