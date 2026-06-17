"""Tests for reader_ask recovery: cleanup plan builders for failure paths."""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.agents.reader_ask_agent import ReaderAskRuntimeState
from app.services.analysis.credit_service import CreditReservation
from app.services.reader_ask import recovery as recovery_svc


# ---------------------------------------------------------------------------
# build_unused_reservation
# ---------------------------------------------------------------------------

class TestBuildUnusedReservation:
    def test_full_refund_when_no_usage(self) -> None:
        reservation = CreditReservation(total_points=10, deducted_from_daily=8, deducted_from_bonus=2)
        unused = recovery_svc.build_unused_reservation(reservation, actual_cost_points=0)
        assert unused.total_points == 10
        assert unused.deducted_from_daily == 8
        assert unused.deducted_from_bonus == 2

    def test_partial_refund(self) -> None:
        reservation = CreditReservation(total_points=10, deducted_from_daily=8, deducted_from_bonus=2)
        unused = recovery_svc.build_unused_reservation(reservation, actual_cost_points=3)
        assert unused.total_points == 7
        assert unused.deducted_from_daily == 5
        assert unused.deducted_from_bonus == 2

    def test_no_refund_when_fully_used(self) -> None:
        reservation = CreditReservation(total_points=10, deducted_from_daily=8, deducted_from_bonus=2)
        unused = recovery_svc.build_unused_reservation(reservation, actual_cost_points=10)
        assert unused.total_points == 0

    def test_no_refund_when_over_used(self) -> None:
        reservation = CreditReservation(total_points=10, deducted_from_daily=8, deducted_from_bonus=2)
        unused = recovery_svc.build_unused_reservation(reservation, actual_cost_points=15)
        assert unused.total_points == 0

    def test_bonus_only_reservation(self) -> None:
        reservation = CreditReservation(total_points=5, deducted_from_daily=0, deducted_from_bonus=5)
        unused = recovery_svc.build_unused_reservation(reservation, actual_cost_points=2)
        assert unused.total_points == 3
        assert unused.deducted_from_daily == 0
        assert unused.deducted_from_bonus == 3


# ---------------------------------------------------------------------------
# build_refund_metadata
# ---------------------------------------------------------------------------

class TestBuildRefundMetadata:
    def test_basic_metadata(self) -> None:
        thread_id = uuid4()
        record_id = uuid4()
        metadata = recovery_svc.build_refund_metadata(
            error_code="reader_ask_failed",
            thread_id=thread_id,
            record_id=record_id,
        )
        assert metadata["reason"] == "reader_ask_failed"
        assert metadata["thread_id"] == str(thread_id)
        assert metadata["record_id"] == str(record_id)
        assert "retry_message_id" not in metadata

    def test_retry_metadata_includes_retry_message_id(self) -> None:
        retry_id = uuid4()
        metadata = recovery_svc.build_refund_metadata(
            error_code="reader_ask_retry_failed",
            thread_id=uuid4(),
            record_id=uuid4(),
            retry_message_id=retry_id,
        )
        assert metadata["retry_message_id"] == str(retry_id)


# ---------------------------------------------------------------------------
# build_failure_event_metadata
# ---------------------------------------------------------------------------

class TestBuildFailureEventMetadata:
    def test_basic_metadata(self) -> None:
        anchor_payload = [{"anchor_type": "text_range"}]
        tool_trace = [MagicMock(tool_name="search_vocabulary")]
        metadata = recovery_svc.build_failure_event_metadata(
            anchor_payload=anchor_payload,
            tool_trace=tool_trace,
        )
        assert metadata["anchor_count"] == 1
        assert metadata["tool_names"] == ["search_vocabulary"]
        assert "retry_message_id" not in metadata

    def test_retry_metadata_includes_retry_message_id(self) -> None:
        retry_id = uuid4()
        metadata = recovery_svc.build_failure_event_metadata(
            anchor_payload=[],
            tool_trace=[],
            retry_message_id=retry_id,
        )
        assert metadata["retry_message_id"] == str(retry_id)


# ---------------------------------------------------------------------------
# build_context_too_large_cleanup_plan
# ---------------------------------------------------------------------------

