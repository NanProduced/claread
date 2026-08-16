"""Article RAG vector-GC service tests (Wave 9 B).

Real-PostgreSQL tests for ``ArticleRagVectorGcService`` with an
in-memory fake deleter:

- consumes the real Wave 8 ``reading_record_deleted_v1`` intent format.
- record not deleted -> fail-closed retry, zero vector I/O.
- active index run / active build job -> retry, zero vector I/O.
- no historical index runs -> completed no_vectors.
- multiple stable-document identities processed one by one.
- completed / terminal outcomes block reprocessing.
- retry available_at in the future -> skipped.
- crash-after-delete-before-completion -> idempotent recovery.
- two services racing -> exactly one deletes and writes the completion.
- unsupported provider / collection mismatch -> terminal before I/O.
- retry attempt/backoff derived from historical retry events.
- event payloads carry no user content, ids, collection, or token.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.contracts.article_rag_contract import ARTICLE_RAG_EMBEDDING_CONTRACT
from app.database.connection import init_connection
from app.services.reader_orchestration.article_rag_vector_deleter import (
    ArticleRagVectorDeletionError,
    ArticleRagVectorDeletionResult,
    UnconfiguredArticleRagVectorDeleter,
)
from app.services.reader_orchestration.article_rag_vector_gc_service import (
    ArticleRagVectorGcService,
)
from app.services.reader_orchestration.reading_record_deletion_service import (
    ReadingRecordDeletionService,
)
from tests.test_reader_orchestration_schema_baseline import BASELINE_SQL, DATABASE_URL

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.chain_article_rag,
    pytest.mark.seam_service_integration,
    pytest.mark.life_permanent_regression,
]

BASE_TEXT = "Hello GC lifecycle body.\nSecond line for the base."

CONFIGURED_COLLECTION = ARTICLE_RAG_EMBEDDING_CONTRACT.vector_collection


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def gc_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    from app.database import connection as db_connection

    schema_name = f"test_rag_gc_{uuid4().hex}"

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


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------


async def _insert_user(pool: asyncpg.Pool) -> UUID:
    async with pool.acquire() as conn:
        return await conn.fetchval("INSERT INTO users DEFAULT VALUES RETURNING id")


async def _insert_record(pool: asyncpg.Pool, user_id: UUID) -> UUID:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            INSERT INTO reading_records (user_id, source_type, title)
            VALUES ($1, 'text', 'GC lifecycle record') RETURNING id
            """,
            user_id,
        )


async def _insert_base(pool: asyncpg.Pool, record_id: UUID) -> UUID:
    async with pool.acquire() as conn:
        return await conn.fetchval(
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


async def _insert_stable_document(
    pool: asyncpg.Pool,
    record_id: UUID,
    *,
    record_generation: int = 1,
    status: str = "active",
) -> UUID:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            INSERT INTO stable_reading_documents (
                reading_record_id, record_generation, title,
                document_version, content_sha256, status
            )
            VALUES (
                $1, $2, 'GC lifecycle doc', $2,
                encode(digest($3, 'sha256'), 'hex'), $4
            )
            RETURNING id
            """,
            record_id,
            record_generation,
            f"stable-{uuid4().hex}",
            status,
        )


async def _insert_base(
    pool: asyncpg.Pool,
    record_id: UUID,
    *,
    base_version: int = 1,
    record_generation: int = 1,
    status: str = "active",
) -> UUID:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            INSERT INTO reading_bases (
                reading_record_id, base_version, record_generation, text,
                content_sha256, content_utf16_length,
                canonicalizer_version, builder_version, segmenter_version,
                status
            )
            VALUES (
                $1, $2, $3, $4,
                encode(digest($4, 'sha256'), 'hex'),
                utf16_code_unit_length($4),
                'test-canon', 'test-builder', 'test-seg',
                $5
            )
            RETURNING id
            """,
            record_id,
            base_version,
            record_generation,
            BASE_TEXT,
            status,
        )


