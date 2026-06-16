"""Tests for the Ask Claread write proposal gate (P5-4)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from pydantic_ai import RunContext

from app.agents.reader_ask_agent import (
    ReaderAskAgentDeps,
    ReaderAskRuntimeState,
    _propose_save_highlight_tool,
    _propose_save_note_tool,
)
from app.agents.reader_ask_tool_registry import (
    TOOL_PROPOSE_SAVE_HIGHLIGHT,
    TOOL_PROPOSE_SAVE_NOTE,
)
from app.agents.reader_ask_write_gate import (
    MISSING_NOTE_TEXT_PAYLOAD,
    NO_ANCHOR_ERROR_PAYLOAD,
    WriteProposalPrecondition,
    check_write_proposal_precondition,
)
from app.schemas.reader_ask import ReaderAskAnchorRef


def _make_anchor() -> ReaderAskAnchorRef:
    return ReaderAskAnchorRef(
        anchor_type="sentence",
        target_key="record:r1:sentence:s1",
        sentence_id="s1",
        selected_text="test",
    )


def _make_deps(
    *,
    has_anchor: bool = True,
) -> ReaderAskAgentDeps:
    anchor = _make_anchor() if has_anchor else None
    event_queue = AsyncMock()
    state = ReaderAskRuntimeState()
    return ReaderAskAgentDeps(
        payload={},
        event_queue=event_queue,
        state=state,
        query_seed="test",
        task_mode="explain",
        record_id="r1",
        record_title="Test",
        primary_anchor=anchor,
        get_record_context_fn=AsyncMock(return_value={"summary": "Context loaded"}),
        get_record_insights_fn=AsyncMock(return_value=[]),
        get_user_vocabulary_book_fn=AsyncMock(return_value=[]),
        resolve_known_reference_fn=AsyncMock(return_value={"status": "not_found"}),
        load_explicit_attachment_context_fn=AsyncMock(return_value={"status": "not_found", "ok": False}),
        generate_sentence_annotation_fn=AsyncMock(return_value=None),
        suggest_prompts_fn=AsyncMock(return_value={"suggestions": []}),
        vocabulary_item_to_citation_fn=MagicMock(),
    )


def _make_ctx(deps: ReaderAskAgentDeps) -> MagicMock:
    ctx = MagicMock(spec=RunContext)
    ctx.deps = deps
    return ctx


# ---------------------------------------------------------------------------
# Gate: propose_save_note no anchor → hard gate, bypass _run_tool
# ---------------------------------------------------------------------------

def test_propose_save_note_no_anchor_not_allowed() -> None:
    result = check_write_proposal_precondition(
        TOOL_PROPOSE_SAVE_NOTE,
        has_primary_anchor=False,
    )
    assert result.allowed is False
    assert result.reason == "requires_primary_anchor"
    assert result.error_payload is not None


def test_propose_save_note_no_anchor_payload_matches_old_shape() -> None:
    result = check_write_proposal_precondition(
        TOOL_PROPOSE_SAVE_NOTE,
        has_primary_anchor=False,
    )
    payload = result.error_payload
    assert payload["status"] == "error"
    assert "No anchor" in payload["summary"]
    assert payload["artifacts"] == []
    assert payload == NO_ANCHOR_ERROR_PAYLOAD


# ---------------------------------------------------------------------------
# Gate: propose_save_note with anchor → allowed (note_text checked in runner)
# ---------------------------------------------------------------------------

def test_propose_save_note_with_anchor_allowed() -> None:
    result = check_write_proposal_precondition(
        TOOL_PROPOSE_SAVE_NOTE,
        has_primary_anchor=True,
    )
    assert result.allowed is True
    assert result.reason is None
    assert result.error_payload is None


# ---------------------------------------------------------------------------
# Gate: propose_save_highlight no anchor → hard gate
# ---------------------------------------------------------------------------

def test_propose_save_highlight_no_anchor_not_allowed() -> None:
    result = check_write_proposal_precondition(
        TOOL_PROPOSE_SAVE_HIGHLIGHT,
        has_primary_anchor=False,
    )
    assert result.allowed is False
    assert result.reason == "requires_primary_anchor"
    assert result.error_payload is not None


def test_propose_save_highlight_no_anchor_payload_matches_old_shape() -> None:
    result = check_write_proposal_precondition(
        TOOL_PROPOSE_SAVE_HIGHLIGHT,
        has_primary_anchor=False,
    )
    payload = result.error_payload
    assert payload["status"] == "error"
    assert "No anchor" in payload["summary"]
    assert payload == NO_ANCHOR_ERROR_PAYLOAD


# ---------------------------------------------------------------------------
# Gate: propose_save_highlight with anchor → allowed
# ---------------------------------------------------------------------------

def test_propose_save_highlight_with_anchor_allowed() -> None:
    result = check_write_proposal_precondition(
        TOOL_PROPOSE_SAVE_HIGHLIGHT,
        has_primary_anchor=True,
    )
    assert result.allowed is True
    assert result.reason is None
    assert result.error_payload is None


# ---------------------------------------------------------------------------
# WriteProposalPrecondition is frozen
# ---------------------------------------------------------------------------

def test_precondition_is_frozen() -> None:
    pc = WriteProposalPrecondition(allowed=True)
    try:
        pc.allowed = False  # type: ignore[misc]
    except AttributeError:
        pass
    else:
        raise AssertionError("WriteProposalPrecondition should be frozen")


# ---------------------------------------------------------------------------
# NO_ANCHOR_ERROR_PAYLOAD is stable
# ---------------------------------------------------------------------------

def test_no_anchor_error_payload_stable_shape() -> None:
    payload = NO_ANCHOR_ERROR_PAYLOAD
    assert payload["status"] == "error"
    assert payload["summary"] == "No anchor available"
    assert payload["next_actions"] == ["Ask the user to select a sentence or text span first."]
    assert payload["artifacts"] == []


# ---------------------------------------------------------------------------
# MISSING_NOTE_TEXT_PAYLOAD is stable
# ---------------------------------------------------------------------------

def test_missing_note_text_payload_stable_shape() -> None:
    payload = MISSING_NOTE_TEXT_PAYLOAD
    assert payload["status"] == "error"
    assert payload["summary"] == "Missing note_text"
    assert payload["next_actions"] == ["Provide the note content before proposing save_note."]
    assert payload["artifacts"] == []


# ---------------------------------------------------------------------------
# Integration: no anchor → bypass _run_tool, no trace, no budget
# ---------------------------------------------------------------------------

def test_propose_save_note_no_anchor_no_trace() -> None:
    deps = _make_deps(has_anchor=False)
    ctx = _make_ctx(deps)
    result = asyncio.run(_propose_save_note_tool(ctx, note_text="test"))
    assert result["status"] == "error"
    assert "No anchor" in result["summary"]
    # No tool trace emitted
    assert len(deps.state.tool_trace) == 0
    # No action requests
    assert len(deps.state.action_requests) == 0


def test_propose_save_highlight_no_anchor_no_trace() -> None:
    deps = _make_deps(has_anchor=False)
    ctx = _make_ctx(deps)
    result = asyncio.run(_propose_save_highlight_tool(ctx))
    assert result["status"] == "error"
    assert "No anchor" in result["summary"]
    assert len(deps.state.tool_trace) == 0
    assert len(deps.state.action_requests) == 0


# ---------------------------------------------------------------------------
# Integration: missing note_text → goes through _run_tool, has trace
# ---------------------------------------------------------------------------

def test_propose_save_note_missing_note_text_through_run_tool() -> None:
    deps = _make_deps(has_anchor=True)
    ctx = _make_ctx(deps)
    result = asyncio.run(_propose_save_note_tool(ctx, note_text=None))
    assert result["status"] == "error"
    assert "Missing note_text" in result["summary"]
    # tool_trace has started + completed
    assert len(deps.state.tool_trace) == 2
    assert deps.state.tool_trace[0].status == "started"
    assert deps.state.tool_trace[1].status == "completed"
    assert "Missing note_text" in deps.state.tool_trace[1].summary
    # No action requests created
    assert len(deps.state.action_requests) == 0
    # Budget was consumed (tool_call_count incremented)
    assert deps.state.tool_call_count == 1
    # event_queue.put called 2 times (started + completed)
    assert deps.event_queue.put.call_count == 2


def test_propose_save_note_empty_note_text_through_run_tool() -> None:
    deps = _make_deps(has_anchor=True)
    ctx = _make_ctx(deps)
    result = asyncio.run(_propose_save_note_tool(ctx, note_text=""))
    assert result["status"] == "error"
    assert len(deps.state.tool_trace) == 2
    assert deps.state.tool_call_count == 1


def test_propose_save_note_whitespace_note_text_through_run_tool() -> None:
    deps = _make_deps(has_anchor=True)
    ctx = _make_ctx(deps)
    result = asyncio.run(_propose_save_note_tool(ctx, note_text="   "))
    assert result["status"] == "error"
    assert len(deps.state.tool_trace) == 2
    assert deps.state.tool_call_count == 1


# ---------------------------------------------------------------------------
# Integration: valid note_text → action request created
# ---------------------------------------------------------------------------

def test_propose_save_note_valid_creates_action_request() -> None:
    deps = _make_deps(has_anchor=True)
    ctx = _make_ctx(deps)
    result = asyncio.run(_propose_save_note_tool(ctx, note_text="important note"))
    assert result["status"] == "success"
    assert len(deps.state.action_requests) == 1
    assert deps.state.action_requests[0].action_type == "save_note"
    assert deps.state.tool_call_count == 1


def test_propose_save_highlight_valid_creates_action_request() -> None:
    deps = _make_deps(has_anchor=True)
    ctx = _make_ctx(deps)
    result = asyncio.run(_propose_save_highlight_tool(ctx))
    assert result["status"] == "success"
    assert len(deps.state.action_requests) == 1
    assert deps.state.action_requests[0].action_type == "save_highlight"
    assert deps.state.tool_call_count == 1


# ---------------------------------------------------------------------------
# P5-7: Write proposal confirmation contract
# ---------------------------------------------------------------------------

def test_propose_save_note_only_creates_action_request_not_write() -> None:
    """propose_save_note only creates an action_request, does not directly persist."""
    deps = _make_deps(has_anchor=True)
    ctx = _make_ctx(deps)
    result = asyncio.run(_propose_save_note_tool(ctx, note_text="my note"))
    assert result["status"] == "success"
    assert len(deps.state.action_requests) == 1
    # The action_request is a proposal, not a direct write
    req = deps.state.action_requests[0]
    assert req.requires_confirmation is True
    assert req.action_type == "save_note"


def test_propose_save_highlight_only_creates_action_request_not_write() -> None:
    """propose_save_highlight only creates an action_request, does not directly persist."""
    deps = _make_deps(has_anchor=True)
    ctx = _make_ctx(deps)
    result = asyncio.run(_propose_save_highlight_tool(ctx))
    assert result["status"] == "success"
    assert len(deps.state.action_requests) == 1
    req = deps.state.action_requests[0]
    assert req.requires_confirmation is True
    assert req.action_type == "save_highlight"


def test_action_request_action_type_only_save_note_or_highlight() -> None:
    """Write proposal tools only produce save_note / save_highlight action_types.

    The runtime dataclass has no runtime validation, but the Pydantic
    ReaderAskActionProposal does. This test verifies both layers:
    1. Runtime dataclass accepts the valid types.
    2. Pydantic model rejects invalid action_types.
    """
    from pydantic import ValidationError

    from app.agents.reader_ask_agent import ReaderAskRuntimeActionRequest
    from app.schemas.reader_ask import ReaderAskActionProposal

    valid_types = {"save_note", "save_highlight"}
    # Runtime dataclass accepts valid types
    for action_type in ("save_note", "save_highlight"):
        req = ReaderAskRuntimeActionRequest(
            action_type=action_type,
            label="Test",
            description="Test",
        )
        assert req.action_type in valid_types

    # Pydantic model rejects invalid action_type
    try:
        ReaderAskActionProposal(
            id="test",
            action_type="delete_everything",  # type: ignore[arg-type]
            label="Test",
            description="Test",
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("Pydantic model should reject invalid action_type")


def test_no_anchor_hard_gate_creates_no_action_request() -> None:
    """no-anchor hard gate must not create any action_request."""
    deps = _make_deps(has_anchor=False)
    ctx = _make_ctx(deps)

    result_note = asyncio.run(_propose_save_note_tool(ctx, note_text="test"))
    assert result_note["status"] == "error"
    assert len(deps.state.action_requests) == 0

    result_highlight = asyncio.run(_propose_save_highlight_tool(ctx))
    assert result_highlight["status"] == "error"
    assert len(deps.state.action_requests) == 0
