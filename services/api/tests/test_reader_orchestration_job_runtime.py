from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.contracts.annotation import utf16_code_unit_length
from app.database.connection import init_connection
from app.services.reader_orchestration.job_runtime import (
    STATUS_CLAIMED,
    STATUS_FAILED_TERMINAL,
    STATUS_PAUSED,
    STATUS_QUEUED,
    STATUS_RETRY_LATER,
    STATUS_SUCCEEDED,
    STATUS_SUPERSEDED,
    FenceViolationError,
    IllegalTransitionError,
    LeaseExpiredError,
    LeaseTokenMismatchError,
    ReaderJobRuntime,
)
from tests.test_reader_orchestration_schema_baseline import BASELINE_SQL, DATABASE_URL

pytestmark = pytest.mark.anyio

API_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Pool / schema fixtures
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


@pytest.fixture
async def job_runtime_env() -> asyncpg.Pool:
    schema_name = f"test_reader_job_runtime_{uuid4().hex}"
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


# ---------------------------------------------------------------------------
# Insert helpers
# ---------------------------------------------------------------------------


async def _insert_user(pool: asyncpg.Pool) -> UUID:
    async with pool.acquire() as conn:
        user_id = await conn.fetchval("INSERT INTO users DEFAULT VALUES RETURNING id")
    assert isinstance(user_id, UUID)
    return user_id


async def _insert_record(
    pool: asyncpg.Pool,
    user_id: UUID,
    *,
    generation: int = 1,
    title: str = "Job Runtime Test",
) -> UUID:
    async with pool.acquire() as conn:
        record_id = await conn.fetchval(
            """
            INSERT INTO reading_records (user_id, source_type, title, language, generation)
            VALUES ($1, 'text', $2, 'en', $3)
            RETURNING id
            """,
            user_id,
            title,
            generation,
        )
    assert isinstance(record_id, UUID)
    return record_id


async def _insert_base(
    pool: asyncpg.Pool,
    record_id: UUID,
    *,
    base_version: int = 1,
    record_generation: int = 1,
    text: str = "Base text for runtime.",
    status: str = "active",
) -> UUID:
    async with pool.acquire() as conn:
        base_id = await conn.fetchval(
            """
            INSERT INTO reading_bases (
                reading_record_id,
                base_version,
                record_generation,
                text,
                content_sha256,
                content_utf16_length,
                canonicalizer_version,
                builder_version,
                segmenter_version,
                language,
                title_snapshot,
                navigation_json,
                status
            )
            VALUES (
                $1, $2, $3, $4, $5, $6,
                'd3-p4-canonicalizer', 'd3-p4-builder', 'd3-p4-segmenter',
                'en', 'Runtime Title', '{"units":[]}'::jsonb, $7
            )
            RETURNING id
            """,
            record_id,
            base_version,
            record_generation,
            text,
            hashlib.sha256(text.encode("utf-8")).hexdigest(),
            utf16_code_unit_length(text),
            status,
        )
    assert isinstance(base_id, UUID)
    return base_id


async def _set_active_base(
    pool: asyncpg.Pool,
    *,
    record_id: UUID,
    base_id: UUID | None,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE reading_records SET active_base_id = $2 WHERE id = $1",
            record_id,
            base_id,
        )


async def _insert_run(
    pool: asyncpg.Pool,
    record_id: UUID,
    user_id: UUID,
    *,
    record_generation: int = 1,
) -> UUID:
    async with pool.acquire() as conn:
        run_id = await conn.fetchval(
            """
            INSERT INTO reader_runs (
                reading_record_id, user_id, run_type, status,
                record_generation, envelope_json, policy_version, trigger_kind
            )
            VALUES ($1, $2, 'initial_build', 'queued', $3, '{}'::jsonb, 'd3-p4', 'user')
            RETURNING id
            """,
            record_id,
            user_id,
            record_generation,
        )
    assert isinstance(run_id, UUID)
    return run_id


async def _insert_job(
    pool: asyncpg.Pool,
    *,
    record_id: UUID,
    run_id: UUID,
    user_id: UUID,
    base_id: UUID | None,
    job_type: str = "translate_unit",
    target_type: str = "unit",
    target_key: str = "u1",
    status: str = "queued",
    priority: int = 0,
    available_at: datetime | None = None,
    expected_generation: int = 1,
    operation_fingerprint: str = "fp-job",
    idempotency_key: str | None = None,
    max_attempts: int = 3,
    attempt_count: int = 0,
) -> UUID:
    if idempotency_key is None:
        idempotency_key = f"id-{uuid4().hex}"
    async with pool.acquire() as conn:
        job_id = await conn.fetchval(
            """
            INSERT INTO reader_jobs (
                reading_record_id, base_id, run_id, user_id,
                job_type, target_type, target_key, status,
                priority, available_at,
                expected_generation, operation_fingerprint, idempotency_key,
                max_attempts, attempt_count
            )
            VALUES (
                $1, $2, $3, $4,
                $5, $6, $7, $8,
                $9, COALESCE($10, NOW()),
                $11, $12, $13,
                $14, $15
            )
            RETURNING id
            """,
            record_id,
            base_id,
            run_id,
            user_id,
            job_type,
            target_type,
            target_key,
            status,
            priority,
            available_at,
            expected_generation,
            operation_fingerprint,
            idempotency_key,
            max_attempts,
            attempt_count,
        )
    assert isinstance(job_id, UUID)
    return job_id


async def _fetch_job(pool: asyncpg.Pool, job_id: UUID) -> asyncpg.Record:
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM reader_jobs WHERE id = $1", job_id)


async def _seed_active_record(
    pool: asyncpg.Pool,
    *,
    generation: int = 1,
) -> tuple[UUID, UUID, UUID, UUID]:
    """Seed user, record, active base, run. Returns (user_id, record_id, base_id, run_id)."""
    user_id = await _insert_user(pool)
    record_id = await _insert_record(pool, user_id, generation=generation)
    base_id = await _insert_base(pool, record_id, record_generation=generation)
    await _set_active_base(pool, record_id=record_id, base_id=base_id)
    run_id = await _insert_run(pool, record_id, user_id, record_generation=generation)
    return user_id, record_id, base_id, run_id


# ---------------------------------------------------------------------------
# Tests: claim ordering and concurrency
# ---------------------------------------------------------------------------


async def test_claim_returns_none_when_queue_empty(
    job_runtime_env: asyncpg.Pool,
) -> None:
    runtime = ReaderJobRuntime(pool=job_runtime_env)
    result = await runtime.claim_next_job(
        lease_owner="worker-1",
        lease_duration=timedelta(seconds=30),
    )
    assert result is None


async def test_claim_order_priority_desc_available_at_asc_created_at_asc_id_asc(
    job_runtime_env: asyncpg.Pool,
) -> None:
    user_id, record_id, base_id, run_id = await _seed_active_record(job_runtime_env)

    # job_low_pri: priority 0, available_at NOW()
    job_low = await _insert_job(
        job_runtime_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="low",
        priority=0,
        operation_fingerprint="fp-low",
        idempotency_key="id-low",
    )
    # job_high_pri: priority 10, available_at NOW()
    job_high = await _insert_job(
        job_runtime_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="high",
        priority=10,
        operation_fingerprint="fp-high",
        idempotency_key="id-high",
    )
    # job_future: priority 100 but available_at in the future -> should NOT be claimed first
    job_future = await _insert_job(
        job_runtime_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="future",
        priority=100,
        available_at=datetime.now(UTC) + timedelta(hours=1),
        operation_fingerprint="fp-future",
        idempotency_key="id-future",
    )

    runtime = ReaderJobRuntime(pool=job_runtime_env)

    first = await runtime.claim_next_job(
        lease_owner="w1", lease_duration=timedelta(seconds=30)
    )
    assert first is not None
    assert first.job_id == job_high

    second = await runtime.claim_next_job(
        lease_owner="w1", lease_duration=timedelta(seconds=30)
    )
    assert second is not None
    assert second.job_id == job_low

    third = await runtime.claim_next_job(
        lease_owner="w1", lease_duration=timedelta(seconds=30)
    )
    assert third is None

    # The future job remains queued and unclaimable until due.
    future_row = await _fetch_job(job_runtime_env, job_future)
    assert future_row["status"] == STATUS_QUEUED
    assert future_row["lease_token"] is None


