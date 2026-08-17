"""Same-generation successor-job recovery for failed_terminal enhancement work.

Regression suite for ``EnhancementJobBootstrapService.recover_failed_enhancement_jobs``
(RA-REC-02). Locked contracts:

- ``failed_terminal`` predecessor jobs are immutable audit records: recovery
  never reopens, resets, or deletes them; successors are new runs/jobs with
  distinct ids reusing the same fingerprint/idempotency machinery.
- Only the explicit recovery entry may bootstrap a ``product_state='failed'``
  record; ordinary ``bootstrap_missing_jobs`` keeps rejecting it.
- Recovery restores ``failed`` -> ``readable_enhancing`` and writes one
  ``record_state_changed`` event with the
  ``reader_parse_recovery_requested_v1`` payload schema only when it actually
  creates successor work; repeated calls are deterministic no-ops.
- Generation fence / missing active base fail closed with zero writes.
- Recovery never bills: no ``ai_usage_events`` / ``user_credit_ledger`` rows
  and no provider attempts (``ai_model_execution_journal`` stays empty).

All tests run offline against a throwaway local schema: no LLM, no network,
no worker loop.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.database import connection as db_connection
from app.services.reader_orchestration.analysis_section_jobs import (
    ANALYSIS_SECTION_REQUEST_ORIGIN,
    USER_EXPLICIT_ANALYSIS_SECTION_ORIGIN,
)
from app.services.reader_orchestration.analysis_section_request_service import (
    _load_planned_sections,
)
from app.services.reader_orchestration.grammar_window_bootstrap import (
    GRAMMAR_WINDOW_JOB_TYPE,
    GrammarWindowBootstrapService,
)
from app.services.reader_orchestration.job_bootstrap import (
    GRAMMAR_BATCH_JOB_TYPE,
    RECOVERY_EVENT_SCHEMA,
    TRANSLATION_BATCH_JOB_TYPE,
    VOCABULARY_BATCH_JOB_TYPE,
    EnhancementJobBootstrapService,
    _load_locked_active_base_state,
)
from tests.reader_orchestration_test_support import (
    BASELINE_SQL,
    connect_admin,
    insert_user,
    make_pool,
    submit_article_ready,
)

GRAMMAR_WINDOW_RUN_TYPE = "grammar_bundle_window"

# >2000 words forces the GROUPED_WINDOWED route (structured tier caps at
# STRUCTURED_ARTICLE_MAX_WORD_COUNT=2000), so bootstrap takes the
# grammar-window path and creates analysis_windows + window jobs.
_GROUPED_TEXT = "\n\n".join(
    [
        " ".join(
            f"Word{i} placeholder sentence for grammar window recovery."
            for i in range(50)
        )
        for _ in range(50)
    ]
)
assert len(_GROUPED_TEXT.split()) > 2000


@pytest.fixture
async def recovery_env() -> asyncpg.Pool:
    schema_name = f"test_reader_failed_terminal_recovery_{uuid4().hex}"
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


async def _load_record(pool: asyncpg.Pool, record_id: UUID) -> asyncpg.Record:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM reading_records WHERE id = $1", record_id
        )
    assert row is not None
    return row


async def _load_jobs(pool: asyncpg.Pool, record_id: UUID) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        return list(
            await conn.fetch(
                """
                SELECT * FROM reader_jobs
                WHERE reading_record_id = $1
                ORDER BY created_at ASC, id ASC
                """,
                record_id,
            )
        )


async def _recovery_events(pool: asyncpg.Pool, record_id: UUID) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT event_type, payload_json
            FROM reader_events
            WHERE reading_record_id = $1
              AND event_type = 'record_state_changed'
              AND payload_json->>'event_schema' = $2
            ORDER BY sequence ASC
            """,
            record_id,
            RECOVERY_EVENT_SCHEMA,
        )
    return [dict(row["payload_json"]) for row in rows]


async def _count_table(pool: asyncpg.Pool, table: str) -> int:
    async with pool.acquire() as conn:
        count = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")  # noqa: S608
    assert isinstance(count, int)
    return count


async def _bootstrap(pool: asyncpg.Pool, *, record_id: UUID, user_id: UUID):
    service = EnhancementJobBootstrapService(pool=pool)
    return await service.bootstrap_missing_jobs(record_id=record_id, user_id=user_id)


async def _recover(
    pool: asyncpg.Pool,
    *,
    record_id: UUID,
    user_id: UUID,
    trigger: str = "manual",
    trace_id: UUID | None = None,
):
    service = EnhancementJobBootstrapService(pool=pool)
    return await service.recover_failed_enhancement_jobs(
        record_id=record_id,
        user_id=user_id,
        trigger=trigger,
        trace_id=trace_id,
    )


async def _setup_incident_record(pool: asyncpg.Pool, user_id: UUID) -> UUID:
    """Article-ready record whose translation batch job died terminally.

    Mirrors the production accident path: bootstrap created the jobs, the
    translation batch job exhausted attempts and went ``failed_terminal``,
    and the worker finalizer marked the record ``product_state='failed'``.
    """
    result = await submit_article_ready(pool, user_id=user_id)
    record_id = result.record_id
    summary = await _bootstrap(pool, record_id=record_id, user_id=user_id)
    assert summary.job_counts.translation == 1
    async with pool.acquire() as conn:
        failed_job_id = await conn.fetchval(
            """
            UPDATE reader_jobs
            SET status = 'failed_terminal',
                failure_class = 'provider',
                failure_code = 'provider_timeout',
                failure_message = 'simulated terminal translation failure',
                attempt_count = max_attempts
            WHERE reading_record_id = $1
              AND job_type = $2
              AND status = 'queued'
            RETURNING id
            """,
            record_id,
            TRANSLATION_BATCH_JOB_TYPE,
        )
        assert failed_job_id is not None
        await conn.execute(
            "UPDATE reading_records SET product_state = 'failed' WHERE id = $1",
            record_id,
        )
    return record_id


