from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from pydantic_ai import RunContext

from app.agents.reader_ask_agent import (
    ReaderAskAgentDeps,
    ReaderAskRuntimeState,
    _propose_save_highlight_tool,
    _propose_save_note_tool,
)
from app.contracts.annotation import compute_text_range_hash
from app.schemas.reader_ask import (
    ReaderAskActionConfirmRequest,
    ReaderAskActionProposal,
    ReaderAskAnchorRef,
    ReaderAskReadingRecordAnchor,
    ReaderAskWriteProposalPayload,
)
from app.services.reader_ask import service as reader_ask_service


def _reading_record_anchor_payload(
    *,
    selected_text: str = "Hello",
    record_id: str = "record-1",
) -> dict[str, Any]:
    return {
        "record_id": record_id,
        "base_id": "base-1",
        "generation": 3,
        "unit_id": "unit-1",
        "anchor_segment_id": "segment-1",
        "scope": "stable_source",
        "offset_unit": "utf16",
        "start_offset": 0,
        "end_offset": len(selected_text),
        "selected_text": selected_text,
        "text_hash": compute_text_range_hash(selected_text),
        "hash_algorithm": "fnv1a32-utf16",
    }


def _reading_record_anchor(
    *,
    record_id: str = "record-1",
) -> ReaderAskReadingRecordAnchor:
    return ReaderAskReadingRecordAnchor.model_validate(
        _reading_record_anchor_payload(record_id=record_id)
    )


def _legacy_anchor() -> ReaderAskAnchorRef:
    return ReaderAskAnchorRef(
        anchor_type="sentence",
        target_key="record:r1:sentence:s1",
        sentence_id="s1",
        selected_text="Hello",
    )


def _make_deps(
    *,
    primary_anchor: ReaderAskAnchorRef | None = None,
) -> ReaderAskAgentDeps:
    return ReaderAskAgentDeps(
        payload={},
        event_queue=AsyncMock(),
        state=ReaderAskRuntimeState(),
        query_seed="test",
        task_mode="explain",
        record_id="record-1",
        record_title="Test",
        primary_anchor=primary_anchor,
        get_record_context_fn=AsyncMock(return_value={"summary": "Context loaded"}),
        get_record_insights_fn=AsyncMock(return_value=[]),
        get_user_vocabulary_book_fn=AsyncMock(return_value=[]),
        resolve_known_reference_fn=AsyncMock(return_value={"status": "not_found"}),
        load_explicit_attachment_context_fn=AsyncMock(
            return_value={"status": "not_found", "ok": False}
        ),
        generate_sentence_annotation_fn=AsyncMock(return_value=None),
        suggest_prompts_fn=AsyncMock(return_value={"suggestions": []}),
        vocabulary_item_to_citation_fn=MagicMock(),
    )


def _make_ctx(deps: ReaderAskAgentDeps) -> RunContext[ReaderAskAgentDeps]:
    ctx = MagicMock(spec=RunContext)
    ctx.deps = deps
    return ctx


def test_save_note_and_highlight_proposals_accept_reading_record_anchor() -> None:
    anchor_payload = _reading_record_anchor_payload()

    for action_type in ("save_note", "save_highlight"):
        payload_json: dict[str, Any] = {
            "record_id": "record-1",
            "anchor": anchor_payload,
        }
        if action_type == "save_note":
            payload_json["note_text"] = "Keep this point"

        proposal = ReaderAskActionProposal(
            id=f"{action_type}-1",
            action_type=action_type,
            label="Test",
            payload_json=payload_json,
        )
        payload = ReaderAskWriteProposalPayload.model_validate(proposal.payload_json)

        assert isinstance(payload.anchor, ReaderAskReadingRecordAnchor)
        assert payload.anchor.anchor_segment_id == "segment-1"
        assert payload.anchor.generation == 3


def test_legacy_save_proposal_payload_still_accepts_old_anchor_and_target_fields() -> None:
    anchor = _legacy_anchor()

    proposal = ReaderAskActionProposal(
        id="legacy-note-1",
        action_type="save_note",
        label="Test",
        payload_json={
            "record_id": "r1",
            "anchor": anchor.model_dump(mode="json"),
            "target_key": anchor.target_key,
            "target_sentence_id": anchor.sentence_id,
            "note_text": "Legacy note",
        },
    )
    payload = ReaderAskWriteProposalPayload.model_validate(proposal.payload_json)

    assert isinstance(payload.anchor, ReaderAskAnchorRef)
    assert payload.anchor.target_key == "record:r1:sentence:s1"
    assert payload.target_key == "record:r1:sentence:s1"
    assert payload.target_sentence_id == "s1"

    target_only = ReaderAskActionProposal(
        id="legacy-highlight-1",
        action_type="save_highlight",
        label="Test",
        payload_json={
            "record_id": "r1",
            "target_key": "record:r1:sentence:s1",
            "target_sentence_id": "s1",
        },
    )
    assert target_only.payload_json["target_sentence_id"] == "s1"


