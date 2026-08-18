"""Bounded automatic recovery scan (RA-REC-05 R1).

Regression suite for ``AutomaticRecoveryService``: strict provider_timeout
fail-closed candidate selection, 30-minute cooldown, per record+generation
attempt cap counted from committed recovery events, bounded oldest-first
batches, and delegation to the audited same-generation recovery core with
``trigger='automatic'``. All tests run offline against a throwaway local
schema: no LLM, no network, no worker loop.
"""

from __future__ import annotations

import asyncio
import copy
import logging
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.database import connection as db_connection
from app.services.reader_orchestration.analysis_section_jobs import (
    ANALYSIS_SECTION_REQUEST_ORIGIN,
)
from app.services.reader_orchestration.automatic_recovery import (
    AutomaticRecoveryService,
)
from app.services.reader_orchestration.job_bootstrap import (
    TRANSLATION_BATCH_JOB_TYPE,
    VOCABULARY_BATCH_JOB_TYPE,
    EnhancementJobBootstrapService,
)
from tests.reader_orchestration_test_support import (
    BASELINE_SQL,
    connect_admin,
    insert_user,
    make_pool,
    submit_article_ready,
)
from tests.test_reader_failed_terminal_recovery import (
    _GROUPED_TEXT,
    _bootstrap,
    _count_table,
    _insert_synthetic_recovery_event,
    _job_row,
    _load_jobs,
    _load_record,
    _recovery_events,
)


@pytest.fixture
async def scanner_env() -> asyncpg.Pool:
    schema_name = f"test_reader_automatic_recovery_{uuid4().hex}"
    admin = await connect_admin()
    await admin.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
    await admin.execute(f'CREATE SCHEMA "{schema_name}"')
    await admin.execute(f'SET search_path TO "{schema_name}", public')
    await admin.execute(BASELINE_SQL)
    await admin.close()

    pool = await make_pool(schema_name)
    previous_pool = db_connection.DB_POOL
    db_connection.DB_POOL = pool
    try:
        yield pool
    finally:
        db_connection.DB_POOL = previous_pool
        await pool.close()
        cleanup = await connect_admin()
        await cleanup.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await cleanup.close()


async def _fail_one_job(
    pool: asyncpg.Pool,
    record_id: UUID,
    *,
    job_type: str,
    failure_class: str,
    failure_code: str,
    backdate_minutes: int = 120,
    origin: str | None = None,
) -> UUID:
    async with pool.acquire() as conn:
        # ``trg_reader_jobs_set_updated_at`` force-refreshes updated_at on
        # every UPDATE; disable it inside this throwaway schema so the
        # cooldown fixture can backdate the failure timestamp.
        await conn.execute(
            "ALTER TABLE reader_jobs "
            "DISABLE TRIGGER trg_reader_jobs_set_updated_at"
        )
        try:
            job_id = await conn.fetchval(
                """
                UPDATE reader_jobs
                SET status = 'failed_terminal',
                    failure_class = $3,
                    failure_code = $4,
                    attempt_count = max_attempts,
                    updated_at = NOW() - ($5::int * INTERVAL '1 minute')
                WHERE id = (
                    SELECT id FROM reader_jobs
                    WHERE reading_record_id = $1
                      AND job_type = $2
                      AND status = 'queued'
                      AND ($6::text IS NULL
                           OR COALESCE(input_json->>'request_origin', '') = $6)
                    ORDER BY created_at ASC, id ASC
                    LIMIT 1
                )
                RETURNING id
                """,
                record_id,
                job_type,
                failure_class,
                failure_code,
                backdate_minutes,
                origin,
            )
        finally:
            await conn.execute(
                "ALTER TABLE reader_jobs "
                "ENABLE TRIGGER trg_reader_jobs_set_updated_at"
            )
    assert job_id is not None
    return UUID(str(job_id))


async def _mark_record_failed(pool: asyncpg.Pool, record_id: UUID) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE reading_records SET product_state = 'failed' WHERE id = $1",
            record_id,
        )


async def _setup_incident(
    pool: asyncpg.Pool,
    user_id: UUID,
    *,
    backdate_minutes: int = 120,
) -> UUID:
    """Article-ready record with one ordinary provider_timeout failure."""
    result = await submit_article_ready(pool, user_id=user_id)
    record_id = result.record_id
    await _bootstrap(pool, record_id=record_id, user_id=user_id)
    await _fail_one_job(
        pool,
        record_id,
        job_type=TRANSLATION_BATCH_JOB_TYPE,
        failure_class="provider",
        failure_code="provider_timeout",
        backdate_minutes=backdate_minutes,
    )
    await _mark_record_failed(pool, record_id)
    return record_id