async def _publish_vocabulary_fixture(pool: asyncpg.Pool, record_id: UUID) -> None:
    """Mark the vocabulary batch job succeeded with published per-unit layers."""
    async with pool.acquire() as conn:
        record = await conn.fetchrow(
            "SELECT active_base_id, generation FROM reading_records WHERE id = $1",
            record_id,
        )
        assert record is not None
        base_id = record["active_base_id"]
        generation = int(record["generation"])
        unit_ids = await conn.fetch(
            """
            SELECT unit_id FROM reading_units
            WHERE reading_record_id = $1 AND base_id = $2
            ORDER BY order_index ASC
            """,
            record_id,
            base_id,
        )
        assert unit_ids
        for row in unit_ids:
            await conn.execute(
                """
                INSERT INTO enhancement_layers (
                    reading_record_id, base_id, layer_type, target_scope,
                    target_key, generation, status, operation_fingerprint,
                    schema_version
                )
                VALUES ($1, $2, 'vocabulary', 'unit', $3, $4, 'published',
                        'test_fixture_vocabulary_v1', 1)
                """,
                record_id,
                base_id,
                str(row["unit_id"]),
                generation,
            )
        updated = await conn.execute(
            """
            UPDATE reader_jobs
            SET status = 'succeeded'
            WHERE reading_record_id = $1
              AND job_type = $2
              AND status = 'queued'
            """,
            record_id,
            VOCABULARY_BATCH_JOB_TYPE,
        )
        assert updated == "UPDATE 1"


async def _job_row(pool: asyncpg.Pool, job_id: UUID) -> asyncpg.Record:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM reader_jobs WHERE id = $1", job_id)
    assert row is not None
    return row


# ---------------------------------------------------------------------------
# 1. Exact accident path
# ---------------------------------------------------------------------------


async def test_recovery_exact_accident_path_creates_successor(
    recovery_env: asyncpg.Pool,
) -> None:
    pool = recovery_env
    user_id = await insert_user(pool)
    record_id = await _setup_incident_record(pool, user_id)

    record_before = await _load_record(pool, record_id)
    assert str(record_before["product_state"]) == "failed"
    assert str(record_before["readiness_state"]) == "article_ready"

    predecessor = next(
        job
        for job in await _load_jobs(pool, record_id)
        if str(job["job_type"]) == TRANSLATION_BATCH_JOB_TYPE
    )
    predecessor_id = UUID(str(predecessor["id"]))
    jobs_before = len(await _load_jobs(pool, record_id))

    summary = await _recover(pool, record_id=record_id, user_id=user_id)

    assert summary.recovered is True
    assert summary.event_written is True
    assert summary.previous_product_state == "failed"
    assert summary.next_product_state == "readable_enhancing"
    assert predecessor_id in summary.predecessor_job_ids

    # Predecessor stays an immutable audit record.
    predecessor_after = await _job_row(pool, predecessor_id)
    assert str(predecessor_after["status"]) == "failed_terminal"
    assert str(predecessor_after["failure_code"]) == "provider_timeout"
    assert str(predecessor_after["failure_class"]) == "provider"
    assert int(predecessor_after["attempt_count"]) == int(
        predecessor_after["max_attempts"]
    )

    # Successor: new ids, queued, same fence + fingerprint, fresh attempts.
    assert len(summary.successor_job_ids) == 1
    successor_id = summary.successor_job_ids[0]
    assert successor_id != predecessor_id
    assert summary.successor_run_ids[0] != predecessor["run_id"]
    successor = await _job_row(pool, successor_id)
    assert str(successor["status"]) == "queued"
    assert str(successor["job_type"]) == TRANSLATION_BATCH_JOB_TYPE
    assert int(successor["attempt_count"]) == 0
    assert str(successor["operation_fingerprint"]) == str(
        predecessor["operation_fingerprint"]
    )
    assert int(successor["expected_generation"]) == int(
        predecessor["expected_generation"]
    )
    assert UUID(str(successor["base_id"])) == UUID(str(predecessor["base_id"]))

    # Record restored; readiness / active base / generation untouched.
    record_after = await _load_record(pool, record_id)
    assert str(record_after["product_state"]) == "readable_enhancing"
    assert str(record_after["readiness_state"]) == "article_ready"
    assert record_after["active_base_id"] == record_before["active_base_id"]
    assert int(record_after["generation"]) == int(record_before["generation"])
    # Display title + grammar queued jobs from the first bootstrap survive.
    assert len(await _load_jobs(pool, record_id)) == jobs_before + 1


# ---------------------------------------------------------------------------
# 2. Succeeded work is preserved, never duplicated
# ---------------------------------------------------------------------------


