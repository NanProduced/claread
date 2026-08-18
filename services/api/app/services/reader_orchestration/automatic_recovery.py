"""Bounded automatic-recovery scan for provider-timeout failed records.

RA-REC-05 R1 scan-only domain service. The batch scan is a bounded
pre-filter; the FINAL eligibility gate runs inside the recovery core's
own ``Record FOR UPDATE`` transaction
(``EnhancementJobBootstrapService.recover_failed_enhancement_jobs``,
``trigger='automatic'``), which atomically enforces:

- failure purity: EVERY ordinary ``failed_terminal`` predecessor must be
  exactly ``failure_class='provider'`` / ``failure_code='provider_timeout'``;
- the 30-minute cooldown measured from the newest eligible failure;
- the per record+generation attempt cap counted from committed recovery
  events (manual triggers, no-ops and other generations never consume it).

Because the gate shares the transaction that creates successors, a worker
terminalizing a successor between scan and recover can never push a
record past the policy — no scanner-side locks, no nested connections.

Pre-filter semantics (kept identical to the core gate so the batch stays
useful):

- ``product_state = 'failed'`` records that already reached an
  article-ready readiness milestone with a valid active base / generation
  / lifecycle fence;
- ordinary enhancement lane only (analysis-section lanes are excluded via
  the production ``ANALYSIS_SECTION_ORIGINS`` and never auto-rebuilt);
- bounded batch, oldest eligible failure first; ``batch_size < 1`` is
  rejected before any database access.

This service never creates/resets jobs, mutates product_state or writes
billing rows itself. Expected fence drift degrades to ``skipped``;
unexpected back-end failures surface as ``error`` results for
observability. No worker/runtime loop is wired here (RA-REC-06).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

import asyncpg  # type: ignore[import-untyped]  # asyncpg ships no py.typed

from app.database import connection as db_connection

from .analysis_section_jobs import ANALYSIS_SECTION_ORIGINS
from .job_bootstrap import (
    _ARTICLE_READY_READINESS_STATES,
    _AUTOMATIC_FAILURE_CLASS,
    _AUTOMATIC_FAILURE_CODE,
    _RECOVERY_ENHANCEMENT_JOB_TYPES,
    AUTOMATIC_RECOVERY_COOLDOWN_MINUTES,
    MAX_AUTOMATIC_RECOVERIES_PER_GENERATION,
    RECOVERY_EVENT_SCHEMA,
    RECOVERY_MODE_SAME_GENERATION_SUCCESSOR_JOBS,
    RECOVERY_TRIGGER_AUTOMATIC,
    EnhancementJobBootstrapService,
)

_logger = logging.getLogger(__name__)

CandidateStatus = Literal["recovered", "noop", "skipped", "error"]

# Bounded candidate pre-filter. Joins reproduce the recovery core's fence
# (active record/base, generation match, article-ready readiness) and the
# ordinary-lane predecessor predicate; the HAVING clause mirrors the
# core's automatic gate (failure purity + cooldown) and the event cap.
# The CASE guard keeps the ::int cast total against any non-numeric
# payload. The core re-checks all of this under the record lock.
_AUTOMATIC_CANDIDATE_SQL = """
SELECT
    rr.id AS record_id,
    rr.user_id AS user_id,
    rr.generation AS generation,
    MAX(rj.updated_at) AS newest_failed_at
FROM reading_records rr
JOIN reading_bases rb
    ON rb.id = rr.active_base_id
    AND rb.reading_record_id = rr.id
    AND rb.record_generation = rr.generation
    AND rb.status = 'active'
JOIN reader_jobs rj
    ON rj.reading_record_id = rr.id
    AND rj.base_id = rr.active_base_id
    AND rj.expected_generation = rr.generation
    AND rj.status = 'failed_terminal'
    AND rj.job_type = ANY($1::text[])
    AND COALESCE(rj.input_json->>'request_origin', '') <> ALL($2::text[])
WHERE rr.product_state = 'failed'
    AND rr.deleted_at IS NULL
    AND rr.lifecycle_status = 'active'
    AND rr.readiness_state = ANY($3::text[])
    AND (
        SELECT COUNT(*)
        FROM reader_events re
        WHERE re.reading_record_id = rr.id
            AND re.event_type = 'record_state_changed'
            AND re.payload_json->>'event_schema' = $4
            AND re.payload_json->>'trigger' = $5
            AND re.payload_json->>'recovery_mode' = $6
            AND CASE
                WHEN re.payload_json->>'generation' ~ '^[0-9]+$'
                THEN (re.payload_json->>'generation')::int = rr.generation
                ELSE FALSE
            END
    ) < $7
GROUP BY rr.id, rr.user_id, rr.generation
HAVING COUNT(*) = COUNT(*) FILTER (
        WHERE rj.failure_class = $8 AND rj.failure_code = $9
    )
    AND MAX(rj.updated_at) <= NOW() - ($10 * INTERVAL '1 minute')
