"""Unit tests for Agentic Reading Record Ask history projection (reload path)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.schemas.reader_ask import ReaderAskMessage
from app.schemas.reader_record_ask_stream import (
    EXECUTION_VERSION_AGENTIC_V1,
    EXECUTION_VERSION_AGENTIC_V2,
    ReaderRecordAskHistoryMessage,
)
from app.services.reader_record_ask.repository import _message_row_to_history
from app.services.reader_record_ask.history_projection import (
    project_agentic_history_message,
)
from app.services.reader_record_ask.reasoning_projection import (
    DEFAULT_PROJECTION_CHAR_CAP,
)

_HANDLE = "evh_" + ("ab" * 16)

_COMPLETED_V2 = {
    "execution_version": EXECUTION_VERSION_AGENTIC_V2,
    "final_status": "ok",
    "answer_text": "Climate change is discussed in paragraph 2.",
    "answer_blocks": [
        {
            "text": "Climate change is discussed in paragraph 2.",
            "citation_ids": ["c1"],
        }
    ],
    "citations": [
        {
            "citation_id": "c1",
            "source_kind": "article",
            "snippet": "climate change impacts",
        }
    ],
    "knowledge_mode": "article_grounded",
    "source_status": None,
    "message_id": "msg-1",
    "thread_id": "thread-1",
    "turn_run_id": "turn-run-1",
}

_SOURCE_UNAVAILABLE_V2 = {
    "execution_version": EXECUTION_VERSION_AGENTIC_V2,
    "final_status": "ok",
    "answer_text": "无法在当前文章中可靠定位原文。",
    "answer_blocks": [],
    "citations": [],
    "knowledge_mode": None,
    "source_status": "article_source_unavailable",
    "message_id": "msg-1",
    "thread_id": "thread-1",
    "turn_run_id": "turn-run-1",
}

_LEGACY_V1 = {
    "execution_version": EXECUTION_VERSION_AGENTIC_V1,
    "final_status": "ok",
    "answer_text": "Climate change is discussed in paragraph 2.",
    "message_id": "msg-1",
    "thread_id": "thread-1",
    "turn_run_id": "turn-run-1",
    "envelope_fingerprint": "env-fp-secret",
    "evidence": [
        {
            "handle_id": _HANDLE,
            "kind": "search_hit",
            "source_tool": "search_current_article",
            "snippet": "climate change impacts",
            "unit_id": "u1",
            "anchor_segment_id": "s1",
            "rag_citation": {
                "rag_substrate_id": "substrate-1",
                "index_run_id": "index-run-1",
                "plan_content_sha256": "plan-sha-abc",
                "source_scope": "main_reading_text",
                "block_type": "paragraph",
                "chunk_id": "chunk-1",
                "content_sha256": "content-sha-def",
                "canonical_text_start_utf16": 10,
                "canonical_text_end_utf16": 42,
                "snippet": "climate change impacts",
                "score": 0.91,
                "stable_document_id": "doc-stable-1",
                "base_id": "base-1",
                "record_generation": 1,
                "block_ids": ["b1"],
                "unit_ids": ["u1"],
                "anchor_segment_ids": ["s1"],
            },
        }
    ],
}


def _assert_no_evh(payload: Any) -> None:
    blob = json.dumps(payload, ensure_ascii=False)
    for forbidden in (
        "evh_",
        "handle_id",
        "cited_evidence_handles",
        "envelope_fingerprint",
        "env-fp-secret",
        "rag_substrate_id",
        "substrate-1",
    ):
        assert forbidden not in blob


def _base_kwargs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "message_id": "msg-1",
        "thread_id": "thread-1",
        "role": "assistant",
        "row_status": "completed",
        "row_content_md": "Climate change is discussed in paragraph 2.",
        "created_at": "2026-07-14T00:00:00+00:00",
        "updated_at": "2026-07-14T00:00:00+00:00",
        "context_anchors": [],
        "usage_event_id": None,
        "current_turn_run_id": "turn-run-1",
        "current_turn_run": {"id": "turn-run-1", "status": "completed"},
        "user_visible_output_json": _COMPLETED_V2,
        "resolved_evidence_json": [{"citation_id": "c1", "handle_id": _HANDLE}],
        "final_status": "ok",
        "turn_run_status": "completed",
    }
    base.update(overrides)
    return base


def test_completed_v2_projects_public_blocks_and_citations() -> None:
    projected = project_agentic_history_message(**_base_kwargs())

    assert projected["status"] == "completed"
    assert projected["content_md"] == "Climate change is discussed in paragraph 2."
    assert projected["execution_version"] == EXECUTION_VERSION_AGENTIC_V2
    assert projected["final_status"] == "ok"
    assert projected["knowledge_mode"] == "article_grounded"
    assert projected["source_status"] is None
    assert projected["legacy_classification"] is None
    assert projected["agentic_answer_blocks"] == [
        {
            "text": "Climate change is discussed in paragraph 2.",
            "citation_ids": ["c1"],
        }
    ]
    assert projected["agentic_citations"] == [
        {
            "citation_id": "c1",
            "source_kind": "article",
            "snippet": "climate change impacts",
        }
    ]
    assert projected["evidence"] == []
    assert projected["article_rag"] is None
    assert projected["current_user_visible_output"] is None
    _assert_no_evh(projected)
    ReaderRecordAskHistoryMessage.model_validate(projected)


def test_source_unavailable_projects_fixed_copy() -> None:
    projected = project_agentic_history_message(
        **_base_kwargs(
            user_visible_output_json=_SOURCE_UNAVAILABLE_V2,
            row_content_md="无法在当前文章中可靠定位原文。",
            resolved_evidence_json=[],
        )
    )
    assert projected["status"] == "completed"
    assert projected["content_md"] == "无法在当前文章中可靠定位原文。"
    assert projected["source_status"] == "article_source_unavailable"
    assert projected["knowledge_mode"] is None
    assert projected["agentic_answer_blocks"] == []
    assert projected["agentic_citations"] == []
    _assert_no_evh(projected)
    ReaderRecordAskHistoryMessage.model_validate(projected)


def test_legacy_v1_history_is_quarantined_fail_closed() -> None:
    projected = project_agentic_history_message(
        **_base_kwargs(user_visible_output_json=_LEGACY_V1)
    )
    assert projected["status"] == "failed"
    assert projected["content_md"] == ""
    assert projected["execution_version"] is None
    assert projected["final_status"] == "failed"
    assert projected["legacy_classification"] is None
    assert projected["agentic_answer_blocks"] is None
    assert projected["agentic_citations"] is None
    assert projected["knowledge_mode"] is None
    _assert_no_evh(projected)
    ReaderRecordAskHistoryMessage.model_validate(projected)


def test_resolved_evidence_never_projected_to_history() -> None:
    projected = project_agentic_history_message(
        **_base_kwargs(
            resolved_evidence_json=[
                {
                    "citation_id": "c1",
                    "handle_id": _HANDLE,
                    "rag_citation": {"rag_substrate_id": "substrate-1"},
                }
            ]
        )
    )
    assert projected["status"] == "completed"
    serialized = json.dumps(projected)
    assert _HANDLE not in serialized
    assert "substrate-1" not in serialized


@pytest.mark.parametrize(
    ("final_status", "expected_ui_status"),
    [
        ("failed", "failed"),
        ("context_stale", "interrupted"),
        ("invalid_citations", "interrupted"),
        ("cancelled", "interrupted"),
    ],
)
def test_terminal_projects_without_fake_answer(
    final_status: str,
    expected_ui_status: str,
) -> None:
    terminal_dto = {
        "execution_version": EXECUTION_VERSION_AGENTIC_V2,
        "final_status": final_status,
        "message_id": "msg-1",
        "thread_id": "thread-1",
        "turn_run_id": "turn-run-1",
        "terminal_reason": "internal diagnostic: do-not-leak",
    }
    projected = project_agentic_history_message(
        **_base_kwargs(
            row_status="failed",
            row_content_md="",
            user_visible_output_json=terminal_dto,
            resolved_evidence_json=[],
            final_status=final_status,
            turn_run_status="failed" if final_status == "failed" else "stale",
        )
    )

    assert projected["status"] == expected_ui_status
    assert projected["content_md"] == ""
    assert projected["final_status"] == final_status
    assert projected["agentic_answer_blocks"] is None
    assert projected["agentic_citations"] is None
    assert projected["evidence"] == []
    assert "terminal_reason" not in projected
    assert "do-not-leak" not in str(projected)
    ReaderRecordAskHistoryMessage.model_validate(projected)


def test_db_json_final_status_mismatch_degrades() -> None:
    terminal_dto = {
        "execution_version": EXECUTION_VERSION_AGENTIC_V2,
        "final_status": "context_stale",
        "message_id": "msg-1",
        "thread_id": "thread-1",
        "turn_run_id": "turn-run-1",
        "terminal_reason": "json says stale",
    }
    projected = project_agentic_history_message(
        **_base_kwargs(
            row_status="failed",
            row_content_md="",
            user_visible_output_json=terminal_dto,
            resolved_evidence_json=[],
            final_status="failed",
            turn_run_status="failed",
        )
    )
    assert projected["status"] == "failed"
    assert projected["content_md"] == ""
    assert projected["final_status"] == "failed"
    assert projected["agentic_answer_blocks"] is None
    assert "json says stale" not in str(projected)


def test_json_cannot_invent_ok_when_db_final_status_missing() -> None:
    projected = project_agentic_history_message(
        **_base_kwargs(
            final_status=None,
            turn_run_status="failed",
            user_visible_output_json=_COMPLETED_V2,
        )
    )
    assert projected["status"] == "failed"
    assert projected["content_md"] == ""
    assert projected["agentic_answer_blocks"] is None


def test_corrupt_completed_payload_degrades_to_legacy_or_failed() -> None:
    projected = project_agentic_history_message(
        **_base_kwargs(
            user_visible_output_json={
                "execution_version": EXECUTION_VERSION_AGENTIC_V2,
                "final_status": "ok",
            },
            row_content_md="",
            resolved_evidence_json=[{"bogus": True}],
        )
    )
    # Incomplete v2 blob without answer_text and without row content → degrade.
    assert projected["status"] == "failed"
    assert projected["agentic_answer_blocks"] is None
    assert projected["evidence"] == []


def _agentic_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": "msg-1",
        "thread_id": "thread-1",
        "role": "assistant",
        "status": "completed",
        "content_md": "Climate change is discussed in paragraph 2.",
        "context_anchors_json": [],
        "citations_json": [],
        "action_proposals_json": [],
        "tool_trace_json": [],
        "metadata_json": {"execution_version": EXECUTION_VERSION_AGENTIC_V2},
        "message_current_turn_run_id": "turn-run-1",
        "usage_event_id": None,
        "created_at": "2026-07-14T00:00:00+00:00",
        "updated_at": "2026-07-14T00:00:00+00:00",
        "current_turn_run_id": "turn-run-1",
        "current_turn_run_user_id": "user-1",
        "current_turn_run_analysis_record_id": None,
        "current_turn_run_reading_record_id": "record-1",
        "current_turn_run_base_id": "base-1",
        "current_turn_run_generation": 1,
        "current_turn_run_turn_id": "turn-1",
        "current_turn_run_run_attempt": 1,
        "current_turn_run_supersedes_run_id": None,
        "current_turn_run_status": "completed",
        "current_turn_run_resolved_intent": None,
        "user_visible_output_json": _COMPLETED_V2,
        "usage_summary_json": None,
        "current_turn_run_usage_event_id": None,
        "current_turn_run_started_at": None,
        "current_turn_run_completed_at": None,
        "current_turn_run_failed_at": None,
        "current_turn_run_created_at": None,
        "current_turn_run_updated_at": None,
        "current_turn_run_execution_version": EXECUTION_VERSION_AGENTIC_V2,
        "current_turn_run_final_status": "ok",
        "current_turn_run_terminal_reason": None,
        "current_turn_run_resolved_evidence_json": [
            {"citation_id": "c1", "handle_id": _HANDLE}
        ],
        "current_turn_run_envelope_fingerprint": "env-fp-secret",
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
    row.update(overrides)
    row["turn_run_id"] = row.get("current_turn_run_id")
    row["turn_run_user_id"] = row.get("current_turn_run_user_id")
    row["turn_run_reading_record_id"] = row.get("current_turn_run_reading_record_id")
    row["turn_run_status"] = row.get("current_turn_run_status")
    row["turn_run_final_status"] = row.get("current_turn_run_final_status")
    row["turn_run_terminal_reason"] = row.get("current_turn_run_terminal_reason")
    row["turn_run_execution_version"] = row.get("current_turn_run_execution_version")
    row["turn_run_resolved_evidence_json"] = row.get(
        "current_turn_run_resolved_evidence_json"
    )
    row["turn_run_envelope_fingerprint"] = row.get(
        "current_turn_run_envelope_fingerprint"
    )
    return row


def test_message_row_to_history_agentic_completed_bypasses_legacy_evidence() -> None:
    message = _message_row_to_history(_agentic_row())

    assert message["status"] == "completed"
    assert message["content_md"] == "Climate change is discussed in paragraph 2."
    assert message["execution_version"] == EXECUTION_VERSION_AGENTIC_V2
    assert message["final_status"] == "ok"
    assert message["agentic_citations"] is not None
    assert message["agentic_citations"][0]["citation_id"] == "c1"
    assert message.get("evidence", []) == []
    assert message["article_rag"] is None
    _assert_no_evh(message)
    ReaderAskMessage.model_validate(
        {
            k: v
            for k, v in message.items()
            if k
            not in {
                "execution_version",
                "final_status",
                "agentic_answer_blocks",
                "agentic_citations",
                "knowledge_mode",
                "source_status",
                "legacy_classification",
            }
        }
    )
    ReaderRecordAskHistoryMessage.model_validate(message)


def test_message_row_to_history_quarantines_json_version_without_column() -> None:
    row = _agentic_row(
        current_turn_run_execution_version=None,
        user_visible_output_json={**_COMPLETED_V2},
        current_turn_run_final_status=None,
    )
    message = _message_row_to_history(row)

    assert message["status"] == "failed"
    assert message["content_md"] == ""
    assert message.get("execution_version") is None
    assert message.get("agentic_citations") is None
    assert message["evidence"] == []
    assert message["article_rag"] is None
    _assert_no_evh(message)
    ReaderRecordAskHistoryMessage.model_validate(message)


def test_message_row_to_history_agentic_terminal_no_fake_answer() -> None:
    terminal_dto = {
        "execution_version": EXECUTION_VERSION_AGENTIC_V2,
        "final_status": "context_stale",
        "message_id": "msg-1",
        "thread_id": "thread-1",
        "turn_run_id": "turn-run-1",
        "terminal_reason": "generation mismatch secret",
    }
    message = _message_row_to_history(
        _agentic_row(
            status="failed",
            content_md="",
            current_turn_run_status="stale",
            current_turn_run_final_status="context_stale",
            current_turn_run_terminal_reason="generation mismatch secret",
            user_visible_output_json=terminal_dto,
            current_turn_run_resolved_evidence_json=[],
        )
    )
    assert message["status"] == "interrupted"
    assert message["content_md"] == ""
    assert message["final_status"] == "context_stale"
    assert message["agentic_citations"] is None
    assert message["evidence"] == []
    assert "generation mismatch secret" not in str(message)
    ReaderRecordAskHistoryMessage.model_validate(message)


def test_message_row_to_history_legacy_row_is_quarantined() -> None:
    row = {
        "id": "msg-legacy",
        "thread_id": "thread-1",
        "role": "assistant",
        "status": "completed",
        "content_md": "legacy body",
        "context_anchors_json": [],
        "citations_json": [],
        "action_proposals_json": [],
        "tool_trace_json": [],
        "metadata_json": {},
        "message_current_turn_run_id": "run-legacy",
        "usage_event_id": None,
        "created_at": "2026-07-14T00:00:00+00:00",
        "updated_at": "2026-07-14T00:00:00+00:00",
        "current_turn_run_id": "run-legacy",
        "current_turn_run_user_id": "user-1",
        "current_turn_run_analysis_record_id": "record-1",
        "current_turn_run_reading_record_id": None,
        "current_turn_run_base_id": None,
        "current_turn_run_generation": None,
        "current_turn_run_turn_id": "turn-legacy",
        "current_turn_run_run_attempt": 1,
        "current_turn_run_supersedes_run_id": None,
        "current_turn_run_status": "completed",
        "current_turn_run_resolved_intent": "explain",
        "user_visible_output_json": {
            "content_md": "legacy body",
            "evidence": [
                {
                    "kind": "citation",
                    "label": "legacy cite",
                    "detail": "from legacy path",
                    "scope": "current_record",
                    "metadata_json": {},
                }
            ],
            "citations": [],
            "action_proposals": [],
            "tool_trace": [],
            "response_cards": [],
            "supplement_candidates": [],
            "persisted_supplements": [],
        },
        "usage_summary_json": None,
        "current_turn_run_usage_event_id": None,
        "current_turn_run_started_at": None,
        "current_turn_run_completed_at": None,
        "current_turn_run_failed_at": None,
        "current_turn_run_created_at": None,
        "current_turn_run_updated_at": None,
        "current_turn_run_execution_version": None,
        "current_turn_run_final_status": None,
        "current_turn_run_terminal_reason": None,
        "current_turn_run_resolved_evidence_json": None,
        "current_turn_run_envelope_fingerprint": None,
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
    message = _message_row_to_history(row)
    assert message["status"] == "failed"
    assert message["content_md"] == ""
    assert message.get("execution_version") is None
    assert message["evidence"] == []


def test_message_row_to_history_user_message_preserves_content_md_with_agentic_metadata() -> None:
    """ASK-UX-HISTORY-COT-R2 P0-1: cold-loaded user messages must keep
    their ``content_md`` even when ``metadata_json`` carries an
    ``execution_version`` marker from the retry snapshot.

    Real shape (see submission_gateway.ensure_submission_for_send +
    repository.ensure_submission_message_pair):
    - role='user', status='completed', content_md=<user text>
    - metadata_json has execution_version='reader_record_ask_agentic_v2'
      (retry snapshot marker, not an agentic output payload claim)
    - current_turn_run_id is NULL — user messages never own a turn run

    Previously the quarantine branch fired on
    ``claims_agentic_payload(metadata)`` and wiped content_md to "",
    producing an empty user bubble on cold load.
    """
    row = {
        "id": "msg-user-1",
        "thread_id": "thread-1",
        "role": "user",
        "status": "completed",
        "content_md": "这篇文章的主旨是什么？",
        "context_anchors_json": [],
        "citations_json": [],
        "action_proposals_json": [],
        "tool_trace_json": [],
        "metadata_json": {
            "retry_contract_version": "ask_retry_contract_r5",
            "execution_version": EXECUTION_VERSION_AGENTIC_V2,
            "model_option_key": None,
            "route_identity": None,
            "web_search_mode": "disabled",
            "retry_snapshot": {
                "retry_contract_version": "ask_retry_contract_r5",
                "execution_version": EXECUTION_VERSION_AGENTIC_V2,
            },
        },
        "message_current_turn_run_id": None,
        "usage_event_id": None,
        "created_at": "2026-07-14T00:00:00+00:00",
        "updated_at": "2026-07-14T00:00:00+00:00",
        "current_turn_run_id": None,
        "current_turn_run_user_id": None,
        "current_turn_run_analysis_record_id": None,
        "current_turn_run_reading_record_id": None,
        "current_turn_run_base_id": None,
        "current_turn_run_generation": None,
        "current_turn_run_turn_id": None,
        "current_turn_run_run_attempt": None,
        "current_turn_run_supersedes_run_id": None,
        "current_turn_run_status": None,
        "current_turn_run_resolved_intent": None,
        "user_visible_output_json": None,
        "usage_summary_json": None,
        "current_turn_run_usage_event_id": None,
        "current_turn_run_started_at": None,
        "current_turn_run_completed_at": None,
        "current_turn_run_failed_at": None,
        "current_turn_run_created_at": None,
        "current_turn_run_updated_at": None,
        "current_turn_run_execution_version": None,
        "current_turn_run_final_status": None,
        "current_turn_run_terminal_reason": None,
        "current_turn_run_resolved_evidence_json": None,
        "current_turn_run_envelope_fingerprint": None,
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
    message = _message_row_to_history(row)

    assert message["role"] == "user"
    assert message["status"] == "completed"
    # content_md must be preserved verbatim — not wiped by quarantine.
    assert message["content_md"] == "这篇文章的主旨是什么？"
    # No agentic-projection fields should be synthesized for user rows.
    assert message.get("execution_version") is None
    assert message.get("agentic_answer_blocks") is None
    assert message.get("agentic_citations") is None
    assert message.get("evidence", []) == []
    ReaderAskMessage.model_validate(message)


# ---------------------------------------------------------------------------
# ASK-REASONING-R1: cold-history reasoning projection
# ---------------------------------------------------------------------------

_REASONING_PROJECTION = {
    "projection_policy_version": "reasoning_projection_v1",
    "text": "先分析句子主干，再确认从句关系。",
    "char_count": len("先分析句子主干，再确认从句关系。"),
    "truncated": False,
}


def test_ok_turn_does_not_restore_legacy_provider_reasoning() -> None:
    """Cold history retires v1 provider reasoning fail-closed."""
    projected = project_agentic_history_message(
        **_base_kwargs(
            current_turn_run={
                "id": "turn-run-1",
                "status": "completed",
                "reasoning_projection_json": _REASONING_PROJECTION,
            }
        )
    )
    assert projected["reasoning_md"] is None
    assert projected["reasoning_status"] is None
    assert projected["reasoning_truncated"] is None
    assert _REASONING_PROJECTION["text"] not in json.dumps(
        projected, ensure_ascii=False
    )
    # The raw JSONB payload never rides on the wire turn_run dict.
    assert "reasoning_projection_json" not in (projected["current_turn_run"] or {})
    blob = json.dumps(projected, ensure_ascii=False)
    assert "projection_policy_version" not in blob.split('"reasoning_md"')[0]
    ReaderRecordAskHistoryMessage.model_validate(projected)


def test_ok_turn_without_reasoning_projects_none_fields() -> None:
    projected = project_agentic_history_message(**_base_kwargs())
    assert projected["reasoning_md"] is None
    assert projected["reasoning_status"] is None
    ReaderRecordAskHistoryMessage.model_validate(projected)


def test_ok_turn_with_malformed_reasoning_payload_fails_closed() -> None:
    for bad_payload in (
        None,
        "not-a-dict",
        {"text": "缺版本号"},
        {"projection_policy_version": "reasoning_projection_v1", "text": ""},
        {"projection_policy_version": "reasoning_projection_v1", "text": "   "},
        {"projection_policy_version": "reasoning_projection_v1"},
    ):
        projected = project_agentic_history_message(
            **_base_kwargs(
                current_turn_run={
                    "id": "turn-run-1",
                    "status": "completed",
                    "reasoning_projection_json": bad_payload,
                }
            )
        )
        assert projected["reasoning_md"] is None, f"payload={bad_payload!r}"
        assert projected["reasoning_status"] is None, f"payload={bad_payload!r}"


def test_terminal_turn_never_resurrects_reasoning() -> None:
    """Fail-closed: terminal rows carry no cold reasoning even if a stray
    projection payload existed on the turn run."""
    terminal_visible = {
        "execution_version": EXECUTION_VERSION_AGENTIC_V2,
        "final_status": "failed",
        "message_id": "msg-1",
        "thread_id": "thread-1",
        "turn_run_id": "turn-run-1",
        "terminal_reason": "agent_run_failed",
    }
    projected = project_agentic_history_message(
        **_base_kwargs(
            user_visible_output_json=terminal_visible,
            resolved_evidence_json=[],
            final_status="failed",
            row_status="failed",
            row_content_md="",
            turn_run_status="failed",
            current_turn_run={
                "id": "turn-run-1",
                "status": "failed",
                "reasoning_projection_json": _REASONING_PROJECTION,
            },
        )
    )
    assert projected["status"] == "failed"
    assert projected["reasoning_md"] is None
    assert projected["reasoning_status"] is None
    assert "reasoning_projection_json" not in (projected["current_turn_run"] or {})


# ---------------------------------------------------------------------------
# ASK-REASONING-R2: canonical snapshot validation at the cold-read boundary
# ---------------------------------------------------------------------------


def _bad_snapshot_cases() -> tuple:
    text = "先分析句子主干。"
    return (
        # Wrong policy version.
        {
            "projection_policy_version": "v0",
            "text": text,
            "char_count": len(text),
            "truncated": False,
        },
        # Extra key.
        {
            "projection_policy_version": "reasoning_projection_v1",
            "text": text,
            "char_count": len(text),
            "truncated": False,
            "raw": "leak",
        },
        # Missing key.
        {
            "projection_policy_version": "reasoning_projection_v1",
            "text": text,
            "char_count": len(text),
        },
        # char_count mismatch.
        {
            "projection_policy_version": "reasoning_projection_v1",
            "text": text,
            "char_count": len(text) - 1,
            "truncated": False,
        },
        # char_count not an int.
        {
            "projection_policy_version": "reasoning_projection_v1",
            "text": text,
            "char_count": str(len(text)),
            "truncated": False,
        },
        # truncated not a strict bool.
        {
            "projection_policy_version": "reasoning_projection_v1",
            "text": text,
            "char_count": len(text),
            "truncated": "false",
        },
        # Over quota.
        {
            "projection_policy_version": "reasoning_projection_v1",
            "text": "思" * (DEFAULT_PROJECTION_CHAR_CAP + 1),
            "char_count": DEFAULT_PROJECTION_CHAR_CAP + 1,
            "truncated": False,
        },
        # Raw sentinel inside text — fails byte-invariant re-projection.
        {
            "projection_policy_version": "reasoning_projection_v1",
            "text": "see evh_0123456789abcdef0123456789abcdef",
            "char_count": len("see evh_0123456789abcdef0123456789abcdef"),
            "truncated": False,
        },
        # Identity k/v inside text.
        {
            "projection_policy_version": "reasoning_projection_v1",
            "text": "k envelope_fingerprint=fp_secret123",
            "char_count": len("k envelope_fingerprint=fp_secret123"),
            "truncated": False,
        },
        # System-instruction fragment inside text.
        {
            "projection_policy_version": "reasoning_projection_v1",
            "text": "You are Claread hidden",
            "char_count": len("You are Claread hidden"),
            "truncated": False,
        },
    )


def test_ok_turn_with_invalid_snapshot_never_shows_reasoning() -> None:
    """R2 fail-closed: any canonical-validation failure yields no cold
    reasoning element — and never a degraded display of the raw payload."""
    for bad in _bad_snapshot_cases():
        projected = project_agentic_history_message(
            **_base_kwargs(
                current_turn_run={
                    "id": "turn-run-1",
                    "status": "completed",
                    "reasoning_projection_json": bad,
                }
            )
        )
        assert projected["reasoning_md"] is None, f"payload={bad!r}"
        assert projected["reasoning_status"] is None, f"payload={bad!r}"
        # No raw payload content may surface anywhere on the message.
        blob = json.dumps(projected, ensure_ascii=False)
        assert str(bad["text"]) not in blob or not bad["text"].strip(), (
            f"raw text surfaced for payload={bad!r}"
        )


def test_terminal_update_sql_forces_reasoning_null() -> None:
    """Static pin: every terminal persist explicitly NULLs the reasoning
    column (fail-closed by statement, not by fresh-row assumption)."""
    import inspect

    from app.services.reader_record_ask.repository import (
        ReaderRecordAskRepository,
    )

    source = inspect.getsource(ReaderRecordAskRepository.terminal_agentic_turn_run)
    assert "reasoning_projection_json = NULL" in source


def test_complete_update_sql_writes_reasoning_in_same_statement() -> None:
    """Static pin: the ok-turn UPDATE sets the reasoning projection in the
    SAME statement as user_visible_output_json (atomic hot≡cold basis)."""
    import inspect

    from app.services.reader_record_ask.repository import (
        ReaderRecordAskRepository,
    )

    source = inspect.getsource(ReaderRecordAskRepository.complete_agentic_turn_run)
    update_start = source.index("UPDATE reader_ask_turn_runs")
    update_end = source.index("WHERE id = $1", update_start)
    update_stmt = source[update_start:update_end]
    assert "reasoning_projection_json = $6::jsonb" in update_stmt
    assert "user_visible_output_json = $3::jsonb" in update_stmt
