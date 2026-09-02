"""WeChat login log/response redaction guard.

AUTH-CLOSEOUT-B requirement: WeChat mini-program login stays, but logging
must only record a fixed event/category/status vocabulary. It must never
record provider data (``resp.text``, provider payload, ``errmsg``, openid,
session_key, unionid) or raw exception text / ``exc_info``; API responses
must never echo provider error content.

RED on clean main (logs echo provider data), GREEN after the redaction.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.auth.session import SessionInfo
from app.services.auth.wechat import WeChatAPIError, code2session

# Provider data / raw exception surface that must never appear in logs or
# API responses.
SENSITIVE_MARKERS = (
    "openid",
    "session_key",
    "unionid",
    "errmsg",
    "invalid code",
    "40029",
    "WeChat API error",
    "upstream error",
    "invalid JSON",
    "wechat-gateway-error-page",
    "token=abc",
    "ConnectError",
    "ConnectTimeout",
    "RemoteProtocolError",
    "tcp reset",
)


def _wechat_log_messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    # The app's BusinessModuleFormatter rewrites record.name in place while
    # formatting (the app console handler runs before the record reaches the
    # root/caplog handler), so match on the fixed-marker message vocabulary
    # instead of the dotted logger name.
    return [
        record.getMessage()
        for record in caplog.records
        if str(record.getMessage()).startswith("wechat event=")
    ]


def _assert_no_sensitive_markers(messages: list[str]) -> None:
    joined = "\n".join(messages)
    for marker in SENSITIVE_MARKERS:
        assert marker.lower() not in joined.lower(), f"log leaked sensitive marker: {marker!r}"


def _mock_settings(mock_settings: MagicMock) -> None:
    mock_settings.return_value.wechat_app_id = "wx_test_appid"
    mock_settings.return_value.wechat_app_secret = "test_secret"


def _mock_wechat_get(mock_client_cls: MagicMock, response: object) -> None:
    mock_client = AsyncMock()
    mock_client.get.return_value = response
    mock_client_cls.return_value.__aenter__.return_value = mock_client


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


class TestCode2SessionLogRedaction:
    async def test_network_error_logs_only_fixed_markers(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.side_effect = httpx.ConnectError("tcp reset: connection refused")
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            with patch("app.services.auth.wechat.get_settings") as mock_settings:
                _mock_settings(mock_settings)
                with caplog.at_level(logging.WARNING, logger="app.services.auth.wechat"):
                    with pytest.raises(WeChatAPIError) as exc_info:
                        await code2session("valid_code")

        assert exc_info.value.errcode == -3
        messages = _wechat_log_messages(caplog)
        assert messages, "expected a wechat warning log record"
        _assert_no_sensitive_markers(messages)
        assert "connection refused" not in "\n".join(messages).lower()

    async def test_http_status_error_logs_only_fixed_markers(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        request = httpx.Request("GET", "https://api.weixin.qq.com/sns/jscode2session")
        upstream = httpx.Response(503, request=request)

        with patch("httpx.AsyncClient") as mock_client_cls:
            resp = MagicMock()
            resp.raise_for_status.side_effect = httpx.HTTPStatusError(
                "5xx upstream", request=request, response=upstream
            )
            _mock_wechat_get(mock_client_cls, resp)

            with patch("app.services.auth.wechat.get_settings") as mock_settings:
                _mock_settings(mock_settings)
                with caplog.at_level(logging.WARNING, logger="app.services.auth.wechat"):
                    with pytest.raises(WeChatAPIError) as exc_info:
                        await code2session("valid_code")

        assert exc_info.value.errcode == -2
        messages = _wechat_log_messages(caplog)
        assert messages
        _assert_no_sensitive_markers(messages)
        assert "503" not in "\n".join(messages)
        assert "5xx" not in "\n".join(messages).lower()

    async def test_upstream_errmsg_logs_only_fixed_markers(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        mock_response = {"errcode": 40029, "errmsg": "invalid code"}

        with patch("httpx.AsyncClient") as mock_client_cls:
            resp = MagicMock()
            resp.json.return_value = mock_response
            resp.raise_for_status = MagicMock()
            _mock_wechat_get(mock_client_cls, resp)

            with patch("app.services.auth.wechat.get_settings") as mock_settings:
                _mock_settings(mock_settings)
                with caplog.at_level(logging.WARNING, logger="app.services.auth.wechat"):
                    with pytest.raises(WeChatAPIError) as exc_info:
                        await code2session("invalid_code")

        assert exc_info.value.errcode == 40029
        messages = _wechat_log_messages(caplog)
        assert messages
        _assert_no_sensitive_markers(messages)
        assert "40029" not in "\n".join(messages)
        assert "invalid code" not in "\n".join(messages)

    async def test_non_json_response_does_not_log_resp_text(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with patch("httpx.AsyncClient") as mock_client_cls:
            resp = MagicMock()
            resp.json.side_effect = ValueError("no json")
            resp.text = "<html>wechat-gateway-error-page token=abc</html>"
            resp.raise_for_status = MagicMock()
            _mock_wechat_get(mock_client_cls, resp)

            with patch("app.services.auth.wechat.get_settings") as mock_settings:
                _mock_settings(mock_settings)
                with caplog.at_level(logging.WARNING, logger="app.services.auth.wechat"):
                    with pytest.raises(WeChatAPIError):
                        await code2session("bad_code")

        messages = _wechat_log_messages(caplog)
        assert messages
        _assert_no_sensitive_markers(messages)
        assert "wechat-gateway-error-page" not in "\n".join(messages)
        assert "token=abc" not in "\n".join(messages)


class TestWechatRouteRedaction:
    def test_login_response_does_not_echo_provider_error(
        self, client: TestClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        with patch("app.api.routes.auth.code2session") as mock_code2session:
            mock_code2session.side_effect = WeChatAPIError(40029, "invalid code")

            with caplog.at_level(logging.ERROR, logger="app.api"):
                response = client.post("/auth/wechat/login", json={"code": "invalid_code"})

        assert response.status_code == 502
        assert response.json()["detail"] == "WeChat service error"
        assert "invalid code" not in response.text
        assert "40029" not in response.text

        messages = _wechat_log_messages(caplog)
        assert messages, "expected an app.api error log record"
        _assert_no_sensitive_markers([record.getMessage() for record in caplog.records])
        assert "invalid code" not in "\n".join(messages)
        assert all(record.exc_info is None for record in caplog.records), (
            "wechat route must never log exc_info / raw exception text"
        )

    def test_bind_response_does_not_echo_provider_error(
        self, client: TestClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        with patch("app.services.auth.dependencies.validate_session") as mock_validate:
            mock_validate.return_value = SessionInfo(
                user_id=uuid4(),
                session_id=uuid4(),
                expires_at=datetime.now(UTC),
                client_platform="web",
            )

            with patch("app.api.routes.auth.code2session") as mock_code2session:
                mock_code2session.side_effect = WeChatAPIError(-2, "HTTP 503: upstream error")

                with caplog.at_level(logging.ERROR, logger="app.api"):
                    response = client.post(
                        "/auth/wechat/bind",
                        json={"code": "bad_code"},
                        headers={"Authorization": "Bearer web_token"},
                    )

        assert response.status_code == 502
        assert response.json()["detail"] == "WeChat service error"
        assert "upstream error" not in response.text
        assert "503" not in response.text

        messages = _wechat_log_messages(caplog)
        assert messages
        _assert_no_sensitive_markers([record.getMessage() for record in caplog.records])
        assert all(record.exc_info is None for record in caplog.records), (
            "wechat bind route must never log exc_info / raw exception text"
        )

    def test_login_success_logs_no_provider_data(
        self, client: TestClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        token = "test_token"
        expires_at = datetime.now(UTC)

        with patch("app.api.routes.auth.code2session") as mock_code2session:
            mock_code2session.return_value = AsyncMock(
                openid="test_openid",
                session_key="test_session_key",
                unionid="test_unionid_xyz",
            )

            with patch("app.api.routes.auth.get_or_create_user_by_wechat") as mock_get_user:
                mock_get_user.return_value = uuid4()

                with patch("app.api.routes.auth.create_session") as mock_create:
                    mock_create.return_value = (token, expires_at)

                    with caplog.at_level(logging.INFO):
                        response = client.post(
                            "/auth/wechat/login",
                            json={"code": "valid_wechat_code"},
                        )

        assert response.status_code == 200
        _assert_no_sensitive_markers([record.getMessage() for record in caplog.records])
        assert all(record.exc_info is None for record in caplog.records)
