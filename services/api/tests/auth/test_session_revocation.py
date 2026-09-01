"""AUTH-F1A: revoke_all_sessions service contract tests.

只验证 service 能力：撤销该用户全部 active session 并返回实际撤销数量。
不新增 API route（由静态 guard 锁定）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from app.services.auth import revoke_all_sessions
from app.services.auth.session import revoke_all_sessions as session_revoke_all_sessions

API_ROOT = Path(__file__).resolve().parents[2]
AUTH_ROUTES_SOURCE = (API_ROOT / "app" / "api" / "routes" / "auth.py").read_text(
    encoding="utf-8"
)


def _make_pool(mock_conn: AsyncMock) -> MagicMock:
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
    return mock_pool


class TestRevokeAllSessions:
    async def test_revokes_only_active_sessions_and_returns_actual_count(self) -> None:
        user_id = UUID("e0e0e0e0-e0e0-4e0e-8e0e-e0e0e0e0e0e0")
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [
            {"id": UUID("e1e1e1e1-e1e1-4e1e-8e1e-e1e1e1e1e1e1")},
            {"id": UUID("e2e2e2e2-e2e2-4e2e-8e2e-e2e2e2e2e2e2")},
            {"id": UUID("e3e3e3e3-e3e3-4e3e-8e3e-e3e3e3e3e3e3")},
        ]

        with patch("app.services.auth.session.db_connection.DB_POOL", _make_pool(mock_conn)):
            revoked = await revoke_all_sessions(user_id)

        assert revoked == 3
        assert mock_conn.fetch.await_count == 1
        call_args: tuple[Any, ...] = mock_conn.fetch.await_args.args
        sql = call_args[0]
        assert "UPDATE user_sessions" in sql
        assert "status = 'revoked'" in sql
        assert "revoked_at = NOW()" in sql
        assert "WHERE user_id = $1 AND status = 'active'" in sql
        assert call_args[1] == user_id

    async def test_returns_zero_when_user_has_no_active_sessions(self) -> None:
        user_id = UUID("e4e4e4e4-e4e4-4e4e-8e4e-e4e4e4e4e4e4")
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []

        with patch("app.services.auth.session.db_connection.DB_POOL", _make_pool(mock_conn)):
            revoked = await revoke_all_sessions(user_id)

        assert revoked == 0
        assert mock_conn.fetch.await_count == 1

    async def test_pool_missing_raises_runtime_error_fail_closed(self) -> None:
        """AUTH-F1A review: 连接池未初始化必须 fail-closed，不得静默返回 0。"""
        with patch("app.services.auth.session.db_connection.DB_POOL", None):
            with pytest.raises(RuntimeError, match="Database pool not initialized"):
                await revoke_all_sessions(UUID("e5e5e5e5-e5e5-4e5e-8e5e-e5e5e5e5e5e5"))

    async def test_exported_from_auth_package(self) -> None:
        assert revoke_all_sessions is session_revoke_all_sessions


def test_revoke_all_sessions_has_no_api_route() -> None:
    """AUTH-F1A 只提供 service 能力，不新增 API route。"""
    assert "revoke_all_sessions" not in AUTH_ROUTES_SOURCE