async def _insert_index_run(
    pool: asyncpg.Pool,
    record_id: UUID,
    base_id: UUID,
    stable_document_id: UUID,
    *,
    status: str,
    provider: str | None = None,
    collection: str | None = None,
    record_generation: int = 1,
) -> UUID:
    async with pool.acquire() as conn:
        index_run_id = await conn.fetchval(
            """
            INSERT INTO reader_article_rag_index_runs (
                reading_record_id, stable_document_id, base_id,
                record_generation, stable_document_content_sha256,
                canonical_text_sha256, plan_content_sha256, chunk_count,
                status, vector_store_provider, vector_collection
            )
            VALUES (
                $1, $2, $3, $4,
                encode(digest($5, 'sha256'), 'hex'),
                encode(digest($5, 'sha256'), 'hex'),
                encode(digest($6, 'sha256'), 'hex'),
                2, $7, $8, $9
            )
            RETURNING id
            """,
            record_id,
            stable_document_id,
            base_id,
            record_generation,
            f"content-{uuid4().hex}",
            f"plan-{uuid4().hex}",
            status,
            provider,
            collection,
        )
    assert isinstance(index_run_id, UUID)
    return index_run_id


async def _insert_build_job(
    pool: asyncpg.Pool,
    record_id: UUID,
    user_id: UUID,
    *,
    status: str,
) -> UUID:
    async with pool.acquire() as conn:
        base_id = await conn.fetchval(
            """
            INSERT INTO reading_bases (
                reading_record_id, base_version, record_generation, text,
                content_sha256, content_utf16_length,
                canonicalizer_version, builder_version, segmenter_version,
                status
            )
            VALUES (
                $1, 3, 2, $2,
                encode(digest($2, 'sha256'), 'hex'),
                utf16_code_unit_length($2),
                'test-canon', 'test-builder', 'test-seg',
                'superseded'
            )
            RETURNING id
            """,
            record_id,
            BASE_TEXT,
        )
        run_id = await conn.fetchval(
            """
            INSERT INTO reader_runs (
                reading_record_id, user_id, run_type, status,
                record_generation, policy_version, trigger_kind
            )
            VALUES ($1, $2, 'article_rag_index_build', 'queued', 2,
                    'test-policy', 'user')
            RETURNING id
            """,
            record_id,
            user_id,
        )
        return await conn.fetchval(
            """
            INSERT INTO reader_jobs (
                reading_record_id, base_id, run_id, user_id, job_type,
                target_type, target_key, status,
                expected_generation, operation_fingerprint, idempotency_key
            )
            VALUES (
                $1, $2, $3, $4, 'article_rag_index_build',
                'unit_range', $5, $6, 2, 'test-fingerprint', $7
            )
            RETURNING id
            """,
            record_id,
            base_id,
            run_id,
            user_id,
            str(uuid4()),
            status,
            str(uuid4()),
        )


async def _publish_intent(
    pool: asyncpg.Pool,
    record_id: UUID,
    user_id: UUID,
) -> UUID:
    """Publish the exact Wave 8 deletion-intent event shape."""
    from app.services.reader_orchestration.event_runtime import ReaderEventRuntime

    envelope = await ReaderEventRuntime(pool=pool).publish_event(
        record_id=record_id,
        event_type="record_state_changed",
        payload_json={
            "event_schema": "reading_record_deleted_v1",
            "operation": "soft_deleted",
            "reason_code": "user_removed_reading_record",
            "actor_user_id": str(user_id),
            "deleted_at": datetime.now(UTC).isoformat(),
            "record_generation": 1,
            "article_rag_vector_gc_requested": True,
            "transition_counts": {
                "jobs_cancelled": 0,
                "runs_cancelled": 0,
                "index_runs_superseded": 0,
            },
        },
    )
    return envelope.event_id


async def _delete_record(pool: asyncpg.Pool, record_id: UUID, user_id: UUID) -> None:
    result = await ReadingRecordDeletionService(pool=pool).delete_record(
        record_id=record_id, user_id=user_id
    )
    assert result is not None


