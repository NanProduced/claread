"""Tests for the Ask Claread tool runtime (P5-5)."""

import asyncio
from unittest.mock import AsyncMock

from app.agents.reader_ask_agent import ReaderAskAgentDeps, ReaderAskRuntimeState
from app.agents.reader_ask_tool_runtime import (
    run_tool,
    truncate_tool_arg,
)
from app.schemas.reader_ask import ReaderAskAnchorRef


def _make_anchor() -> ReaderAskAnchorRef:
    return ReaderAskAnchorRef(
        anchor_type="sentence",
        target_key="record:r1:sentence:s1",
        sentence_id="s1",
        selected_text="test",
    )


def _make_deps() -> ReaderAskAgentDeps:
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
        primary_anchor=_make_anchor(),
        get_record_context_fn=AsyncMock(return_value={"summary": "Context loaded"}),
        get_record_insights_fn=AsyncMock(return_value=[]),
        get_user_vocabulary_book_fn=AsyncMock(return_value=[]),
        resolve_known_reference_fn=AsyncMock(return_value={"status": "not_found"}),
        load_explicit_attachment_context_fn=AsyncMock(return_value={"status": "not_found", "ok": False}),
        generate_sentence_annotation_fn=AsyncMock(return_value=None),
        suggest_prompts_fn=AsyncMock(return_value={"suggestions": []}),
        vocabulary_item_to_citation_fn=AsyncMock(),
    )


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------

def test_run_tool_success() -> None:
    deps = _make_deps()

    async def runner() -> dict[str, str]:
        return {"summary": "Context loaded", "next_actions": ["Explain"], "artifacts": ["rec:1"]}

    result = asyncio.run(run_tool(deps, "test_tool", runner))
    assert result["summary"] == "Context loaded"
    # tool_trace: started + completed
    assert len(deps.state.tool_trace) == 2
    assert deps.state.tool_trace[0].status == "started"
    assert deps.state.tool_trace[1].status == "completed"
    assert deps.state.tool_trace[1].summary == "Context loaded"
    assert deps.state.tool_trace[1].next_actions == ["Explain"]
    assert deps.state.tool_trace[1].artifacts == ["rec:1"]
    # Budget consumed
    assert deps.state.tool_call_count == 1


def test_run_tool_success_event_emission() -> None:
    deps = _make_deps()

    async def runner() -> dict[str, str]:
        return {"summary": "Done"}

    asyncio.run(run_tool(deps, "test_tool", runner))
    # event_queue.put called: tool.started + tool.completed
    assert deps.event_queue.put.call_count == 2


# ---------------------------------------------------------------------------
# Failure path (runner raises)
# ---------------------------------------------------------------------------

def test_run_tool_failure() -> None:
    deps = _make_deps()

    async def runner() -> None:
        raise ValueError("something went wrong")

    try:
        asyncio.run(run_tool(deps, "test_tool", runner))
    except ValueError:
        pass
    else:
        raise AssertionError("Should have re-raised ValueError")

    # tool_trace: started + failed
    assert len(deps.state.tool_trace) == 2
    assert deps.state.tool_trace[0].status == "started"
    assert deps.state.tool_trace[1].status == "failed"
    assert "something went wrong" in deps.state.tool_trace[1].summary
    # Budget consumed
    assert deps.state.tool_call_count == 1


def test_run_tool_failure_event_emission() -> None:
    deps = _make_deps()

    async def runner() -> None:
        raise RuntimeError("boom")

    try:
        asyncio.run(run_tool(deps, "test_tool", runner))
    except RuntimeError:
        pass

    # event_queue.put called: tool.started + tool.failed
    assert deps.event_queue.put.call_count == 2


# ---------------------------------------------------------------------------
# Max tool calls exceeded
# ---------------------------------------------------------------------------

def test_run_tool_max_calls_exceeded() -> None:
    deps = _make_deps()
    deps.state.tool_call_count = 5  # already at max
    deps.state.max_tool_calls = 5

    async def runner() -> dict[str, str]:
        return {"summary": "should not reach"}

    try:
        asyncio.run(run_tool(deps, "test_tool", runner))
    except RuntimeError as exc:
        assert "Tool call limit exceeded" in str(exc)
    else:
        raise AssertionError("Should have raised RuntimeError")

    # tool_trace: failed only (no started)
    assert len(deps.state.tool_trace) == 1
    assert deps.state.tool_trace[0].status == "failed"
    assert "Tool call limit exceeded" in deps.state.tool_trace[0].summary


def test_run_tool_max_calls_exceeded_event_emission() -> None:
    deps = _make_deps()
    deps.state.tool_call_count = 5
    deps.state.max_tool_calls = 5

    async def runner() -> dict[str, str]:
        return {"summary": "should not reach"}

    try:
        asyncio.run(run_tool(deps, "test_tool", runner))
    except RuntimeError:
        pass

    # event_queue.put called: tool.failed only
    assert deps.event_queue.put.call_count == 1


# ---------------------------------------------------------------------------
# tool.completed SSE summary matches normalize result
# ---------------------------------------------------------------------------

