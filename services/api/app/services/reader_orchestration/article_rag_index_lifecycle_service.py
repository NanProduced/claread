"""D6-I4S: Article RAG Index Lifecycle Coordinator.

Coordinates the *trigger* and *status query* for Article RAG index
builds without owning the embedding / vector write path.

Design
------
This service is a thin **coordination layer** that sits above the
existing ``ArticleRagIndexBootstrapService``.  It does NOT:

  * call any embedding provider (DashScope / 百炼)
  * call any vector store (Zilliz / Milvus)
  * write chunk text / embedding vectors
  * add API routes or publish events
  * duplicate plan / job SQL — it delegates to the bootstrap service

The **index truth** comes exclusively from the Stable Document /
Canonical Text / Reading Units / Anchor Segments layer.  Plate /
Markdown / DOM / Slate / UI projections are **not** RAG truth and are
never read or projected by this service.

Two entry points
----------------
``ensure_article_rag_index_job_in_transaction``
    Caller-managed-transaction trigger.  Validates that the record is
    ``article_ready`` with a non-null ``active_base_id`` and the
    expected generation, then delegates to
    ``bootstrap_article_rag_index_in_transaction``.  Returns a typed
    result so the caller can switch on ``status`` / ``reason_code``
    without catching exceptions.

``load_article_rag_index_lifecycle_status``
    Read-only status query.  Does NOT write, does NOT lock rows, and
    NEVER reads chunk text / vector payload.  Performs a consistency
    check between the current active base / stable document and the
    latest index run so a stale run (whose base_id /
    stable_document_id / generation no longer match the active base)
    is reported as ``superseded_or_stale`` regardless of the run's
    own status (queued / indexing / indexed / failed / superseded).

Transaction model
-----------------
``ensure_*`` requires an active transaction on ``conn`` and fails
closed otherwise.  ``load_*`` is read-only and safe under autocommit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from app.services.reader_orchestration.article_rag_index_bootstrap import (
    ARTICLE_RAG_INDEX_BUILD_JOB_TYPE,
    DEFAULT_INDEX_VERSION,
    ArticleRagIndexBootstrapError,
    ArticleRagIndexBootstrapService,
)

if TYPE_CHECKING:
    import asyncpg

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# readiness_state values that mean the record's truth layer is ready
# for RAG indexing.  ``article_ready`` is the primary state; the
# bootstrap's plan service will additionally validate the stable
# document / base / generation.
_READY_READINESS_STATES: frozenset[str] = frozenset({"article_ready"})

# reader_article_rag_index_runs.status values that mean the index is
# actionable (not dead).
_ACTIVE_INDEX_RUN_STATUSES: frozenset[str] = frozenset(
    {"planned", "queued", "indexing", "indexed"}
)

# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

# ``ensure`` result status values:
#   enqueued            — fresh bootstrap created index run + job
#   idempotent_noop     — same plan already active, no new job
#   not_ready           — record not article_ready
#   no_active_base      — active_base_id is NULL
#   generation_mismatch — expected_generation != record.generation
#   record_not_found    — wrong user / deleted / inactive
#   plan_hash_mismatch  — bootstrap detected plan hash drift
#   bootstrap_inconsistent — existing run has dead/inconsistent job
#   error               — unexpected bootstrap failure
#
# NOTE: ``chunker_version_mismatch`` was removed because validating it
# *after* bootstrap would violate the "not enqueue" contract — the
# bootstrap's fresh path has already written index_runs / reader_runs /
# reader_jobs by the time it returns.  The bootstrap plan service is the
# authority on chunker_version.

ENSURE_STATUS_ENQUEUED = "enqueued"
ENSURE_STATUS_IDEMPOTENT_NOOP = "idempotent_noop"
ENSURE_STATUS_NOT_READY = "not_ready"
ENSURE_STATUS_NO_ACTIVE_BASE = "no_active_base"
ENSURE_STATUS_GENERATION_MISMATCH = "generation_mismatch"
ENSURE_STATUS_RECORD_NOT_FOUND = "record_not_found"
ENSURE_STATUS_PLAN_HASH_MISMATCH = "plan_hash_mismatch"
ENSURE_STATUS_BOOTSTRAP_INCONSISTENT = "bootstrap_inconsistent"
ENSURE_STATUS_ERROR = "error"


@dataclass(frozen=True, slots=True)
class ArticleRagIndexEnsureResult:
    """Typed result of :meth:`ensure_article_rag_index_job_in_transaction`.

    ``status`` is the high-level outcome the caller can switch on.
    ``reason_code`` is a finer-grained stable identifier.  Fields like
    ``stable_document_id`` / ``base_id`` / ``index_run_id`` / ``job_id``
    are populated only on the ``enqueued`` / ``idempotent_noop`` paths;
    they are ``None`` on every non-enqueue path.
    """

    reading_record_id: UUID
    status: str
    reason_code: str
    idempotent_noop: bool
    stable_document_id: UUID | None = None
    base_id: UUID | None = None
    record_generation: int | None = None
    index_run_id: UUID | None = None
    job_id: UUID | None = None
    index_version: str | None = None
    chunker_version: str | None = None


# ``status`` query result status values:
#   not_ready          — record not article_ready or no active base / stable doc
#   not_indexed        — ready but no index run exists
#   queued             — index run status planned/queued
#   indexing           — index run status indexing
#   indexed            — index run status indexed AND matches current active base
#   failed             — index run status failed
#   superseded_or_stale — index run indexed but base/stable/generation mismatch,
#                         or index run status superseded
#   unavailable        — defensive fallback (e.g. record not found)

STATUS_NOT_READY = "not_ready"
STATUS_NOT_INDEXED = "not_indexed"
STATUS_QUEUED = "queued"
STATUS_INDEXING = "indexing"
STATUS_INDEXED = "indexed"
STATUS_FAILED = "failed"
STATUS_SUPERSEDED_OR_STALE = "superseded_or_stale"
STATUS_UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class ArticleRagIndexLifecycleStatus:
    """Read-only lifecycle status for an Article RAG index.

    No chunk text, embedding vector, Plate JSON, Markdown syntax,
    DOM selection, or Slate path is ever present in this dataclass.
    Only truth-layer identifiers, hashes, counts, and status strings.
    """

    reading_record_id: UUID
    user_id: UUID
    status: str
    stable_document_id: UUID | None = None
    base_id: UUID | None = None
    record_generation: int | None = None
    index_run_id: UUID | None = None
    index_version: str | None = None
    plan_content_sha256: str | None = None
    chunk_count: int | None = None
    reason_code: str | None = None


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ArticleRagIndexLifecycleService:
    """Coordinate Article RAG index trigger + status query.

    This service is a coordination layer — it does NOT call embedding
    providers or vector stores.  It delegates the actual job creation
    to :class:`ArticleRagIndexBootstrapService` and adds:

      * ``article_ready`` / ``active_base_id`` / ``expected_generation``
        pre-validation so callers get a typed reason instead of a
        bare ``ArticleRagIndexPlanError`` when the record isn't ready.
      * A read-only status query that detects stale / superseded
        index runs.
    """

    def __init__(
        self,
        *,
        bootstrap_service: ArticleRagIndexBootstrapService | None = None,
    ) -> None:
        self._bootstrap_service = bootstrap_service or ArticleRagIndexBootstrapService()

    # -----------------------------------------------------------------
    # ensure
    # -----------------------------------------------------------------

    async def ensure_article_rag_index_job_in_transaction(
        self,
        conn: asyncpg.Connection,
        *,
        reading_record_id: UUID,
        user_id: UUID,
        expected_generation: int,
        index_version: str = DEFAULT_INDEX_VERSION,
        chunker_version: str | None = None,
        now: datetime | None = None,
    ) -> ArticleRagIndexEnsureResult:
        """Validate readiness, then delegate to bootstrap.

        Fails closed (typed result, no job created) when:
          * ``conn`` is not in a transaction
          * record not found / wrong user / deleted / inactive
          * record not ``article_ready``
          * ``active_base_id`` is NULL
          * ``expected_generation`` != actual ``generation``
          * bootstrap raises ``plan_hash_mismatch``
          * bootstrap raises ``idempotent_run_inconsistent``

        ``chunker_version`` is accepted for API compatibility but is NOT
        validated here.  Validating it *after* bootstrap would violate the
        "not enqueue" contract, because the bootstrap's fresh path has
        already inserted ``reader_article_rag_index_runs`` /
        ``reader_runs`` / ``reader_jobs`` before returning.  The bootstrap
        plan service is the authority on ``chunker_version``; callers
        that need to assert a specific version must do so before invoking
        this method (or assert it against the returned ``chunker_version``
        field on the success paths).

        Returns ``enqueued`` or ``idempotent_noop`` on success.
        """
        # 1. Transaction guard.
        if not conn.is_in_transaction():
            return ArticleRagIndexEnsureResult(
                reading_record_id=reading_record_id,
                status=ENSURE_STATUS_ERROR,
                reason_code="caller_transaction_required",
                idempotent_noop=False,
            )

        # 2. Read record with ownership + lifecycle check.
        record_row = await conn.fetchrow(
            """
            SELECT generation, active_base_id, readiness_state
            FROM reading_records
            WHERE id = $1
              AND user_id = $2
              AND deleted_at IS NULL
              AND lifecycle_status = 'active'
            """,
            reading_record_id,
            user_id,
        )
        if record_row is None:
            return ArticleRagIndexEnsureResult(
                reading_record_id=reading_record_id,
                status=ENSURE_STATUS_RECORD_NOT_FOUND,
                reason_code="record_not_found",
                idempotent_noop=False,
            )

        actual_generation = int(record_row["generation"])
        active_base_id_raw = record_row["active_base_id"]
        readiness_state = str(record_row["readiness_state"])

        # 3. Generation check (optimistic concurrency).
        if actual_generation != expected_generation:
            return ArticleRagIndexEnsureResult(
                reading_record_id=reading_record_id,
                status=ENSURE_STATUS_GENERATION_MISMATCH,
                reason_code="generation_mismatch",
                idempotent_noop=False,
                record_generation=actual_generation,
            )

        # 4. Readiness check.
        if readiness_state not in _READY_READINESS_STATES:
            return ArticleRagIndexEnsureResult(
                reading_record_id=reading_record_id,
                status=ENSURE_STATUS_NOT_READY,
                reason_code="record_not_article_ready",
                idempotent_noop=False,
                record_generation=actual_generation,
            )

        # 5. Active base check.
        if active_base_id_raw is None:
            return ArticleRagIndexEnsureResult(
                reading_record_id=reading_record_id,
                status=ENSURE_STATUS_NO_ACTIVE_BASE,
                reason_code="active_base_id_is_null",
                idempotent_noop=False,
                record_generation=actual_generation,
            )

        # 6. Delegate to bootstrap.
        try:
            bootstrap_result = (
                await self._bootstrap_service.bootstrap_article_rag_index_in_transaction(
                    conn,
                    reading_record_id=reading_record_id,
                    user_id=user_id,
                    index_version=index_version,
                    now=now,
                )
            )
        except ArticleRagIndexBootstrapError as exc:
            return self._translate_bootstrap_error(
                exc,
                reading_record_id=reading_record_id,
            )

        # 7. Translate bootstrap result → ensure result.
        #
        # NOTE: chunker_version is intentionally NOT validated here.
        # Bootstrap has already written index_runs / reader_runs /
        # reader_jobs by this point, so a mismatch returned as a
        # "no-enqueue" result would contradict the dataclass contract
        # that non-enqueue paths must not populate IDs.  The bootstrap
        # plan service is the authority on chunker_version.
        if bootstrap_result.idempotent_noop:
            return ArticleRagIndexEnsureResult(
                reading_record_id=reading_record_id,
                status=ENSURE_STATUS_IDEMPOTENT_NOOP,
                reason_code="idempotent_noop",
                idempotent_noop=True,
                stable_document_id=bootstrap_result.stable_document_id,
                base_id=bootstrap_result.base_id,
                record_generation=bootstrap_result.record_generation,
                index_run_id=bootstrap_result.index_run_id,
                job_id=bootstrap_result.job_id,
                index_version=bootstrap_result.index_version,
                chunker_version=bootstrap_result.chunker_version,
            )

        return ArticleRagIndexEnsureResult(
            reading_record_id=reading_record_id,
            status=ENSURE_STATUS_ENQUEUED,
            reason_code="enqueued",
            idempotent_noop=False,
            stable_document_id=bootstrap_result.stable_document_id,
            base_id=bootstrap_result.base_id,
            record_generation=bootstrap_result.record_generation,
            index_run_id=bootstrap_result.index_run_id,
            job_id=bootstrap_result.job_id,
            index_version=bootstrap_result.index_version,
            chunker_version=bootstrap_result.chunker_version,
        )

    # -----------------------------------------------------------------
    # status
    # -----------------------------------------------------------------

    async def load_article_rag_index_lifecycle_status(
        self,
        conn: asyncpg.Connection,
        *,
        reading_record_id: UUID,
        user_id: UUID,
        index_version: str = DEFAULT_INDEX_VERSION,
    ) -> ArticleRagIndexLifecycleStatus:
        """Read-only lifecycle status query.

        Does NOT write, does NOT lock rows, and NEVER reads chunk
        text / vector payload.  Performs a consistency check between
        the current active base / stable document and the latest
        index run so a stale run is reported as
        ``superseded_or_stale`` regardless of the run's own status
        (queued / indexing / indexed / failed / superseded).
        """
        # 1. Ownership + lifecycle check.
        record_row = await conn.fetchrow(
            """
            SELECT generation, active_base_id, readiness_state
            FROM reading_records
            WHERE id = $1
              AND user_id = $2
              AND deleted_at IS NULL
              AND lifecycle_status = 'active'
            """,
            reading_record_id,
            user_id,
        )
        if record_row is None:
            return ArticleRagIndexLifecycleStatus(
                reading_record_id=reading_record_id,
                user_id=user_id,
                status=STATUS_UNAVAILABLE,
                reason_code="record_not_found",
            )

        actual_generation = int(record_row["generation"])
        active_base_id_raw = record_row["active_base_id"]
        readiness_state = str(record_row["readiness_state"])

        # 2. Readiness check.
        if readiness_state not in _READY_READINESS_STATES:
            return ArticleRagIndexLifecycleStatus(
                reading_record_id=reading_record_id,
                user_id=user_id,
                status=STATUS_NOT_READY,
                record_generation=actual_generation,
                reason_code="record_not_article_ready",
            )

        if active_base_id_raw is None:
            return ArticleRagIndexLifecycleStatus(
                reading_record_id=reading_record_id,
                user_id=user_id,
                status=STATUS_NOT_READY,
                record_generation=actual_generation,
                reason_code="active_base_id_is_null",
            )

        active_base_id = UUID(str(active_base_id_raw))

        # 3. Active stable document check.
        stable_row = await conn.fetchrow(
            """
            SELECT id, record_generation
            FROM stable_reading_documents
            WHERE reading_record_id = $1
              AND status = 'active'
            """,
            reading_record_id,
        )
        if stable_row is None:
            return ArticleRagIndexLifecycleStatus(
                reading_record_id=reading_record_id,
                user_id=user_id,
                status=STATUS_NOT_READY,
                base_id=active_base_id,
                record_generation=actual_generation,
                reason_code="no_active_stable_document",
            )

        stable_document_id = UUID(str(stable_row["id"]))
        stable_generation = int(stable_row["record_generation"])
        if stable_generation != actual_generation:
            return ArticleRagIndexLifecycleStatus(
                reading_record_id=reading_record_id,
                user_id=user_id,
                status=STATUS_NOT_READY,
                stable_document_id=stable_document_id,
                base_id=active_base_id,
                record_generation=actual_generation,
                reason_code="stable_generation_mismatch",
            )

        # 4. Latest index run for this stable_document + index_version.
        #    Only read truth-layer identifiers / hashes / counts —
        #    NEVER chunk text / embedding / vector payload.
        index_row = await conn.fetchrow(
            """
            SELECT id, base_id, record_generation, stable_document_id,
                   plan_content_sha256, chunk_count, status, index_version
            FROM reader_article_rag_index_runs
            WHERE stable_document_id = $1
              AND index_version = $2
            ORDER BY created_at DESC
            LIMIT 1
            """,
            stable_document_id,
            index_version,
        )
        if index_row is None:
            return ArticleRagIndexLifecycleStatus(
                reading_record_id=reading_record_id,
                user_id=user_id,
                status=STATUS_NOT_INDEXED,
                stable_document_id=stable_document_id,
                base_id=active_base_id,
                record_generation=actual_generation,
                reason_code="no_index_run",
            )

        run_status = str(index_row["status"])
        run_base_id = (
            UUID(str(index_row["base_id"]))
            if index_row["base_id"] is not None
            else None
        )
        run_generation = int(index_row["record_generation"])
        run_stable_document_id = UUID(str(index_row["stable_document_id"]))

        # 5. Stale consistency check — applies to ALL statuses.
        #
        # A stale queued / indexing / failed run can be reported as
        # current work for the active base if the guard only runs on
        # ``indexed``.  The guard must therefore run *before* status-
        # specific mapping so any status whose base_id /
        # stable_document_id / generation no longer matches the
        # active base is reported as ``superseded_or_stale`` rather
        # than as the run's own status.
        if (
            run_base_id != active_base_id
            or run_stable_document_id != stable_document_id
            or run_generation != actual_generation
        ):
            return ArticleRagIndexLifecycleStatus(
                reading_record_id=reading_record_id,
                user_id=user_id,
                status=STATUS_SUPERSEDED_OR_STALE,
                stable_document_id=stable_document_id,
                base_id=active_base_id,
                record_generation=actual_generation,
                index_run_id=UUID(str(index_row["id"])),
                index_version=str(index_row["index_version"]),
                plan_content_sha256=str(index_row["plan_content_sha256"]),
                chunk_count=int(index_row["chunk_count"]),
                reason_code="index_run_base_or_generation_mismatch",
            )

        # 6. Map index run status → lifecycle status.
        if run_status == "failed":
            return ArticleRagIndexLifecycleStatus(
                reading_record_id=reading_record_id,
                user_id=user_id,
                status=STATUS_FAILED,
                stable_document_id=stable_document_id,
                base_id=active_base_id,
                record_generation=actual_generation,
                index_run_id=UUID(str(index_row["id"])),
                index_version=str(index_row["index_version"]),
                plan_content_sha256=str(index_row["plan_content_sha256"]),
                chunk_count=int(index_row["chunk_count"]),
                reason_code="index_run_failed",
            )

        if run_status == "superseded":
            return ArticleRagIndexLifecycleStatus(
                reading_record_id=reading_record_id,
                user_id=user_id,
                status=STATUS_SUPERSEDED_OR_STALE,
                stable_document_id=stable_document_id,
                base_id=active_base_id,
                record_generation=actual_generation,
                index_run_id=UUID(str(index_row["id"])),
                index_version=str(index_row["index_version"]),
                plan_content_sha256=str(index_row["plan_content_sha256"]),
                chunk_count=int(index_row["chunk_count"]),
                reason_code="index_run_superseded",
            )

        if run_status in ("planned", "queued"):
            return ArticleRagIndexLifecycleStatus(
                reading_record_id=reading_record_id,
                user_id=user_id,
                status=STATUS_QUEUED,
                stable_document_id=stable_document_id,
                base_id=active_base_id,
                record_generation=actual_generation,
                index_run_id=UUID(str(index_row["id"])),
                index_version=str(index_row["index_version"]),
                plan_content_sha256=str(index_row["plan_content_sha256"]),
                chunk_count=int(index_row["chunk_count"]),
                reason_code="index_run_queued",
            )

        if run_status == "indexing":
            return ArticleRagIndexLifecycleStatus(
                reading_record_id=reading_record_id,
                user_id=user_id,
                status=STATUS_INDEXING,
                stable_document_id=stable_document_id,
                base_id=active_base_id,
                record_generation=actual_generation,
                index_run_id=UUID(str(index_row["id"])),
                index_version=str(index_row["index_version"]),
                plan_content_sha256=str(index_row["plan_content_sha256"]),
                chunk_count=int(index_row["chunk_count"]),
                reason_code="index_run_indexing",
            )

        if run_status == "indexed":
            # Stale consistency check has already run above (step 5) —
            # any indexed run reaching here matches the current active
            # base / stable document / generation and is therefore current.
            return ArticleRagIndexLifecycleStatus(
                reading_record_id=reading_record_id,
                user_id=user_id,
                status=STATUS_INDEXED,
                stable_document_id=stable_document_id,
                base_id=active_base_id,
                record_generation=actual_generation,
                index_run_id=UUID(str(index_row["id"])),
                index_version=str(index_row["index_version"]),
                plan_content_sha256=str(index_row["plan_content_sha256"]),
                chunk_count=int(index_row["chunk_count"]),
                reason_code="indexed",
            )

        # Defensive fallback for unknown status values.
        return ArticleRagIndexLifecycleStatus(
            reading_record_id=reading_record_id,
            user_id=user_id,
            status=STATUS_UNAVAILABLE,
            stable_document_id=stable_document_id,
            base_id=active_base_id,
            record_generation=actual_generation,
            index_run_id=UUID(str(index_row["id"])),
            index_version=str(index_row["index_version"]),
            plan_content_sha256=str(index_row["plan_content_sha256"]),
            chunk_count=int(index_row["chunk_count"]),
            reason_code="unknown_index_run_status",
        )

    # -----------------------------------------------------------------
    # helpers
    # -----------------------------------------------------------------

    @staticmethod
    def _translate_bootstrap_error(
        exc: ArticleRagIndexBootstrapError,
        *,
        reading_record_id: UUID,
    ) -> ArticleRagIndexEnsureResult:
        """Map ``ArticleRagIndexBootstrapError.reason_code`` to an
        ``ArticleRagIndexEnsureResult`` status."""
        reason_code = exc.reason_code or "bootstrap_failed"
        if reason_code == "plan_hash_mismatch":
            status = ENSURE_STATUS_PLAN_HASH_MISMATCH
        elif reason_code == "idempotent_run_inconsistent":
            status = ENSURE_STATUS_BOOTSTRAP_INCONSISTENT
        elif reason_code == "caller_transaction_required":
            status = ENSURE_STATUS_ERROR
        else:
            status = ENSURE_STATUS_ERROR
        return ArticleRagIndexEnsureResult(
            reading_record_id=reading_record_id,
            status=status,
            reason_code=reason_code,
            idempotent_noop=False,
        )


__all__ = [
    "ARTICLE_RAG_INDEX_BUILD_JOB_TYPE",
    "ArticleRagIndexEnsureResult",
    "ArticleRagIndexLifecycleService",
    "ArticleRagIndexLifecycleStatus",
    # ensure status constants
    "ENSURE_STATUS_BOOTSTRAP_INCONSISTENT",
    "ENSURE_STATUS_ENQUEUED",
    "ENSURE_STATUS_ERROR",
    "ENSURE_STATUS_GENERATION_MISMATCH",
    "ENSURE_STATUS_IDEMPOTENT_NOOP",
    "ENSURE_STATUS_NO_ACTIVE_BASE",
    "ENSURE_STATUS_NOT_READY",
    "ENSURE_STATUS_PLAN_HASH_MISMATCH",
    "ENSURE_STATUS_RECORD_NOT_FOUND",
    # status constants
    "STATUS_FAILED",
    "STATUS_INDEXED",
    "STATUS_INDEXING",
    "STATUS_NOT_INDEXED",
    "STATUS_NOT_READY",
    "STATUS_QUEUED",
    "STATUS_SUPERSEDED_OR_STALE",
    "STATUS_UNAVAILABLE",
]
