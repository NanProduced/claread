"""Round 17 regression tests: planner.py legacy semantic-planner cleanup.

Round 17 removes the remaining executable semantic-planner decision
consumption API from ``planner.py``. The module now retains only live
agent-loop-first helpers and trace/historical dataclasses.
"""

from __future__ import annotations

import inspect

from app.services.reader_ask import planner as planner_svc


class TestPlannerLegacyApiRemoved:
    def test_plan_request_removed(self) -> None:
        assert not hasattr(planner_svc, "plan_request")

    def test_planner_input_builder_removed(self) -> None:
        assert not hasattr(planner_svc, "build_planner_input")

    def test_decision_consumption_helpers_removed(self) -> None:
        removed_names = {
            "reference_needs_from_decision",
            "_structured_asset_needs_from_decision",
            "_planned_context_plan",
            "_planned_trace_summary",
            "_planned_disambiguation_state",
            "_planned_external_asset_disambiguation_state",
        }
        for name in removed_names:
            assert not hasattr(planner_svc, name), f"{name} should stay removed"

    def test_planner_module_no_longer_imports_planner_decision_schema(self) -> None:
        source = inspect.getsource(planner_svc)
        assert "ReaderAskPlannerDecision" not in source
        assert "ReaderAskPlannerInput" not in source
        assert "ReaderAskPlannerHistoryMessage" not in source


class TestPlannerLiveHelpersRetained:
    def test_agent_loop_helpers_still_exist(self) -> None:
        retained_names = {
            "MinimalPlanningSnapshot",
            "build_minimal_context_plan",
            "build_minimal_trace_summary",
            "build_minimal_resolved_intent",
            "build_resolved_context_input",
            "build_context_plan",
            "build_trace_summary",
            "build_resolved_context_summary",
        }
        for name in retained_names:
            assert hasattr(planner_svc, name), f"{name} should remain available"

    def test_trace_dataclasses_still_exist(self) -> None:
        retained_names = {
            "ReaderAskPlanningSnapshot",
            "ReaderAskReferenceNeeds",
            "ReaderAskReferenceResolution",
            "ReaderAskStructuredAssetNeeds",
            "ReaderAskStructuredAssetResolution",
            "ReaderAskWorkingSet",
        }
        for name in retained_names:
            assert hasattr(planner_svc, name), f"{name} should remain available"
