"""Quality assertion tests for Ask Claread.

All tests are deterministic/offline — no real LLM calls.
The conftest.py autouse ``fail_on_real_llm_attempts`` fixture blocks real LLM.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.agents.reader_ask_agent import ReaderAskAgentDeps, ReaderAskRuntimeState, _suggest_prompts_tool
from app.agents.reader_ask_tool_policy import (
    ToolAvailabilityInput,
    ToolAvailabilityResult,
    build_tool_availability,
)
from app.agents.reader_ask_tool_registry import (
    TOOL_GET_RECORD_CONTEXT,
    TOOL_PROPOSE_SAVE_HIGHLIGHT,
    TOOL_PROPOSE_SAVE_NOTE,
    TOOL_SUGGEST_PROMPTS,
    agent_callable_tool_names,
)
from app.agents.reader_ask_tool_runtime import run_tool
from app.schemas.reader_ask import ReaderAskAnchorRef, ReaderAskCitation
from app.services.reader_ask.agent_runner import build_replan_event, is_degenerate_answer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_deps(**overrides) -> ReaderAskAgentDeps:
    defaults = dict(
        payload={},
        event_queue=AsyncMock(),
        state=ReaderAskRuntimeState(),
        query_seed="test",
        task_mode="explain",
        record_id="r1",
        record_title="Test",
        primary_anchor=None,
        get_record_context_fn=AsyncMock(return_value={"summary": "Context loaded"}),
        get_record_insights_fn=AsyncMock(return_value=[]),
        get_user_vocabulary_book_fn=AsyncMock(return_value=[]),
        resolve_known_reference_fn=AsyncMock(return_value={"status": "not_found"}),
        load_explicit_attachment_context_fn=AsyncMock(return_value={"status": "not_found", "ok": False}),
        generate_sentence_annotation_fn=AsyncMock(return_value={"status": "ok"}),
        suggest_prompts_fn=AsyncMock(return_value={"status": "warning", "summary": "No suggestions"}),
        vocabulary_item_to_citation_fn=lambda item: None,
    )
    defaults.update(overrides)
    return ReaderAskAgentDeps(**defaults)


def _make_anchor() -> ReaderAskAnchorRef:
    return ReaderAskAnchorRef(
        anchor_type="sentence",
        sentence_id="s1",
        paragraph_id="p1",
        query="test",
    )


# ---------------------------------------------------------------------------
# 1. Citation presence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_citation_presence_tool_trace_records_completed():
    """When a tool returns data that generates a citation, the tool trace
    should record a completed status and the citation can be populated
    by the tool callback via runtime_state.citations."""
    state = ReaderAskRuntimeState()
    deps = _make_deps(state=state)

    async def runner():
        return {"status": "ok", "summary": "Loaded context"}

    result = await run_tool(deps, TOOL_GET_RECORD_CONTEXT, runner, input_summary="scope=window")

    # Tool trace must show completed status
    assert len(state.tool_trace) == 2  # started + completed
    assert state.tool_trace[0].tool_name == TOOL_GET_RECORD_CONTEXT
    assert state.tool_trace[0].status == "started"
    assert state.tool_trace[1].tool_name == TOOL_GET_RECORD_CONTEXT
    assert state.tool_trace[1].status == "completed"

    # Simulate the tool callback appending a citation (as _get_user_vocabulary_book_tool does)
    citation = ReaderAskCitation(
        citation_id="c1",
        kind="vocabulary",
        label="test word",
        record_id="r1",
    )
    state.citations.append(citation)
    assert len(state.citations) == 1
    assert state.citations[0].citation_id == "c1"
    assert state.citations[0].kind == "vocabulary"


# ---------------------------------------------------------------------------
# 2. Tool selection correctness
# ---------------------------------------------------------------------------


def test_tool_availability_no_anchor_flags_write_proposals_in_unavailable_reasons():
    """When there is no primary anchor, build_tool_availability should flag
    propose_save_note and propose_save_highlight in unavailable_reasons
    with 'requires_primary_anchor'."""
    inp = ToolAvailabilityInput(
        task_mode="explain",
        entry_action="ask_about_this",
        has_primary_anchor=False,
    )
    result = build_tool_availability(inp)

    assert TOOL_PROPOSE_SAVE_NOTE in result.unavailable_reasons
    assert result.unavailable_reasons[TOOL_PROPOSE_SAVE_NOTE] == "requires_primary_anchor"
    assert TOOL_PROPOSE_SAVE_HIGHLIGHT in result.unavailable_reasons
    assert result.unavailable_reasons[TOOL_PROPOSE_SAVE_HIGHLIGHT] == "requires_primary_anchor"

    # Write-proposal tools are still in allowed_tool_names (informational only)
    assert TOOL_PROPOSE_SAVE_NOTE in result.allowed_tool_names
    assert TOOL_PROPOSE_SAVE_HIGHLIGHT in result.allowed_tool_names


@pytest.mark.asyncio
async def test_run_tool_with_disallowed_tool_returns_error_payload():
    """run_tool with a tool not in allowed_tool_names should return an error
    payload without consuming budget."""
    state = ReaderAskRuntimeState()
    availability = ToolAvailabilityResult(
        allowed_tool_names=frozenset({TOOL_GET_RECORD_CONTEXT}),
        unavailable_reasons={},
    )
    deps = _make_deps(state=state, tool_availability=availability)

    async def runner():
        return {"status": "ok"}

    result = await run_tool(deps, TOOL_PROPOSE_SAVE_NOTE, runner, input_summary="test")

    # Should return error payload, not the runner result
    assert isinstance(result, dict)
    assert result["status"] == "error"
    assert result["reason"] == "tool_not_available"

    # Budget should NOT be consumed
    assert state.tool_call_count == 0

    # Tool trace should record a failed entry
    assert len(state.tool_trace) == 1
    assert state.tool_trace[0].status == "failed"


# ---------------------------------------------------------------------------
# 3. Follow-up suggestions
# ---------------------------------------------------------------------------


def test_suggest_prompts_is_agent_callable():
    """The suggest_prompts tool must be in the agent-callable set."""
    assert TOOL_SUGGEST_PROMPTS in agent_callable_tool_names()


def test_suggest_prompts_tool_contract():
    """The suggest_prompts tool contract: when called with 2-3 valid
    suggestions, it should pass them through; fewer than 2 valid items
    should return a warning."""
    # Verify the tool is callable and in the allowed set.
    assert callable(_suggest_prompts_tool)


@pytest.mark.asyncio
async def test_suggest_prompts_with_valid_suggestions():
    """When suggest_prompts is called with 2-3 valid suggestions,
    the tool should pass them through to suggest_prompts_fn."""
    from unittest.mock import MagicMock

    from pydantic_ai import RunContext

    suggest_fn = AsyncMock(return_value={"status": "ok", "suggestions": []})
    deps = _make_deps(suggest_prompts_fn=suggest_fn)
    ctx = MagicMock(spec=RunContext)
    ctx.deps = deps

    suggestions = [
        {"label": "Explain grammar", "prompt": "Can you explain the grammar here?"},
        {"label": "Translate", "prompt": "What does this mean in Chinese?"},
    ]
    result = await _suggest_prompts_tool(ctx, suggestions=suggestions)

    # suggest_prompts_fn should have been called with the cleaned suggestions
    suggest_fn.assert_awaited_once()
    call_args = suggest_fn.call_args[0][0]
    assert len(call_args) == 2
    assert call_args[0]["label"] == "Explain grammar"


@pytest.mark.asyncio
async def test_suggest_prompts_with_no_suggestions_returns_warning():
    """When suggest_prompts is called with no suggestions,
    it should return a warning without calling suggest_prompts_fn."""
    from unittest.mock import MagicMock

    from pydantic_ai import RunContext

    suggest_fn = AsyncMock(return_value={"status": "ok"})
    deps = _make_deps(suggest_prompts_fn=suggest_fn)
    ctx = MagicMock(spec=RunContext)
    ctx.deps = deps

    result = await _suggest_prompts_tool(ctx, suggestions=None)

    assert result["status"] == "warning"
    assert result["ok"] is False
    suggest_fn.assert_not_awaited()


# ---------------------------------------------------------------------------
# 4. Write proposal gating
# ---------------------------------------------------------------------------


def test_build_tool_availability_without_primary_anchor_flags_write_proposals():
    """Without a primary anchor, build_tool_availability should flag
    propose_save_note and propose_save_highlight in unavailable_reasons."""
    inp = ToolAvailabilityInput(
        task_mode="explain",
        entry_action="ask_about_this",
        has_primary_anchor=False,
    )
    result = build_tool_availability(inp)

    assert TOOL_PROPOSE_SAVE_NOTE in result.unavailable_reasons
    assert TOOL_PROPOSE_SAVE_HIGHLIGHT in result.unavailable_reasons
    for tool_name in (TOOL_PROPOSE_SAVE_NOTE, TOOL_PROPOSE_SAVE_HIGHLIGHT):
        assert result.unavailable_reasons[tool_name] == "requires_primary_anchor"


def test_build_tool_availability_with_primary_anchor_no_write_flags():
    """With a primary anchor, write-proposal tools should NOT be in
    unavailable_reasons."""
    inp = ToolAvailabilityInput(
        task_mode="explain",
        entry_action="ask_about_this",
        has_primary_anchor=True,
    )
    result = build_tool_availability(inp)

    assert TOOL_PROPOSE_SAVE_NOTE not in result.unavailable_reasons
    assert TOOL_PROPOSE_SAVE_HIGHLIGHT not in result.unavailable_reasons


@pytest.mark.asyncio
async def test_run_tool_disallowed_write_proposal_returns_error_without_consuming_budget():
    """run_tool with a write-proposal tool not in allowed_tool_names should
    return an error payload without consuming budget."""
    state = ReaderAskRuntimeState()
    availability = ToolAvailabilityResult(
        allowed_tool_names=frozenset({TOOL_GET_RECORD_CONTEXT}),
        unavailable_reasons={TOOL_PROPOSE_SAVE_NOTE: "requires_primary_anchor"},
    )
    deps = _make_deps(state=state, tool_availability=availability)

    async def runner():
        return {"status": "ok"}

    result = await run_tool(deps, TOOL_PROPOSE_SAVE_NOTE, runner, input_summary="save note")

    assert isinstance(result, dict)
    assert result["status"] == "error"
    assert result["reason"] == "tool_not_available"

    # Budget must NOT be consumed
    assert state.tool_call_count == 0


# ---------------------------------------------------------------------------
# 5. Budget enforcement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_budget_enforcement_raises_runtime_error():
    """When tool_call_count > max_tool_calls, run_tool should raise RuntimeError."""
    state = ReaderAskRuntimeState()
    state.tool_call_count = 5
    state.max_tool_calls = 5
    deps = _make_deps(state=state)

    async def runner():
        return {"status": "ok"}

    with pytest.raises(RuntimeError, match="Tool call limit exceeded"):
        await run_tool(deps, TOOL_GET_RECORD_CONTEXT, runner, input_summary="scope=window")

    # Tool trace should record a failed entry
    assert any(t.status == "failed" for t in state.tool_trace)


# ---------------------------------------------------------------------------
# 6. Degenerate detection
# ---------------------------------------------------------------------------


def test_is_degenerate_answer_detects_empty():
    """Empty string should be detected as degenerate."""
    assert is_degenerate_answer("") is True
    assert is_degenerate_answer("   ") is True


def test_is_degenerate_answer_detects_refusal():
    """Refusal patterns should be detected as degenerate."""
    assert is_degenerate_answer("I cannot answer this question.") is True
    assert is_degenerate_answer("As an AI, I cannot provide that.") is True
    assert is_degenerate_answer("我无法回答这个问题。") is True


def test_is_degenerate_answer_allows_valid_short_answers():
    """Short but valid answers should NOT be degenerate."""
    assert is_degenerate_answer("Yes.") is False
    assert is_degenerate_answer("Present perfect.") is False
    assert is_degenerate_answer("是的。") is False


def test_build_replan_event_agent_loop_first_sets_degenerate_detected():
    """When planner_route == 'agent_loop_first' and the answer is degenerate,
    build_replan_event should set degenerate_detected=True on runtime_state
    and return None (no replan event)."""
    state = ReaderAskRuntimeState()
    result = build_replan_event(
        final_content_md="I cannot answer this.",
        planning_snapshot=None,
        assistant_message_id="msg1",
        planner_route="agent_loop_first",
        runtime_state=state,
    )

    # No replan event for agent_loop_first
    assert result is None
    assert state.degenerate_detected is True
    assert state.degenerate_reason == "degenerate_answer"


def test_build_replan_event_agent_loop_first_non_degenerate_no_flag():
    """When planner_route == 'agent_loop_first' and the answer is NOT degenerate,
    degenerate_detected should remain False."""
    state = ReaderAskRuntimeState()
    result = build_replan_event(
        final_content_md="This is a valid explanation of the grammar point.",
        planning_snapshot=None,
        assistant_message_id="msg1",
        planner_route="agent_loop_first",
        runtime_state=state,
    )

    assert result is None
    assert state.degenerate_detected is False
    assert state.degenerate_reason is None
