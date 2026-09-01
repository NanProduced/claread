"""HTTP routes for email authentication."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Annotated, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Request

from app.config.settings import get_settings
from app.database import connection as db_connection
from app.schemas.email_auth import (
    EmailOTPVerifyRequest,
    EmailOTPVerifyResponse,
    EmailPasswordLoginRequest,
    EmailPasswordResetCompleteRequest,
    EmailPasswordResetRequest,
    EmailPasswordResetResponse,
    EmailRegisterRequest,
    EmailSessionResponse,
    EmailStartRequest,
    EmailStartResponse,
)
from app.services.auth.email_address import InvalidEmailAddressError
from app.services.auth.email_auth import (
    EmailAuthError,
    EmailAuthService,
)
from app.services.auth.email_challenges import (
    EmailAuthChallengeService,
    EmailAuthStateError,
)
from app.services.auth.passwords import InvalidPasswordError

router = APIRouter(prefix="/auth/email", tags=["auth"])

_RATE_LIMIT_CODES = frozenset(
    {"email_cooldown", "email_hourly_limit", "ip_hourly_limit"}
)
_INVALID_STATE_CODES = frozenset(
    {
        "invalid_or_expired_code",
        "ticket_invalid_or_expired",
        "ticket_purpose_mismatch",
        "invalid_purpose",
    }
)
_T = TypeVar("_T")


def _unavailable() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={"code": "email_auth_unavailable"},
    )


async def _require_email_auth_ready() -> None:
    try:
        settings = get_settings()
    except Exception:
        raise _unavailable() from None
    if not settings.email_auth_enabled:
        raise _unavailable()

    try:
        ready = await db_connection.is_redis_ready()
    except Exception:
        ready = False
    if not ready:
        raise _unavailable()


async def _get_email_auth_service(
    _: Annotated[None, Depends(_require_email_auth_ready)],
) -> EmailAuthService:
    try:
        settings = get_settings()
        redis_client = await db_connection.get_redis()
        if redis_client is None:
            raise _unavailable()
        challenge_service = EmailAuthChallengeService(redis_client, settings)
        return EmailAuthService(challenge_service)
    except Exception:
        raise _unavailable() from None


def _client_ip(request: Request) -> str:
    return request.client.host if request.client is not None else ""


def _map_error(error: Exception) -> HTTPException:
    if isinstance(error, EmailAuthError):
        if error.code == "invalid_credentials":
            return HTTPException(401, detail={"code": error.code})
        if error.code in {"common", "compromised"}:
            return HTTPException(422, detail={"code": error.code})
        if error.code == "email_delivery_rejected":
            return HTTPException(503, detail={"code": error.code})
        return _unavailable()

    if isinstance(error, InvalidEmailAddressError):
        return HTTPException(422, detail={"code": "invalid_email"})
    if isinstance(error, InvalidPasswordError):
        return HTTPException(422, detail={"code": "invalid_password"})

    if isinstance(error, EmailAuthStateError):
        if error.code in _RATE_LIMIT_CODES and error.retry_after is not None:
            return HTTPException(
                429,
                detail={"code": error.code, "retry_after": error.retry_after},
                headers={"Retry-After": str(error.retry_after)},
            )
        if error.code in _INVALID_STATE_CODES:
            return HTTPException(400, detail={"code": error.code})
        if error.code == "invalid_client_ip":
            return HTTPException(422, detail={"code": error.code})
        return _unavailable()

    return _unavailable()


async def _run(operation: Awaitable[_T]) -> _T:
    try:
        return await operation
    except Exception as error:
        raise _map_error(error) from None


@router.post("/start", response_model=EmailStartResponse)
async def email_start(
    request: Request,
    body: EmailStartRequest,
    service: Annotated[EmailAuthService, Depends(_get_email_auth_service)],
) -> EmailStartResponse:
    result = await _run(
        service.start_email_auth(email=body.email, client_ip=_client_ip(request))
    )
    return EmailStartResponse(
        mode=result.mode,
        challenge_id=result.challenge_id,
        expires_in=result.expires_in,
    )


@router.post("/otp/verify", response_model=EmailOTPVerifyResponse)
async def email_otp_verify(
    body: EmailOTPVerifyRequest,
    service: Annotated[EmailAuthService, Depends(_get_email_auth_service)],
) -> EmailOTPVerifyResponse:
    result = await _run(
        service.verify_email_otp(challenge_id=body.challenge_id, code=body.code)
    )
    return EmailOTPVerifyResponse(ticket=result.ticket, expires_in=result.expires_in)


@router.post("/register", response_model=EmailSessionResponse)
async def email_register(
    request: Request,
    body: EmailRegisterRequest,
    service: Annotated[EmailAuthService, Depends(_get_email_auth_service)],
) -> EmailSessionResponse:
    token, expires_at = await _run(
        service.register_with_ticket(
            ticket=body.ticket,
            raw_password=body.password,
            client_ip=_client_ip(request),
        )
    )
    return EmailSessionResponse(session_token=token, expires_at=expires_at)


@router.post("/password/login", response_model=EmailSessionResponse)
async def email_password_login(
    request: Request,
    body: EmailPasswordLoginRequest,
    service: Annotated[EmailAuthService, Depends(_get_email_auth_service)],
) -> EmailSessionResponse:
    token, expires_at = await _run(
        service.login_with_password(
            email=body.email,
            raw_password=body.password,
            client_ip=_client_ip(request),
        )
    )
    return EmailSessionResponse(session_token=token, expires_at=expires_at)


@router.post("/password-reset/request", response_model=EmailPasswordResetResponse)
async def email_password_reset_request(
    request: Request,
    body: EmailPasswordResetRequest,
    service: Annotated[EmailAuthService, Depends(_get_email_auth_service)],
) -> EmailPasswordResetResponse:
    result = await _run(
        service.request_password_reset(email=body.email, client_ip=_client_ip(request))
    )
    return EmailPasswordResetResponse(
        status=result.status,
        challenge_id=result.challenge_id,
        expires_in=result.expires_in,
    )


@router.post("/password-reset/complete", response_model=EmailSessionResponse)
async def email_password_reset_complete(
    request: Request,
    body: EmailPasswordResetCompleteRequest,
    service: Annotated[EmailAuthService, Depends(_get_email_auth_service)],
) -> EmailSessionResponse:
    token, expires_at = await _run(
        service.reset_with_ticket(
            ticket=body.ticket,
            raw_password=body.password,
            client_ip=_client_ip(request),
        )
    )
    return EmailSessionResponse(session_token=token, expires_at=expires_at)