async def _events_for_intent(
    pool: asyncpg.Pool,
    record_id: UUID,
    intent_event_id: UUID,
) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT id, event_type, payload_json, created_at
            FROM reader_events
            WHERE reading_record_id = $1
              AND event_type = 'record_state_changed'
              AND payload_json->>'intent_event_id' = $2
            ORDER BY created_at, id
            """,
            record_id,
            str(intent_event_id),
        )


def _with_schema(
    events: list[asyncpg.Record],
    schema: str,
) -> list[asyncpg.Record]:
    return [
        e for e in events if e["payload_json"]["event_schema"] == schema
    ]


# ---------------------------------------------------------------------------
# Fake deleter
# ---------------------------------------------------------------------------


class _FakeDeleter:
    def __init__(
        self,
        *,
        result: ArticleRagVectorDeletionResult | None = None,
    ) -> None:
        self.result = result or ArticleRagVectorDeletionResult(
            outcome="deleted",
            discovered_chunk_count=2,
            deleted_chunk_count=2,
            delete_call_count=1,
        )
        self.calls: list[tuple[str, UUID]] = []
        self.raise_error: ArticleRagVectorDeletionError | None = None

    async def delete_for_stable_document(
        self,
        *,
        collection: str,
        stable_document_id: UUID,
    ) -> ArticleRagVectorDeletionResult:
        self.calls.append((collection, stable_document_id))
        if self.raise_error is not None:
            raise self.raise_error
        return self.result


def _build_service(
    pool: asyncpg.Pool,
    *,
    deleter: object,
    backoff_base: timedelta = timedelta(seconds=30),
    backoff_max: timedelta = timedelta(hours=1),
    clock: datetime | None = None,
) -> ArticleRagVectorGcService:
    return ArticleRagVectorGcService(
        pool=pool,
        deleter=deleter,  # type: ignore[arg-type]
        backoff_base=backoff_base,
        backoff_max=backoff_max,
        clock=lambda: clock or datetime.now(UTC),
    )


async def _latest_intent_id(pool: asyncpg.Pool, record_id: UUID) -> UUID:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id
            FROM reader_events
            WHERE reading_record_id = $1
              AND event_type = 'record_state_changed'
              AND payload_json->>'event_schema' = 'reading_record_deleted_v1'
            ORDER BY created_at, id
            LIMIT 1
            """,
            record_id,
        )
    assert row is not None
    return UUID(str(row["id"]))


async def _full_deleted_env(
    gc_env: dict[str, object],
) -> tuple[asyncpg.Pool, UUID, UUID, UUID, UUID]:
    """Record + stable doc + superseded indexed run, then REAL Wave 8 delete.

    Returns ``(pool, user_id, record_id, stable_document_id, intent_id)``
    where ``intent_id`` is the deletion transaction's own GC intent event.
    """
    pool: asyncpg.Pool = gc_env["pool"]  # type: ignore[assignment]
    user_id = await _insert_user(pool)
    record_id = await _insert_record(pool, user_id)
    base_id = await _insert_base(pool, record_id)
    stable_document_id = await _insert_stable_document(pool, record_id)
    await _insert_index_run(
        pool,
        record_id,
        base_id,
        stable_document_id,
        status="superseded",
        provider="zilliz",
        collection=CONFIGURED_COLLECTION,
    )
    await _delete_record(pool, record_id, user_id)
    intent_id = await _latest_intent_id(pool, record_id)
    return pool, user_id, record_id, stable_document_id, intent_id


# ===========================================================================
# Consuming Wave 8 intents
# ===========================================================================


