"""Tests for the Ask Claread tool registry (P5-1)."""

from app.agents.reader_ask_tool_registry import (
    READER_ASK_TOOL_NAMES,
    READER_ASK_TOOL_REGISTRY,
    TOOL_GENERATE_SENTENCE_ANNOTATION,
    TOOL_GET_RECORD_CONTEXT,
    TOOL_GET_RECORD_INSIGHTS,
    TOOL_LOOKUP_DICTIONARY_ENTRY,
    TOOL_PROPOSE_SAVE_HIGHLIGHT,
    TOOL_PROPOSE_SAVE_NOTE,
    TOOL_RUN_DICTIONARY_AI_CONTEXT_EXPLAIN,
    TOOL_SEARCH_USER_VOCABULARY,
    ToolSpec,
    get_tool_spec,
    is_write_proposal_tool,
    requires_anchor,
)

# ---------------------------------------------------------------------------
# Registry completeness
# ---------------------------------------------------------------------------

_EXPECTED_TOOL_NAMES = frozenset({
    "get_record_context",
    "get_record_insights",
    "search_user_vocabulary",
    "lookup_dictionary_entry",
    "run_dictionary_ai_context_explain",
    "generate_sentence_annotation",
    "propose_save_note",
    "propose_save_highlight",
})


def test_registry_contains_exactly_8_tools() -> None:
    assert set(READER_ASK_TOOL_REGISTRY.keys()) == _EXPECTED_TOOL_NAMES
    assert len(READER_ASK_TOOL_REGISTRY) == 8


def test_tool_names_constant_matches_registry() -> None:
    assert READER_ASK_TOOL_NAMES == _EXPECTED_TOOL_NAMES


def test_every_tool_name_is_unique() -> None:
    names = [spec.name for spec in READER_ASK_TOOL_REGISTRY.values()]
    assert len(names) == len(set(names))


# ---------------------------------------------------------------------------
# Per-tool metadata
# ---------------------------------------------------------------------------

_EXPECTED_SPECS: dict[str, dict] = {
    "get_record_context": {
        "category": "context",
        "effect": "read",
        "requires_anchor": False,
        "consumes_budget_when_precondition_fails": True,
        "agent_callable": True,
        "output_kind": "dict_or_none",
        "observation_statuses": ("success",),
    },
    "get_record_insights": {
        "category": "context",
        "effect": "read",
        "requires_anchor": False,
        "consumes_budget_when_precondition_fails": True,
        "agent_callable": True,
        "output_kind": "list_or_empty",
        "observation_statuses": ("success",),
    },
    "search_user_vocabulary": {
        "category": "vocabulary",
        "effect": "read",
        "requires_anchor": False,
        "consumes_budget_when_precondition_fails": True,
        "agent_callable": True,
        "output_kind": "list_or_empty",
        "observation_statuses": ("success",),
    },
    "lookup_dictionary_entry": {
        "category": "dictionary",
        "effect": "read",
        "requires_anchor": False,
        "consumes_budget_when_precondition_fails": True,
        "agent_callable": True,
        "output_kind": "dict_or_none",
        "observation_statuses": ("success",),
    },
    "run_dictionary_ai_context_explain": {
        "category": "dictionary",
        "effect": "read",
        "requires_anchor": False,
        "consumes_budget_when_precondition_fails": True,
        "agent_callable": True,
        "output_kind": "dict_or_none",
        "observation_statuses": ("success",),
    },
    "generate_sentence_annotation": {
        "category": "annotation",
        "effect": "read",
        "requires_anchor": False,
        "consumes_budget_when_precondition_fails": True,
        "agent_callable": True,
        "output_kind": "dict_or_none",
        "observation_statuses": ("success",),
    },
    "propose_save_note": {
        "category": "write_proposal",
        "effect": "propose_write",
        "requires_anchor": True,
        "consumes_budget_when_precondition_fails": False,
        "agent_callable": True,
        "output_kind": "dict_always",
        "observation_statuses": ("success", "error"),
    },
    "propose_save_highlight": {
        "category": "write_proposal",
        "effect": "propose_write",
        "requires_anchor": True,
        "consumes_budget_when_precondition_fails": False,
        "agent_callable": True,
        "output_kind": "dict_always",
        "observation_statuses": ("success", "error"),
    },
}


def test_per_tool_metadata() -> None:
    for name, expected in _EXPECTED_SPECS.items():
        spec = READER_ASK_TOOL_REGISTRY[name]
        assert spec.name == name, f"{name}: name mismatch"
        assert spec.category == expected["category"], f"{name}: category"
        assert spec.effect == expected["effect"], f"{name}: effect"
        assert spec.requires_anchor == expected["requires_anchor"], f"{name}: requires_anchor"
        assert spec.consumes_budget_when_precondition_fails == expected[
            "consumes_budget_when_precondition_fails"
        ], f"{name}: consumes_budget_when_precondition_fails"
        assert spec.agent_callable == expected["agent_callable"], f"{name}: agent_callable"
        assert spec.output_kind == expected["output_kind"], f"{name}: output_kind"
        assert spec.observation_statuses == expected["observation_statuses"], (
            f"{name}: observation_statuses"
        )