# ---------------------------------------------------------------------------
# 1. Provider-timeout past cooldown recovers with trigger=automatic
# ---------------------------------------------------------------------------


async def test_automatic_recovery_after_cooldown(scanner_env: asyncpg.Pool) -> None:
    pool = scanner_env
    user_id = await insert_user(pool)
    record_id = await _setup_incident(pool, user_id, backdate_minutes=120)
    predecessor = next(
        job
        for job in await _load_jobs(pool, record_id)
        if str(job["job_type"]) == TRANSLATION_BATCH_JOB_TYPE
    )

    summary = await AutomaticRecoveryService(pool=pool).run_once(batch_size=8)

    assert summary.recovered_count == 1
    assert summary.noop_count == 0
    assert summary.skipped_count == 0
    result = summary.results[0]
    assert result.record_id == record_id
    assert result.status == "recovered"
    assert result.successor_job_count == 1

    # State restored; predecessor stays an immutable audit row.
    record = await _load_record(pool, record_id)
    assert str(record["product_state"]) == "readable_enhancing"
    predecessor_after = await _job_row(pool, UUID(str(predecessor["id"])))
    assert str(predecessor_after["status"]) == "failed_terminal"
    translation_jobs = [
        job
        for job in await _load_jobs(pool, record_id)
        if str(job["job_type"]) == TRANSLATION_BATCH_JOB_TYPE
    ]
    assert sorted(str(job["status"]) for job in translation_jobs) == [
        "failed_terminal",
        "queued",
    ]

    # Exactly one recovery event, written with the automatic trigger.
    events = await _recovery_events(pool, record_id)
    assert len(events) == 1
    assert events[0]["trigger"] == "automatic"
    assert events[0]["recovery_mode"] == "same_generation_successor_jobs"
    assert events[0]["billing_mode"] == "internal_only"
    assert events[0]["generation"] == int(record["generation"])


# ---------------------------------------------------------------------------
# 2. Cooldown blocks recovery with zero writes
# ---------------------------------------------------------------------------


async def test_within_cooldown_no_recovery_zero_writes(
    scanner_env: asyncpg.Pool,
) -> None:
    pool = scanner_env
    user_id = await insert_user(pool)
    record_id = await _setup_incident(pool, user_id, backdate_minutes=0)
    jobs_before = await _load_jobs(pool, record_id)

    service = AutomaticRecoveryService(pool=pool)
    assert await service.scan_candidates(batch_size=8) == ()
    summary = await service.run_once(batch_size=8)

    assert summary.results == ()
    assert summary.recovered_count == 0
    assert len(await _load_jobs(pool, record_id)) == len(jobs_before)
    assert await _recovery_events(pool, record_id) == []
    assert str((await _load_record(pool, record_id))["product_state"]) == "failed"


# ---------------------------------------------------------------------------
# 3. Attempt cap: two automatic same-generation events block the scan
# ---------------------------------------------------------------------------


async def test_attempt_cap_blocks_after_two_automatic_events(
    scanner_env: asyncpg.Pool,
) -> None:
    pool = scanner_env
    user_id = await insert_user(pool)
    record_id = await _setup_incident(pool, user_id)
    jobs_before = await _load_jobs(pool, record_id)
    for _ in range(2):
        await _insert_synthetic_recovery_event(
            pool, record_id, trigger="automatic", generation=1
        )

    summary = await AutomaticRecoveryService(pool=pool).run_once(batch_size=8)

    assert summary.results == ()
    assert len(await _load_jobs(pool, record_id)) == len(jobs_before)
    # Only the two seeded cap events exist; the scan added no recovery
    # event of its own.
    events = await _recovery_events(pool, record_id)
    assert len(events) == 2
    assert str((await _load_record(pool, record_id))["product_state"]) == "failed"


async def test_manual_and_other_generation_events_do_not_consume_cap(
    scanner_env: asyncpg.Pool,
) -> None:
    pool = scanner_env
    user_id = await insert_user(pool)
    record_id = await _setup_incident(pool, user_id)
    await _insert_synthetic_recovery_event(
        pool, record_id, trigger="manual", generation=1
    )
    for _ in range(2):
        await _insert_synthetic_recovery_event(
            pool, record_id, trigger="automatic", generation=2
        )

    summary = await AutomaticRecoveryService(pool=pool).run_once(batch_size=8)

    assert summary.recovered_count == 1
    assert summary.results[0].record_id == record_id


