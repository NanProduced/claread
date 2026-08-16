"""Article RAG vector-GC service (Wave 9).

Consumes the Wave 8 ``reading_record_deleted_v1`` GC intents persisted in
``reader_events`` and safely deletes the matching Article RAG vectors via
the exact-id deleter.  ``reader_events`` is the single durable fact source
for intent, retry scheduling, and terminal outcomes — no new tables, no
outbox, no job framework.

State machine per intent:

* pending intent + terminal outcome -> never processed again.
* retry event with ``available_at`` in the future -> skipped.
* record not deleted / active index run / active build job ->
  ``article_rag_vector_gc_retry_scheduled_v1``, zero vector I/O.
* unsupported provider / collection mismatch / malformed identity ->
  ``article_rag_vector_gc_failed_terminal_v1`` before any vector I/O.
* all identities deleted -> ``article_rag_vector_gc_completed_v1`` with
  ``outcome="deleted"``; nothing to delete -> ``outcome="no_vectors"``.

Concurrency: a PostgreSQL session advisory lock keyed by intent event id
serializes workers; a second advisory lock keyed by ``stable_document_id``
serializes GC vs. the index writer's vector upserts.  Both locks are
re-validated inside the processing flow, and every retry event is
re-derived from history (deterministic exponential backoff).

Safety: events, logs, and exception DTOs carry only fixed failure codes,
safe counts, and the intent event id — never user content, chunk ids,
stable document ids, collection names, URIs/tokens, or SDK raw text.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

import asyncpg

from app.contracts.article_rag_contract import ARTICLE_RAG_EMBEDDING_CONTRACT
from app.database import connection as db_connection

from .advisory_lock import (
    LOCK_NAMESPACE_VECTOR_GC_INTENT,
    LOCK_NAMESPACE_VECTOR_MUTATION,
    SessionAdvisoryLock,
    advisory_lock_key,
)
from .article_rag_vector_deleter import (
    READER_ARTICLE_RAG_VECTOR_PROVIDER_ZILLIZ,
    ArticleRagVectorDeleter,
    ArticleRagVectorDeletionError,
    UnconfiguredArticleRagVectorDeleter,
)
from .event_runtime import ReaderEventRuntime

logger = logging.getLogger(__name__)

# Event schema discriminators (all under event_type='record_state_changed').
GC_INTENT_SCHEMA = "reading_record_deleted_v1"
GC_COMPLETED_SCHEMA = "article_rag_vector_gc_completed_v1"
GC_RETRY_SCHEMA = "article_rag_vector_gc_retry_scheduled_v1"
GC_FAILED_TERMINAL_SCHEMA = "article_rag_vector_gc_failed_terminal_v1"

# Deterministic exponential backoff bounds.  Fixed — deliberately not
# configurable: the GC must never stop retrying a pending intent because
# of a small retry cap (provider/config outages recover).
# ponytail: retry events grow unboundedly per never-quiescent intent.
# Upgrade to a delivery ledger only when real ops data shows event
# growth is a problem — the reader_events outcome index keeps the scan
# cheap today.
DEFAULT_GC_BACKOFF_BASE = timedelta(seconds=30)
DEFAULT_GC_BACKOFF_MAX = timedelta(hours=1)

# Retryable qualification failure codes.
FAILURE_CODE_RECORD_NOT_DELETED = "record_not_deleted"
FAILURE_CODE_ACTIVE_INDEX_RUN_PRESENT = "active_index_run_present"
FAILURE_CODE_ACTIVE_BUILD_JOB_PRESENT = "active_build_job_present"

# Active statuses that block GC (a superseded/failed run is quiescent).
_ACTIVE_INDEX_RUN_STATUSES = ("planned", "queued", "indexing", "indexed")
# Terminal job statuses (anything else blocks GC).
_JOB_TERMINAL_STATUSES = (
    "succeeded",
    "failed_terminal",
    "cancelled",
    "superseded",
)

_DUE_INTENT_SQL = """
    SELECT e.id, e.reading_record_id
    FROM reader_events e
    WHERE e.event_type = 'record_state_changed'
      AND e.payload_json ->> 'event_schema' = $1
      AND e.payload_json ->> 'article_rag_vector_gc_requested' = 'true'
      AND NOT EXISTS (
          SELECT 1 FROM reader_events o
          WHERE o.event_type = 'record_state_changed'
            AND o.payload_json ->> 'intent_event_id' = e.id::text
            AND o.payload_json ->> 'event_schema' IN ($2, $4)
      )
      AND NOT EXISTS (
          SELECT 1 FROM reader_events r
          WHERE r.event_type = 'record_state_changed'
            AND r.payload_json ->> 'intent_event_id' = e.id::text
            AND r.payload_json ->> 'event_schema' = $3
            AND (r.payload_json ->> 'available_at')::timestamptz > NOW()
      )
    ORDER BY e.created_at, e.id
    LIMIT 1
