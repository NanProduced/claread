"""Reading Record soft-delete transaction tests (Wave 8 B2).

Real-PostgreSQL tests for ``ReadingRecordDeletionService``:

- first delete: soft-delete state + timestamps, single-transaction
  convergence of reader_jobs / reader_runs / rag index runs, exactly one
  ``reading_record_deleted_v1`` GC-intent event, full data retention.
- repeat delete: idempotent (keeps first deleted_at, no duplicate
  intent, no re-convergence).
- concurrent double delete: exactly one transition + one intent.
- non-owner / missing: no writes.
- intent publish failure: the whole transaction rolls back.
- legacy soft-deleted rows missing the intent: backfilled in-lock.
- claimed worker lease is revoked and cannot publish afterwards.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.database.connection import init_connection
from app.services.reader_orchestration.event_runtime import ReaderEventRuntime
from app.services.reader_orchestration.job_runtime import (
    IllegalTransitionError,
    ReaderJobRuntime,
)
from app.services.reader_orchestration.reading_record_deletion_service import (
    ReadingRecordDeletionService,
)
from tests.test_reader_orchestration_schema_baseline import BASELINE_SQL, DATABASE_URL

pytestmark = pytest.mark.anyio

BASE_TEXT = "Hello deletion lifecycle body.\nSecond line for the base."


# ---------------------------------------------------------------------------
# Fixtures / data helpers
# ---------------------------------------------------------------------------


@pytest.fixture
async def deletion_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    from app.database import connection as db_connection

    schema_name = f"test_reader_del_{uuid4().hex}"

    async def _setup_conn(conn: asyncpg.Connection) -> None:
        await conn.execute(f'SET search_path TO "{schema_name}", public')

    admin_conn = await asyncpg.connect(DATABASE_URL)
    pool: asyncpg.Pool | None = None
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)
        pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=1,
            max_size=4,
            init=init_connection,
            setup=_setup_conn,
        )
        monkeypatch.setattr(db_connection, "DB_POOL", pool)
        yield {"pool": pool}
    finally:
        if pool is not None:
            await pool.close()
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


async def _insert_user(pool: asyncpg.Pool) -> UUID:
    async with pool.acquire() as conn:
        return await conn.fetchval("INSERT INTO users DEFAULT VALUES RETURNING id")


async def _insert_record(pool: asyncpg.Pool, user_id: UUID) -> UUID:
    async with pool.acquire() as conn:
        record_id = await conn.fetchval(
            """
            INSERT INTO reading_records (user_id, source_type, title)
            VALUES ($1, 'text', 'Deletion lifecycle record') RETURNING id
            """,
            user_id,
        )
    assert isinstance(record_id, UUID)
    return record_id


async def _insert_base(pool: asyncpg.Pool, record_id: UUID) -> UUID:
    async with pool.acquire() as conn:
        base_id = await conn.fetchval(
            """
            INSERT INTO reading_bases (
                reading_record_id, base_version, record_generation, text,
                content_sha256, content_utf16_length,
                canonicalizer_version, builder_version, segmenter_version
            )
            VALUES (
                $1, 1, 1, $2,
                encode(digest($2, 'sha256'), 'hex'),
                utf16_code_unit_length($2),
                'test-canon', 'test-builder', 'test-seg'
            )
            RETURNING id
            """,
            record_id,
            BASE_TEXT,
        )
    assert isinstance(base_id, UUID)
    return base_id


async def _insert_stable_document(
    pool: asyncpg.Pool,
    record_id: UUID,
    *,
    ordinal: int = 1,
) -> UUID:
    # Ordinals >= 2 must be superseded: only one active stable document
    # is allowed per record (uq_stable_reading_documents_active_per_record).
    status = "active" if ordinal == 1 else "superseded"
    async with pool.acquire() as conn:
        stable_id = await conn.fetchval(
            """
            INSERT INTO stable_reading_documents (
                reading_record_id, record_generation, title,
                document_version, content_sha256, status
            )
            VALUES ($1, $2, 'Deletion lifecycle doc', $2,
                    encode(digest($3, 'sha256'), 'hex'), $4)
            RETURNING id
            """,
            record_id,
            ordinal,
            BASE_TEXT,
            status,
        )
    assert isinstance(stable_id, UUID)
    return stable_id


async def _insert_run(
    pool: asyncpg.Pool,
    record_id: UUID,
    user_id: UUID,
    *,
    status: str = "queued",
) -> UUID:
    async with pool.acquire() as conn:
        run_id = await conn.fetchval(
            """
            INSERT INTO reader_runs (
                reading_record_id, user_id, run_type, status,
                record_generation, policy_version, trigger_kind
            )
            VALUES ($1, $2, 'enhancement', $3, 1, 'test-policy', 'user')
            RETURNING id
            """,
            record_id,
            user_id,
            status,
        )
    assert isinstance(run_id, UUID)
    return run_id


async def _insert_job(
    pool: asyncpg.Pool,
    record_id: UUID,
    user_id: UUID,
    run_id: UUID,
    base_id: UUID,
    *,
    status: str,
    with_lease: bool = False,
    job_type: str = "translate_unit",
) -> UUID:
    async with pool.acquire() as conn:
        job_id = await conn.fetchval(
            """
            INSERT INTO reader_jobs (
                reading_record_id, base_id, run_id, user_id, job_type,
                target_type, target_key, status,
                expected_generation, operation_fingerprint, idempotency_key,
                lease_owner, lease_token, lease_expires_at, claimed_at
            )
            VALUES (
                $1, $2, $3, $4, $5, 'unit', $6, $7, 1,
                'fp-test', $8,
                $9, $10, $11, $11
            )
            RETURNING id
            """,
            record_id,
            base_id,
            run_id,
            user_id,
            job_type,
            f"unit-{uuid4().hex[:8]}",
            status,
            f"idem-{uuid4().hex}",
            "worker-1" if with_lease else None,
            uuid4() if with_lease else None,
            datetime.now(tz=UTC) + timedelta(minutes=10) if with_lease else None,
        )
    assert isinstance(job_id, UUID)
    return job_id


async def _insert_index_run(
    pool: asyncpg.Pool,
    record_id: UUID,
    base_id: UUID,
    stable_document_id: UUID,
    *,
    status: str,
    error_json: dict | None = None,
) -> UUID:
    async with pool.acquire() as conn:
        index_run_id = await conn.fetchval(
            """
            INSERT INTO reader_article_rag_index_runs (
                reading_record_id, stable_document_id, base_id,
                record_generation, stable_document_content_sha256,
                canonical_text_sha256, plan_content_sha256, chunk_count,
                status, error_json
            )
            VALUES (
                $1, $2, $3, 1,
                encode(digest($4, 'sha256'), 'hex'),
                encode(digest($4, 'sha256'), 'hex'),
                encode(digest($5, 'sha256'), 'hex'),
                2, $6, $7::jsonb
            )
            RETURNING id
            """,
            record_id,
            stable_document_id,
            base_id,
            BASE_TEXT,
            f"plan-{uuid4().hex}",
            status,
            error_json or {},
        )
    assert isinstance(index_run_id, UUID)
    return index_run_id


async def _full_record_tree(
    pool: asyncpg.Pool,
    user_id: UUID,
) -> dict[str, UUID]:
    """Insert a representative full data tree for retention checks."""
    record_id = await _insert_record(pool, user_id)
    base_id = await _insert_base(pool, record_id)
    stable_document_id = await _insert_stable_document(pool, record_id)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE reading_records SET active_base_id = $2 WHERE id = $1",
            record_id,
            base_id,
        )
        original_input_id = await conn.fetchval(
            """
            INSERT INTO original_inputs (
                reading_record_id, user_id, input_type, source_text,
                content_sha256
            )
            VALUES ($1, $2, 'plain_text', $3,
                    encode(digest($3, 'sha256'), 'hex'))
            RETURNING id
            """,
            record_id,
            user_id,
            BASE_TEXT,
        )
        unit_id = await conn.fetchval(
            """
            INSERT INTO reading_units (
                reading_record_id, base_id, unit_id, order_index,
                unit_type, base_start_utf16, base_end_utf16, text_hash
            )
            VALUES ($1, $2, 'u1', 1, 'body', 0, 20,
                    substr(encode(digest($3, 'sha256'), 'hex'), 1, 8))
            RETURNING id
            """,
            record_id,
            base_id,
            BASE_TEXT,
        )
        anchor_id = await conn.fetchval(
            """
            INSERT INTO anchor_segments (
                reading_record_id, base_id, unit_id, anchor_segment_id,
                order_index, unit_order_index, segment_type,
                base_start_utf16, base_end_utf16,
                unit_start_utf16, unit_end_utf16, text_hash
            )
            VALUES ($1, $2, 'u1', 'a1', 1, 1, 'sentence',
                    0, 5, 0, 5,
                    substr(encode(digest($3, 'sha256'), 'hex'), 1, 8))
            RETURNING id
            """,
            record_id,
            base_id,
            BASE_TEXT,
        )
        note_id = await conn.fetchval(
            """
            INSERT INTO reader_notes (
                user_id, quote_mode, target_key, selected_text, note_text,
                reading_record_id, base_id, generation, unit_id,
                anchor_segment_id
            )
            VALUES ($1, 'sentence', 'u1:a1', 'Hello', 'A kept note.',
                    $2, $3, 1, 'u1', 'a1')
            RETURNING id
            """,
            user_id,
            record_id,
            base_id,
        )
        ask_thread_id = await conn.fetchval(
            """
            INSERT INTO reader_ask_threads (user_id, title, reading_record_id)
            VALUES ($1, 'Kept ask thread', $2) RETURNING id
            """,
            user_id,
            record_id,
        )
    assert isinstance(original_input_id, UUID)
    assert isinstance(unit_id, UUID)
    assert isinstance(anchor_id, UUID)
    assert isinstance(note_id, UUID)
    assert isinstance(ask_thread_id, UUID)
    return {
        "record_id": record_id,
        "base_id": base_id,
        "stable_document_id": stable_document_id,
        "original_input_id": original_input_id,
        "unit_row_id": unit_id,
        "anchor_row_id": anchor_id,
        "note_id": note_id,
        "ask_thread_id": ask_thread_id,
    }


async def _deletion_events(
    pool: asyncpg.Pool,
    record_id: UUID,
) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT id, sequence, event_type, payload_json, created_at
            FROM reader_events
            WHERE reading_record_id = $1
              AND event_type = 'record_state_changed'
              AND payload_json->>'event_schema' = 'reading_record_deleted_v1'
            """,
            record_id,
        )


