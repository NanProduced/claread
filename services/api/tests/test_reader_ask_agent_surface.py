"""Round 5: agent tool surface matches registry — runtime assertion tests."""

from app.agents.reader_ask_agent import get_reader_ask_agent
from app.agents.reader_ask_tool_registry import (
    DEPRECATED_TOOL_NAMES,
    RESERVED_TOOL_NAMES,
    agent_callable_tool_names,
)


def test_agent_tool_definitions_match_registry() -> None:
    """The agent's registered tool names must equal agent_callable_tool_names()."""
    agent = get_reader_ask_agent()
    registered = frozenset(agent._function_toolset.tools.keys())
    expected = agent_callable_tool_names()
    assert registered == expected, (
        f"Agent tool surface mismatch: registered={registered - expected}, "
        f"missing={expected - registered}"
    )


def test_agent_tool_definitions_exclude_deprecated() -> None:
    """Deprecated tool names must not appear in the agent's tool definitions."""
    agent = get_reader_ask_agent()
    registered = frozenset(agent._function_toolset.tools.keys())
    for name in DEPRECATED_TOOL_NAMES:
        assert name not in registered, (
            f"Deprecated tool '{name}' leaked into agent tool definitions"
        )


def test_agent_tool_definitions_exclude_reserved() -> None:
    """Reserved tool names must not appear in the agent's tool definitions."""
    agent = get_reader_ask_agent()
    registered = frozenset(agent._function_toolset.tools.keys())
    for name in RESERVED_TOOL_NAMES:
        assert name not in registered, (
            f"Reserved tool '{name}' leaked into agent tool definitions"
        )


def test_agent_module_only_imports_callable_constants() -> None:
    """AST-level check: reader_ask_agent.py only imports agent-callable TOOL_*
    constants from the registry, not deprecated or reserved ones."""
    import ast
    import importlib
    from pathlib import Path

    from app.agents.reader_ask_tool_registry import READER_ASK_TOOL_REGISTRY

    agent_path = Path(__file__).resolve().parent.parent / "app" / "agents" / "reader_ask_agent.py"
    source = agent_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    # Find the import from reader_ask_tool_registry
    imported_tool_constants: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module != "app.agents.reader_ask_tool_registry":
            continue
        for alias in node.names:
            name = alias.asname if alias.asname else alias.name
            if name.startswith("TOOL_"):
                imported_tool_constants.add(name)

    # Resolve imported constants to tool names and verify they are agent-callable
    registry_module = importlib.import_module("app.agents.reader_ask_tool_registry")
    for const_name in imported_tool_constants:
        tool_name = getattr(registry_module, const_name)
        spec = READER_ASK_TOOL_REGISTRY.get(tool_name)
        assert spec is not None, (
            f"reader_ask_agent imports {const_name}='{tool_name}' which is not in registry"
        )
        assert spec.agent_callable is True, (
            f"reader_ask_agent imports {const_name}='{tool_name}' which is not agent-callable"
        )
