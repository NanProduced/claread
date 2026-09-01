"""Request and response schemas for the email authentication API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

_CHALLENGE_ID_PATTERN = r"^[A-Za-z0-9_-]{32}$"
_CODE_PATTERN = r"^[0-9]{6}$"
_TICKET_PATTERN = r"^[A-Za-z0-9_-]{43}$"


class EmailStartRequest(BaseModel):
    email: str = Field(min_length=1, max_length=320)


class EmailStartResponse(BaseModel):
    mode: Literal["password", "register"]
    challenge_id: str | None = None
    expires_in: int | None = None


class EmailOTPVerifyRequest(BaseModel):
    challenge_id: str = Field(
        min_length=32,
        max_length=32,
        pattern=_CHALLENGE_ID_PATTERN,
    )
    code: str = Field(min_length=6, max_length=6, pattern=_CODE_PATTERN)


class EmailOTPVerifyResponse(BaseModel):
    ticket: str
    expires_in: int


class EmailRegisterRequest(BaseModel):
    ticket: str = Field(min_length=43, max_length=43, pattern=_TICKET_PATTERN)
    password: str = Field(min_length=1, max_length=512)


class EmailPasswordLoginRequest(BaseModel):
    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=512)


class EmailPasswordResetRequest(BaseModel):
    email: str = Field(min_length=1, max_length=320)


class EmailPasswordResetCompleteRequest(BaseModel):
    ticket: str = Field(min_length=43, max_length=43, pattern=_TICKET_PATTERN)
    password: str = Field(min_length=1, max_length=512)


class EmailSessionResponse(BaseModel):
    session_token: str
    expires_at: datetime


class EmailPasswordResetResponse(BaseModel):
    status: Literal["accepted"]
    challenge_id: str
    expires_in: int
