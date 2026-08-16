"""Reading Record recent-reading visibility lifecycle (Wave 8 B1).

DB-backed route tests for:

- ``recent_hidden_at`` schema column + partial index for the recent list.
- ``GET /reader/records?recent_only=true`` excludes hidden and
  never-opened records while ``recent_only=false`` (default) keeps the
  full-list semantics.
- ``DELETE /reader/records/{record_id}/recent`` hides a record from the
  recent list (idempotent, keeps the first timestamp, does not touch
  ``updated_at`` / ``last_opened_at`` / events).
- ``POST /reader/records/{record_id}/opened`` restores a hidden record
  to the recent list in the same UPDATE.
- Owner isolation and unified 404s for missing / deleted / non-owner
  records.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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


# ---------------------------------------------------------------------------
# Fixtures (real PostgreSQL, isolated schema — same pattern as
# test_reading_record_list_route.py)
# ---------------------------------------------------------------------------


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


async def _connect_admin(schema_name: str | None = None) -> asyncpg.Connection:
    conn = await asyncpg.connect(DATABASE_URL)
    if schema_name is not None:
        await conn.execute(f'SET search_path TO "{schema_name}", public')
    return conn


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


@pytest.fixture
async def reader_api_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    schema_name = f"test_reader_recent_{uuid4().hex}"
    admin_conn = await _connect_admin()
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)
        pool = await _make_pool(schema_name)
        monkeypatch.setattr(db_connection, "DB_POOL", pool)

        app = FastAPI()
        app.include_router(api_router)

        try:
            yield {"pool": pool, "app": app, "schema_name": schema_name}
        finally:
            await pool.close()
    finally:
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


async def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


async def _insert_record(
    pool: asyncpg.Pool,
    user_id: UUID,
    *,
    title: str | None = None,
) -> str:
    async with pool.acquire() as conn:
        record_id = await conn.fetchval(
            """
            INSERT INTO reading_records (user_id, source_type, title)
            VALUES ($1, 'text', $2)
            RETURNING id
            """,
            user_id,
            title,
        )
    assert isinstance(record_id, UUID)
    return str(record_id)


async def _fetch_record_row(
    pool: asyncpg.Pool,
    record_id: str,
) -> asyncpg.Record:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT last_opened_at, recent_hidden_at, updated_at, deleted_at,
                   lifecycle_status
            FROM reading_records WHERE id = $1
            """,
            UUID(record_id),
        )
    assert row is not None
    return row


async def _set_last_opened_at(
    pool: asyncpg.Pool,
    *,
    record_id: str,
    last_opened_at: datetime | None,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE reading_records SET last_opened_at = $2 WHERE id = $1",
            UUID(record_id),
            last_opened_at,
        )


async def _set_product_state(
    pool: asyncpg.Pool,
    *,
    record_id: str,
    product_state: str,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE reading_records SET product_state = $2 WHERE id = $1",
            UUID(record_id),
            product_state,
        )


