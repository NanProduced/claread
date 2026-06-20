from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import anyio
import asyncpg
import pytest

from app.database.connection import init_connection
from app.services.reader_orchestration.event_runtime import (
    ReaderEventRuntime,
    parse_last_event_id,
)
from tests.test_reader_orchestration_schema_baseline import BASELINE_SQL, DATABASE_URL

pytestmark = pytest.mark.anyio

API_ROOT = Path(__file__).resolve().parents[1]


async def _make_pool(schema_name: str) -> asyncpg.Pool:
    async def _init_conn(conn: asyncpg.Connection) -> None:
        await init_connection(conn)

    async def _setup_conn(conn: asyncpg.Connection) -> None:
        await conn.execute(f'SET search_path TO "{schema_name}", public')

    return await asyncpg.create_pool(
        DATABASE_URL,
        min_size=1,
        max_size=6,
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


async def _insert_record(
    pool: asyncpg.Pool,
    user_id: UUID,
    *,
    title: str = "Reader Event Runtime Test",
) -> UUID:
    async with pool.acquire() as conn:
        record_id = await conn.fetchval(
            """
            INSERT INTO reading_records (user_id, source_type, title, language, generation)
            VALUES ($1, 'text', $2, 'en', 1)
            RETURNING id
            """,
            user_id,
            title,
        )
    assert isinstance(record_id, UUID)
    return record_id


@pytest.fixture
async def reader_event_runtime_env() -> asyncpg.Pool:
    schema_name = f"test_reader_event_runtime_{uuid4().hex}"
    admin_conn = await _connect_admin()
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)
        pool = await _make_pool(schema_name)
        try:
            yield pool
        finally:
            await pool.close()
    finally:
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


async def test_publish_event_starts_sequence_at_one_and_keeps_payload_object(
    reader_event_runtime_env: asyncpg.Pool,
) -> None:
    runtime = ReaderEventRuntime(pool=reader_event_runtime_env)
    user_id = await _insert_user(reader_event_runtime_env)
    record_id = await _insert_record(reader_event_runtime_env, user_id)

    event = await runtime.publish_event(
        record_id=record_id,
        event_type="article_ready",
        payload_json={"base_id": "base-1", "generation": 1},
    )

    assert event.sequence == 1
    assert event.payload_json == {"base_id": "base-1", "generation": 1}

    async with reader_event_runtime_env.acquire() as conn:
        assert await conn.fetchval(
            """
            SELECT next_sequence
            FROM reader_event_sequences
            WHERE reading_record_id = $1
            """,
            record_id,
        ) == 2
        row = await conn.fetchrow(
            """
            SELECT sequence, payload_json
            FROM reader_events
            WHERE reading_record_id = $1
            """,
            record_id,
        )
        assert row is not None
        assert row["sequence"] == 1
        assert row["payload_json"] == {"base_id": "base-1", "generation": 1}


