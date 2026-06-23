from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.schemas.reader_orchestration import ReadingRecordProductState

from .pipeline_runner import (
    PipelineAttemptOutcome,
    PipelineStoppedReason,
    ReaderPipelineRunSummary,
)

PRODUCT_STATE_UPDATED_EVENT_TYPE = "record_product_state_updated"

# D6-P4 keeps terminal failure mapping fail-closed. Only explicit, user-remediable
# attention codes may surface as action_required; worker/system/provider/tool/runtime
# failures stay in failed.
USER_ACTION_REQUIRED_ATTENTION_CODES: frozenset[str] = frozenset(
    {"reader_user_confirmation_required"}
)

_NO_STATE_CHANGE_STOPPED_REASONS = frozenset(
    {
        "all_workers_no_job",
        "max_ticks_reached",
        "max_jobs_reached",
    }
)
_NO_STATE_CHANGE_OUTCOMES = frozenset(
    {
        "retry_later",
        "superseded",
    }
)


@dataclass(frozen=True, slots=True)
class ProductStateDecision:
    next_product_state: ReadingRecordProductState | None
    reason_code: str
    user_visible: bool
    should_update_record: bool


def decide_failed_terminal_product_state(
    attention_code: str | None,
) -> ProductStateDecision:
    if attention_code in USER_ACTION_REQUIRED_ATTENTION_CODES:
        return ProductStateDecision(
            next_product_state="action_required",
            reason_code=attention_code,
            user_visible=True,
            should_update_record=True,
        )

    return ProductStateDecision(
        next_product_state="failed",
        reason_code=attention_code or "failed_terminal",
        user_visible=False,
        should_update_record=True,
    )


def decide_product_state_update(
    *,
    stopped_reason: PipelineStoppedReason,
    stopped_outcome: PipelineAttemptOutcome | None,
    attention_code: str | None,
) -> ProductStateDecision:
    if stopped_reason in _NO_STATE_CHANGE_STOPPED_REASONS:
        return ProductStateDecision(
            next_product_state=None,
            reason_code=stopped_reason,
            user_visible=False,
            should_update_record=False,
        )

    if stopped_reason != "attention_required":
        return ProductStateDecision(
            next_product_state=None,
            reason_code=stopped_reason,
            user_visible=False,
            should_update_record=False,
        )

    if stopped_outcome is None:
        return ProductStateDecision(
            next_product_state=None,
            reason_code="attention_required_without_outcome",
            user_visible=False,
            should_update_record=False,
        )

    if stopped_outcome in _NO_STATE_CHANGE_OUTCOMES:
        return ProductStateDecision(
            next_product_state=None,
            reason_code=attention_code or stopped_outcome,
            user_visible=False,
            should_update_record=False,
        )

    if stopped_outcome != "failed_terminal":
        return ProductStateDecision(
            next_product_state=None,
            reason_code=attention_code or stopped_outcome,
            user_visible=False,
            should_update_record=False,
        )

    return decide_failed_terminal_product_state(attention_code)


def decide_product_state_for_pipeline_summary(
    summary: ReaderPipelineRunSummary,
) -> ProductStateDecision:
    return decide_product_state_update(
        stopped_reason=summary.stopped_reason,
        stopped_outcome=summary.stopped_outcome,
        attention_code=summary.attention_code,
    )


def build_product_state_event_payload(
    *,
    decision: ProductStateDecision,
    attention_code: str | None,
    stopped_reason: PipelineStoppedReason,
    stopped_outcome: PipelineAttemptOutcome | None,
) -> dict[str, Any]:
    if decision.next_product_state is None:
        raise ValueError("product_state event payload requires next_product_state")

    return {
        "product_state": decision.next_product_state,
        "reason_code": decision.reason_code,
        "user_visible": decision.user_visible,
        "attention_code": attention_code,
        "stopped_reason": stopped_reason,
        "stopped_outcome": stopped_outcome,
    }