def test_run_tool_completed_event_summary() -> None:
    deps = _make_deps()

    async def runner() -> dict[str, str]:
        return {"summary": "Found 3 items", "next_actions": ["Review"], "artifacts": ["rec:1"]}

    asyncio.run(run_tool(deps, "test_tool", runner))

    # The second put call should be tool.completed with the normalized summary
    completed_call = deps.event_queue.put.call_args_list[1]
    event_name, payload = completed_call[0][0]
    assert event_name == "tool.completed"
    assert payload["summary"] == "Found 3 items"


# ---------------------------------------------------------------------------
# truncate_tool_arg
# ---------------------------------------------------------------------------

def testtruncate_tool_arg_short() -> None:
    assert truncate_tool_arg("hello") == "hello"


def testtruncate_tool_arg_long() -> None:
    long_str = "a" * 200
    result = truncate_tool_arg(long_str)
    assert result is not None
    assert len(result) == 120


def testtruncate_tool_arg_none() -> None:
    assert truncate_tool_arg(None) is None


def testtruncate_tool_arg_empty() -> None:
    assert truncate_tool_arg("") is None


def testtruncate_tool_arg_whitespace() -> None:
    assert truncate_tool_arg("   ") is None


def testtruncate_tool_arg_non_string() -> None:
    assert truncate_tool_arg(42) is None  # type: ignore[arg-type]


def testtruncate_tool_arg_collapses_whitespace() -> None:
    assert truncate_tool_arg("hello   world") == "hello world"


# ---------------------------------------------------------------------------
# P5-8: Tool availability hard enforcement
# ---------------------------------------------------------------------------

def test_run_tool_availability_none_allows_execution() -> None:
    """When tool_availability is None, run_tool behaves as before."""
    deps = _make_deps()
    assert deps.tool_availability is None

    async def runner() -> dict[str, str]:
        return {"summary": "Done"}

    result = asyncio.run(run_tool(deps, "test_tool", runner))
    assert result["summary"] == "Done"
    assert deps.state.tool_call_count == 1
    assert len(deps.state.tool_trace) == 2


def test_run_tool_allowed_in_availability_set() -> None:
    """Tool in allowed_tool_names is executed normally."""
    from app.agents.reader_ask_tool_policy import ToolAvailabilityResult

    deps = _make_deps()
    deps.tool_availability = ToolAvailabilityResult(
        allowed_tool_names=frozenset({"test_tool", "other_tool"}),
        unavailable_reasons={},
    )

    async def runner() -> dict[str, str]:
        return {"summary": "Done"}

    result = asyncio.run(run_tool(deps, "test_tool", runner))
    assert result["summary"] == "Done"
    assert deps.state.tool_call_count == 1


def test_run_tool_disallowed_returns_error_payload() -> None:
    """Tool not in allowed_tool_names returns error payload, no runner call."""
    from app.agents.reader_ask_tool_policy import ToolAvailabilityResult

    deps = _make_deps()
    deps.tool_availability = ToolAvailabilityResult(
        allowed_tool_names=frozenset({"other_tool"}),
        unavailable_reasons={},
    )

    runner_called = False

    async def runner() -> dict[str, str]:
        nonlocal runner_called
        runner_called = True
        return {"summary": "should not reach"}

    result = asyncio.run(run_tool(deps, "test_tool", runner))

    # Runner was NOT called
    assert not runner_called
    # Returns error payload
    assert result["status"] == "error"
    assert "not available" in result["summary"]
    assert result["reason"] == "tool_not_available"
    # Budget NOT consumed
    assert deps.state.tool_call_count == 0
    # Failed trace recorded
    assert len(deps.state.tool_trace) == 1
    assert deps.state.tool_trace[0].status == "failed"
    assert "not available" in deps.state.tool_trace[0].summary


def test_run_tool_disallowed_emits_failed_event() -> None:
    """Disallowed tool emits tool.failed event."""
    from app.agents.reader_ask_tool_policy import ToolAvailabilityResult

    deps = _make_deps()
    deps.tool_availability = ToolAvailabilityResult(
        allowed_tool_names=frozenset({"other_tool"}),
        unavailable_reasons={},
    )

    async def runner() -> dict[str, str]:
        return {"summary": "should not reach"}

    asyncio.run(run_tool(deps, "test_tool", runner))

    # event_queue.put called once: tool.failed
    assert deps.event_queue.put.call_count == 1
    call_args = deps.event_queue.put.call_args[0][0]
    assert call_args[0] == "tool.failed"


def test_run_tool_disallowed_no_started_trace() -> None:
    """Disallowed tool has no 'started' trace — only 'failed'."""
    from app.agents.reader_ask_tool_policy import ToolAvailabilityResult

    deps = _make_deps()
    deps.tool_availability = ToolAvailabilityResult(
        allowed_tool_names=frozenset(),
        unavailable_reasons={},
    )

    async def runner() -> dict[str, str]:
        return {"summary": "should not reach"}

    asyncio.run(run_tool(deps, "test_tool", runner))

    # Only one trace entry: failed (no started)
    assert len(deps.state.tool_trace) == 1
    assert deps.state.tool_trace[0].status == "failed"
    assert deps.state.tool_trace[0].tool_name == "test_tool"
