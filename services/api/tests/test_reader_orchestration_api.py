from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.router import api_router
from app.database import connection as db_connection
from app.database.connection import init_connection
from app.services.reader_orchestration.event_runtime import ReaderEventRuntime
from tests.test_reader_orchestration_schema_baseline import BASELINE_SQL, DATABASE_URL

pytestmark = pytest.mark.anyio

API_ROOT = Path(__file__).resolve().parents[1]
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
    schema_name = f"test_reader_api_{uuid4().hex}"
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
            yield {"pool": pool, "app": app}
        finally:
            await pool.close()
    finally:
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


async def _create_client(app: FastAPI) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    )


async def test_submit_plain_text_returns_article_ready_snapshot_and_snapshot_reload(
    reader_api_env: dict[str, object],
) -> None:
    pool = reader_api_env["pool"]
    app = reader_api_env["app"]
    assert isinstance(pool, asyncpg.Pool)
    assert isinstance(app, FastAPI)
    user_id = await _insert_user(pool)

    with _mock_auth(user_id):
        async with await _create_client(app) as client:
            submit_response = await client.post(
                "/reader/records/plain-text",
                headers=AUTH_HEADERS,
                json={
                    "plain_text": "First sentence.\n\nSecond paragraph.",
                    "title": "API Submit",
                    "language": "en",
                    "source_metadata": {"source_kind": "api_test"},
                    "client_record_id": "reader-api-1",
                },
            )

            assert submit_response.status_code == 200
            submitted = submit_response.json()
            assert submitted["article_ready_sequence"] == 1
            assert submitted["snapshot"]["schema_kind"] == "reader_plate_snapshot"
            assert submitted["snapshot"]["record_id"] == submitted["record_id"]
            assert submitted["snapshot"]["base"]["base_id"] == submitted["base_id"]

            snapshot_response = await client.get(
                f"/reader/records/{submitted['record_id']}/snapshot",
                headers=AUTH_HEADERS,
            )
            assert snapshot_response.status_code == 200
            snapshot = snapshot_response.json()
            assert snapshot["schema_kind"] == "reader_plate_snapshot"
            assert snapshot["record_id"] == submitted["record_id"]
            assert snapshot["base"]["base_id"] == submitted["base_id"]
            assert snapshot["last_event_sequence"] == 1


async def test_polling_returns_article_ready_event_and_empty_page_after_cursor(
    reader_api_env: dict[str, object],
) -> None:
    pool = reader_api_env["pool"]
    app = reader_api_env["app"]
    assert isinstance(pool, asyncpg.Pool)
    assert isinstance(app, FastAPI)
    user_id = await _insert_user(pool)

    with _mock_auth(user_id):
        async with await _create_client(app) as client:
            submit_response = await client.post(
                "/reader/records/plain-text",
                headers=AUTH_HEADERS,
                json={"plain_text": "Polling event body.", "title": "Polling"},
            )
            record_id = submit_response.json()["record_id"]

            first_page = await client.get(
                f"/reader/records/{record_id}/events?after_sequence=0&limit=10",
                headers=AUTH_HEADERS,
            )
            assert first_page.status_code == 200
            payload = first_page.json()
            assert payload["reload_required"] is False
            assert payload["next_after_sequence"] == 1
            assert payload["last_event_sequence"] == 1
            assert len(payload["events"]) == 1
            assert payload["events"][0]["event_type"] == "article_ready"
            assert payload["events"][0]["payload"]["record_id"] == record_id

            empty_page = await client.get(
                f"/reader/records/{record_id}/events?after_sequence=1&limit=10",
                headers=AUTH_HEADERS,
            )
            assert empty_page.status_code == 200
            empty_payload = empty_page.json()
            assert empty_payload["events"] == []
            assert empty_payload["next_after_sequence"] == 1
            assert empty_payload["reload_required"] is False


async def test_polling_limit_truncation_does_not_skip_cursor(
    reader_api_env: dict[str, object],
) -> None:
    pool = reader_api_env["pool"]
    app = reader_api_env["app"]
    assert isinstance(pool, asyncpg.Pool)
    assert isinstance(app, FastAPI)
    user_id = await _insert_user(pool)

    with _mock_auth(user_id):
        async with await _create_client(app) as client:
            submit_response = await client.post(
                "/reader/records/plain-text",
                headers=AUTH_HEADERS,
                json={"plain_text": "Cursor truncation body.", "title": "Cursor"},
            )
            record_id = UUID(submit_response.json()["record_id"])

            runtime = ReaderEventRuntime(pool=pool)
            await runtime.publish_event(
                record_id=record_id,
                event_type="record_state_changed",
                payload_json={"step": 2},
            )
            await runtime.publish_event(
                record_id=record_id,
                event_type="projection_reset_required",
                payload_json={"reason": "manual-refresh"},
            )

            page = await client.get(
                f"/reader/records/{record_id}/events?after_sequence=0&limit=2",
                headers=AUTH_HEADERS,
            )
            assert page.status_code == 200
            data = page.json()
            assert [event["sequence"] for event in data["events"]] == [1, 2]
            assert data["next_after_sequence"] == 2
            assert data["last_event_sequence"] == 3
            assert data["has_more"] is True
            assert data["truncated"] is True
            assert data["reload_required"] is False

            next_page = await client.get(
                f"/reader/records/{record_id}/events?after_sequence=2&limit=2",
                headers=AUTH_HEADERS,
            )
            assert next_page.status_code == 200
            next_data = next_page.json()
            assert [event["sequence"] for event in next_data["events"]] == [3]
            assert next_data["next_after_sequence"] == 3
            assert next_data["has_more"] is False