ORDER BY MAX(rj.updated_at) ASC, rr.id ASC
LIMIT $11
"""


@dataclass(frozen=True, slots=True)
class AutomaticRecoveryCandidate:
    """One failed record that passed the automatic pre-filter."""

    record_id: UUID
    user_id: UUID
    generation: int
    newest_failed_at: datetime


@dataclass(frozen=True, slots=True)
class AutomaticRecoveryCandidateResult:
    """Outcome of running the recovery core for one scanned candidate."""

    record_id: UUID
    status: CandidateStatus
    successor_job_count: int = 0


@dataclass(frozen=True, slots=True)
class AutomaticRecoveryScanSummary:
    """Aggregate outcome of one bounded ``run_once`` pass.

    ``error_count`` separates unexpected back-end failures from expected
    fail-closed skips so RA-REC-06 can alert on scanner faults.
    """

    batch_size: int
    results: tuple[AutomaticRecoveryCandidateResult, ...]
    recovered_count: int
    noop_count: int
    skipped_count: int
    error_count: int


class AutomaticRecoveryService:
    """Bounded scan + single-pass automatic recovery (no runtime loop)."""

    def __init__(self, *, pool: asyncpg.Pool | None = None) -> None:
        self._pool = pool

    def get_pool(self) -> asyncpg.Pool:
        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("Database pool not initialized")
        return pool

    async def scan_candidates(
        self,
        batch_size: int,
    ) -> tuple[AutomaticRecoveryCandidate, ...]:
        """Return pre-filtered candidates, oldest eligible failure first."""
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        async with self.get_pool().acquire() as conn:
            rows = await conn.fetch(
                _AUTOMATIC_CANDIDATE_SQL,
                list(_RECOVERY_ENHANCEMENT_JOB_TYPES),
                list(ANALYSIS_SECTION_ORIGINS),
                list(_ARTICLE_READY_READINESS_STATES),
                RECOVERY_EVENT_SCHEMA,
                RECOVERY_TRIGGER_AUTOMATIC,
                RECOVERY_MODE_SAME_GENERATION_SUCCESSOR_JOBS,
                MAX_AUTOMATIC_RECOVERIES_PER_GENERATION,
                _AUTOMATIC_FAILURE_CLASS,
                _AUTOMATIC_FAILURE_CODE,
                AUTOMATIC_RECOVERY_COOLDOWN_MINUTES,
                batch_size,
            )
        return tuple(
            AutomaticRecoveryCandidate(
                record_id=UUID(str(row["record_id"])),
                user_id=UUID(str(row["user_id"])),
                generation=int(row["generation"]),
                newest_failed_at=row["newest_failed_at"],
            )
            for row in rows
        )

    async def run_once(self, batch_size: int) -> AutomaticRecoveryScanSummary:
        """Scan one bounded batch and recover each candidate exactly once.

        Policy atomicity lives in the recovery core: its record FOR
        UPDATE transaction re-validates purity, cooldown and the attempt
        cap right before creating successors. A candidate that drifts
        out of eligibility degrades to ``skipped``/``noop`` there and
        never blocks the remaining candidates.
        """
        candidates = await self.scan_candidates(batch_size)
        bootstrap = EnhancementJobBootstrapService(pool=self._pool)
        results = [
            await self._recover_candidate(bootstrap, candidate)
            for candidate in candidates
        ]
        recovered = sum(1 for result in results if result.status == "recovered")
        skipped = sum(1 for result in results if result.status == "skipped")
        errors = sum(1 for result in results if result.status == "error")
        return AutomaticRecoveryScanSummary(
            batch_size=batch_size,
            results=tuple(results),
            recovered_count=recovered,
            noop_count=len(results) - recovered - skipped - errors,
            skipped_count=skipped,
            error_count=errors,
        )

    async def _recover_candidate(
        self,
        bootstrap: EnhancementJobBootstrapService,
        candidate: AutomaticRecoveryCandidate,
    ) -> AutomaticRecoveryCandidateResult:
        try:
            summary = await bootstrap.recover_failed_enhancement_jobs(
                record_id=candidate.record_id,
                user_id=candidate.user_id,
                trigger=RECOVERY_TRIGGER_AUTOMATIC,
            )
        except (ValueError, LookupError):
            # Expected fail-closed rejection: fence drift or the core's
            # automatic policy gate (purity / cooldown / attempt cap)
            # observed newer facts under the record lock. Deterministic
            # skip, no alert needed.
            _logger.info(
                "reader_automatic_recovery_skipped record_id=%s",
                candidate.record_id,
            )
            return AutomaticRecoveryCandidateResult(
                record_id=candidate.record_id, status="skipped"
            )
        except Exception:
            # Unexpected back-end fault (database error, program error):
            # counted separately so RA-REC-06 can alert on scanner
            # faults. Logs carry only the stable event name + record id —
            # never the exception body, traceback, failure_message or
            # payload content.
            _logger.error(
                "reader_automatic_recovery_error record_id=%s",
                candidate.record_id,
            )
            return AutomaticRecoveryCandidateResult(
                record_id=candidate.record_id, status="error"
            )
        if summary.recovered:
            _logger.info(
                "reader_automatic_recovery_succeeded record_id=%s",
                candidate.record_id,
            )
            return AutomaticRecoveryCandidateResult(
                record_id=candidate.record_id,
                status="recovered",
                successor_job_count=len(summary.successor_job_ids),
            )
        return AutomaticRecoveryCandidateResult(
            record_id=candidate.record_id, status="noop"
        )
