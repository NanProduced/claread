"""Reading Record soft-delete application service (Wave 8 B2).

Minimal transaction coordinator for ``DELETE /reader/records/{id}``:

1. Lock the ``reading_records`` row (id + user_id; soft-deleted rows are
   readable so repeat deletes stay idempotent).
2. First delete: ``deleted_at`` / ``lifecycle_status='deleted'`` /
   ``product_state='deleted'`` / ``updated_at = deleted_at``.
3. Converge non-terminal execution state in the SAME transaction:
   - ``reader_jobs`` queued/claimed/retry_later/paused -> ``cancelled``
     (via the ReaderJobRuntime administrative seam, one
     ``job_cancelled`` event per job);
   - ``reader_runs`` non-terminal -> ``cancelled``;
   - ``reader_article_rag_index_runs`` planned/queued/indexing/indexed
     -> ``superseded`` with a fixed, safe error reason.
4. Persist the Vector GC intent as exactly one
   ``reading_record_deleted_v1`` ``record_state_changed`` reader event
   (reader_events doubles as the simplified outbox; Wave 9 consumes it).

No physical deletes: parsing data, audit rows, and related assets are
retained. No vector-store I/O happens here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import asyncpg

from app.database import connection as db_connection
from app.database.json_compat import jsonb_param
from app.services.reader_orchestration.event_runtime import ReaderEventRuntime
from app.services.reader_orchestration.job_runtime import ReaderJobRuntime

DELETION_EVENT_SCHEMA = "reading_record_deleted_v1"
DELETION_EVENT_REASON_CODE = "user_removed_reading_record"
JOB_CANCELLATION_RATIONALE_CODE = "reading_record_deleted"
INDEX_RUN_FAILURE_CODE = "reading_record_deleted"
INDEX_RUN_RATIONALE_CODE = "user_deleted_record"

_NON_TERMINAL_RUN_STATUSES = (
    "queued",
    "running",
    "waiting_user",
    "waiting_quota",
    "paused",
    "failed_retryable",
)
_SUPERSEDABLE_INDEX_RUN_STATUSES = ("planned", "queued", "indexing", "indexed")


@dataclass(frozen=True, slots=True)
class ReadingRecordDeletionResult:
    record_id: UUID
    status: str
    deleted_at: datetime
    vector_gc_intent_recorded: bool


class ReadingRecordDeletionService:
    """Owns the single-transaction delete lifecycle.

    No interface / factory: routes construct it directly, tests inject a
    pool. Job convergence delegates to ``ReaderJobRuntime``; the GC
    intent delegates to ``ReaderEventRuntime`` — this class only
    coordinates.
    """

    def __init__(
        self,
        *,
        pool: asyncpg.Pool | None = None,
        job_runtime: ReaderJobRuntime | None = None,
        event_runtime: ReaderEventRuntime | None = None,
    ) -> None:
        self._pool = pool
        self._job_runtime = job_runtime or ReaderJobRuntime(pool=pool)
        self._event_runtime = event_runtime or ReaderEventRuntime(pool=pool)

    def get_pool(self) -> asyncpg.Pool:
        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("Database pool not initialized")
        return pool

    async def delete_record(
        self,
        *,
        record_id: UUID,
        user_id: UUID,
    ) -> ReadingRecordDeletionResult | None:
        """Soft-delete the record or accept an existing soft delete.

        Returns ``None`` when the record does not exist or belongs to
        another user (callers surface a uniform 404); otherwise the
        idempotent result including the retained/intended GC state.
        """
        pool = self.get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                locked = await conn.fetchrow(
                    """
                    SELECT deleted_at, generation
                    FROM reading_records
                    WHERE id = $1 AND user_id = $2
                    FOR UPDATE
                    """,
                    record_id,
                    user_id,
                )
                if locked is None:
                    return None

                if locked["deleted_at"] is not None:
                    # Idempotent repeat: keep the first deleted_at, no
                    # re-convergence. Legacy soft-deleted rows that
                    # predate the intent contract get it backfilled
                    # under the same lock.
                    deleted_at = locked["deleted_at"]
                    await self._ensure_deletion_intent(
                        conn,
                        record_id=record_id,
                        user_id=user_id,
                        deleted_at=deleted_at,
                        record_generation=int(locked["generation"]),
                    )
                    return ReadingRecordDeletionResult(
                        record_id=record_id,
                        status="already_deleted",
                        deleted_at=deleted_at,
                        vector_gc_intent_recorded=True,
                    )

                deleted_row = await conn.fetchrow(
                    """
                    UPDATE reading_records
                    SET deleted_at = NOW(),
                        lifecycle_status = 'deleted',
                        product_state = 'deleted',
                        updated_at = NOW()
                    WHERE id = $1
                    RETURNING deleted_at, generation
                    """,
                    record_id,
                )
                if deleted_row is None:
                    raise RuntimeError(
                        f"reading_records row {record_id} vanished under lock"
                    )
                deleted_at = deleted_row["deleted_at"]
                record_generation = int(deleted_row["generation"])

                jobs_cancelled = (
                    await self._job_runtime
                    .administrative_cancel_in_transaction(
                        conn,
                        record_id=record_id,
                        rationale_code=JOB_CANCELLATION_RATIONALE_CODE,
                        updated_at=deleted_at,
                    )
                )
                runs_cancelled = await self._cancel_non_terminal_runs(
                    conn,
                    record_id=record_id,
                    cancelled_at=deleted_at,
                )
                index_runs_superseded = await self._supersede_index_runs(
                    conn,
                    record_id=record_id,
                    superseded_at=deleted_at,
                )

                await self._event_runtime.publish_event_in_transaction(
                    conn,
                    record_id=record_id,
                    event_type="record_state_changed",
                    payload_json=self._deletion_payload(
                        user_id=user_id,
                        deleted_at=deleted_at,
                        record_generation=record_generation,
                        jobs_cancelled=jobs_cancelled,
                        runs_cancelled=runs_cancelled,
                        index_runs_superseded=index_runs_superseded,
                    ),
                )
                return ReadingRecordDeletionResult(
                    record_id=record_id,
                    status="deleted",
                    deleted_at=deleted_at,
                    vector_gc_intent_recorded=True,
                )

    # ------------------------------------------------------------------
    # Convergence helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _cancel_non_terminal_runs(
        conn: asyncpg.Connection,
        *,
        record_id: UUID,
        cancelled_at: datetime,
    ) -> int:
        rows = await conn.fetch(
            """
            UPDATE reader_runs
            SET status = 'cancelled',
                finished_at = COALESCE(finished_at, $2),
                updated_at = $2
            WHERE reading_record_id = $1
              AND status = ANY($3::text[])
            RETURNING id
            """,
            record_id,
            cancelled_at,
            list(_NON_TERMINAL_RUN_STATUSES),
        )
        return len(rows)

    @staticmethod
    async def _supersede_index_runs(
        conn: asyncpg.Connection,
        *,
        record_id: UUID,
        superseded_at: datetime,
    ) -> int:
        rows = await conn.fetch(
            """
            UPDATE reader_article_rag_index_runs
            SET status = 'superseded',
                completed_at = COALESCE(completed_at, $2),
                updated_at = $2,
                error_json = error_json || $3::jsonb
            WHERE reading_record_id = $1
              AND status = ANY($4::text[])
            RETURNING id
            """,
            record_id,
            superseded_at,
            jsonb_param(
                {
                    "failure_code": INDEX_RUN_FAILURE_CODE,
                    "rationale_code": INDEX_RUN_RATIONALE_CODE,
                }
            ),
            list(_SUPERSEDABLE_INDEX_RUN_STATUSES),
        )
        return len(rows)

    # ------------------------------------------------------------------
    # GC intent
    # ------------------------------------------------------------------

    async def _ensure_deletion_intent(
        self,
        conn: asyncpg.Connection,
        *,
        record_id: UUID,
        user_id: UUID,
        deleted_at: datetime,
        record_generation: int,
    ) -> None:
        existing = await conn.fetchval(
            """
            SELECT id
            FROM reader_events
            WHERE reading_record_id = $1
              AND event_type = 'record_state_changed'
              AND payload_json->>'event_schema' = $2
            LIMIT 1
            """,
            record_id,
            DELETION_EVENT_SCHEMA,
        )
        if existing is not None:
            return
        await self._event_runtime.publish_event_in_transaction(
            conn,
            record_id=record_id,
            event_type="record_state_changed",
            payload_json=self._deletion_payload(
                user_id=user_id,
                deleted_at=deleted_at,
                record_generation=record_generation,
                jobs_cancelled=0,
                runs_cancelled=0,
                index_runs_superseded=0,
            ),
        )

    @staticmethod
    def _deletion_payload(
        *,
        user_id: UUID,
        deleted_at: datetime,
        record_generation: int,
        jobs_cancelled: int,
        runs_cancelled: int,
        index_runs_superseded: int,
    ) -> dict[str, object]:
        """Fixed Wave 9 GC-intent contract.

        Never includes titles, chunk text, filenames, raw URLs, provider
        errors, or secrets — only identity, timestamps, and counts.
        """
        return {
            "event_schema": DELETION_EVENT_SCHEMA,
            "operation": "soft_deleted",
            "reason_code": DELETION_EVENT_REASON_CODE,
            "actor_user_id": str(user_id),
            "deleted_at": deleted_at.isoformat(),
            "record_generation": record_generation,
            "article_rag_vector_gc_requested": True,
            "transition_counts": {
                "jobs_cancelled": jobs_cancelled,
                "runs_cancelled": runs_cancelled,
                "index_runs_superseded": index_runs_superseded,
            },
        }