# ---------------------------------------------------------------------------
# 4. Fail-closed on non-provider failures (alone or mixed)
# ---------------------------------------------------------------------------


async def test_validation_only_failure_fail_closed(
    scanner_env: asyncpg.Pool,
) -> None:
    pool = scanner_env
    user_id = await insert_user(pool)
    result = await submit_article_ready(pool, user_id=user_id)
    record_id = result.record_id
    await _bootstrap(pool, record_id=record_id, user_id=user_id)
    await _fail_one_job(
        pool,
        record_id,
        job_type=TRANSLATION_BATCH_JOB_TYPE,
        failure_class="validation",
        failure_code="invalid_output",
    )
    await _mark_record_failed(pool, record_id)
    jobs_before = await _load_jobs(pool, record_id)

    summary = await AutomaticRecoveryService(pool=pool).run_once(batch_size=8)

    assert summary.results == ()
    assert len(await _load_jobs(pool, record_id)) == len(jobs_before)
    assert await _recovery_events(pool, record_id) == []
    assert str((await _load_record(pool, record_id))["product_state"]) == "failed"


async def test_mixed_provider_and_validation_fail_closed(
    scanner_env: asyncpg.Pool,
) -> None:
    pool = scanner_env
    user_id = await insert_user(pool)
    result = await submit_article_ready(pool, user_id=user_id)
    record_id = result.record_id
    await _bootstrap(pool, record_id=record_id, user_id=user_id)
    await _fail_one_job(
        pool,
        record_id,
        job_type=TRANSLATION_BATCH_JOB_TYPE,
        failure_class="provider",
        failure_code="provider_timeout",
    )
    await _fail_one_job(
        pool,
        record_id,
        job_type=VOCABULARY_BATCH_JOB_TYPE,
        failure_class="worker_exception",
        failure_code="crash",
    )
    await _mark_record_failed(pool, record_id)
    jobs_before = await _load_jobs(pool, record_id)

    summary = await AutomaticRecoveryService(pool=pool).run_once(batch_size=8)

    # The whole record is skipped even though one failure is eligible.
    assert summary.results == ()
    assert len(await _load_jobs(pool, record_id)) == len(jobs_before)
    assert await _recovery_events(pool, record_id) == []
    assert str((await _load_record(pool, record_id))["product_state"]) == "failed"


# ---------------------------------------------------------------------------
# 5. Analysis-section lane failures never become candidates
# ---------------------------------------------------------------------------


async def test_section_only_failure_not_candidate(
    scanner_env: asyncpg.Pool,
) -> None:
    pool = scanner_env
    user_id = await insert_user(pool)
    result = await submit_article_ready(
        pool,
        user_id=user_id,
        plain_text=_GROUPED_TEXT,
        title="Automatic Section Boundary",
        language="en",
    )
    record_id = result.record_id
    await _bootstrap(pool, record_id=record_id, user_id=user_id)
    await _fail_one_job(
        pool,
        record_id,
        job_type="build_vocabulary_layer_article",
        failure_class="provider",
        failure_code="provider_timeout",
        origin=ANALYSIS_SECTION_REQUEST_ORIGIN,
    )
    await _mark_record_failed(pool, record_id)
    jobs_before = await _load_jobs(pool, record_id)

    summary = await AutomaticRecoveryService(pool=pool).run_once(batch_size=8)

    assert summary.results == ()
    assert len(await _load_jobs(pool, record_id)) == len(jobs_before)
    assert await _recovery_events(pool, record_id) == []
    assert str((await _load_record(pool, record_id))["product_state"]) == "failed"


# ---------------------------------------------------------------------------
# 6. Fence mismatches stay out of the scan
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mutation", ["readiness", "lifecycle", "base", "generation"]
)
async def test_fence_mismatch_not_candidate(
    scanner_env: asyncpg.Pool, mutation: str
) -> None:
    pool = scanner_env
    user_id = await insert_user(pool)
    record_id = await _setup_incident(pool, user_id)
    async with pool.acquire() as conn:
        if mutation == "readiness":
            await conn.execute(
                "UPDATE reading_records SET readiness_state = "
                "'candidate_base_ready' WHERE id = $1",
                record_id,
            )
        elif mutation == "lifecycle":
            await conn.execute(
                "UPDATE reading_records SET lifecycle_status = 'cancelled' "
                "WHERE id = $1",
                record_id,
            )
        elif mutation == "base":
            await conn.execute(
                "UPDATE reading_records SET active_base_id = NULL WHERE id = $1",
                record_id,
            )
        else:
            # ``fk_reading_records_active_base`` normally makes a persisted
            # record/base generation mismatch impossible; drop it inside
            # this throwaway schema to simulate legacy/corrupt data.
            await conn.execute(
                "ALTER TABLE reading_records "
                "DROP CONSTRAINT fk_reading_records_active_base"
            )
            await conn.execute(
                "UPDATE reading_records SET generation = generation + 1 "
                "WHERE id = $1",
                record_id,
            )

    service = AutomaticRecoveryService(pool=pool)
    assert await service.scan_candidates(batch_size=8) == ()
    summary = await service.run_once(batch_size=8)
    assert summary.results == ()
    assert str((await _load_record(pool, record_id))["product_state"]) == "failed"