async def test_recovery_preserves_succeeded_work(
    recovery_env: asyncpg.Pool,
) -> None:
    pool = recovery_env
    user_id = await insert_user(pool)
    result = await submit_article_ready(pool, user_id=user_id)
    record_id = result.record_id
    await _bootstrap(pool, record_id=record_id, user_id=user_id)
    await _publish_vocabulary_fixture(pool, record_id)

    async with pool.acquire() as conn:
        failed_job_id = await conn.fetchval(
            """
            UPDATE reader_jobs
            SET status = 'failed_terminal',
                failure_class = 'provider',
                failure_code = 'provider_error',
                attempt_count = max_attempts
            WHERE reading_record_id = $1
              AND job_type = $2
              AND status = 'queued'
            RETURNING id
            """,
            record_id,
            TRANSLATION_BATCH_JOB_TYPE,
        )
        assert failed_job_id is not None
        await conn.execute(
            "UPDATE reading_records SET product_state = 'failed' WHERE id = $1",
            record_id,
        )

    vocabulary_job = next(
        job
        for job in await _load_jobs(pool, record_id)
        if str(job["job_type"]) == VOCABULARY_BATCH_JOB_TYPE
    )
    grammar_jobs_before = [
        job
        for job in await _load_jobs(pool, record_id)
        if str(job["job_type"]) == GRAMMAR_BATCH_JOB_TYPE
    ]
    layers_before = await _count_table(pool, "enhancement_layers")

    summary = await _recover(pool, record_id=record_id, user_id=user_id)

    assert summary.recovered is True
    assert TRANSLATION_BATCH_JOB_TYPE in summary.successor_job_types
    assert VOCABULARY_BATCH_JOB_TYPE not in summary.successor_job_types

    jobs_after = await _load_jobs(pool, record_id)
    # Succeeded vocabulary job untouched; no duplicate vocabulary job.
    vocabulary_jobs = [
        job
        for job in jobs_after
        if str(job["job_type"]) == VOCABULARY_BATCH_JOB_TYPE
    ]
    assert len(vocabulary_jobs) == 1
    assert UUID(str(vocabulary_jobs[0]["id"])) == UUID(str(vocabulary_job["id"]))
    assert str(vocabulary_jobs[0]["status"]) == "succeeded"
    # Published vocabulary layers intact.
    assert await _count_table(pool, "enhancement_layers") == layers_before
    # Queued grammar job not duplicated.
    grammar_jobs_after = [
        job for job in jobs_after if str(job["job_type"]) == GRAMMAR_BATCH_JOB_TYPE
    ]
    assert [UUID(str(job["id"])) for job in grammar_jobs_after] == [
        UUID(str(job["id"])) for job in grammar_jobs_before
    ]
    # Only the failed translation capability got a successor.
    translation_jobs = [
        job
        for job in jobs_after
        if str(job["job_type"]) == TRANSLATION_BATCH_JOB_TYPE
    ]
    statuses = sorted(str(job["status"]) for job in translation_jobs)
    assert statuses == ["failed_terminal", "queued"]


# ---------------------------------------------------------------------------
# 3. Idempotency
# ---------------------------------------------------------------------------


async def test_recovery_is_idempotent(recovery_env: asyncpg.Pool) -> None:
    pool = recovery_env
    user_id = await insert_user(pool)
    record_id = await _setup_incident_record(pool, user_id)

    first = await _recover(pool, record_id=record_id, user_id=user_id)
    assert first.recovered is True
    jobs_after_first = await _load_jobs(pool, record_id)
    successor_id = first.successor_job_ids[0]
    successor_after_first = await _job_row(pool, successor_id)

    second = await _recover(pool, record_id=record_id, user_id=user_id)

    assert second.recovered is False
    assert second.event_written is False
    assert second.next_product_state == "readable_enhancing"
    # No additional active jobs, no successor reset, no duplicate event.
    assert len(await _load_jobs(pool, record_id)) == len(jobs_after_first)
    successor_after_second = await _job_row(pool, successor_id)
    assert int(successor_after_second["attempt_count"]) == int(
        successor_after_first["attempt_count"]
    )
    assert str(successor_after_second["status"]) == "queued"
    events = await _recovery_events(pool, record_id)
    assert len(events) == 1


# ---------------------------------------------------------------------------
# 4. Generation fence fails closed
# ---------------------------------------------------------------------------