class TestWave8IntentConsumption:
    async def test_consumes_wave8_intent_and_completes(self, gc_env: dict) -> None:
        pool, _, record_id, stable_document_id, intent_id = await _full_deleted_env(gc_env)
        deleter = _FakeDeleter()
        service = _build_service(pool, deleter=deleter)

        result = await service.process_next_due_intent()

        assert result is not None
        assert result.status == "completed"
        assert result.outcome == "deleted"
        assert result.stable_document_count == 1
        assert result.discovered_chunk_count == 2
        assert result.deleted_chunk_count == 2
        assert deleter.calls == [(CONFIGURED_COLLECTION, stable_document_id)]

        events = await _events_for_intent(pool, record_id, result.intent_event_id)
        completed = _with_schema(events, "article_rag_vector_gc_completed_v1")
        assert len(completed) == 1
        payload = completed[0]["payload_json"]
        assert payload["intent_event_id"] == str(result.intent_event_id)
        assert payload["outcome"] == "deleted"
        assert payload["stable_document_count"] == 1
        assert payload["discovered_chunk_count"] == 2
        assert payload["deleted_chunk_count"] == 2
        assert "completed_at" in payload

    async def test_no_index_runs_completes_no_vectors(self, gc_env: dict) -> None:
        pool = gc_env["pool"]  # type: ignore[assignment]
        user_id = await _insert_user(pool)
        record_id = await _insert_record(pool, user_id)
        await _delete_record(pool, record_id, user_id)
        intent_id = await _latest_intent_id(pool, record_id)
        deleter = _FakeDeleter()
        service = _build_service(pool, deleter=deleter)

        result = await service.process_next_due_intent()

        assert result is not None
        assert result.status == "completed"
        assert result.outcome == "no_vectors"
        assert result.stable_document_count == 0
        assert result.deleted_chunk_count == 0
        assert deleter.calls == []

        events = await _events_for_intent(pool, record_id, intent_id)
        completed = _with_schema(events, "article_rag_vector_gc_completed_v1")
        assert len(completed) == 1
        assert completed[0]["payload_json"]["outcome"] == "no_vectors"


# ===========================================================================
# Qualification / fail-closed
# ===========================================================================


