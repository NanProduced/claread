"""Round 20 regression tests: eval trace uses runtime-route semantics.

The database column is still named ``planning_snapshot_json`` for now, but
new writes must carry agent-loop-oriented fields so downstream eval readers do
not need to infer current runtime semantics from legacy planner names.
"""

from __future__ import annotations

from app.services.reader_ask import service as service_svc


def test_agent_loop_trace_snapshot_has_runtime_route_fields() -> None:
    data = service_svc._planning_snapshot_json(
        None,
        planner_route_used="agent_loop_first",
    )

    assert data["trace_kind"] == "agent_loop_trace_snapshot"
    assert data["runtime_route"] == "agent_loop"
    assert data["planner_removed"] is True
    # Historical compatibility fields are intentionally retained.
    assert data["planner_skipped"] is True
    assert data["planner_route_used"] == "agent_loop_first"


def test_legacy_planner_trace_snapshot_remains_readable() -> None:
    data = service_svc._planning_snapshot_json(
        None,
        planner_route_used="planner_first",
    )

    assert data["trace_kind"] == "legacy_planner_trace_snapshot"
    assert data["runtime_route"] == "legacy_planner"
    assert data["planner_removed"] is False
    assert data["planner_skipped"] is False
    assert data["planner_route_used"] == "planner_first"


def test_metrics_json_exposes_runtime_route_next_to_legacy_route() -> None:
    data = service_svc._metrics_json(
        trace_summary=None,
        billed_points=0,
        usage_event_id=None,
        planner_route="agent_loop_first",
    )

    assert data["planner_route"] == "agent_loop_first"
    assert data["runtime_route"] == "agent_loop"
    assert data["planner_removed"] is True
