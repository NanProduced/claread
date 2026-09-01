"""Offline contract tests for the bounded Resend verification-email adapter."""

from __future__ import annotations

import json
import logging

import httpx
import pytest
from pydantic import SecretStr

from app.config.settings import Settings
from app.services.auth.resend_email import send_verification_email

pytestmark = [
    pytest.mark.chain_auth,
    pytest.mark.seam_pure_unit,
    pytest.mark.life_permanent_regression,
    pytest.mark.no_network_default,
]

API_KEY = "re_test_private-key"
CODE = "123456"
CHALLENGE_ID = "A" * 32


def _settings() -> Settings:
    return Settings(_env_file=None, resend_api_key=SecretStr(API_KEY))


async def test_register_email_uses_normalized_recipient_and_fixed_english_contract() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "provider-id"})

    result = await send_verification_email(
        recipient="Reader@EXAMPLE.COM",
        purpose="register",
        code=CODE,
        challenge_id=CHALLENGE_ID,
        settings=_settings(),
        transport=httpx.MockTransport(handler),
    )

    assert result == "sent"
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == "https://api.resend.com/emails"
    assert request.headers["Authorization"] == f"Bearer {API_KEY}"
    assert request.headers["User-Agent"] == "Claread"
    assert request.headers["Idempotency-Key"] == f"email-auth/register/{CHALLENGE_ID}"
    assert request.extensions["timeout"] == {
        "connect": 5.0,
        "read": 5.0,
        "write": 5.0,
        "pool": 5.0,
    }
    assert json.loads(request.content) == {
        "from": "Claread <login@auth.claread.com>",
        "to": ["Reader@example.com"],
        "subject": "Your Claread verification code",
        "text": (
            "Your Claread verification code is: 123456\n\n"
            "Do not share this code with anyone.\n"
            "If you did not request this code, you can ignore this email.\n"
            "This is an automated message. Please do not reply."
        ),
        "html": (
            "<p>Your Claread verification code is:</p>"
            "<p><strong>123456</strong></p>"
            "<p>Do not share this code with anyone.</p>"
            "<p>If you did not request this code, you can ignore this email.</p>"
            "<p>This is an automated message. Please do not reply.</p>"
        ),
    }


async def test_password_reset_email_uses_fixed_english_contract() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "provider-id"})

    result = await send_verification_email(
        recipient="reader@example.com",
        purpose="password_reset",
        code=CODE,
        challenge_id=CHALLENGE_ID,
        settings=_settings(),
        transport=httpx.MockTransport(handler),
    )

    assert result == "sent"
    assert len(requests) == 1
    request = requests[0]
    assert request.headers["Idempotency-Key"] == (
        f"email-auth/password_reset/{CHALLENGE_ID}"
    )
    assert json.loads(request.content) == {
        "from": "Claread <login@auth.claread.com>",
        "to": ["reader@example.com"],
        "subject": "Your Claread verification code",
        "text": (
            "Your Claread verification code is: 123456\n\n"
            "Do not share this code with anyone.\n"
            "If you did not request this code, you can ignore this email.\n"
            "This is an automated message. Please do not reply."
        ),
        "html": (
            "<p>Your Claread verification code is:</p>"
            "<p><strong>123456</strong></p>"
            "<p>Do not share this code with anyone.</p>"
            "<p>If you did not request this code, you can ignore this email.</p>"
            "<p>This is an automated message. Please do not reply.</p>"
        ),
    }


async def test_non_empty_reply_to_is_included() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "provider-id"})

    settings = Settings(
        _env_file=None,
        resend_api_key=SecretStr(API_KEY),
        resend_reply_to="support@claread.com",
    )
    result = await send_verification_email(
        recipient="reader@example.com",
        purpose="register",
        code=CODE,
        challenge_id=CHALLENGE_ID,
        settings=settings,
        transport=httpx.MockTransport(handler),
    )

    assert result == "sent"
    assert len(requests) == 1
    assert json.loads(requests[0].content)["reply_to"] == "support@claread.com"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("recipient", "not-an-email"),
        ("purpose", "login"),
        ("code", "12345"),
        ("code", "１２３４５６"),
        ("challenge_id", "A" * 31),
        ("challenge_id", "!" * 32),
    ],
)
async def test_invalid_inputs_fail_closed_without_a_request(field: str, value: str) -> None:
    requests = 0

    def unexpected_request(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        raise AssertionError(f"unexpected request to {request.url}")

    arguments = {
        "recipient": "reader@example.com",
        "purpose": "register",
        "code": CODE,
        "challenge_id": CHALLENGE_ID,
    }
    arguments[field] = value

    with pytest.raises(ValueError):
        await send_verification_email(
            **arguments,  # type: ignore[arg-type]
            settings=_settings(),
            transport=httpx.MockTransport(unexpected_request),
        )

    assert requests == 0


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 422, 429, 499])
async def test_explicit_4xx_is_rejected_once(status_code: int) -> None:
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(
            status_code,
            json={"code": "provider-code", "message": "provider-message"},
        )

    result = await send_verification_email(
        recipient="reader@example.com",
        purpose="register",
        code=CODE,
        challenge_id=CHALLENGE_ID,
        settings=_settings(),
        transport=httpx.MockTransport(handler),
    )

    assert result == "rejected"
    assert requests == 1


