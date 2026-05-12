"""微信登录和业务会话测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from app.services.auth.session import (
    create_session,
    get_or_create_user_by_wechat,
    revoke_session,
    validate_session,
)
from app.services.auth.wechat import WeChatAPIError, code2session


class TestWeChatCode2Session:
    """code2Session API 测试"""

    async def test_code2session_success(self) -> None:
        """微信返回正常 session"""
        mock_response = {
            "openid": "test_openid_123",
            "session_key": "test_session_key_abc",
            "unionid": None,
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_response_obj = MagicMock()
            mock_response_obj.json.return_value = mock_response
            mock_response_obj.raise_for_status = MagicMock()
            mock_client.get.return_value = mock_response_obj
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            with patch("app.services.auth.wechat.get_settings") as mock_settings:
                mock_settings.return_value.wechat_app_id = "wx_test_appid"
                mock_settings.return_value.wechat_app_secret = "test_secret"

                result = await code2session("valid_code")

        assert result.openid == "test_openid_123"
        assert result.session_key == "test_session_key_abc"
        assert result.unionid is None

    async def test_code2session_with_unionid(self) -> None:
        """微信返回包含 unionid 的 session"""
        mock_response = {
            "openid": "test_openid_123",
            "session_key": "test_session_key_abc",
            "unionid": "test_unionid_xyz",
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_response_obj = MagicMock()
            mock_response_obj.json.return_value = mock_response
            mock_response_obj.raise_for_status = MagicMock()
            mock_client.get.return_value = mock_response_obj
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            with patch("app.services.auth.wechat.get_settings") as mock_settings:
                mock_settings.return_value.wechat_app_id = "wx_test_appid"
                mock_settings.return_value.wechat_app_secret = "test_secret"

                result = await code2session("valid_code")

        assert result.unionid == "test_unionid_xyz"

    async def test_code2session_invalid_code(self) -> None:
        """微信返回错误码"""
        mock_response = {"errcode": 40029, "errmsg": "invalid code"}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_response_obj = MagicMock()
            mock_response_obj.json.return_value = mock_response
            mock_response_obj.raise_for_status = MagicMock()
            mock_client.get.return_value = mock_response_obj
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            with patch("app.services.auth.wechat.get_settings") as mock_settings:
                mock_settings.return_value.wechat_app_id = "wx_test_appid"
                mock_settings.return_value.wechat_app_secret = "test_secret"

                with pytest.raises(WeChatAPIError) as exc_info:
                    await code2session("invalid_code")

        assert exc_info.value.errcode == 40029

    async def test_code2session_missing_credentials(self) -> None:
        """未配置微信凭证时抛出错误"""
        with patch("app.services.auth.wechat.get_settings") as mock_settings:
            mock_settings.return_value.wechat_app_id = ""
            mock_settings.return_value.wechat_app_secret = ""

            with pytest.raises(WeChatAPIError) as exc_info:
                await code2session("any_code")

        assert exc_info.value.errcode == -1
        assert "not configured" in exc_info.value.errmsg


class TestGetOrCreateUserByWechat:
    """根据微信 openid 查找或创建用户测试"""

    async def test_existing_user_returns_user_id(self) -> None:
        """已存在的微信用户直接返回 user_id"""
        existing_user_id = UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")

        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"user_id": existing_user_id}

        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        mock_pool.acquire.return_value.__aexit__.return_value = None

        # Patch via module reference (session.py uses `from app.database import connection`)
        with patch("app.services.auth.session.db_connection.DB_POOL", mock_pool):
            result = await get_or_create_user_by_wechat(
                openid="existing_openid",
                unionid=None,
                auth_payload={},
            )

        assert result == existing_user_id
        # advisory lock (execute) + SELECT identity (fetchrow) = early return
        assert mock_conn.execute.call_count == 1
        mock_conn.fetchval.assert_not_called()
        mock_conn.fetchrow.assert_called_once()

    async def test_new_user_creates_user_and_identity(self) -> None:
        """新用户同时创建 user 和 user_identity"""
        new_user_id = UUID("b2c3d4e5-f6a7-8901-bcde-f12345678901")

        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = None
        mock_conn.fetchval.return_value = new_user_id

        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        mock_pool.acquire.return_value.__aexit__.return_value = None

        with patch("app.services.auth.session.db_connection.DB_POOL", mock_pool):
            with patch("app.services.auth.session.get_settings") as mock_settings:
                mock_settings.return_value.wechat_app_id = "wx_appid"

                result = await get_or_create_user_by_wechat(
                    openid="new_openid",
                    unionid="test_unionid",
                    auth_payload={"session_key": "sk_abc"},
                )

        assert result == new_user_id
        assert mock_conn.fetchval.call_count == 1
        # 2 execute calls: advisory lock + identity insert
        assert mock_conn.execute.call_count == 2
        mock_conn.fetchrow.assert_called_once()


class TestSessionCreate:
    """Session 创建测试"""

    async def test_create_session_returns_token_and_expiry(self) -> None:
        """create_session 返回 token 明文和过期时间"""
        user_id = UUID("c3d4e5f6-a7b8-9012-cdef-123456789abc")

        mock_conn = AsyncMock()
        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        mock_pool.acquire.return_value.__aexit__.return_value = None

        with patch("app.services.auth.session.db_connection.DB_POOL", mock_pool):
            with patch("app.services.auth.session.get_settings") as mock_settings:
                mock_settings.return_value.auth_session_expiry_days = 30

                token, expires_at = await create_session(
                    user_id=user_id,
                    provider="wechat_miniprogram",
                    provider_user_id="openid_123",
                    auth_payload={"session_key": "sk_test"},
                    client_platform="wechat_miniprogram",
                    ip_address="127.0.0.1",
                )

        assert isinstance(token, str)
        assert len(token) > 20
        assert expires_at > datetime.now(UTC)


class TestSessionValidate:
    """Session 验证测试"""

    async def test_validate_session_success(self) -> None:
        """有效 session 返回 SessionInfo"""
        user_id = UUID("d4e5f6a7-b8c9-0123-defa-23456789abcd")
        session_id = UUID("e5f6a7b8-c9d0-1234-efab-3456789abcde")
        expires = datetime.now(UTC) + timedelta(days=1)

        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {
            "user_id": user_id,
            "id": session_id,
            "expires_at": expires,
            "client_platform": "wechat_miniprogram",
            "status": "active",
        }

        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        mock_pool.acquire.return_value.__aexit__.return_value = None

        with patch("app.services.auth.session.db_connection.DB_POOL", mock_pool):
            result = await validate_session("any_valid_token")

        assert result is not None
        assert result.user_id == user_id
        assert result.session_id == session_id

    async def test_validate_session_not_found(self) -> None:
        """不存在的 token 返回 None"""
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = None

        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        mock_pool.acquire.return_value.__aexit__.return_value = None

        with patch("app.services.auth.session.db_connection.DB_POOL", mock_pool):
            result = await validate_session("nonexistent_token")

        assert result is None

    async def test_validate_session_expired(self) -> None:
        """过期的 session 返回 None 并标记为 expired"""
        user_id = UUID("f6a7b8c9-d0e1-2345-fabc-456789abcdef")
        session_id = UUID("a7b8c9d0-e1f2-3456-abcd-56789abcdef0")
        expires = datetime.now(UTC) - timedelta(days=1)

        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {
            "user_id": user_id,
            "id": session_id,
            "expires_at": expires,
            "client_platform": "wechat_miniprogram",
            "status": "active",
        }

        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        mock_pool.acquire.return_value.__aexit__.return_value = None

        with patch("app.services.auth.session.db_connection.DB_POOL", mock_pool):
            result = await validate_session("expired_token")

        assert result is None
        mock_conn.execute.assert_called()

    async def test_validate_session_revoked(self) -> None:
        """已撤销的 session 返回 None"""
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {
            "user_id": UUID("b8c9d0e1-f2a3-4567-bcde-6789abcdef02"),
            "id": UUID("c9d0e1f2-a3b4-5678-cdef-789abcdef012"),
            "expires_at": datetime.now(UTC) + timedelta(days=1),
            "client_platform": "wechat_miniprogram",
            "status": "revoked",
        }

        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        mock_pool.acquire.return_value.__aexit__.return_value = None

        with patch("app.services.auth.session.db_connection.DB_POOL", mock_pool):
            result = await validate_session("revoked_token")

        assert result is None


class TestSessionRevoke:
    """Session 撤销测试"""

    async def test_revoke_session_success(self) -> None:
        """成功撤销 session 返回 True"""
        mock_conn = AsyncMock()
        mock_conn.execute.return_value = "UPDATE 1"

        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        mock_pool.acquire.return_value.__aexit__.return_value = None

        with patch("app.services.auth.session.db_connection.DB_POOL", mock_pool):
            result = await revoke_session("active_token")

        assert result is True

    async def test_revoke_session_not_found(self) -> None:
        """不存在的 token 返回 False"""
        mock_conn = AsyncMock()
        mock_conn.execute.return_value = "UPDATE 0"

        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        mock_pool.acquire.return_value.__aexit__.return_value = None

        with patch("app.services.auth.session.db_connection.DB_POOL", mock_pool):
            result = await revoke_session("nonexistent_token")

        assert result is False