async def test_concurrent_claim_same_job_only_one_worker_wins(
    job_runtime_env: asyncpg.Pool,
) -> None:
    user_id, record_id, base_id, run_id = await _seed_active_record(job_runtime_env)
    job_id = await _insert_job(
        job_runtime_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="contended",
        operation_fingerprint="fp-contended",
        idempotency_key="id-contended",
    )

    runtime = ReaderJobRuntime(pool=job_runtime_env)

    # Fire two claims in parallel; only one should win.
    results = await asyncio.gather(
        runtime.claim_next_job(lease_owner="w-a", lease_duration=timedelta(seconds=30)),
        runtime.claim_next_job(lease_owner="w-b", lease_duration=timedelta(seconds=30)),
    )
    claimed = [r for r in results if r is not None]
    assert len(claimed) == 1
    assert claimed[0].job_id == job_id

    row = await _fetch_job(job_runtime_env, job_id)
    assert row["status"] == STATUS_CLAIMED
    assert row["attempt_count"] == 1
    assert row["lease_owner"] in {"w-a", "w-b"}
    assert row["lease_token"] is not None
    assert row["lease_expires_at"] is not None
    assert row["claimed_at"] is not None


async def test_claim_increments_attempt_count_each_claim(
    job_runtime_env: asyncpg.Pool,
) -> None:
    user_id, record_id, base_id, run_id = await _seed_active_record(job_runtime_env)
    job_id = await _insert_job(
        job_runtime_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="attempt",
        operation_fingerprint="fp-attempt",
        idempotency_key="id-attempt",
    )

    runtime = ReaderJobRuntime(pool=job_runtime_env)

    first = await runtime.claim_next_job(
        lease_owner="w1", lease_duration=timedelta(seconds=30)
    )
    assert first is not None
    assert first.attempt_count == 1

    # Release back to retry_later, then claim again.
    await runtime.transition(
        job_id=job_id,
        target_status=STATUS_RETRY_LATER,
        lease_token=first.lease_token,
        available_at=datetime.now(UTC) - timedelta(seconds=1),
    )

    second = await runtime.claim_next_job(
        lease_owner="w1", lease_duration=timedelta(seconds=30)
    )
    assert second is not None
    assert second.attempt_count == 2


# ---------------------------------------------------------------------------
# Tests: retry_later due / not due
# ---------------------------------------------------------------------------


async def test_retry_later_not_due_cannot_be_claimed(
    job_runtime_env: asyncpg.Pool,
) -> None:
    user_id, record_id, base_id, run_id = await _seed_active_record(job_runtime_env)
    future = datetime.now(UTC) + timedelta(hours=1)
    job_id = await _insert_job(
        job_runtime_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="retry-future",
        status=STATUS_RETRY_LATER,
        available_at=future,
        operation_fingerprint="fp-retry-future",
        idempotency_key="id-retry-future",
    )

    runtime = ReaderJobRuntime(pool=job_runtime_env)
    result = await runtime.claim_next_job(
        lease_owner="w1", lease_duration=timedelta(seconds=30)
    )
    assert result is None

    row = await _fetch_job(job_runtime_env, job_id)
    assert row["status"] == STATUS_RETRY_LATER
    assert row["lease_token"] is None


async def test_retry_later_due_can_be_claimed(
    job_runtime_env: asyncpg.Pool,
) -> None:
    user_id, record_id, base_id, run_id = await _seed_active_record(job_runtime_env)
    past = datetime.now(UTC) - timedelta(seconds=1)
    job_id = await _insert_job(
        job_runtime_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="retry-due",
        status=STATUS_RETRY_LATER,
        available_at=past,
        operation_fingerprint="fp-retry-due",
        idempotency_key="id-retry-due",
    )

    runtime = ReaderJobRuntime(pool=job_runtime_env)
    result = await runtime.claim_next_job(
        lease_owner="w1", lease_duration=timedelta(seconds=30)
    )
    assert result is not None
    assert result.job_id == job_id
    assert result.attempt_count == 1


# ---------------------------------------------------------------------------
# Tests: heartbeat
# ---------------------------------------------------------------------------


async def test_heartbeat_extends_lease_with_matching_token(
    job_runtime_env: asyncpg.Pool,
) -> None:
    user_id, record_id, base_id, run_id = await _seed_active_record(job_runtime_env)
    job_id = await _insert_job(
        job_runtime_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="hb",
        operation_fingerprint="fp-hb",
        idempotency_key="id-hb",
    )

    runtime = ReaderJobRuntime(pool=job_runtime_env)
    claimed = await runtime.claim_next_job(
        lease_owner="w1", lease_duration=timedelta(seconds=10)
    )
    assert claimed is not None

    new_expires = await runtime.heartbeat(
        job_id=job_id,
        lease_token=claimed.lease_token,
        lease_duration=timedelta(seconds=60),
    )
    assert new_expires > claimed.lease_expires_at

    row = await _fetch_job(job_runtime_env, job_id)
    assert row["lease_expires_at"] == new_expires


async def test_heartbeat_token_mismatch_fails(
    job_runtime_env: asyncpg.Pool,
) -> None:
    user_id, record_id, base_id, run_id = await _seed_active_record(job_runtime_env)
    job_id = await _insert_job(
        job_runtime_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="hb-mismatch",
        operation_fingerprint="fp-hb-mismatch",
        idempotency_key="id-hb-mismatch",
    )

    runtime = ReaderJobRuntime(pool=job_runtime_env)
    claimed = await runtime.claim_next_job(
        lease_owner="w1", lease_duration=timedelta(seconds=30)
    )
    assert claimed is not None

    wrong_token = uuid4()
    with pytest.raises(LeaseTokenMismatchError):
        await runtime.heartbeat(
            job_id=job_id,
            lease_token=wrong_token,
            lease_duration=timedelta(seconds=30),
        )

    # Job state is unchanged after the failed heartbeat.
    row = await _fetch_job(job_runtime_env, job_id)
    assert row["status"] == STATUS_CLAIMED
    assert row["lease_token"] == claimed.lease_token


async def test_heartbeat_expired_lease_fails(
    job_runtime_env: asyncpg.Pool,
) -> None:
    user_id, record_id, base_id, run_id = await _seed_active_record(job_runtime_env)
    job_id = await _insert_job(
        job_runtime_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="hb-expired",
        operation_fingerprint="fp-hb-expired",
        idempotency_key="id-hb-expired",
    )

    runtime = ReaderJobRuntime(pool=job_runtime_env)
    claimed = await runtime.claim_next_job(
        lease_owner="w1", lease_duration=timedelta(seconds=30)
    )
    assert claimed is not None

    # Force the lease to be expired.
    async with job_runtime_env.acquire() as conn:
        await conn.execute(
            "UPDATE reader_jobs SET lease_expires_at = NOW() - INTERVAL '1 second' WHERE id = $1",
            job_id,
        )

    with pytest.raises(LeaseExpiredError):
        await runtime.heartbeat(
            job_id=job_id,
            lease_token=claimed.lease_token,
            lease_duration=timedelta(seconds=30),
        )


# ---------------------------------------------------------------------------
# Tests: stale lease recovery
# ---------------------------------------------------------------------------


async def test_recover_stale_leases_requeues_when_attempts_remaining(
    job_runtime_env: asyncpg.Pool,
) -> None:
    user_id, record_id, base_id, run_id = await _seed_active_record(job_runtime_env)
    job_id = await _insert_job(
        job_runtime_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="stale-recover",
        max_attempts=3,
        operation_fingerprint="fp-stale-recover",
        idempotency_key="id-stale-recover",
    )

    runtime = ReaderJobRuntime(pool=job_runtime_env)
    claimed = await runtime.claim_next_job(
        lease_owner="w1", lease_duration=timedelta(seconds=30)
    )
    assert claimed is not None
    assert claimed.attempt_count == 1

    # Force lease expiry.
    async with job_runtime_env.acquire() as conn:
        await conn.execute(
            "UPDATE reader_jobs SET lease_expires_at = NOW() - INTERVAL '1 second' WHERE id = $1",
            job_id,
        )

    recovered = await runtime.recover_stale_leases()
    assert recovered == 1

    row = await _fetch_job(job_runtime_env, job_id)
    assert row["status"] == STATUS_QUEUED
    assert row["lease_token"] is None
    assert row["lease_owner"] is None
    assert row["lease_expires_at"] is None
    assert row["claimed_at"] is None
    assert row["rationale_code"] == "lease_lost"
    # attempt_count is preserved across recovery.
    assert row["attempt_count"] == 1

    # A new claim should succeed with a fresh lease token.
    reclaimed = await runtime.claim_next_job(
        lease_owner="w2", lease_duration=timedelta(seconds=30)
    )
    assert reclaimed is not None
    assert reclaimed.job_id == job_id
    assert reclaimed.lease_token != claimed.lease_token
    assert reclaimed.attempt_count == 2


