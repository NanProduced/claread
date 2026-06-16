"""Tests for the Ask Claread tool registry (P5-1, Round 2 tool surface, Round 5 hardening)."""

from app.agents.reader_ask_tool_registry import (
    READER_ASK_TOOL_NAMES,
    READER_ASK_TOOL_REGISTRY,
    RESERVED_TOOL_NAMES,
    TOOL_GENERATE_SENTENCE_ANNOTATION,
    TOOL_GET_RECORD_CONTEXT,
    TOOL_GET_RECORD_INSIGHTS,
    TOOL_GET_USER_VOCABULARY_BOOK,
    TOOL_LOAD_EXPLICIT_ATTACHMENT_CONTEXT,
    TOOL_LOOKUP_RECORD_BY_EMBEDDING,
    TOOL_PROPOSE_SAVE_HIGHLIGHT,
    TOOL_PROPOSE_SAVE_NOTE,
    TOOL_RESOLVE_KNOWN_REFERENCE,
    TOOL_SUGGEST_PROMPTS,
    ToolSpec,
    agent_callable_tool_names,
    assert_registry_invariants,
    get_tool_spec,
    is_agent_callable,
    is_write_proposal_tool,
    non_agent_callable_tool_names,
    requires_anchor,
)

# ---------------------------------------------------------------------------
# Registry completeness
# ---------------------------------------------------------------------------

_ALL_TOOL_NAMES = frozenset({
    # Read / context
    TOOL_GET_RECORD_CONTEXT,
    TOOL_GET_RECORD_INSIGHTS,
    TOOL_GET_USER_VOCABULARY_BOOK,
    # Resolver
    TOOL_RESOLVE_KNOWN_REFERENCE,
    # External attachment context loader
    TOOL_LOAD_EXPLICIT_ATTACHMENT_CONTEXT,
    # Annotation
    TOOL_GENERATE_SENTENCE_ANNOTATION,
    # Write-proposal
    TOOL_PROPOSE_SAVE_NOTE,
    TOOL_PROPOSE_SAVE_HIGHLIGHT,
    # Suggestion
    TOOL_SUGGEST_PROMPTS,
    # Reserved RAG
    TOOL_LOOKUP_RECORD_BY_EMBEDDING,
})

_AGENT_CALLABLE_NAMES = frozenset({
    TOOL_GET_RECORD_CONTEXT,
    TOOL_GET_RECORD_INSIGHTS,
    TOOL_GET_USER_VOCABULARY_BOOK,
    TOOL_RESOLVE_KNOWN_REFERENCE,
    TOOL_LOAD_EXPLICIT_ATTACHMENT_CONTEXT,
    TOOL_GENERATE_SENTENCE_ANNOTATION,
    TOOL_PROPOSE_SAVE_NOTE,
    TOOL_PROPOSE_SAVE_HIGHLIGHT,
    TOOL_SUGGEST_PROMPTS,
})

_NON_AGENT_CALLABLE_NAMES = _ALL_TOOL_NAMES - _AGENT_CALLABLE_NAMES


def test_registry_contains_round2_tools() -> None:
    assert set(READER_ASK_TOOL_REGISTRY.keys()) == _ALL_TOOL_NAMES


def test_tool_names_constant_matches_registry() -> None:
    assert READER_ASK_TOOL_NAMES == _ALL_TOOL_NAMES


def test_every_tool_name_is_unique() -> None:
    names = [spec.name for spec in READER_ASK_TOOL_REGISTRY.values()]
    assert len(names) == len(set(names))


# ---------------------------------------------------------------------------
# Agent-callable filter — the main agent only sees a subset
# ---------------------------------------------------------------------------


def test_agent_callable_tool_names_set() -> None:
    assert agent_callable_tool_names() == _AGENT_CALLABLE_NAMES


def test_reserved_rag_tool_not_agent_callable() -> None:
    spec = READER_ASK_TOOL_REGISTRY[TOOL_LOOKUP_RECORD_BY_EMBEDDING]
    assert spec.agent_callable is False
    assert is_agent_callable(TOOL_LOOKUP_RECORD_BY_EMBEDDING) is False


def test_deprecated_vocabulary_tool_not_agent_callable() -> None:
    """Round 5: search_user_vocabulary fully removed from registry."""
    assert "search_user_vocabulary" not in READER_ASK_TOOL_REGISTRY
    assert is_agent_callable("search_user_vocabulary") is False


