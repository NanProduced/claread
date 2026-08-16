"""Session advisory-lock helper tests (Wave 9).

- deterministic signed int64 key derivation from namespace + UUID
  (stdlib only, stable across processes).
- acquire / try-acquire / unlock semantics across two connections.
- connection exit auto-releases the session lock (PostgreSQL contract).
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.services.reader_orchestration.advisory_lock import (
    SessionAdvisoryLock,
    advisory_lock_key,
)
from tests.test_reader_orchestration_schema_baseline import DATABASE_URL

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.chain_article_rag,
    pytest.mark.life_permanent_regression,
]

_VECTOR_MUTATION_NAMESPACE = "claread.article_rag.vector_mutation"


class TestKeyDerivation:
    def test_key_is_deterministic(self) -> None:
        uid = uuid4()
        assert advisory_lock_key(_VECTOR_MUTATION_NAMESPACE, uid) == advisory_lock_key(
            _VECTOR_MUTATION_NAMESPACE, uid
        )

    def test_key_is_signed_int64(self) -> None:
        for uid in (uuid4(), uuid4(), UUID(int=0), UUID(int=2**127 - 1)):
            key = advisory_lock_key(_VECTOR_MUTATION_NAMESPACE, uid)
            assert isinstance(key, int)
            assert -(2**63) <= key < 2**63

    def test_different_identities_yield_different_keys(self) -> None:
        a = uuid4()
        b = uuid4()
        assert a != b
        assert advisory_lock_key(_VECTOR_MUTATION_NAMESPACE, a) != advisory_lock_key(
            _VECTOR_MUTATION_NAMESPACE, b
        )

    def test_different_namespaces_yield_different_keys(self) -> None:
        uid = uuid4()
        assert advisory_lock_key("ns.one", uid) != advisory_lock_key("ns.two", uid)


class TestSessionLockSemantics:
    @pytest.fixture
    async def conns(self) -> tuple[asyncpg.Connection, asyncpg.Connection]:
        conn_a = await asyncpg.connect(DATABASE_URL)
        conn_b = await asyncpg.connect(DATABASE_URL)
        try:
            yield conn_a, conn_b
        finally:
            await conn_a.close()
            await conn_b.close()

    async def test_try_acquire_is_mutually_exclusive(self, conns: tuple) -> None:
        conn_a, conn_b = conns
        key = advisory_lock_key(_VECTOR_MUTATION_NAMESPACE, uuid4())
        lock_a = SessionAdvisoryLock(conn_a, key)
        lock_b = SessionAdvisoryLock(conn_b, key)

        assert await lock_a.try_acquire() is True
        assert lock_a.held is True
        assert await lock_b.try_acquire() is False
        assert lock_b.held is False

        assert await lock_a.unlock() is True
        assert lock_a.held is False
        assert await lock_b.try_acquire() is True

    async def test_acquire_blocks_until_released(self, conns: tuple) -> None:
        conn_a, conn_b = conns
        key = advisory_lock_key(_VECTOR_MUTATION_NAMESPACE, uuid4())
        lock_a = SessionAdvisoryLock(conn_a, key)
        lock_b = SessionAdvisoryLock(conn_b, key)
        await lock_a.try_acquire()

        acquired_by_b = asyncio.Event()

        async def _contend() -> None:
            await lock_b.acquire()
            acquired_by_b.set()

        task = asyncio.create_task(_contend())
        await asyncio.sleep(0.05)
        assert not acquired_by_b.is_set()
        await lock_a.unlock()
        await asyncio.wait_for(task, timeout=5)
        assert acquired_by_b.is_set()
        assert lock_b.held is True

    async def test_unlock_releases_only_own_session(self, conns: tuple) -> None:
        conn_a, conn_b = conns
        key = advisory_lock_key(_VECTOR_MUTATION_NAMESPACE, uuid4())
        lock_a = SessionAdvisoryLock(conn_a, key)
        lock_b = SessionAdvisoryLock(conn_b, key)
        await lock_a.try_acquire()
        # A non-holder unlock is a no-op.
        assert await lock_b.unlock() is True
        assert await lock_b.try_acquire() is False

    async def test_connection_exit_releases_session_lock(self, conns: tuple) -> None:
        conn_a, conn_b = conns
        key = advisory_lock_key(_VECTOR_MUTATION_NAMESPACE, uuid4())
        lock_a = SessionAdvisoryLock(conn_a, key)
        await lock_a.try_acquire()
        await conn_a.close()

        lock_b = SessionAdvisoryLock(conn_b, key)
        assert await lock_b.try_acquire() is True

    async def test_unlock_in_finally_pattern(self, conns: tuple) -> None:
        conn_a, conn_b = conns
        key = advisory_lock_key(_VECTOR_MUTATION_NAMESPACE, uuid4())

        lock = SessionAdvisoryLock(conn_a, key)
        try:
            await lock.acquire()
            assert lock.held is True
        finally:
            await lock.unlock()
        assert lock.held is False

        lock_b = SessionAdvisoryLock(conn_b, key)
        assert await lock_b.try_acquire() is True