# ---------------------------------------------------------------------------
# 7. Concurrency: dual run_once collapses to one successor group
# ---------------------------------------------------------------------------


async def test_concurrent_run_once_single_recovery(
    scanner_env: asyncpg.Pool,
) -> None:
    pool = scanner_env
    user_id = await insert_user(pool)
    record_id = await _setup_incident(pool, user_id)

    summaries = await asyncio.gather(
        AutomaticRecoveryService(pool=pool).run_once(batch_size=4),
        AutomaticRecoveryService(pool=pool).run_once(batch_size=4),
    )

    statuses = sorted(result.status for s in summaries for result in s.results)
    # Winner recovers; loser serializes on the core's record FOR UPDATE
    # lock, sees the immutable failed predecessor plus the winner's
    # committed event and queued successor, and creates nothing (noop).
    assert statuses == ["noop", "recovered"]
    total_successors = sum(
        result.successor_job_count for s in summaries for result in s.results
    )
    assert total_successors == 1
    # Exactly one translation successor row and one recovery event.
    translation_jobs = [
        job
        for job in await _load_jobs(pool, record_id)
        if str(job["job_type"]) == TRANSLATION_BATCH_JOB_TYPE
    ]
    assert sorted(str(job["status"]) for job in translation_jobs) == [
        "failed_terminal",
        "queued",
    ]
    assert len(await _recovery_events(pool, record_id)) == 1
    assert str((await _load_record(pool, record_id))["product_state"]) == (
        "readable_enhancing"
    )


# ---------------------------------------------------------------------------
# 8. One bad candidate never blocks the rest; logs stay sanitized
# ---------------------------------------------------------------------------


