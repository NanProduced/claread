"""D6-I4B: Article RAG Index Job Bootstrap + Index State Foundation.

Enqueues a base-scoped ``article_rag_index_build`` reader_job and
persists the index state row in ``reader_article_rag_index_runs``.

This service does NOT:
  * call any embedding provider
  * call Zilliz / Milvus / DashScope / 百炼向量服务
  * modify ArticleRagIndexPlanService truth / citation rules
  * add API routes
  * send events
  * write chunk text / Plate JSON / Markdown syntax / DOM selections /
    Slate paths into the index state row or the job payload

Transaction model: **caller-managed transaction**.  The caller MUST
hold an open transaction on ``conn``.  The bootstrap service inserts
the index state row + reader_run + reader_job within that transaction.
Any failure raises and the caller's transaction rolls back, preventing
half-bootstrapped state.

Idempotency: if an active index run already exists for the same
``(stable_document_id, index_version)`` with the same
``plan_content_sha256`` and ``chunk_count``, the service returns an
idempotent no-op result with ``idempotent_noop=True``.  If the
plan_content_sha256 differs, the service fails closed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import asyncpg

from app.database.json_compat import jsonb_param
from app.services.reader_orchestration.article_rag_index_plan import (
    ArticleRagIndexPlanService,
    compute_plan_content_sha256,
)
from app.services.reader_orchestration.repository import ReaderOrchestrationRepository

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ARTICLE_RAG_INDEX_BUILD_JOB_TYPE = "article_rag_index_build"
ARTICLE_RAG_INDEX_BUILD_RUN_TYPE = "article_rag_index_build"
ARTICLE_RAG_INDEX_BUILD_TARGET_TYPE = "record"
ARTICLE_RAG_INDEX_BUILD_TRIGGER_KIND = "system"
ARTICLE_RAG_INDEX_BUILD_POLICY_VERSION = "article_rag_index_bootstrap_v1"
DEFAULT_INDEX_VERSION = "article_rag_index_v1"
DEFAULT_ARTICLE_RAG_INDEX_MAX_ATTEMPTS = 3

# Job statuses that mean the job will never execute. If an active index
# run references a job in one of these statuses, the index is silently
# stuck and the bootstrap service must fail closed rather than return
# an idempotent no-op.
_DEAD_JOB_STATUSES = frozenset({"failed_terminal", "cancelled", "superseded"})


# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------


class ArticleRagIndexBootstrapError(ValueError):
    """Typed error for bootstrap failures (validation, fence, idempotency).

    ``reason_code`` is a stable identifier the caller can switch on:
      * ``caller_transaction_required`` — conn not in a transaction
      * ``plan_hash_mismatch`` — existing active run has a different plan
      * ``idempotent_run_inconsistent`` — existing active run has a null
        job_id / reader_run_id, missing job / run row, mismatched fields,
        or a dead job status; the index is silently stuck
      * ``bootstrap_failed`` — generic fallback
    """

    def __init__(
        self,
        message: str,
        *,
        reason_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code or "bootstrap_failed"


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ArticleRagIndexBootstrapResult:
    """Result of bootstrapping an article RAG index build.

    For a fresh bootstrap, ``job_id`` / ``job_status`` are populated
    from the newly inserted reader_job and ``idempotent_noop`` is
    ``False``.

    For an idempotent no-op (same plan_content_sha256 + chunk_count as
    an existing active run), ``job_id`` / ``job_status`` are queried
    from the existing reader_job (may be ``None`` if the prior
    transaction committed the index run but not the job linkage, which
    should not happen in practice but is handled defensively) and
    ``idempotent_noop`` is ``True``.
    """

    index_run_id: UUID
    reading_record_id: UUID
    stable_document_id: UUID
    base_id: UUID
    record_generation: int
    index_version: str
    chunker_version: str
    plan_content_sha256: str
    chunk_count: int
    job_id: UUID | None
    job_status: str | None
    idempotent_noop: bool


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ArticleRagIndexBootstrapService:
    """Bootstrap an article RAG index build job + persistent index state.

    The service is caller-managed-transaction: the caller opens a
    transaction on ``conn`` and calls
    :meth:`bootstrap_article_rag_index_in_transaction`.  A thin wrapper
    :meth:`bootstrap_article_rag_index` acquires its own connection +
    transaction for convenience.

    The service never calls embedding providers or vector stores.  It
    only:
      1. Validates ownership + active base / stable document (via the
         plan service's read-only checks).
      2. Builds the index plan (read-only).
      3. Checks for an existing active index run (idempotency).
      4. Inserts a ``reader_article_rag_index_runs`` row
         (status='queued').
      5. Enqueues a ``reader_runs`` + ``reader_jobs`` row
         (job_type='article_rag_index_build', base_id NOT NULL).
      6. Links the job_id / reader_run_id back to the index run row.
    """

    def __init__(
        self,
        *,
        pool: asyncpg.Pool | None = None,
        plan_service: ArticleRagIndexPlanService | None = None,
    ) -> None:
        self._pool = pool
        self._plan_service = plan_service or ArticleRagIndexPlanService(pool=pool)

    def _get_pool(self) -> asyncpg.Pool:
        if self._pool is not None:
            return self._pool
        return ReaderOrchestrationRepository().get_pool()

    async def bootstrap_article_rag_index(
        self,
        *,
        reading_record_id: UUID,
        user_id: UUID,
        index_version: str = DEFAULT_INDEX_VERSION,
        now: datetime | None = None,
    ) -> ArticleRagIndexBootstrapResult:
        """Convenience wrapper: acquire pool + transaction, delegate.

        Callers that already hold a connection / transaction (e.g.
        inside a larger orchestration flow) should call
        :meth:`bootstrap_article_rag_index_in_transaction` directly.
        """
        async with self._get_pool().acquire() as conn:
            async with conn.transaction():
                return await self.bootstrap_article_rag_index_in_transaction(
                    conn,
                    reading_record_id=reading_record_id,
                    user_id=user_id,
                    index_version=index_version,
                    now=now,
                )

    async def bootstrap_article_rag_index_in_transaction(
        self,
        conn: asyncpg.Connection,
        *,
        reading_record_id: UUID,
        user_id: UUID,
        index_version: str = DEFAULT_INDEX_VERSION,
        now: datetime | None = None,
    ) -> ArticleRagIndexBootstrapResult:
        """Caller-managed-transaction variant.

        The caller MUST hold an open transaction on ``conn``.  Fails
        closed with ``ArticleRagIndexBootstrapError`` if
        ``conn.is_in_transaction()`` is ``False``.
        """
        if not conn.is_in_transaction():
            raise ArticleRagIndexBootstrapError(
                "bootstrap_article_rag_index_in_transaction must be called "
                "within an active transaction. Refusing to execute outside "
                "a transaction to prevent half-bootstrapped state.",
                reason_code="caller_transaction_required",
            )

        now = now or datetime.now(UTC)

        # 1. Lock and validate reading_records (ownership check).
        #    FOR UPDATE serializes concurrent bootstrap calls for the
        #    same record, preventing race conditions on the idempotency
        #    check below.
        record_row = await conn.fetchrow(
            """
            SELECT generation, active_base_id
            FROM reading_records
            WHERE id = $1
              AND user_id = $2
              AND deleted_at IS NULL
              AND lifecycle_status = 'active'
            FOR UPDATE
            """,
            reading_record_id,
            user_id,
        )
        if record_row is None:
            raise LookupError(
                f"Reading record {reading_record_id} was not found "
                f"for user {user_id}."
            )

        # 2. Build plan (uses the same conn; reads participate in this
        #    transaction so the index state insert sees a consistent
        #    snapshot).
        #    The plan service already checks:
        #      - no active base → ArticleRagIndexPlanError
        #      - no active stable document → ArticleRagIndexPlanError
        #      - stale generation → ArticleRagIndexPlanError
        #      - no eligible chunks → ArticleRagIndexPlanError
        plan = await self._plan_service.build_index_plan_in_transaction(
            conn,
            record_id=reading_record_id,
            user_id=user_id,
        )

        plan_content_sha256 = compute_plan_content_sha256(plan)
        chunk_count = len(plan.chunks)

        # 3. Check for existing active index run (idempotency).
        existing = await conn.fetchrow(
            """
            SELECT id, plan_content_sha256, chunk_count, job_id, reader_run_id
            FROM reader_article_rag_index_runs
            WHERE stable_document_id = $1
              AND index_version = $2
              AND status IN ('planned', 'queued', 'indexing', 'indexed')
            LIMIT 1
            """,
            plan.stable_document_id,
            index_version,
        )

        if existing is not None:
            existing_plan_sha = str(existing["plan_content_sha256"])
            existing_chunk_count = int(existing["chunk_count"])
            if (
                existing_plan_sha == plan_content_sha256
                and existing_chunk_count == chunk_count
            ):
                # Same plan content + chunk count. Before declaring an
                # idempotent no-op, verify the existing run has a valid,
                # actionable job. A null job_id / reader_run_id, missing
                # job / run row, mismatched fields, or dead job status
                # means the index is silently stuck — fail closed rather
                # than letting the caller believe the index is queued.
                existing_job_id = existing["job_id"]
                existing_reader_run_id = existing["reader_run_id"]

                if existing_job_id is None or existing_reader_run_id is None:
                    raise ArticleRagIndexBootstrapError(
                        f"Existing active index run {existing['id']} for "
                        f"stable_document_id={plan.stable_document_id} "
                        f"index_version={index_version} has a null "
                        f"job_id or reader_run_id "
                        f"(job_id={existing_job_id}, "
                        f"reader_run_id={existing_reader_run_id}). "
                        f"The index is silently stuck. Refusing to "
                        f"return an idempotent no-op.",
                        reason_code="idempotent_run_inconsistent",
                    )

                # Verify the job row exists and matches the bootstrap
                # contract.
                job_row = await conn.fetchrow(
                    """
                    SELECT status, reading_record_id, base_id, user_id,
                           job_type, target_key
                    FROM reader_jobs
                    WHERE id = $1
                    """,
                    existing_job_id,
                )
                if job_row is None:
                    raise ArticleRagIndexBootstrapError(
                        f"Existing active index run {existing['id']} "
                        f"references job_id={existing_job_id} but the "
                        f"job row does not exist. The index is silently "
                        f"stuck.",
                        reason_code="idempotent_run_inconsistent",
                    )

                # Verify the run row exists and matches.
                run_row = await conn.fetchrow(
                    """
                    SELECT reading_record_id, user_id, run_type
                    FROM reader_runs
                    WHERE id = $1
                    """,
                    existing_reader_run_id,
                )
                if run_row is None:
                    raise ArticleRagIndexBootstrapError(
                        f"Existing active index run {existing['id']} "
                        f"references reader_run_id={existing_reader_run_id} "
                        f"but the run row does not exist. The index is "
                        f"silently stuck.",
                        reason_code="idempotent_run_inconsistent",
                    )

                # Verify job fields match the expected bootstrap contract.
                if (
                    str(job_row["reading_record_id"]) != str(reading_record_id)
                    or str(job_row["base_id"]) != str(plan.base_id)
                    or str(job_row["user_id"]) != str(user_id)
                    or str(job_row["job_type"]) != ARTICLE_RAG_INDEX_BUILD_JOB_TYPE
                    or str(job_row["target_key"]) != str(plan.stable_document_id)
                ):
                    raise ArticleRagIndexBootstrapError(
                        f"Existing active index run {existing['id']} "
                        f"references job_id={existing_job_id} but the "
                        f"job row fields do not match the bootstrap "
                        f"contract (reading_record_id / base_id / "
                        f"user_id / job_type / target_key). The index "
                        f"is silently stuck.",
                        reason_code="idempotent_run_inconsistent",
                    )

                # Verify run fields match.
                if (
                    str(run_row["reading_record_id"]) != str(reading_record_id)
                    or str(run_row["user_id"]) != str(user_id)
                    or str(run_row["run_type"]) != ARTICLE_RAG_INDEX_BUILD_RUN_TYPE
                ):
                    raise ArticleRagIndexBootstrapError(
                        f"Existing active index run {existing['id']} "
                        f"references reader_run_id={existing_reader_run_id} "
                        f"but the run row fields do not match the "
                        f"bootstrap contract (reading_record_id / "
                        f"user_id / run_type). The index is silently "
                        f"stuck.",
                        reason_code="idempotent_run_inconsistent",
                    )

                # Verify the job status is not dead. A dead job
                # (failed_terminal / cancelled / superseded) with an
                # active index run means the index is stuck — the job
                # will never execute.
                existing_job_status = str(job_row["status"])
                if existing_job_status in _DEAD_JOB_STATUSES:
                    raise ArticleRagIndexBootstrapError(
                        f"Existing active index run {existing['id']} "
                        f"references job_id={existing_job_id} but the "
                        f"job has a dead status "
                        f"'{existing_job_status}'. The index is "
                        f"silently stuck.",
                        reason_code="idempotent_run_inconsistent",
                    )

                return ArticleRagIndexBootstrapResult(
                    index_run_id=existing["id"],
                    reading_record_id=reading_record_id,
                    stable_document_id=plan.stable_document_id,
                    base_id=plan.base_id,
                    record_generation=plan.record_generation,
                    index_version=index_version,
                    chunker_version=plan.chunker_version,
                    plan_content_sha256=plan_content_sha256,
                    chunk_count=chunk_count,
                    job_id=existing_job_id,
                    job_status=existing_job_status,
                    idempotent_noop=True,
                )
            # Different plan_content_sha256 → fail closed.
            raise ArticleRagIndexBootstrapError(
                f"Existing active index run {existing['id']} for "
                f"stable_document_id={plan.stable_document_id} "
                f"index_version={index_version} has a different "
                f"plan_content_sha256 "
                f"(existing={existing_plan_sha}, "
                f"new={plan_content_sha256}). Refusing to shadow the "
                f"existing run. Supersede the existing run first.",
                reason_code="plan_hash_mismatch",
            )

        # 4. Insert index state row (status='queued').
        #    embedding_model / vector_store_provider / vector_collection
        #    are left NULL — D6-I4B does not call embedding / vector
        #    stores.
        index_run_id = await conn.fetchval(
            """
            INSERT INTO reader_article_rag_index_runs (
                reading_record_id, stable_document_id, base_id,
                record_generation,
                stable_document_content_sha256, canonical_text_sha256,
                plan_content_sha256, chunk_count,
                status, index_version, chunker_version,
                created_at, updated_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8,
                    'queued', $9, $10, $11, $11)
            RETURNING id
            """,
            reading_record_id,
            plan.stable_document_id,
            plan.base_id,
            plan.record_generation,
            plan.content_sha256,
            plan.canonical_text_sha256,
            plan_content_sha256,
            chunk_count,
            index_version,
            plan.chunker_version,
            now,
        )

        # 5. Enqueue reader_run + reader_job.
        #    operation_fingerprint includes index_version so re-indexing
        #    with a new index_version produces a distinct fingerprint
        #    (and thus a distinct entry in uq_reader_jobs_active_fingerprint).
        operation_fingerprint = f"article_rag_index_build_v1:{index_version}"
        idempotency_key = (
            f"article_rag_index_build_v1:"
            f"{plan.stable_document_id}:{index_version}"
        )
        input_hash = hashlib.sha256(
            (
                f"{plan.stable_document_id}:"
                f"{plan.base_id}:"
                f"{plan_content_sha256}:"
                f"{index_version}"
            ).encode("utf-8")
        ).hexdigest()

        # Job payload: only IDs and run params.  No chunk text, no
        # Plate JSON, no Markdown syntax, no DOM / Slate / UI fields.
        input_json = {
            "source": "article_rag_index_bootstrap",
            "reading_record_id": str(reading_record_id),
            "stable_document_id": str(plan.stable_document_id),
            "base_id": str(plan.base_id),
            "record_generation": plan.record_generation,
            "index_run_id": str(index_run_id),
            "index_version": index_version,
            "chunker_version": plan.chunker_version,
        }

        run_id = await conn.fetchval(
            """
            INSERT INTO reader_runs (
                reading_record_id, user_id, run_type, status,
                record_generation, envelope_json, policy_version,
                trigger_kind
            )
            VALUES ($1, $2, $3, 'queued', $4, $5::jsonb, $6, $7)
            RETURNING id
            """,
            reading_record_id,
            user_id,
            ARTICLE_RAG_INDEX_BUILD_RUN_TYPE,
            plan.record_generation,
            jsonb_param(input_json),
            ARTICLE_RAG_INDEX_BUILD_POLICY_VERSION,
            ARTICLE_RAG_INDEX_BUILD_TRIGGER_KIND,
        )

        job_row = await conn.fetchrow(
            """
            INSERT INTO reader_jobs (
                reading_record_id, base_id, run_id, user_id,
                job_type, target_type, target_key,
                status, priority, expected_generation,
                operation_fingerprint, idempotency_key,
                input_hash, input_json, max_attempts
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7,
                    'queued', 0, $8, $9, $10, $11, $12::jsonb, $13)
            RETURNING id, status
            """,
            reading_record_id,
            plan.base_id,
            run_id,
            user_id,
            ARTICLE_RAG_INDEX_BUILD_JOB_TYPE,
            ARTICLE_RAG_INDEX_BUILD_TARGET_TYPE,
            str(plan.stable_document_id),
            plan.record_generation,
            operation_fingerprint,
            idempotency_key,
            input_hash,
            jsonb_param(input_json),
            DEFAULT_ARTICLE_RAG_INDEX_MAX_ATTEMPTS,
        )

        # 6. Link job_id + reader_run_id back to the index run row.
        await conn.execute(
            """
            UPDATE reader_article_rag_index_runs
            SET job_id = $2, reader_run_id = $3, updated_at = $4
            WHERE id = $1
            """,
            index_run_id,
            job_row["id"],
            run_id,
            now,
        )

        return ArticleRagIndexBootstrapResult(
            index_run_id=index_run_id,
            reading_record_id=reading_record_id,
            stable_document_id=plan.stable_document_id,
            base_id=plan.base_id,
            record_generation=plan.record_generation,
            index_version=index_version,
            chunker_version=plan.chunker_version,
            plan_content_sha256=plan_content_sha256,
            chunk_count=chunk_count,
            job_id=job_row["id"],
            job_status=job_row["status"],
            idempotent_noop=False,
        )
