"""Tests for the Ask Claread tool availability policy (P5-3)."""

import pytest

from app.agents.reader_ask_tool_policy import (
    ToolAvailabilityInput,
    build_tool_availability,
)
from app.agents.reader_ask_tool_registry import (
    READER_ASK_TOOL_NAMES,
    TOOL_PROPOSE_SAVE_HIGHLIGHT,
    TOOL_PROPOSE_SAVE_NOTE,
)


def _default_input(**overrides: object) -> ToolAvailabilityInput:
    """Build a ToolAvailabilityInput with sensible defaults."""
    defaults = dict(
        task_mode="explain",
        entry_action="ask_about_this",
        has_primary_anchor=True,
    )
    defaults.update(overrides)
    return ToolAvailabilityInput(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Default: all 8 tools allowed
# ---------------------------------------------------------------------------

def test_default_input_allows_all_8_tools() -> None:
    result = build_tool_availability(_default_input())
    assert result.allowed_tool_names == READER_ASK_TOOL_NAMES
    assert len(result.allowed_tool_names) == 8


def test_default_input_no_unavailable_reasons() -> None:
    result = build_tool_availability(_default_input())
    assert result.unavailable_reasons == {}


# ---------------------------------------------------------------------------
# No primary anchor: write proposals still allowed but flagged
# ---------------------------------------------------------------------------

def test_no_primary_anchor_write_proposals_still_allowed() -> None:
    result = build_tool_availability(_default_input(has_primary_anchor=False))
    # Write proposal tools must still be in allowed_tool_names
    assert TOOL_PROPOSE_SAVE_NOTE in result.allowed_tool_names
    assert TOOL_PROPOSE_SAVE_HIGHLIGHT in result.allowed_tool_names
    # All 8 tools still allowed
    assert result.allowed_tool_names == READER_ASK_TOOL_NAMES


def test_no_primary_anchor_write_proposals_flagged_in_reasons() -> None:
    result = build_tool_availability(_default_input(has_primary_anchor=False))
    assert result.unavailable_reasons[TOOL_PROPOSE_SAVE_NOTE] == "requires_primary_anchor"
    assert result.unavailable_reasons[TOOL_PROPOSE_SAVE_HIGHLIGHT] == "requires_primary_anchor"
    # Only write proposal tools should have reasons
    assert len(result.unavailable_reasons) == 2


def test_has_primary_anchor_no_unavailable_reasons() -> None:
    result = build_tool_availability(_default_input(has_primary_anchor=True))
    assert TOOL_PROPOSE_SAVE_NOTE not in result.unavailable_reasons
    assert TOOL_PROPOSE_SAVE_HIGHLIGHT not in result.unavailable_reasons


# ---------------------------------------------------------------------------
# All allowed tools come from registry
# ---------------------------------------------------------------------------

def test_allowed_tools_subset_of_registry() -> None:
    result = build_tool_availability(_default_input())
    assert result.allowed_tool_names.issubset(READER_ASK_TOOL_NAMES)


# ---------------------------------------------------------------------------
# task_mode does not remove tools
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("task_mode", [
    "explain", "breakdown", "vocabulary", "grammar", "practice", "general",
])
def test_task_mode_does_not_remove_tools(task_mode: str) -> None:
    result = build_tool_availability(_default_input(task_mode=task_mode))
    assert result.allowed_tool_names == READER_ASK_TOOL_NAMES


def test_vocabulary_mode_does_not_remove_dictionary_tools() -> None:
    result = build_tool_availability(_default_input(task_mode="vocabulary"))
    assert "lookup_dictionary_entry" in result.allowed_tool_names
    assert "run_dictionary_ai_context_explain" in result.allowed_tool_names


def test_grammar_mode_does_not_remove_annotation_tools() -> None:
    result = build_tool_availability(_default_input(task_mode="grammar"))
    assert "generate_sentence_annotation" in result.allowed_tool_names


def test_general_mode_does_not_remove_any_tools() -> None:
    result = build_tool_availability(_default_input(task_mode="general"))
    assert result.allowed_tool_names == READER_ASK_TOOL_NAMES


# ---------------------------------------------------------------------------
# Cache flag does not remove generate_sentence_annotation
# ---------------------------------------------------------------------------

def test_annotation_cache_does_not_remove_annotation_tool() -> None:
    result = build_tool_availability(
        _default_input(has_generated_annotation_cache=True)
    )
    assert "generate_sentence_annotation" in result.allowed_tool_names


# ---------------------------------------------------------------------------
# Dictionary anchor flag does not change availability
# ---------------------------------------------------------------------------

def test_dictionary_anchor_flag_does_not_change_availability() -> None:
    result_with = build_tool_availability(
        _default_input(has_dictionary_anchor=True)
    )
    result_without = build_tool_availability(
        _default_input(has_dictionary_anchor=False)
    )
    assert result_with.allowed_tool_names == result_without.allowed_tool_names
    assert result_with.unavailable_reasons == result_without.unavailable_reasons


# ---------------------------------------------------------------------------
# Result is frozen
# ---------------------------------------------------------------------------

def test_result_is_frozen() -> None:
    result = build_tool_availability(_default_input())
    try:
        result.allowed_tool_names = frozenset()  # type: ignore[misc]
    except AttributeError:
        pass
    else:
        raise AssertionError("ToolAvailabilityResult should be frozen")


# ---------------------------------------------------------------------------
# Input is frozen
# ---------------------------------------------------------------------------

def test_input_is_frozen() -> None:
    inp = _default_input()
    try:
        inp.task_mode = "general"  # type: ignore[misc]
    except AttributeError:
        pass
    else:
        raise AssertionError("ToolAvailabilityInput should be frozen")


# ---------------------------------------------------------------------------
# P5-6 Wiring: ReaderAskAgentDeps carries tool_availability
# ---------------------------------------------------------------------------

def test_deps_carries_tool_availability() -> None:
    from unittest.mock import AsyncMock, MagicMock

    from app.agents.reader_ask_agent import ReaderAskAgentDeps, ReaderAskRuntimeState
    from app.schemas.reader_ask import ReaderAskAnchorRef

    anchor = ReaderAskAnchorRef(
        anchor_type="sentence",
        target_key="record:r1:sentence:s1",
        sentence_id="s1",
        selected_text="test",
    )
    availability = build_tool_availability(
        ToolAvailabilityInput(
            task_mode="explain",
            entry_action="ask_about_this",
            has_primary_anchor=True,
        )
    )
    deps = ReaderAskAgentDeps(
        payload={},
        event_queue=AsyncMock(),
        state=ReaderAskRuntimeState(),
        query_seed="test",
        task_mode="explain",
        record_id="r1",
        record_title="Test",
        primary_anchor=anchor,
        get_record_context_fn=AsyncMock(return_value={}),
        get_record_insights_fn=AsyncMock(return_value=[]),
        search_user_vocabulary_fn=AsyncMock(return_value=[]),
        lookup_dictionary_entry_fn=AsyncMock(return_value=None),
        run_dictionary_ai_context_explain_fn=AsyncMock(return_value=None),
        generate_sentence_annotation_fn=AsyncMock(return_value=None),
        vocabulary_item_to_citation_fn=MagicMock(),
        dictionary_item_to_citation_fn=MagicMock(),
        dictionary_ai_to_citation_fn=MagicMock(),
        tool_availability=availability,
    )
    assert deps.tool_availability is not None
    assert deps.tool_availability.allowed_tool_names == READER_ASK_TOOL_NAMES


def test_deps_tool_availability_defaults_to_none() -> None:
    from unittest.mock import AsyncMock, MagicMock

    from app.agents.reader_ask_agent import ReaderAskAgentDeps, ReaderAskRuntimeState
    from app.schemas.reader_ask import ReaderAskAnchorRef

    anchor = ReaderAskAnchorRef(
        anchor_type="sentence",
        target_key="record:r1:sentence:s1",
        sentence_id="s1",
        selected_text="test",
    )
    deps = ReaderAskAgentDeps(
        payload={},
        event_queue=AsyncMock(),
        state=ReaderAskRuntimeState(),
        query_seed="test",
        task_mode="explain",
        record_id="r1",
        record_title="Test",
        primary_anchor=anchor,
        get_record_context_fn=AsyncMock(return_value={}),
        get_record_insights_fn=AsyncMock(return_value=[]),
        search_user_vocabulary_fn=AsyncMock(return_value=[]),
        lookup_dictionary_entry_fn=AsyncMock(return_value=None),
        run_dictionary_ai_context_explain_fn=AsyncMock(return_value=None),
        generate_sentence_annotation_fn=AsyncMock(return_value=None),
        vocabulary_item_to_citation_fn=MagicMock(),
        dictionary_item_to_citation_fn=MagicMock(),
        dictionary_ai_to_citation_fn=MagicMock(),
    )
    assert deps.tool_availability is None


def test_deps_no_anchor_unavailable_reasons_contains_write_proposals() -> None:
    from unittest.mock import AsyncMock, MagicMock

    from app.agents.reader_ask_agent import ReaderAskAgentDeps, ReaderAskRuntimeState

    availability = build_tool_availability(
        ToolAvailabilityInput(
            task_mode="explain",
            entry_action="ask_about_this",
            has_primary_anchor=False,
        )
    )
    deps = ReaderAskAgentDeps(
        payload={},
        event_queue=AsyncMock(),
        state=ReaderAskRuntimeState(),
        query_seed="test",
        task_mode="explain",
        record_id="r1",
        record_title="Test",
        primary_anchor=None,
        get_record_context_fn=AsyncMock(return_value={}),
        get_record_insights_fn=AsyncMock(return_value=[]),
        search_user_vocabulary_fn=AsyncMock(return_value=[]),
        lookup_dictionary_entry_fn=AsyncMock(return_value=None),
        run_dictionary_ai_context_explain_fn=AsyncMock(return_value=None),
        generate_sentence_annotation_fn=AsyncMock(return_value=None),
        vocabulary_item_to_citation_fn=MagicMock(),
        dictionary_item_to_citation_fn=MagicMock(),
        dictionary_ai_to_citation_fn=MagicMock(),
        tool_availability=availability,
    )
    assert deps.tool_availability is not None
    assert TOOL_PROPOSE_SAVE_NOTE in deps.tool_availability.unavailable_reasons
    assert TOOL_PROPOSE_SAVE_HIGHLIGHT in deps.tool_availability.unavailable_reasons
