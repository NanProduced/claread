"""Real PostgreSQL gate for migration 0028 and thread-memory CAS.

Opt in with ``CLAREAD_RUN_THREAD_MEMORY_DB_TESTS=1`` after applying 0028.
The test creates a non-default disposable Ask thread under an existing local
reading record and deletes it in ``finally``; business rows outside that
thread are never modified.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import pytest

from app.config.settings import get_settings
from app.database.connection import init_connection
from app.services.reader_record_ask.thread_memory.allowlist import (
    compute_watermark,
)
from app.services.reader_record_ask.thread_memory.repository import (
    ThreadMemoryRepository,
)
from app.services.reader_record_ask.thread_memory.schema import (
    ThreadMemorySnapshot,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("CLAREAD_RUN_THREAD_MEMORY_DB_TESTS") != "1",
    reason="opt-in: apply migration 0028 and set CLAREAD_RUN_THREAD_MEMORY_DB_TESTS=1",
)


@pytest.mark.asyncio
async def test_real_postgres_canonical_view_and_snapshot_cas() -> None:
    pool = await asyncpg.create_pool(
        get_settings().database_url,
        min_size=1,
        max_size=2,
        init=init_connection,
    )
    thread_id = uuid4()
    user_message_id = uuid4()
    assistant_message_id = uuid4()
    run_id = uuid4()
    now = datetime.now(UTC)
    try:
        async with pool.acquire() as conn:
            fixture = await conn.fetchrow(
                """
                SELECT id AS reading_record_id, user_id
                FROM reading_records
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """
            )
            assert fixture is not None, "local DB needs one reading_record fixture"
            await conn.execute(
                """
                INSERT INTO reader_ask_threads (
                    id, user_id, reading_record_id, title, is_default,
                    created_at, updated_at
                )
                VALUES ($1, $2, $3, 'thread-memory-db-gate', false, $4, $4)
                """,
                thread_id,
                fixture["user_id"],
                fixture["reading_record_id"],
                now,
            )
            await conn.execute(
                """
                INSERT INTO reader_ask_messages (
                    id, thread_id, role, status, content_md, created_at, updated_at
                )
                VALUES
                    ($1, $3, 'user', 'completed', 'db gate question', $4, $4),
                    ($2, $3, 'assistant', 'completed', 'db gate answer', $5, $5)
                """,
                user_message_id,
                assistant_message_id,
                thread_id,
                now,
                now + timedelta(microseconds=1),
            )
            await conn.execute(
                """
                INSERT INTO reader_ask_turn_runs (
                    id, message_id, thread_id, user_id, reading_record_id,
                    base_id, generation, turn_id, run_attempt, status,
                    final_status, execution_version, envelope_fingerprint,
                    user_visible_output_json, resolved_evidence_json,
                    started_at, completed_at, created_at, updated_at
                )
                VALUES (
                    $1, $2, $3, $4, $5,
                    $6, 1, $7, 1, 'completed',
                    'ok', 'reader_record_ask_agentic_v2', $8,
                    $9::jsonb, '[]'::jsonb,
                    $10, $10, $10, $10
                )
                """,
                run_id,
                assistant_message_id,
                thread_id,
                fixture["user_id"],
                fixture["reading_record_id"],
                uuid4(),
                user_message_id,
                "f" * 64,
                json.dumps(
                    {
                        "answer_text": "db gate answer",
                        "answer_blocks": [
                            {"kind": "text", "text": "db gate answer"}
                        ],
                        "web_search": None,
                    }
                ),
                now,
            )
            await conn.execute(
                """
                UPDATE reader_ask_messages
                SET current_turn_run_id = $1
                WHERE id = $2
                """,
                run_id,
                assistant_message_id,
            )

        repository = ThreadMemoryRepository(pool=pool)
        view = await repository.load_canonical_memory_view(thread_id=thread_id)
        assert view is not None
        assert view.storage_available is True
        assert [row["id"] for row in view.canonical_messages] == [
            str(user_message_id),
            str(assistant_message_id),
        ]
        assert view.canonical_messages[1]["canonical_turn_run_id"] == str(run_id)
        assert [row["id"] for row in view.ok_turn_runs] == [str(run_id)]

        snapshot = ThreadMemorySnapshot(
            version="thread_memory_v1",
            watermark=compute_watermark(list(view.canonical_messages)),
            thread_id=str(thread_id),
            created_at=now.isoformat(),
            episodes=[],
        )
        first = await repository.upsert_thread_memory_snapshot(
            thread_id=thread_id,
            snapshot=snapshot,
            version=0,
        )
        assert first.applied is True
        assert first.version == 1

        stale = await repository.upsert_thread_memory_snapshot(
            thread_id=thread_id,
            snapshot=snapshot,
            version=0,
        )
        assert stale.applied is False
        assert stale.version == 1

        updated = snapshot.model_copy(update={"watermark": "next-watermark"})
        second = await repository.upsert_thread_memory_snapshot(
            thread_id=thread_id,
            snapshot=updated,
            version=1,
        )
        assert second.applied is True
        assert second.version == 2

        reloaded = await repository.load_canonical_memory_view(thread_id=thread_id)
        assert reloaded is not None
        assert reloaded.snapshot == updated
        assert reloaded.snapshot_version == 2
    finally:
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM reader_ask_threads WHERE id = $1",
                thread_id,
            )
        await pool.close()