# ---------------------------------------------------------------------------
# Helper: is_write_proposal_tool
# ---------------------------------------------------------------------------

def test_is_write_proposal_tool_only_for_propose_tools() -> None:
    assert is_write_proposal_tool("propose_save_note") is True
    assert is_write_proposal_tool("propose_save_highlight") is True
    # All other tools must NOT be write-proposal tools
    for name in _EXPECTED_TOOL_NAMES - {"propose_save_note", "propose_save_highlight"}:
        assert is_write_proposal_tool(name) is False, f"{name} should not be write_proposal"


def test_is_write_proposal_tool_unknown_returns_false() -> None:
    assert is_write_proposal_tool("nonexistent_tool") is False


# ---------------------------------------------------------------------------
# Helper: requires_anchor
# ---------------------------------------------------------------------------

def test_requires_anchor_for_write_proposal_tools() -> None:
    assert requires_anchor("propose_save_note") is True
    assert requires_anchor("propose_save_highlight") is True


def test_requires_anchor_false_for_non_write_tools() -> None:
    for name in _EXPECTED_TOOL_NAMES - {"propose_save_note", "propose_save_highlight"}:
        assert requires_anchor(name) is False, f"{name} should not require anchor"


def test_requires_anchor_unknown_returns_false() -> None:
    assert requires_anchor("nonexistent_tool") is False


# ---------------------------------------------------------------------------
# Helper: get_tool_spec
# ---------------------------------------------------------------------------

def test_get_tool_spec_returns_spec_for_known_tool() -> None:
    spec = get_tool_spec("get_record_context")
    assert spec is not None
    assert spec.name == "get_record_context"
    assert isinstance(spec, ToolSpec)


def test_get_tool_spec_returns_none_for_unknown_tool() -> None:
    assert get_tool_spec("nonexistent_tool") is None


# ---------------------------------------------------------------------------
# Tool-name constants match string values
# ---------------------------------------------------------------------------

def test_tool_name_constants_match_strings() -> None:
    assert TOOL_GET_RECORD_CONTEXT == "get_record_context"
    assert TOOL_GET_RECORD_INSIGHTS == "get_record_insights"
    assert TOOL_SEARCH_USER_VOCABULARY == "search_user_vocabulary"
    assert TOOL_LOOKUP_DICTIONARY_ENTRY == "lookup_dictionary_entry"
    assert TOOL_RUN_DICTIONARY_AI_CONTEXT_EXPLAIN == "run_dictionary_ai_context_explain"
    assert TOOL_GENERATE_SENTENCE_ANNOTATION == "generate_sentence_annotation"
    assert TOOL_PROPOSE_SAVE_NOTE == "propose_save_note"
    assert TOOL_PROPOSE_SAVE_HIGHLIGHT == "propose_save_highlight"


# ---------------------------------------------------------------------------
# P5-7: Agent tool registration uses registry constants
# ---------------------------------------------------------------------------

