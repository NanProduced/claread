from __future__ import annotations

from typing import Any

import pytest

from app.services.reader_ask.repository import _message_row_to_dict, _thread_row_to_dict


def test_thread_row_hydrates_selected_model_key() -> None:
    row = {
        "id": "thread-1",
        "record_id": "record-1",
        "title": "Ask Claread",
        "is_default": True,
        "selected_model_key": "glm-fast",
        "archived_at": None,
        "created_at": None,
        "updated_at": None,
        "last_message_at": None,
    }

    thread = _thread_row_to_dict(row)

    assert thread["selected_model_key"] == "glm-fast"


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


def test_message_row_hydrates_interrupted_reasoning_with_started_but_no_content() -> None:
    """When reasoning.started fired but no delta arrived, the interrupted run
    should hydrate reasoning_status="completed" (terminal) and reasoning_md=None
    (because emitted_reasoning was empty, and "" or None → None)."""
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
        "current_turn_run_status": "interrupted",
        "current_turn_run_resolved_intent": "explain",
        "user_visible_output_json": {
            "content_md": "",
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
            "reasoning_md": None,
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

    assert message["status"] == "interrupted"
    assert message["reasoning_md"] is None
    assert message["reasoning_status"] == "completed"


# ---------------------------------------------------------------------------
# P0-4: Stream Checkpoint Shape Contract
# ---------------------------------------------------------------------------


def test_hydration_reads_all_contract_fields_from_user_visible_output() -> None:
    """Repository hydration must read every field in HYDRATION_READ_FIELDS."""
    from app.services.reader_ask.output_contract import HYDRATION_READ_FIELDS

    # Build a full user_visible_output_json with non-None values for every field
    # so we can verify that hydration actually reads each one.
    full_output: dict[str, Any] = {
        "content_md": "hydrated content",
        "submission_mode": "chat",
        "resolved_intent": "explain",
        "citations": [{"source_label": "c1", "anchor_text": "t1"}],
        "action_proposals": [{"action_type": "add_supplement", "label": "add"}],
        "tool_trace": [{"tool_name": "search", "status": "completed"}],
        "evidence": [{"kind": "current_record", "label": "record"}],
        "trace_summary": {"steps": 3},
        "disambiguation": {"candidates": []},
        "external_asset_disambiguation": {"candidates": []},
        "response_cards": [{"card_type": "grammar_note_card"}],
        "usage_summary": {"input_tokens": 5, "output_tokens": 10},
        "billed_points": 2,
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
        "context_plan": {"entry_action": "ask_about_this"},
        "resolved_context_input": {
            "page_identity": {"record_id": "record-1", "title": "Test"},
            "entry_action": "ask_about_this",
            "attachments": [],
            "anchors": [],
        },
        "run_info": {"turn_id": "turn-1", "run_id": "run-1", "attempt": 1},
        "supplement_candidates": [],
        "persisted_supplements": [],
        "reasoning_md": "thinking content",
        "reasoning_status": "completed",
        "follow_up_suggestions": [
            {"label": "Next", "prompt": "Tell me more"},
            {"label": "Save", "prompt": "Save as note"},
        ],
        "article_rag_citations": [
            {"citation_id": "rag-1", "kind": "article_rag_context", "label": "RAG source"},
        ],
        "article_rag": {
            "status": "stale_due_to_repair",
            "failure_code": "article_rag_repair_citations_dropped",
            "retryable": False,
            "fallback_allowed": True,
            "should_attach": False,
            "context_ids": [],
            "source_pack_hash": None,
            "query_sha256": None,
            "citations": [],
        },
    }

    # Verify the test data covers all contract fields
    assert set(full_output.keys()) >= HYDRATION_READ_FIELDS | {"usage_summary", "billed_points"}

    row: dict[str, Any] = {
        "id": "msg-1",
        "thread_id": "thread-1",
        "role": "assistant",
        "status": "completed",
        "content_md": "fallback",
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
        "user_visible_output_json": full_output,
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

    # Verify every hydration-read field was read from user_visible_output_json
    # (not from the fallback row values). For list fields, we check that the
    # hydrated value matches what we put in the output dict.
    for field in HYDRATION_READ_FIELDS:
        value = message.get(field)
        expected = full_output.get(field)
        assert value == expected, (
            f"Hydration field '{field}': expected {expected!r}, got {value!r}"
        )

    # Specific spot checks for key fields
    assert message["content_md"] == "hydrated content"
    assert message["reasoning_md"] == "thinking content"
    assert message["reasoning_status"] == "completed"
    assert message["response_cards"] == [{"card_type": "grammar_note_card"}]
    assert message["supplement_candidates"] == []
    assert message["persisted_supplements"] == []


def test_hydration_contract_fields_match_output_contract() -> None:
    """HYDRATION_READ_FIELDS must be a subset of USER_VISIBLE_OUTPUT_FIELDS."""
    from app.services.reader_ask.output_contract import (
        HYDRATION_READ_FIELDS,
        USER_VISIBLE_OUTPUT_FIELDS,
    )

    assert HYDRATION_READ_FIELDS <= USER_VISIBLE_OUTPUT_FIELDS
    # usage_summary and billed_points are written but not hydrated from the JSON
    assert USER_VISIBLE_OUTPUT_FIELDS - HYDRATION_READ_FIELDS == {"usage_summary", "billed_points"}
