from __future__ import annotations

import pytest

from app.services.reader_ask.repository import _message_row_to_dict


def test_message_row_hydrates_persisted_reasoning_from_user_visible_output() -> None:
    row = {
        "id": "msg-1",
        "thread_id": "thread-1",
        "role": "assistant",
        "status": "completed",
        "content_md": "fallback body",
        "context_anchors_json": [],
        "citations_json": [],
        "action_proposals_json": [],
        "tool_trace_json": [],
        "metadata_json": {},
        "current_turn_run_id": "run-1",
        "usage_event_id": None,
        "created_at": None,
        "updated_at": None,
        "current_turn_run_user_id": "user-1",
        "current_turn_run_record_id": "record-1",
        "current_turn_run_turn_id": "turn-1",
        "current_turn_run_attempt": 1,
        "current_turn_run_supersedes_run_id": None,
        "current_turn_run_status": "completed",
        "current_turn_run_resolved_intent": "explain",
        "user_visible_output_json": {
            "content_md": "final body",
            "resolved_context": {
                "record_id": "record-1",
                "record_title": "Test",
                "anchor_count": 0,
                "explicit_attachment_count": 0,
                "used_cross_record_context": False,
                "current_sentence_used": False,
                "current_paragraph_used": False,
                "used_record_insights": False,
                "used_dictionary": False,
                "source_labels": [],
            },
            "reasoning_md": "先判断句子主干。",
            "reasoning_status": "completed",
        },
        "usage_summary_json": None,
        "current_turn_run_usage_event_id": None,
        "current_turn_run_started_at": None,
        "current_turn_run_completed_at": None,
        "current_turn_run_failed_at": None,
        "current_turn_run_created_at": None,
        "current_turn_run_updated_at": None,
        "eval_trace_turn_run_id": None,
        "trace_schema_version": None,
        "planning_snapshot_json": None,
        "capability_trace_json": None,
        "action_audit_json": None,
        "supplement_audit_json": None,
        "metrics_json": None,
        "eval_trace_created_at": None,
        "eval_trace_updated_at": None,
    }

    message = _message_row_to_dict(row)

    assert message["content_md"] == "final body"
    assert message["reasoning_md"] == "先判断句子主干。"
    assert message["reasoning_status"] == "completed"


@pytest.mark.parametrize(
    ("turn_run_status", "reasoning_status", "expected_message_status"),
    [
        ("streaming", "streaming", "streaming"),
        ("interrupted", "completed", "interrupted"),
    ],
)
def test_message_row_hydrates_reasoning_snapshot_for_non_completed_runs(
    turn_run_status: str,
    reasoning_status: str,
    expected_message_status: str,
) -> None:
    row = {
        "id": "msg-1",
        "thread_id": "thread-1",
        "role": "assistant",
        "status": "streaming",
        "content_md": "",
        "context_anchors_json": [],
        "citations_json": [],
        "action_proposals_json": [],
        "tool_trace_json": [],
        "metadata_json": {},
        "current_turn_run_id": "run-1",
        "usage_event_id": None,
        "created_at": None,
        "updated_at": None,
        "current_turn_run_user_id": "user-1",
        "current_turn_run_record_id": "record-1",
        "current_turn_run_turn_id": "turn-1",
        "current_turn_run_attempt": 1,
        "current_turn_run_supersedes_run_id": None,
        "current_turn_run_status": turn_run_status,
        "current_turn_run_resolved_intent": "explain",
        "user_visible_output_json": {
            "content_md": "已生成的回答片段",
            "resolved_context": {
                "record_id": "record-1",
                "record_title": "Test",
                "anchor_count": 0,
                "explicit_attachment_count": 0,
                "used_cross_record_context": False,
                "current_sentence_used": False,
                "current_paragraph_used": False,
                "used_record_insights": False,
                "used_dictionary": False,
                "source_labels": [],
            },
            "reasoning_md": "已生成的 thinking 片段",
            "reasoning_status": reasoning_status,
        },
        "usage_summary_json": None,
        "current_turn_run_usage_event_id": None,
        "current_turn_run_started_at": None,
        "current_turn_run_completed_at": None,
        "current_turn_run_failed_at": None,
        "current_turn_run_created_at": None,
        "current_turn_run_updated_at": None,
        "eval_trace_turn_run_id": None,
        "trace_schema_version": None,
        "planning_snapshot_json": None,
        "capability_trace_json": None,
        "action_audit_json": None,
        "supplement_audit_json": None,
        "metrics_json": None,
        "eval_trace_created_at": None,
        "eval_trace_updated_at": None,
    }

    message = _message_row_to_dict(row)

    assert message["status"] == expected_message_status
    assert message["content_md"] == "已生成的回答片段"
    assert message["reasoning_md"] == "已生成的 thinking 片段"
    assert message["reasoning_status"] == reasoning_status