class TestBuildContextTooLargeCleanupPlan:
    def _make_plan(
        self,
        *,
        reservation: CreditReservation | None = None,
        record_id: UUID | None = None,
        retry_message_id: UUID | None = None,
        compaction_audit: list[str] | None = None,
        active_turn_run_id: UUID | None = None,
        thread: dict | None = None,
    ) -> recovery_svc.ContextTooLargeCleanupPlan:
        return recovery_svc.build_context_too_large_cleanup_plan(
            user_id=uuid4(),
            thread_id=uuid4(),
            record_id=record_id,
            reservation=reservation,
            assistant_message_id=uuid4(),
            active_turn_run_id=active_turn_run_id,
            runtime_state=ReaderAskRuntimeState(),
            resolved_intent="explain",
            resolved_context_input=None,
            run_info=None,
            submission_mode="chat",
            anchor_payload=[],
            error_code="reader_ask_failed",
            retry_message_id=retry_message_id,
            compaction_audit=compaction_audit,
            trace_summary=None,
            build_message_metadata_cb=lambda **kw: {},
            build_turn_run_output_cb=lambda **kw: {"resolved_context": {}} if active_turn_run_id and record_id else None,
            record_bundle=MagicMock() if record_id else None,
            resolved_anchors=[],
            attachments=[],
            reference_resolution=None,
            disambiguation=None,
            external_asset_disambiguation=None,
            planning_snapshot=None,
            context_plan=None,
            persisted_supplements_json=None,
            user_message_text="test",
            start_perf=0.0,
            thread=thread,
        )

    def test_full_cleanup_plan_with_reservation_and_record(self) -> None:
        """Plan includes refund, message_failed, turn_run_failed, eval_trace, failure_event."""
        record_id = uuid4()
        turn_run_id = uuid4()
        reservation = CreditReservation(total_points=10, deducted_from_daily=10, deducted_from_bonus=0)

        plan = self._make_plan(
            reservation=reservation,
            record_id=record_id,
            active_turn_run_id=turn_run_id,
            thread={"id": str(uuid4())},
        )

        assert plan.refund is not None
        assert plan.refund.reservation.total_points == 10
        assert plan.refund.metadata["reason"] == "reader_ask_failed"

        assert plan.message_failed is not None
        assert plan.message_failed.content_md == ""

        assert plan.turn_run_failed is not None
        assert plan.turn_run_failed.turn_run_id == turn_run_id

        assert plan.eval_trace is not None
        assert plan.eval_trace.turn_run_id == turn_run_id

        assert plan.failure_event is not None
        assert plan.failure_event.error_code == "reader_ask_failed"
        assert plan.failure_event.error_message == "CONTEXT_TOO_LARGE"

    def test_no_refund_when_no_reservation(self) -> None:
        """No reservation → no refund action."""
        record_id = uuid4()
        plan = self._make_plan(record_id=record_id)
        assert plan.refund is None

    def test_no_refund_when_zero_points(self) -> None:
        """Reservation with 0 points → no refund action."""
        record_id = uuid4()
        reservation = CreditReservation(total_points=0, deducted_from_daily=0, deducted_from_bonus=0)
        plan = self._make_plan(reservation=reservation, record_id=record_id)
        assert plan.refund is None

    def test_no_refund_when_no_record(self) -> None:
        """No record_id → no refund action."""
        reservation = CreditReservation(total_points=10, deducted_from_daily=10, deducted_from_bonus=0)
        plan = self._make_plan(reservation=reservation, record_id=None)
        assert plan.refund is None

    def test_retry_path_metadata_includes_retry_message_id(self) -> None:
        """Retry path includes retry_message_id in refund and failure event metadata."""
        record_id = uuid4()
        retry_id = uuid4()
        reservation = CreditReservation(total_points=10, deducted_from_daily=10, deducted_from_bonus=0)

        plan = self._make_plan(
            reservation=reservation,
            record_id=record_id,
            retry_message_id=retry_id,
            thread={"id": str(uuid4())},
        )

        assert plan.refund is not None
        assert plan.refund.metadata["retry_message_id"] == str(retry_id)

        assert plan.failure_event is not None
        assert plan.failure_event.metadata_json["retry_message_id"] == str(retry_id)

    def test_compaction_audit_injected_into_trace_summary(self) -> None:
        """Compaction audit should be injected into trace_summary before plan is built."""
        from app.schemas.reader_ask import ReaderAskTraceSummary

        trace_summary = ReaderAskTraceSummary(
            input_tokens=100,
            output_tokens=50,
            context_layers=[],
            notes=["existing_note"],
        )

        plan = recovery_svc.build_context_too_large_cleanup_plan(
            user_id=uuid4(),
            thread_id=uuid4(),
            record_id=uuid4(),
            reservation=CreditReservation(total_points=10, deducted_from_daily=10, deducted_from_bonus=0),
            assistant_message_id=uuid4(),
            active_turn_run_id=uuid4(),
            runtime_state=ReaderAskRuntimeState(),
            resolved_intent="explain",
            resolved_context_input=None,
            run_info=None,
            submission_mode="chat",
            anchor_payload=[],
            error_code="reader_ask_failed",
            compaction_audit=["history", "vocabulary"],
            trace_summary=trace_summary,
            build_message_metadata_cb=lambda **kw: {},
            build_turn_run_output_cb=lambda **kw: {"resolved_context": {}},
            record_bundle=MagicMock(),
            resolved_anchors=[],
            attachments=[],
            reference_resolution=None,
            disambiguation=None,
            external_asset_disambiguation=None,
            planning_snapshot=None,
            context_plan=None,
            persisted_supplements_json=None,
            user_message_text="test",
            start_perf=0.0,
            thread={"id": str(uuid4())},
        )

        # The eval_trace plan should have the updated trace_summary
        assert plan.eval_trace is not None
        assert plan.eval_trace.trace_summary is not None
        notes = plan.eval_trace.trace_summary.notes
        assert "existing_note" in notes
        assert any("context_compaction:history,vocabulary" in n for n in notes)

    def test_no_failure_event_when_no_thread(self) -> None:
        """No thread → no failure event."""
        record_id = uuid4()
        plan = self._make_plan(record_id=record_id, thread=None)
        assert plan.failure_event is None

    def test_no_turn_run_failed_when_no_active_turn_run(self) -> None:
        """No active_turn_run_id → no turn_run_failed or eval_trace."""
        record_id = uuid4()
        plan = self._make_plan(record_id=record_id, active_turn_run_id=None)
        assert plan.turn_run_failed is None
        assert plan.eval_trace is None
