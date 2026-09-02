"""Offline contract tests for the bounded Resend verification-email adapter."""

from __future__ import annotations

import json
import logging
import re

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
PROVIDER_ID = "provider-private-id"
REGISTER_SUBJECT = "Claread 注册验证码"
RESET_SUBJECT = "Claread 密码重置验证码"
REGISTER_HEADING = "完成账号创建"
RESET_HEADING = "重置账号密码"
REGISTER_INTRO = "请在 Claread 输入以下验证码，完成账号创建。"
RESET_INTRO = "请在 Claread 输入以下验证码，继续重置账号密码。"
REGISTER_PREHEADER = "使用此验证码完成 Claread 账号创建。"
RESET_PREHEADER = "使用此验证码重置你的 Claread 密码。"
ONE_TIME_COPY = "验证码仅可使用一次，请尽快完成验证。"
COMMON_SAFETY = "请勿将验证码告知任何人。"
REGISTER_SAFETY = "如果不是你本人操作，忽略本邮件即可。"
RESET_SAFETY = "如果不是你本人操作，忽略本邮件即可，你的密码不会因此发生变化。"
AUTOMATED_COPY = "此邮件由 Claread 自动发送，请勿回复。"
_BANNED_HTML = (
    "<img",
    "<a ",
    "http://",
    "https://",
    "url(",
    "@import",
    "@font-face",
    "fonts.google",
    "gradient",
    "utm_",
    "有效期",
    "分钟",
    "小时",
    "expires",
    "valid for",
)


def _assert_provider_outcome(
    caplog: pytest.LogCaptureFixture,
    expected: dict[str, object],
) -> None:
    records = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "email_provider_outcome"
    ]
    assert len(records) == 1
    record = records[0]
    actual = {
        key: getattr(record, key)
        for key in ("event", "outcome", "reason", "status_code")
        if hasattr(record, key)
    }
    assert actual == {"event": "email_provider_outcome", **expected}
    message_fields = ["event=email_provider_outcome", f"outcome={expected['outcome']}"]
    for key in ("reason", "status_code"):
        if key in expected:
            message_fields.append(f"{key}={expected[key]}")
    assert record.getMessage() == " ".join(message_fields)
    assert record.levelno == (logging.INFO if expected["outcome"] == "sent" else logging.WARNING)
    assert record.args == ()
    assert record.exc_info is None
    assert record.exc_text is None


def _settings() -> Settings:
    return Settings(_env_file=None, resend_api_key=SecretStr(API_KEY))


def _payload(request: httpx.Request) -> dict[str, object]:
    body = json.loads(request.content)
    assert isinstance(body, dict)
    return body


def _assert_transport_envelope(request: httpx.Request, *, purpose: str) -> None:
    assert request.method == "POST"
    assert str(request.url) == "https://api.resend.com/emails"
    assert request.headers["Authorization"] == f"Bearer {API_KEY}"
    assert request.headers["User-Agent"] == "Claread"
    assert request.headers["Idempotency-Key"] == f"email-auth/{purpose}/{CHALLENGE_ID}"
    assert request.extensions["timeout"] == {
        "connect": 5.0,
        "read": 5.0,
        "write": 5.0,
        "pool": 5.0,
    }


def _assert_branded_bodies(
    payload: dict[str, object],
    *,
    subject: str,
    heading: str,
    intro: str,
    preheader: str,
    safety: str,
    recipient: str,
) -> None:
    assert payload["from"] == "Claread透读 <login@auth.claread.com>"
    assert payload["subject"] == subject
    text = payload["text"]
    html = payload["html"]
    assert isinstance(text, str)
    assert isinstance(html, str)
    for required in (
        heading,
        intro,
        ONE_TIME_COPY,
        "安全提醒",
        COMMON_SAFETY,
        safety,
        AUTOMATED_COPY,
        "透读英文文章",
    ):
        assert required in text
        assert required in html
    assert text.startswith("Claread 透读\n")
    assert f"一次性验证码：{CODE}" in text
    assert 'aria-label="Claread 透读"' in html
    assert "一次性验证码" in html
    assert preheader in html
    assert html.startswith("<!DOCTYPE html>")
    assert 'lang="zh-CN"' in html
    assert '<table role="presentation"' in html.lower()
    assert "max-width:560px" in html.replace(" ", "")
    assert "<h1" in html.lower()
    for color in (
        "#f6f3ec",
        "#faf9f6",
        "#111111",
        "#eae7df",
        "#155cff",
        "#6b6e77",
    ):
        assert color in html.lower()
    lowered_html = html.lower()
    for font_name in ("bodoni 72", "didot", "bodoni mt", "times new roman", "georgia"):
        assert font_name in lowered_html
    for font_name in ("songti sc", "stsong", "noto serif sc"):
        assert font_name in lowered_html
    assert "border-radius:4px" in html.replace(" ", "").lower()
    assert re.search(r"display\s*:\s*none", html, flags=re.IGNORECASE)
    assert CODE in html
    assert recipient not in text
    assert recipient not in html
    assert CHALLENGE_ID not in text
    assert CHALLENGE_ID not in html
    lowered = f"{text}\n{html}".lower()
    for banned in _BANNED_HTML:
        assert banned not in lowered
    if subject == REGISTER_SUBJECT:
        for excluded in (RESET_HEADING, RESET_INTRO, RESET_PREHEADER, RESET_SAFETY):
            assert excluded not in text
            assert excluded not in html
    else:
        for excluded in (
            REGISTER_HEADING,
            REGISTER_INTRO,
            REGISTER_PREHEADER,
            REGISTER_SAFETY,
        ):
            assert excluded not in text
            assert excluded not in html