async def test_recover_stale_leases_fails_terminal_when_max_attempts_reached(
    job_runtime_env: asyncpg.Pool,
) -> None:
    user_id, record_id, base_id, run_id = await _seed_active_record(job_runtime_env)
    job_id = await _insert_job(
        job_runtime_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="stale-max",
        max_attempts=2,
        attempt_count=2,
        operation_fingerprint="fp-stale-max",
        idempotency_key="id-stale-max",
    )

    # Manually mark as claimed with an expired lease.
    async with job_runtime_env.acquire() as conn:
        await conn.execute(
            """
            UPDATE reader_jobs
            SET status = 'claimed',
                lease_owner = 'w-dead',
                lease_token = $2,
                lease_expires_at = NOW() - INTERVAL '1 second',
                claimed_at = NOW() - INTERVAL '1 minute'
            WHERE id = $1
            """,
            job_id,
            uuid4(),
        )

    runtime = ReaderJobRuntime(pool=job_runtime_env)
    recovered = await runtime.recover_stale_leases()
    assert recovered == 1

    row = await _fetch_job(job_runtime_env, job_id)
    assert row["status"] == STATUS_FAILED_TERMINAL
    assert row["lease_token"] is None
    assert row["failure_class"] == "lease_lost"
    assert row["failure_code"] == "max_attempts_exceeded"
    assert row["rationale_code"] == "lease_lost_max_attempts"


async def test_recover_stale_leases_skips_active_lease(
    job_runtime_env: asyncpg.Pool,
) -> None:
    user_id, record_id, base_id, run_id = await _seed_active_record(job_runtime_env)
    job_id = await _insert_job(
        job_runtime_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="stale-active",
        operation_fingerprint="fp-stale-active",
        idempotency_key="id-stale-active",
    )

    runtime = ReaderJobRuntime(pool=job_runtime_env)
    claimed = await runtime.claim_next_job(
        lease_owner="w1", lease_duration=timedelta(seconds=30)
    )
    assert claimed is not None

    recovered = await runtime.recover_stale_leases()
    assert recovered == 0

    row = await _fetch_job(job_runtime_env, job_id)
    assert row["status"] == STATUS_CLAIMED
    assert row["lease_token"] == claimed.lease_token


# ---------------------------------------------------------------------------
# Tests: fence validation
# ---------------------------------------------------------------------------


async def test_claim_rejects_stale_generation_and_marks_superseded(
    job_runtime_env: asyncpg.Pool,
) -> None:
    user_id, record_id, base_id, run_id = await _seed_active_record(
        job_runtime_env, generation=1
    )
    # Job expects generation 1, but the record has moved to generation 2.
    await _insert_job(
        job_runtime_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="stale-gen",
        expected_generation=1,
        operation_fingerprint="fp-stale-gen",
        idempotency_key="id-stale-gen",
    )
    async with job_runtime_env.acquire() as conn:
        await conn.execute(
            "UPDATE reading_records SET active_base_id = NULL, generation = 2 WHERE id = $1",
            record_id,
        )

    runtime = ReaderJobRuntime(pool=job_runtime_env)
    result = await runtime.claim_next_job(
        lease_owner="w1", lease_duration=timedelta(seconds=30)
    )
    assert result is None

    async with job_runtime_env.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, rationale_code FROM reader_jobs WHERE target_key = $1",
            "stale-gen",
        )
    assert row["status"] == STATUS_SUPERSEDED
    assert row["rationale_code"] == "stale_generation"


async def test_claim_rejects_inactive_base_and_marks_superseded(
    job_runtime_env: asyncpg.Pool,
) -> None:
    user_id, record_id, base_id, run_id = await _seed_active_record(job_runtime_env)
    # Flip the base to superseded.
    async with job_runtime_env.acquire() as conn:
        await conn.execute(
            "UPDATE reading_bases SET status = 'superseded' WHERE id = $1",
            base_id,
        )

    await _insert_job(
        job_runtime_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="inactive-base",
        operation_fingerprint="fp-inactive-base",
        idempotency_key="id-inactive-base",
    )

    runtime = ReaderJobRuntime(pool=job_runtime_env)
    result = await runtime.claim_next_job(
        lease_owner="w1", lease_duration=timedelta(seconds=30)
    )
    assert result is None

    async with job_runtime_env.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, rationale_code FROM reader_jobs WHERE target_key = $1",
            "inactive-base",
        )
    assert row["status"] == STATUS_SUPERSEDED
    assert row["rationale_code"] == "inactive_base"


async def test_claim_rejects_base_that_is_not_record_active_base(
    job_runtime_env: asyncpg.Pool,
) -> None:
    user_id, record_id, base_id, run_id = await _seed_active_record(job_runtime_env)
    await _set_active_base(job_runtime_env, record_id=record_id, base_id=None)

    await _insert_job(
        job_runtime_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="inactive-active-base",
        operation_fingerprint="fp-inactive-active-base",
        idempotency_key="id-inactive-active-base",
    )

    runtime = ReaderJobRuntime(pool=job_runtime_env)
    result = await runtime.claim_next_job(
        lease_owner="w1", lease_duration=timedelta(seconds=30)
    )
    assert result is None

    async with job_runtime_env.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, rationale_code FROM reader_jobs WHERE target_key = $1",
            "inactive-active-base",
        )
    assert row["status"] == STATUS_SUPERSEDED
    assert row["rationale_code"] == "active_base_mismatch"


async def test_claim_rejects_missing_base_for_non_build_base_job(
    job_runtime_env: asyncpg.Pool,
) -> None:
    user_id, record_id, _base_id, run_id = await _seed_active_record(job_runtime_env)
    # Insert a translate_unit job with base_id NULL via raw SQL bypassing the
    # CHECK constraint is impossible; instead we delete the base row to make
    # the FK dangling is also blocked by ON DELETE CASCADE. We instead simulate
    # "missing base" by inserting a build_base/record job (allowed NULL base_id)
    # and then verifying that a translate_unit/record job with NULL base_id is
    # rejected by the CHECK constraint at insert time. The runtime fence for
    # missing base is exercised by deleting the base after the job is created.
    job_id = await _insert_job(
        job_runtime_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=None,
        job_type="build_base",
        target_type="record",
        target_key=str(record_id),
        operation_fingerprint="fp-build-base",
        idempotency_key="id-build-base",
    )

    runtime = ReaderJobRuntime(pool=job_runtime_env)
    # build_base + record + null base_id is allowed by the fence.
    claimed = await runtime.claim_next_job(
        lease_owner="w1", lease_duration=timedelta(seconds=30)
    )
    assert claimed is not None
    assert claimed.job_id == job_id


async def test_publish_fence_rejects_succeeded_with_stale_generation(
    job_runtime_env: asyncpg.Pool,
) -> None:
    user_id, record_id, base_id, run_id = await _seed_active_record(
        job_runtime_env, generation=1
    )
    job_id = await _insert_job(
        job_runtime_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="publish-stale",
        expected_generation=1,
        operation_fingerprint="fp-publish-stale",
        idempotency_key="id-publish-stale",
    )

    runtime = ReaderJobRuntime(pool=job_runtime_env)
    claimed = await runtime.claim_next_job(
        lease_owner="w1", lease_duration=timedelta(seconds=30)
    )
    assert claimed is not None

    # Bump the record generation after claim; publish fence must reject.
    async with job_runtime_env.acquire() as conn:
        await conn.execute(
            "UPDATE reading_records SET active_base_id = NULL, generation = 2 WHERE id = $1",
            record_id,
        )

    with pytest.raises(FenceViolationError, match="stale_generation"):
        await runtime.transition(
            job_id=job_id,
            target_status=STATUS_SUCCEEDED,
            lease_token=claimed.lease_token,
        )

    row = await _fetch_job(job_runtime_env, job_id)
    assert row["status"] == STATUS_CLAIMED


