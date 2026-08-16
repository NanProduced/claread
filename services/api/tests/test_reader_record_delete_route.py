"""DELETE /reader/records/{record_id} route tests (Wave 8 B2).

Real-PostgreSQL route-level tests for the Reading Record delete API and
the post-delete access boundary:

- typed 200 DTO (deleted / already_deleted), uniform 404s, zero writes
  on 404 paths.
- after delete, every user-facing entry point fails closed: list
  (full + recent), opened, hide-from-recent, snapshot, events polling,
  stable document, Article RAG status/ensure, Ask threads (list /
  create-default / thread detail).
- ensure must not enqueue jobs for a deleted record.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.router import api_router
from app.database import connection as db_connection
from app.database.connection import init_connection
from tests.test_reader_orchestration_schema_baseline import BASELINE_SQL, DATABASE_URL

pytestmark = pytest.mark.anyio

AUTH_HEADERS = {"Authorization": "Bearer test_token"}


async def _make_pool(schema_name: str) -> asyncpg.Pool:
    async def _init_conn(conn: asyncpg.Connection) -> None:
        await init_connection(conn)

    async def _setup_conn(conn: asyncpg.Connection) -> None:
        await conn.execute(f'SET search_path TO "{schema_name}", public')

    return await asyncpg.create_pool(
        DATABASE_URL,
        min_size=1,
        max_size=4,
        init=_init_conn,
        setup=_setup_conn,
    )


@pytest.fixture
async def delete_api_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    schema_name = f"test_reader_delete_{uuid4().hex}"
    admin_conn = await asyncpg.connect(DATABASE_URL)
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)
        pool = await _make_pool(schema_name)
        monkeypatch.setattr(db_connection, "DB_POOL", pool)

        app = FastAPI()
        app.include_router(api_router)

        try:
            yield {"pool": pool, "app": app}
        finally:
            await pool.close()
    finally:
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


async def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


async def _insert_user(pool: asyncpg.Pool) -> UUID:
    async with pool.acquire() as conn:
        user_id = await conn.fetchval("INSERT INTO users DEFAULT VALUES RETURNING id")
    assert isinstance(user_id, UUID)
    return user_id


def _mock_auth(user_id: UUID):
    return patch(
        "app.services.auth.dependencies.validate_session",
        new_callable=AsyncMock,
        return_value=type("SessionInfo", (), {
            "user_id": user_id,
            "session_id": uuid4(),
        })(),
    )


async def _insert_record(
    pool: asyncpg.Pool,
    user_id: UUID,
) -> UUID:
    async with pool.acquire() as conn:
        record_id = await conn.fetchval(
            """
            INSERT INTO reading_records (user_id, source_type, title)
            VALUES ($1, 'text', 'Delete route record') RETURNING id
            """,
            user_id,
        )
    assert isinstance(record_id, UUID)
    return record_id


async def _insert_ask_thread(
    pool: asyncpg.Pool,
    user_id: UUID,
    record_id: UUID,
) -> UUID:
    async with pool.acquire() as conn:
        thread_id = await conn.fetchval(
            """
            INSERT INTO reader_ask_threads (user_id, title, reading_record_id)
            VALUES ($1, 'Bound thread', $2) RETURNING id
            """,
            user_id,
            record_id,
        )
    assert isinstance(thread_id, UUID)
    return thread_id


async def test_delete_route_typed_dto_and_idempotency(
    delete_api_env: dict[str, object],
) -> None:
    pool: asyncpg.Pool = delete_api_env["pool"]  # type: ignore[assignment]
    app: FastAPI = delete_api_env["app"]  # type: ignore[assignment]
    user_id = await _insert_user(pool)
    record_id = await _insert_record(pool, user_id)

    async with await _client(app) as client:
        with _mock_auth(user_id):
            first = await client.delete(
                f"/reader/records/{record_id}", headers=AUTH_HEADERS
            )
            assert first.status_code == 200, first.text
            first_body = first.json()
            assert first_body["record_id"] == str(record_id)
            assert first_body["status"] == "deleted"
            assert first_body["deleted_at"] is not None
            assert first_body["vector_gc_intent_recorded"] is True

            second = await client.delete(
                f"/reader/records/{record_id}", headers=AUTH_HEADERS
            )
            assert second.status_code == 200, second.text
            second_body = second.json()
            assert second_body["status"] == "already_deleted"
            assert second_body["deleted_at"] == first_body["deleted_at"]
            assert second_body["vector_gc_intent_recorded"] is True


async def test_delete_route_404s_with_zero_writes(
    delete_api_env: dict[str, object],
) -> None:
    pool: asyncpg.Pool = delete_api_env["pool"]  # type: ignore[assignment]
    app: FastAPI = delete_api_env["app"]  # type: ignore[assignment]
    owner_id = await _insert_user(pool)
    other_user_id = await _insert_user(pool)
    record_id = await _insert_record(pool, owner_id)

    async with await _client(app) as client:
        with _mock_auth(owner_id):
            missing = await client.delete(
                f"/reader/records/{uuid4()}", headers=AUTH_HEADERS
            )
            assert missing.status_code == 404
        with _mock_auth(other_user_id):
            non_owner = await client.delete(
                f"/reader/records/{record_id}", headers=AUTH_HEADERS
            )
            assert non_owner.status_code == 404

    async with pool.acquire() as conn:
        record = await conn.fetchrow(
            "SELECT deleted_at, lifecycle_status FROM reading_records WHERE id = $1",
            record_id,
        )
        assert record is not None
        assert record["deleted_at"] is None
        assert record["lifecycle_status"] == "active"
        assert await conn.fetchval(
            "SELECT COUNT(*) FROM reader_events WHERE reading_record_id = $1",
            record_id,
        ) == 0


async def test_post_delete_access_boundary_fails_closed(
    delete_api_env: dict[str, object],
) -> None:
    pool: asyncpg.Pool = delete_api_env["pool"]  # type: ignore[assignment]
    app: FastAPI = delete_api_env["app"]  # type: ignore[assignment]
    user_id = await _insert_user(pool)
    record_id = await _insert_record(pool, user_id)
    thread_id = await _insert_ask_thread(pool, user_id, record_id)

    async with await _client(app) as client:
        with _mock_auth(user_id):
            deleted = await client.delete(
                f"/reader/records/{record_id}", headers=AUTH_HEADERS
            )
            assert deleted.status_code == 200, deleted.text

            # list (full + recent): not visible.
            full = await client.get("/reader/records", headers=AUTH_HEADERS)
            assert full.status_code == 200
            assert full.json()["total"] == 0
            recent = await client.get(
                "/reader/records",
                params={"recent_only": "true"},
                headers=AUTH_HEADERS,
            )
            assert recent.status_code == 200
            assert recent.json()["total"] == 0

            # opened: 404.
            opened = await client.post(
                f"/reader/records/{record_id}/opened", headers=AUTH_HEADERS
            )
            assert opened.status_code == 404

            # hide-from-recent: 404.
            hide_recent = await client.delete(
                f"/reader/records/{record_id}/recent", headers=AUTH_HEADERS
            )
            assert hide_recent.status_code == 404

            # snapshot: 404.
            snapshot = await client.get(
                f"/reader/records/{record_id}/snapshot", headers=AUTH_HEADERS
            )
            assert snapshot.status_code == 404

            # events polling: 404.
            events = await client.get(
                f"/reader/records/{record_id}/events",
                params={"after_sequence": 0},
                headers=AUTH_HEADERS,
            )
            assert events.status_code == 404

            # stable document: 404.
            stable = await client.get(
                f"/reader/records/{record_id}/stable-document",
                headers=AUTH_HEADERS,
            )
            assert stable.status_code == 404

            # Article RAG status: 404 (record_not_found mapping).
            rag_status = await client.get(
                f"/reader/records/{record_id}/article-rag-index/status",
                headers=AUTH_HEADERS,
            )
            assert rag_status.status_code == 404

            # Article RAG ensure: 404, and must not enqueue anything.
            rag_ensure = await client.post(
                f"/reader/records/{record_id}/article-rag-index/ensure",
                headers=AUTH_HEADERS,
                json={"expected_generation": 1},
            )
            assert rag_ensure.status_code == 404

            # Ask: list threads / create default / thread detail all
            # fail closed for the deleted record (typed 400
            # reading_record_not_found via the shared snapshot guard).
            ask_threads = await client.get(
                f"/reader/records/{record_id}/ask/threads",
                headers=AUTH_HEADERS,
            )
            assert ask_threads.status_code == 400
            assert ask_threads.json()["detail"]["code"] == "reading_record_not_found"

            ask_default = await client.post(
                f"/reader/records/{record_id}/ask/threads/default",
                headers=AUTH_HEADERS,
            )
            assert ask_default.status_code == 400
            assert ask_default.json()["detail"]["code"] == "reading_record_not_found"

            ask_detail = await client.get(
                f"/reader/records/{record_id}/ask/threads/{thread_id}",
                headers=AUTH_HEADERS,
            )
            assert ask_detail.status_code == 400
            assert ask_detail.json()["detail"]["code"] == "reading_record_not_found"

    async with pool.acquire() as conn:
        # ensure must not have enqueued any new job / run / index run.
        assert await conn.fetchval(
            "SELECT COUNT(*) FROM reader_jobs WHERE reading_record_id = $1",
            record_id,
        ) == 0
        assert await conn.fetchval(
            "SELECT COUNT(*) FROM reader_article_rag_index_runs "
            "WHERE reading_record_id = $1",
            record_id,
        ) == 0
        # Ask thread data is retained, only user access is closed.
        assert await conn.fetchval(
            "SELECT COUNT(*) FROM reader_ask_threads WHERE id = $1",
            thread_id,
        ) == 1
