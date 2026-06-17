"""Round 15 regression tests: removal of executable legacy semantic planner path.

Round 15 deletes the executable semantic planner path from service.py and
planner_runtime.py. After this round:

- ``service.py`` no longer calls ``resolve_semantic_planning`` anywhere.
- ``planner_runtime.py`` no longer exposes ``resolve_semantic_planning``,
  ``run_semantic_planner``, ``fallback_semantic_planner_decision``,
  ``planner_history_messages``, ``fallback_reference_query``,
  ``SemanticPlanningResult``, ``ResolvePlanningDeps``, or ``RunPlannerDeps``.
- ``planning_deps_factory.py`` and ``reader_ask_planner_agent.py`` have been
  deleted.
- The live agent-loop-first repair (Round 14) still works.
- ``planner_first`` survives only as a trace/historical value — there is no
  executable path that reaches ``resolve_semantic_planning``.

All tests use mocks; no real LLM is called.
"""

from __future__ import annotations

import ast
import importlib.util
from uuid import uuid4

import pytest


# ---------------------------------------------------------------------------
# 1. service.py no longer calls resolve_semantic_planning
# ---------------------------------------------------------------------------


class TestServiceNoLongerCallsResolveSemanticPlanning:
    """Round 15: service.py must not call ``resolve_semantic_planning``."""

    def test_service_py_has_no_resolve_semantic_planning_calls(self) -> None:
        """AST-level check: service.py contains zero calls to
        ``resolve_semantic_planning``."""
        spec = importlib.util.find_spec("app.services.reader_ask.service")
        assert spec is not None and spec.origin is not None
        with open(spec.origin, encoding="utf-8") as f:
            source = f.read()
        module = ast.parse(source)

        resolve_calls = [
            node
            for node in ast.walk(module)
            if isinstance(node, ast.Attribute)
            and node.attr == "resolve_semantic_planning"
        ]
        # Allow references in comments/docstrings (AST only sees code).
        # Filter to only attribute accesses that are called.
        called = [
            node
            for node in ast.walk(module)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "resolve_semantic_planning"
        ]
        assert len(called) == 0, (
            f"service.py must not call resolve_semantic_planning; "
            f"found {len(called)} call(s)"
        )

    def test_service_py_has_no_build_reader_ask_resolve_planning_deps_refs(self) -> None:
        """AST-level check: service.py contains zero references to
        ``build_reader_ask_resolve_planning_deps`` (the factory has been
        deleted)."""
        spec = importlib.util.find_spec("app.services.reader_ask.service")
        assert spec is not None and spec.origin is not None
        with open(spec.origin, encoding="utf-8") as f:
            source = f.read()
        module = ast.parse(source)

        refs = [
            node
            for node in ast.walk(module)
            if isinstance(node, ast.Name)
            and node.id == "build_reader_ask_resolve_planning_deps"
        ]
        assert len(refs) == 0, (
            f"service.py must not reference build_reader_ask_resolve_planning_deps; "
            f"found {len(refs)} reference(s)"
        )

    def test_service_py_has_no_build_reader_ask_replan_event_calls(self) -> None:
        """AST-level check: service.py contains zero calls to
        ``build_reader_ask_replan_event`` (the bounded-replan block has been
        removed)."""
        spec = importlib.util.find_spec("app.services.reader_ask.service")
        assert spec is not None and spec.origin is not None
        with open(spec.origin, encoding="utf-8") as f:
            source = f.read()
        module = ast.parse(source)

        called = [
            node
            for node in ast.walk(module)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "build_reader_ask_replan_event"
        ]
        assert len(called) == 0, (
            f"service.py must not call build_reader_ask_replan_event; "
            f"found {len(called)} call(s)"
        )


# ---------------------------------------------------------------------------
# 2. planner_runtime.py no longer exposes semantic planner symbols
# ---------------------------------------------------------------------------