# ---------------------------------------------------------------------------
# First delete: state, convergence, intent, retention
# ---------------------------------------------------------------------------


async def test_first_delete_converges_state_and_writes_single_intent(
    deletion_env: dict[str, object],
) -> None:
    pool: asyncpg.Pool = deletion_env["pool"]  # type: ignore[assignment]
    user_id = await _insert_user(pool)
    tree = await _full_record_tree(pool, user_id)
    record_id = tree["record_id"]
    base_id = tree["base_id"]
    stable_document_id = tree["stable_document_id"]

    queued_run = await _insert_run(pool, record_id, user_id, status="queued")
    running_run = await _insert_run(pool, record_id, user_id, status="running")
    completed_run = await _insert_run(pool, record_id, user_id, status="completed")

    queued_job = await _insert_job(
        pool, record_id, user_id, queued_run, base_id, status="queued"
    )
    claimed_job = await _insert_job(
        pool, record_id, user_id, running_run, base_id,
        status="claimed", with_lease=True,
    )
    retry_job = await _insert_job(
        pool, record_id, user_id, queued_run, base_id, status="retry_later"
    )
    paused_job = await _insert_job(
        pool, record_id, user_id, queued_run, base_id, status="paused"
    )
    succeeded_job = await _insert_job(
        pool, record_id, user_id, completed_run, base_id, status="succeeded"
    )

    planned_index_run = await _insert_index_run(
        pool, record_id, base_id,
        await _insert_stable_document(pool, record_id, ordinal=2),
        status="planned",
    )
    indexed_index_run = await _insert_index_run(
        pool, record_id, base_id,
        await _insert_stable_document(pool, record_id, ordinal=3),
        status="indexed",
        error_json={"diagnostics": {"prior": "kept"}},
    )
    superseded_index_run = await _insert_index_run(
        pool, record_id, base_id,
        await _insert_stable_document(pool, record_id, ordinal=4),
        status="superseded",
    )

    result = await ReadingRecordDeletionService(pool=pool).delete_record(
        record_id=record_id, user_id=user_id
    )

    assert result is not None
    assert result.record_id == record_id
    assert result.status == "deleted"
    assert result.deleted_at is not None
    assert result.vector_gc_intent_recorded is True

    async with pool.acquire() as conn:
        record = await conn.fetchrow(
            """
            SELECT deleted_at, lifecycle_status, product_state, updated_at,
                   generation
            FROM reading_records WHERE id = $1
            """,
            record_id,
        )
        assert record is not None
        assert record["deleted_at"] == result.deleted_at
        assert record["lifecycle_status"] == "deleted"
        assert record["product_state"] == "deleted"
        assert record["updated_at"] == record["deleted_at"]

        # jobs: non-terminal -> cancelled, terminal untouched.
        job_rows = {
            row["id"]: row
            for row in await conn.fetch(
                """
                SELECT id, status, lease_owner, lease_token, lease_expires_at,
                       claimed_at, pause_owner, rationale_code
                FROM reader_jobs WHERE reading_record_id = $1
                """,
                record_id,
            )
        }
        for job in (queued_job, claimed_job, retry_job, paused_job):
            row = job_rows[job]
            assert row["status"] == "cancelled"
            assert row["rationale_code"] == "reading_record_deleted"
            assert row["lease_owner"] is None
            assert row["lease_token"] is None
            assert row["lease_expires_at"] is None
            assert row["claimed_at"] is None
            assert row["pause_owner"] is None
        assert job_rows[succeeded_job]["status"] == "succeeded"

        # one job_cancelled event per actually-transitioned job.
        cancel_events = await conn.fetch(
            """
            SELECT job_id, payload_json FROM reader_job_events
            WHERE reading_record_id = $1 AND event_type = 'job_cancelled'
            """,
            record_id,
        )
        cancelled_job_ids = {row["job_id"] for row in cancel_events}
        assert cancelled_job_ids == {queued_job, claimed_job, retry_job, paused_job}
        by_job = {row["job_id"]: row["payload_json"] for row in cancel_events}
        assert by_job[claimed_job]["previous_status"] == "claimed"
        assert by_job[claimed_job]["rationale_code"] == "reading_record_deleted"

        # runs: non-terminal -> cancelled with timestamps; terminal untouched.
        run_rows = {
            row["id"]: row
            for row in await conn.fetch(
                """
                SELECT id, status, finished_at, updated_at
                FROM reader_runs WHERE reading_record_id = $1
                """,
                record_id,
            )
        }
        for run in (queued_run, running_run):
            assert run_rows[run]["status"] == "cancelled"
            assert run_rows[run]["finished_at"] == record["deleted_at"]
            assert run_rows[run]["updated_at"] == record["deleted_at"]
        assert run_rows[completed_run]["status"] == "completed"

        # index runs: planned/queued/indexing/indexed -> superseded.
        index_rows = {
            row["id"]: row
            for row in await conn.fetch(
                """
                SELECT id, status, completed_at, updated_at, error_json
                FROM reader_article_rag_index_runs WHERE reading_record_id = $1
                """,
                record_id,
            )
        }
        for index_run in (planned_index_run, indexed_index_run):
            row = index_rows[index_run]
            assert row["status"] == "superseded"
            assert row["completed_at"] == record["deleted_at"]
            assert row["updated_at"] == record["deleted_at"]
        assert index_rows[superseded_index_run]["status"] == "superseded"

        merged_error = index_rows[indexed_index_run]["error_json"]
        assert merged_error["failure_code"] == "reading_record_deleted"
        assert merged_error["rationale_code"] == "user_deleted_record"
        assert merged_error["diagnostics"]["prior"] == "kept"

        # retention: the full data tree still exists.
        assert await conn.fetchval(
            "SELECT COUNT(*) FROM original_inputs WHERE id = $1",
            tree["original_input_id"],
        ) == 1
        assert await conn.fetchval(
            "SELECT COUNT(*) FROM reading_bases WHERE id = $1", base_id
        ) == 1
        assert await conn.fetchval(
            "SELECT COUNT(*) FROM stable_reading_documents WHERE id = $1",
            stable_document_id,
        ) == 1
        assert await conn.fetchval(
            "SELECT COUNT(*) FROM reading_units WHERE id = $1",
            tree["unit_row_id"],
        ) == 1
        assert await conn.fetchval(
            "SELECT COUNT(*) FROM anchor_segments WHERE id = $1",
            tree["anchor_row_id"],
        ) == 1
        assert await conn.fetchval(
            "SELECT COUNT(*) FROM reader_notes WHERE id = $1", tree["note_id"]
        ) == 1
        assert await conn.fetchval(
            "SELECT COUNT(*) FROM reader_ask_threads WHERE id = $1",
            tree["ask_thread_id"],
        ) == 1

    # exactly one deletion-v1 intent event with the exact payload contract.
    events = await _deletion_events(pool, record_id)
    assert len(events) == 1
    payload = events[0]["payload_json"]
    assert payload["event_schema"] == "reading_record_deleted_v1"
    assert payload["operation"] == "soft_deleted"
    assert payload["reason_code"] == "user_removed_reading_record"
    assert payload["actor_user_id"] == str(user_id)
    assert payload["deleted_at"] == result.deleted_at.isoformat()
    assert payload["record_generation"] == 1
    assert payload["article_rag_vector_gc_requested"] is True
    assert payload["transition_counts"] == {
        "jobs_cancelled": 4,
        "runs_cancelled": 2,
        "index_runs_superseded": 2,
    }
    # Payload must not carry any user content.
    payload_text = str(payload)
    assert "Deletion lifecycle record" not in payload_text
    assert BASE_TEXT.splitlines()[0] not in payload_text