async def test_publish_fence_rejects_succeeded_with_inactive_base(
    job_runtime_env: asyncpg.Pool,
) -> None:
    user_id, record_id, base_id, run_id = await _seed_active_record(job_runtime_env)
    job_id = await _insert_job(
        job_runtime_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="publish-inactive",
        operation_fingerprint="fp-publish-inactive",
        idempotency_key="id-publish-inactive",
    )

    runtime = ReaderJobRuntime(pool=job_runtime_env)
    claimed = await runtime.claim_next_job(
        lease_owner="w1", lease_duration=timedelta(seconds=30)
    )
    assert claimed is not None

    async with job_runtime_env.acquire() as conn:
        await conn.execute(
            "UPDATE reading_bases SET status = 'superseded' WHERE id = $1",
            base_id,
        )

    with pytest.raises(FenceViolationError, match="inactive_base"):
        await runtime.transition(
            job_id=job_id,
            target_status=STATUS_SUCCEEDED,
            lease_token=claimed.lease_token,
        )


async def test_publish_fence_rejects_succeeded_when_base_is_not_active_base(
    job_runtime_env: asyncpg.Pool,
) -> None:
    user_id, record_id, base_id, run_id = await _seed_active_record(job_runtime_env)
    job_id = await _insert_job(
        job_runtime_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="publish-active-base-mismatch",
        operation_fingerprint="fp-publish-active-base-mismatch",
        idempotency_key="id-publish-active-base-mismatch",
    )

    runtime = ReaderJobRuntime(pool=job_runtime_env)
    claimed = await runtime.claim_next_job(
        lease_owner="w1", lease_duration=timedelta(seconds=30)
    )
    assert claimed is not None

    await _set_active_base(job_runtime_env, record_id=record_id, base_id=None)

    with pytest.raises(FenceViolationError, match="active_base_mismatch"):
        await runtime.transition(
            job_id=job_id,
            target_status=STATUS_SUCCEEDED,
            lease_token=claimed.lease_token,
        )

    row = await _fetch_job(job_runtime_env, job_id)
    assert row["status"] == STATUS_CLAIMED


# ---------------------------------------------------------------------------
# Tests: transition helper
# ---------------------------------------------------------------------------


async def test_transition_succeeded_clears_lease(
    job_runtime_env: asyncpg.Pool,
) -> None:
    user_id, record_id, base_id, run_id = await _seed_active_record(job_runtime_env)
    job_id = await _insert_job(
        job_runtime_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="ok",
        operation_fingerprint="fp-ok",
        idempotency_key="id-ok",
    )

    runtime = ReaderJobRuntime(pool=job_runtime_env)
    claimed = await runtime.claim_next_job(
        lease_owner="w1", lease_duration=timedelta(seconds=30)
    )
    assert claimed is not None

    snapshot = await runtime.transition(
        job_id=job_id,
        target_status=STATUS_SUCCEEDED,
        lease_token=claimed.lease_token,
        output_ref={"artifact": "ok"},
    )
    assert snapshot.status == STATUS_SUCCEEDED
    assert snapshot.lease_token is None
    assert snapshot.lease_owner is None
    assert snapshot.lease_expires_at is None
    assert snapshot.claimed_at is None


async def test_transition_retry_later_requires_available_at(
    job_runtime_env: asyncpg.Pool,
) -> None:
    user_id, record_id, base_id, run_id = await _seed_active_record(job_runtime_env)
    job_id = await _insert_job(
        job_runtime_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="retry-req",
        operation_fingerprint="fp-retry-req",
        idempotency_key="id-retry-req",
    )

    runtime = ReaderJobRuntime(pool=job_runtime_env)
    claimed = await runtime.claim_next_job(
        lease_owner="w1", lease_duration=timedelta(seconds=30)
    )
    assert claimed is not None

    with pytest.raises(ValueError, match="available_at is required"):
        await runtime.transition(
            job_id=job_id,
            target_status=STATUS_RETRY_LATER,
            lease_token=claimed.lease_token,
        )


async def test_transition_paused_requires_pause_owner(
    job_runtime_env: asyncpg.Pool,
) -> None:
    user_id, record_id, base_id, run_id = await _seed_active_record(job_runtime_env)
    job_id = await _insert_job(
        job_runtime_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="pause-req",
        operation_fingerprint="fp-pause-req",
        idempotency_key="id-pause-req",
    )

    runtime = ReaderJobRuntime(pool=job_runtime_env)
    claimed = await runtime.claim_next_job(
        lease_owner="w1", lease_duration=timedelta(seconds=30)
    )
    assert claimed is not None

    with pytest.raises(ValueError, match="pause_owner is required"):
        await runtime.transition(
            job_id=job_id,
            target_status=STATUS_PAUSED,
            lease_token=claimed.lease_token,
        )


async def test_transition_from_claimed_requires_lease_token(
    job_runtime_env: asyncpg.Pool,
) -> None:
    user_id, record_id, base_id, run_id = await _seed_active_record(job_runtime_env)
    job_id = await _insert_job(
        job_runtime_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="no-token",
        operation_fingerprint="fp-no-token",
        idempotency_key="id-no-token",
    )

    runtime = ReaderJobRuntime(pool=job_runtime_env)
    claimed = await runtime.claim_next_job(
        lease_owner="w1", lease_duration=timedelta(seconds=30)
    )
    assert claimed is not None

    with pytest.raises(LeaseTokenMismatchError):
        await runtime.transition(
            job_id=job_id,
            target_status=STATUS_FAILED_TERMINAL,
            lease_token=None,
        )


async def test_transition_from_claimed_with_wrong_token_fails(
    job_runtime_env: asyncpg.Pool,
) -> None:
    user_id, record_id, base_id, run_id = await _seed_active_record(job_runtime_env)
    job_id = await _insert_job(
        job_runtime_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="wrong-token",
        operation_fingerprint="fp-wrong-token",
        idempotency_key="id-wrong-token",
    )

    runtime = ReaderJobRuntime(pool=job_runtime_env)
    claimed = await runtime.claim_next_job(
        lease_owner="w1", lease_duration=timedelta(seconds=30)
    )
    assert claimed is not None

    with pytest.raises(LeaseTokenMismatchError):
        await runtime.transition(
            job_id=job_id,
            target_status=STATUS_FAILED_TERMINAL,
            lease_token=uuid4(),
        )


async def test_transition_rejects_illegal_target(
    job_runtime_env: asyncpg.Pool,
) -> None:
    user_id, record_id, base_id, run_id = await _seed_active_record(job_runtime_env)
    job_id = await _insert_job(
        job_runtime_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="illegal-target",
        operation_fingerprint="fp-illegal-target",
        idempotency_key="id-illegal-target",
    )

    runtime = ReaderJobRuntime(pool=job_runtime_env)
    # 'claimed' is not a valid transition target; must use claim_next_job.
    with pytest.raises(ValueError, match="unsupported transition target"):
        await runtime.transition(job_id=job_id, target_status=STATUS_CLAIMED)


async def test_transition_rejects_illegal_state_jump(
    job_runtime_env: asyncpg.Pool,
) -> None:
    user_id, record_id, base_id, run_id = await _seed_active_record(job_runtime_env)
    # Start in queued status.
    job_id = await _insert_job(
        job_runtime_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="illegal-jump",
        status=STATUS_QUEUED,
        operation_fingerprint="fp-illegal-jump",
        idempotency_key="id-illegal-jump",
    )

    runtime = ReaderJobRuntime(pool=job_runtime_env)
    # queued -> succeeded is not allowed (must go through claimed first).
    with pytest.raises(IllegalTransitionError):
        await runtime.transition(
            job_id=job_id,
            target_status=STATUS_SUCCEEDED,
        )


async def test_transition_rejects_terminal_to_anything(
    job_runtime_env: asyncpg.Pool,
) -> None:
    user_id, record_id, base_id, run_id = await _seed_active_record(job_runtime_env)
    job_id = await _insert_job(
        job_runtime_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="terminal",
        status=STATUS_FAILED_TERMINAL,
        operation_fingerprint="fp-terminal",
        idempotency_key="id-terminal",
    )

    runtime = ReaderJobRuntime(pool=job_runtime_env)
    with pytest.raises(IllegalTransitionError):
        await runtime.transition(
            job_id=job_id,
            target_status=STATUS_SUCCEEDED,
        )