async def test_unexpected_exception_is_error_isolated_and_log_sanitized(
    scanner_env: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    pool = scanner_env
    user_id = await insert_user(pool)
    victim_id = await _setup_incident(pool, user_id, backdate_minutes=240)
    healthy_id = await _setup_incident(pool, user_id, backdate_minutes=120)

    real_recover = EnhancementJobBootstrapService.recover_failed_enhancement_jobs

    async def flaky(self, *, record_id, user_id, trigger, trace_id=None):
        if record_id == victim_id:
            raise RuntimeError("probe-secret-7f3a SELECT internal diagnostics")
        return await real_recover(
            self,
            record_id=record_id,
            user_id=user_id,
            trigger=trigger,
            trace_id=trace_id,
        )

    monkeypatch.setattr(
        EnhancementJobBootstrapService,
        "recover_failed_enhancement_jobs",
        flaky,
    )

    with caplog.at_level(logging.INFO):
        summary = await AutomaticRecoveryService(pool=pool).run_once(batch_size=8)

    by_record = {result.record_id: result for result in summary.results}
    # Unexpected back-end fault: error status, counted for alerting —
    # NOT disguised as an ordinary skip.
    assert by_record[victim_id].status == "error"
    assert by_record[healthy_id].status == "recovered"
    assert summary.error_count == 1
    assert summary.skipped_count == 0
    assert summary.recovered_count == 1
    # Victim untouched; the healthy candidate recovered normally.
    assert str((await _load_record(pool, victim_id))["product_state"]) == "failed"
    assert await _recovery_events(pool, victim_id) == []
    assert str((await _load_record(pool, healthy_id))["product_state"]) == (
        "readable_enhancing"
    )
    # Stable error event name + record id only: no exception body in logs.
    assert "reader_automatic_recovery_error" in caplog.text
    assert "probe-secret-7f3a" not in caplog.text
    assert "internal diagnostics" not in caplog.text


async def test_expected_fence_drift_is_skipped_not_error(
    scanner_env: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    pool = scanner_env
    user_id = await insert_user(pool)
    record_id = await _setup_incident(pool, user_id)
    assert record_id is not None  # the incident itself is the candidate

    async def drifted(self, *, record_id, user_id, trigger, trace_id=None):
        raise ValueError("generation fence drifted under the scanner")

    monkeypatch.setattr(
        EnhancementJobBootstrapService,
        "recover_failed_enhancement_jobs",
        drifted,
    )

    with caplog.at_level(logging.INFO):
        summary = await AutomaticRecoveryService(pool=pool).run_once(batch_size=8)

    # Expected fail-closed drift: skipped, never counted as an error.
    assert summary.skipped_count == 1
    assert summary.error_count == 0
    assert summary.results[0].status == "skipped"
    assert "reader_automatic_recovery_skipped" in caplog.text
    assert "reader_automatic_recovery_error" not in caplog.text
    assert "generation fence drifted" not in caplog.text


async def test_stale_candidate_capped_between_scan_and_recover(
    scanner_env: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TOCTOU regression: a candidate that hits the attempt cap after the
    pre-filter scan is rejected by the core's in-transaction gate —
    never passed through to successor creation."""
    pool = scanner_env
    user_id = await insert_user(pool)
    record_id = await _setup_incident(pool, user_id)

    stale_candidates = copy.deepcopy(
        await AutomaticRecoveryService(pool=pool).scan_candidates(batch_size=8)
    )
    assert len(stale_candidates) == 1
    # Simulate the scanner/worker race: two automatic recoveries already
    # committed between the batch scan and this candidate's lock.
    for _ in range(2):
        await _insert_synthetic_recovery_event(
            pool, record_id, trigger="automatic", generation=1
        )

    async def stale_scan(self, batch_size: int):
        return stale_candidates

    monkeypatch.setattr(AutomaticRecoveryService, "scan_candidates", stale_scan)

    summary = await AutomaticRecoveryService(pool=pool).run_once(batch_size=8)

    assert summary.results[0].status == "skipped"
    assert summary.recovered_count == 0
    assert summary.error_count == 0
    # No third recovery: no successor, no event beyond the seeded cap
    # events, product_state untouched.
    translation_jobs = [
        job
        for job in await _load_jobs(pool, record_id)
        if str(job["job_type"]) == TRANSLATION_BATCH_JOB_TYPE
    ]
    assert [str(job["status"]) for job in translation_jobs] == ["failed_terminal"]
    assert len(await _recovery_events(pool, record_id)) == 2
    assert str((await _load_record(pool, record_id))["product_state"]) == "failed"


# ---------------------------------------------------------------------------
# 9. Billing tables stay untouched; batch bound + oldest-first ordering
# ---------------------------------------------------------------------------


async def test_automatic_recovery_does_not_bill(
    scanner_env: asyncpg.Pool,
) -> None:
    pool = scanner_env
    user_id = await insert_user(pool)
    record_id = await _setup_incident(pool, user_id)

    service = AutomaticRecoveryService(pool=pool)
    await service.scan_candidates(batch_size=8)
    await service.run_once(batch_size=8)
    # Second pass: already restored, must stay a zero-write no-op.
    await service.run_once(batch_size=8)

    assert await _count_table(pool, "ai_usage_events") == 0
    assert await _count_table(pool, "user_credit_ledger") == 0
    assert await _count_table(pool, "ai_model_execution_journal") == 0
    assert len(await _recovery_events(pool, record_id)) == 1


async def test_scan_bounds_batch_and_orders_oldest_first(
    scanner_env: asyncpg.Pool,
) -> None:
    pool = scanner_env
    user_id = await insert_user(pool)
    older_id = await _setup_incident(pool, user_id, backdate_minutes=240)
    newer_id = await _setup_incident(pool, user_id, backdate_minutes=120)

    service = AutomaticRecoveryService(pool=pool)
    single = await service.scan_candidates(batch_size=1)
    assert [candidate.record_id for candidate in single] == [older_id]
    both = await service.scan_candidates(batch_size=8)
    assert [candidate.record_id for candidate in both] == [older_id, newer_id]


async def test_batch_size_rejected_before_database_access(
    scanner_env: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No injected pool and no global pool: any database access would
    # raise RuntimeError, so ValueError proves validation runs first.
    monkeypatch.setattr(db_connection, "DB_POOL", None)
    service = AutomaticRecoveryService()
    with pytest.raises(ValueError, match="batch_size"):
        await service.scan_candidates(0)
    with pytest.raises(ValueError, match="batch_size"):
        await service.run_once(-3)