# ---------------------------------------------------------------------------
# Idempotency & concurrency
# ---------------------------------------------------------------------------


async def test_repeat_delete_is_idempotent(
    deletion_env: dict[str, object],
) -> None:
    pool: asyncpg.Pool = deletion_env["pool"]  # type: ignore[assignment]
    user_id = await _insert_user(pool)
    tree = await _full_record_tree(pool, user_id)
    record_id = tree["record_id"]
    base_id = tree["base_id"]
    run_id = await _insert_run(pool, record_id, user_id, status="queued")
    job_id = await _insert_job(
        pool, record_id, user_id, run_id, base_id, status="queued"
    )

    service = ReadingRecordDeletionService(pool=pool)
    first = await service.delete_record(record_id=record_id, user_id=user_id)
    assert first is not None and first.status == "deleted"

    async with pool.acquire() as conn:
        job_events_after_first = await conn.fetchval(
            "SELECT COUNT(*) FROM reader_job_events WHERE reading_record_id = $1",
            record_id,
        )

    second = await service.delete_record(record_id=record_id, user_id=user_id)
    assert second is not None
    assert second.status == "already_deleted"
    assert second.deleted_at == first.deleted_at

    async with pool.acquire() as conn:
        record = await conn.fetchrow(
            "SELECT deleted_at, updated_at FROM reading_records WHERE id = $1",
            record_id,
        )
        assert record is not None
        assert record["deleted_at"] == first.deleted_at
        assert record["updated_at"] == record["deleted_at"]
        job = await conn.fetchrow(
            "SELECT status FROM reader_jobs WHERE id = $1", job_id
        )
        assert job is not None and job["status"] == "cancelled"
        job_events_after_second = await conn.fetchval(
            "SELECT COUNT(*) FROM reader_job_events WHERE reading_record_id = $1",
            record_id,
        )
        assert job_events_after_second == job_events_after_first

    assert len(await _deletion_events(pool, record_id)) == 1