@pytest.mark.parametrize("status_code", [201, 204, 299, 300, 307, 308, 409, 500, 503, 599])
async def test_non_definitive_status_is_uncertain_once_without_redirects(
    status_code: int,
) -> None:
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(
            status_code,
            headers={"Location": "https://example.com/must-not-follow"},
        )

    result = await send_verification_email(
        recipient="reader@example.com",
        purpose="register",
        code=CODE,
        challenge_id=CHALLENGE_ID,
        settings=_settings(),
        transport=httpx.MockTransport(handler),
    )

    assert result == "uncertain"
    assert requests == 1


@pytest.mark.parametrize(
    "content",
    [b"not-json", b"{}", b'{"id":""}', b'{"id":123}', b"[]"],
)
async def test_malformed_200_is_uncertain_once(content: bytes) -> None:
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, content=content, headers={"Content-Type": "application/json"})

    result = await send_verification_email(
        recipient="reader@example.com",
        purpose="register",
        code=CODE,
        challenge_id=CHALLENGE_ID,
        settings=_settings(),
        transport=httpx.MockTransport(handler),
    )

    assert result == "uncertain"
    assert requests == 1


@pytest.mark.parametrize("error_type", [httpx.ReadTimeout, httpx.ConnectError])
async def test_request_error_is_uncertain_once_without_sensitive_logs(
    error_type: type[httpx.RequestError],
    caplog: pytest.LogCaptureFixture,
) -> None:
    requests = 0
    recipient = "private-reader@example.com"
    provider_message = "provider-private-message"

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        raise error_type(
            f"{API_KEY}|{recipient}|{CODE}|{CHALLENGE_ID}|{provider_message}",
            request=request,
        )

    caplog.set_level(logging.DEBUG)
    result = await send_verification_email(
        recipient=recipient,
        purpose="register",
        code=CODE,
        challenge_id=CHALLENGE_ID,
        settings=_settings(),
        transport=httpx.MockTransport(handler),
    )

    assert result == "uncertain"
    assert requests == 1
    logs = "\n".join(record.getMessage() for record in caplog.records)
    for sensitive in (API_KEY, recipient, CODE, CHALLENGE_ID, provider_message):
        assert sensitive not in logs


async def test_provider_failure_body_and_request_body_are_not_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    requests: list[httpx.Request] = []
    provider_message = "provider-response-private-message"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500, text=provider_message)

    caplog.set_level(logging.DEBUG)
    result = await send_verification_email(
        recipient="private-reader@example.com",
        purpose="password_reset",
        code=CODE,
        challenge_id=CHALLENGE_ID,
        settings=_settings(),
        transport=httpx.MockTransport(handler),
    )

    assert result == "uncertain"
    assert len(requests) == 1
    logs = "\n".join(record.getMessage() for record in caplog.records)
    for sensitive in (
        API_KEY,
        "private-reader@example.com",
        CODE,
        CHALLENGE_ID,
        requests[0].content.decode(),
        f"Bearer {API_KEY}",
        provider_message,
    ):
        assert sensitive not in logs


async def test_default_transport_has_zero_retries_and_makes_one_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured_retries: list[int] = []
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(503)

    def fake_transport(*, retries: int) -> httpx.MockTransport:
        configured_retries.append(retries)
        return httpx.MockTransport(handler)

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", fake_transport)
    result = await send_verification_email(
        recipient="reader@example.com",
        purpose="register",
        code=CODE,
        challenge_id=CHALLENGE_ID,
        settings=_settings(),
    )

    assert result == "uncertain"
    assert configured_retries == [0]
    assert requests == 1
