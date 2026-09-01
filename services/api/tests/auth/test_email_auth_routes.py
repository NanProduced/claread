"""Offline contract tests for the email-auth HTTP routes."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes.email_auth import _get_email_auth_service
from app.main import app
from app.services.auth.email_address import InvalidEmailAddressError
from app.services.auth.email_auth import (
    EmailAuthError,
    EmailAuthService,
    EmailEntryResult,
    EmailResetRequestResult,
)
from app.services.auth.email_challenges import EmailAuthStateError, TicketIssued
from app.services.auth.passwords import InvalidPasswordError

pytestmark = [
    pytest.mark.chain_auth,
    pytest.mark.seam_api_contract,
    pytest.mark.life_permanent_regression,
    pytest.mark.no_network_default,
]

EMAIL = "User@Example.COM"
TICKET = "T" * 43
CHALLENGE_ID = "C" * 32
CODE = "123456"
SESSION_TOKEN = "session-token"
EXPIRES_AT = datetime(2030, 1, 1, tzinfo=UTC)
CLIENT_IP = "testclient"


def test_email_auth_routes_are_registered(api_app: FastAPI = app) -> None:
    paths = {route.path for route in api_app.routes}

    assert {
        "/auth/email/start",
        "/auth/email/otp/verify",
        "/auth/email/register",
        "/auth/email/password/login",
        "/auth/email/password-reset/request",
        "/auth/email/password-reset/complete",
    } <= paths


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def service() -> Iterator[AsyncMock]:
    fake = AsyncMock(spec=EmailAuthService)
    app.dependency_overrides[_get_email_auth_service] = lambda: fake
    yield fake
    app.dependency_overrides.clear()


def test_start_password_mode_uses_raw_email_and_trusted_client_host(
    client: TestClient,
    service: AsyncMock,
) -> None:
    service.start_email_auth.return_value = EmailEntryResult(mode="password")

    response = client.post(
        "/auth/email/start",
        json={"email": EMAIL},
        headers={"X-Forwarded-For": "198.51.100.99"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "mode": "password",
        "challenge_id": None,
        "expires_in": None,
    }
    service.start_email_auth.assert_awaited_once_with(
        email=EMAIL,
        client_ip=CLIENT_IP,
    )


def test_start_register_mode_returns_challenge_metadata(
    client: TestClient,
    service: AsyncMock,
) -> None:
    service.start_email_auth.return_value = EmailEntryResult(
        mode="register",
        challenge_id=CHALLENGE_ID,
        expires_in=600,
    )

    response = client.post("/auth/email/start", json={"email": EMAIL})

    assert response.status_code == 200
    assert response.json() == {
        "mode": "register",
        "challenge_id": CHALLENGE_ID,
        "expires_in": 600,
    }


def test_otp_verify_returns_one_time_ticket_only(
    client: TestClient,
    service: AsyncMock,
) -> None:
    service.verify_email_otp.return_value = TicketIssued(ticket=TICKET, expires_in=900)

    response = client.post(
        "/auth/email/otp/verify",
        json={"challenge_id": CHALLENGE_ID, "code": CODE},
    )

    assert response.status_code == 200
    assert response.json() == {"ticket": TICKET, "expires_in": 900}
    service.verify_email_otp.assert_awaited_once_with(
        challenge_id=CHALLENGE_ID,
        code=CODE,
    )


@pytest.mark.parametrize(
    ("path", "method_name", "payload"),
    [
        (
            "/auth/email/register",
            "register_with_ticket",
            {"ticket": TICKET, "password": "safe password"},
        ),
        (
            "/auth/email/password/login",
            "login_with_password",
            {"email": EMAIL, "password": "safe password"},
        ),
        (
            "/auth/email/password-reset/complete",
            "reset_with_ticket",
            {"ticket": TICKET, "password": "safe password"},
        ),
    ],
)
def test_successful_session_routes_return_only_token_and_expiry(
    client: TestClient,
    service: AsyncMock,
    path: str,
    method_name: str,
    payload: dict[str, str],
) -> None:
    getattr(service, method_name).return_value = (SESSION_TOKEN, EXPIRES_AT)

    response = client.post(
        path,
        json=payload,
        headers={"X-Forwarded-For": "198.51.100.99"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "session_token": SESSION_TOKEN,
        "expires_at": EXPIRES_AT.isoformat().replace("+00:00", "Z"),
    }
    assert set(response.json()) == {"session_token", "expires_at"}
    assert "set-cookie" not in response.headers
    if method_name == "register_with_ticket":
        service.register_with_ticket.assert_awaited_once_with(
            ticket=TICKET,
            raw_password="safe password",
            client_ip=CLIENT_IP,
        )
    elif method_name == "login_with_password":
        service.login_with_password.assert_awaited_once_with(
            email=EMAIL,
            raw_password="safe password",
            client_ip=CLIENT_IP,
        )
    else:
        service.reset_with_ticket.assert_awaited_once_with(
            ticket=TICKET,
            raw_password="safe password",
            client_ip=CLIENT_IP,
        )


def test_password_reset_request_keeps_generic_accepted_contract(
    client: TestClient,
    service: AsyncMock,
) -> None:
    service.request_password_reset.return_value = EmailResetRequestResult(
        status="accepted",
        challenge_id=CHALLENGE_ID,
        expires_in=600,
    )

    response = client.post(
        "/auth/email/password-reset/request",
        json={"email": EMAIL},
        headers={"X-Forwarded-For": "198.51.100.99"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "accepted",
        "challenge_id": CHALLENGE_ID,
        "expires_in": 600,
    }
    service.request_password_reset.assert_awaited_once_with(
        email=EMAIL,
        client_ip=CLIENT_IP,
    )


@pytest.mark.parametrize(
    ("path", "payload", "method_name"),
    [
        (
            "/auth/email/otp/verify",
            {"challenge_id": "short", "code": CODE},
            "verify_email_otp",
        ),
        (
            "/auth/email/otp/verify",
            {"challenge_id": CHALLENGE_ID, "code": "１２３４５６"},
            "verify_email_otp",
        ),
        (
            "/auth/email/register",
            {"ticket": "short", "password": "safe password"},
            "register_with_ticket",
        ),
        (
            "/auth/email/password-reset/complete",
            {"ticket": "short", "password": "safe password"},
            "reset_with_ticket",
        ),
    ],
)
def test_token_shape_validation_happens_before_service(
    client: TestClient,
    service: AsyncMock,
    path: str,
    payload: dict[str, str],
    method_name: str,
) -> None:
    response = client.post(path, json=payload)

    assert response.status_code == 422
    getattr(service, method_name).assert_not_awaited()


def test_email_domain_validation_stays_in_service_layer(
    client: TestClient,
    service: AsyncMock,
) -> None:
    service.start_email_auth.return_value = EmailEntryResult(mode="password")

    response = client.post("/auth/email/start", json={"email": "not-an-email"})

    assert response.status_code == 200
    service.start_email_auth.assert_awaited_once_with(
        email="not-an-email",
        client_ip=CLIENT_IP,
    )


@pytest.mark.parametrize(
    ("method_name", "path", "payload", "error", "status", "detail", "retry_after"),
    [
        (
            "login_with_password",
            "/auth/email/password/login",
            {"email": EMAIL, "password": "safe password"},
            EmailAuthError("invalid_credentials"),
            401,
            {"code": "invalid_credentials"},
            None,
        ),
        (
            "start_email_auth",
            "/auth/email/start",
            {"email": EMAIL},
            InvalidEmailAddressError("invalid email"),
            422,
            {"code": "invalid_email"},
            None,
        ),
        (
            "register_with_ticket",
            "/auth/email/register",
            {"ticket": TICKET, "password": "safe password"},
            InvalidPasswordError("invalid password"),
            422,
            {"code": "invalid_password"},
            None,
        ),
        (
            "register_with_ticket",
            "/auth/email/register",
            {"ticket": TICKET, "password": "safe password"},
            EmailAuthError("common"),
            422,
            {"code": "common"},
            None,
        ),
        (
            "reset_with_ticket",
            "/auth/email/password-reset/complete",
            {"ticket": TICKET, "password": "safe password"},
            EmailAuthError("compromised"),
            422,
            {"code": "compromised"},
            None,
        ),
        (
            "verify_email_otp",
            "/auth/email/otp/verify",
            {"challenge_id": CHALLENGE_ID, "code": CODE},
            EmailAuthStateError("invalid_or_expired_code"),
            400,
            {"code": "invalid_or_expired_code"},
            None,
        ),
        (
            "register_with_ticket",
            "/auth/email/register",
            {"ticket": TICKET, "password": "safe password"},
            EmailAuthStateError("ticket_invalid_or_expired"),
            400,
            {"code": "ticket_invalid_or_expired"},
            None,
        ),
        (
            "reset_with_ticket",
            "/auth/email/password-reset/complete",
            {"ticket": TICKET, "password": "safe password"},
            EmailAuthStateError("ticket_purpose_mismatch"),
            400,
            {"code": "ticket_purpose_mismatch"},
            None,
        ),
        (
            "start_email_auth",
            "/auth/email/start",
            {"email": EMAIL},
            EmailAuthStateError("email_cooldown", retry_after=23),
            429,
            {"code": "email_cooldown", "retry_after": 23},
            "23",
        ),
        (
            "start_email_auth",
            "/auth/email/start",
            {"email": EMAIL},
            EmailAuthStateError("email_hourly_limit", retry_after=23),
            429,
            {"code": "email_hourly_limit", "retry_after": 23},
            "23",
        ),
        (
            "start_email_auth",
            "/auth/email/start",
            {"email": EMAIL},
            EmailAuthStateError("ip_hourly_limit", retry_after=23),
            429,
            {"code": "ip_hourly_limit", "retry_after": 23},
            "23",
        ),
        (
            "verify_email_otp",
            "/auth/email/otp/verify",
            {"challenge_id": CHALLENGE_ID, "code": CODE},
            EmailAuthStateError("backend_unavailable"),
            503,
            {"code": "email_auth_unavailable"},
            None,
        ),
        (
            "start_email_auth",
            "/auth/email/start",
            {"email": EMAIL},
            EmailAuthStateError("invalid_configuration"),
            503,
            {"code": "email_auth_unavailable"},
            None,
        ),
        (
            "start_email_auth",
            "/auth/email/start",
            {"email": EMAIL},
            EmailAuthError("email_delivery_rejected"),
            503,
            {"code": "email_delivery_rejected"},
            None,
        ),
        (
            "start_email_auth",
            "/auth/email/start",
            {"email": EMAIL},
            RuntimeError("provider internal secret"),
            503,
            {"code": "email_auth_unavailable"},
            None,
        ),
    ],
)
def test_service_error_mapping_is_stable_and_non_sensitive(
    client: TestClient,
    service: AsyncMock,
    method_name: str,
    path: str,
    payload: dict[str, str],
    error: Exception,
    status: int,
    detail: dict[str, object],
    retry_after: str | None,
) -> None:
    getattr(service, method_name).side_effect = error

    response = client.post(path, json=payload)

    assert response.status_code == status
    assert response.json()["detail"] == detail
    assert "provider internal secret" not in response.text
    if retry_after is None:
        assert "retry_after" not in response.json()["detail"]
        assert "retry-after" not in response.headers
    else:
        assert response.headers["retry-after"] == retry_after


def test_email_auth_disabled_fails_closed_before_business_service(client: TestClient) -> None:
    settings = SimpleNamespace(email_auth_enabled=False)
    service_factory = Mock()
    redis_ready = AsyncMock()

    with (
        patch("app.api.routes.email_auth.get_settings", return_value=settings),
        patch("app.api.routes.email_auth.EmailAuthService", service_factory),
        patch("app.database.connection.is_redis_ready", redis_ready),
    ):
        response = client.post("/auth/email/start", json={"email": EMAIL})

    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "email_auth_unavailable"}}
    service_factory.assert_not_called()
    redis_ready.assert_not_awaited()


def test_email_auth_unready_redis_fails_closed_before_business_service(
    client: TestClient,
) -> None:
    settings = SimpleNamespace(email_auth_enabled=True)
    service_factory = Mock()
    redis_ready = AsyncMock(return_value=False)

    with (
        patch("app.api.routes.email_auth.get_settings", return_value=settings),
        patch("app.api.routes.email_auth.EmailAuthService", service_factory),
        patch("app.database.connection.is_redis_ready", redis_ready),
    ):
        response = client.post("/auth/email/start", json={"email": EMAIL})

    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "email_auth_unavailable"}}
    service_factory.assert_not_called()
    redis_ready.assert_awaited_once_with()


def test_email_auth_service_reads_current_redis_from_connection_module(
    client: TestClient,
) -> None:
    settings = SimpleNamespace(email_auth_enabled=True)
    redis_client = object()
    ready = AsyncMock(return_value=True)
    get_redis = AsyncMock(return_value=redis_client)
    challenge_factory = Mock()
    service = Mock()
    service.start_email_auth = AsyncMock(return_value=EmailEntryResult(mode="password"))

    with (
        patch("app.api.routes.email_auth.get_settings", return_value=settings),
        patch("app.database.connection.is_redis_ready", ready),
        patch("app.database.connection.get_redis", get_redis),
        patch("app.api.routes.email_auth.EmailAuthChallengeService", challenge_factory),
        patch("app.api.routes.email_auth.EmailAuthService", return_value=service),
    ):
        response = client.post("/auth/email/start", json={"email": EMAIL})

    assert response.status_code == 200
    ready.assert_awaited_once_with()
    get_redis.assert_awaited_once_with()
    challenge_factory.assert_called_once_with(redis_client, settings)
    service.start_email_auth.assert_awaited_once_with(
        email=EMAIL,
        client_ip=CLIENT_IP,
    )


def test_email_auth_route_does_not_snapshot_or_log_sensitive_auth_state() -> None:
    source_path = Path(__file__).resolve().parents[2] / "app" / "api" / "routes" / "email_auth.py"
    source = source_path.read_text(encoding="utf-8")

    assert "RedisPool" not in source
    assert "x-forwarded-for" not in source.lower()
    assert "logger" not in source.lower()