async def test_concurrent_double_delete_single_transition_and_intent(
    deletion_env: dict[str, object],
) -> None:
    pool: asyncpg.Pool = deletion_env["pool"]  # type: ignore[assignment]
    user_id = await _insert_user(pool)
    tree = await _full_record_tree(pool, user_id)
    record_id = tree["record_id"]
    base_id = tree["base_id"]
    run_id = await _insert_run(pool, record_id, user_id, status="queued")
    await _insert_job(pool, record_id, user_id, run_id, base_id, status="queued")

    service = ReadingRecordDeletionService(pool=pool)
    first, second = await asyncio.gather(
        service.delete_record(record_id=record_id, user_id=user_id),
        service.delete_record(record_id=record_id, user_id=user_id),
    )
    statuses = {first.status, second.status}
    assert statuses == {"deleted", "already_deleted"}
    assert first.deleted_at == second.deleted_at

    events = await _deletion_events(pool, record_id)
    assert len(events) == 1
    async with pool.acquire() as conn:
        cancelled_events = await conn.fetchval(
            """
            SELECT COUNT(*) FROM reader_job_events
            WHERE reading_record_id = $1 AND event_type = 'job_cancelled'
            """,
            record_id,
        )
        assert cancelled_events == 1


# ---------------------------------------------------------------------------
# Ownership & rollback
# ---------------------------------------------------------------------------


