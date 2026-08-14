"""Completion state finalizer.

When the enhancement pipeline reports ``all_workers_no_job`` for a record,
the worker loop must distinguish two scenarios that the pipeline summary
alone cannot disambiguate:

1. **All work terminal and successful** — every enhancement job and every
   analysis window has reached a terminal status (``succeeded`` /
   ``skipped`` / ``cancelled`` / ``superseded`` for jobs; ``completed`` /
   ``no_op`` / ``failed`` for windows). The record can transition
   ``readiness_state -> coverage_complete`` so the worker loop stops
   re-scanning it and downstream readers see the article as fully
   enhanced.

2. **Idle but not finished** — the pipeline returned ``all_workers_no_job``
   because every job in the queue is currently ``claimed`` by another
   worker, or because some analysis window is still in ``pending`` /
   ``running`` (e.g. the grammar-window grammar bundle window worker has not been
   registered in this deployment). The record must NOT be finalized.

The finalizer is a pure decision + single-row write helper. It is invoked
by :class:`ReaderEnhancementWorkerLoopService` after
``decide_product_state_for_pipeline_summary`` has declined to update
``product_state`` (i.e. there is no ``failed_terminal`` /
``action_required`` outcome to handle first). It performs three reads
against the durable PostgreSQL state and, when eligible, transitions
``readiness_state`` and publishes a single ``record_completion_finalized``
event in the caller's transaction.

Design invariants:

- Never overrides a ``failed`` / ``action_required`` ``product_state``
  decision. The caller skips the finalizer when
  ``product_state_decision.should_update_record`` is true.
- Never finalizes when ``stopped_reason`` is ``attention_required`` —
  the product_state decision path owns retry_later / failed_terminal /
  superseded outcomes.
- Treats ``max_ticks_reached`` / ``max_jobs_reached`` as finalizable.
  The pipeline runner checks these caps AFTER incrementing the processed
  count, so the last succeeding job can land exactly on the budget even
  though all work is done. The durable-state guards (non-terminal jobs,
  non-terminal windows, tracked_job_count) — not ``stopped_reason`` —
  are the source of truth for "is the work actually finished". When a
  cap is hit mid-stream, ``non_terminal_jobs_present`` skips and the
  candidate scan re-picks the record (``runnable_job_count > 0``).
- Never finalizes when non-terminal jobs remain in the DB. When
  non-terminal analysis windows remain but all enhancement jobs are
  terminal, the windows are stuck (the per-record advisory lock
  guarantees the pipeline already exhausted every worker). The finalizer
  force-fails those windows to ``failed`` and finalizes as
  ``completed_with_failures`` — otherwise the record would be wedged
  (the candidate scan only re-picks records with runnable jobs).
- The ``product_state`` is intentionally left at ``readable_enhancing``
  on the clean / partial-no_op / completed-with-failures paths. v1
  grammar window ``failed`` outcomes are captured by diagnostics;
  forcing ``product_state = failed`` would lock users out of articles
  whose translation + vocabulary succeeded.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

import asyncpg

from .job_runtime import (
    ANALYSIS_WINDOW_STATUS_FAILED,
    ANALYSIS_WINDOW_STATUS_NO_OP,
    NON_TERMINAL_ANALYSIS_WINDOW_STATUSES,
    NON_TERMINAL_JOB_STATUSES,
    STATUS_FAILED_TERMINAL,
)
from .pipeline_runner import PipelineStoppedReason, ReaderPipelineRunSummary
from .repository import ReaderOrchestrationRepository

RECORD_COMPLETION_FINALIZED_EVENT_TYPE = "record_state_changed"

# Discriminator written into the ``record_state_changed`` event payload
# so consumers can tell finalizer-emitted readiness transitions apart
# from field-level updates (e.g. ``display_title_zh``). The finalizer
# reuses the existing ``record_state_changed`` event type — already in
# the ``reader_events.event_type`` CHECK constraint — to avoid a schema
# migration. The ``field`` key mirrors the convention used by
# ``display_title_worker``.
COMPLETION_EVENT_FIELD = "readiness_state"

# ``readiness_state`` values that are eligible to advance to
# ``coverage_complete``. Mirrors the worker loop scan filter
# ``_RUNNABLE_RECORD_READYNESS_STATES`` so the finalizer only touches
# records that the worker loop itself would have picked up.
COMPLETION_ELIGIBLE_READINESS_STATES: tuple[str, ...] = (
    "article_ready",
    "initial_enhancement_ready",
)
COMPLETION_TARGET_READINESS_STATE = "coverage_complete"

# ``stopped_reason`` values for which the finalizer must NOT run. Only
# ``attention_required`` is excluded — the product_state decision path
# owns retry_later / failed_terminal / superseded outcomes. ``max_ticks``
# / ``max_jobs`` / ``budget_exhausted`` are NOT excluded because:
# - ``max_ticks`` / ``max_jobs``: the pipeline runner checks those caps
#   AFTER incrementing the processed count, so the last succeeding job
#   can land exactly on the budget.
# - ``budget_exhausted``: when the execution budget is
#   exhausted, remaining non-terminal jobs are force-failed by the
#   finalizer (via the existing non-terminal guard), and the record
#   finalizes as ``completed_with_failures``.
# The durable-state guards below decide whether the work is actually
# finished.
NON_FINALIZABLE_STOPPED_REASONS: frozenset[PipelineStoppedReason] = frozenset({
    "attention_required",
})

# Failure metadata written into ``analysis_windows.coverage`` when the
# finalizer force-fails stuck non-terminal windows. Mirrors the
# diagnostics convention so diagnostic queries surface the forced-fail
# reason without a schema migration.
FINALIZER_FORCED_WINDOW_FAILURE_CODE = "finalizer_forced_window_failure"
FINALIZER_FORCED_WINDOW_FAILURE_REASON = (
    "non-terminal analysis window when all enhancement jobs reached "
    "terminal state; the window worker could not make progress"
)

# Failure metadata written into job diagnostics when the
# finalizer force-fails non-terminal jobs due to budget exhaustion.
BUDGET_EXHAUSTED_FAILURE_CODE = "budget_exhausted"
BUDGET_EXHAUSTED_FAILURE_REASON = (
    "execution budget exhausted; remaining non-terminal jobs "
    "force-failed by the completion finalizer"
)

# Maps budget layer names to the job_types that belong to
# that layer. Used by the finalizer to force-fail ONLY the exhausted
# layers' jobs during ``partial_budget_exhausted``, preserving
# non-exhausted layers' retry_later / queued jobs.
BUDGET_LAYER_TO_JOB_TYPES: dict[str, tuple[str, ...]] = {
    "translation": ("translate_unit", "translate_article"),
    "vocabulary": (
        "build_vocabulary_layer",
        "build_vocabulary_layer_article",
    ),
    "grammar": ("build_grammar_bundle", "build_grammar_bundle_window"),
}

CompletionOutcome = Literal[
    "completed_clean",
    "completed_with_no_op",
    "completed_with_failures",
]
CompletionSkipReason = Literal[
    "stopped_reason_not_finalizable",
    "product_state_decision_takes_precedence",
    "no_tracked_enhancement_jobs",
    "non_terminal_jobs_present",
    "readiness_state_not_eligible",
    "readiness_state_update_did_not_apply",
]


@dataclass(frozen=True, slots=True)
class CompletionFinalizationResult:
    """Outcome of a finalizer invocation.

    Exactly one of ``outcome`` / ``skip_reason`` is set. When ``outcome``
    is set, ``readiness_state_updated`` indicates whether the DB row was
    actually updated (it can be ``False`` if a concurrent worker already
    advanced the record). When ``skip_reason`` is set, the finalizer did
    not attempt a write.
    """

    finalized: bool
    outcome: CompletionOutcome | None = None
    skip_reason: CompletionSkipReason | None = None
    readiness_state_updated: bool = False
    job_status_counts: dict[str, int] | None = None
    window_status_counts: dict[str, int] | None = None
    # Number of stuck non-terminal analysis windows the finalizer
    # force-failed to ``failed``. 0 on the clean / no_op / skip paths.
    force_failed_window_count: int = 0
    event_sequence: int | None = None


def should_attempt_finalization(summary: ReaderPipelineRunSummary) -> bool:
    """Quick gate used by the worker loop before issuing any DB reads.

    The worker loop calls this to avoid even the cheap count queries when
    the pipeline summary itself already disqualifies the record (budget
    cap or attention signal).
    """
    return summary.stopped_reason not in NON_FINALIZABLE_STOPPED_REASONS


def _classify_completion_outcome(
    job_status_counts: dict[str, int],
    window_status_counts: dict[str, int],
) -> CompletionOutcome:
    """Map terminal job/window counts to a ``CompletionOutcome``.

    Pre-condition: caller has already verified there are no non-terminal
    jobs or windows. The classifier only inspects the *kinds* of terminal
    statuses present.
    """
    failed_window_count = window_status_counts.get(
        ANALYSIS_WINDOW_STATUS_FAILED, 0
    )
    no_op_window_count = window_status_counts.get(
        ANALYSIS_WINDOW_STATUS_NO_OP, 0
    )

    # The finalizer is only invoked when there are no failed_terminal
    # jobs (the caller skips us when product_state_decision takes
    # precedence). Defensive: if a stray failed_terminal job appears,
    # treat the completion as ``completed_with_failures`` rather than
    # crashing — the diagnostics path captures the failure.
    failed_job_count = job_status_counts.get(STATUS_FAILED_TERMINAL, 0)

    if failed_window_count > 0 or failed_job_count > 0:
        return "completed_with_failures"
    if no_op_window_count > 0:
        return "completed_with_no_op"
    return "completed_clean"


def build_completion_finalized_event_payload(
    *,
    outcome: CompletionOutcome,
    job_status_counts: dict[str, int],
    window_status_counts: dict[str, int],
    stopped_reason: PipelineStoppedReason,
    readiness_state: str,
    previous_readiness_state: str,
    force_failed_window_count: int = 0,
) -> dict[str, Any]:
    """Build the ``record_state_changed`` event payload for a finalizer
    transition.

    The payload reuses the ``field`` discriminator convention from
    ``display_title_worker`` so downstream consumers can route on
    ``field == "readiness_state"`` to identify finalizer-emitted events.
    The ``completion_outcome`` distinguishes clean / no_op / failures
    flavors; the per-status counts feed Console observability.
    ``force_failed_window_count`` is non-zero when the finalizer
    force-failed stuck non-terminal windows to ``failed``.
    """
    return {
        "field": COMPLETION_EVENT_FIELD,
        "previous_value": previous_readiness_state,
        "next_value": readiness_state,
        "completion_outcome": outcome,
        "stopped_reason": stopped_reason,
        "job_status_counts": dict(job_status_counts),
        "window_status_counts": dict(window_status_counts),
        "force_failed_window_count": force_failed_window_count,
    }


class CompletionFinalizer:
    """Decide whether a record is ready to transition to
    ``coverage_complete`` and apply the transition in a single transaction.

    The finalizer is stateless apart from its repository handle. It is
    safe to instantiate once per worker loop or per call.
    """

    def __init__(
        self,
        repository: ReaderOrchestrationRepository | None = None,
    ) -> None:
        self._repository = repository or ReaderOrchestrationRepository()

    async def finalize_completion_state(
        self,
        conn: asyncpg.Connection,
        *,
        record_id: UUID,
        base_id: UUID,
        expected_generation: int,
        summary: ReaderPipelineRunSummary,
        enhancement_job_types: tuple[str, ...],
        event_runtime: Any,
        updated_at: datetime,
    ) -> CompletionFinalizationResult:
        """Run the finalizer decision and (if eligible) the readiness write.

        ``conn`` is the caller's transactional connection. The event is
        published via ``event_runtime.publish_event_in_transaction`` so it
        commits atomically with the ``readiness_state`` update. When the
        finalizer decides not to finalize, no writes are performed.
        """
        if not should_attempt_finalization(summary):
            return CompletionFinalizationResult(
                finalized=False,
                skip_reason="stopped_reason_not_finalizable",
            )

        job_status_counts = (
            await self._repository.count_enhancement_jobs_by_terminal_status(
                conn,
                record_id=record_id,
                base_id=base_id,
                expected_generation=expected_generation,
                job_types=enhancement_job_types,
            )
        )
        window_status_counts = (
            await self._repository.count_analysis_windows_by_terminal_status(
                conn,
                record_id=record_id,
                base_id=base_id,
                expected_generation=expected_generation,
            )
        )

        # Guard: a record with zero tracked enhancement jobs has not been
        # bootstrapped (or bootstrap failed silently). The pipeline
        # returned ``all_workers_no_job`` because there was nothing to
        # claim, not because the work is done. Do NOT finalize — let the
        # next worker loop tick retry bootstrap. This also covers test
        # fixtures that inject a stub runner without bootstrapping.
        tracked_job_count = sum(job_status_counts.values())
        if tracked_job_count == 0:
            return CompletionFinalizationResult(
                finalized=False,
                skip_reason="no_tracked_enhancement_jobs",
                job_status_counts=job_status_counts,
                window_status_counts=window_status_counts,
            )

        non_terminal_job_count = sum(
            job_status_counts.get(status, 0)
            for status in NON_TERMINAL_JOB_STATUSES
        )
        if non_terminal_job_count > 0:
            # When the pipeline stopped due to budget
            # exhaustion, force-fail non-terminal jobs so the record
            # can finalize as ``completed_with_failures`` instead of
            # being wedged.
            #
            # Fix: BOTH ``budget_exhausted`` (full) and
            # ``partial_budget_exhausted`` must ONLY force-fail jobs
            # in budget layers (translation / vocabulary / grammar).
            # ``display_title`` is NOT a budget layer and must NEVER
            # be force-failed by budget exhaustion — a retryable
            # display_title job must survive even when all budget
            # layers are exhausted.
            if summary.stopped_reason == "budget_exhausted":
                # All budget layers exhausted — force-fail all budget
                # layer job types, but NOT display_title.
                force_fail_types = tuple(
                    job_type
                    for layer in ("translation", "vocabulary", "grammar")
                    for job_type in BUDGET_LAYER_TO_JOB_TYPES.get(
                        layer, ()
                    )
                )
            elif summary.stopped_reason == "partial_budget_exhausted":
                # Only force-fail the exhausted layers' job types.
                force_fail_types = tuple(
                    job_type
                    for layer in summary.exhausted_layers
                    for job_type in BUDGET_LAYER_TO_JOB_TYPES.get(
                        layer, ()
                    )
                )
                if not force_fail_types:
                    # No known job types for the exhausted layers —
                    # skip force-fail and let the non-terminal guard
                    # prevent finalization (safe: no wedge because
                    # the worker loop will re-scan).
                    return CompletionFinalizationResult(
                        finalized=False,
                        skip_reason="non_terminal_jobs_present",
                        job_status_counts=job_status_counts,
                        window_status_counts=window_status_counts,
                    )
            else:
                return CompletionFinalizationResult(
                    finalized=False,
                    skip_reason="non_terminal_jobs_present",
                    job_status_counts=job_status_counts,
                    window_status_counts=window_status_counts,
                )

            force_failed_job_count = (
                await self._repository.force_fail_non_terminal_jobs(
                    conn,
                    record_id=record_id,
                    base_id=base_id,
                    expected_generation=expected_generation,
                    job_types=force_fail_types,
                    failure_code=BUDGET_EXHAUSTED_FAILURE_CODE,
                    failure_reason=BUDGET_EXHAUSTED_FAILURE_REASON,
                    updated_at=updated_at,
                )
            )
            # Reflect the mutation in the in-memory counts.
            job_status_counts[STATUS_FAILED_TERMINAL] = (
                job_status_counts.get(STATUS_FAILED_TERMINAL, 0)
                + force_failed_job_count
            )
            # For partial exhaustion, only zero out the
            # non-terminal counts for the force-failed types. For full
            # budget exhaustion, zero out all (all types were
            # force-failed). We approximate by zeroing all non-terminal
            # counts when force_failed_job_count == non_terminal_job_count
            # (full exhaustion), otherwise leave the counts as-is — the
            # classifier uses failed_terminal presence, not exact
            # non-terminal counts.
            if force_failed_job_count >= non_terminal_job_count:
                for status in NON_TERMINAL_JOB_STATUSES:
                    job_status_counts[status] = 0
            # For partial exhaustion where some non-terminal jobs
            # survive (non-exhausted layers), we must NOT finalize
            # yet — the record still has in-flight work. Return
            # non_terminal_jobs_present so the worker loop re-scans.
            # Same applies to full exhaustion when a
            # non-budget-layer job (e.g. display_title) survives.
            if summary.stopped_reason in (
                "partial_budget_exhausted",
                "budget_exhausted",
            ):
                remaining_non_terminal = (
                    non_terminal_job_count - force_failed_job_count
                )
                if remaining_non_terminal > 0:
                    return CompletionFinalizationResult(
                        finalized=False,
                        skip_reason="non_terminal_jobs_present",
                        job_status_counts=job_status_counts,
                        window_status_counts=window_status_counts,
                    )

        non_terminal_window_count = sum(
            window_status_counts.get(status, 0)
            for status in NON_TERMINAL_ANALYSIS_WINDOW_STATUSES
        )
        force_failed_window_count = 0
        if non_terminal_window_count > 0:
            # All enhancement jobs are terminal, but analysis windows are
            # still pending/running. The per-record advisory lock
            # guarantees the pipeline runner already exhausted every
            # worker in ``worker_order`` — the window worker returned
            # ``no_job`` (not registered / cannot reclaim a stuck
            # ``running`` lease). These windows are stuck, not in-flight.
            #
            # The candidate scan only re-picks records with
            # ``runnable_job_count > 0``; a record with all-terminal jobs
            # would never be re-scanned. Leaving the windows pending would
            # therefore wedge the record in ``article_ready`` /
            # ``initial_enhancement_ready`` forever. Force-fail them so the
            # durable state is truthful, then finalize as
            # ``completed_with_failures`` — mirroring the v1 design that
            # grammar window issues do not block ``coverage_complete``
            # (diagnostics capture the failure).
            force_failed_window_count = (
                await self._repository.force_fail_non_terminal_analysis_windows(
                    conn,
                    record_id=record_id,
                    base_id=base_id,
                    expected_generation=expected_generation,
                    failure_code=FINALIZER_FORCED_WINDOW_FAILURE_CODE,
                    failure_reason=FINALIZER_FORCED_WINDOW_FAILURE_REASON,
                    updated_at=updated_at,
                )
            )
            # Reflect the mutation in the in-memory counts so the
            # classifier and event payload report the post-fail state.
            # Under the per-record advisory lock, ``force_failed_window_count``
            # equals ``non_terminal_window_count``.
            window_status_counts["failed"] = (
                window_status_counts.get("failed", 0) + force_failed_window_count
            )
            for status in NON_TERMINAL_ANALYSIS_WINDOW_STATUSES:
                window_status_counts[status] = 0

        outcome = _classify_completion_outcome(
            job_status_counts, window_status_counts
        )

        updated, previous_readiness_state = (
            await self._repository.update_record_readiness_state_if_active(
                conn,
                record_id=record_id,
                expected_generation=expected_generation,
                current_readiness_states=COMPLETION_ELIGIBLE_READINESS_STATES,
                next_readiness_state=COMPLETION_TARGET_READINESS_STATE,
                updated_at=updated_at,
            )
        )
        if not updated or previous_readiness_state is None:
            return CompletionFinalizationResult(
                finalized=False,
                skip_reason="readiness_state_update_did_not_apply",
                outcome=outcome,
                job_status_counts=job_status_counts,
                window_status_counts=window_status_counts,
                force_failed_window_count=force_failed_window_count,
            )

        published_event = await event_runtime.publish_event_in_transaction(
            conn,
            record_id=record_id,
            event_type=RECORD_COMPLETION_FINALIZED_EVENT_TYPE,
            payload_json=build_completion_finalized_event_payload(
                outcome=outcome,
                job_status_counts=job_status_counts,
                window_status_counts=window_status_counts,
                stopped_reason=summary.stopped_reason,
                readiness_state=COMPLETION_TARGET_READINESS_STATE,
                previous_readiness_state=previous_readiness_state,
                force_failed_window_count=force_failed_window_count,
            ),
            created_at=updated_at,
        )

        return CompletionFinalizationResult(
            finalized=True,
            outcome=outcome,
            readiness_state_updated=True,
            job_status_counts=job_status_counts,
            window_status_counts=window_status_counts,
            force_failed_window_count=force_failed_window_count,
            event_sequence=published_event.sequence,
        )


__all__ = [
    "COMPLETION_ELIGIBLE_READINESS_STATES",
    "COMPLETION_TARGET_READINESS_STATE",
    "FINALIZER_FORCED_WINDOW_FAILURE_CODE",
    "FINALIZER_FORCED_WINDOW_FAILURE_REASON",
    "NON_FINALIZABLE_STOPPED_REASONS",
    "RECORD_COMPLETION_FINALIZED_EVENT_TYPE",
    "CompletionFinalizationResult",
    "CompletionFinalizer",
    "CompletionOutcome",
    "CompletionSkipReason",
    "build_completion_finalized_event_payload",
    "should_attempt_finalization",
]
