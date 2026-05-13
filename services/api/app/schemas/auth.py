"""Auth API Schemas.

Defines request/response Pydantic models for /auth endpoints.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class WeChatLoginRequest(BaseModel):
    code: str = Field(min_length=1)


class PhoneCodeRequest(BaseModel):
    phone: str = Field(min_length=1, max_length=32)


class PhoneVerifyRequest(BaseModel):
    phone: str = Field(min_length=1, max_length=32)
    code: str = Field(min_length=4, max_length=12)


class PhoneBindRequest(BaseModel):
    phone: str = Field(min_length=1, max_length=32)
    code: str = Field(min_length=4, max_length=12)


class WeChatBindRequest(BaseModel):
    code: str = Field(min_length=1)


class LogoutRequest(BaseModel):
    session_token: str = Field(min_length=1)


class ProfileUpdateRequest(BaseModel):
    nickname: str | None = Field(default=None, max_length=50)
    avatar_url: str | None = Field(default=None, max_length=500)
    settings: dict[str, Any] | None = Field(default=None, description="用户设置 JSON")


class WeChatLoginResponse(BaseModel):
    user_id: str
    session_token: str
    expires_at: str


class PhoneCodeResponse(BaseModel):
    ok: bool
    message: str
    normalized_phone: str | None = None


class IdentityBindResponse(BaseModel):
    ok: bool
    provider: str
    status: str
    user_id: str


class SessionInfoResponse(BaseModel):
    user_id: str
    session_id: str
    nickname: str
    avatar_url: str
    cumulative_article_count: int
    settings: dict[str, Any]


class ProfileUpdateResponse(BaseModel):
    ok: bool
    updated: list[str]


class LogoutResponse(BaseModel):
    ok: bool