class TestPlannerRuntimeSemanticPlannerRemoved:
    """Round 15: planner_runtime.py must not expose semantic planner
    execution symbols."""

    def test_resolve_semantic_planning_removed(self) -> None:
        from app.services.reader_ask import planner_runtime
        assert not hasattr(planner_runtime, "resolve_semantic_planning")

    def test_run_semantic_planner_removed(self) -> None:
        from app.services.reader_ask import planner_runtime
        assert not hasattr(planner_runtime, "run_semantic_planner")

    def test_fallback_semantic_planner_decision_removed(self) -> None:
        from app.services.reader_ask import planner_runtime
        assert not hasattr(planner_runtime, "fallback_semantic_planner_decision")

    def test_planner_history_messages_removed(self) -> None:
        from app.services.reader_ask import planner_runtime
        assert not hasattr(planner_runtime, "planner_history_messages")

    def test_fallback_reference_query_removed(self) -> None:
        from app.services.reader_ask import planner_runtime
        assert not hasattr(planner_runtime, "fallback_reference_query")

    def test_semantic_planning_result_removed(self) -> None:
        from app.services.reader_ask import planner_runtime
        assert not hasattr(planner_runtime, "SemanticPlanningResult")

    def test_resolve_planning_deps_removed(self) -> None:
        from app.services.reader_ask import planner_runtime
        assert not hasattr(planner_runtime, "ResolvePlanningDeps")

    def test_run_planner_deps_removed(self) -> None:
        from app.services.reader_ask import planner_runtime
        assert not hasattr(planner_runtime, "RunPlannerDeps")

    def test_live_helpers_still_present(self) -> None:
        """The live helpers that service.py depends on must still be
        present."""
        from app.services.reader_ask import planner_runtime
        assert hasattr(planner_runtime, "annotation_quick_action_kind")
        assert hasattr(planner_runtime, "submission_mode")
        assert hasattr(planner_runtime, "quick_action_not_applicable")
        assert hasattr(planner_runtime, "quick_action_label")
        assert hasattr(planner_runtime, "quick_action_content")


# ---------------------------------------------------------------------------
# 3. planning_deps_factory.py and reader_ask_planner_agent.py deleted
# ---------------------------------------------------------------------------


class TestDeletedModules:
    """Round 15: planning_deps_factory.py and reader_ask_planner_agent.py
    have been deleted."""

    def test_planning_deps_factory_module_deleted(self) -> None:
        spec = importlib.util.find_spec(
            "app.services.reader_ask.planning_deps_factory"
        )
        assert spec is None, "planning_deps_factory.py must be deleted"

    def test_reader_ask_planner_agent_module_deleted(self) -> None:
        spec = importlib.util.find_spec(
            "app.agents.reader_ask_planner_agent"
        )
        assert spec is None, "reader_ask_planner_agent.py must be deleted"


# ---------------------------------------------------------------------------
# 4. planner_first survives only as a trace/historical value
# ---------------------------------------------------------------------------


class TestPlannerFirstTraceValueRetained:
    """Round 15: ``planner_first`` survives as a ``PlannerRoute`` literal
    for backward-compatible trace serialization, but no executable path
    reaches ``resolve_semantic_planning``."""

    def test_planner_first_is_valid_route_literal(self) -> None:
        from app.services.reader_ask.planner_route_policy import PlannerRoute
        # The literal must still accept "planner_first" for trace compat.
        route: PlannerRoute = "planner_first"
        assert route == "planner_first"

    def test_planner_first_trace_serialization(self) -> None:
        """planner_route_used='planner_first' still works in trace."""
        from app.services.reader_ask.service import _planning_snapshot_json
        data = _planning_snapshot_json(
            None, planner_route_used="planner_first"
        )
        assert data is not None
        assert data["planner_route_used"] == "planner_first"

    def test_resolve_planner_route_always_returns_agent_loop_first(self) -> None:
        """The route resolver always returns agent_loop_first — no live
        condition triggers planner_first."""
        from app.services.reader_ask.planner_route_policy import resolve_planner_route
        route = resolve_planner_route(
            entry_action="ask_about_this",
            history_messages=[],
            attachments=[],
            anchors=[],
            cross_record_toggle=False,
            latest_user_message="test",
        )
        assert route == "agent_loop_first"
