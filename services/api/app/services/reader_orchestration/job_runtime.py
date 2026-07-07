"""Reader job runtime skeleton.

Provides the minimal claim/heartbeat/transition/recovery helpers for
``reader_jobs`` on top of the D3 schema. PostgreSQL is the durable authority;
this module never calls LLMs, PydanticAI or LangGraph.

Key invariants enforced here:

- Claim uses ``SELECT FOR UPDATE SKIP LOCKED`` so concurrent workers never
  pick the same job.
- Claim writes ``lease_owner``, ``lease_token`` (UUID), ``lease_expires_at``,
  ``claimed_at`` and increments ``attempt_count``.
- Heartbeat and transitions from ``claimed`` require ``lease_token`` match
  and a non-expired lease.
- Base/generation fence is enforced at claim and at publish (transition to
  ``succeeded``): stale ``expected_generation``, non-active base, or missing
  ``base_id`` for jobs other than ``build_base``, ``input_artifact_extraction``,
  and ``extracted_artifact_materialization`` are rejected.
  ``input_artifact_extraction`` and ``extracted_artifact_materialization`` are
  also superseded if ``active_base_id`` is already set (they must run before
  any base exists).
- Stale claimed jobs (lease expired) are recovered to ``queued`` or
  ``failed_terminal`` when ``attempt_count >= max_attempts``.
- Transition helper rejects illegal status jumps per the contract state machine.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from app.database import connection as db_connection
from app.database.json_compat import jsonb_param
from app.services.reader_orchestration.span_recorder import (
    SPAN_KIND_CLAIM,
    current_span,
    derive_retry_class,
    get_default_recorder,
    parse_trace_id_from_envelope,
)

# ---------------------------------------------------------------------------
# Status constants
# ---------------------------------------------------------------------------

STATUS_QUEUED = "queued"
STATUS_CLAIMED = "claimed"
STATUS_RETRY_LATER = "retry_later"
STATUS_PAUSED = "paused"
STATUS_SKIPPED = "skipped"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED_TERMINAL = "failed_terminal"
STATUS_CANCELLED = "cancelled"
STATUS_SUPERSEDED = "superseded"

# Allowed target statuses for the public transition helper. ``claimed`` is
# intentionally excluded because claiming must go through ``claim_next_job``
# (which generates the lease token and increments ``attempt_count``).
# ``queued`` is only reachable via ``paused -> queued`` (resume) or
# ``recover_stale_leases``; it is validated against the state machine below.
TRANSITION_TARGETS: frozenset[str] = frozenset({
    STATUS_SUCCEEDED,
    STATUS_FAILED_TERMINAL,
    STATUS_RETRY_LATER,
    STATUS_PAUSED,
    STATUS_SKIPPED,
    STATUS_CANCELLED,
    STATUS_SUPERSEDED,
    STATUS_QUEUED,
})

# Full state machine per schema-and-domain-contract.md.
_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    STATUS_QUEUED: frozenset({
        STATUS_CLAIMED, STATUS_PAUSED, STATUS_SKIPPED,
        STATUS_CANCELLED, STATUS_SUPERSEDED,
    }),
    STATUS_CLAIMED: frozenset({
        STATUS_SUCCEEDED, STATUS_RETRY_LATER, STATUS_PAUSED, STATUS_SKIPPED,
        STATUS_FAILED_TERMINAL, STATUS_CANCELLED, STATUS_SUPERSEDED,
    }),
    STATUS_RETRY_LATER: frozenset({
        STATUS_CLAIMED, STATUS_PAUSED, STATUS_CANCELLED,
        STATUS_SUPERSEDED, STATUS_FAILED_TERMINAL,
    }),
    STATUS_PAUSED: frozenset({
        STATUS_QUEUED, STATUS_CANCELLED, STATUS_SUPERSEDED, STATUS_FAILED_TERMINAL,
    }),
    STATUS_SUCCEEDED: frozenset(),
    STATUS_FAILED_TERMINAL: frozenset(),
    STATUS_CANCELLED: frozenset(),
    STATUS_SUPERSEDED: frozenset(),
    STATUS_SKIPPED: frozenset(),
}

# Transitions that clear lease fields (everything except -> claimed).
_LEASE_CLEARING_TARGETS: frozenset[str] = TRANSITION_TARGETS

_EVENT_TYPE_FOR_TRANSITION: dict[str, str] = {
    STATUS_SUCCEEDED: "job_succeeded",
    STATUS_FAILED_TERMINAL: "job_failed_terminal",
    STATUS_RETRY_LATER: "job_retry_later",
    STATUS_PAUSED: "job_paused",
    STATUS_SKIPPED: "job_skipped",
    STATUS_CANCELLED: "job_cancelled",
    STATUS_SUPERSEDED: "job_superseded",
    STATUS_QUEUED: "job_requeued",
}


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ClaimResult:
    """Returned by ``claim_next_job`` on a successful claim."""

    job_id: UUID
    run_id: UUID
    reading_record_id: UUID
    user_id: UUID
    base_id: UUID | None
    job_type: str
    target_type: str
    target_key: str
    expected_generation: int
    operation_fingerprint: str
    attempt_count: int
    lease_owner: str
    lease_token: UUID
    lease_expires_at: datetime
    # Observability fields (gap report #2 + #4). trace_id is parsed from
    # reader_runs.envelope_json so workers can use it as the parent_span_id
    # root for the reader_runtime_spans tree. claim_wait_ms measures the
    # wall-clock time from entering claim_next_job to the successful UPDATE.
    # Per-retry-class attempt counts feed derive_retry_class() so the claim
    # span records why this attempt is happening (gap report #6).
    trace_id: UUID | None = None
    claim_wait_ms: int | None = None
    transient_attempt_count: int = 0
    repair_attempt_count: int = 0
    replan_attempt_count: int = 0


@dataclass(frozen=True, slots=True)
class JobSnapshot:
    """Returned by ``transition`` to capture the post-transition job state."""

    id: UUID
    reading_record_id: UUID
    base_id: UUID | None
    run_id: UUID
    status: str
    job_type: str
    target_type: str
    target_key: str
    expected_generation: int
    attempt_count: int
    max_attempts: int
    lease_owner: str | None
    lease_token: UUID | None
    lease_expires_at: datetime | None
    claimed_at: datetime | None
    available_at: datetime
    pause_owner: str | None
    rationale_code: str | None


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class FenceViolationError(ValueError):
    """Raised when a base/generation fence check fails."""


class IllegalTransitionError(ValueError):
    """Raised when a job status transition is not allowed."""


class LeaseTokenMismatchError(ValueError):
    """Raised when a heartbeat or transition uses the wrong lease token."""


class LeaseExpiredError(ValueError):
    """Raised when a heartbeat or transition is attempted on an expired lease."""


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------


class ReaderJobRuntime:
    """Minimal Reader job runtime helper.

    All operations are short PostgreSQL transactions. ``claim_next_job`` uses
    ``SELECT FOR UPDATE SKIP LOCKED`` so concurrent workers never claim the
    same job. LLM calls must never run inside these transactions.
    """

    def __init__(self, *, pool: asyncpg.Pool | None = None) -> None:
        self._pool = pool

    def get_pool(self) -> asyncpg.Pool:
        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("Database pool not initialized")
        return pool

    # ------------------------------------------------------------------
    # Claim
    # ------------------------------------------------------------------

    async def claim_next_job(
        self,
        *,
        lease_owner: str,
        lease_duration: timedelta,
        job_type: str | None = None,
        target_type: str | None = None,
        operation_fingerprint: str | None = None,
        reading_record_id: UUID | None = None,
        base_id: UUID | None = None,
        expected_generation: int | None = None,
    ) -> ClaimResult | None:
        """Atomically claim the next available job.

        Picks jobs with ``status IN ('queued', 'retry_later')`` and
        ``available_at <= NOW()``. Optional ``job_type`` / ``target_type`` /
        ``operation_fingerprint`` / record scope filters narrow the claim
        domain for specialized workers or record-scoped drains. Jobs are ordered by
        ``priority DESC, available_at ASC, created_at ASC, id ASC``.

        Before claiming, validates the base/generation fence. If the fence
        fails, the job is marked ``superseded`` and the next job is tried.

        On success, writes ``lease_owner``, ``lease_token`` (new UUID),
        ``lease_expires_at`` (``now + lease_duration``), ``claimed_at`` and
        increments ``attempt_count``.
        """
        lease_token = uuid4()
        lease_expires_at = datetime.now(UTC) + lease_duration
        # Measure claim contention (gap report #2). Starts before the
        # SKIP LOCKED SELECT loop and ends when a job is successfully
        # claimed, including any fence-violation retries.
        claim_started_at = time.monotonic()

        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                while True:
                    row = await conn.fetchrow(
                        """
                        SELECT *
                        FROM reader_jobs
                        WHERE status IN ('queued', 'retry_later')
                          AND available_at <= NOW()
                          AND ($1::text IS NULL OR job_type = $1)
                          AND ($2::text IS NULL OR target_type = $2)
                          AND ($3::text IS NULL
                               OR operation_fingerprint = $3
                               OR starts_with(operation_fingerprint, $3 || ':'))
                          AND ($4::uuid IS NULL OR reading_record_id = $4)
                          AND ($5::uuid IS NULL OR base_id = $5)
                          AND ($6::integer IS NULL OR expected_generation = $6)
                        ORDER BY priority DESC, available_at ASC, created_at ASC, id ASC
                        LIMIT 1
                        FOR UPDATE SKIP LOCKED
                        """,
                        job_type,
                        target_type,
                        operation_fingerprint,
                        reading_record_id,
                        base_id,
                        expected_generation,
                    )
                    if row is None:
                        return None

                    fence_error = await self._validate_fence(conn, row)
                    if fence_error is not None:
                        await self._mark_job_superseded(
                            conn,
                            job_row=row,
                            rationale_code=fence_error,
                        )
                        continue

                    updated = await conn.fetchrow(
                        """
                        UPDATE reader_jobs
                        SET status = 'claimed',
                            lease_owner = $2,
                            lease_token = $3,
                            lease_expires_at = $4,
                            claimed_at = NOW(),
                            attempt_count = attempt_count + 1,
                            updated_at = NOW()
                        WHERE id = $1
                        RETURNING *
                        """,
                        row["id"],
                        lease_owner,
                        lease_token,
                        lease_expires_at,
                    )

                    await self._insert_job_event(
                        conn,
                        reading_record_id=updated["reading_record_id"],
                        run_id=updated["run_id"],
                        job_id=updated["id"],
                        event_type="job_claimed",
                        payload={
                            "lease_owner": lease_owner,
                            "lease_token": str(lease_token),
                            "lease_expires_at": lease_expires_at.isoformat(),
                            "attempt_count": int(updated["attempt_count"]),
                        },
                    )

                    # Fetch run envelope to extract trace_id for span tree
                    # linkage (gap report #3). Primary key lookup, negligible
                    # cost compared to the SKIP LOCKED scan above.
                    run_envelope_json = await conn.fetchval(
                        "SELECT envelope_json FROM reader_runs WHERE id = $1",
                        updated["run_id"],
                    )
                    claim_wait_ms = int(
                        (time.monotonic() - claim_started_at) * 1000
                    )

                    claim = _claim_result_from_row(
                        updated,
                        lease_owner=lease_owner,
                        lease_token=lease_token,
                        lease_expires_at=lease_expires_at,
                        claim_wait_ms=claim_wait_ms,
                        run_envelope_json=run_envelope_json,
                    )
                    # Record claim span (best-effort, gap report #2 + #4).
                    # Claim is a leaf span: start + end immediately, no use_span
                    # wrapping. trace_id prefers parent (pipeline_root) then
                    # falls back to envelope trace_id, then uuid4().
                    parent = current_span()
                    claim_trace_id = (
                        parent.trace_id
                        if parent is not None
                        else (claim.trace_id or uuid4())
                    )
                    recorder = get_default_recorder()
                    claim_span = await recorder.start_span(
                        trace_id=claim_trace_id,
                        span_kind=SPAN_KIND_CLAIM,
                        reading_record_id=claim.reading_record_id,
                        parent_span_id=parent.span_id if parent is not None else None,
                        reader_run_id=claim.run_id,
                        reader_job_id=claim.job_id,
                        claim_wait_ms=claim.claim_wait_ms,
                        attempt_number=claim.attempt_count,
                        retry_class=derive_retry_class(
                            transient_attempt_count=claim.transient_attempt_count,
                            repair_attempt_count=claim.repair_attempt_count,
                            replan_attempt_count=claim.replan_attempt_count,
                        ),
                        metadata={"lease_owner": lease_owner},
                    )
                    await recorder.end_span(claim_span, status=STATUS_SUCCEEDED)
                    return claim

    # ------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------

    async def heartbeat(
        self,
        *,
        job_id: UUID,
        lease_token: UUID,
        lease_duration: timedelta,
    ) -> datetime:
        """Extend ``lease_expires_at`` for a claimed job.

        Requires ``status = 'claimed'``, matching ``lease_token`` and a
        non-expired lease. Returns the new ``lease_expires_at``.
        """
        new_expires_at = datetime.now(UTC) + lease_duration

        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    SELECT status, lease_token, lease_expires_at,
                           reading_record_id, run_id
                    FROM reader_jobs
                    WHERE id = $1
                    FOR UPDATE
                    """,
                    job_id,
                )
                if row is None:
                    raise LookupError(f"reader job {job_id} not found")
                if row["status"] != STATUS_CLAIMED:
                    raise IllegalTransitionError(
                        "heartbeat requires status='claimed', "
                        f"got status='{row['status']}'"
                    )
                _assert_lease_valid(row, job_id, lease_token)

                await conn.execute(
                    """
                    UPDATE reader_jobs
                    SET lease_expires_at = $2,
                        updated_at = NOW()
                    WHERE id = $1
                    """,
                    job_id,
                    new_expires_at,
                )

                await self._insert_job_event(
                    conn,
                    reading_record_id=row["reading_record_id"],
                    run_id=row["run_id"],
                    job_id=job_id,
                    event_type="job_heartbeat",
                    payload={
                        "lease_token": str(lease_token),
                        "lease_expires_at": new_expires_at.isoformat(),
                    },
                )

                return new_expires_at

    # ------------------------------------------------------------------
    # Transition
    # ------------------------------------------------------------------

    async def transition(
        self,
        *,
        job_id: UUID,
        target_status: str,
        lease_token: UUID | None = None,
        available_at: datetime | None = None,
        pause_owner: str | None = None,
        output_ref: dict[str, Any] | None = None,
        failure_class: str | None = None,
        failure_code: str | None = None,
        failure_message: str | None = None,
        rationale_code: str | None = None,
    ) -> JobSnapshot:
        """Transition a job to ``target_status``.

        Supported targets: ``succeeded``, ``failed_terminal``, ``retry_later``,
        ``paused``, ``skipped``, ``cancelled``, ``superseded``, and ``queued``
        (only from ``paused`` — resume). Use ``claim_next_job`` for claiming,
        and ``recover_stale_leases`` for requeuing expired leases.

        Transitions from ``claimed`` require ``lease_token`` match and a
        non-expired lease. Transition to ``succeeded`` additionally validates
        the publish fence (generation, active base, base_id presence).
        """
        if target_status not in TRANSITION_TARGETS:
            raise ValueError(
                f"unsupported transition target {target_status!r}; "
                f"use claim_next_job for claiming"
            )

        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT * FROM reader_jobs WHERE id = $1 FOR UPDATE",
                    job_id,
                )
                if row is None:
                    raise LookupError(f"reader job {job_id} not found")

                current_status = row["status"]
                allowed = _ALLOWED_TRANSITIONS.get(current_status, frozenset())
                if target_status not in allowed:
                    raise IllegalTransitionError(
                        f"illegal transition {current_status!r} -> {target_status!r}"
                    )

                if current_status == STATUS_CLAIMED:
                    if lease_token is None:
                        raise LeaseTokenMismatchError(
                            f"lease_token required for transition from claimed "
                            f"for job {job_id}"
                        )
                    _assert_lease_valid(row, job_id, lease_token)

                if target_status == STATUS_SUCCEEDED:
                    fence_error = await self._validate_fence(conn, row)
                    if fence_error is not None:
                        raise FenceViolationError(
                            f"publish fence failed for job {job_id}: {fence_error}"
                        )

                if target_status == STATUS_RETRY_LATER and available_at is None:
                    raise ValueError(
                        "available_at is required for retry_later transition"
                    )
                if target_status == STATUS_PAUSED and pause_owner is None:
                    raise ValueError(
                        "pause_owner is required for paused transition"
                    )

                updated = await self._apply_transition(
                    conn,
                    job_row=row,
                    target_status=target_status,
                    available_at=available_at,
                    pause_owner=pause_owner,
                    output_ref=output_ref,
                    failure_class=failure_class,
                    failure_code=failure_code,
                    failure_message=failure_message,
                    rationale_code=rationale_code,
                )

                event_payload: dict[str, Any] = {
                    "previous_status": current_status,
                    "target_status": target_status,
                }
                if rationale_code is not None:
                    event_payload["rationale_code"] = rationale_code
                if target_status == STATUS_RETRY_LATER and available_at is not None:
                    event_payload["available_at"] = available_at.isoformat()
                if target_status == STATUS_PAUSED and pause_owner is not None:
                    event_payload["pause_owner"] = pause_owner

                await self._insert_job_event(
                    conn,
                    reading_record_id=updated["reading_record_id"],
                    run_id=updated["run_id"],
                    job_id=updated["id"],
                    event_type=_EVENT_TYPE_FOR_TRANSITION.get(
                        target_status, "job_transitioned"
                    ),
                    payload=event_payload,
                )

                return _job_snapshot_from_row(updated)

    # ------------------------------------------------------------------
    # Stale lease recovery
    # ------------------------------------------------------------------

    async def recover_stale_leases(
        self,
        *,
        batch_size: int = 100,
    ) -> int:
        """Recover claimed jobs whose lease has expired.

        Jobs with ``attempt_count >= max_attempts`` are moved to
        ``failed_terminal``; otherwise they are requeued to ``queued`` with
        ``available_at = NOW()``. Returns the number of recovered jobs.
        """
        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch(
                    """
                    SELECT *
                    FROM reader_jobs
                    WHERE status = 'claimed' AND lease_expires_at < NOW()
                    ORDER BY lease_expires_at ASC
                    LIMIT $1
                    FOR UPDATE SKIP LOCKED
                    """,
                    batch_size,
                )
                for row in rows:
                    attempt_count = int(row["attempt_count"])
                    max_attempts = int(row["max_attempts"])
                    if attempt_count >= max_attempts:
                        await self._apply_transition(
                            conn,
                            job_row=row,
                            target_status=STATUS_FAILED_TERMINAL,
                            available_at=None,
                            pause_owner=None,
                            output_ref=None,
                            failure_class="lease_lost",
                            failure_code="max_attempts_exceeded",
                            failure_message=None,
                            rationale_code="lease_lost_max_attempts",
                        )
                        await self._insert_job_event(
                            conn,
                            reading_record_id=row["reading_record_id"],
                            run_id=row["run_id"],
                            job_id=row["id"],
                            event_type="job_failed_terminal",
                            payload={
                                "reason": "lease_lost_max_attempts",
                                "attempt_count": attempt_count,
                                "max_attempts": max_attempts,
                            },
                        )
                    else:
                        await self._apply_transition(
                            conn,
                            job_row=row,
                            target_status=STATUS_QUEUED,
                            available_at=datetime.now(UTC),
                            pause_owner=None,
                            output_ref=None,
                            failure_class=None,
                            failure_code=None,
                            failure_message=None,
                            rationale_code="lease_lost",
                        )
                        await self._insert_job_event(
                            conn,
                            reading_record_id=row["reading_record_id"],
                            run_id=row["run_id"],
                            job_id=row["id"],
                            event_type="heartbeat_lost",
                            payload={
                                "previous_lease_token": (
                                    str(row["lease_token"])
                                    if row["lease_token"] is not None
                                    else None
                                ),
                                "attempt_count": attempt_count,
                                "max_attempts": max_attempts,
                            },
                        )

                return len(rows)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _validate_fence(
        self,
        conn: asyncpg.Connection,
        job_row: asyncpg.Record,
    ) -> str | None:
        """Validate base/generation fence for claim or publish.

        Returns ``None`` on success, or a rationale code string on failure.
        """
        record_row = await conn.fetchrow(
            """
            SELECT generation, active_base_id
            FROM reading_records
            WHERE id = $1
              AND deleted_at IS NULL
            """,
            job_row["reading_record_id"],
        )
        if record_row is None:
            return "missing_record"
        record_gen = int(record_row["generation"])
        if int(record_gen) != int(job_row["expected_generation"]):
            return "stale_generation"

        base_id = job_row["base_id"]
        if base_id is None:
            # build_base, input_artifact_extraction, and
            # extracted_artifact_materialization record-level jobs may have
            # null base_id: build_base creates the first reading base;
            # input_artifact_extraction runs before any base exists (it
            # extracts text from the uploaded artifact into original_inputs);
            # extracted_artifact_materialization also runs before any base
            # exists (the stable path itself creates the first base via
            # persist_stable_document_freeze_plan).
            # All other non-build_base jobs require a base_id.
            job_type = job_row["job_type"]
            target_type = job_row["target_type"]
            if target_type == "record" and job_type == "build_base":
                return None
            if target_type == "record" and job_type in (
                "input_artifact_extraction",
                "extracted_artifact_materialization",
            ):
                # Extraction and materialization run before any base exists.
                # If a base has already been built for this generation, the
                # job is stale — supersede it to prevent overwriting
                # original_inputs.source_text (extraction) or re-freezing a
                # stable document (materialization) after downstream consumers
                # may have already read it.
                if record_row["active_base_id"] is not None:
                    return "active_base_already_exists"
                return None
            return "missing_base"

        base_row = await conn.fetchrow(
            """
            SELECT reading_record_id, status, record_generation
            FROM reading_bases
            WHERE id = $1
            """,
            base_id,
        )
        if base_row is None:
            return "missing_base"
        if base_row["reading_record_id"] != job_row["reading_record_id"]:
            return "base_record_mismatch"
        if base_row["status"] != "active":
            return "inactive_base"
        if int(base_row["record_generation"]) != int(job_row["expected_generation"]):
            return "stale_generation"
        if record_row["active_base_id"] != base_id:
            return "active_base_mismatch"

        return None

    async def _mark_job_superseded(
        self,
        conn: asyncpg.Connection,
        *,
        job_row: asyncpg.Record,
        rationale_code: str,
    ) -> None:
        await conn.execute(
            """
            UPDATE reader_jobs
            SET status = 'superseded',
                lease_owner = NULL,
                lease_token = NULL,
                lease_expires_at = NULL,
                claimed_at = NULL,
                rationale_code = $2,
                updated_at = NOW()
            WHERE id = $1
            """,
            job_row["id"],
            rationale_code,
        )
        await self._insert_job_event(
            conn,
            reading_record_id=job_row["reading_record_id"],
            run_id=job_row["run_id"],
            job_id=job_row["id"],
            event_type="job_superseded",
            payload={"rationale_code": rationale_code},
        )

    async def _apply_transition(
        self,
        conn: asyncpg.Connection,
        *,
        job_row: asyncpg.Record,
        target_status: str,
        available_at: datetime | None,
        pause_owner: str | None,
        output_ref: dict[str, Any] | None,
        failure_class: str | None,
        failure_code: str | None,
        failure_message: str | None,
        rationale_code: str | None,
    ) -> asyncpg.Record:
        set_parts: list[str] = ["status = $2", "updated_at = NOW()"]
        params: list[Any] = [job_row["id"], target_status]
        param_idx = 3

        if target_status in _LEASE_CLEARING_TARGETS:
            set_parts.extend([
                "lease_owner = NULL",
                "lease_token = NULL",
                "lease_expires_at = NULL",
                "claimed_at = NULL",
            ])

        if target_status == STATUS_RETRY_LATER and available_at is not None:
            set_parts.append(f"available_at = ${param_idx}")
            params.append(available_at)
            param_idx += 1

        if target_status == STATUS_QUEUED and available_at is not None:
            set_parts.append(f"available_at = ${param_idx}")
            params.append(available_at)
            param_idx += 1

        # Resume from paused: clear pause_owner so the job is fully back in the
        # claimable queue.
        if target_status == STATUS_QUEUED:
            set_parts.append("pause_owner = NULL")

        if target_status == STATUS_PAUSED and pause_owner is not None:
            set_parts.append(f"pause_owner = ${param_idx}")
            params.append(pause_owner)
            param_idx += 1

        if target_status == STATUS_SUCCEEDED and output_ref is not None:
            set_parts.append(f"output_ref_json = ${param_idx}::jsonb")
            params.append(jsonb_param(output_ref))
            param_idx += 1

        if target_status == STATUS_FAILED_TERMINAL:
            if failure_class is not None:
                set_parts.append(f"failure_class = ${param_idx}")
                params.append(failure_class)
                param_idx += 1
            if failure_code is not None:
                set_parts.append(f"failure_code = ${param_idx}")
                params.append(failure_code)
                param_idx += 1
            if failure_message is not None:
                set_parts.append(f"failure_message = ${param_idx}")
                params.append(failure_message)
                param_idx += 1

        if rationale_code is not None:
            set_parts.append(f"rationale_code = ${param_idx}")
            params.append(rationale_code)
            param_idx += 1

        query = (
            f"UPDATE reader_jobs SET {', '.join(set_parts)} "
            f"WHERE id = $1 RETURNING *"
        )
        return await conn.fetchrow(query, *params)

    async def _insert_job_event(
        self,
        conn: asyncpg.Connection,
        *,
        reading_record_id: UUID,
        run_id: UUID | None,
        job_id: UUID,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        await conn.execute(
            """
            INSERT INTO reader_job_events
                (reading_record_id, run_id, job_id, event_type, payload_json)
            VALUES ($1, $2, $3, $4, $5::jsonb)
            """,
            reading_record_id,
            run_id,
            job_id,
            event_type,
            jsonb_param(payload),
        )


# ---------------------------------------------------------------------------
# Row -> dataclass helpers
# ---------------------------------------------------------------------------


def _claim_result_from_row(
    row: asyncpg.Record,
    *,
    lease_owner: str,
    lease_token: UUID,
    lease_expires_at: datetime,
    claim_wait_ms: int | None = None,
    run_envelope_json: Any = None,
) -> ClaimResult:
    # Parse trace_id from reader_runs.envelope_json so workers can use it
    # as the parent_span_id root for the reader_runtime_spans tree.
    # Defensive: legacy rows without trace_id in envelope yield None, and
    # the span recorder falls back to generating a fresh trace_id.
    trace_id = parse_trace_id_from_envelope(run_envelope_json)

    return ClaimResult(
        job_id=row["id"],
        run_id=row["run_id"],
        reading_record_id=row["reading_record_id"],
        user_id=row["user_id"],
        base_id=row["base_id"],
        job_type=row["job_type"],
        target_type=row["target_type"],
        target_key=row["target_key"],
        expected_generation=int(row["expected_generation"]),
        operation_fingerprint=row["operation_fingerprint"],
        attempt_count=int(row["attempt_count"]),
        lease_owner=lease_owner,
        lease_token=lease_token,
        lease_expires_at=lease_expires_at,
        trace_id=trace_id,
        claim_wait_ms=claim_wait_ms,
        transient_attempt_count=int(row["transient_attempt_count"]),
        repair_attempt_count=int(row["repair_attempt_count"]),
        replan_attempt_count=int(row["replan_attempt_count"]),
    )


def _job_snapshot_from_row(row: asyncpg.Record) -> JobSnapshot:
    return JobSnapshot(
        id=row["id"],
        reading_record_id=row["reading_record_id"],
        base_id=row["base_id"],
        run_id=row["run_id"],
        status=row["status"],
        job_type=row["job_type"],
        target_type=row["target_type"],
        target_key=row["target_key"],
        expected_generation=int(row["expected_generation"]),
        attempt_count=int(row["attempt_count"]),
        max_attempts=int(row["max_attempts"]),
        lease_owner=row["lease_owner"],
        lease_token=row["lease_token"],
        lease_expires_at=row["lease_expires_at"],
        claimed_at=row["claimed_at"],
        available_at=row["available_at"],
        pause_owner=row["pause_owner"],
        rationale_code=row["rationale_code"],
    )


def _assert_lease_valid(
    row: asyncpg.Record,
    job_id: UUID,
    lease_token: UUID,
) -> None:
    stored_token = row["lease_token"]
    if stored_token is None or stored_token != lease_token:
        raise LeaseTokenMismatchError(
            f"lease_token mismatch for job {job_id}"
        )
    lease_expires_at = row["lease_expires_at"]
    if lease_expires_at is not None and lease_expires_at < datetime.now(UTC):
        raise LeaseExpiredError(
            f"lease expired for job {job_id}"
        )