class TestQualification:
    async def test_record_not_deleted_fails_closed(self, gc_env: dict) -> None:
        pool = gc_env["pool"]  # type: ignore[assignment]
        user_id = await _insert_user(pool)
        record_id = await _insert_record(pool, user_id)
        await _insert_stable_document(pool, record_id)
        await _publish_intent(pool, record_id, user_id)
        deleter = _FakeDeleter()
        service = _build_service(pool, deleter=deleter)

        result = await service.process_next_due_intent()

        assert result is not None
        assert result.status == "retry_scheduled"
        assert result.failure_code == "record_not_deleted"
        assert deleter.calls == []

    async def test_active_index_run_writes_retry_zero_io(self, gc_env: dict) -> None:
        pool, user_id, record_id, _, intent_id = await _full_deleted_env(gc_env)
        # A run that raced back into an active state after deletion.
        await _insert_index_run(
            pool,
            record_id,
            await _insert_base(
                pool, record_id, base_version=2, record_generation=2,
                status="superseded",
            ),
            await _insert_stable_document(
                pool, record_id, record_generation=2, status="superseded"
            ),
            record_generation=2,
            status="indexing",
        )
        deleter = _FakeDeleter()
        service = _build_service(pool, deleter=deleter)

        result = await service.process_next_due_intent()

        assert result is not None
        assert result.status == "retry_scheduled"
        assert result.failure_code == "active_index_run_present"
        assert deleter.calls == []

    async def test_active_build_job_writes_retry_zero_io(self, gc_env: dict) -> None:
        pool, user_id, record_id, _, intent_id = await _full_deleted_env(gc_env)
        await _insert_build_job(pool, record_id, user_id, status="queued")
        deleter = _FakeDeleter()
        service = _build_service(pool, deleter=deleter)

        result = await service.process_next_due_intent()

        assert result is not None
        assert result.status == "retry_scheduled"
        assert result.failure_code == "active_build_job_present"
        assert deleter.calls == []

    async def test_multiple_identities_processed_one_by_one(self, gc_env: dict) -> None:
        pool, user_id, record_id, first_stable, intent_id = await _full_deleted_env(gc_env)
        second_stable = await _insert_stable_document(
            pool, record_id, record_generation=2, status="superseded"
        )
        await _insert_index_run(
            pool,
            record_id,
            await _insert_base(
                pool, record_id, base_version=2, record_generation=2,
                status="superseded",
            ),
            second_stable,
            record_generation=2,
            status="superseded", provider="zilliz", collection=CONFIGURED_COLLECTION,
        )
        deleter = _FakeDeleter()
        service = _build_service(pool, deleter=deleter)

        result = await service.process_next_due_intent()

        assert result is not None
        assert result.status == "completed"
        assert result.stable_document_count == 2
        assert result.deleted_chunk_count == 4
        ordered = sorted([first_stable, second_stable])
        assert deleter.calls == [(CONFIGURED_COLLECTION, s) for s in ordered]

    async def test_unsupported_provider_terminates_before_io(self, gc_env: dict) -> None:
        pool, user_id, record_id, stable_document_id, intent_id = await _full_deleted_env(gc_env)
        await _insert_index_run(
            pool,
            record_id,
            await _insert_base(
                pool, record_id, base_version=2, record_generation=2,
                status="superseded",
            ),
            stable_document_id,
            status="superseded", provider="pinecone", collection=CONFIGURED_COLLECTION,
        )
        deleter = _FakeDeleter()
        service = _build_service(pool, deleter=deleter)

        result = await service.process_next_due_intent()

        assert result is not None
        assert result.status == "failed_terminal"
        assert result.failure_code == "unsupported_provider"
        assert deleter.calls == []

    async def test_collection_mismatch_terminates_before_io(self, gc_env: dict) -> None:
        pool, user_id, record_id, stable_document_id, intent_id = await _full_deleted_env(gc_env)
        await _insert_index_run(
            pool,
            record_id,
            await _insert_base(
                pool, record_id, base_version=2, record_generation=2,
                status="superseded",
            ),
            stable_document_id,
            status="superseded", provider="zilliz", collection="other_collection",
        )
        deleter = _FakeDeleter()
        service = _build_service(pool, deleter=deleter)

        result = await service.process_next_due_intent()

        assert result is not None
        assert result.status == "failed_terminal"
        assert result.failure_code == "collection_mismatch"
        assert deleter.calls == []

    async def test_deleter_typed_failure_writes_retry(self, gc_env: dict) -> None:
        pool, user_id, record_id, _, intent_id = await _full_deleted_env(gc_env)
        deleter = _FakeDeleter()
        deleter.raise_error = ArticleRagVectorDeletionError(
            "fixed safe delete failure",
            retryable=True,
            failure_code="vector_deletion_delete_failed",
        )
        service = _build_service(pool, deleter=deleter)

        result = await service.process_next_due_intent()

        assert result is not None
        assert result.status == "retry_scheduled"
        assert result.failure_code == "vector_deletion_delete_failed"
        events = await _events_for_intent(pool, record_id, intent_id)
        retries = _with_schema(events, "article_rag_vector_gc_retry_scheduled_v1")
        assert len(retries) == 1
        assert retries[0]["payload_json"]["failure_code"] == "vector_deletion_delete_failed"
        assert retries[0]["payload_json"]["attempt_number"] == 1


# ===========================================================================
# Outcome / retry / terminal state machine
# ===========================================================================