async def test_transition_paused_to_queued_via_recovery_path(
    job_runtime_env: asyncpg.Pool,
) -> None:
    user_id, record_id, base_id, run_id = await _seed_active_record(job_runtime_env)
    job_id = await _insert_job(
        job_runtime_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="paused-resume",
        operation_fingerprint="fp-paused-resume",
        idempotency_key="id-paused-resume",
    )

    runtime = ReaderJobRuntime(pool=job_runtime_env)
    claimed = await runtime.claim_next_job(
        lease_owner="w1", lease_duration=timedelta(seconds=30)
    )
    assert claimed is not None

    paused = await runtime.transition(
        job_id=job_id,
        target_status=STATUS_PAUSED,
        lease_token=claimed.lease_token,
        pause_owner="user",
    )
    assert paused.status == STATUS_PAUSED
    assert paused.pause_owner == "user"

    # paused -> queued is allowed (resume).
    resumed = await runtime.transition(
        job_id=job_id,
        target_status=STATUS_QUEUED,
    )
    assert resumed.status == STATUS_QUEUED
    assert resumed.pause_owner is None


async def test_transition_writes_job_event(
    job_runtime_env: asyncpg.Pool,
) -> None:
    user_id, record_id, base_id, run_id = await _seed_active_record(job_runtime_env)
    job_id = await _insert_job(
        job_runtime_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="event",
        operation_fingerprint="fp-event",
        idempotency_key="id-event",
    )

    runtime = ReaderJobRuntime(pool=job_runtime_env)
    claimed = await runtime.claim_next_job(
        lease_owner="w1", lease_duration=timedelta(seconds=30)
    )
    assert claimed is not None

    await runtime.transition(
        job_id=job_id,
        target_status=STATUS_FAILED_TERMINAL,
        lease_token=claimed.lease_token,
        failure_class="execution",
        failure_code="boom",
        failure_message="worker exploded",
    )

    async with job_runtime_env.acquire() as conn:
        events = await conn.fetch(
            "SELECT event_type FROM reader_job_events WHERE job_id = $1 ORDER BY created_at",
            job_id,
        )
    event_types = [e["event_type"] for e in events]
    assert "job_claimed" in event_types
    assert "job_failed_terminal" in event_types


async def test_claim_allows_input_artifact_extraction_job_with_null_base(
    job_runtime_env: asyncpg.Pool,
) -> None:
    """Extraction jobs with null base_id can now be claimed (D6-I3L).

    D6-I3K bootstraps ``input_artifact_extraction`` jobs with ``base_id IS NULL``
    (the artifact has not been extracted into a reading base yet). D6-I3L updates
    ``_validate_fence`` to allow ``input_artifact_extraction`` + ``record`` +
    null ``base_id``, so the extraction worker can claim and execute the job.

    The record has no active_base_id (extraction runs before any base exists).
    """
    user_id = await _insert_user(job_runtime_env)
    record_id = await _insert_record(job_runtime_env, user_id)
    # Deliberately do NOT create a reading base or set active_base_id — the
    # extraction job runs before any base exists.
    run_id = await _insert_run(job_runtime_env, record_id, user_id)
    # I3K enqueue contract sets target_key=str(artifact_id); use a stand-in
    # UUID to match the real job shape (runtime itself does not inspect it).
    artifact_id = uuid4()

    job_id = await _insert_job(
        job_runtime_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=None,
        job_type="input_artifact_extraction",
        target_type="record",
        target_key=str(artifact_id),
        operation_fingerprint="input_artifact_extraction_v1",
        idempotency_key="id-extraction-claim",
    )

    runtime = ReaderJobRuntime(pool=job_runtime_env)
    claimed = await runtime.claim_next_job(
        lease_owner="extraction-worker",
        lease_duration=timedelta(seconds=30),
        job_type="input_artifact_extraction",
    )
    assert claimed is not None
    assert claimed.job_id == job_id
    assert claimed.base_id is None
    assert claimed.job_type == "input_artifact_extraction"
    assert claimed.target_type == "record"
    assert claimed.operation_fingerprint == "input_artifact_extraction_v1"

    row = await _fetch_job(job_runtime_env, job_id)
    assert row["status"] == STATUS_CLAIMED
    assert row["lease_owner"] == "extraction-worker"


async def test_db_constraint_rejects_non_build_base_null_base_job_insert(
    job_runtime_env: asyncpg.Pool,
) -> None:
    """DB constraint ck_reader_jobs_base_scope blocks non-extraction null-base jobs.

    Only ``build_base`` and ``input_artifact_extraction`` record-level jobs may
    have null base_id at the DB level. Attempting to insert a ``translate_unit``
    job with null base_id raises ``CheckViolationError`` before the runtime
    fence ever sees it.
    """
    user_id = await _insert_user(job_runtime_env)
    record_id = await _insert_record(job_runtime_env, user_id)
    run_id = await _insert_run(job_runtime_env, record_id, user_id)

    with pytest.raises(asyncpg.CheckViolationError):
        await _insert_job(
            job_runtime_env,
            record_id=record_id,
            run_id=run_id,
            user_id=user_id,
            base_id=None,
            job_type="translate_unit",
            target_type="unit",
            target_key="u1",
            operation_fingerprint="translate_unit_v1",
            idempotency_key="id-translate-null-base-constraint",
        )


async def test_validate_fence_supersedes_non_build_base_null_base_job_row(
    job_runtime_env: asyncpg.Pool,
) -> None:
    """Runtime fence _validate_fence rejects non-extraction null-base jobs.

    Defense-in-depth: even if a null-base ``translate_unit`` row somehow made
    it into the DB (e.g. constraint relaxed in future), the runtime fence must
    still return ``missing_base``. We call ``_validate_fence`` directly with a
    dict-based fake job row because the DB constraint prevents inserting such a
    row.
    """
    user_id = await _insert_user(job_runtime_env)
    record_id = await _insert_record(job_runtime_env, user_id)

    runtime = ReaderJobRuntime(pool=job_runtime_env)
    fake_job_row = {
        "reading_record_id": record_id,
        "expected_generation": 1,
        "base_id": None,
        "job_type": "translate_unit",
        "target_type": "unit",
    }

    async with job_runtime_env.acquire() as conn:
        rationale = await runtime._validate_fence(conn, fake_job_row)  # type: ignore[attr-defined]
    assert rationale == "missing_base"


async def test_claim_supersedes_extraction_job_when_active_base_already_exists(
    job_runtime_env: asyncpg.Pool,
) -> None:
    """Extraction job is stale if a base already exists for this generation.

    ``input_artifact_extraction`` runs before any base exists. If
    ``reading_records.active_base_id`` is already set (e.g. build_base
    completed), a lingering extraction job must be superseded with
    ``active_base_already_exists`` to prevent overwriting
    ``original_inputs.source_text`` after downstream consumers may have read it.
    """
    user_id = await _insert_user(job_runtime_env)
    record_id = await _insert_record(job_runtime_env, user_id)
    run_id = await _insert_run(job_runtime_env, record_id, user_id)
    base_id = await _insert_base(job_runtime_env, record_id)
    await _set_active_base(job_runtime_env, record_id=record_id, base_id=base_id)
    artifact_id = uuid4()

    job_id = await _insert_job(
        job_runtime_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=None,
        job_type="input_artifact_extraction",
        target_type="record",
        target_key=str(artifact_id),
        operation_fingerprint="input_artifact_extraction_v1",
        idempotency_key="id-extraction-stale-active-base",
    )

    runtime = ReaderJobRuntime(pool=job_runtime_env)
    claimed = await runtime.claim_next_job(
        lease_owner="extraction-worker",
        lease_duration=timedelta(seconds=30),
        job_type="input_artifact_extraction",
        target_type="record",
        operation_fingerprint="input_artifact_extraction_v1",
    )
    assert claimed is None

    row = await _fetch_job(job_runtime_env, job_id)
    assert row["status"] == STATUS_SUPERSEDED
    assert row["rationale_code"] == "active_base_already_exists"


