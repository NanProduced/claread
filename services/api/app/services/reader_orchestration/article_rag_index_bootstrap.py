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
from app.services.reader_orchestration.article_rag_index_profile import (
    DEFAULT_ARTICLE_RAG_INDEX_VERSION,
    ArticleRagIndexProfileResolutionError,
    resolve_article_rag_index_profile,
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
# Alias to the P1-B resolver's canonical V1 index version so bootstrap
# and resolver share a single source of truth for the V1 identity string.
DEFAULT_INDEX_VERSION = DEFAULT_ARTICLE_RAG_INDEX_VERSION
DEFAULT_ARTICLE_RAG_INDEX_MAX_ATTEMPTS = 3

# Job statuses that mean the job will never execute. If an active index
# run references a job in one of these statuses, the index is silently
# stuck and the bootstrap service must fail closed rather than return
# an idempotent no-op.
_DEAD_JOB_STATUSES = frozenset({"failed_terminal", "cancelled", "superseded"})

# Index-run statuses considered "execution-active": the worker may
# still pick up the associated job and consume its ``input_json`` /
# ``input_hash``.  For these rows the bootstrap idempotency path
# enforces the 3-layer profile_fingerprint freeze (index-run column +
# ``reader_jobs.input_json.profile_fingerprint`` +
# ``reader_jobs.input_hash``).  ``indexed`` is intentionally excluded:
# a terminal ``indexed`` row no longer enters worker execution and may
# carry a legacy pre-P1-C job payload.
_EXECUTION_ACTIVE_INDEX_RUN_STATUSES = frozenset({"planned", "queued", "indexing"})


def _compute_article_rag_index_build_input_hash(
    *,
    stable_document_id: UUID,
    base_id: UUID,
    plan_content_sha256: str,
    index_version: str,
    profile_fingerprint: str,
) -> str:
    """Compute the canonical ``reader_jobs.input_hash`` for an
    ``article_rag_index_build`` job under the P1-C algorithm.

    The digest covers ``stable_document_id``, ``base_id``,
    ``plan_content_sha256``, ``index_version``, and
    ``profile_fingerprint``.  The fingerprint is appended last so the
    P1-C field is additive over the pre-P1-C prefix bytes.
    """
    return hashlib.sha256(
        (
            f"{stable_document_id}:"
            f"{base_id}:"
            f"{plan_content_sha256}:"
            f"{index_version}:"
            f"{profile_fingerprint}"
        ).encode()
    ).hexdigest()


# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------


class ArticleRagIndexBootstrapError(ValueError):
    """Typed error for bootstrap failures (validation, fence, idempotency).

    ``reason_code`` is a stable identifier the caller can switch on:
      * ``caller_transaction_required`` — conn not in a transaction
      * ``index_profile_unregistered`` — index_version did not resolve
        to a registered Article RAG profile; no index-run / job / run
        was written
      * ``index_profile_chunker_mismatch`` — resolved profile's
        chunker_version does not match the plan builder's chunker_version
      * ``index_profile_fingerprint_mismatch`` — existing active index
        run has a profile_fingerprint different from the current
        resolution; the existing row must not be reused or overwritten
      * ``plan_hash_mismatch`` — existing active run has a different plan
      * ``idempotent_run_inconsistent`` — existing active run has a null
        job_id / reader_run_id, missing job / run row, mismatched fields,
        or a dead job status; the index is silently stuck
      * ``idempotent_run_profile_freeze_mismatch`` — existing
        execution-active run has a ``reader_jobs`` payload whose
        ``input_json.profile_fingerprint`` or ``input_hash`` does not
        match the resolved profile / current P1-C hash algorithm; the
        bootstrap refuses to reuse, overwrite, or repair the existing
        row (caller must drain / fix manually)
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

    ``profile_fingerprint`` is the SHA-256 fingerprint of the canonical
    ArticleRagIndexProfile resolved by the P1-B resolver for the
    ``index_version`` passed to bootstrap.  It is identical across the
    index-run row, ``reader_jobs.input_json``, and the
    ``reader_jobs.input_hash`` digest for this bootstrap.
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
    profile_fingerprint: str
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

        # P1-C: resolve the Article RAG profile for the requested
        # index_version through the P1-B resolver BEFORE any DB write
        # or plan construction.  Unknown / blank / whitespace-padded /
        # non-string / malicious index_version fails closed here with a
        # fixed local message; the offending input is never echoed,
        # and no index-run / job / run row is written.  The resolver
        # also returns the canonical profile_fingerprint that this
        # bootstrap must freeze into the index-run column, the job
        # input_json, and the job input_hash.
        try:
            resolution = resolve_article_rag_index_profile(index_version)
        except ArticleRagIndexProfileResolutionError as exc:
            raise ArticleRagIndexBootstrapError(
                "Article RAG index profile is not registered; "
                "cannot bootstrap index-run.",
                reason_code="index_profile_unregistered",
            ) from exc
        profile = resolution.profile
        profile_fingerprint = resolution.profile_fingerprint

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

        # P1-C: plan compatibility guard.  The bootstrap must not
        # parameterise the plan builder (V1 plan stays the canonical
        # plan), but AFTER the plan is built it must verify that the
        # plan's chunker_version equals the resolved profile's
        # chunker_version.  A mismatch means the V1 plan builder and
        # the V1 profile have drifted apart — fail closed without
        # inserting any index-run / job / run row.
        if plan.chunker_version != profile.chunker_version:
            raise ArticleRagIndexBootstrapError(
                "Article RAG index plan chunker_version does not match "
                "the resolved profile chunker_version; refusing to "
                "bootstrap index-run.",
                reason_code="index_profile_chunker_mismatch",
            )

        # 3. Check for existing active index run (idempotency).
        #    P1-C: the SELECT also reads profile_fingerprint so the
        #    idempotency guard can verify the existing row was frozen
        #    with the same canonical profile as the current resolution.
        existing = await conn.fetchrow(
            """
            SELECT id, plan_content_sha256, chunk_count,
                   profile_fingerprint, job_id, reader_run_id, status
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
            existing_profile_fingerprint = (
                str(existing["profile_fingerprint"])
                if existing["profile_fingerprint"] is not None
                else None
            )
            existing_status = str(existing["status"])
            # P1-C: fingerprint guard runs BEFORE the plan-sha / chunk
            # count comparison.  A fingerprint mismatch means the
            # existing row was frozen under a different canonical
            # profile — fail closed without reusing, overwriting, or
            # auto-correcting the existing row.  The fingerprint value
            # is never echoed in the error message.
            if existing_profile_fingerprint != profile_fingerprint:
                raise ArticleRagIndexBootstrapError(
                    "Existing active index run has a profile_fingerprint "
                    "that does not match the resolved profile; refusing "
                    "to reuse or overwrite the existing row.",
                    reason_code="index_profile_fingerprint_mismatch",
                )
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
                # contract.  P1-C rework: also fetch ``input_json`` and
                # ``input_hash`` so the 3-layer freeze guard below can
                # verify the job-layer profile_fingerprint without an
                # extra round-trip.
                job_row = await conn.fetchrow(
                    """
                    SELECT status, reading_record_id, base_id, user_id,
                           job_type, target_key,
                           input_json, input_hash
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

                # P1-C rework Problem B: 3-layer profile_fingerprint
                # freeze guard for execution-active existing runs.
                # The migration backfills the index-run column only;
                # the associated ``reader_jobs.input_json`` /
                # ``input_hash`` may carry a legacy pre-P1-C payload
                # (missing fingerprint / old hash algorithm).  Such a
                # half-frozen active run must NOT be reused as an
                # idempotent no-op, because a strict worker consuming
                # the job payload would fail.  The bootstrap refuses
                # to repair, overwrite, or recreate the existing row —
                # the caller must drain / fix manually.
                #
                # This guard only applies to execution-active statuses
                # (``planned`` / ``queued`` / ``indexing``).  A
                # terminal ``indexed`` row no longer enters worker
                # execution and may legitimately carry a legacy
                # pre-P1-C job payload (migration cannot rewrite
                # historical succeeded jobs).
                #
                # The error message is a FIXED LOCAL STRING.  It must
                # not echo the persisted or expected fingerprint,
                # input_hash, job payload, row id, or any database
                # content.  Sentinel values used by tests must not
                # leak through str / repr / traceback / __cause__.
                if existing_status in _EXECUTION_ACTIVE_INDEX_RUN_STATUSES:
                    existing_job_input_json = job_row["input_json"]
                    existing_job_input_hash = job_row["input_hash"]
                    existing_job_fp = None
                    if isinstance(existing_job_input_json, dict):
                        existing_job_fp = existing_job_input_json.get(
                            "profile_fingerprint"
                        )
                    expected_input_hash = _compute_article_rag_index_build_input_hash(
                        stable_document_id=plan.stable_document_id,
                        base_id=plan.base_id,
                        plan_content_sha256=plan_content_sha256,
                        index_version=index_version,
                        profile_fingerprint=profile_fingerprint,
                    )
                    if (
                        existing_job_fp != profile_fingerprint
                        or existing_job_input_hash != expected_input_hash
                    ):
                        raise ArticleRagIndexBootstrapError(
                            "Existing execution-active index run has a "
                            "reader_jobs payload whose "
                            "input_json.profile_fingerprint or "
                            "input_hash does not match the resolved "
                            "profile / current P1-C hash algorithm; "
                            "refusing to reuse, overwrite, or repair "
                            "the existing row.",
                            reason_code="idempotent_run_profile_freeze_mismatch",
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
                    profile_fingerprint=profile_fingerprint,
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
        #    P1-C: profile_fingerprint is the durable freeze of the
        #    canonical ArticleRagIndexProfile resolved by the P1-B
        #    resolver.  It must be identical to the value frozen into
        #    reader_jobs.input_json and reader_jobs.input_hash below.
        index_run_id = await conn.fetchval(
            """
            INSERT INTO reader_article_rag_index_runs (
                reading_record_id, stable_document_id, base_id,
                record_generation,
                stable_document_content_sha256, canonical_text_sha256,
                plan_content_sha256, chunk_count,
                status, index_version, chunker_version,
                profile_fingerprint,
                created_at, updated_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8,
                    'queued', $9, $10, $11, $12, $12)
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
            profile_fingerprint,
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
        # P1-C: input_hash MUST cover profile_fingerprint so the digest
        # reflects the canonical profile frozen into this job.  The
        # fingerprint is appended after index_version so the new field
        # is additive and does not shift the existing prefix bytes.
        input_hash = _compute_article_rag_index_build_input_hash(
            stable_document_id=plan.stable_document_id,
            base_id=plan.base_id,
            plan_content_sha256=plan_content_sha256,
            index_version=index_version,
            profile_fingerprint=profile_fingerprint,
        )

        # Job payload: only IDs and run params.  No chunk text, no
        # Plate JSON, no Markdown syntax, no DOM / Slate / UI fields.
        # P1-C: profile_fingerprint is the canonical SHA-256 of the
        # ArticleRagIndexProfile; it carries no model settings, API
        # key, URI, token, or chunk / article text.  The worker is
        # permitted to ignore this field this round; consumption /
        # verification is deferred to the next round.
        input_json = {
            "source": "article_rag_index_bootstrap",
            "reading_record_id": str(reading_record_id),
            "stable_document_id": str(plan.stable_document_id),
            "base_id": str(plan.base_id),
            "record_generation": plan.record_generation,
            "index_run_id": str(index_run_id),
            "index_version": index_version,
            "chunker_version": plan.chunker_version,
            "profile_fingerprint": profile_fingerprint,
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
            profile_fingerprint=profile_fingerprint,
            job_id=job_row["id"],
            job_status=job_row["status"],
            idempotent_noop=False,
        )
