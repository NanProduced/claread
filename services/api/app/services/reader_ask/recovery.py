"""Failure / Recovery: cleanup plan builders for Ask Claread failure paths.

This module provides pure functions that compute *what* needs to happen on
failure (refund, message/turn_run status, eval trace, failure event).
The actual side-effects (repo calls, billing API) are executed by service.py
based on the returned plans.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from uuid import UUID

from app.agents.reader_ask_agent import ReaderAskRuntimeState
from app.services.credits import CreditReservation
from app.schemas.reader_ask import (
    ReaderAskAnchorRef,
    ReaderAskAttachment,
    ReaderAskContextPlan,
    ReaderAskDisambiguation,
    ReaderAskResolvedContextInput,
    ReaderAskResolvedIntent,
    ReaderAskTraceSummary,
)
from app.services.reader_ask import planner
from app.services.reader_ask import prompt_preparation as prompt_preparation_svc


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ReplanContextTooLargeError(Exception):
    """Raised when replan prompt exceeds budget — skip replan, use original answer."""


# ---------------------------------------------------------------------------
# Plan data structures
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class RefundPlan:
    """Refund plan: pure data, executed by service."""
    reservation: CreditReservation
    metadata: dict[str, Any]


@dataclass(slots=True)
class MessageFailedPlan:
    """Message failed update plan."""
    message_id: UUID
    content_md: str
    metadata: dict[str, Any]
    current_turn_run_id: UUID | None


@dataclass(slots=True)
class TurnRunFailedPlan:
    """TurnRun failed update plan."""
    turn_run_id: UUID
    user_visible_output_json: dict[str, Any]


@dataclass(slots=True)
class EvalTracePlan:
    """Eval trace upsert plan."""
    turn_run_id: UUID
    planning_snapshot: planner.ReaderAskPlanningSnapshot | None
    runtime_state: ReaderAskRuntimeState
    context_plan: ReaderAskContextPlan | None
    trace_summary: ReaderAskTraceSummary | None


@dataclass(slots=True)
class FailureEventPlan:
    """Failure event record plan."""
    user_id: UUID
    record_id: UUID
    thread_id: UUID
    user_message: str
    start_perf: float
    error_code: str
    error_message: str
    metadata_json: dict[str, Any]


@dataclass(slots=True)
class ContextTooLargeCleanupPlan:
    """Complete cleanup plan for CONTEXT_TOO_LARGE early returns."""
    refund: RefundPlan | None
    message_failed: MessageFailedPlan
    turn_run_failed: TurnRunFailedPlan | None
    eval_trace: EvalTracePlan | None
    failure_event: FailureEventPlan | None


# ---------------------------------------------------------------------------
# Metadata builders (pure functions)
# ---------------------------------------------------------------------------

def build_refund_metadata(
    *,
    error_code: str,
    thread_id: UUID,
    record_id: UUID,
    retry_message_id: UUID | None = None,
) -> dict[str, Any]:
    """Build refund metadata dict."""
    metadata: dict[str, Any] = {
        "reason": error_code,
        "thread_id": str(thread_id),
        "record_id": str(record_id),
    }
    if retry_message_id is not None:
        metadata["retry_message_id"] = str(retry_message_id)
    return metadata


def build_failure_event_metadata(
    *,
    anchor_payload: list[dict[str, Any]],
    tool_trace: list[Any],
    retry_message_id: UUID | None = None,
) -> dict[str, Any]:
    """Build failure event metadata dict."""
    metadata: dict[str, Any] = {
        "anchor_count": len(anchor_payload),
        "tool_names": [e.tool_name for e in tool_trace],
    }
    if retry_message_id is not None:
        metadata["retry_message_id"] = str(retry_message_id)
    return metadata


# ---------------------------------------------------------------------------
# Unused reservation calculator (pure function)
# ---------------------------------------------------------------------------

def build_unused_reservation(
    reservation: CreditReservation,
    actual_cost_points: int,
) -> CreditReservation:
    """Calculate the unused portion of a credit reservation for partial refund.

    Prioritizes consuming daily quota first, then bonus.  Returns a
    CreditReservation representing the refundable remainder.
    """
    if actual_cost_points >= reservation.total_points:
        return CreditReservation(total_points=0, deducted_from_daily=0, deducted_from_bonus=0)

    used_daily = min(actual_cost_points, reservation.deducted_from_daily)
    used_bonus = max(actual_cost_points - used_daily, 0)
    refund_daily = reservation.deducted_from_daily - used_daily
    refund_bonus = reservation.deducted_from_bonus - used_bonus
    refund_total = max(refund_daily, 0) + max(refund_bonus, 0)
    return CreditReservation(
        total_points=refund_total,
        deducted_from_daily=max(refund_daily, 0),
        deducted_from_bonus=max(refund_bonus, 0),
    )


# ---------------------------------------------------------------------------
# Context too large cleanup plan builder
# ---------------------------------------------------------------------------

def build_context_too_large_cleanup_plan(
    *,
    user_id: UUID,
    thread_id: UUID,
    record_id: UUID | None,
    reservation: CreditReservation | None,
    assistant_message_id: UUID,
    active_turn_run_id: UUID | None,
    runtime_state: ReaderAskRuntimeState,
    resolved_intent: ReaderAskResolvedIntent | None,
    resolved_context_input: ReaderAskResolvedContextInput | None,
    run_info: dict[str, Any] | None,
    submission_mode: str,
    anchor_payload: list[dict[str, Any]],
    error_code: str,
    retry_message_id: UUID | None = None,
    compaction_audit: list[str] | None = None,
    trace_summary: ReaderAskTraceSummary | None = None,
    # Callbacks for building turn_run output (delegated to service)
    build_message_metadata_cb: Callable[..., dict[str, Any]],
    build_turn_run_output_cb: Callable[..., dict[str, Any]] | None = None,
    # Additional data needed by callbacks
    run_history: list[dict[str, Any]] | None = None,
    record_bundle: Any = None,
    resolved_anchors: list[ReaderAskAnchorRef] | None = None,
    attachments: list[ReaderAskAttachment] | None = None,
    reference_resolution: planner.ReaderAskReferenceResolution | None = None,
    disambiguation: ReaderAskDisambiguation | None = None,
    external_asset_disambiguation: Any = None,
    planning_snapshot: planner.ReaderAskPlanningSnapshot | None = None,
    context_plan: ReaderAskContextPlan | None = None,
    persisted_supplements_json: list[dict[str, Any]] | None = None,
    user_message_text: str = "",
    start_perf: float = 0.0,
    thread: dict[str, Any] | None = None,
) -> ContextTooLargeCleanupPlan:
    """Build a complete cleanup plan for CONTEXT_TOO_LARGE early return.

    This is a pure function: it computes *what* needs to happen but does
    not execute any side effects.  The caller (service.py) is responsible
    for executing the plan steps.
    """
    # Step 0: Inject compaction audit into trace_summary
    if compaction_audit:
        trace_summary = prompt_preparation_svc.inject_compaction_audit(trace_summary, compaction_audit)

    # Step 1: Refund plan
    refund: RefundPlan | None = None
    if reservation is not None and reservation.total_points > 0 and record_id is not None:
        refund = RefundPlan(
            reservation=reservation,
            metadata=build_refund_metadata(
                error_code=error_code,
                thread_id=thread_id,
                record_id=record_id,
                retry_message_id=retry_message_id,
            ),
        )

    # Step 2: Message failed plan
    message_metadata = build_message_metadata_cb(
        resolved_intent=resolved_intent,
        run_info=run_info,
        run_history=run_history,
        resolved_context_input=resolved_context_input,
        submission_mode=submission_mode,
    )
    message_failed = MessageFailedPlan(
        message_id=assistant_message_id,
        content_md="",
        metadata=message_metadata,
        current_turn_run_id=active_turn_run_id,
    )

    # Step 3: TurnRun failed + eval trace plan
    turn_run_failed: TurnRunFailedPlan | None = None
    eval_trace: EvalTracePlan | None = None
    if active_turn_run_id is not None and record_id is not None and build_turn_run_output_cb is not None:
        failed_output_json = build_turn_run_output_cb(
            content_md="",
            reasoning_md=None,
            reasoning_status=None,
            submission_mode=submission_mode,
            resolved_intent=resolved_intent,
            record=record_bundle,
            anchors=resolved_anchors or [],
            attachments=attachments or [],
            runtime_state=runtime_state,
            reference_resolution=reference_resolution,
            disambiguation=disambiguation,
            external_asset_disambiguation=external_asset_disambiguation,
            trace_summary=trace_summary,
            context_plan=context_plan,
            resolved_context_input=resolved_context_input,
            run_info=run_info,
            persisted_supplements=persisted_supplements_json or [],
        )
        turn_run_failed = TurnRunFailedPlan(
            turn_run_id=active_turn_run_id,
            user_visible_output_json=failed_output_json,
        )
        eval_trace = EvalTracePlan(
            turn_run_id=active_turn_run_id,
            planning_snapshot=planning_snapshot,
            runtime_state=runtime_state,
            context_plan=context_plan,
            trace_summary=trace_summary,
        )

    # Step 4: Failure event plan
    failure_event: FailureEventPlan | None = None
    if record_id is not None and thread is not None:
        failure_event = FailureEventPlan(
            user_id=user_id,
            record_id=record_id,
            thread_id=thread_id,
            user_message=user_message_text,
            start_perf=start_perf,
            error_code=error_code,
            error_message="CONTEXT_TOO_LARGE",
            metadata_json=build_failure_event_metadata(
                anchor_payload=anchor_payload,
                tool_trace=runtime_state.tool_trace,
                retry_message_id=retry_message_id,
            ),
        )

    return ContextTooLargeCleanupPlan(
        refund=refund,
        message_failed=message_failed,
        turn_run_failed=turn_run_failed,
        eval_trace=eval_trace,
        failure_event=failure_event,
    )