class TestStateMachine:
    async def test_completed_blocks_reprocessing(self, gc_env: dict) -> None:
        pool, user_id, record_id, _, intent_id = await _full_deleted_env(gc_env)
        deleter = _FakeDeleter()
        service = _build_service(pool, deleter=deleter)

        first = await service.process_next_due_intent()
        assert first is not None and first.status == "completed"
        second = await service.process_next_due_intent()

        assert second is None
        assert len(deleter.calls) == 1

    async def test_terminal_blocks_reprocessing(self, gc_env: dict) -> None:
        pool, user_id, record_id, _, intent_id = await _full_deleted_env(gc_env)
        from app.services.reader_orchestration.event_runtime import ReaderEventRuntime

        await ReaderEventRuntime(pool=pool).publish_event(
            record_id=record_id,
            event_type="record_state_changed",
            payload_json={
                "event_schema": "article_rag_vector_gc_failed_terminal_v1",
                "intent_event_id": str(intent_id),
                "failure_code": "unsupported_provider",
                "failed_at": datetime.now(UTC).isoformat(),
            },
        )
        deleter = _FakeDeleter()
        service = _build_service(pool, deleter=deleter)

        result = await service.process_next_due_intent()

        assert result is None
        assert deleter.calls == []

    async def test_retry_not_due_yet_is_skipped(self, gc_env: dict) -> None:
        pool, user_id, record_id, _, intent_id = await _full_deleted_env(gc_env)
        from app.services.reader_orchestration.event_runtime import ReaderEventRuntime

        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        await ReaderEventRuntime(pool=pool).publish_event(
            record_id=record_id,
            event_type="record_state_changed",
            payload_json={
                "event_schema": "article_rag_vector_gc_retry_scheduled_v1",
                "intent_event_id": str(intent_id),
                "attempt_number": 1,
                "failure_code": "active_index_run_present",
                "available_at": future,
            },
        )
        deleter = _FakeDeleter()
        service = _build_service(pool, deleter=deleter)

        result = await service.process_next_due_intent()

        assert result is None
        assert deleter.calls == []

    async def test_retry_attempt_and_backoff_derived_from_history(
        self, gc_env: dict
    ) -> None:
        pool, user_id, record_id, _, intent_id = await _full_deleted_env(gc_env)
        from app.services.reader_orchestration.event_runtime import ReaderEventRuntime

        past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        for attempt in (1, 2):
            await ReaderEventRuntime(pool=pool).publish_event(
                record_id=record_id,
                event_type="record_state_changed",
                payload_json={
                    "event_schema": "article_rag_vector_gc_retry_scheduled_v1",
                    "intent_event_id": str(intent_id),
                    "attempt_number": attempt,
                    "failure_code": "active_index_run_present",
                    "available_at": past,
                },
            )
        deleter = _FakeDeleter()
        deleter.raise_error = ArticleRagVectorDeletionError(
            "fixed safe delete failure",
            retryable=True,
            failure_code="vector_deletion_delete_failed",
        )
        fixed_now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        service = _build_service(
            pool, deleter=deleter,
            backoff_base=timedelta(seconds=30),
            backoff_max=timedelta(hours=1),
            clock=fixed_now,
        )

        result = await service.process_next_due_intent()

        assert result is not None
        assert result.status == "retry_scheduled"
        assert result.attempt_number == 3
        events = await _events_for_intent(pool, record_id, intent_id)
        retries = _with_schema(events, "article_rag_vector_gc_retry_scheduled_v1")
        latest = retries[-1]["payload_json"]
        assert latest["attempt_number"] == 3
        expected_available_at = fixed_now + timedelta(seconds=120)
        assert datetime.fromisoformat(latest["available_at"]) == expected_available_at

    async def test_crash_after_delete_before_completion_recovers_idempotently(
        self, gc_env: dict
    ) -> None:
        """Rows deleted, completion not written -> next pass completes no_vectors."""
        pool, user_id, record_id, _, intent_id = await _full_deleted_env(gc_env)
        # Crash analog: the deleter removes rows from its shared store and
        # then dies with an unexpected (non-typed) exception BEFORE the
        # completion event is written.  The intent stays pending; the next
        # pass re-enumerates, finds nothing, and completes no_vectors.
        store = {"deleted_already": False}

        class _CrashyDeleter:
            def __init__(self) -> None:
                self.calls = 0

            async def delete_for_stable_document(
                self, *, collection: str, stable_document_id: UUID
            ) -> ArticleRagVectorDeletionResult:
                self.calls += 1
                if store["deleted_already"]:
                    return ArticleRagVectorDeletionResult(
                        outcome="no_vectors",
                        discovered_chunk_count=0,
                        deleted_chunk_count=0,
                        delete_call_count=0,
                    )
                store["deleted_already"] = True
                raise RuntimeError("simulated crash after delete")

        crashy = _CrashyDeleter()
        service = _build_service(pool, deleter=crashy)
        with pytest.raises(RuntimeError, match="simulated crash"):
            await service.process_next_due_intent()

        second = await service.process_next_due_intent()
        assert second is not None
        assert second.status == "completed"
        assert second.outcome == "no_vectors"
        assert crashy.calls == 2

        events = await _events_for_intent(pool, record_id, intent_id)
        completed = _with_schema(events, "article_rag_vector_gc_completed_v1")
        assert len(completed) == 1
        assert completed[0]["payload_json"]["outcome"] == "no_vectors"

    async def test_two_concurrent_services_exactly_one_processes(
        self, gc_env: dict
    ) -> None:
        pool, user_id, record_id, _, intent_id = await _full_deleted_env(gc_env)
        deleter_a = _FakeDeleter()
        deleter_b = _FakeDeleter()
        service_a = _build_service(pool, deleter=deleter_a)
        service_b = _build_service(pool, deleter=deleter_b)

        results = await asyncio.gather(
            service_a.process_next_due_intent(),
            service_b.process_next_due_intent(),
        )

        statuses = [r.status if r is not None else None for r in results]
        assert statuses.count("completed") == 1
        assert statuses.count(None) == 1
        total_delete_calls = len(deleter_a.calls) + len(deleter_b.calls)
        assert total_delete_calls == 1

        events = await _events_for_intent(pool, record_id, intent_id)
        completed = _with_schema(events, "article_rag_vector_gc_completed_v1")
        assert len(completed) == 1

    async def test_unconfigured_deleter_retries_without_network(
        self, gc_env: dict
    ) -> None:
        pool, user_id, record_id, _, intent_id = await _full_deleted_env(gc_env)
        service = _build_service(pool, deleter=UnconfiguredArticleRagVectorDeleter())

        result = await service.process_next_due_intent()

        assert result is not None
        assert result.status == "retry_scheduled"
        assert result.failure_code == "vector_deleter_unconfigured"
        events = await _events_for_intent(pool, record_id, intent_id)
        retries = _with_schema(events, "article_rag_vector_gc_retry_scheduled_v1")
        assert len(retries) == 1
        assert retries[0]["payload_json"]["failure_code"] == "vector_deleter_unconfigured"