async def test_publish_event_rollback_has_no_gap(
    reader_event_runtime_env: asyncpg.Pool,
) -> None:
    runtime = ReaderEventRuntime(pool=reader_event_runtime_env)
    user_id = await _insert_user(reader_event_runtime_env)
    record_id = await _insert_record(reader_event_runtime_env, user_id)

    async with reader_event_runtime_env.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        published = await runtime.publish_event_in_transaction(
            conn,
            record_id=record_id,
            event_type="article_ready",
            payload_json={"step": "rolled-back"},
        )
        assert published.sequence == 1
        await tx.rollback()

        assert await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM reader_events
            WHERE reading_record_id = $1
            """,
            record_id,
        ) == 0

    committed = await runtime.publish_event(
        record_id=record_id,
        event_type="article_ready",
        payload_json={"step": "committed"},
    )
    assert committed.sequence == 1


async def test_concurrent_publish_sequences_are_contiguous(
    reader_event_runtime_env: asyncpg.Pool,
) -> None:
    runtime = ReaderEventRuntime(pool=reader_event_runtime_env)
    user_id = await _insert_user(reader_event_runtime_env)
    record_id = await _insert_record(reader_event_runtime_env, user_id)

    worker_count = 6
    results: list[int | None] = [None] * worker_count
    start_event = anyio.Event()

    async def _publish(index: int) -> None:
        await start_event.wait()
        event = await runtime.publish_event(
            record_id=record_id,
            event_type="record_state_changed",
            payload_json={"worker": index},
        )
        results[index] = event.sequence

    async with anyio.create_task_group() as task_group:
        for index in range(worker_count):
            task_group.start_soon(_publish, index)
        start_event.set()

    sequences = sorted(sequence for sequence in results if sequence is not None)
    assert sequences == [1, 2, 3, 4, 5, 6]

    async with reader_event_runtime_env.acquire() as conn:
        db_sequences = await conn.fetch(
            """
            SELECT sequence
            FROM reader_events
            WHERE reading_record_id = $1
            ORDER BY sequence ASC
            """,
            record_id,
        )
        assert [int(row["sequence"]) for row in db_sequences] == [1, 2, 3, 4, 5, 6]


async def test_polling_limit_truncation_does_not_advance_to_last_event_sequence(
    reader_event_runtime_env: asyncpg.Pool,
) -> None:
    runtime = ReaderEventRuntime(pool=reader_event_runtime_env)
    user_id = await _insert_user(reader_event_runtime_env)
    record_id = await _insert_record(reader_event_runtime_env, user_id)

    for step in range(3):
        await runtime.publish_event(
            record_id=record_id,
            event_type="record_state_changed",
            payload_json={"step": step + 1},
        )

    page = await runtime.poll_events(
        record_id=record_id,
        user_id=user_id,
        after_sequence=0,
        limit=2,
    )

    assert [event.sequence for event in page.events] == [1, 2]
    assert page.next_after_sequence == 2
    assert page.last_event_sequence == 3
    assert page.has_more is True
    assert page.truncated is True
    assert page.reload_required is False

    next_page = await runtime.poll_events(
        record_id=record_id,
        user_id=user_id,
        after_sequence=page.next_after_sequence,
        limit=2,
    )
    assert [event.sequence for event in next_page.events] == [3]
    assert next_page.next_after_sequence == 3
    assert next_page.has_more is False


async def test_polling_after_sequence_ahead_of_server_keeps_client_cursor(
    reader_event_runtime_env: asyncpg.Pool,
) -> None:
    runtime = ReaderEventRuntime(pool=reader_event_runtime_env)
    user_id = await _insert_user(reader_event_runtime_env)
    record_id = await _insert_record(reader_event_runtime_env, user_id)

    await runtime.publish_event(
        record_id=record_id,
        event_type="article_ready",
        payload_json={"status": "ready"},
    )

    page = await runtime.poll_events(
        record_id=record_id,
        user_id=user_id,
        after_sequence=5,
        limit=10,
    )

    assert page.events == ()
    assert page.next_after_sequence == 5
    assert page.last_event_sequence == 1
    assert page.has_more is False
    assert page.truncated is False
    assert page.reload_required is False


async def test_polling_after_sequence_equal_to_last_returns_empty_without_reload(
    reader_event_runtime_env: asyncpg.Pool,
) -> None:
    runtime = ReaderEventRuntime(pool=reader_event_runtime_env)
    user_id = await _insert_user(reader_event_runtime_env)
    record_id = await _insert_record(reader_event_runtime_env, user_id)

    await runtime.publish_event(
        record_id=record_id,
        event_type="article_ready",
        payload_json={"status": "ready"},
    )

    page = await runtime.poll_events(
        record_id=record_id,
        user_id=user_id,
        after_sequence=1,
        limit=10,
    )

    assert page.events == ()
    assert page.next_after_sequence == 1
    assert page.last_event_sequence == 1
    assert page.has_more is False
    assert page.truncated is False
    assert page.reload_required is False


async def test_polling_empty_stream_returns_empty_without_reload(
    reader_event_runtime_env: asyncpg.Pool,
) -> None:
    runtime = ReaderEventRuntime(pool=reader_event_runtime_env)
    user_id = await _insert_user(reader_event_runtime_env)
    record_id = await _insert_record(reader_event_runtime_env, user_id)

    page = await runtime.poll_events(
        record_id=record_id,
        user_id=user_id,
        after_sequence=0,
        limit=10,
    )

    assert page.events == ()
    assert page.next_after_sequence == 0
    assert page.last_event_sequence == 0
    assert page.has_more is False
    assert page.truncated is False
    assert page.reload_required is False


async def test_polling_gap_returns_reload_required(
    reader_event_runtime_env: asyncpg.Pool,
) -> None:
    runtime = ReaderEventRuntime(pool=reader_event_runtime_env)
    user_id = await _insert_user(reader_event_runtime_env)
    record_id = await _insert_record(reader_event_runtime_env, user_id)

    await runtime.publish_event(
        record_id=record_id,
        event_type="article_ready",
        payload_json={"sequence": 1},
    )

    async with reader_event_runtime_env.acquire() as conn:
        await conn.execute(
            """
            UPDATE reader_event_sequences
            SET next_sequence = 4,
                updated_at = NOW()
            WHERE reading_record_id = $1
            """,
            record_id,
        )
        await conn.execute(
            """
            INSERT INTO reader_events (reading_record_id, sequence, event_type, payload_json)
            VALUES ($1, 3, 'projection_reset_required', $2::jsonb)
            """,
            record_id,
            {"reason": "manual-gap"},
        )

    page = await runtime.poll_events(
        record_id=record_id,
        user_id=user_id,
        after_sequence=1,
        limit=10,
    )

    assert page.events == ()
    assert page.next_after_sequence == 1
    assert page.last_event_sequence == 3
    assert page.reload_required is True
    assert page.reload_reason is not None
    assert "gap" in page.reload_reason


async def test_polling_requires_record_owner(
    reader_event_runtime_env: asyncpg.Pool,
) -> None:
    runtime = ReaderEventRuntime(pool=reader_event_runtime_env)
    owner_id = await _insert_user(reader_event_runtime_env)
    other_user_id = await _insert_user(reader_event_runtime_env)
    record_id = await _insert_record(reader_event_runtime_env, owner_id)

    await runtime.publish_event(
        record_id=record_id,
        event_type="article_ready",
        payload_json={"status": "ready"},
    )

    with pytest.raises(LookupError, match="not found for user"):
        await runtime.poll_events(
            record_id=record_id,
            user_id=other_user_id,
            after_sequence=0,
            limit=10,
        )


async def test_publish_event_requires_json_object_payload(
    reader_event_runtime_env: asyncpg.Pool,
) -> None:
    runtime = ReaderEventRuntime(pool=reader_event_runtime_env)
    user_id = await _insert_user(reader_event_runtime_env)
    record_id = await _insert_record(reader_event_runtime_env, user_id)

    with pytest.raises(TypeError, match="JSON object mapping"):
        await runtime.publish_event(
            record_id=record_id,
            event_type="article_ready",
            payload_json=["not", "an", "object"],  # type: ignore[arg-type]
        )


def test_parse_last_event_id_prefers_non_negative_numeric_values() -> None:
    assert parse_last_event_id("17") == 17
    assert parse_last_event_id(" 0 ") == 0
    assert parse_last_event_id("abc") is None
    assert parse_last_event_id("-1") is None
    assert parse_last_event_id(None) is None


def test_event_runtime_module_does_not_reference_render_scene_json() -> None:
    path = API_ROOT / "app" / "services" / "reader_orchestration" / "event_runtime.py"
    assert "render_scene_json" not in path.read_text(encoding="utf-8")