async def _soft_delete_record(pool: asyncpg.Pool, *, record_id: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE reading_records
            SET deleted_at = NOW(),
                lifecycle_status = 'deleted',
                product_state = 'deleted'
            WHERE id = $1
            """,
            UUID(record_id),
        )


async def _count_events(pool: asyncpg.Pool, *, record_id: str) -> tuple[int, int]:
    async with pool.acquire() as conn:
        reader_events = await conn.fetchval(
            "SELECT COUNT(*) FROM reader_events WHERE reading_record_id = $1",
            UUID(record_id),
        )
        job_events = await conn.fetchval(
            "SELECT COUNT(*) FROM reader_job_events WHERE reading_record_id = $1",
            UUID(record_id),
        )
    return int(reader_events), int(job_events)


# ---------------------------------------------------------------------------
# Schema contract
# ---------------------------------------------------------------------------


def test_baseline_sql_declares_recent_visibility_schema() -> None:
    assert "recent_hidden_at timestamp with time zone" in BASELINE_SQL
    assert (
        "CREATE INDEX idx_reading_records_user_recent_visible "
        "ON reading_records USING btree (user_id, last_opened_at DESC, "
        "created_at DESC, id DESC) "
        "WHERE (deleted_at IS NULL AND recent_hidden_at IS NULL "
        "AND last_opened_at IS NOT NULL)"
    ) in BASELINE_SQL


async def test_live_schema_has_recent_hidden_at_and_partial_index(
    reader_api_env: dict[str, object],
) -> None:
    pool: asyncpg.Pool = reader_api_env["pool"]  # type: ignore[assignment]
    schema_name: str = reader_api_env["schema_name"]  # type: ignore[assignment]
    async with pool.acquire() as conn:
        column_type = await conn.fetchval(
            """
            SELECT data_type FROM information_schema.columns
            WHERE table_schema = $1
              AND table_name = 'reading_records'
              AND column_name = 'recent_hidden_at'
            """,
            schema_name,
        )
        index_def = await conn.fetchval(
            """
            SELECT indexdef FROM pg_indexes
            WHERE schemaname = $1
              AND indexname = 'idx_reading_records_user_recent_visible'
            """,
            schema_name,
        )
    assert column_type == "timestamp with time zone"
    assert index_def is not None
    assert "recent_hidden_at IS NULL" in index_def
    assert "last_opened_at IS NOT NULL" in index_def
    assert "deleted_at IS NULL" in index_def


# ---------------------------------------------------------------------------
# List semantics
# ---------------------------------------------------------------------------


async def test_recent_only_excludes_hidden_and_never_opened(
    reader_api_env: dict[str, object],
) -> None:
    pool: asyncpg.Pool = reader_api_env["pool"]  # type: ignore[assignment]
    app: FastAPI = reader_api_env["app"]  # type: ignore[assignment]
    user_id = await _insert_user(pool)
    opened_at = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)

    async with await _client(app) as client:
        with _mock_auth(user_id):
            record_a = await _insert_record(pool, user_id)
            record_b = await _insert_record(pool, user_id)
            record_c = await _insert_record(pool, user_id)  # never opened

            await _set_last_opened_at(
                pool, record_id=record_a, last_opened_at=opened_at
            )
            await _set_last_opened_at(
                pool,
                record_id=record_b,
                last_opened_at=opened_at + timedelta(minutes=5),
            )

            # Hide record B from recent.
            hide = await client.delete(
                f"/reader/records/{record_b}/recent",
                headers=AUTH_HEADERS,
            )
            assert hide.status_code == 200, hide.text

            full = await client.get("/reader/records", headers=AUTH_HEADERS)
            assert full.status_code == 200
            full_ids = {item["record_id"] for item in full.json()["items"]}
            assert full_ids == {record_a, record_b, record_c}
            assert full.json()["total"] == 3

            recent = await client.get(
                "/reader/records",
                params={"recent_only": "true"},
                headers=AUTH_HEADERS,
            )
            assert recent.status_code == 200
            recent_ids = {item["record_id"] for item in recent.json()["items"]}
            # Hidden record and never-opened record are both excluded.
            assert recent_ids == {record_a}
            assert recent.json()["total"] == 1


async def test_recent_only_items_and_total_use_same_filter(
    reader_api_env: dict[str, object],
) -> None:
    pool: asyncpg.Pool = reader_api_env["pool"]  # type: ignore[assignment]
    app: FastAPI = reader_api_env["app"]  # type: ignore[assignment]
    user_id = await _insert_user(pool)
    opened_at = datetime(2026, 8, 1, 8, 0, tzinfo=UTC)

    async with await _client(app) as client:
        with _mock_auth(user_id):
            ids = [await _insert_record(pool, user_id) for _ in range(3)]
            for idx, record_id in enumerate(ids):
                await _set_last_opened_at(
                    pool,
                    record_id=record_id,
                    last_opened_at=opened_at + timedelta(hours=idx),
                )
            await client.delete(
                f"/reader/records/{ids[1]}/recent",
                headers=AUTH_HEADERS,
            )

            response = await client.get(
                "/reader/records",
                params={"recent_only": "true", "limit": 1},
                headers=AUTH_HEADERS,
            )
            assert response.status_code == 200
            body = response.json()
            # limit slices items; total counts ALL matching rows.
            assert len(body["items"]) == 1
            assert body["total"] == 2


async def test_recent_only_combines_with_query_and_product_state(
    reader_api_env: dict[str, object],
) -> None:
    pool: asyncpg.Pool = reader_api_env["pool"]  # type: ignore[assignment]
    app: FastAPI = reader_api_env["app"]  # type: ignore[assignment]
    user_id = await _insert_user(pool)
    opened_at = datetime(2026, 8, 2, 9, 0, tzinfo=UTC)

    async with await _client(app) as client:
        with _mock_auth(user_id):
            record_alpha = await _insert_record(pool, user_id, title="Alpha Report")
            record_beta = await _insert_record(pool, user_id, title="Beta Report")
            record_hidden = await _insert_record(pool, user_id, title="Alpha Hidden")
            for record_id in (record_alpha, record_beta, record_hidden):
                await _set_last_opened_at(
                    pool, record_id=record_id, last_opened_at=opened_at
                )
            for record_id in (record_alpha, record_beta, record_hidden):
                await _set_product_state(
                    pool, record_id=record_id, product_state="failed"
                )
            await client.delete(
                f"/reader/records/{record_hidden}/recent",
                headers=AUTH_HEADERS,
            )

            recent = await client.get(
                "/reader/records",
                params={"recent_only": "true", "query": "Alpha"},
                headers=AUTH_HEADERS,
            )
            assert recent.status_code == 200
            # Hidden Alpha record is excluded from recent search results;
            # the visible Alpha record still matches.
            assert recent.json()["total"] == 1
            assert [item["record_id"] for item in recent.json()["items"]] == [
                record_alpha
            ]

            full = await client.get(
                "/reader/records",
                params={"query": "Alpha"},
                headers=AUTH_HEADERS,
            )
            assert full.status_code == 200
            full_ids = {item["record_id"] for item in full.json()["items"]}
            assert full_ids == {record_alpha, record_hidden}

            recent_failed = await client.get(
                "/reader/records",
                params={"recent_only": "true", "product_state": "failed"},
                headers=AUTH_HEADERS,
            )
            assert recent_failed.status_code == 200
            # Alpha + Beta are visible failed records; hidden Alpha-Hidden
            # is excluded.
            assert recent_failed.json()["total"] == 2


# ---------------------------------------------------------------------------
# DELETE /records/{record_id}/recent
# ---------------------------------------------------------------------------


async def test_hide_from_recent_is_idempotent_and_preserves_state(
    reader_api_env: dict[str, object],
) -> None:
    pool: asyncpg.Pool = reader_api_env["pool"]  # type: ignore[assignment]
    app: FastAPI = reader_api_env["app"]  # type: ignore[assignment]
    user_id = await _insert_user(pool)
    opened_at = datetime(2026, 8, 3, 10, 0, tzinfo=UTC)

    async with await _client(app) as client:
        with _mock_auth(user_id):
            record_id = await _insert_record(pool, user_id)
            await _set_last_opened_at(
                pool, record_id=record_id, last_opened_at=opened_at
            )
            events_before = await _count_events(pool, record_id=record_id)
            row_before = await _fetch_record_row(pool, record_id)

            first = await client.delete(
                f"/reader/records/{record_id}/recent",
                headers=AUTH_HEADERS,
            )
            assert first.status_code == 200, first.text
            first_body = first.json()
            assert first_body["record_id"] == record_id
            assert first_body["status"] == "removed_from_recent"
            assert first_body["recent_hidden_at"] is not None

            row_after = await _fetch_record_row(pool, record_id)
            assert row_after["last_opened_at"] == opened_at
            assert row_after["updated_at"] == row_before["updated_at"]
            assert row_after["deleted_at"] is None

            second = await client.delete(
                f"/reader/records/{record_id}/recent",
                headers=AUTH_HEADERS,
            )
            assert second.status_code == 200, second.text
            second_body = second.json()
            assert second_body["status"] == "already_removed"
            # First timestamp is preserved.
            assert second_body["recent_hidden_at"] == first_body["recent_hidden_at"]

            row_final = await _fetch_record_row(pool, record_id)
            assert row_final["recent_hidden_at"] == row_after["recent_hidden_at"]
            assert row_final["updated_at"] == row_before["updated_at"]
            assert row_final["last_opened_at"] == opened_at

            events_after = await _count_events(pool, record_id=record_id)
            assert events_after == events_before


async def test_opened_restores_recent_visibility(
    reader_api_env: dict[str, object],
) -> None:
    pool: asyncpg.Pool = reader_api_env["pool"]  # type: ignore[assignment]
    app: FastAPI = reader_api_env["app"]  # type: ignore[assignment]
    user_id = await _insert_user(pool)
    opened_at = datetime(2026, 8, 3, 11, 0, tzinfo=UTC)

    async with await _client(app) as client:
        with _mock_auth(user_id):
            record_id = await _insert_record(pool, user_id)
            await _set_last_opened_at(
                pool, record_id=record_id, last_opened_at=opened_at
            )

            hide = await client.delete(
                f"/reader/records/{record_id}/recent",
                headers=AUTH_HEADERS,
            )
            assert hide.status_code == 200

            recent_before = await client.get(
                "/reader/records",
                params={"recent_only": "true"},
                headers=AUTH_HEADERS,
            )
            assert record_id not in {
                item["record_id"] for item in recent_before.json()["items"]
            }

            reopened = await client.post(
                f"/reader/records/{record_id}/opened",
                headers=AUTH_HEADERS,
            )
            assert reopened.status_code == 200, reopened.text

            row = await _fetch_record_row(pool, record_id)
            assert row["recent_hidden_at"] is None
            assert row["last_opened_at"] is not None
            assert row["last_opened_at"] > opened_at

            recent_after = await client.get(
                "/reader/records",
                params={"recent_only": "true"},
                headers=AUTH_HEADERS,
            )
            assert record_id in {
                item["record_id"] for item in recent_after.json()["items"]
            }


async def test_recent_hide_ownership_and_404s(
    reader_api_env: dict[str, object],
) -> None:
    pool: asyncpg.Pool = reader_api_env["pool"]  # type: ignore[assignment]
    app: FastAPI = reader_api_env["app"]  # type: ignore[assignment]
    owner_id = await _insert_user(pool)
    other_user_id = await _insert_user(pool)
    opened_at = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)

    async with await _client(app) as client:
        with _mock_auth(owner_id):
            record_id = await _insert_record(pool, owner_id)
            await _set_last_opened_at(
                pool, record_id=record_id, last_opened_at=opened_at
            )
            missing_record_id = uuid4()

            missing = await client.delete(
                f"/reader/records/{missing_record_id}/recent",
                headers=AUTH_HEADERS,
            )
            assert missing.status_code == 404

        with _mock_auth(other_user_id):
            non_owner = await client.delete(
                f"/reader/records/{record_id}/recent",
                headers=AUTH_HEADERS,
            )
            assert non_owner.status_code == 404
            # Non-owner lists never see the record.
            listing = await client.get(
                "/reader/records",
                params={"recent_only": "true"},
                headers=AUTH_HEADERS,
            )
            assert record_id not in {
                item["record_id"] for item in listing.json()["items"]
            }

        with _mock_auth(owner_id):
            row_before = await _fetch_record_row(pool, record_id)
            await _soft_delete_record(pool, record_id=record_id)
            deleted = await client.delete(
                f"/reader/records/{record_id}/recent",
                headers=AUTH_HEADERS,
            )
            assert deleted.status_code == 404
            # Soft-deleted record never appears in either list.
            full = await client.get("/reader/records", headers=AUTH_HEADERS)
            assert record_id not in {
                item["record_id"] for item in full.json()["items"]
            }
            assert full.json()["total"] == 0
            recent = await client.get(
                "/reader/records",
                params={"recent_only": "true"},
                headers=AUTH_HEADERS,
            )
            assert record_id not in {
                item["record_id"] for item in recent.json()["items"]
            }

            # Hide must not have written anything for the 404 paths.
            row_after = await _fetch_record_row(pool, record_id)
            assert row_after["recent_hidden_at"] == row_before["recent_hidden_at"]