def test_malformed_reading_record_anchor_is_rejected_by_schema() -> None:
    malformed_anchor = _reading_record_anchor_payload()
    malformed_anchor["text_hash"] = "00000000"

    with pytest.raises(ValidationError):
        ReaderAskActionProposal(
            id="bad-anchor-1",
            action_type="save_highlight",
            label="Test",
            payload_json={
                "record_id": "record-1",
                "anchor": malformed_anchor,
            },
        )


def test_agent_tools_only_create_new_anchor_proposals_without_db_writes() -> None:
    deps = _make_deps(primary_anchor=None)
    ctx = _make_ctx(deps)
    anchor = _reading_record_anchor()

    with (
        patch(
            "app.services.user_annotations.create_user_annotation",
            new_callable=AsyncMock,
        ) as create_annotation,
        patch(
            "app.services.reader_notes.create_reader_note",
            new_callable=AsyncMock,
        ) as create_note,
        patch(
            "app.services.reader_ask.supplements.create_supplement",
            new_callable=AsyncMock,
        ) as create_supplement,
        patch(
            "app.services.reader_orchestration.anchor_gate.load_validated_reading_record_anchor",
            new_callable=AsyncMock,
        ) as validate_anchor,
    ):
        note_result = asyncio.run(
            _propose_save_note_tool(ctx, note_text="Keep this point", anchor=anchor)
        )
        highlight_result = asyncio.run(_propose_save_highlight_tool(ctx, anchor=anchor))

    assert note_result["status"] == "success"
    assert highlight_result["status"] == "success"
    assert [request.action_type for request in deps.state.action_requests] == [
        "save_note",
        "save_highlight",
    ]
    for request in deps.state.action_requests:
        proposal = ReaderAskActionProposal(
            id=f"{request.action_type}-proposal",
            action_type=request.action_type,
            label=request.label,
            payload_json=request.payload_json,
        )
        proposal_anchor = proposal.payload_json["anchor"]
        assert proposal_anchor["anchor_segment_id"] == "segment-1"
        assert proposal_anchor["generation"] == 3
        assert "target_key" not in proposal.payload_json
        assert "target_sentence_id" not in proposal.payload_json

    create_annotation.assert_not_called()
    create_note.assert_not_called()
    create_supplement.assert_not_called()
    validate_anchor.assert_not_called()


def test_agent_tools_reject_cross_record_reading_record_anchor() -> None:
    deps = _make_deps(primary_anchor=None)
    ctx = _make_ctx(deps)
    anchor = _reading_record_anchor(record_id="record-2")

    note_result = asyncio.run(
        _propose_save_note_tool(ctx, note_text="Keep this point", anchor=anchor)
    )
    highlight_result = asyncio.run(_propose_save_highlight_tool(ctx, anchor=anchor))

    assert note_result["status"] == "error"
    assert highlight_result["status"] == "error"
    assert note_result["summary"] == "Reading Record anchor record_id mismatch"
    assert highlight_result["summary"] == "Reading Record anchor record_id mismatch"
    assert deps.state.action_requests == []
    assert deps.state.tool_call_count == 0


@pytest.mark.asyncio
async def test_confirm_reading_record_anchor_proposal_returns_pending_without_write() -> None:
    action_id = "reading-record-anchor-action-1"
    proposal_dict = {
        "id": action_id,
        "action_type": "save_highlight",
        "label": "保存为高亮",
        "status": "pending",
        "payload_json": {
            "record_id": "record-1",
            "anchor": _reading_record_anchor_payload(),
        },
    }
    message_dict = {
        "id": "msg-1",
        "thread_id": "thread-1",
        "role": "assistant",
        "status": "completed",
        "content_md": "done",
        "action_proposals": [proposal_dict],
        "created_at": "2026-06-23T00:00:00Z",
        "updated_at": "2026-06-23T00:00:00Z",
    }

    with (
        patch.object(
            reader_ask_service.repo,
            "find_action_proposal",
            new=AsyncMock(return_value=(message_dict, proposal_dict)),
        ),
        patch.object(
            reader_ask_service.repo,
            "get_thread",
            new=AsyncMock(
                return_value={"record_id": "00000000-0000-0000-0000-000000000001"}
            ),
        ),
        patch.object(
            reader_ask_service.repo,
            "update_message",
            new=AsyncMock(),
        ) as update_message,
        patch.object(
            reader_ask_service.user_annotations_svc,
            "create_user_annotation",
            new=AsyncMock(),
        ) as create_annotation,
        patch.object(
            reader_ask_service.reader_notes_svc,
            "create_reader_note",
            new=AsyncMock(),
        ) as create_note,
    ):
        with pytest.raises(HTTPException) as excinfo:
            await reader_ask_service.confirm_action(
                user_id=uuid4(),
                thread_id=uuid4(),
                action_id=action_id,
                body=ReaderAskActionConfirmRequest(confirmed=True),
            )

    assert excinfo.value.status_code == 409
    assert (
        excinfo.value.detail["code"]
        == reader_ask_service.READING_RECORD_ANCHOR_CONFIRM_PENDING
    )
    update_message.assert_not_called()
    create_annotation.assert_not_called()
    create_note.assert_not_called()
