"""Tests for the Ask Claread tool observation normalization (P5-2)."""

from app.agents.reader_ask_tool_observation import (
    ToolObservation,
    normalize_tool_observation,
)

# ---------------------------------------------------------------------------
# dict results
# ---------------------------------------------------------------------------

def test_dict_with_summary_next_actions_artifacts() -> None:
    result = {
        "status": "success",
        "summary": "Found 3 items",
        "next_actions": ["review"],
        "artifacts": ["rec:1"],
    }
    obs = normalize_tool_observation(result)
    assert obs.status == "success"
    assert obs.summary == "Found 3 items"
    assert obs.next_actions == ["review"]
    assert obs.artifacts == ["rec:1"]


def test_dict_with_reason_fallback() -> None:
    result = {"reason": "Not found"}
    obs = normalize_tool_observation(result)
    assert obs.status == "success"
    assert obs.summary == "Not found"


def test_dict_with_error_status() -> None:
    result = {
        "status": "error",
        "summary": "No anchor available",
        "next_actions": ["Select text first"],
    }
    obs = normalize_tool_observation(result)
    assert obs.status == "error"
    assert obs.summary == "No anchor available"
    assert obs.next_actions == ["Select text first"]


def test_dict_error_status_does_not_raise() -> None:
    """Error observations are returned, not raised."""
    result = {"status": "error", "summary": "Something went wrong"}
    obs = normalize_tool_observation(result)
    assert isinstance(obs, ToolObservation)
    assert obs.status == "error"


def test_dict_with_warning_status() -> None:
    result = {"status": "warning", "summary": "Partial results"}
    obs = normalize_tool_observation(result)
    assert obs.status == "warning"
    assert obs.summary == "Partial results"


def test_empty_dict() -> None:
    obs = normalize_tool_observation({})
    assert obs.status == "success"
    assert obs.summary == "Loaded"
    assert obs.next_actions == []
    assert obs.artifacts == []


def test_dict_with_empty_summary_and_reason() -> None:
    result = {"summary": "", "reason": ""}
    obs = normalize_tool_observation(result)
    assert obs.summary == "Loaded"


def test_dict_with_none_summary_uses_reason() -> None:
    result = {"summary": None, "reason": "fallback reason"}
    obs = normalize_tool_observation(result)
    assert obs.summary == "fallback reason"


# ---------------------------------------------------------------------------
# list results
# ---------------------------------------------------------------------------

def test_list_result() -> None:
    obs = normalize_tool_observation([1, 2, 3])
    assert obs.status == "success"
    assert obs.summary == "3 item(s)"
    assert obs.next_actions == []
    assert obs.artifacts == []


def test_empty_list_result() -> None:
    obs = normalize_tool_observation([])
    assert obs.summary == "0 item(s)"


# ---------------------------------------------------------------------------
# None result
# ---------------------------------------------------------------------------

def test_none_result() -> None:
    obs = normalize_tool_observation(None)
    assert obs.status == "success"
    assert obs.summary == "Loaded"
    assert obs.next_actions == []
    assert obs.artifacts == []


# ---------------------------------------------------------------------------
# scalar result
# ---------------------------------------------------------------------------

def test_scalar_result() -> None:
    obs = normalize_tool_observation(42)
    assert obs.status == "success"
    assert obs.summary == "Loaded"


def test_string_scalar_result() -> None:
    obs = normalize_tool_observation("some text")
    assert obs.status == "success"
    assert obs.summary == "Loaded"


# ---------------------------------------------------------------------------
# Filtering: next_actions and artifacts
# ---------------------------------------------------------------------------

def test_next_actions_filters_non_strings() -> None:
    result = {"next_actions": ["valid", 123, None, "", "  "]}
    obs = normalize_tool_observation(result)
    assert obs.next_actions == ["valid"]


def test_artifacts_filters_non_strings() -> None:
    result = {"artifacts": ["rec:1", 42, ""]}
    obs = normalize_tool_observation(result)
    assert obs.artifacts == ["rec:1"]


def test_next_actions_with_non_list_value() -> None:
    result = {"next_actions": "not a list"}
    obs = normalize_tool_observation(result)
    assert obs.next_actions == []


def test_artifacts_with_non_list_value() -> None:
    result = {"artifacts": 42}
    obs = normalize_tool_observation(result)
    assert obs.artifacts == []


# ---------------------------------------------------------------------------
# ToolObservation is frozen
# ---------------------------------------------------------------------------

def test_observation_is_frozen() -> None:
    obs = ToolObservation(status="success", summary="test")
    # Frozen dataclass should raise on attribute assignment
    try:
        obs.summary = "changed"  # type: ignore[misc]
    except AttributeError:
        pass
    else:
        raise AssertionError("ToolObservation should be frozen")


# ---------------------------------------------------------------------------
# Integration: _run_tool uses normalize_tool_observation
# ---------------------------------------------------------------------------

def test_run_tool_uses_normalize_for_trace() -> None:
    """Verify that _run_tool produces the same trace entries via
    normalize_tool_observation as it did with the old _tool_observation."""
    from unittest.mock import AsyncMock, MagicMock

    from app.agents.reader_ask_agent import ReaderAskAgentDeps, ReaderAskRuntimeState
    from app.agents.reader_ask_tool_runtime import run_tool
    from app.schemas.reader_ask import ReaderAskAnchorRef

    anchor = ReaderAskAnchorRef(
        anchor_type="sentence",
        target_key="record:r1:sentence:s1",
        sentence_id="s1",
        selected_text="test",
    )
    event_queue = AsyncMock()
    state = ReaderAskRuntimeState()
    deps = ReaderAskAgentDeps(
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
        search_user_vocabulary_fn=AsyncMock(return_value=[]),
        lookup_dictionary_entry_fn=AsyncMock(return_value=None),
        run_dictionary_ai_context_explain_fn=AsyncMock(return_value=None),
        generate_sentence_annotation_fn=AsyncMock(return_value=None),
        vocabulary_item_to_citation_fn=MagicMock(),
        dictionary_item_to_citation_fn=MagicMock(),
        dictionary_ai_to_citation_fn=MagicMock(),
    )

    async def runner() -> dict[str, str]:
        return {"summary": "Context loaded", "next_actions": ["Explain"], "artifacts": ["rec:1"]}

    import asyncio
    asyncio.run(run_tool(deps, "test_tool", runner))

    # The completed trace entry should have the normalized fields
    completed_trace = state.tool_trace[-1]
    assert completed_trace.status == "completed"
    assert completed_trace.summary == "Context loaded"
    assert completed_trace.next_actions == ["Explain"]
    assert completed_trace.artifacts == ["rec:1"]
