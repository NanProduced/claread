"""认证 API 路由测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.auth.session import SessionInfo
from app.services.auth.wechat import WeChatAPIError


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


class TestWeChatLogin:
    """POST /auth/wechat/login"""

    def test_login_success(self, client: TestClient) -> None:
        """完整登录流程返回 user_id + session_token + expires_at"""
        user_id = uuid4()
        token = "test_token_abc123"
        expires_at = datetime.now(UTC)

        with patch("app.api.routes.auth.code2session") as mock_code2session:
            mock_code2session.return_value = AsyncMock(
                openid="test_openid",
                session_key="test_session_key",
                unionid=None,
            )

            with patch("app.api.routes.auth.get_or_create_user_by_wechat") as mock_get_user:
                mock_get_user.return_value = user_id

                with patch("app.api.routes.auth.create_session") as mock_create:
                    mock_create.return_value = (token, expires_at)

                    response = client.post(
                        "/auth/wechat/login",
                        json={"code": "valid_wechat_code"},
                    )

        assert response.status_code == 200
        data = response.json()
        assert data["user_id"] == str(user_id)
        assert data["session_token"] == token
        assert "expires_at" in data

    def test_login_missing_code_returns_422(self, client: TestClient) -> None:
        """缺少 code 字段返回 422（Pydantic 验证失败）"""
        response = client.post("/auth/wechat/login", json={})
        assert response.status_code == 422

    def test_login_empty_code_returns_422(self, client: TestClient) -> None:
        """code 为空字符串返回 422（Pydantic 验证失败）"""
        response = client.post("/auth/wechat/login", json={"code": ""})
        assert response.status_code == 422

    def test_login_wechat_error_returns_502(self, client: TestClient) -> None:
        """微信接口返回 errcode → 502 Bad Gateway"""
        with patch("app.api.routes.auth.code2session") as mock_code2session:
            mock_code2session.side_effect = WeChatAPIError(40029, "invalid code")

            response = client.post(
                "/auth/wechat/login",
                json={"code": "invalid_code"},
            )

        assert response.status_code == 502
        assert "WeChat service error" in response.json()["detail"]
        assert "invalid code" in response.json()["detail"]

    def test_login_wechat_http_error_returns_502(self, client: TestClient) -> None:
        """微信服务 5xx / 非 JSON 响应 → 502 Bad Gateway"""

        with patch("app.api.routes.auth.code2session") as mock_code2session:
            mock_code2session.side_effect = WeChatAPIError(-2, "HTTP 503: upstream error")

            response = client.post(
                "/auth/wechat/login",
                json={"code": "server_error_code"},
            )

        assert response.status_code == 502
        assert "upstream error" in response.json()["detail"]

    def test_login_request_contract(self, client: TestClient) -> None:
        """只接受 JSON body，不接受 form-encoded"""
        # 无 body → FastAPI 422（缺少必需字段）
        response = client.post("/auth/wechat/login")
        assert response.status_code == 422

        # 无效 JSON
        response = client.post(
            "/auth/wechat/login",
            content=b"not-json",
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 422  # validation error


class TestLogout:
    """POST /auth/session/logout"""

    def test_logout_success(self, client: TestClient) -> None:
        """有效 token 登出返回 ok=True"""
        with patch("app.api.routes.auth.revoke_session") as mock_revoke:
            mock_revoke.return_value = True

            response = client.post(
                "/auth/session/logout",
                json={"session_token": "valid_token"},
            )

        assert response.status_code == 200
        assert response.json()["ok"] is True
        mock_revoke.assert_called_once_with("valid_token")

    def test_logout_idempotent(self, client: TestClient) -> None:
        """无效 token（不存在/已撤销）也返回 ok=True（幂等）"""
        with patch("app.api.routes.auth.revoke_session") as mock_revoke:
            mock_revoke.return_value = False

            response = client.post(
                "/auth/session/logout",
                json={"session_token": "already_revoked_token"},
            )

        assert response.status_code == 200
        assert response.json()["ok"] is True

    def test_logout_missing_token_returns_422(self, client: TestClient) -> None:
        """缺少 session_token 返回 422（Pydantic 验证失败）"""
        response = client.post("/auth/session/logout", json={})
        assert response.status_code == 422

    def test_logout_empty_token_returns_422(self, client: TestClient) -> None:
        """session_token 为空字符串返回 422（Pydantic 验证失败）"""
        response = client.post(
            "/auth/session/logout",
            json={"session_token": ""},
        )
        assert response.status_code == 422


class TestGetMe:
    """GET /auth/session/me"""

    def test_me_without_auth_header_returns_401(self, client: TestClient) -> None:
        """无 Authorization header → 401"""
        response = client.get("/auth/session/me")
        assert response.status_code == 401
        assert "Missing authorization header" in response.json()["detail"]

    def test_me_with_bearer_token_returns_user_info(
        self, client: TestClient
    ) -> None:
        """有效 Bearer token → 返回 user_id + session_id"""
        user_id = uuid4()
        session_id = uuid4()
        token = "valid_session_token"

        with patch(
            "app.services.auth.dependencies.validate_session"
        ) as mock_validate:
            mock_validate.return_value = SessionInfo(
                user_id=user_id,
                session_id=session_id,
                expires_at=datetime.now(UTC),
                client_platform="wechat_miniprogram",
            )

            with patch("app.api.routes.auth.get_user_profile") as mock_profile:
                mock_profile.return_value = {
                    "nickname": "Test User",
                    "avatar_url": "https://example.com/avatar.png",
                    "cumulative_article_count": 3,
                    "settings": {"theme": "system"},
                }

                response = client.get(
                    "/auth/session/me",
                    headers={"Authorization": f"Bearer {token}"},
                )

        assert response.status_code == 200
        data = response.json()
        assert data["user_id"] == str(user_id)
        assert data["session_id"] == str(session_id)

    def test_me_with_invalid_token_returns_401(self, client: TestClient) -> None:
        """无效/过期/已撤销 token → 401"""
        with patch(
            "app.services.auth.dependencies.validate_session"
        ) as mock_validate:
            mock_validate.return_value = None

            response = client.get(
                "/auth/session/me",
                headers={"Authorization": "Bearer invalid_or_expired_token"},
            )

        assert response.status_code == 401
        assert "Invalid or expired session" in response.json()["detail"]

    def test_me_with_empty_bearer_token_returns_401(self, client: TestClient) -> None:
        """Bearer token 为空字符串 → 401"""
        with patch(
            "app.services.auth.dependencies.validate_session"
        ) as mock_validate:
            mock_validate.return_value = None

            response = client.get(
                "/auth/session/me",
                headers={"Authorization": "Bearer "},
            )

        assert response.status_code == 401
