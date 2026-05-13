"""Multi-provider identity service tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from app.services.auth.identity import (
    IdentityConflictError,
    bind_identity_to_user,
    get_or_create_user_by_identity,
)


class TestGetOrCreateUserByIdentity:
    async def test_existing_identity_returns_existing_user(self) -> None:
        existing_user_id = UUID("11111111-1111-4111-8111-111111111111")
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"user_id": existing_user_id}
        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

        with patch("app.services.auth.identity.db_connection.DB_POOL", mock_pool):
            result = await get_or_create_user_by_identity(
                provider="phone",
                provider_user_id="+8613800138000",
            )

        assert result.user_id == existing_user_id
        assert result.created is False
        assert mock_conn.execute.call_count == 2
        mock_conn.fetchval.assert_not_called()

    async def test_new_identity_creates_user_and_identity(self) -> None:
        new_user_id = UUID("22222222-2222-4222-8222-222222222222")
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = None
        mock_conn.fetchval.return_value = new_user_id
        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

        with patch("app.services.auth.identity.db_connection.DB_POOL", mock_pool):
            result = await get_or_create_user_by_identity(
                provider="phone",
                provider_user_id="+8613800138000",
                auth_payload={"verified_by": "mock"},
            )

        assert result.user_id == new_user_id
        assert result.created is True
        assert mock_conn.fetchval.call_count == 1
        assert mock_conn.execute.call_count == 2

    async def test_new_wechat_identity_with_existing_unionid_reuses_user(self) -> None:
        existing_user_id = UUID("77777777-7777-4777-8777-777777777777")
        mock_conn = AsyncMock()
        mock_conn.fetchrow.side_effect = [
            None,
            {"user_id": existing_user_id},
        ]
        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

        with patch("app.services.auth.identity.db_connection.DB_POOL", mock_pool):
            result = await get_or_create_user_by_identity(
                provider="wechat_open",
                provider_user_id="web_openid",
                unionid="union_1",
            )

        assert result.user_id == existing_user_id
        assert result.created is False
        mock_conn.fetchval.assert_not_called()
        assert mock_conn.execute.call_count == 3

    async def test_existing_identity_rejects_unionid_owned_by_other_user(self) -> None:
        identity_user_id = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
        other_user_id = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
        mock_conn = AsyncMock()
        mock_conn.fetchrow.side_effect = [
            {"user_id": identity_user_id},
            {"user_id": other_user_id},
        ]
        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

        with patch("app.services.auth.identity.db_connection.DB_POOL", mock_pool):
            with pytest.raises(IdentityConflictError) as exc_info:
                await get_or_create_user_by_identity(
                    provider="wechat_miniprogram",
                    provider_user_id="mp_openid",
                    unionid="union_split",
                )

        assert exc_info.value.existing_user_id == other_user_id

    async def test_db_pool_missing_raises_runtime_error(self) -> None:
        with patch("app.services.auth.identity.db_connection.DB_POOL", None):
            with pytest.raises(RuntimeError):
                await get_or_create_user_by_identity(
                    provider="phone",
                    provider_user_id="+8613800138000",
                )


class TestBindIdentityToUser:
    async def test_bind_new_identity_creates_row(self) -> None:
        user_id = UUID("33333333-3333-4333-8333-333333333333")
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = None
        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

        with patch("app.services.auth.identity.db_connection.DB_POOL", mock_pool):
            result = await bind_identity_to_user(
                user_id=user_id,
                provider="phone",
                provider_user_id="+8613800138000",
            )

        assert result == "created"
        assert mock_conn.execute.call_count == 2

    async def test_bind_existing_identity_same_user_is_idempotent(self) -> None:
        user_id = UUID("44444444-4444-4444-8444-444444444444")
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"user_id": user_id}
        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

        with patch("app.services.auth.identity.db_connection.DB_POOL", mock_pool):
            result = await bind_identity_to_user(
                user_id=user_id,
                provider="phone",
                provider_user_id="+8613800138000",
            )

        assert result == "already_bound"
        assert mock_conn.execute.call_count == 2

    async def test_bind_existing_identity_other_user_raises_conflict(self) -> None:
        user_id = UUID("55555555-5555-4555-8555-555555555555")
        other_user_id = UUID("66666666-6666-4666-8666-666666666666")
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"user_id": other_user_id}
        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

        with patch("app.services.auth.identity.db_connection.DB_POOL", mock_pool):
            with pytest.raises(IdentityConflictError) as exc_info:
                await bind_identity_to_user(
                    user_id=user_id,
                    provider="phone",
                    provider_user_id="+8613800138000",
                )

        assert exc_info.value.existing_user_id == other_user_id

    async def test_bind_new_wechat_identity_rejects_unionid_owned_by_other_user(self) -> None:
        user_id = UUID("88888888-8888-4888-8888-888888888888")
        other_user_id = UUID("99999999-9999-4999-8999-999999999999")
        mock_conn = AsyncMock()
        mock_conn.fetchrow.side_effect = [
            None,
            {"user_id": other_user_id},
        ]
        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

        with patch("app.services.auth.identity.db_connection.DB_POOL", mock_pool):
            with pytest.raises(IdentityConflictError) as exc_info:
                await bind_identity_to_user(
                    user_id=user_id,
                    provider="wechat_open",
                    provider_user_id="web_openid",
                    unionid="union_2",
                )

        assert exc_info.value.existing_user_id == other_user_id

    async def test_bind_new_wechat_identity_accepts_unionid_owned_by_same_user(self) -> None:
        user_id = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
        mock_conn = AsyncMock()
        mock_conn.fetchrow.side_effect = [
            None,
            {"user_id": user_id},
        ]
        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

        with patch("app.services.auth.identity.db_connection.DB_POOL", mock_pool):
            result = await bind_identity_to_user(
                user_id=user_id,
                provider="wechat_open",
                provider_user_id="web_openid",
                unionid="union_3",
            )

        assert result == "created"
        assert mock_conn.execute.call_count == 3