async def test_recovery_generation_mismatch_fails_closed(
    recovery_env: asyncpg.Pool,
) -> None:
    pool = recovery_env
    user_id = await insert_user(pool)
    record_id = await _setup_incident_record(pool, user_id)
    jobs_before = await _load_jobs(pool, record_id)

    # ``fk_reading_records_active_base`` normally makes a persisted
    # record/base generation mismatch impossible, so drop it inside this
    # throwaway schema to simulate legacy/corrupt data. (Holding the bump
    # in an open transaction instead would deadlock against recovery's
    # FOR UPDATE lock on the same row.)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            ALTER TABLE reading_records
            DROP CONSTRAINT fk_reading_records_active_base
            """
        )
        await conn.execute(
            "UPDATE reading_records SET generation = generation + 1 WHERE id = $1",
            record_id,
        )

    with pytest.raises(ValueError, match="generation"):
        await _recover(pool, record_id=record_id, user_id=user_id)

    # Zero jobs, zero events, zero product_state modification.
    assert len(await _load_jobs(pool, record_id)) == len(jobs_before)
    assert await _recovery_events(pool, record_id) == []
    record = await _load_record(pool, record_id)
    assert str(record["product_state"]) == "failed"


# ---------------------------------------------------------------------------
# 5. Missing active base is a deterministic rejection
# ---------------------------------------------------------------------------


async def test_recovery_without_active_base_rejected(
    recovery_env: asyncpg.Pool,
) -> None:
    pool = recovery_env
    user_id = await insert_user(pool)
    record_id = await _setup_incident_record(pool, user_id)
    jobs_before = await _load_jobs(pool, record_id)

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE reading_records SET active_base_id = NULL WHERE id = $1",
            record_id,
        )
        bases_before = await conn.fetchval(
            "SELECT COUNT(*) FROM reading_bases WHERE reading_record_id = $1",
            record_id,
        )

    with pytest.raises(ValueError, match="active base"):
        await _recover(pool, record_id=record_id, user_id=user_id)

    # No silent generation rebuild, no jobs, no events, state untouched.
    async with pool.acquire() as conn:
        bases_after = await conn.fetchval(
            "SELECT COUNT(*) FROM reading_bases WHERE reading_record_id = $1",
            record_id,
        )
    assert bases_after == bases_before
    assert len(await _load_jobs(pool, record_id)) == len(jobs_before)
    assert await _recovery_events(pool, record_id) == []
    record = await _load_record(pool, record_id)
    assert str(record["product_state"]) == "failed"
    assert int(record["generation"]) == 1


# ---------------------------------------------------------------------------
# 6. Ordinary bootstrap keeps rejecting failed records
# ---------------------------------------------------------------------------


async def test_ordinary_bootstrap_still_rejects_failed(
    recovery_env: asyncpg.Pool,
) -> None:
    pool = recovery_env
    user_id = await insert_user(pool)
    record_id = await _setup_incident_record(pool, user_id)

    with pytest.raises(ValueError, match="not ready for enhancement bootstrap"):
        await _bootstrap(pool, record_id=record_id, user_id=user_id)

    # Only the explicit recovery entry may proceed.
    summary = await _recover(pool, record_id=record_id, user_id=user_id)
    assert summary.recovered is True
    record = await _load_record(pool, record_id)
    assert str(record["product_state"]) == "readable_enhancing"


# ---------------------------------------------------------------------------
# 7. Recovery event contract
# ---------------------------------------------------------------------------


async def test_recovery_event_contract(recovery_env: asyncpg.Pool) -> None:
    pool = recovery_env
    user_id = await insert_user(pool)
    record_id = await _setup_incident_record(pool, user_id)
    trace_id = uuid4()

    summary = await _recover(
        pool, record_id=record_id, user_id=user_id, trace_id=trace_id
    )
    record = await _load_record(pool, record_id)

    events = await _recovery_events(pool, record_id)
    assert len(events) == 1
    payload = events[0]
    assert payload["event_schema"] == RECOVERY_EVENT_SCHEMA
    assert payload["trigger"] == "manual"
    assert payload["recovery_mode"] == "same_generation_successor_jobs"
    assert payload["record_id"] == str(record_id)
    assert payload["base_id"] == str(record["active_base_id"])
    assert payload["generation"] == int(record["generation"])
    assert payload["trace_id"] == str(trace_id)
    assert payload["previous_product_state"] == "failed"
    assert payload["next_product_state"] == "readable_enhancing"
    assert payload["billing_mode"] == "internal_only"
    assert payload["predecessor_job_ids"] == [
        str(job_id) for job_id in summary.predecessor_job_ids
    ]
    assert payload["successor_job_ids"] == [
        str(job_id) for job_id in summary.successor_job_ids
    ]
    assert payload["successor_run_ids"] == [
        str(run_id) for run_id in summary.successor_run_ids
    ]
    assert payload["successor_job_types"] == [TRANSLATION_BATCH_JOB_TYPE]
    assert set(payload["predecessor_job_ids"]).isdisjoint(
        set(payload["successor_job_ids"])
    )

    # event_type itself is the closed-set record_state_changed.
    async with pool.acquire() as conn:
        event_types = await conn.fetch(
            """
            SELECT DISTINCT event_type FROM reader_events
            WHERE reading_record_id = $1
              AND payload_json->>'event_schema' = $2
            """,
            record_id,
            RECOVERY_EVENT_SCHEMA,
        )
    assert [row["event_type"] for row in event_types] == ["record_state_changed"]

    # Invalid trigger fails closed before any write.
    other_record_id = await _setup_incident_record(pool, user_id)
    jobs_before = await _load_jobs(pool, other_record_id)
    with pytest.raises(ValueError, match="trigger"):
        await _recover(
            pool, record_id=other_record_id, user_id=user_id, trigger="retry_now"
        )
    assert len(await _load_jobs(pool, other_record_id)) == len(jobs_before)
    assert await _recovery_events(pool, other_record_id) == []
    assert str(
        (await _load_record(pool, other_record_id))["product_state"]
    ) == "failed"


# ---------------------------------------------------------------------------
# 8. No duplicate billing, no provider attempts
# ---------------------------------------------------------------------------


async def test_recovery_does_not_bill(recovery_env: asyncpg.Pool) -> None:
    pool = recovery_env
    user_id = await insert_user(pool)
    record_id = await _setup_incident_record(pool, user_id)

    await _recover(pool, record_id=record_id, user_id=user_id)
    await _recover(pool, record_id=record_id, user_id=user_id)

    # Recovery writes no usage / credit rows and performs zero provider
    # attempts: bootstrap only inserts reader_runs / reader_jobs rows, and
    # no worker/LLM call happens in-process.
    assert await _count_table(pool, "ai_usage_events") == 0
    assert await _count_table(pool, "user_credit_ledger") == 0
    assert await _count_table(pool, "ai_model_execution_journal") == 0


# ---------------------------------------------------------------------------
# 9. No state flip without recoverable work
# ---------------------------------------------------------------------------


async def test_recovery_noop_without_recoverable_work(
    recovery_env: asyncpg.Pool,
) -> None:
    pool = recovery_env
    user_id = await insert_user(pool)
    result = await submit_article_ready(pool, user_id=user_id)
    record_id = result.record_id
    await _bootstrap(pool, record_id=record_id, user_id=user_id)
    jobs_before = await _load_jobs(pool, record_id)

    # Manually failed record but every enhancement job is still active:
    # nothing to recover -> deterministic no-op, no unfounded state flip.
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE reading_records SET product_state = 'failed' WHERE id = $1",
            record_id,
        )

    summary = await _recover(pool, record_id=record_id, user_id=user_id)

    assert summary.recovered is False
    assert summary.event_written is False
    assert summary.next_product_state == "failed"
    assert len(await _load_jobs(pool, record_id)) == len(jobs_before)
    assert await _recovery_events(pool, record_id) == []
    assert str((await _load_record(pool, record_id))["product_state"]) == "failed"


# ---------------------------------------------------------------------------
# 10. Grammar-window lane: failed_terminal window jobs get real successors
# ---------------------------------------------------------------------------


async def test_recovery_creates_grammar_window_successor(
    recovery_env: asyncpg.Pool,
) -> None:
    pool = recovery_env
    user_id = await insert_user(pool)
    result = await submit_article_ready(
        pool,
        user_id=user_id,
        plain_text=_GROUPED_TEXT,
        title="Grammar Window Recovery",
        language="en",
    )
    record_id = result.record_id
    record = await _load_record(pool, record_id)
    await _bootstrap(pool, record_id=record_id, user_id=user_id)
    # Legacy window plan: current main routes GROUPED grammar to the
    # first-section compact batch, so create the window plan explicitly
    # (the path real legacy records with analysis_windows take).
    await GrammarWindowBootstrapService(pool=pool).bootstrap_grammar_window_plan(
        record_id=record_id,
        base_id=record["active_base_id"],
    )

    async with pool.acquire() as conn:
        window_jobs = await conn.fetch(
            """
            SELECT id, operation_fingerprint
            FROM reader_jobs
            WHERE reading_record_id = $1 AND job_type = $2
            ORDER BY created_at ASC, id ASC
            """,
            record_id,
            GRAMMAR_WINDOW_JOB_TYPE,
        )
        assert window_jobs, "window plan bootstrap must create window jobs"
        victim = window_jobs[0]
        victim_id = UUID(str(victim["id"]))
        await conn.execute(
            """
            UPDATE reader_jobs
            SET status = 'failed_terminal',
                failure_class = 'provider',
                failure_code = 'provider_timeout',
                failure_message = 'simulated grammar window failure',
                attempt_count = max_attempts
            WHERE id = $1
            """,
            victim_id,
        )
        # Mirror the pipeline-runner failure path: window row -> failed.
        await conn.execute(
            """
            UPDATE analysis_windows
            SET status = 'failed', completed_at = NOW()
            WHERE job_id = $1
            """,
            victim_id,
        )
        await conn.execute(
            "UPDATE reading_records SET product_state = 'failed' WHERE id = $1",
            record_id,
        )
        jobs_before = await conn.fetchval(
            "SELECT COUNT(*) FROM reader_jobs WHERE reading_record_id = $1",
            record_id,
        )

    summary = await _recover(pool, record_id=record_id, user_id=user_id)

    # Window successors are created inside the recovery transaction and
    # drive recovered / state flip / event.
    assert summary.recovered is True
    assert summary.event_written is True
    assert summary.previous_product_state == "failed"
    assert summary.next_product_state == "readable_enhancing"
    assert victim_id in summary.predecessor_job_ids
    assert len(summary.grammar_window_successor_job_ids) == 1
    successor_id = summary.grammar_window_successor_job_ids[0]
    assert successor_id != victim_id
    assert successor_id in summary.successor_job_ids
    assert GRAMMAR_WINDOW_JOB_TYPE in summary.successor_job_types

    successor = await _job_row(pool, successor_id)
    assert str(successor["status"]) == "queued"
    assert str(successor["job_type"]) == GRAMMAR_WINDOW_JOB_TYPE
    assert int(successor["attempt_count"]) == 0
    assert str(successor["operation_fingerprint"]) == str(
        victim["operation_fingerprint"]
    )

    # Predecessor window job stays an immutable audit record.
    victim_after = await _job_row(pool, victim_id)
    assert str(victim_after["status"]) == "failed_terminal"
    assert str(victim_after["failure_code"]) == "provider_timeout"

    # The failed window row is reset to pending and points at the successor.
    async with pool.acquire() as conn:
        window_row = await conn.fetchrow(
            "SELECT status, job_id FROM analysis_windows WHERE job_id = $1",
            successor_id,
        )
        jobs_after = await conn.fetchval(
            "SELECT COUNT(*) FROM reader_jobs WHERE reading_record_id = $1",
            record_id,
        )
    assert window_row is not None
    assert str(window_row["status"]) == "pending"
    assert jobs_after == jobs_before + 1
    record_after = await _load_record(pool, record_id)
    assert str(record_after["product_state"]) == "readable_enhancing"
    events = await _recovery_events(pool, record_id)
    assert len(events) == 1
    assert str(successor_id) in events[0]["successor_job_ids"]

    # Idempotent second call: no successors left, no flip, no event.
    second = await _recover(pool, record_id=record_id, user_id=user_id)
    assert second.recovered is False
    assert second.event_written is False
    async with pool.acquire() as conn:
        jobs_second = await conn.fetchval(
            "SELECT COUNT(*) FROM reader_jobs WHERE reading_record_id = $1",
            record_id,
        )
    assert jobs_second == jobs_after
    assert len(await _recovery_events(pool, record_id)) == 1


async def test_concurrent_recoveries_serialize_on_record_lock(
    recovery_env: asyncpg.Pool,
) -> None:
    """Two simultaneous recovery calls must not double-flip or double-event.

    Both calls serialize on the ``reading_records`` FOR UPDATE lock taken
    by the recovery loader; the winner creates the successors, the loser
    observes the committed successors and becomes a deterministic no-op.
    """
    pool = recovery_env
    user_id = await insert_user(pool)
    record_id = await _setup_incident_record(pool, user_id)

    summaries = await asyncio.gather(
        _recover(pool, record_id=record_id, user_id=user_id),
        _recover(pool, record_id=record_id, user_id=user_id),
    )

    assert sorted(s.recovered for s in summaries) == [False, True]
    assert sum(1 for s in summaries if s.event_written) == 1
    # Exactly one translation successor exists, not one per caller.
    assert sum(len(s.successor_job_ids) for s in summaries) == 1
    events = await _recovery_events(pool, record_id)
    assert len(events) == 1
    record = await _load_record(pool, record_id)
    assert str(record["product_state"]) == "readable_enhancing"


async def test_window_successor_failure_rolls_back_entire_recovery(
    recovery_env: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure while creating window successors must roll back the whole
    recovery: no product_state flip, no event, no partial successors."""
    pool = recovery_env
    user_id = await insert_user(pool)
    result = await submit_article_ready(
        pool,
        user_id=user_id,
        plain_text=_GROUPED_TEXT,
        title="Grammar Window Rollback",
        language="en",
    )
    record_id = result.record_id
    record = await _load_record(pool, record_id)
    await _bootstrap(pool, record_id=record_id, user_id=user_id)
    await GrammarWindowBootstrapService(pool=pool).bootstrap_grammar_window_plan(
        record_id=record_id,
        base_id=record["active_base_id"],
    )

    async with pool.acquire() as conn:
        victim_id = await conn.fetchval(
            """
            UPDATE reader_jobs
            SET status = 'failed_terminal',
                failure_class = 'provider',
                failure_code = 'provider_timeout',
                attempt_count = max_attempts
            WHERE reading_record_id = $1 AND job_type = $2 AND status = 'queued'
            RETURNING id
            """,
            record_id,
            GRAMMAR_WINDOW_JOB_TYPE,
        )
        assert victim_id is not None
        await conn.execute(
            "UPDATE analysis_windows SET status = 'failed' WHERE job_id = $1",
            victim_id,
        )
        # Also fail the ordinary translation lane so the in-transaction
        # path would both create an ordinary successor AND flip state —
        # proving the rollback undoes everything, not just the window part.
        await conn.execute(
            """
            UPDATE reader_jobs
            SET status = 'failed_terminal',
                failure_class = 'provider',
                failure_code = 'provider_timeout',
                attempt_count = max_attempts
            WHERE reading_record_id = $1
              AND job_type = $2
              AND status = 'queued'
            """,
            record_id,
            TRANSLATION_BATCH_JOB_TYPE,
        )
        await conn.execute(
            "UPDATE reading_records SET product_state = 'failed' WHERE id = $1",
            record_id,
        )
        jobs_before = await conn.fetchval(
            "SELECT COUNT(*) FROM reader_jobs WHERE reading_record_id = $1",
            record_id,
        )

    async def _explode(self, conn, **kwargs):
        raise RuntimeError("simulated window successor creation failure")

    monkeypatch.setattr(
        GrammarWindowBootstrapService, "_create_window_reader_job", _explode
    )

    with pytest.raises(RuntimeError, match="simulated window successor"):
        await _recover(pool, record_id=record_id, user_id=user_id)

    # Whole transaction rolled back: no flip, no event, no successors.
    record_after = await _load_record(pool, record_id)
    assert str(record_after["product_state"]) == "failed"
    assert await _recovery_events(pool, record_id) == []
    async with pool.acquire() as conn:
        jobs_after = await conn.fetchval(
            "SELECT COUNT(*) FROM reader_jobs WHERE reading_record_id = $1",
            record_id,
        )
    assert jobs_after == jobs_before