async def test_register_email_uses_normalized_recipient_and_branded_chinese_contract(
    caplog: pytest.LogCaptureFixture,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": PROVIDER_ID})

    caplog.set_level(logging.INFO)
    result = await send_verification_email(
        recipient="Reader@EXAMPLE.COM",
        purpose="register",
        code=CODE,
        challenge_id=CHALLENGE_ID,
        settings=_settings(),
        transport=httpx.MockTransport(handler),
    )

    assert result == "sent"
    _assert_provider_outcome(caplog, {"outcome": "sent"})
    assert PROVIDER_ID not in "\n".join(record.getMessage() for record in caplog.records)
    assert len(requests) == 1
    request = requests[0]
    _assert_transport_envelope(request, purpose="register")
    payload = _payload(request)
    assert payload["to"] == ["Reader@example.com"]
    _assert_branded_bodies(
        payload,
        subject=REGISTER_SUBJECT,
        heading=REGISTER_HEADING,
        intro=REGISTER_INTRO,
        preheader=REGISTER_PREHEADER,
        safety=REGISTER_SAFETY,
        recipient="Reader@EXAMPLE.COM",
    )
    assert "reply_to" not in payload


async def test_password_reset_email_uses_distinct_branded_chinese_contract() -> None:
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
    _assert_transport_envelope(request, purpose="password_reset")
    payload = _payload(request)
    assert payload["to"] == ["reader@example.com"]
    _assert_branded_bodies(
        payload,
        subject=RESET_SUBJECT,
        heading=RESET_HEADING,
        intro=RESET_INTRO,
        preheader=RESET_PREHEADER,
        safety=RESET_SAFETY,
        recipient="reader@example.com",
    )


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
async def test_explicit_4xx_is_rejected_once(
    status_code: int,
    caplog: pytest.LogCaptureFixture,
) -> None:
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
    _assert_provider_outcome(
        caplog,
        {"outcome": "rejected", "reason": "http_rejected", "status_code": status_code},
    )
    assert requests == 1


@pytest.mark.parametrize(
    ("status_code", "reason"),
    [
        (201, "http_unexpected"),
        (204, "http_unexpected"),
        (299, "http_unexpected"),
        (300, "http_redirect"),
        (307, "http_redirect"),
        (308, "http_redirect"),
        (409, "http_conflict"),
        (500, "http_server_error"),
        (503, "http_server_error"),
        (599, "http_server_error"),
    ],
)
async def test_non_definitive_status_is_uncertain_once_without_redirects(
    status_code: int,
    reason: str,
    caplog: pytest.LogCaptureFixture,
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
    _assert_provider_outcome(
        caplog,
        {"outcome": "uncertain", "reason": reason, "status_code": status_code},
    )
    assert requests == 1


@pytest.mark.parametrize(
    "content",
    [b"not-json", b"{}", b'{"id":""}', b'{"id":123}', b"[]"],
)
async def test_malformed_200_is_uncertain_once(
    content: bytes,
    caplog: pytest.LogCaptureFixture,
) -> None:
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
    _assert_provider_outcome(caplog, {"outcome": "uncertain", "reason": "malformed_success"})
    assert requests == 1


@pytest.mark.parametrize(
    ("error_type", "reason"),
    [(httpx.ReadTimeout, "request_timeout"), (httpx.ConnectError, "request_error")],
)
async def test_request_error_is_uncertain_once_without_sensitive_logs(
    error_type: type[httpx.RequestError],
    reason: str,
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
    _assert_provider_outcome(caplog, {"outcome": "uncertain", "reason": reason})
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
    _assert_provider_outcome(
        caplog,
        {"outcome": "uncertain", "reason": "http_server_error", "status_code": 500},
    )
    logs = "\n".join(record.getMessage() for record in caplog.records)
    for sensitive in (
        API_KEY,
        "private-reader@example.com",
        CODE,
        CHALLENGE_ID,
        requests[0].content.decode(),
        f"Bearer {API_KEY}",
        provider_message,
        PROVIDER_ID,
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