async def test_non_owner_or_missing_delete_writes_nothing(
    deletion_env: dict[str, object],
) -> None:
    pool: asyncpg.Pool = deletion_env["pool"]  # type: ignore[assignment]
    user_id = await _insert_user(pool)
    other_user_id = await _insert_user(pool)
    tree = await _full_record_tree(pool, user_id)
    record_id = tree["record_id"]

    service = ReadingRecordDeletionService(pool=pool)
    missing = await service.delete_record(
        record_id=uuid4(), user_id=user_id
    )
    non_owner = await service.delete_record(
        record_id=record_id, user_id=other_user_id
    )
    assert missing is None
    assert non_owner is None

    async with pool.acquire() as conn:
        record = await conn.fetchrow(
            """
            SELECT deleted_at, lifecycle_status, product_state
            FROM reading_records WHERE id = $1
            """,
            record_id,
        )
        assert record is not None
        assert record["deleted_at"] is None
        assert record["lifecycle_status"] == "active"
        assert record["product_state"] != "deleted"
        assert await conn.fetchval(
            "SELECT COUNT(*) FROM reader_events WHERE reading_record_id = $1",
            record_id,
        ) == 0


async def test_intent_publish_failure_rolls_back_everything(
    deletion_env: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool: asyncpg.Pool = deletion_env["pool"]  # type: ignore[assignment]
    user_id = await _insert_user(pool)
    tree = await _full_record_tree(pool, user_id)
    record_id = tree["record_id"]
    base_id = tree["base_id"]
    run_id = await _insert_run(pool, record_id, user_id, status="queued")
    job_id = await _insert_job(
        pool, record_id, user_id, run_id, base_id, status="queued"
    )

    async def _fail(self, conn, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("simulated reader_events insert failure")

    monkeypatch.setattr(
        ReaderEventRuntime, "publish_event_in_transaction", _fail
    )

    with pytest.raises(RuntimeError, match="simulated reader_events"):
        await ReadingRecordDeletionService(pool=pool).delete_record(
            record_id=record_id, user_id=user_id
        )

    async with pool.acquire() as conn:
        record = await conn.fetchrow(
            """
            SELECT deleted_at, lifecycle_status, product_state
            FROM reading_records WHERE id = $1
            """,
            record_id,
        )
        assert record is not None
        assert record["deleted_at"] is None
        assert record["lifecycle_status"] == "active"
        job = await conn.fetchrow(
            "SELECT status FROM reader_jobs WHERE id = $1", job_id
        )
        assert job is not None and job["status"] == "queued"
        run = await conn.fetchrow(
            "SELECT status FROM reader_runs WHERE id = $1", run_id
        )
        assert run is not None and run["status"] == "queued"
        assert await conn.fetchval(
            "SELECT COUNT(*) FROM reader_events WHERE reading_record_id = $1",
            record_id,
        ) == 0
        assert await conn.fetchval(
            "SELECT COUNT(*) FROM reader_job_events WHERE reading_record_id = $1",
            record_id,
        ) == 0


async def test_legacy_soft_deleted_row_backfills_intent(
    deletion_env: dict[str, object],
) -> None:
    pool: asyncpg.Pool = deletion_env["pool"]  # type: ignore[assignment]
    user_id = await _insert_user(pool)
    tree = await _full_record_tree(pool, user_id)
    record_id = tree["record_id"]

    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE reading_records
            SET deleted_at = NOW(), lifecycle_status = 'deleted',
                product_state = 'deleted'
            WHERE id = $1
            """,
            record_id,
        )

    result = await ReadingRecordDeletionService(pool=pool).delete_record(
        record_id=record_id, user_id=user_id
    )
    assert result is not None
    assert result.status == "already_deleted"

    events = await _deletion_events(pool, record_id)
    assert len(events) == 1
    payload = events[0]["payload_json"]
    assert payload["event_schema"] == "reading_record_deleted_v1"
    assert payload["actor_user_id"] == str(user_id)
    assert payload["article_rag_vector_gc_requested"] is True


# ---------------------------------------------------------------------------
# Provider isolation
# ---------------------------------------------------------------------------


async def test_retrieval_fails_closed_for_deleted_record_without_provider_io(
    deletion_env: dict[str, object],
) -> None:
    """Deleted record: zero embedding / vector-search / vector-delete
    attempts — retrieval fails at the shared plan guard."""
    from app.services.reader_orchestration.article_rag_index_worker import (
        FakeArticleRagEmbeddingProvider,
    )
    from app.services.reader_orchestration.article_rag_retrieval_service import (
        ArticleRagRetrievalService,
    )

    pool: asyncpg.Pool = deletion_env["pool"]  # type: ignore[assignment]
    user_id = await _insert_user(pool)
    tree = await _full_record_tree(pool, user_id)
    record_id = tree["record_id"]

    result = await ReadingRecordDeletionService(pool=pool).delete_record(
        record_id=record_id, user_id=user_id
    )
    assert result is not None and result.status == "deleted"

    class _CountingSearcher:
        def __init__(self) -> None:
            self.search_calls = 0

        async def search(self, *args: object, **kwargs: object) -> list[object]:
            self.search_calls += 1
            return []

    fake_embedding = FakeArticleRagEmbeddingProvider()
    counting_searcher = _CountingSearcher()
    service = ArticleRagRetrievalService(
        pool=pool,
        embedding_provider=fake_embedding,
        vector_searcher=counting_searcher,  # type: ignore[arg-type]
    )
    with pytest.raises(LookupError):
        await service.retrieve_for_record(
            reading_record_id=record_id,
            user_id=user_id,
            query_text="anything at all",
        )
    assert fake_embedding.call_count == 0
    assert counting_searcher.search_calls == 0


# ---------------------------------------------------------------------------
# Claimed worker fence
# ---------------------------------------------------------------------------


async def test_claimed_worker_cannot_publish_after_delete(
    deletion_env: dict[str, object],
) -> None:
    pool: asyncpg.Pool = deletion_env["pool"]  # type: ignore[assignment]
    user_id = await _insert_user(pool)
    tree = await _full_record_tree(pool, user_id)
    record_id = tree["record_id"]
    base_id = tree["base_id"]
    run_id = await _insert_run(pool, record_id, user_id, status="running")
    job_id = await _insert_job(
        pool, record_id, user_id, run_id, base_id,
        status="claimed", with_lease=True,
    )
    async with pool.acquire() as conn:
        lease_token = await conn.fetchval(
            "SELECT lease_token FROM reader_jobs WHERE id = $1", job_id
        )
    assert lease_token is not None

    result = await ReadingRecordDeletionService(pool=pool).delete_record(
        record_id=record_id, user_id=user_id
    )
    assert result is not None and result.status == "deleted"

    runtime = ReaderJobRuntime(pool=pool)
    async with pool.acquire() as conn:
        async with conn.transaction():
            with pytest.raises(IllegalTransitionError):
                await runtime.transition_in_transaction(
                    conn,
                    job_id=job_id,
                    target_status="succeeded",
                    lease_token=lease_token,
                )