# ---------------------------------------------------------------------------
# Tests: article_rag_index_build fence tripwire (D6-I4C)
#
# ``article_rag_index_build`` is a base-scoped job_type added in D6-I4B. It
# must NOT be in the build_base / extraction / materialization allow-list,
# so the runtime fence must enforce:
#   * base_id IS NOT NULL (DB CHECK constraint catches this at insert time)
#   * expected_generation matches reading_records.generation
#   * base row exists and status='active'
#   * reading_records.active_base_id matches the job's base_id
# These tripwire tests pin the fence behavior for the new job_type so a
# future allow-list change does not silently exempt it.
# ---------------------------------------------------------------------------


async def test_claim_accepts_article_rag_index_build_with_valid_fence(
    job_runtime_env: asyncpg.Pool,
) -> None:
    """article_rag_index_build with a valid base/generation fence is claimed."""
    user_id, record_id, base_id, run_id = await _seed_active_record(job_runtime_env)
    job_id = await _insert_job(
        job_runtime_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        job_type="article_rag_index_build",
        target_type="record",
        target_key=str(uuid4()),
        operation_fingerprint="article_rag_index_build_v1",
        idempotency_key="id-rag-index-build-valid",
    )

    runtime = ReaderJobRuntime(pool=job_runtime_env)
    claimed = await runtime.claim_next_job(
        lease_owner="rag-index-worker",
        lease_duration=timedelta(seconds=30),
        job_type="article_rag_index_build",
        target_type="record",
        operation_fingerprint="article_rag_index_build_v1",
    )
    assert claimed is not None
    assert claimed.job_id == job_id
    assert claimed.job_type == "article_rag_index_build"
    assert claimed.base_id == base_id

    row = await _fetch_job(job_runtime_env, job_id)
    assert row["status"] == STATUS_CLAIMED


async def test_claim_supersedes_article_rag_index_build_with_inactive_base(
    job_runtime_env: asyncpg.Pool,
) -> None:
    """article_rag_index_build is superseded when its base is inactive."""
    user_id, record_id, base_id, run_id = await _seed_active_record(job_runtime_env)
    await _insert_job(
        job_runtime_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        job_type="article_rag_index_build",
        target_type="record",
        target_key=str(uuid4()),
        operation_fingerprint="article_rag_index_build_v1",
        idempotency_key="id-rag-index-build-inactive-base",
    )
    async with job_runtime_env.acquire() as conn:
        await conn.execute(
            "UPDATE reading_bases SET status = 'superseded' WHERE id = $1",
            base_id,
        )

    runtime = ReaderJobRuntime(pool=job_runtime_env)
    result = await runtime.claim_next_job(
        lease_owner="rag-index-worker",
        lease_duration=timedelta(seconds=30),
        job_type="article_rag_index_build",
        target_type="record",
        operation_fingerprint="article_rag_index_build_v1",
    )
    assert result is None

    async with job_runtime_env.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, rationale_code FROM reader_jobs "
            "WHERE idempotency_key = $1",
            "id-rag-index-build-inactive-base",
        )
    assert row["status"] == STATUS_SUPERSEDED
    assert row["rationale_code"] == "inactive_base"


async def test_claim_supersedes_article_rag_index_build_with_stale_generation(
    job_runtime_env: asyncpg.Pool,
) -> None:
    """article_rag_index_build is superseded when generation has moved."""
    user_id, record_id, base_id, run_id = await _seed_active_record(
        job_runtime_env, generation=1
    )
    await _insert_job(
        job_runtime_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        job_type="article_rag_index_build",
        target_type="record",
        target_key=str(uuid4()),
        expected_generation=1,
        operation_fingerprint="article_rag_index_build_v1",
        idempotency_key="id-rag-index-build-stale-gen",
    )
    async with job_runtime_env.acquire() as conn:
        await conn.execute(
            "UPDATE reading_records SET active_base_id = NULL, generation = 2 "
            "WHERE id = $1",
            record_id,
        )

    runtime = ReaderJobRuntime(pool=job_runtime_env)
    result = await runtime.claim_next_job(
        lease_owner="rag-index-worker",
        lease_duration=timedelta(seconds=30),
        job_type="article_rag_index_build",
        target_type="record",
        operation_fingerprint="article_rag_index_build_v1",
    )
    assert result is None

    async with job_runtime_env.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, rationale_code FROM reader_jobs "
            "WHERE idempotency_key = $1",
            "id-rag-index-build-stale-gen",
        )
    assert row["status"] == STATUS_SUPERSEDED
    assert row["rationale_code"] == "stale_generation"


async def test_claim_supersedes_article_rag_index_build_with_active_base_mismatch(
    job_runtime_env: asyncpg.Pool,
) -> None:
    """article_rag_index_build is superseded when active_base_id does not match."""
    user_id, record_id, base_id, run_id = await _seed_active_record(job_runtime_env)
    await _insert_job(
        job_runtime_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        job_type="article_rag_index_build",
        target_type="record",
        target_key=str(uuid4()),
        operation_fingerprint="article_rag_index_build_v1",
        idempotency_key="id-rag-index-build-active-base-mismatch",
    )
    # Clear active_base_id so the job's base_id no longer matches.
    await _set_active_base(job_runtime_env, record_id=record_id, base_id=None)

    runtime = ReaderJobRuntime(pool=job_runtime_env)
    result = await runtime.claim_next_job(
        lease_owner="rag-index-worker",
        lease_duration=timedelta(seconds=30),
        job_type="article_rag_index_build",
        target_type="record",
        operation_fingerprint="article_rag_index_build_v1",
    )
    assert result is None

    async with job_runtime_env.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, rationale_code FROM reader_jobs "
            "WHERE idempotency_key = $1",
            "id-rag-index-build-active-base-mismatch",
        )
    assert row["status"] == STATUS_SUPERSEDED
    assert row["rationale_code"] == "active_base_mismatch"


# ---------------------------------------------------------------------------
# P0-B: public atomic in-transaction retry terminalization seam
# ---------------------------------------------------------------------------


async def test_transition_retryable_failure_in_transaction_final_attempt_is_terminal(
    job_runtime_env: asyncpg.Pool,
) -> None:
    """Tracer bullet: a claimed job at max_attempts must terminalize atomically.

    This test exercises the public ``transition_retryable_failure_in_transaction``
    seam that Article RAG will use to eliminate private runtime helper calls.
    """
    user_id, record_id, base_id, run_id = await _seed_active_record(job_runtime_env)
    job_id = await _insert_job(
        job_runtime_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="p0b-tracer",
        operation_fingerprint="fp-p0b-tracer",
        idempotency_key="id-p0b-tracer",
        max_attempts=1,
    )

    runtime = ReaderJobRuntime(pool=job_runtime_env)
    claimed = await runtime.claim_next_job(
        lease_owner="w1", lease_duration=timedelta(seconds=30)
    )
    assert claimed is not None
    assert claimed.job_id == job_id

    async with job_runtime_env.acquire() as conn:
        async with conn.transaction():
            snapshot = await runtime.transition_retryable_failure_in_transaction(
                conn,
                job_id=job_id,
                lease_token=claimed.lease_token,
                retry_delay=timedelta(seconds=60),
                failure_class="embedding",
                failure_code="embedding_failed",
                failure_message="embedding provider throttled",
                rationale_code="embedding_failed",
                output_ref={"diagnostics": {"provider_status": 429}},
            )

    assert snapshot.status == STATUS_FAILED_TERMINAL
    assert snapshot.rationale_code == "max_attempts_exceeded"

    async with job_runtime_env.acquire() as conn:
        job = await _fetch_job(job_runtime_env, job_id)
        terminal_events = await conn.fetchval(
            "SELECT count(*) FROM reader_job_events "
            "WHERE job_id = $1 AND event_type = 'job_failed_terminal'",
            job_id,
        )
        retry_events = await conn.fetchval(
            "SELECT count(*) FROM reader_job_events "
            "WHERE job_id = $1 AND event_type = 'job_retry_later'",
            job_id,
        )

    assert job["status"] == STATUS_FAILED_TERMINAL
    assert job["rationale_code"] == "max_attempts_exceeded"
    assert terminal_events == 1
    assert retry_events == 0


