"""Unit tests for Agentic Reading Record Ask history projection (reload path)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest

from app.schemas.reader_ask import ReaderAskMessage
from app.schemas.reader_record_ask_stream import (
    EXECUTION_VERSION_AGENTIC_V1,
    ReaderRecordAskHistoryMessage,
    ReaderRecordAskThreadDetail,
)
from app.services.reader_ask.repository import _message_row_to_dict
from app.services.reader_record_ask.history_projection import (
    project_agentic_history_message,
)

_HANDLE = "evh_" + ("ab" * 16)

_COMPLETE_RAG = {
    "rag_substrate_id": "substrate-1",
    "index_run_id": "index-run-1",
    "index_version": "v1",
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
}

_SEARCH_HIT = {
    "handle_id": _HANDLE,
    "kind": "search_hit",
    "source_tool": "search_current_article",
    "snippet": "climate change impacts",
    "unit_id": "u1",
    "anchor_segment_id": "s1",
    "rag_citation": _COMPLETE_RAG,
}

_EVIDENCE_SCOPE = {
    "reading_record_id": "22222222-2222-2222-2222-222222222222",
    "base_id": "33333333-3333-3333-3333-333333333333",
    "record_generation": 1,
    "stable_document_id": "doc-stable-1",
}

_COMPLETED_DTO = {
    "execution_version": EXECUTION_VERSION_AGENTIC_V1,
    "final_status": "ok",
    "answer_text": "Climate change is discussed in paragraph 2.",
    "message_id": "msg-1",
    "thread_id": "thread-1",
    "turn_run_id": "turn-run-1",
    "envelope_fingerprint": "env-fp-secret",
    "evidence_scope": _EVIDENCE_SCOPE,
    "evidence": [_SEARCH_HIT],
}

# Pre-R3B0 historical wire: no evidence_scope field (nullable compatibility).
_COMPLETED_DTO_LEGACY_NO_SCOPE = {
    "execution_version": EXECUTION_VERSION_AGENTIC_V1,
    "final_status": "ok",
    "answer_text": "Climate change is discussed in paragraph 2.",
    "message_id": "msg-1",
    "thread_id": "thread-1",
    "turn_run_id": "turn-run-1",
    "envelope_fingerprint": "env-fp-secret",
    "evidence": [_SEARCH_HIT],
}


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
        "user_visible_output_json": _COMPLETED_DTO,
        "resolved_evidence_json": [_SEARCH_HIT],
        "final_status": "ok",
        "turn_run_status": "completed",
    }
    base.update(overrides)
    return base


def test_completed_projects_answer_and_agentic_evidence() -> None:
    projected = project_agentic_history_message(**_base_kwargs())

    assert projected["status"] == "completed"
    assert projected["content_md"] == "Climate change is discussed in paragraph 2."
    assert projected["execution_version"] == EXECUTION_VERSION_AGENTIC_V1
    assert projected["final_status"] == "ok"
    assert projected["agentic_evidence"] is not None
    assert len(projected["agentic_evidence"]) == 1
    assert projected["agentic_evidence"][0]["kind"] == "search_hit"
    assert projected["agentic_evidence_scope"] == _EVIDENCE_SCOPE
    assert projected["evidence"] == []
    assert projected["article_rag"] is None
    assert projected["article_rag_citations"] == []
    assert "envelope_fingerprint" not in projected
    assert projected["current_user_visible_output"] is None

    # Strict RR history DTO accepts agentic_evidence + agentic_evidence_scope.
    ReaderRecordAskHistoryMessage.model_validate(projected)


def test_completed_legacy_missing_scope_hydrates_answer_evidence_with_null_scope() -> None:
    """Old v1 JSON without evidence_scope: answer/Sources hydrate; scope stays None.

    Product freeze (R3A/R3B0): navigation must treat null as
    unavailable.legacy_scope_missing for every evidence kind — no page-identity
    or rag_citation-only temporary navigation branch.
    """
    projected = project_agentic_history_message(
        **_base_kwargs(user_visible_output_json=_COMPLETED_DTO_LEGACY_NO_SCOPE)
    )
    assert projected["status"] == "completed"
    assert projected["content_md"] == "Climate change is discussed in paragraph 2."
    assert projected["agentic_evidence"] is not None
    assert projected["agentic_evidence"][0]["kind"] == "search_hit"
    assert projected["agentic_evidence_scope"] is None
    ReaderRecordAskHistoryMessage.model_validate(projected)


def test_completed_explicit_null_scope_projects_null() -> None:
    completed = {**_COMPLETED_DTO_LEGACY_NO_SCOPE, "evidence_scope": None}
    projected = project_agentic_history_message(
        **_base_kwargs(user_visible_output_json=completed)
    )
    assert projected["status"] == "completed"
    assert projected["agentic_evidence"] is not None
    assert projected["agentic_evidence_scope"] is None


def test_malformed_evidence_scope_degrades_without_raw_dict() -> None:
    projected = project_agentic_history_message(
        **_base_kwargs(
            user_visible_output_json={
                **_COMPLETED_DTO_LEGACY_NO_SCOPE,
                "evidence_scope": {"reading_record_id": "only-one-field"},
            }
        )
    )
    assert projected["status"] == "failed"
    assert projected["content_md"] == ""
    assert projected["agentic_evidence"] is None
    assert projected["agentic_evidence_scope"] is None
    assert projected["evidence"] == []
    ReaderRecordAskHistoryMessage.model_validate(projected)


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
        "execution_version": EXECUTION_VERSION_AGENTIC_V1,
        "final_status": final_status,
        "message_id": "msg-1",
        "thread_id": "thread-1",
        "turn_run_id": "turn-run-1",
        "envelope_fingerprint": "env-fp-secret",
        "terminal_reason": "internal diagnostic: do-not-leak",
        "rejected_handles": [],
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
    assert projected["agentic_evidence"] is None
    assert projected["agentic_evidence_scope"] is None
    assert projected["evidence"] == []
    assert projected["article_rag"] is None
    assert "terminal_reason" not in projected
    assert "env-fp-secret" not in str(projected)
    assert "do-not-leak" not in str(projected)
    ReaderRecordAskHistoryMessage.model_validate(projected)


def test_db_json_final_status_mismatch_degrades() -> None:
    """P0: DB final_status column wins; JSON that disagrees is corrupt."""
    terminal_dto = {
        "execution_version": EXECUTION_VERSION_AGENTIC_V1,
        "final_status": "context_stale",
        "message_id": "msg-1",
        "thread_id": "thread-1",
        "turn_run_id": "turn-run-1",
        "envelope_fingerprint": "env-fp-secret",
        "terminal_reason": "json says stale",
        "rejected_handles": [],
    }
    projected = project_agentic_history_message(
        **_base_kwargs(
            row_status="failed",
            row_content_md="",
            user_visible_output_json=terminal_dto,
            resolved_evidence_json=[],
            # DB column says failed, JSON says context_stale.
            final_status="failed",
            turn_run_status="failed",
        )
    )
    assert projected["status"] == "failed"
    assert projected["content_md"] == ""
    assert projected["final_status"] == "failed"
    assert projected["agentic_evidence"] is None
    assert "json says stale" not in str(projected)
    ReaderRecordAskHistoryMessage.model_validate(projected)


def test_json_cannot_invent_ok_when_db_final_status_missing() -> None:
    """Without DB final_status, completed JSON alone must not become completed."""
    projected = project_agentic_history_message(
        **_base_kwargs(
            final_status=None,
            turn_run_status="failed",
            user_visible_output_json=_COMPLETED_DTO,
        )
    )
    assert projected["status"] == "failed"
    assert projected["content_md"] == ""
    assert projected["agentic_evidence"] is None


def test_corrupt_completed_payload_degrades_safely() -> None:
    projected = project_agentic_history_message(
        **_base_kwargs(
            user_visible_output_json={
                "execution_version": EXECUTION_VERSION_AGENTIC_V1,
                "final_status": "ok",
            },
            resolved_evidence_json=[{"bogus": True}],
        )
    )
    assert projected["status"] == "failed"
    assert projected["content_md"] == ""
    assert projected["agentic_evidence"] is None
    assert projected["evidence"] == []
    ReaderRecordAskHistoryMessage.model_validate(projected)


def test_resolved_evidence_is_restricted_fallback_only() -> None:
    completed = {**_COMPLETED_DTO, "evidence": []}
    projected = project_agentic_history_message(
        **_base_kwargs(
            user_visible_output_json=completed,
            resolved_evidence_json=[_SEARCH_HIT],
        )
    )
    assert projected["status"] == "completed"
    assert projected["agentic_evidence"] is not None
    assert projected["agentic_evidence"][0]["handle_id"] == _HANDLE
    assert projected["evidence"] == []


def test_invalid_resolved_evidence_fallback_does_not_pollute_legacy() -> None:
    completed = {**_COMPLETED_DTO, "evidence": []}
    projected = project_agentic_history_message(
        **_base_kwargs(
            user_visible_output_json=completed,
            resolved_evidence_json=[{"not": "an evidence item"}],
        )
    )
    assert projected["agentic_evidence"] == []
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
        "metadata_json": {"execution_version": EXECUTION_VERSION_AGENTIC_V1},
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
        "user_visible_output_json": _COMPLETED_DTO,
        "usage_summary_json": None,
        "current_turn_run_usage_event_id": None,
        "current_turn_run_started_at": None,
        "current_turn_run_completed_at": None,
        "current_turn_run_failed_at": None,
        "current_turn_run_created_at": None,
        "current_turn_run_updated_at": None,
        "current_turn_run_execution_version": EXECUTION_VERSION_AGENTIC_V1,
        "current_turn_run_final_status": "ok",
        "current_turn_run_terminal_reason": None,
        "current_turn_run_resolved_evidence_json": [_SEARCH_HIT],
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
    return row


def test_message_row_to_dict_agentic_completed_bypasses_legacy_evidence() -> None:
    message = _message_row_to_dict(_agentic_row())

    assert message["status"] == "completed"
    assert message["content_md"] == "Climate change is discussed in paragraph 2."
    assert message["execution_version"] == EXECUTION_VERSION_AGENTIC_V1
    assert message["final_status"] == "ok"
    assert message["agentic_evidence"] is not None
    assert message["agentic_evidence"][0]["kind"] == "search_hit"
    assert message["evidence"] == []
    assert message["article_rag"] is None
    assert "envelope_fingerprint" not in message
    # Legacy Analysis Ask schema still accepts base fields without agentic keys.
    ReaderAskMessage.model_validate(
        {
            k: v
            for k, v in message.items()
            if k not in {"execution_version", "final_status", "agentic_evidence"}
        }
    )
    ReaderRecordAskHistoryMessage.model_validate(message)


def test_message_row_to_dict_quarantines_json_version_without_column() -> None:
    """DB version missing + JSON claims v1 → isolate, no raw evidence leak."""
    row = _agentic_row(
        current_turn_run_execution_version=None,
        # JSON still claims agentic, but column is empty → quarantine path.
        user_visible_output_json={
            **_COMPLETED_DTO,
            # agentic evidence with internal rag_citation fields.
        },
        current_turn_run_final_status=None,
    )
    message = _message_row_to_dict(row)

    assert message["status"] == "failed"
    assert message["content_md"] == ""
    # Not a trusted agentic success identity.
    assert message.get("execution_version") is None
    assert message.get("agentic_evidence") is None
    # Must not retain raw agentic evidence via the legacy channel.
    assert message["evidence"] == []
    assert message["article_rag"] is None
    assert message["article_rag_citations"] == []

    serialized = str(message)
    for forbidden in (
        "substrate-1",
        "index-run-1",
        "plan-sha-abc",
        "content-sha-def",
        "env-fp-secret",
        "rag_substrate_id",
        "index_run_id",
        "plan_content_sha256",
        "content_sha256",
        "envelope_fingerprint",
    ):
        assert forbidden not in serialized

    # Quarantine payload must still validate as RR history message.
    ReaderRecordAskHistoryMessage.model_validate(message)


def test_message_row_to_dict_agentic_terminal_no_fake_answer() -> None:
    terminal_dto = {
        "execution_version": EXECUTION_VERSION_AGENTIC_V1,
        "final_status": "context_stale",
        "message_id": "msg-1",
        "thread_id": "thread-1",
        "turn_run_id": "turn-run-1",
        "envelope_fingerprint": "env-fp-secret",
        "terminal_reason": "generation mismatch secret",
        "rejected_handles": [],
    }
    message = _message_row_to_dict(
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
    assert message["agentic_evidence"] is None
    assert message["evidence"] == []
    assert "generation mismatch secret" not in str(message)
    ReaderRecordAskHistoryMessage.model_validate(message)


def test_message_row_to_dict_legacy_row_unchanged() -> None:
    """Legacy visible.evidence still hydrates for non-agentic turns."""
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
    message = _message_row_to_dict(row)
    assert message["content_md"] == "legacy body"
    assert "execution_version" not in message
    assert "final_status" not in message
    assert "agentic_evidence" not in message
    assert len(message["evidence"]) == 1
    assert message["evidence"][0]["kind"] == "citation"
    ReaderAskMessage.model_validate(message)


@pytest.mark.asyncio
async def test_get_reading_record_thread_detail_projects_agentic_completed() -> None:
    from app.services.ask_runtime import thread_service

    user_id = UUID("00000000-0000-0000-0000-000000000001")
    thread_id = UUID("00000000-0000-0000-0000-0000000000aa")
    reading_record_id = UUID("00000000-0000-0000-0000-0000000000bb")

    thread = {
        "id": str(thread_id),
        "record_id": str(reading_record_id),
        "record_scope": "reading_record",
        "reading_record_id": str(reading_record_id),
        "analysis_record_id": None,
        "title": "Ask Claread",
        "is_default": True,
        "selected_model_key": None,
        "archived_at": None,
        "created_at": "2026-07-14T00:00:00+00:00",
        "updated_at": "2026-07-14T00:00:00+00:00",
        "last_message_at": "2026-07-14T00:00:00+00:00",
    }
    agentic_message = _message_row_to_dict(_agentic_row())

    with (
        patch(
            "app.services.ask_runtime.thread_service.repo.get_thread",
            new=AsyncMock(return_value=thread),
        ),
        patch(
            "app.services.ask_runtime.thread_service.repo.list_messages",
            new=AsyncMock(return_value=[agentic_message]),
        ),
    ):
        detail = await thread_service.get_reading_record_thread_detail(
            user_id,
            thread_id,
            reading_record_id=reading_record_id,
        )

    assert isinstance(detail, ReaderRecordAskThreadDetail)
    assert len(detail.messages) == 1
    msg = detail.messages[0]
    assert msg.status == "completed"
    assert msg.content_md == "Climate change is discussed in paragraph 2."
    assert msg.execution_version == EXECUTION_VERSION_AGENTIC_V1
    assert msg.final_status == "ok"
    assert msg.agentic_evidence is not None
    assert msg.agentic_evidence[0].kind == "search_hit"
    assert msg.evidence == []
    assert msg.article_rag is None


@pytest.mark.asyncio
async def test_get_reading_record_thread_detail_terminal_no_fake_answer() -> None:
    from app.services.ask_runtime import thread_service

    user_id = UUID("00000000-0000-0000-0000-000000000001")
    thread_id = UUID("00000000-0000-0000-0000-0000000000aa")
    reading_record_id = UUID("00000000-0000-0000-0000-0000000000bb")

    thread = {
        "id": str(thread_id),
        "record_id": str(reading_record_id),
        "record_scope": "reading_record",
        "reading_record_id": str(reading_record_id),
        "analysis_record_id": None,
        "title": "Ask Claread",
        "is_default": True,
        "selected_model_key": None,
        "archived_at": None,
        "created_at": "2026-07-14T00:00:00+00:00",
        "updated_at": "2026-07-14T00:00:00+00:00",
        "last_message_at": None,
    }
    terminal_message = _message_row_to_dict(
        _agentic_row(
            status="failed",
            content_md="",
            current_turn_run_status="stale",
            current_turn_run_final_status="context_stale",
            current_turn_run_terminal_reason="generation mismatch secret",
            user_visible_output_json={
                "execution_version": EXECUTION_VERSION_AGENTIC_V1,
                "final_status": "context_stale",
                "message_id": "msg-1",
                "thread_id": "thread-1",
                "turn_run_id": "turn-run-1",
                "envelope_fingerprint": "env-fp-secret",
                "terminal_reason": "generation mismatch secret",
                "rejected_handles": [],
            },
            current_turn_run_resolved_evidence_json=[],
        )
    )

    with (
        patch(
            "app.services.ask_runtime.thread_service.repo.get_thread",
            new=AsyncMock(return_value=thread),
        ),
        patch(
            "app.services.ask_runtime.thread_service.repo.list_messages",
            new=AsyncMock(return_value=[terminal_message]),
        ),
    ):
        detail = await thread_service.get_reading_record_thread_detail(
            user_id,
            thread_id,
            reading_record_id=reading_record_id,
        )

    msg = detail.messages[0]
    assert msg.status == "interrupted"
    assert msg.content_md == ""
    assert msg.final_status == "context_stale"
    assert msg.agentic_evidence is None
    dumped = detail.model_dump(mode="json")
    assert "generation mismatch secret" not in str(dumped)
    assert "env-fp-secret" not in str(dumped)


@pytest.mark.asyncio
async def test_get_thread_detail_analysis_still_uses_legacy_schema() -> None:
    """Analysis Ask detail must remain ReaderAskThreadDetail (no agentic fields)."""
    from app.schemas.reader_ask import ReaderAskThreadDetail
    from app.services.ask_runtime import thread_service

    user_id = UUID("00000000-0000-0000-0000-000000000001")
    thread_id = UUID("00000000-0000-0000-0000-0000000000cc")

    thread = {
        "id": str(thread_id),
        "record_id": "record-analysis-1",
        "record_scope": "analysis",
        "reading_record_id": None,
        "analysis_record_id": "record-analysis-1",
        "title": "Ask Claread",
        "is_default": True,
        "selected_model_key": None,
        "archived_at": None,
        "created_at": "2026-07-14T00:00:00+00:00",
        "updated_at": "2026-07-14T00:00:00+00:00",
        "last_message_at": None,
    }
    legacy_message = {
        "id": "msg-legacy",
        "thread_id": str(thread_id),
        "role": "assistant",
        "status": "completed",
        "content_md": "analysis answer",
        "submission_mode": "chat",
        "resolved_intent": "explain",
        "context_anchors": [],
        "citations": [],
        "action_proposals": [],
        "tool_trace": [],
        "evidence": [],
        "trace_summary": None,
        "disambiguation": None,
        "external_asset_disambiguation": None,
        "response_cards": [],
        "resolved_context": None,
        "context_plan": None,
        "resolved_context_input": None,
        "run_info": None,
        "run_history": [],
        "supplement_candidates": [],
        "persisted_supplements": [],
        "reasoning_md": None,
        "reasoning_status": None,
        "follow_up_suggestions": None,
        "article_rag_citations": [],
        "article_rag": None,
        "usage_event_id": None,
        "created_at": "2026-07-14T00:00:00+00:00",
        "updated_at": "2026-07-14T00:00:00+00:00",
    }

    with (
        patch(
            "app.services.ask_runtime.thread_service.repo.get_thread",
            new=AsyncMock(return_value=thread),
        ),
        patch(
            "app.services.ask_runtime.thread_service.repo.list_messages",
            new=AsyncMock(return_value=[legacy_message]),
        ),
    ):
        detail = await thread_service.get_thread_detail(user_id, thread_id)

    assert isinstance(detail, ReaderAskThreadDetail)
    assert (
        not hasattr(detail.messages[0], "agentic_evidence")
        or getattr(detail.messages[0], "agentic_evidence", None) is None
    )
    assert detail.messages[0].content_md == "analysis answer"
    # Ensure Analysis wire model has no agentic fields.
    assert "execution_version" not in ReaderAskMessage.model_fields
    assert "agentic_evidence" not in ReaderAskMessage.model_fields
    assert "agentic_evidence_scope" not in ReaderAskMessage.model_fields