# ===========================================================================
# Payload hygiene
# ===========================================================================


class TestPayloadHygiene:
    async def test_events_contain_only_safe_fields(self, gc_env: dict) -> None:
        pool, user_id, record_id, _, intent_id = await _full_deleted_env(gc_env)
        from app.services.reader_orchestration.event_runtime import ReaderEventRuntime

        await ReaderEventRuntime(pool=pool).publish_event(
            record_id=record_id,
            event_type="record_state_changed",
            payload_json={
                "event_schema": "article_rag_vector_gc_retry_scheduled_v1",
                "intent_event_id": str(intent_id),
                "attempt_number": 1,
                "failure_code": "active_index_run_present",
                "available_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
            },
        )
        deleter = _FakeDeleter()
        service = _build_service(pool, deleter=deleter)

        result = await service.process_next_due_intent()
        assert result is not None and result.status == "completed"

        events = await _events_for_intent(pool, record_id, intent_id)
        assert events, "expected outcome events for the intent"
        forbidden = {"chunk_id", "chunk_ids", "stable_document_id", "collection",
                     "token", "uri", "api_key", "user_content", "text", "error"}
        allowed_completed = {
            "event_schema", "intent_event_id", "outcome",
            "stable_document_count", "discovered_chunk_count",
            "deleted_chunk_count", "completed_at",
        }
        allowed_retry = {
            "event_schema", "intent_event_id", "attempt_number",
            "failure_code", "available_at",
        }
        allowed_terminal = {
            "event_schema", "intent_event_id", "failure_code", "failed_at",
        }
        for event in events:
            payload = event["payload_json"]
            assert forbidden.isdisjoint(payload.keys()), payload.keys()
            schema = payload["event_schema"]
            if schema == "article_rag_vector_gc_completed_v1":
                assert set(payload.keys()) == allowed_completed
            elif schema == "article_rag_vector_gc_retry_scheduled_v1":
                assert set(payload.keys()) == allowed_retry
            elif schema == "article_rag_vector_gc_failed_terminal_v1":
                assert set(payload.keys()) == allowed_terminal