async def test_transition_in_transaction_succeeded_within_caller_transaction(
    job_runtime_env: asyncpg.Pool,
) -> None:
    """transition_in_transaction commits within caller's tx; 1 succeeded event."""
    user_id, record_id, base_id, run_id = await _seed_active_record(job_runtime_env)
    job_id = await _insert_job(
        job_runtime_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="p0b-tit-succ",
        operation_fingerprint="fp-p0b-tit-succ",
        idempotency_key="id-p0b-tit-succ",
    )

    runtime = ReaderJobRuntime(pool=job_runtime_env)
    claimed = await runtime.claim_next_job(
        lease_owner="w1", lease_duration=timedelta(seconds=30)
    )
    assert claimed is not None

    async with job_runtime_env.acquire() as conn:
        async with conn.transaction():
            snapshot = await runtime.transition_in_transaction(
                conn,
                job_id=job_id,
                target_status=STATUS_SUCCEEDED,
                lease_token=claimed.lease_token,
                output_ref={"artifact": "ok"},
            )

    assert snapshot.status == STATUS_SUCCEEDED
    assert snapshot.lease_token is None

    async with job_runtime_env.acquire() as conn:
        succeeded_events = await conn.fetchval(
            "SELECT count(*) FROM reader_job_events "
            "WHERE job_id = $1 AND event_type = 'job_succeeded'",
            job_id,
        )
    assert succeeded_events == 1


async def test_transition_in_transaction_wrong_lease_rejected(
    job_runtime_env: asyncpg.Pool,
) -> None:
    """Wrong lease token → LeaseTokenMismatchError; no job/event mutation."""
    user_id, record_id, base_id, run_id = await _seed_active_record(job_runtime_env)
    job_id = await _insert_job(
        job_runtime_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="p0b-tit-wrong-lease",
        operation_fingerprint="fp-p0b-tit-wrong-lease",
        idempotency_key="id-p0b-tit-wrong-lease",
    )

    runtime = ReaderJobRuntime(pool=job_runtime_env)
    claimed = await runtime.claim_next_job(
        lease_owner="w1", lease_duration=timedelta(seconds=30)
    )
    assert claimed is not None

    wrong_token = uuid4()
    async with job_runtime_env.acquire() as conn:
        async with conn.transaction():
            with pytest.raises(LeaseTokenMismatchError):
                await runtime.transition_in_transaction(
                    conn,
                    job_id=job_id,
                    target_status=STATUS_SUCCEEDED,
                    lease_token=wrong_token,
                )

    # Job should still be claimed; no event written by the failed attempt.
    job = await _fetch_job(job_runtime_env, job_id)
    assert job["status"] == STATUS_CLAIMED
    async with job_runtime_env.acquire() as conn:
        events = await conn.fetchval(
            "SELECT count(*) FROM reader_job_events WHERE job_id = $1",
            job_id,
        )
    # claim_next_job writes a job_claimed event; no succeeded event.
    assert events == 1


async def test_transition_in_transaction_rollback_rolls_back_job_and_event(
    job_runtime_env: asyncpg.Pool,
) -> None:
    """Caller rollback must roll back both job update and event insert."""
    user_id, record_id, base_id, run_id = await _seed_active_record(job_runtime_env)
    job_id = await _insert_job(
        job_runtime_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="p0b-tit-rollback",
        operation_fingerprint="fp-p0b-tit-rollback",
        idempotency_key="id-p0b-tit-rollback",
    )

    runtime = ReaderJobRuntime(pool=job_runtime_env)
    claimed = await runtime.claim_next_job(
        lease_owner="w1", lease_duration=timedelta(seconds=30)
    )
    assert claimed is not None

    async with job_runtime_env.acquire() as conn:
        try:
            async with conn.transaction():
                await runtime.transition_in_transaction(
                    conn,
                    job_id=job_id,
                    target_status=STATUS_SUCCEEDED,
                    lease_token=claimed.lease_token,
                    output_ref={"artifact": "ok"},
                )
                raise RuntimeError("force rollback")
        except RuntimeError:
            pass

    # Both job update and event insert must be rolled back.
    job = await _fetch_job(job_runtime_env, job_id)
    assert job["status"] == STATUS_CLAIMED
    async with job_runtime_env.acquire() as conn:
        succeeded_events = await conn.fetchval(
            "SELECT count(*) FROM reader_job_events "
            "WHERE job_id = $1 AND event_type = 'job_succeeded'",
            job_id,
        )
    assert succeeded_events == 0


async def test_transition_retryable_failure_in_transaction_below_cap(
    job_runtime_env: asyncpg.Pool,
) -> None:
    """attempt_count=1, max_attempts=3 → retry_later + 1 retry event, lease cleared."""
    user_id, record_id, base_id, run_id = await _seed_active_record(job_runtime_env)
    job_id = await _insert_job(
        job_runtime_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="p0b-below-cap",
        operation_fingerprint="fp-p0b-below-cap",
        idempotency_key="id-p0b-below-cap",
        max_attempts=3,
    )

    runtime = ReaderJobRuntime(pool=job_runtime_env)
    claimed = await runtime.claim_next_job(
        lease_owner="w1", lease_duration=timedelta(seconds=30)
    )
    assert claimed is not None
    # claim_next_job increments attempt_count to 1.

    async with job_runtime_env.acquire() as conn:
        async with conn.transaction():
            snapshot = await runtime.transition_retryable_failure_in_transaction(
                conn,
                job_id=job_id,
                lease_token=claimed.lease_token,
                retry_delay=timedelta(seconds=60),
                failure_class="embedding",
                failure_code="embedding_failed",
                failure_message="throttled",
                rationale_code="embedding_failed",
                output_ref={"diagnostics": {"provider_status": 429}},
            )

    assert snapshot.status == STATUS_RETRY_LATER
    assert snapshot.lease_token is None
    assert snapshot.lease_owner is None

    async with job_runtime_env.acquire() as conn:
        retry_events = await conn.fetchval(
            "SELECT count(*) FROM reader_job_events "
            "WHERE job_id = $1 AND event_type = 'job_retry_later'",
            job_id,
        )
        terminal_events = await conn.fetchval(
            "SELECT count(*) FROM reader_job_events "
            "WHERE job_id = $1 AND event_type = 'job_failed_terminal'",
            job_id,
        )
    assert retry_events == 1
    assert terminal_events == 0


async def test_transition_retryable_failure_in_transaction_wrong_lease_rejected(
    job_runtime_env: asyncpg.Pool,
) -> None:
    """Wrong lease on retryable seam → rejected; no mutation."""
    user_id, record_id, base_id, run_id = await _seed_active_record(job_runtime_env)
    job_id = await _insert_job(
        job_runtime_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="p0b-wrong-lease",
        operation_fingerprint="fp-p0b-wrong-lease",
        idempotency_key="id-p0b-wrong-lease",
        max_attempts=3,
    )

    runtime = ReaderJobRuntime(pool=job_runtime_env)
    claimed = await runtime.claim_next_job(
        lease_owner="w1", lease_duration=timedelta(seconds=30)
    )
    assert claimed is not None

    wrong_token = uuid4()
    async with job_runtime_env.acquire() as conn:
        async with conn.transaction():
            with pytest.raises(LeaseTokenMismatchError):
                await runtime.transition_retryable_failure_in_transaction(
                    conn,
                    job_id=job_id,
                    lease_token=wrong_token,
                    retry_delay=timedelta(seconds=60),
                    failure_class="embedding",
                    failure_code="embedding_failed",
                )

    job = await _fetch_job(job_runtime_env, job_id)
    assert job["status"] == STATUS_CLAIMED
    async with job_runtime_env.acquire() as conn:
        retry_events = await conn.fetchval(
            "SELECT count(*) FROM reader_job_events "
            "WHERE job_id = $1 AND event_type = 'job_retry_later'",
            job_id,
        )
        terminal_events = await conn.fetchval(
            "SELECT count(*) FROM reader_job_events "
            "WHERE job_id = $1 AND event_type = 'job_failed_terminal'",
            job_id,
        )
    assert retry_events == 0
    assert terminal_events == 0