async def test_recovery_rejects_mislinked_window_job_pointer(
    recovery_env: asyncpg.Pool,
) -> None:
    """``analysis_windows.job_id`` has no FK/unique binding, so recovery
    must verify the job actually targets that window (target_type +
    target_key = window id). Cross-linked pointers fail closed: zero
    successors, zero state changes."""
    pool = recovery_env
    user_id = await insert_user(pool)
    result = await submit_article_ready(
        pool,
        user_id=user_id,
        plain_text=_GROUPED_TEXT,
        title="Window Mislink Fence",
        language="en",
    )
    record_id = result.record_id
    record = await _load_record(pool, record_id)
    await _bootstrap(pool, record_id=record_id, user_id=user_id)
    await GrammarWindowBootstrapService(pool=pool).bootstrap_grammar_window_plan(
        record_id=record_id,
        base_id=record["active_base_id"],
    )

    async with pool.acquire() as conn:
        windows = await conn.fetch(
            """
            SELECT id, job_id FROM analysis_windows
            ORDER BY window_index ASC
            LIMIT 2
            """
        )
        assert len(windows) == 2, "fixture must plan at least two windows"
        w1_id, w2_id = windows[0]["id"], windows[1]["id"]
        j1_id, j2_id = windows[0]["job_id"], windows[1]["job_id"]
        # Fail job 1 (targets window 1), then corrupt BOTH pointers:
        # window 2 -> failed job of window 1, window 1 -> active job of
        # window 2. Neither window legitimately qualifies for recovery.
        await conn.execute(
            """
            UPDATE reader_jobs
            SET status = 'failed_terminal',
                failure_class = 'provider',
                failure_code = 'provider_timeout',
                attempt_count = max_attempts
            WHERE id = $1
            """,
            j1_id,
        )
        await conn.execute(
            "UPDATE analysis_windows SET job_id = $2 WHERE id = $1", w2_id, j1_id
        )
        await conn.execute(
            "UPDATE analysis_windows SET job_id = $2 WHERE id = $1", w1_id, j2_id
        )
        await conn.execute(
            "UPDATE reading_records SET product_state = 'failed' WHERE id = $1",
            record_id,
        )
        jobs_before = await conn.fetchval(
            "SELECT COUNT(*) FROM reader_jobs WHERE reading_record_id = $1",
            record_id,
        )

    summary = await _recover(pool, record_id=record_id, user_id=user_id)

    # Mis-linked failed job is not treated as a recoverable predecessor.
    assert summary.recovered is False
    assert summary.event_written is False
    assert summary.grammar_window_successor_job_ids == ()
    assert str((await _load_record(pool, record_id))["product_state"]) == "failed"
    assert await _recovery_events(pool, record_id) == []
    async with pool.acquire() as conn:
        jobs_after = await conn.fetchval(
            "SELECT COUNT(*) FROM reader_jobs WHERE reading_record_id = $1",
            record_id,
        )
        w2_row = await conn.fetchrow(
            "SELECT status, job_id FROM analysis_windows WHERE id = $1", w2_id
        )
    assert jobs_after == jobs_before
    # Window 2 untouched: no reset, pointer left as (corrupt) evidence.
    assert UUID(str(w2_row["job_id"])) == UUID(str(j1_id))


