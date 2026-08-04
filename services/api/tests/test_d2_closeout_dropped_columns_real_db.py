"""DATA-D2-CLOSEOUT-R1 real-PostgreSQL contracts for the dropped columns.

Verified against the single fresh baseline (``infra/migrations/0001_initial.sql``)
in an isolated schema:

1. The 13 dropped identity columns are physically absent from the baseline
   tables (DB-level zero-residual check, complementing the static guard in
   ``test_data_legacy_identity_exit_guard.py``).
2. ``record_ai_usage_event`` persists with the current column set
   (``ai_usage_events.task_id`` / ``.record_id`` stay dropped).
3. ``get_credit_ledger`` lists entries projecting ``title_snapshot`` as
   ``article_title`` (``user_credit_ledger.task_id`` stays dropped).

Snapshot assets/supplements on the same baseline are covered by
``test_reader_orchestration_article_ready_service.py`` and
``test_b2_ask_supplements_snapshot.py``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.database import connection as db_connection
from app.database.connection import init_connection
from app.services.ai_usage.service import AIUsageEventCreate, record_ai_usage_event
from app.services.quota.ledger import get_credit_ledger
from tests.test_reader_orchestration_schema_baseline import BASELINE_SQL, DATABASE_URL

pytestmark = pytest.mark.anyio

# The 13 identity columns dropped by DATA-SCHEMA-BASELINE D2.
DROPPED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("user_annotations", "analysis_record_id"),
    ("reader_notes", "analysis_record_id"),
    ("reader_notes", "anchor_sentence_id"),
    ("favorite_records", "analysis_record_id"),
    ("feedback", "analysis_record_id"),
    ("feedback", "annotation_type"),
    ("dict_ai_candidate_entries", "record_id"),
    ("ai_usage_events", "record_id"),
    ("ai_usage_events", "task_id"),
    ("user_credit_ledger", "task_id"),
    ("reader_ask_threads", "analysis_record_id"),
    ("reader_ask_turn_runs", "analysis_record_id"),
    ("reader_ask_supplements", "analysis_record_id"),
)


@pytest.fixture
async def d2_schema_pool() -> AsyncIterator[asyncpg.Pool]:
    schema_name = f"test_d2_closeout_{uuid4().hex}"
    admin_conn = await asyncpg.connect(DATABASE_URL)
    pool: asyncpg.Pool | None = None
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)

        async def _setup_conn(conn: asyncpg.Connection) -> None:
            await init_connection(conn)
            await conn.execute(f'SET search_path TO "{schema_name}", public')

        pool = await asyncpg.create_pool(
            DATABASE_URL, min_size=1, max_size=4, setup=_setup_conn
        )
        yield pool
    finally:
        if pool is not None:
            await pool.close()
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


async def test_dropped_identity_columns_are_absent_from_baseline(
    d2_schema_pool: asyncpg.Pool,
) -> None:
    async with d2_schema_pool.acquire() as conn:
        survivors = await conn.fetch(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND (table_name, column_name) IN (
                  SELECT * FROM unnest($1::text[], $2::text[])
              )
            """,
            [table for table, _ in DROPPED_COLUMNS],
            [column for _, column in DROPPED_COLUMNS],
        )
    assert survivors == [], (
        "dropped identity columns reappeared in the baseline: "
        + ", ".join(f"{row['table_name']}.{row['column_name']}" for row in survivors)
    )


async def test_usage_persist_works_without_task_record_columns(
    d2_schema_pool: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(db_connection, "DB_POOL", d2_schema_pool)
    async with d2_schema_pool.acquire() as conn:
        user_id = await conn.fetchval("INSERT INTO users DEFAULT VALUES RETURNING id")

    event_id = await record_ai_usage_event(
        AIUsageEventCreate(
            usage_scope="user_billed",
            capability_code="dict_ai_lookup",
            billing_mode="user_points",
            status="succeeded",
            user_id=user_id,
            model_provider="offline-test",
            model_name="offline-test-model",
            usage_data={"input_tokens": 10, "output_tokens": 5},
        )
    )
    assert isinstance(event_id, UUID)

    async with d2_schema_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT user_id, capability_code, status, input_tokens,
                   output_tokens, total_tokens
            FROM ai_usage_events
            WHERE id = $1
            """,
            event_id,
        )
    assert row is not None
    assert row["user_id"] == user_id
    assert row["capability_code"] == "dict_ai_lookup"
    assert row["input_tokens"] == 10
    assert row["output_tokens"] == 5
    assert row["total_tokens"] == 15


async def test_credit_ledger_list_projects_title_snapshot_as_article_title(
    d2_schema_pool: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(db_connection, "DB_POOL", d2_schema_pool)
    async with d2_schema_pool.acquire() as conn:
        user_id = await conn.fetchval("INSERT INTO users DEFAULT VALUES RETURNING id")
        await conn.execute(
            """
            INSERT INTO user_credit_ledger (
                user_id, entry_type, points, bucket_type, balance_after,
                title_snapshot, metadata_json
            )
            VALUES ($1, 'ai_capability_deduct', -3, 'daily_free', 97,
                    $2, $3)
            """,
            user_id,
            "城市阅读 · 词典 AI",
            {"capability_code": "dict_ai_lookup", "query": "test"},
        )

    result = await get_credit_ledger(user_id)

    assert result["has_more"] is False
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["article_title"] == "城市阅读 · 词典 AI"
    assert item["entry_type"] == "ai_capability_deduct"
    assert item["points"] == -3
    assert "task_id" not in item