async def test_other_user_cannot_read_snapshot_or_events(
    reader_api_env: dict[str, object],
) -> None:
    pool = reader_api_env["pool"]
    app = reader_api_env["app"]
    assert isinstance(pool, asyncpg.Pool)
    assert isinstance(app, FastAPI)
    owner_id = await _insert_user(pool)
    other_user_id = await _insert_user(pool)

    with _mock_auth(owner_id):
        async with await _create_client(app) as client:
            submit_response = await client.post(
                "/reader/records/plain-text",
                headers=AUTH_HEADERS,
                json={"plain_text": "Private reader record.", "title": "Private"},
            )
            record_id = submit_response.json()["record_id"]

    with _mock_auth(other_user_id):
        async with await _create_client(app) as client:
            snapshot_response = await client.get(
                f"/reader/records/{record_id}/snapshot",
                headers=AUTH_HEADERS,
            )
            assert snapshot_response.status_code == 404

            events_response = await client.get(
                f"/reader/records/{record_id}/events?after_sequence=0&limit=10",
                headers=AUTH_HEADERS,
            )
            assert events_response.status_code == 404


async def test_empty_plain_text_submit_returns_validation_error(
    reader_api_env: dict[str, object],
) -> None:
    pool = reader_api_env["pool"]
    app = reader_api_env["app"]
    assert isinstance(pool, asyncpg.Pool)
    assert isinstance(app, FastAPI)
    user_id = await _insert_user(pool)

    with _mock_auth(user_id):
        async with await _create_client(app) as client:
            response = await client.post(
                "/reader/records/plain-text",
                headers=AUTH_HEADERS,
                json={"plain_text": "   \n\t  "},
            )

    assert response.status_code == 422


async def test_blank_client_record_id_is_normalized_to_null_and_does_not_conflict(
    reader_api_env: dict[str, object],
) -> None:
    pool = reader_api_env["pool"]
    app = reader_api_env["app"]
    assert isinstance(pool, asyncpg.Pool)
    assert isinstance(app, FastAPI)
    user_id = await _insert_user(pool)

    with _mock_auth(user_id):
        async with await _create_client(app) as client:
            first = await client.post(
                "/reader/records/plain-text",
                headers=AUTH_HEADERS,
                json={
                    "plain_text": "Blank client record id first submit.",
                    "client_record_id": "   ",
                },
            )
            second = await client.post(
                "/reader/records/plain-text",
                headers=AUTH_HEADERS,
                json={
                    "plain_text": "Blank client record id second submit.",
                    "client_record_id": "",
                },
            )

    assert first.status_code == 200
    assert second.status_code == 200

    first_record_id = UUID(first.json()["record_id"])
    second_record_id = UUID(second.json()["record_id"])
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, client_record_id
            FROM reading_records
            WHERE id = ANY($1::uuid[])
            ORDER BY created_at ASC
            """,
            [first_record_id, second_record_id],
        )
    assert len(rows) == 2
    assert all(row["client_record_id"] is None for row in rows)


async def test_duplicate_client_record_id_returns_conflict(
    reader_api_env: dict[str, object],
) -> None:
    pool = reader_api_env["pool"]
    app = reader_api_env["app"]
    assert isinstance(pool, asyncpg.Pool)
    assert isinstance(app, FastAPI)
    user_id = await _insert_user(pool)

    with _mock_auth(user_id):
        async with await _create_client(app) as client:
            first = await client.post(
                "/reader/records/plain-text",
                headers=AUTH_HEADERS,
                json={
                    "plain_text": "First idempotency-like submit.",
                    "client_record_id": "dup-client-record-id",
                },
            )
            second = await client.post(
                "/reader/records/plain-text",
                headers=AUTH_HEADERS,
                json={
                    "plain_text": "Second conflicting submit.",
                    "client_record_id": "dup-client-record-id",
                },
            )

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["detail"] == "client_record_id already exists for this user"


def test_reader_orchestration_api_route_does_not_reference_render_scene_json() -> None:
    path = API_ROOT / "app" / "api" / "routes" / "reader_orchestration.py"
    assert "render_scene_json" not in path.read_text(encoding="utf-8")
