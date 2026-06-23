from __future__ import annotations

from uuid import UUID

import pytest

from app.services.reader_orchestration import product_state as product_state_module
from app.services.reader_orchestration.job_bootstrap import (
    EnhancementBootstrapJobCounts,
    EnhancementBootstrapSummary,
)
from app.services.reader_orchestration.pipeline_runner import (
    EnhancementOutcomeCounts,
    EnhancementWorkerTickCounts,
    ReaderPipelineRunSummary,
)


def _summary(
    *,
    stopped_reason: str,
    stopped_outcome: str | None = None,
    attention_code: str | None = None,
) -> ReaderPipelineRunSummary:
    record_id = UUID("00000000-0000-0000-0000-000000000001")
    base_id = UUID("00000000-0000-0000-0000-000000000002")
    return ReaderPipelineRunSummary(
        record_id=record_id,
        base_id=base_id,
        expected_generation=1,
        bootstrap=EnhancementBootstrapSummary(
            record_id=record_id,
            base_id=base_id,
            expected_generation=1,
            last_event_sequence=1,
            job_counts=EnhancementBootstrapJobCounts(),
        ),
        bootstrapped_job_counts=EnhancementBootstrapJobCounts(),
        worker_tick_counts=EnhancementWorkerTickCounts(),
        outcome_counts=EnhancementOutcomeCounts(),
        total_ticks=3,
        total_jobs=1,
        last_event_sequence=1,
        snapshot_reload_recommended=False,
        stopped_reason=stopped_reason,
        stopped_outcome=stopped_outcome,
        attention_code=attention_code,
    )


@pytest.mark.parametrize(
    ("stopped_reason", "reason_code"),
    [
        ("all_workers_no_job", "all_workers_no_job"),
        ("max_ticks_reached", "max_ticks_reached"),
        ("max_jobs_reached", "max_jobs_reached"),
    ],
)
def test_decision_keeps_current_product_state_for_non_attention_stop_reasons(
    stopped_reason: str,
    reason_code: str,
) -> None:
    decision = product_state_module.decide_product_state_update(
        stopped_reason=stopped_reason,
        stopped_outcome=None,
        attention_code=None,
    )

    assert decision.should_update_record is False
    assert decision.next_product_state is None
    assert decision.reason_code == reason_code
    assert decision.user_visible is False


def test_decision_keeps_current_product_state_for_retry_later() -> None:
    decision = product_state_module.decide_product_state_update(
        stopped_reason="attention_required",
        stopped_outcome="retry_later",
        attention_code="temporary_outage",
    )

    assert decision.should_update_record is False
    assert decision.next_product_state is None
    assert decision.reason_code == "temporary_outage"
    assert decision.user_visible is False


def test_decision_keeps_current_product_state_for_superseded_publish_fence() -> None:
    decision = product_state_module.decide_product_state_update(
        stopped_reason="attention_required",
        stopped_outcome="superseded",
        attention_code="publish_fence_failed",
    )

    assert decision.should_update_record is False
    assert decision.next_product_state is None
    assert decision.reason_code == "publish_fence_failed"
    assert decision.user_visible is False


@pytest.mark.parametrize(
    "attention_code",
    [
        "vocabulary_executor_unconfigured",
        "grammar_bundle_executor_unconfigured",
        "model_route_unavailable",
        "publish_fence_failed",
        "model_output_invalid",
        "vocabulary_execution_failed",
        "max_attempts_exceeded",
        None,
    ],
)
def test_decision_maps_failed_terminal_to_failed_by_default(
    attention_code: str | None,
) -> None:
    decision = product_state_module.decide_product_state_update(
        stopped_reason="attention_required",
        stopped_outcome="failed_terminal",
        attention_code=attention_code,
    )

    assert decision.should_update_record is True
    assert decision.next_product_state == "failed"
    assert decision.reason_code == (attention_code or "failed_terminal")
    assert decision.user_visible is False


def test_decision_maps_user_actionable_failed_terminal_to_action_required() -> None:
    decision = product_state_module.decide_product_state_update(
        stopped_reason="attention_required",
        stopped_outcome="failed_terminal",
        attention_code="reader_user_confirmation_required",
    )

    assert decision.should_update_record is True
    assert decision.next_product_state == "action_required"
    assert decision.reason_code == "reader_user_confirmation_required"
    assert decision.user_visible is True


def test_pipeline_summary_helper_uses_same_rules() -> None:
    decision = product_state_module.decide_product_state_for_pipeline_summary(
        _summary(
            stopped_reason="attention_required",
            stopped_outcome="failed_terminal",
            attention_code="model_route_unavailable",
        )
    )

    assert decision.should_update_record is True
    assert decision.next_product_state == "failed"
    assert decision.reason_code == "model_route_unavailable"


def test_build_product_state_event_payload_includes_contract_fields() -> None:
    decision = product_state_module.decide_product_state_update(
        stopped_reason="attention_required",
        stopped_outcome="failed_terminal",
        attention_code="model_route_unavailable",
    )

    payload = product_state_module.build_product_state_event_payload(
        decision=decision,
        attention_code="model_route_unavailable",
        stopped_reason="attention_required",
        stopped_outcome="failed_terminal",
    )

    assert payload == {
        "product_state": "failed",
        "reason_code": "model_route_unavailable",
        "user_visible": False,
        "attention_code": "model_route_unavailable",
        "stopped_reason": "attention_required",
        "stopped_outcome": "failed_terminal",
    }
