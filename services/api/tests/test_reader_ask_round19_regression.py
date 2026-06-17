"""Round 19 regression tests: service runtime no longer resolves planner routes.

Round 19 keeps the public trace literals and route-policy helper predicates,
but removes the remaining live service dependency on ``resolve_planner_route``
and deletes unreachable clarification-only planner branches from stream/retry.

All tests are static / AST-level — no real LLM is called.
"""

from __future__ import annotations

import ast
import inspect

from app.services.reader_ask import service as service_svc


def _service_source() -> str:
    return inspect.getsource(service_svc)


def test_service_no_longer_imports_planner_route_policy() -> None:
    module = ast.parse(_service_source())
    imported_names: set[str] = set()
    for node in ast.walk(module):
        if isinstance(node, ast.ImportFrom):
            imported_names.update(alias.asname or alias.name for alias in node.names)

    assert "planner_route_policy" not in imported_names


def test_service_no_longer_calls_resolve_planner_route() -> None:
    module = ast.parse(_service_source())
    calls: list[ast.Call] = [node for node in ast.walk(module) if isinstance(node, ast.Call)]

    for call in calls:
        func = call.func
        assert not (
            isinstance(func, ast.Attribute)
            and func.attr == "resolve_planner_route"
        )
        assert not (
            isinstance(func, ast.Name)
            and func.id == "resolve_planner_route"
        )


def test_stream_and_retry_have_no_planner_clarification_only_branch() -> None:
    source = _service_source()

    assert "clarification_only" not in source
    assert "reader_ask_unused_reservation_clarification" not in source
    assert "build_clarification_message" not in source


def test_service_has_no_planning_result_placeholder() -> None:
    source = _service_source()

    assert "planning_result" not in source