# ---------------------------------------------------------------------------
# 11. Section lane (real analysis-section helpers) stays out of recovery
# ---------------------------------------------------------------------------


async def _section_job_count(pool: asyncpg.Pool, record_id: UUID) -> int:
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            """
            SELECT COUNT(*) FROM reader_jobs
            WHERE reading_record_id = $1
              AND (input_json->>'request_origin') = ANY($2::text[])
            """,
            record_id,
            [
                ANALYSIS_SECTION_REQUEST_ORIGIN,
                USER_EXPLICIT_ANALYSIS_SECTION_ORIGIN,
            ],
        )
    assert isinstance(count, int)
    return count


async def test_recovery_excludes_first_section_lane_real_helper(
    recovery_env: asyncpg.Pool,
) -> None:
    """GROUPED bootstrap creates automatic first-section vocab/grammar jobs;
    record-level recovery must report/repair only the ordinary lane and
    never rebuild those section jobs (even though their job types are
    shared and their dedup queries ignore failed_terminal)."""
    pool = recovery_env
    user_id = await insert_user(pool)
    result = await submit_article_ready(
        pool,
        user_id=user_id,
        plain_text=_GROUPED_TEXT,
        title="First Section Boundary",
        language="en",
    )
    record_id = result.record_id
    await _bootstrap(pool, record_id=record_id, user_id=user_id)
    sections_before = await _section_job_count(pool, record_id)
    assert sections_before > 0, "grouped bootstrap creates first-section jobs"

    async with pool.acquire() as conn:
        # Fail the automatic first-section vocabulary job.
        section_job_id = await conn.fetchval(
            """
            UPDATE reader_jobs
            SET status = 'failed_terminal',
                failure_class = 'provider',
                failure_code = 'provider_timeout',
                attempt_count = max_attempts
            WHERE reading_record_id = $1
              AND (input_json->>'request_origin') = $2
              AND job_type = 'build_vocabulary_layer_article'
            RETURNING id
            """,
            record_id,
            ANALYSIS_SECTION_REQUEST_ORIGIN,
        )
        assert section_job_id is not None
        # Fail one ordinary translation window job too, so recovery has
        # real ordinary work to do alongside the section failure.
        translation_job_id = await conn.fetchval(
            """
            UPDATE reader_jobs
            SET status = 'failed_terminal',
                failure_class = 'provider',
                failure_code = 'provider_timeout',
                attempt_count = max_attempts
            WHERE id = (
                SELECT id FROM reader_jobs
                WHERE reading_record_id = $1
                  AND job_type = $2
                  AND status = 'queued'
                ORDER BY created_at ASC, id ASC
                LIMIT 1
            )
            RETURNING id
            """,
            record_id,
            TRANSLATION_BATCH_JOB_TYPE,
        )
        assert translation_job_id is not None
        await conn.execute(
            "UPDATE reading_records SET product_state = 'failed' WHERE id = $1",
            record_id,
        )

    summary = await _recover(pool, record_id=record_id, user_id=user_id)

    assert summary.recovered is True
    # Predecessors: ordinary translation only; section job excluded.
    assert UUID(str(translation_job_id)) in summary.predecessor_job_ids
    assert UUID(str(section_job_id)) not in summary.predecessor_job_ids
    # Successors: ordinary translation window only; no section rebuild.
    assert summary.successor_job_types == (TRANSLATION_BATCH_JOB_TYPE,)
    assert await _section_job_count(pool, record_id) == sections_before
    section_row = await _job_row(pool, UUID(str(section_job_id)))
    assert str(section_row["status"]) == "failed_terminal"
    events = await _recovery_events(pool, record_id)
    assert len(events) == 1
    assert str(section_job_id) not in events[0]["predecessor_job_ids"]
    assert str(section_job_id) not in events[0]["successor_job_ids"]