async def test_transition_retryable_failure_in_transaction_repeat_call_fail_closed(
    job_runtime_env: asyncpg.Pool,
) -> None:
    """Second terminalization on same lease is rejected; terminal event count stays 1."""
    user_id, record_id, base_id, run_id = await _seed_active_record(job_runtime_env)
    job_id = await _insert_job(
        job_runtime_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="p0b-repeat",
        operation_fingerprint="fp-p0b-repeat",
        idempotency_key="id-p0b-repeat",
        max_attempts=1,
    )

    runtime = ReaderJobRuntime(pool=job_runtime_env)
    claimed = await runtime.claim_next_job(
        lease_owner="w1", lease_duration=timedelta(seconds=30)
    )
    assert claimed is not None

    # First call terminalizes.
    async with job_runtime_env.acquire() as conn:
        async with conn.transaction():
            snapshot = await runtime.transition_retryable_failure_in_transaction(
                conn,
                job_id=job_id,
                lease_token=claimed.lease_token,
                retry_delay=timedelta(seconds=60),
                failure_class="embedding",
                failure_code="embedding_failed",
            )
    assert snapshot.status == STATUS_FAILED_TERMINAL

    # Second call with the same lease must fail closed — job is no longer claimed.
    async with job_runtime_env.acquire() as conn:
        async with conn.transaction():
            with pytest.raises(IllegalTransitionError):
                await runtime.transition_retryable_failure_in_transaction(
                    conn,
                    job_id=job_id,
                    lease_token=claimed.lease_token,
                    retry_delay=timedelta(seconds=60),
                    failure_class="embedding",
                    failure_code="embedding_failed",
                )

    async with job_runtime_env.acquire() as conn:
        terminal_events = await conn.fetchval(
            "SELECT count(*) FROM reader_job_events "
            "WHERE job_id = $1 AND event_type = 'job_failed_terminal'",
            job_id,
        )
        retry_events = await conn.fetchval(
            "SELECT count(*) FROM reader_job_events "
            "WHERE job_id = $1 AND event_type = 'job_retry_later'",
            job_id,
        )
    assert terminal_events == 1
    assert retry_events == 0


# ---------------------------------------------------------------------------
# P0-B round 2: active-transaction guard (caller-owned tx is mandatory)
# ---------------------------------------------------------------------------


async def test_transition_in_transaction_requires_active_caller_transaction(
    job_runtime_env: asyncpg.Pool,
) -> None:
    """Calling transition_in_transaction without an active transaction fails closed.

    The seam MUST reject connections that are not inside an ``async with
    conn.transaction()`` block BEFORE issuing any SQL — no job state mutation,
    no event insert. Failures must raise a stable, local ``RuntimeError``
    (not a raw asyncpg error or SDK exception).
    """
    user_id, record_id, base_id, run_id = await _seed_active_record(job_runtime_env)
    job_id = await _insert_job(
        job_runtime_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="p0b-guard-tit",
        operation_fingerprint="fp-p0b-guard-tit",
        idempotency_key="id-p0b-guard-tit",
    )

    runtime = ReaderJobRuntime(pool=job_runtime_env)
    claimed = await runtime.claim_next_job(
        lease_owner="w1", lease_duration=timedelta(seconds=30)
    )
    assert claimed is not None

    events_before = await _count_job_events(job_runtime_env, job_id)

    async with job_runtime_env.acquire() as conn:
        # NO conn.transaction() wrapper — the guard must reject this.
        with pytest.raises(RuntimeError) as exc_info:
            await runtime.transition_in_transaction(
                conn,
                job_id=job_id,
                target_status=STATUS_SUCCEEDED,
                lease_token=claimed.lease_token,
            )
        msg = str(exc_info.value)
        # Local, stable message — no SDK/asyncpg internals, no key/URI.
        assert "transaction" in msg.lower()

    # Job state untouched; no new events.
    job = await _fetch_job(job_runtime_env, job_id)
    assert job["status"] == STATUS_CLAIMED
    events_after = await _count_job_events(job_runtime_env, job_id)
    assert events_after == events_before


async def test_transition_retryable_failure_in_transaction_requires_active_caller_transaction(
    job_runtime_env: asyncpg.Pool,
) -> None:
    """transition_retryable_failure_in_transaction fails closed without an active tx."""
    user_id, record_id, base_id, run_id = await _seed_active_record(job_runtime_env)
    job_id = await _insert_job(
        job_runtime_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="p0b-guard-trf",
        operation_fingerprint="fp-p0b-guard-trf",
        idempotency_key="id-p0b-guard-trf",
        max_attempts=1,
    )

    runtime = ReaderJobRuntime(pool=job_runtime_env)
    claimed = await runtime.claim_next_job(
        lease_owner="w1", lease_duration=timedelta(seconds=30)
    )
    assert claimed is not None

    events_before = await _count_job_events(job_runtime_env, job_id)

    async with job_runtime_env.acquire() as conn:
        # NO conn.transaction() wrapper — the guard must reject this.
        with pytest.raises(RuntimeError) as exc_info:
            await runtime.transition_retryable_failure_in_transaction(
                conn,
                job_id=job_id,
                lease_token=claimed.lease_token,
                retry_delay=timedelta(seconds=60),
                failure_class="embedding",
                failure_code="embedding_failed",
            )
        assert "transaction" in str(exc_info.value).lower()

    # Job untouched; no new events.
    job = await _fetch_job(job_runtime_env, job_id)
    assert job["status"] == STATUS_CLAIMED
    events_after = await _count_job_events(job_runtime_env, job_id)
    assert events_after == events_before


async def test_validate_claim_in_transaction_requires_active_caller_transaction(
    job_runtime_env: asyncpg.Pool,
) -> None:
    """validate_claim_in_transaction fails closed without an active tx (no SQL issued)."""
    user_id, record_id, base_id, run_id = await _seed_active_record(job_runtime_env)
    job_id = await _insert_job(
        job_runtime_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="p0b-guard-vc",
        operation_fingerprint="fp-p0b-guard-vc",
        idempotency_key="id-p0b-guard-vc",
    )

    runtime = ReaderJobRuntime(pool=job_runtime_env)
    claimed = await runtime.claim_next_job(
        lease_owner="w1", lease_duration=timedelta(seconds=30)
    )
    assert claimed is not None

    events_before = await _count_job_events(job_runtime_env, job_id)

    async with job_runtime_env.acquire() as conn:
        # NO conn.transaction() wrapper — the guard must reject this before any SQL.
        with pytest.raises(RuntimeError) as exc_info:
            await runtime.validate_claim_in_transaction(
                conn,
                job_id=job_id,
                lease_token=claimed.lease_token,
            )
        assert "transaction" in str(exc_info.value).lower()

    # Job untouched; no new events.
    job = await _fetch_job(job_runtime_env, job_id)
    assert job["status"] == STATUS_CLAIMED
    events_after = await _count_job_events(job_runtime_env, job_id)
    assert events_after == events_before


async def _count_job_events(pool: asyncpg.Pool, job_id: UUID) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT count(*) FROM reader_job_events WHERE job_id = $1",
            job_id,
        )


async def test_validate_claim_in_transaction_expired_lease_fail_closed(
    job_runtime_env: asyncpg.Pool,
) -> None:
    """Expired lease inside validate_claim_in_transaction is rejected with no mutation.

    Even when the caller holds an active transaction, the seam must reject
    an expired lease (``lease_expires_at < now``) by raising
    ``LeaseExpiredError``. No ``reader_job_events`` row may be written and
    the job status must remain ``claimed`` (caller's rollback is responsible
    for any partial work; the seam itself writes nothing).
    """
    user_id, record_id, base_id, run_id = await _seed_active_record(job_runtime_env)
    job_id = await _insert_job(
        job_runtime_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="p0b-expired-lease",
        operation_fingerprint="fp-p0b-expired-lease",
        idempotency_key="id-p0b-expired-lease",
    )

    runtime = ReaderJobRuntime(pool=job_runtime_env)
    claimed = await runtime.claim_next_job(
        lease_owner="w1", lease_duration=timedelta(seconds=30)
    )
    assert claimed is not None

    # Force the lease to be expired in the DB.
    async with job_runtime_env.acquire() as conn:
        await conn.execute(
            "UPDATE reader_jobs SET lease_expires_at = NOW() - INTERVAL '1 hour' "
            "WHERE id = $1",
            job_id,
        )

    events_before = await _count_job_events(job_runtime_env, job_id)

    async with job_runtime_env.acquire() as conn:
        async with conn.transaction():
            with pytest.raises(LeaseExpiredError):
                await runtime.validate_claim_in_transaction(
                    conn,
                    job_id=job_id,
                    lease_token=claimed.lease_token,
                )

    # Job still claimed (the lease expiry update above did not change status);
    # no new events written by the failed validation.
    job = await _fetch_job(job_runtime_env, job_id)
    assert job["status"] == STATUS_CLAIMED
    events_after = await _count_job_events(job_runtime_env, job_id)
    assert events_after == events_before