def test_agent_tool_names_use_registry_constants() -> None:
    """Verify reader_ask_agent uses registry constants for tool names.

    This test parses the source file and checks that every
    ``@agent.tool(name=...)`` decorator and every ``run_tool(...)`` call passes
    a ``TOOL_*`` identifier, not a hardcoded string literal.
    """
    import ast
    from pathlib import Path

    agent_path = Path(__file__).resolve().parent.parent / "app" / "agents" / "reader_ask_agent.py"
    source = agent_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    decorator_tool_names: list[str] = []
    run_tool_names: list[str] = []
    expected_constants = {
        "TOOL_GET_RECORD_CONTEXT",
        "TOOL_GET_RECORD_INSIGHTS",
        "TOOL_SEARCH_USER_VOCABULARY",
        "TOOL_LOOKUP_DICTIONARY_ENTRY",
        "TOOL_RUN_DICTIONARY_AI_CONTEXT_EXPLAIN",
        "TOOL_GENERATE_SENTENCE_ANNOTATION",
        "TOOL_PROPOSE_SAVE_NOTE",
        "TOOL_PROPOSE_SAVE_HIGHLIGHT",
    }

    for node in ast.walk(tree):
        # Look for @agent.tool(name=...) decorators
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            for deco in node.decorator_list:
                if not isinstance(deco, ast.Call):
                    continue
                if not isinstance(deco.func, ast.Attribute):
                    continue
                if deco.func.attr != "tool":
                    continue
                for kw in deco.keywords:
                    if kw.arg != "name":
                        continue
                    # Must be a Name node (e.g. TOOL_GET_RECORD_CONTEXT),
                    # not a Constant/str (e.g. "get_record_context")
                    if isinstance(kw.value, ast.Constant):
                        raise AssertionError(
                            f"@agent.tool(name=\"{kw.value.value}\") at line "
                            f"{kw.value.lineno} uses a hardcoded string instead "
                            "of a registry constant"
                        )
                    if isinstance(kw.value, ast.Name):
                        decorator_tool_names.append(kw.value.id)
                    else:
                        raise AssertionError(
                            f"@agent.tool(name=...) at line {kw.value.lineno} "
                            f"uses unexpected expression: {ast.dump(kw.value)}"
                        )

        # Look for run_tool(deps, TOOL_*, ...)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id != "run_tool":
                continue
            if len(node.args) < 2:
                raise AssertionError(f"run_tool(...) at line {node.lineno} has no tool name arg")
            tool_name_arg = node.args[1]
            if isinstance(tool_name_arg, ast.Constant):
                raise AssertionError(
                    f"run_tool(..., \"{tool_name_arg.value}\", ...) at line "
                    f"{tool_name_arg.lineno} uses a hardcoded string instead "
                    "of a registry constant"
                )
            if isinstance(tool_name_arg, ast.Name):
                run_tool_names.append(tool_name_arg.id)
            else:
                raise AssertionError(
                    f"run_tool(...) at line {node.lineno} uses unexpected tool "
                    f"name expression: {ast.dump(tool_name_arg)}"
                )

    actual_decorator_constants = set(decorator_tool_names)
    assert actual_decorator_constants == expected_constants, (
        f"Tool name constants mismatch: "
        f"missing={expected_constants - actual_decorator_constants}, "
        f"extra={actual_decorator_constants - expected_constants}"
    )
    actual_run_tool_constants = set(run_tool_names)
    assert actual_run_tool_constants == expected_constants, (
        f"run_tool constants mismatch: "
        f"missing={expected_constants - actual_run_tool_constants}, "
        f"extra={actual_run_tool_constants - expected_constants}"
    )


# ---------------------------------------------------------------------------
# Registry constant names match agent registration
# ---------------------------------------------------------------------------

def test_registry_names_match_agent_tool_names() -> None:
    """Verify that the registry tool name constants are the same ones
    imported and used in reader_ask_agent.py's @agent.tool(name=...) decorators."""
    from app.agents.reader_ask_agent import (
        TOOL_GENERATE_SENTENCE_ANNOTATION as AGENT_GENERATE_SENTENCE_ANNOTATION,
    )
    from app.agents.reader_ask_agent import (
        TOOL_GET_RECORD_CONTEXT as AGENT_GET_RECORD_CONTEXT,
    )
    from app.agents.reader_ask_agent import (
        TOOL_GET_RECORD_INSIGHTS as AGENT_GET_RECORD_INSIGHTS,
    )
    from app.agents.reader_ask_agent import (
        TOOL_LOOKUP_DICTIONARY_ENTRY as AGENT_LOOKUP_DICTIONARY_ENTRY,
    )
    from app.agents.reader_ask_agent import (
        TOOL_PROPOSE_SAVE_HIGHLIGHT as AGENT_PROPOSE_SAVE_HIGHLIGHT,
    )
    from app.agents.reader_ask_agent import (
        TOOL_PROPOSE_SAVE_NOTE as AGENT_PROPOSE_SAVE_NOTE,
    )
    from app.agents.reader_ask_agent import (
        TOOL_RUN_DICTIONARY_AI_CONTEXT_EXPLAIN as AGENT_RUN_DICTIONARY_AI_CONTEXT_EXPLAIN,
    )
    from app.agents.reader_ask_agent import (
        TOOL_SEARCH_USER_VOCABULARY as AGENT_SEARCH_USER_VOCABULARY,
    )

    # The agent module re-exports the same constants from the registry module,
    # so these must be identical objects (same identity and value).
    assert AGENT_GET_RECORD_CONTEXT is TOOL_GET_RECORD_CONTEXT
    assert AGENT_GET_RECORD_INSIGHTS is TOOL_GET_RECORD_INSIGHTS
    assert AGENT_SEARCH_USER_VOCABULARY is TOOL_SEARCH_USER_VOCABULARY
    assert AGENT_LOOKUP_DICTIONARY_ENTRY is TOOL_LOOKUP_DICTIONARY_ENTRY
    assert AGENT_RUN_DICTIONARY_AI_CONTEXT_EXPLAIN is TOOL_RUN_DICTIONARY_AI_CONTEXT_EXPLAIN
    assert AGENT_GENERATE_SENTENCE_ANNOTATION is TOOL_GENERATE_SENTENCE_ANNOTATION
    assert AGENT_PROPOSE_SAVE_NOTE is TOOL_PROPOSE_SAVE_NOTE
    assert AGENT_PROPOSE_SAVE_HIGHLIGHT is TOOL_PROPOSE_SAVE_HIGHLIGHT
