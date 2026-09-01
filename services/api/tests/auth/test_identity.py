"""Multi-provider identity service tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from app.services.auth.identity import (
    IdentityConflictError,
    bind_identity_to_user,
    get_or_create_user_by_identity,
)


def _make_mock_conn() -> AsyncMock:
    """AsyncMock connection whose transaction() supports ``async with``."""
    mock_conn = AsyncMock()
    tx_ctx = MagicMock()
    tx_ctx.__aenter__ = AsyncMock(return_value=None)
    tx_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_conn.transaction = MagicMock(return_value=tx_ctx)
    return mock_conn


class TestGetOrCreateUserByIdentity:
    async def test_existing_identity_returns_existing_user(self) -> None:
        existing_user_id = UUID("11111111-1111-4111-8111-111111111111")
        mock_conn = _make_mock_conn()
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
        mock_conn = _make_mock_conn()
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
        mock_conn = _make_mock_conn()
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
        mock_conn = _make_mock_conn()
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
        mock_conn = _make_mock_conn()
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
        mock_conn = _make_mock_conn()
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
        mock_conn = _make_mock_conn()
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
        mock_conn = _make_mock_conn()
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
        mock_conn = _make_mock_conn()
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


class _RecordingTransaction:
    """Fake asyncpg transaction that records begin/end on its connection."""

    def __init__(self, conn: _RecordingConnection) -> None:
        self._conn = conn

    async def __aenter__(self) -> None:
        self._conn.events.append(("tx", "begin"))

    async def __aexit__(self, *exc_info: object) -> bool:
        self._conn.events.append(("tx", "end"))
        return False


class _RecordingConnection:
    """Fake asyncpg connection recording the exact statement order.

    AUTH-F1A contract: the advisory xact lock, every lookup and every write
    of the identity services must run inside one explicit transaction.
    """

    def __init__(
        self,
        *,
        fetchrow_results: list[Any] | None = None,
        fetchval_results: list[Any] | None = None,
    ) -> None:
        self.events: list[tuple[str, str]] = []
        self._fetchrow_results = list(fetchrow_results or [])
        self._fetchval_results = list(fetchval_results or [])

    def transaction(self) -> _RecordingTransaction:
        return _RecordingTransaction(self)

    async def execute(self, query: str, *args: Any) -> str:
        self.events.append(("execute", " ".join(query.split())))
        return "INSERT 0 1"

    async def fetchrow(self, query: str, *args: Any) -> Any:
        self.events.append(("fetchrow", " ".join(query.split())))
        if not self._fetchrow_results:
            return None
        value = self._fetchrow_results.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    async def fetchval(self, query: str, *args: Any) -> Any:
        self.events.append(("fetchval", " ".join(query.split())))
        if not self._fetchval_results:
            return None
        return self._fetchval_results.pop(0)


def _recording_pool(conn: _RecordingConnection) -> MagicMock:
    pool = MagicMock()
    pool.acquire.return_value.__aenter__.return_value = conn
    return pool


def _assert_single_transaction(events: list[tuple[str, str]]) -> None:
    """The whole statement sequence must be wrapped in exactly one transaction."""
    assert events[0] == ("tx", "begin"), "transaction must open before the first statement"
    assert events[-1] == ("tx", "end"), "no statement may run after the transaction ends"
    assert [event for event in events if event[0] == "tx"] == [
        ("tx", "begin"),
        ("tx", "end"),
    ], "advisory lock, lookups and writes must share one transaction"


_ADVISORY_LOCK = ("execute", "SELECT pg_advisory_xact_lock($1)")


class TestGetOrCreateUserByIdentityTransaction:
    """AUTH-F1A: 显式事务必须覆盖 advisory lock、全部查询与写入。"""

    async def test_new_identity_locks_and_writes_inside_one_transaction(self) -> None:
        new_user_id = UUID("d0d0d0d0-d0d0-4d0d-8d0d-d0d0d0d0d0d0")
        conn = _RecordingConnection(fetchrow_results=[None], fetchval_results=[new_user_id])

        with patch("app.services.auth.identity.db_connection.DB_POOL", _recording_pool(conn)):
            result = await get_or_create_user_by_identity(
                provider="phone",
                provider_user_id="+8613800138000",
            )

        assert result.user_id == new_user_id
        assert result.created is True
        _assert_single_transaction(conn.events)
        assert conn.events[1] == _ADVISORY_LOCK

    async def test_unionid_and_identity_locks_both_inside_transaction(self) -> None:
        new_user_id = UUID("d1d1d1d1-d1d1-4d1d-8d1d-d1d1d1d1d1d1")
        conn = _RecordingConnection(
            fetchrow_results=[None, None],
            fetchval_results=[new_user_id],
        )

        with patch("app.services.auth.identity.db_connection.DB_POOL", _recording_pool(conn)):
            result = await get_or_create_user_by_identity(
                provider="wechat_open",
                provider_user_id="web_openid",
                unionid="union_tx",
            )

        assert result.created is True
        _assert_single_transaction(conn.events)
        assert conn.events[1] == _ADVISORY_LOCK
        assert conn.events[2] == _ADVISORY_LOCK

    async def test_existing_identity_update_runs_inside_transaction(self) -> None:
        existing_user_id = UUID("d2d2d2d2-d2d2-4d2d-8d2d-d2d2d2d2d2d2")
        conn = _RecordingConnection(fetchrow_results=[{"user_id": existing_user_id}])

        with patch("app.services.auth.identity.db_connection.DB_POOL", _recording_pool(conn)):
            result = await get_or_create_user_by_identity(
                provider="phone",
                provider_user_id="+8613800138000",
            )

        assert result.created is False
        _assert_single_transaction(conn.events)
        assert conn.events[1] == _ADVISORY_LOCK

    async def test_failure_closes_transaction(self) -> None:
        conn = _RecordingConnection(fetchrow_results=[RuntimeError("lookup exploded")])

        with patch("app.services.auth.identity.db_connection.DB_POOL", _recording_pool(conn)):
            with pytest.raises(RuntimeError, match="lookup exploded"):
                await get_or_create_user_by_identity(
                    provider="phone",
                    provider_user_id="+8613800138000",
                )

        assert conn.events[0] == ("tx", "begin")
        assert conn.events[-1] == ("tx", "end")


class TestBindIdentityToUserTransaction:
    """AUTH-F1A: bind 路径同样必须整体处于一个显式事务。"""

    async def test_new_binding_locks_and_writes_inside_one_transaction(self) -> None:
        user_id = UUID("d3d3d3d3-d3d3-4d3d-8d3d-d3d3d3d3d3d3")
        conn = _RecordingConnection(fetchrow_results=[None])

        with patch("app.services.auth.identity.db_connection.DB_POOL", _recording_pool(conn)):
            result = await bind_identity_to_user(
                user_id=user_id,
                provider="phone",
                provider_user_id="+8613800138000",
            )

        assert result == "created"
        _assert_single_transaction(conn.events)
        assert conn.events[1] == _ADVISORY_LOCK

    async def test_idempotent_binding_update_runs_inside_transaction(self) -> None:
        user_id = UUID("d4d4d4d4-d4d4-4d4d-8d4d-d4d4d4d4d4d4")
        conn = _RecordingConnection(fetchrow_results=[{"user_id": user_id}])

        with patch("app.services.auth.identity.db_connection.DB_POOL", _recording_pool(conn)):
            result = await bind_identity_to_user(
                user_id=user_id,
                provider="phone",
                provider_user_id="+8613800138000",
            )

        assert result == "already_bound"
        _assert_single_transaction(conn.events)
        assert conn.events[1] == _ADVISORY_LOCK

    async def test_conflict_closes_transaction(self) -> None:
        user_id = UUID("d5d5d5d5-d5d5-4d5d-8d5d-d5d5d5d5d5d5")
        other_user_id = UUID("d6d6d6d6-d6d6-4d6d-8d6d-d6d6d6d6d6d6")
        conn = _RecordingConnection(fetchrow_results=[{"user_id": other_user_id}])

        with patch("app.services.auth.identity.db_connection.DB_POOL", _recording_pool(conn)):
            with pytest.raises(IdentityConflictError):
                await bind_identity_to_user(
                    user_id=user_id,
                    provider="phone",
                    provider_user_id="+8613800138000",
                )

        assert conn.events[0] == ("tx", "begin")
        assert conn.events[-1] == ("tx", "end")
