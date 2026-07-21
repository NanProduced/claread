"""Migration test for 0022_ai_usage_events_invocation_key.sql.

Verifies the partial unique index ``uq_ai_usage_events_invocation_key``
on ``ai_usage_events(request_id)`` that hardens idempotent model-invocation
usage persistence for the ``reader_grammar_batch:`` namespace.

The test connects to the main (public schema) database and SKIPS if
migration 0022 has not been applied yet. This is by design: Phase 6 must
not execute migration 0022 (hard constraint, awaiting user confirmation
in Phase 7). Once the user applies 0022 in Phase 8, the skip guard
lifts and the three tests run for real.

See infra/migrations/0022_ai_usage_events_invocation_key.sql.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from uuid import uuid4

import asyncpg
import pytest

from tests.test_reader_orchestration_schema_baseline import DATABASE_URL

pytestmark = pytest.mark.anyio

_INDEX_NAME = "uq_ai_usage_events_invocation_key"
_BATCH_PREFIX = "reader_grammar_batch:"

_SKIP_REASON = (
    "Migration 0022 not applied: uq_ai_usage_events_invocation_key index "
    "not found. Run migration 0022 to enable this test."
)


def _batch_invocation_key() -> str:
    """Build a unique reader_grammar_batch: invocation key for test rows."""
    return f"{_BATCH_PREFIX}test_job_{uuid4().hex}:test_lease_token_{uuid4().hex}"


async def _migration_0022_applied(conn: asyncpg.Connection) -> bool:
    """Check if uq_ai_usage_events_invocation_key index exists."""
    result = await conn.fetchval(
        """
        SELECT 1 FROM pg_indexes
        WHERE indexname = $1
        LIMIT 1
        """,
        _INDEX_NAME,
    )
    return result is not None


async def _skip_if_migration_0022_not_applied(conn: asyncpg.Connection) -> None:
    if not await _migration_0022_applied(conn):
        pytest.skip(_SKIP_REASON)


@pytest.fixture
async def main_conn() -> AsyncIterator[asyncpg.Connection]:
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        yield conn
    finally:
        await conn.close()


async def _insert_usage_event(
    conn: asyncpg.Connection,
    *,
    request_id: str | None,
) -> None:
    """Insert a minimal ai_usage_events row with the given request_id."""
    await conn.execute(
        """
        INSERT INTO ai_usage_events (
            usage_scope, capability_code, billing_mode, status, request_id
        ) VALUES ($1, $2, $3, $4, $5)
        """,
        "system_internal",
        "test_migration_0022",
        "no_charge",
        "succeeded",
        request_id,
    )


async def _try_insert_usage_event(
    conn: asyncpg.Connection,
    *,
    request_id: str,
) -> bool:
    """Try to insert a row; return True on success, False on UniqueViolationError."""
    try:
        async with conn.transaction():
            await _insert_usage_event(conn, request_id=request_id)
        return True
    except asyncpg.UniqueViolationError:
        return False


async def _cleanup_by_request_id(
    conn: asyncpg.Connection,
    request_ids: list[str | None],
) -> None:
    """Delete test rows by request_id (None entries skipped)."""
    for rid in request_ids:
        if rid is None:
            continue
        await conn.execute(
            "DELETE FROM ai_usage_events WHERE request_id = $1",
            rid,
        )


# --- Test 6.1.1: index existence ------------------------------------------


async def test_uq_ai_usage_events_invocation_key_index_exists(
    main_conn: asyncpg.Connection,
) -> None:
    """Migration 0022 must create the partial unique index."""
    await _skip_if_migration_0022_not_applied(main_conn)

    idx_def = await main_conn.fetchval(
        """
        SELECT indexdef FROM pg_indexes
        WHERE indexname = $1
        """,
        _INDEX_NAME,
    )
    assert idx_def is not None, f"index {_INDEX_NAME} must exist"
    assert "UNIQUE" in idx_def.upper(), "index must be UNIQUE"
    assert "ai_usage_events" in idx_def, "index must be on ai_usage_events"
    assert (
        "reader_grammar_batch:" in idx_def
    ), "index must be partial on batch namespace"


# --- Test 6.1.2: concurrent idempotency ------------------------------------


async def test_concurrent_insert_with_same_invocation_key_only_one_row_survives(
    main_conn: asyncpg.Connection,
) -> None:
    """Two concurrent inserts of the same batch invocation_key collapse to one row."""
    await _skip_if_migration_0022_not_applied(main_conn)

    invocation_key = _batch_invocation_key()
    conn2 = await asyncpg.connect(DATABASE_URL)
    try:
        results = await asyncio.gather(
            _try_insert_usage_event(main_conn, request_id=invocation_key),
            _try_insert_usage_event(conn2, request_id=invocation_key),
        )
        success_count = sum(1 for ok in results if ok)
        assert success_count == 1, (
            f"expected exactly one insert to succeed, got {success_count}; "
            f"results={results}"
        )

        surviving = await main_conn.fetchval(
            "SELECT count(*) FROM ai_usage_events WHERE request_id = $1",
            invocation_key,
        )
        assert surviving == 1, f"expected exactly one row, got {surviving}"
    finally:
        await conn2.close()
        await _cleanup_by_request_id(main_conn, [invocation_key])


# --- Test 6.1.3: legacy compatibility --------------------------------------


async def test_legacy_request_id_not_subject_to_partial_index(
    main_conn: asyncpg.Connection,
) -> None:
    """Non-batch request_id values (per-unit, legacy, NULL) bypass the partial index."""
    await _skip_if_migration_0022_not_applied(main_conn)

    legacy_request_ids: list[str | None] = [
        f"reader_grammar_per_unit:test_{uuid4().hex}",
        f"legacy_request_id_{uuid4().hex}",
        None,
    ]
    try:
        for rid in legacy_request_ids:
            await _insert_usage_event(main_conn, request_id=rid)

        non_null_count = sum(1 for r in legacy_request_ids if r is not None)
        inserted = await main_conn.fetchval(
            """
            SELECT count(*) FROM ai_usage_events
            WHERE request_id = ANY($1::text[])
            """,
            [r for r in legacy_request_ids if r is not None],
        )
        assert inserted == non_null_count, (
            f"expected {non_null_count} non-null legacy rows, got {inserted}"
        )
    finally:
        await _cleanup_by_request_id(main_conn, legacy_request_ids)
