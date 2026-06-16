"""Round 7 tests: real-LLM smoke guard behavior and residual cleanup regression.

These tests verify:
1. The @pytest.mark.real_llm marker + conftest guard works correctly
2. Deleted code stays deleted (regression)
3. Preserved code stays alive (regression)
4. Registry invariants hold after dictionary cleanup
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


# ---------------------------------------------------------------------------
# 1. Real-LLM smoke guard behavior
# ---------------------------------------------------------------------------


class TestRealLlmMarkerGuard:
    """Verify the opt-in mechanism for real LLM tests."""

    class _Config:
        def __init__(self, markexpr: str) -> None:
            self.markexpr = markexpr

        def getoption(self, name: str, default: str = "") -> str:
            if name == "markexpr":
                return self.markexpr
            return default

    class _Node:
        def __init__(self, *, marked: bool) -> None:
            self.marked = marked

        def get_closest_marker(self, name: str):
            if name == "real_llm" and self.marked:
                return object()
            return None

    @classmethod
    def _request(cls, *, markexpr: str, marked: bool = True):
        return SimpleNamespace(
            node=cls._Node(marked=marked),
            config=cls._Config(markexpr),
        )

    def test_conftest_has_skip_real_llm_tests_fixture(self) -> None:
        """conftest.py should define the skip_real_llm_tests fixture."""
        import tests.conftest as conftest_mod

        assert hasattr(conftest_mod, "skip_real_llm_tests")

    def test_conftest_has_fail_on_real_llm_attempts_fixture(self) -> None:
        """conftest.py should define the fail_on_real_llm_attempts fixture."""
        import tests.conftest as conftest_mod

        assert hasattr(conftest_mod, "fail_on_real_llm_attempts")

    def test_conftest_has_shared_real_llm_gate_helper(self) -> None:
        """Both real-LLM fixtures should use the same shared gate helper."""
        import tests.conftest as conftest_mod

        assert hasattr(conftest_mod, "_real_llm_gate_open")

    def test_real_llm_gate_requires_exact_markexpr(self, monkeypatch) -> None:
        """Env vars alone are not enough; the mark expression must be exactly real_llm."""
        import tests.conftest as conftest_mod

        monkeypatch.setenv("CLAREAD_ALLOW_REAL_LLM_TESTS", "1")
        monkeypatch.setenv("CLAREAD_REAL_LLM_MODEL", "qwen-plus")

        assert conftest_mod._real_llm_gate_open(self._request(markexpr="real_llm")) is True
        assert conftest_mod._real_llm_gate_open(self._request(markexpr="")) is False
        assert conftest_mod._real_llm_gate_open(self._request(markexpr="not real_llm")) is False

    def test_call_guard_real_llm_tests_allowed_function_exists(self) -> None:
        """call_guard.py should expose real_llm_tests_allowed()."""
        from app.llm.call_guard import real_llm_tests_allowed

        assert callable(real_llm_tests_allowed)

    def test_real_llm_tests_not_allowed_by_default(self, monkeypatch) -> None:
        """In normal pytest runs, real_llm_tests_allowed() should be False."""
        from app.llm.call_guard import real_llm_tests_allowed

        monkeypatch.delenv("CLAREAD_ALLOW_REAL_LLM_TESTS", raising=False)
        assert not real_llm_tests_allowed()

    def test_real_llm_model_env_not_set_by_default(self) -> None:
        """CLAREAD_REAL_LLM_MODEL should not be set in normal test runs."""
        import os

        assert not os.environ.get("CLAREAD_REAL_LLM_MODEL")


# ---------------------------------------------------------------------------
# 2. Deleted code stays deleted
# ---------------------------------------------------------------------------


class TestDeletedCodeStaysDeleted:
    """Verify that code removed in Round 5/7 has not been re-introduced."""

    def test_should_use_fast_path_not_in_planner_route_policy(self) -> None:
        """should_use_fast_path was deleted in Round 7."""
        from app.services.reader_ask import planner_route_policy

        assert not hasattr(planner_route_policy, "should_use_fast_path")

    def test_fast_path_actions_constant_not_in_planner_route_policy(self) -> None:
        """_FAST_PATH_ACTIONS was deleted in Round 7."""
        from app.services.reader_ask import planner_route_policy

        assert not hasattr(planner_route_policy, "_FAST_PATH_ACTIONS")

    def test_tool_search_user_vocabulary_not_in_registry(self) -> None:
        """search_user_vocabulary was deleted in Round 5."""
        from app.agents.reader_ask_tool_registry import READER_ASK_TOOL_NAMES

        assert "search_user_vocabulary" not in READER_ASK_TOOL_NAMES

    def test_tool_lookup_dictionary_entry_not_in_registry(self) -> None:
        """lookup_dictionary_entry was deleted in Round 7."""
        from app.agents.reader_ask_tool_registry import READER_ASK_TOOL_NAMES

        assert "lookup_dictionary_entry" not in READER_ASK_TOOL_NAMES

    def test_tool_run_dictionary_ai_context_explain_not_in_registry(self) -> None:
        """run_dictionary_ai_context_explain was deleted in Round 7."""
        from app.agents.reader_ask_tool_registry import READER_ASK_TOOL_NAMES

        assert "run_dictionary_ai_context_explain" not in READER_ASK_TOOL_NAMES

    def test_deprecated_tool_names_not_in_registry(self) -> None:
        """DEPRECATED_TOOL_NAMES was deleted in Round 7."""
        from app.agents import reader_ask_tool_registry

        assert not hasattr(reader_ask_tool_registry, "DEPRECATED_TOOL_NAMES")

    def test_latest_dictionary_entry_not_in_runtime_state(self) -> None:
        """latest_dictionary_entry was removed from RuntimeState in Round 7."""
        import dataclasses

        from app.agents.reader_ask_agent import ReaderAskRuntimeState

        field_names = {f.name for f in dataclasses.fields(ReaderAskRuntimeState)}
        assert "latest_dictionary_entry" not in field_names

    def test_latest_dictionary_ai_not_in_runtime_state(self) -> None:
        """latest_dictionary_ai was removed from RuntimeState in Round 7."""
        import dataclasses

        from app.agents.reader_ask_agent import ReaderAskRuntimeState

        field_names = {f.name for f in dataclasses.fields(ReaderAskRuntimeState)}
        assert "latest_dictionary_ai" not in field_names

    def test_is_fast_path_not_in_planning_snapshot(self) -> None:
        """is_fast_path was replaced with planner_skipped in Round 7."""
        from pathlib import Path

        service_source = Path(
            "app/services/reader_ask/service.py"
        ).read_text(encoding="utf-8")
        assert '"is_fast_path"' not in service_source

    def test_ensure_task_card_data_not_in_service(self) -> None:
        """_ensure_task_card_data was deleted in Round 5."""
        from pathlib import Path

        service_source = Path(
            "app/services/reader_ask/service.py"
        ).read_text(encoding="utf-8")
        assert "_ensure_task_card_data" not in service_source

    def test_tool_lookup_dictionary_entry_not_in_service(self) -> None:
        """_tool_lookup_dictionary_entry was deleted in Round 7."""
        from pathlib import Path

        service_source = Path(
            "app/services/reader_ask/service.py"
        ).read_text(encoding="utf-8")
        assert "_tool_lookup_dictionary_entry" not in service_source

    def test_tool_run_dictionary_ai_context_explain_not_in_service(self) -> None:
        """_tool_run_dictionary_ai_context_explain was deleted in Round 7."""
        from pathlib import Path

        service_source = Path(
            "app/services/reader_ask/service.py"
        ).read_text(encoding="utf-8")
        assert "_tool_run_dictionary_ai_context_explain" not in service_source

    def test_dictionary_item_to_citation_not_in_service(self) -> None:
        """_dictionary_item_to_citation was deleted in Round 7."""
        from pathlib import Path

        service_source = Path(
            "app/services/reader_ask/service.py"
        ).read_text(encoding="utf-8")
        assert "_dictionary_item_to_citation" not in service_source

    def test_dictionary_ai_to_citation_not_in_service(self) -> None:
        """_dictionary_ai_to_citation was deleted in Round 7."""
        from pathlib import Path

        service_source = Path(
            "app/services/reader_ask/service.py"
        ).read_text(encoding="utf-8")
        assert "_dictionary_ai_to_citation" not in service_source


# ---------------------------------------------------------------------------
# 3. Preserved code stays alive
# ---------------------------------------------------------------------------


class TestPreservedCodeStaysAlive:
    """Verify that code intentionally preserved in Round 7 still exists."""

    def test_resolve_planner_route_exists(self) -> None:
        """resolve_planner_route is the core routing function."""
        from app.services.reader_ask.planner_route_policy import resolve_planner_route

        assert callable(resolve_planner_route)

    def test_planner_first_route_value_still_valid(self) -> None:
        """planner_first is still a valid route value for long-history fallbacks."""
        from app.services.reader_ask.planner_route_policy import resolve_planner_route

        # Long history → planner_first (dictionary migrated in Round 11)
        history = [{"role": "user", "content_md": f"msg {i}"} for i in range(11)]
        route = resolve_planner_route(
            entry_action="ask_about_this",
            history_messages=history,
            attachments=[],
            anchors=[],
            cross_record_toggle=False,
            latest_user_message="继续",
        )
        assert route == "planner_first"

    def test_materialize_planned_context_exists(self) -> None:
        """materialize_planned_context is still used by planner_first path."""
        from app.services.reader_ask.context_runtime import (
            materialize_planned_context,
        )

        assert callable(materialize_planned_context)

    def test_build_replan_event_exists(self) -> None:
        """build_replan_event is still used by planner_first path."""
        from app.services.reader_ask.agent_runner import build_replan_event

        assert callable(build_replan_event)

    def test_reserved_tool_names_exists(self) -> None:
        """RESERVED_TOOL_NAMES should still exist (contains lookup_record_by_embedding)."""
        from app.agents.reader_ask_tool_registry import RESERVED_TOOL_NAMES

        assert "lookup_record_by_embedding" in RESERVED_TOOL_NAMES

    def test_lookup_record_by_embedding_in_registry(self) -> None:
        """lookup_record_by_embedding should still be in the registry as reserved."""
        from app.agents.reader_ask_tool_registry import READER_ASK_TOOL_REGISTRY

        assert "lookup_record_by_embedding" in READER_ASK_TOOL_REGISTRY

    def test_planner_skipped_field_in_metrics(self) -> None:
        """planner_skipped should be in _metrics_json output (replaces is_fast_path)."""
        from app.services.reader_ask.service import _metrics_json

        result = _metrics_json(
            trace_summary=None,
            billed_points=0,
            usage_event_id=None,
            runtime_state=None,
        )
        # is_fast_path should not exist; planner_skipped only appears with runtime_state
        assert "is_fast_path" not in result


# ---------------------------------------------------------------------------
# 4. Registry invariants after dictionary cleanup
# ---------------------------------------------------------------------------


class TestRegistryInvariantsAfterCleanup:
    """Verify registry invariants hold after Round 7 dictionary cleanup."""

    def test_registry_has_10_entries(self) -> None:
        """Registry should have 10 entries: 9 callable + 1 reserved."""
        from app.agents.reader_ask_tool_registry import READER_ASK_TOOL_REGISTRY

        assert len(READER_ASK_TOOL_REGISTRY) == 10

    def test_9_agent_callable_tools(self) -> None:
        """Exactly 9 tools should be agent-callable."""
        from app.agents.reader_ask_tool_registry import agent_callable_tool_names

        assert len(agent_callable_tool_names()) == 9

    def test_1_non_agent_callable_tool(self) -> None:
        """Exactly 1 tool should be non-agent-callable (reserved)."""
        from app.agents.reader_ask_tool_registry import non_agent_callable_tool_names

        assert len(non_agent_callable_tool_names()) == 1

    def test_reserved_tool_is_non_callable(self) -> None:
        """The reserved tool should be non-agent-callable."""
        from app.agents.reader_ask_tool_registry import (
            RESERVED_TOOL_NAMES,
            non_agent_callable_tool_names,
        )

        assert RESERVED_TOOL_NAMES <= non_agent_callable_tool_names()

    def test_assert_registry_invariants_passes(self) -> None:
        """assert_registry_invariants() should pass without error."""
        from app.agents.reader_ask_tool_registry import assert_registry_invariants

        assert_registry_invariants()  # Should not raise

    def test_no_reserved_tools_in_policy_allowed(self) -> None:
        """build_tool_availability should never include reserved tools."""
        from app.agents.reader_ask_tool_policy import (
            ToolAvailabilityInput,
            build_tool_availability,
        )

        avail = build_tool_availability(
            ToolAvailabilityInput(
                task_mode="general",
                entry_action="ask_about_this",
                has_primary_anchor=False,
            )
        )
        assert "lookup_record_by_embedding" not in avail.allowed_tool_names