async def test_recovery_later_section_failure_not_rebuilt(
    recovery_env: asyncpg.Pool,
) -> None:
    """A failed LATER section job must never trigger a FIRST-section
    successor from record-level recovery: section work is rebuilt only
    through its own request flow."""
    pool = recovery_env
    user_id = await insert_user(pool)
    result = await submit_article_ready(
        pool,
        user_id=user_id,
        plain_text=_GROUPED_TEXT,
        title="Later Section Boundary",
        language="en",
    )
    record_id = result.record_id
    await _bootstrap(pool, record_id=record_id, user_id=user_id)

    # Enqueue a real later-section job via the production helper used by
    # AnalysisSectionRequestService (user-explicit lane).
    service = EnhancementJobBootstrapService(pool=pool)
    async with pool.acquire() as conn:
        async with conn.transaction():
            state = await _load_locked_active_base_state(
                conn, record_id=record_id, user_id=user_id
            )
            planned = await _load_planned_sections(conn, state=state)
            assert len(planned) >= 2, "fixture text must plan multiple sections"
            created = await service.enqueue_analysis_section_jobs(
                conn,
                state=state,
                section=planned[1],
                request_origin=USER_EXPLICIT_ANALYSIS_SECTION_ORIGIN,
                include_vocabulary=True,
                include_grammar=True,
            )
            assert created
    jobs_before = await _load_jobs(pool, record_id)
    sections_before = await _section_job_count(pool, record_id)

    # Fail the later-section vocabulary job, mark the record failed.
    async with pool.acquire() as conn:
        later_section_job_id = await conn.fetchval(
            """
            UPDATE reader_jobs
            SET status = 'failed_terminal',
                failure_class = 'provider',
                failure_code = 'provider_timeout',
                attempt_count = max_attempts
            WHERE reading_record_id = $1
              AND (input_json->>'request_origin') = $2
              AND (input_json->>'analysis_section_order_index') = '1'
              AND job_type = 'build_vocabulary_layer_article'
            RETURNING id
            """,
            record_id,
            USER_EXPLICIT_ANALYSIS_SECTION_ORIGIN,
        )
        assert later_section_job_id is not None
        await conn.execute(
            "UPDATE reading_records SET product_state = 'failed' WHERE id = $1",
            record_id,
        )

    summary = await _recover(pool, record_id=record_id, user_id=user_id)

    # Deterministic no-op: no first-section substitute, no later-section
    # rebuild, no unfounded state flip, no event.
    assert summary.recovered is False
    assert summary.event_written is False
    assert summary.next_product_state == "failed"
    assert summary.predecessor_job_ids == ()
    assert summary.successor_job_ids == ()
    assert len(await _load_jobs(pool, record_id)) == len(jobs_before)
    assert await _section_job_count(pool, record_id) == sections_before
    assert await _recovery_events(pool, record_id) == []
    assert str((await _load_record(pool, record_id))["product_state"]) == "failed"


# ---------------------------------------------------------------------------
# 12. Readiness gate fails closed
# ---------------------------------------------------------------------------


async def test_recovery_readiness_gate_fails_closed(
    recovery_env: asyncpg.Pool,
) -> None:
    pool = recovery_env
    user_id = await insert_user(pool)
    record_id = await _setup_incident_record(pool, user_id)
    jobs_before = await _load_jobs(pool, record_id)

    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE reading_records
            SET readiness_state = 'candidate_base_ready'
            WHERE id = $1
            """,
            record_id,
        )

    with pytest.raises(ValueError, match="article-ready"):
        await _recover(pool, record_id=record_id, user_id=user_id)

    # Zero writes: no jobs, no events, product_state untouched.
    assert len(await _load_jobs(pool, record_id)) == len(jobs_before)
    assert await _recovery_events(pool, record_id) == []
    assert str((await _load_record(pool, record_id))["product_state"]) == "failed"