"""

_TERMINAL_OUTCOME_SCHEMAS = (GC_COMPLETED_SCHEMA, GC_FAILED_TERMINAL_SCHEMA)


@dataclass(frozen=True, slots=True)
class ArticleRagVectorGcResult:
    """Outcome of one ``process_next_due_intent`` call."""

    intent_event_id: UUID
    status: Literal["completed", "retry_scheduled", "failed_terminal", "skipped"]
    failure_code: str | None = None
    attempt_number: int | None = None
    outcome: str | None = None
    stable_document_count: int = 0
    discovered_chunk_count: int = 0
    deleted_chunk_count: int = 0


class ArticleRagVectorGcService:
    """Owns the one-intent-per-call vector-GC loop for the RAG worker."""

    def __init__(
        self,
        *,
        pool: asyncpg.Pool | None = None,
        deleter: ArticleRagVectorDeleter | None = None,
        configured_provider: str = READER_ARTICLE_RAG_VECTOR_PROVIDER_ZILLIZ,
        configured_collection: str = ARTICLE_RAG_EMBEDDING_CONTRACT.vector_collection,
        backoff_base: timedelta = DEFAULT_GC_BACKOFF_BASE,
        backoff_max: timedelta = DEFAULT_GC_BACKOFF_MAX,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._pool = pool
        self._deleter = deleter or UnconfiguredArticleRagVectorDeleter()
        self._event_runtime = ReaderEventRuntime(pool=pool)
        self._configured_provider = configured_provider
        self._configured_collection = configured_collection
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max
        self._clock = clock or (lambda: datetime.now(UTC))

    def get_pool(self) -> asyncpg.Pool:
        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("Database pool not initialized")
        return pool

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def process_next_due_intent(self) -> ArticleRagVectorGcResult | None:
        """Process at most one due vector-GC intent.

        Returns ``None`` when no intent is due or another worker already
        holds the intent lock.
        """
        pool = self.get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                _DUE_INTENT_SQL,
                GC_INTENT_SCHEMA,
                GC_COMPLETED_SCHEMA,
                GC_RETRY_SCHEMA,
                GC_FAILED_TERMINAL_SCHEMA,
            )
        if row is None:
            return None

        intent_event_id = UUID(str(row["id"]))
        record_id = UUID(str(row["reading_record_id"]))
        intent_key = advisory_lock_key(LOCK_NAMESPACE_VECTOR_GC_INTENT, intent_event_id)

        async with pool.acquire() as intent_conn:
            intent_lock = SessionAdvisoryLock(intent_conn, intent_key)
            if not await intent_lock.try_acquire():
                return None
            try:
                return await self._process_locked_intent(
                    record_id=record_id,
                    intent_event_id=intent_event_id,
                )
            finally:
                await intent_lock.unlock()

    # ------------------------------------------------------------------
    # Locked intent processing
    # ------------------------------------------------------------------

    async def _process_locked_intent(
        self,
        *,
        record_id: UUID,
        intent_event_id: UUID,
    ) -> ArticleRagVectorGcResult | None:
        # Re-read and re-validate the intent AFTER taking the intent lock:
        # another worker may have completed or terminalized it meanwhile,
        # or its retry window may no longer be due.
        if not await self._intent_still_pending(record_id, intent_event_id):
            return None

        violation = await self._quiescence_violation(record_id)
        if violation is not None:
            return await self._write_retry(
                record_id, intent_event_id, violation
            )

        try:
            identities = await self._collect_identities(record_id)
        except ArticleRagVectorDeletionError as exc:
            if exc.retryable:
                return await self._write_retry(
                    record_id, intent_event_id, exc.failure_code
                )
            return await self._write_terminal(
                record_id, intent_event_id, exc.failure_code
            )
        if not identities:
            # No committed index runs ever wrote vectors for this record.
            return await self._write_completed(
                record_id=record_id,
                intent_event_id=intent_event_id,
                outcome="no_vectors",
                stable_document_count=0,
                discovered_chunk_count=0,
                deleted_chunk_count=0,
            )

        # Validate ALL identities before any vector I/O (fail-closed).
        for _, provider, collection in identities:
            if provider != self._configured_provider:
                return await self._write_terminal(
                    record_id, intent_event_id, "unsupported_provider"
                )
            if collection != self._configured_collection:
                return await self._write_terminal(
                    record_id, intent_event_id, "collection_mismatch"
                )

        # Delete each identity one by one under its mutation lock.
        pool = self.get_pool()
        discovered_total = 0
        deleted_total = 0
        for stable_document_id, _, collection in identities:
            mutation_key = advisory_lock_key(
                LOCK_NAMESPACE_VECTOR_MUTATION, stable_document_id
            )
            async with pool.acquire() as mut_conn:
                mutation_lock = SessionAdvisoryLock(mut_conn, mutation_key)
                try:
                    await mutation_lock.acquire()
                    # Re-verify quiescence while holding the mutation lock:
                    # a writer that crossed the fence before us must not be
                    # racing anymore.
                    recheck = await self._quiescence_violation(
                        record_id, conn=mut_conn
                    )
                    if recheck is not None:
                        return await self._write_retry(
                            record_id, intent_event_id, recheck
                        )
                    try:
                        result = await self._deleter.delete_for_stable_document(
                            collection=collection,
                            stable_document_id=stable_document_id,
                        )
                    except ArticleRagVectorDeletionError as exc:
                        if exc.retryable:
                            return await self._write_retry(
                                record_id, intent_event_id, exc.failure_code
                            )
                        return await self._write_terminal(
                            record_id, intent_event_id, exc.failure_code
                        )
                finally:
                    await mutation_lock.unlock()
            discovered_total += result.discovered_chunk_count
            deleted_total += result.deleted_chunk_count

        outcome: Literal["deleted", "no_vectors"] = (
            "deleted" if deleted_total > 0 else "no_vectors"
        )
        return await self._write_completed(
            record_id=record_id,
            intent_event_id=intent_event_id,
            outcome=outcome,
            stable_document_count=len(identities),
            discovered_chunk_count=discovered_total,
            deleted_chunk_count=deleted_total,
        )

    # ------------------------------------------------------------------
    # Verification helpers
    # ------------------------------------------------------------------

    async def _intent_still_pending(
        self,
        record_id: UUID,
        intent_event_id: UUID,
    ) -> bool:
        pool = self.get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT 1
                FROM reader_events
                WHERE id = $1
                  AND reading_record_id = $2
                  AND event_type = 'record_state_changed'
                  AND payload_json ->> 'event_schema' = $3
                  AND payload_json ->> 'article_rag_vector_gc_requested' = 'true'
                  AND NOT EXISTS (
                      SELECT 1 FROM reader_events o
                      WHERE o.event_type = 'record_state_changed'
                        AND o.payload_json ->> 'intent_event_id' = $1::text
                        AND o.payload_json ->> 'event_schema' = ANY($4::text[])
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM reader_events r
                      WHERE r.event_type = 'record_state_changed'
                        AND r.payload_json ->> 'intent_event_id' = $1::text
                        AND r.payload_json ->> 'event_schema' = $5
                        AND (r.payload_json ->> 'available_at')::timestamptz > NOW()
                  )
                """,
                intent_event_id,
                record_id,
                GC_INTENT_SCHEMA,
                list(_TERMINAL_OUTCOME_SCHEMAS),
                GC_RETRY_SCHEMA,
            )
            return row is not None

    async def _quiescence_violation(
        self,
        record_id: UUID,
        *,
        conn: asyncpg.Connection | None = None,
    ) -> str | None:
        """Return the first blocking condition, or None when quiescent.

        When ``conn`` is supplied the checks run inside a short read-only
        transaction on that connection (used under the mutation lock);
        otherwise a fresh connection is used.
        """
        pool = self.get_pool()

        async def _check(c: asyncpg.Connection) -> str | None:
            record = await c.fetchrow(
                """
                SELECT deleted_at
                FROM reading_records
                WHERE id = $1
                """,
                record_id,
            )
            if record is None or record["deleted_at"] is None:
                return FAILURE_CODE_RECORD_NOT_DELETED
            active_run = await c.fetchval(
                """
                SELECT 1
                FROM reader_article_rag_index_runs
                WHERE reading_record_id = $1
                  AND status = ANY($2::text[])
                LIMIT 1
                """,
                record_id,
                list(_ACTIVE_INDEX_RUN_STATUSES),
            )
            if active_run:
                return FAILURE_CODE_ACTIVE_INDEX_RUN_PRESENT
            active_job = await c.fetchval(
                """
                SELECT 1
                FROM reader_jobs
                WHERE reading_record_id = $1
                  AND job_type = 'article_rag_index_build'
                  AND status NOT IN (SELECT unnest($2::text[]))
                LIMIT 1
                """,
                record_id,
                list(_JOB_TERMINAL_STATUSES),
            )
            if active_job:
                return FAILURE_CODE_ACTIVE_BUILD_JOB_PRESENT
            return None

        if conn is not None:
            async with conn.transaction(readonly=True):
                return await _check(conn)
        async with pool.acquire() as fresh_conn:
            async with fresh_conn.transaction(readonly=True):
                return await _check(fresh_conn)

    async def _collect_identities(
        self,
        record_id: UUID,
    ) -> list[tuple[UUID, str, str]]:
        """Distinct ``(stable_document_id, provider, collection)`` targets.

        Collected from ALL retained index runs of the record (the audit
        history).  A run with NULL provider/collection never completed a
        committed vector write in the indexed path — but a run superseded
        from ``indexing`` may still have uncommitted leftover rows from
        the pre-Wave-9 in-flight window, so its ``stable_document_id`` is
        kept and the single-path contract identity is inferred.
        ponytail: inference is safe because the Article RAG index is one
        path — the worker validates the frozen contract collection before
        every upsert, so leftover rows can only exist in the configured
        collection.  If a second collection ever ships, identity-less
        runs must fail closed instead of inferring.
        """
        pool = self.get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT DISTINCT stable_document_id,
                       vector_store_provider, vector_collection
                FROM reader_article_rag_index_runs
                WHERE reading_record_id = $1
                ORDER BY stable_document_id
                """,
                record_id,
            )
        identities: list[tuple[UUID, str, str]] = []
        for row in rows:
            stable_value = row["stable_document_id"]
            if not isinstance(stable_value, UUID):
                # Defence in depth: the FK guarantees a UUID, but a
                # malformed identity must never reach a delete filter.
                raise ArticleRagVectorDeletionError(
                    "Article RAG vector GC collected a malformed stable "
                    "document identity",
                    retryable=False,
                    failure_code="malformed_identity",
                    failure_class="malformed_identity",
                )
            provider = row["vector_store_provider"] or self._configured_provider
            collection = row["vector_collection"] or self._configured_collection
            identities.append((stable_value, str(provider), str(collection)))
        return identities

    # ------------------------------------------------------------------
    # Event writers
    # ------------------------------------------------------------------

    async def _retry_attempt_number(
        self,
        record_id: UUID,
        intent_event_id: UUID,
    ) -> int:
        pool = self.get_pool()
        async with pool.acquire() as conn:
            count = await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM reader_events
                WHERE reading_record_id = $1
                  AND event_type = 'record_state_changed'
                  AND payload_json ->> 'event_schema' = $2
                  AND payload_json ->> 'intent_event_id' = $3
                """,
                record_id,
                GC_RETRY_SCHEMA,
                str(intent_event_id),
            )
        return int(count or 0) + 1

    async def _write_retry(
        self,
        record_id: UUID,
        intent_event_id: UUID,
        failure_code: str,
    ) -> ArticleRagVectorGcResult:
        attempt_number = await self._retry_attempt_number(record_id, intent_event_id)
        delay = min(
            self._backoff_base * (2 ** (attempt_number - 1)),
            self._backoff_max,
        )
        available_at = self._clock() + delay
        await self._event_runtime.publish_event(
            record_id=record_id,
            event_type="record_state_changed",
            payload_json={
                "event_schema": GC_RETRY_SCHEMA,
                "intent_event_id": str(intent_event_id),
                "attempt_number": attempt_number,
                "failure_code": failure_code,
                "available_at": available_at.isoformat(),
            },
        )
        logger.warning(
            "article RAG vector GC scheduled retry",
            extra={
                "intent_event_id": str(intent_event_id),
                "attempt_number": attempt_number,
                "failure_code": failure_code,
            },
        )
        return ArticleRagVectorGcResult(
            intent_event_id=intent_event_id,
            status="retry_scheduled",
            failure_code=failure_code,
            attempt_number=attempt_number,
        )

    async def _write_terminal(
        self,
        record_id: UUID,
        intent_event_id: UUID,
        failure_code: str,
    ) -> ArticleRagVectorGcResult:
        await self._event_runtime.publish_event(
            record_id=record_id,
            event_type="record_state_changed",
            payload_json={
                "event_schema": GC_FAILED_TERMINAL_SCHEMA,
                "intent_event_id": str(intent_event_id),
                "failure_code": failure_code,
                "failed_at": self._clock().isoformat(),
            },
        )
        logger.error(
            "article RAG vector GC failed terminal",
            extra={
                "intent_event_id": str(intent_event_id),
                "failure_code": failure_code,
            },
        )
        return ArticleRagVectorGcResult(
            intent_event_id=intent_event_id,
            status="failed_terminal",
            failure_code=failure_code,
        )

    async def _write_completed(
        self,
        *,
        record_id: UUID,
        intent_event_id: UUID,
        outcome: Literal["deleted", "no_vectors"],
        stable_document_count: int,
        discovered_chunk_count: int,
        deleted_chunk_count: int,
    ) -> ArticleRagVectorGcResult:
        await self._event_runtime.publish_event(
            record_id=record_id,
            event_type="record_state_changed",
            payload_json={
                "event_schema": GC_COMPLETED_SCHEMA,
                "intent_event_id": str(intent_event_id),
                "outcome": outcome,
                "stable_document_count": stable_document_count,
                "discovered_chunk_count": discovered_chunk_count,
                "deleted_chunk_count": deleted_chunk_count,
                "completed_at": self._clock().isoformat(),
            },
        )
        logger.info(
            "article RAG vector GC completed",
            extra={
                "intent_event_id": str(intent_event_id),
                "outcome": outcome,
                "stable_document_count": stable_document_count,
                "discovered_chunk_count": discovered_chunk_count,
                "deleted_chunk_count": deleted_chunk_count,
            },
        )
        return ArticleRagVectorGcResult(
            intent_event_id=intent_event_id,
            status="completed",
            outcome=outcome,
            stable_document_count=stable_document_count,
            discovered_chunk_count=discovered_chunk_count,
            deleted_chunk_count=deleted_chunk_count,
        )


__all__ = [
    "ArticleRagVectorGcResult",
    "ArticleRagVectorGcService",
    "GC_INTENT_SCHEMA",
    "GC_COMPLETED_SCHEMA",
    "GC_RETRY_SCHEMA",
    "GC_FAILED_TERMINAL_SCHEMA",
]