def test_is_agent_callable_unknown_returns_false() -> None:
    assert is_agent_callable("nonexistent_tool") is False


# ---------------------------------------------------------------------------
# Per-tool metadata
# ---------------------------------------------------------------------------

_EXPECTED_SPECS: dict[str, dict] = {
    TOOL_GET_RECORD_CONTEXT: {
        "category": "context",
        "effect": "read",
        "requires_anchor": False,
        "consumes_budget_when_precondition_fails": True,
        "agent_callable": True,
        "output_kind": "dict_or_none",
        "observation_statuses": ("success", "warning"),
    },
    TOOL_GET_RECORD_INSIGHTS: {
        "category": "context",
        "effect": "read",
        "requires_anchor": False,
        "consumes_budget_when_precondition_fails": True,
        "agent_callable": True,
        "output_kind": "list_or_empty",
        "observation_statuses": ("success", "warning"),
    },
    TOOL_GET_USER_VOCABULARY_BOOK: {
        "category": "vocabulary",
        "effect": "read",
        "requires_anchor": False,
        "consumes_budget_when_precondition_fails": True,
        "agent_callable": True,
        "output_kind": "list_or_empty",
        "observation_statuses": ("success", "warning"),
    },
    TOOL_RESOLVE_KNOWN_REFERENCE: {
        "category": "resolver",
        "effect": "read",
        "requires_anchor": False,
        "consumes_budget_when_precondition_fails": True,
        "agent_callable": True,
        "output_kind": "dict_or_none",
        "observation_statuses": ("success", "warning"),
    },
    TOOL_LOAD_EXPLICIT_ATTACHMENT_CONTEXT: {
        "category": "context",
        "effect": "read",
        "requires_anchor": False,
        "consumes_budget_when_precondition_fails": True,
        "agent_callable": True,
        "output_kind": "dict_or_none",
        "observation_statuses": ("success", "warning"),
    },
    TOOL_SUGGEST_PROMPTS: {
        "category": "suggestion",
        "effect": "read",
        "requires_anchor": False,
        "consumes_budget_when_precondition_fails": True,
        "agent_callable": True,
        "output_kind": "dict_or_none",
        "observation_statuses": ("success", "warning"),
    },
    TOOL_GENERATE_SENTENCE_ANNOTATION: {
        "category": "annotation",
        "effect": "read",
        "requires_anchor": False,
        "consumes_budget_when_precondition_fails": True,
        "agent_callable": True,
        "output_kind": "dict_or_none",
        "observation_statuses": ("success",),
    },
    TOOL_PROPOSE_SAVE_NOTE: {
        "category": "write_proposal",
        "effect": "propose_write",
        "requires_anchor": True,
        "consumes_budget_when_precondition_fails": False,
        "agent_callable": True,
        "output_kind": "dict_always",
        "observation_statuses": ("success", "error"),
    },
    TOOL_PROPOSE_SAVE_HIGHLIGHT: {
        "category": "write_proposal",
        "effect": "propose_write",
        "requires_anchor": True,
        "consumes_budget_when_precondition_fails": False,
        "agent_callable": True,
        "output_kind": "dict_always",
        "observation_statuses": ("success", "error"),
    },
    TOOL_LOOKUP_RECORD_BY_EMBEDDING: {
        "category": "context",
        "effect": "read",
        "requires_anchor": False,
        "consumes_budget_when_precondition_fails": True,
        "agent_callable": False,
        "output_kind": "list_or_empty",
        "observation_statuses": ("success", "warning"),
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
    for name in _ALL_TOOL_NAMES - {"propose_save_note", "propose_save_highlight"}:
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
    for name in _ALL_TOOL_NAMES - {"propose_save_note", "propose_save_highlight"}:
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
    assert TOOL_GET_USER_VOCABULARY_BOOK == "get_user_vocabulary_book"
    assert TOOL_RESOLVE_KNOWN_REFERENCE == "resolve_known_reference"
    assert TOOL_LOAD_EXPLICIT_ATTACHMENT_CONTEXT == "load_explicit_attachment_context"
    assert TOOL_GENERATE_SENTENCE_ANNOTATION == "generate_sentence_annotation"
    assert TOOL_PROPOSE_SAVE_NOTE == "propose_save_note"
    assert TOOL_PROPOSE_SAVE_HIGHLIGHT == "propose_save_highlight"
    assert TOOL_SUGGEST_PROMPTS == "suggest_prompts"
    assert TOOL_LOOKUP_RECORD_BY_EMBEDDING == "lookup_record_by_embedding"


# ---------------------------------------------------------------------------
# Round 2: agent tool registration uses registry constants
# ---------------------------------------------------------------------------

# Tools that the agent module's @agent.tool(name=...) / run_tool(...) must use
# in Round 2. Deprecated search_user_vocabulary / dictionary tools and the
# reserved lookup_record_by_embedding are intentionally excluded — the main
# agent must never invoke them.
_AGENT_MODULE_TOOL_CONSTANTS = frozenset({
    "TOOL_GET_RECORD_CONTEXT",
    "TOOL_GET_RECORD_INSIGHTS",
    "TOOL_GET_USER_VOCABULARY_BOOK",
    "TOOL_RESOLVE_KNOWN_REFERENCE",
    "TOOL_LOAD_EXPLICIT_ATTACHMENT_CONTEXT",
    "TOOL_GENERATE_SENTENCE_ANNOTATION",
    "TOOL_PROPOSE_SAVE_NOTE",
    "TOOL_PROPOSE_SAVE_HIGHLIGHT",
    "TOOL_SUGGEST_PROMPTS",
})


def test_agent_tool_names_use_registry_constants() -> None:
    """Verify reader_ask_agent uses registry constants for tool names.

    This test parses the source file and checks that every
    ``@agent.tool(name=...)`` decorator and every ``run_tool(...)`` call passes
    a ``TOOL_*`` identifier from the Round 2 agent-callable set, not a
    hardcoded string literal and not a deprecated constant.
    """
    import ast
    from pathlib import Path

    agent_path = Path(__file__).resolve().parent.parent / "app" / "agents" / "reader_ask_agent.py"
    source = agent_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    decorator_tool_names: list[str] = []
    run_tool_names: list[str] = []

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
                    f"run_tool(...) at line {tool_name_arg.lineno} uses unexpected tool "
                    f"name expression: {ast.dump(tool_name_arg)}"
                )

    actual_decorator_constants = set(decorator_tool_names)
    assert actual_decorator_constants == _AGENT_MODULE_TOOL_CONSTANTS, (
        f"Tool name constants mismatch: "
        f"missing={_AGENT_MODULE_TOOL_CONSTANTS - actual_decorator_constants}, "
        f"extra={actual_decorator_constants - _AGENT_MODULE_TOOL_CONSTANTS}"
    )
    actual_run_tool_constants = set(run_tool_names)
    assert actual_run_tool_constants == _AGENT_MODULE_TOOL_CONSTANTS, (
        f"run_tool constants mismatch: "
        f"missing={_AGENT_MODULE_TOOL_CONSTANTS - actual_run_tool_constants}, "
        f"extra={actual_run_tool_constants - _AGENT_MODULE_TOOL_CONSTANTS}"
    )


# ---------------------------------------------------------------------------
# Round 2: deprecated / reserved tools do NOT leak into the agent module
# ---------------------------------------------------------------------------


def test_agent_module_does_not_register_reserved_tools() -> None:
    """The agent module must not re-export constants for reserved tools."""
    import importlib

    agent_module = importlib.import_module("app.agents.reader_ask_agent")
    for name in (
        "TOOL_LOOKUP_RECORD_BY_EMBEDDING",
    ):
        assert not hasattr(agent_module, name), (
            f"reader_ask_agent should not re-export {name}"
        )


# ---------------------------------------------------------------------------
# Round 5: registry invariants
# ---------------------------------------------------------------------------


def test_registry_invariants_pass() -> None:
    """Import-time invariant check must not raise."""
    assert_registry_invariants()


def test_reserved_tool_names_are_non_callable() -> None:
    assert RESERVED_TOOL_NAMES <= non_agent_callable_tool_names()


def test_callable_non_callable_partition_registry() -> None:
    callable_names = agent_callable_tool_names()
    non_callable_names = non_agent_callable_tool_names()
    assert callable_names | non_callable_names == READER_ASK_TOOL_NAMES
    assert callable_names & non_callable_names == frozenset()


def test_search_user_vocabulary_not_in_registry() -> None:
    """Round 5: search_user_vocabulary fully removed from registry."""
    assert "search_user_vocabulary" not in READER_ASK_TOOL_REGISTRY
    assert "search_user_vocabulary" not in READER_ASK_TOOL_NAMES
