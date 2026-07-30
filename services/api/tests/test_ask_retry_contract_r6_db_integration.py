"""ASK-RETRY-CONTRACT-R6 real PostgreSQL integration — OPT-IN only.

Prerequisites (Owner, not this agent):
1. Apply migrations 0026 + 0027 to local Postgres.
2. Set ``CLAREAD_RUN_SUBMISSION_DB_TESTS=1``.
3. Working ``DB_POOL`` (same as other integration tests).

When env is set, this module must NOT contain unconditional pytest.skip.
It self-seeds a minimal user / reading record / thread fixture.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest

# Module-level gate only — no unconditional skip inside tests when env is on.
pytestmark = pytest.mark.skipif(
    os.environ.get("CLAREAD_RUN_SUBMISSION_DB_TESTS") != "1",
    reason=(
        "opt-in: set CLAREAD_RUN_SUBMISSION_DB_TESTS=1 after Owner applies "
        "migrations 0026+0027 to local DB"
    ),
)


async def _seed_thread(conn) -> tuple[object, object, object]:
    """Create disposable user + reading_record + ask thread. Returns ids."""
    user_id = uuid4()
    record_id = uuid4()
    thread_id = uuid4()
    now = datetime.now(UTC)

    await conn.execute(
        "INSERT INTO users (id) VALUES ($1) ON CONFLICT DO NOTHING",
        user_id,
    )
    # Minimal reading_records row — columns may vary; use common baseline.
    try:
        await conn.execute(
            """
            INSERT INTO reading_records (id, user_id, created_at, updated_at)
            VALUES ($1, $2, $3, $3)
            ON CONFLICT DO NOTHING
            """,
            record_id,
            user_id,
            now,
        )
    except Exception:
        # Alternate schema: try with title/status if required.
        await conn.execute(
            """
            INSERT INTO reading_records (
                id, user_id, title, status, created_at, updated_at
            )
            VALUES ($1, $2, 'r6-test', 'ready', $3, $3)
            ON CONFLICT DO NOTHING
            """,
            record_id,
            user_id,
            now,
        )

    await conn.execute(
        """
        INSERT INTO reader_ask_threads (
            id, user_id, record_scope, reading_record_id,
            title, created_at, updated_at
        )
        VALUES ($1, $2, 'reading_record', $3, 'r6', $4, $4)
        ON CONFLICT DO NOTHING
        """,
        thread_id,
        user_id,
        record_id,
        now,
    )
    return user_id, record_id, thread_id


@pytest.mark.asyncio
async def test_r6_concurrent_ensure_one_pair_one_model_claim() -> None:
    """asyncio.gather two identical client_submission_id ensures.

    Exactly one may_create_model=True, one pair, winner readable by
    duplicate, no second model-eligible claim.
    """
    from app.database import connection as db_connection
    from app.services.reader_record_ask.repository import (
        ReaderRecordAskRepository,
        SubmissionIdempotencyUnavailable,
    )
    from app.services.reader_record_ask.submission_gateway import (
        build_retry_snapshot,
        ensure_submission_for_send,
    )

    assert db_connection.DB_POOL is not None, (
        "DB_POOL must be initialized when CLAREAD_RUN_SUBMISSION_DB_TESTS=1"
    )
    pool = db_connection.DB_POOL
    repo = ReaderRecordAskRepository()
    snap = build_retry_snapshot(
        lane="agentic",
        model_option_key="ask-clarity",
        web_search_mode="disabled",
    )

    async with pool.acquire() as conn:
        user_id, _record_id, thread_id = await _seed_thread(conn)

    client_sub = uuid4()

    async def _one():
        return await ensure_submission_for_send(
            repo=repo,
            thread_id=thread_id,
            user_id=user_id,
            client_submission_id=client_sub,
            content_md="r6 concurrent body",
            retry_snapshot=snap,
        )

    try:
        a, b = await asyncio.gather(_one(), _one())
    except SubmissionIdempotencyUnavailable as exc:
        pytest.fail(
            f"migration 0026/0027 not applied (Owner must apply): {exc}"
        )

    assert a is not None and b is not None
    winners = [x for x in (a, b) if x.may_create_model]
    losers = [x for x in (a, b) if x.stop_model]
    assert len(winners) == 1, "exactly one claim may create the model"
    assert len(losers) == 1
    winner = winners[0]
    assert winner.user_message_id is not None
    assert winner.assistant_message_id is not None

    # Third call is pure duplicate — still stop_model, same pair.
    c = await ensure_submission_for_send(
        repo=repo,
        thread_id=thread_id,
        user_id=user_id,
        client_submission_id=client_sub,
        content_md="r6 concurrent body",
        retry_snapshot=snap,
    )
    assert c is not None
    assert c.may_create_model is False
    assert c.stop_model is True
    assert c.user_message_id == winner.user_message_id
    assert c.assistant_message_id == winner.assistant_message_id


@pytest.mark.asyncio
async def test_r6_stale_generation_terminal_rejected() -> None:
    """Old claim_generation must not overwrite a newer generation's status."""
    from app.database import connection as db_connection
    from app.services.reader_record_ask.repository import (
        ReaderRecordAskRepository,
        SubmissionIdempotencyUnavailable,
    )
    from app.services.reader_record_ask.submission_gateway import (
        build_retry_snapshot,
        ensure_submission_for_send,
    )

    assert db_connection.DB_POOL is not None
    pool = db_connection.DB_POOL
    repo = ReaderRecordAskRepository()
    snap = build_retry_snapshot(
        lane="legacy",
        model_option_key="ask-fast",
        web_search_mode="disabled",
    )

    async with pool.acquire() as conn:
        user_id, _record_id, thread_id = await _seed_thread(conn)

    client_sub = uuid4()
    try:
        fresh = await ensure_submission_for_send(
            repo=repo,
            thread_id=thread_id,
            user_id=user_id,
            client_submission_id=client_sub,
            content_md="gen cas",
            retry_snapshot=snap,
        )
    except SubmissionIdempotencyUnavailable as exc:
        pytest.fail(f"migration missing: {exc}")

    assert fresh is not None and fresh.may_create_model
    gen = int(fresh.claim_generation or 1)

    # Stale generation must not complete.
    n = await repo.mark_client_submission_terminal(
        status="completed",
        thread_id=thread_id,
        client_submission_id=client_sub,
        claim_generation=gen + 99,
    )
    assert n == 0

    # Correct generation succeeds.
    n2 = await repo.mark_client_submission_terminal(
        status="completed",
        thread_id=thread_id,
        client_submission_id=client_sub,
        claim_generation=gen,
    )
    assert n2 == 1

    # Already terminal — second update is no-op.
    n3 = await repo.mark_client_submission_terminal(
        status="failed",
        thread_id=thread_id,
        client_submission_id=client_sub,
        claim_generation=gen,
    )
    assert n3 == 0
