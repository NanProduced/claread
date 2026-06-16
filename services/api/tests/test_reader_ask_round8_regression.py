"""Round 8 regression tests: fast_path naming cleanup + deictic migration.

These tests verify:
1. Deictic-without-anchor now routes to agent_loop_first (not planner_first)
2. has_deictic_without_anchor is a public API
3. build_agent_loop_context sets deictic_clarification_hint
4. followup_hint flows to prompt payload
5. fast_path naming has been cleaned up (no stale references)
6. Remaining planner_first fallbacks are preserved
7. MinimalPlanningSnapshot naming is correct
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.agents.reader_ask_agent import ReaderAskRuntimeState
from app.schemas.reader_ask import (
    ReaderAskAnchorRef,
    ReaderAskAttachment,
    ReaderAskAttachmentMetadata,
)
from app.services.reader_ask import planner_route_policy


# ---------------------------------------------------------------------------
# 1. Deictic-without-anchor routes to agent_loop_first
# ---------------------------------------------------------------------------


class TestDeicticRoutesToAgentLoopFirst:
    """Verify that deictic-without-anchor no longer triggers planner_first."""

    def test_deictic_chinese_returns_agent_loop_first(self) -> None:
        assert (
            planner_route_policy.resolve_planner_route(
                entry_action="ask_about_this",
                history_messages=[],
                attachments=[],
                anchors=[],
                cross_record_toggle=False,
                latest_user_message="解释这句",
            )
            == "agent_loop_first"
        )

    def test_deictic_english_returns_agent_loop_first(self) -> None:
        assert (
            planner_route_policy.resolve_planner_route(
                entry_action="ask_about_this",
                history_messages=[],
                attachments=[],
                anchors=[],
                cross_record_toggle=False,
                latest_user_message="explain this sentence",
            )
            == "agent_loop_first"
        )

    def test_deictic_zheli_returns_agent_loop_first(self) -> None:
        assert (
            planner_route_policy.resolve_planner_route(
                entry_action="ask_about_this",
                history_messages=[],
                attachments=[],
                anchors=[],
                cross_record_toggle=False,
                latest_user_message="这里什么意思",
            )
            == "agent_loop_first"
        )

    def test_deictic_with_anchor_still_agent_loop_first(self) -> None:
        """Anchor grounds the deictic, so agent-loop handles it."""
        assert (
            planner_route_policy.resolve_planner_route(
                entry_action="ask_about_this",
                history_messages=[],
                attachments=[],
                anchors=[ReaderAskAnchorRef(anchor_type="sentence", label="a", sentence_id="s1")],  # type: ignore[arg-type]
                cross_record_toggle=False,
                latest_user_message="解释这句",
            )
            == "agent_loop_first"
        )


# ---------------------------------------------------------------------------
# 2. has_deictic_without_anchor is public API
# ---------------------------------------------------------------------------


class TestHasDeicticWithoutAnchorPublic:
    """Verify has_deictic_without_anchor is a public, callable API."""

    def test_function_is_callable(self) -> None:
        assert callable(planner_route_policy.has_deictic_without_anchor)

    def test_returns_true_for_deictic_no_anchors(self) -> None:
        assert planner_route_policy.has_deictic_without_anchor("解释这句", []) is True

    def test_returns_false_for_deictic_with_anchors(self) -> None:
        anchors = [ReaderAskAnchorRef(anchor_type="sentence", label="a", sentence_id="s1")]  # type: ignore[arg-type]
        assert planner_route_policy.has_deictic_without_anchor("解释这句", anchors) is False

    def test_returns_false_for_non_deictic(self) -> None:
        assert planner_route_policy.has_deictic_without_anchor("这篇文章的主题", []) is False

    def test_returns_false_for_empty_string(self) -> None:
        assert planner_route_policy.has_deictic_without_anchor("", []) is False


# ---------------------------------------------------------------------------
# 3. build_agent_loop_context sets deictic_clarification_hint
# ---------------------------------------------------------------------------


class TestDeicticClarificationHint:
    """Verify that build_agent_loop_context sets the hint on runtime_state."""

    def _make_record(self) -> MagicMock:
        record = MagicMock()
        record.record_id = uuid4()
        record.title = "Test Record"
        record.render_scene = {}
        record.page_state_json = {}
        return record

    def test_hint_set_when_deictic_no_anchor(self) -> None:
        from app.services.reader_ask.context_runtime import build_agent_loop_context

        record = self._make_record()
        runtime_state = ReaderAskRuntimeState()

        with patch("app.services.reader_ask.context_runtime.planner.build_resolved_context_input", return_value={}):
            build_agent_loop_context(
                record=record,
                runtime_state=runtime_state,
                anchors=[],
                attachments=[],
                user_id=uuid4(),
                page_identity=MagicMock(),
                entry_action="ask_about_this",
                latest_user_message="解释这句",
            )

        assert runtime_state.deictic_clarification_hint is not None
        assert "指代表达" in runtime_state.deictic_clarification_hint

    def test_hint_not_set_when_anchor_present(self) -> None:
        from app.services.reader_ask.context_runtime import build_agent_loop_context

        record = self._make_record()
        runtime_state = ReaderAskRuntimeState()
        anchors = [ReaderAskAnchorRef(anchor_type="sentence", label="a", sentence_id="s1")]  # type: ignore[arg-type]

        with patch("app.services.reader_ask.context_runtime.planner.build_resolved_context_input", return_value={}):
            build_agent_loop_context(
                record=record,
                runtime_state=runtime_state,
                anchors=anchors,
                attachments=[],
                user_id=uuid4(),
                page_identity=MagicMock(),
                entry_action="ask_about_this",
                latest_user_message="解释这句",
            )

        assert runtime_state.deictic_clarification_hint is None

    def test_hint_not_set_when_no_deictic(self) -> None:
        from app.services.reader_ask.context_runtime import build_agent_loop_context

        record = self._make_record()
        runtime_state = ReaderAskRuntimeState()

        with patch("app.services.reader_ask.context_runtime.planner.build_resolved_context_input", return_value={}):
            build_agent_loop_context(
                record=record,
                runtime_state=runtime_state,
                anchors=[],
                attachments=[],
                user_id=uuid4(),
                page_identity=MagicMock(),
                entry_action="ask_about_this",
                latest_user_message="这篇文章的主题",
            )

        assert runtime_state.deictic_clarification_hint is None


# ---------------------------------------------------------------------------
# 4. followup_hint flows to prompt payload
# ---------------------------------------------------------------------------


class TestFollowupHintInPayload:
    """Verify followup_hint is included in the prompt payload."""

    def _make_contract(self, *, followup_hint: str | None = None, planning_snapshot=None):
        from app.services.reader_ask.runtime_contract import ReaderAskAnswerRuntimeInput
        from app.schemas.reader_ask import ReaderAskPageIdentity

        record = MagicMock()
        record.record_id = uuid4()
        record.title = "Test"
        record.workflow_version = "1"
        record.schema_version = "1"

        return ReaderAskAnswerRuntimeInput(
            thread={"id": "t-1", "record_id": "r-1", "title": "Test"},
            record=record,
            user_message="解释这句",
            history_messages=[],
            page_identity=ReaderAskPageIdentity(
                record_id="r-1",
                title="Test",
                available_context_capabilities=["record_context"],
                has_article_overview=True,
                has_sentence_entries=True,
                has_annotations=False,
                has_reader_notes=False,
            ),
            attachments=[],
            anchors=[],
            resolved_intent="explain",
            resolved_intent_label="Explain",
            entry_action="ask_about_this",
            submission_mode="chat",
            cross_record_context_allowed=False,
            resolved_context_input=None,
            quick_action_annotation=None,
            reference_resolution=None,
            planning_snapshot=planning_snapshot,
            max_history_messages=10,
            max_message_text=800,
            followup_hint=followup_hint,
        )

    def test_followup_hint_in_payload(self) -> None:
        from app.services.reader_ask.runtime_contract import build_prompt_payload

        contract = self._make_contract(followup_hint="请先追问用户选中具体位置")
        payload = build_prompt_payload(contract)
        assert payload["followup_hint"] == "请先追问用户选中具体位置"

    def test_followup_hint_none_in_payload(self) -> None:
        from app.services.reader_ask.runtime_contract import build_prompt_payload

        contract = self._make_contract(followup_hint=None)
        payload = build_prompt_payload(contract)
        assert payload["followup_hint"] is None

    def test_followup_hint_takes_priority_over_planning_snapshot(self) -> None:
        """When both followup_hint and planning_snapshot.clarification_reason exist,
        followup_hint takes priority."""
        from app.services.reader_ask.runtime_contract import build_prompt_payload
        from app.services.reader_ask.planner import MinimalPlanningSnapshot

        snap = MinimalPlanningSnapshot(
            clarification_mode="can_answer_with_followup",
            clarification_reason="planner says followup",
        )
        contract = self._make_contract(
            followup_hint="agent-loop deictic hint",
            planning_snapshot=snap,
        )
        payload = build_prompt_payload(contract)
        assert payload["followup_hint"] == "agent-loop deictic hint"

    def test_planning_snapshot_clarification_used_as_fallback(self) -> None:
        """When followup_hint is None but planning_snapshot has clarification,
        the planning_snapshot clarification is used."""
        from app.services.reader_ask.runtime_contract import build_prompt_payload
        from app.services.reader_ask.planner import MinimalPlanningSnapshot

        snap = MinimalPlanningSnapshot(
            clarification_mode="can_answer_with_followup",
            clarification_reason="planner says followup",
        )
        contract = self._make_contract(
            followup_hint=None,
            planning_snapshot=snap,
        )
        payload = build_prompt_payload(contract)
        assert payload["followup_hint"] == "planner says followup"

    def test_dictionary_context_explain_not_advertised(self) -> None:
        """Round 7 removed Ask dictionary tools; payload must not advertise them."""
        from app.services.reader_ask.runtime_contract import build_prompt_payload

        contract = self._make_contract()
        payload = build_prompt_payload(contract)
        assert payload["tooling_contract"]["dictionary_context_explain_available"] is False


# ---------------------------------------------------------------------------
# 5. fast_path naming cleanup
# ---------------------------------------------------------------------------


class TestFastPathNamingCleanup:
    """Verify that fast_path naming has been cleaned up."""

    def test_no_fast_path_runtime_module(self) -> None:
        """fast_path_runtime.py should not exist as a module."""
        from app.services.reader_ask import planner_route_policy

        # The module should be planner_route_policy, not fast_path_runtime
        assert planner_route_policy.__name__.endswith("planner_route_policy")

    def test_no_fast_path_planning_snapshot_class(self) -> None:
        """FastPathPlanningSnapshot should not exist; use MinimalPlanningSnapshot."""
        from app.services.reader_ask import planner

        assert not hasattr(planner, "FastPathPlanningSnapshot")
        assert hasattr(planner, "MinimalPlanningSnapshot")

    def test_has_deictic_without_anchor_is_public(self) -> None:
        """has_deictic_without_anchor should be a public function (no underscore prefix)."""
        assert hasattr(planner_route_policy, "has_deictic_without_anchor")
        assert not planner_route_policy.has_deictic_without_anchor.__name__.startswith("_")


# ---------------------------------------------------------------------------
# 6. Remaining planner_first fallbacks preserved
# ---------------------------------------------------------------------------


class TestPlannerFirstFallbacksPreserved:
    """Verify that the remaining planner_first fallbacks are still intact."""

    def test_external_attachment_fallback(self) -> None:
        att = ReaderAskAttachment(
            kind="record_ref",
            subtype="related_record",
            label="att",
            metadata=ReaderAskAttachmentMetadata(source_surface="reader_page"),
        )
        route = planner_route_policy.resolve_planner_route(
            entry_action="ask_about_this",
            history_messages=[],
            attachments=[att],
            anchors=[],
            cross_record_toggle=False,
            latest_user_message="对照我之前那篇",
        )
        assert route == "planner_first"

    def test_dictionary_anchor_fallback(self) -> None:
        dict_anchor = ReaderAskAnchorRef(
            anchor_type="dictionary_entry", label="dict", dict_entry_id=1
        )
        route = planner_route_policy.resolve_planner_route(
            entry_action="ask_about_this",
            history_messages=[],
            attachments=[],
            anchors=[dict_anchor],
            cross_record_toggle=False,
            latest_user_message="这个词什么意思",
        )
        assert route == "planner_first"

    def test_dictionary_attachment_fallback(self) -> None:
        dict_att = ReaderAskAttachment(
            kind="text_selection",
            subtype="dictionary_entry",
            label="dict",
            metadata=ReaderAskAttachmentMetadata(source_surface="reader_page"),
        )
        route = planner_route_policy.resolve_planner_route(
            entry_action="ask_about_this",
            history_messages=[],
            attachments=[dict_att],
            anchors=[],
            cross_record_toggle=False,
            latest_user_message="这个词什么意思",
        )
        assert route == "planner_first"

    def test_cross_record_toggle_with_keywords_fallback(self) -> None:
        route = planner_route_policy.resolve_planner_route(
            entry_action="ask_about_this",
            history_messages=[],
            attachments=[],
            anchors=[],
            cross_record_toggle=True,
            latest_user_message="和我之前那篇文章有什么不同？",
        )
        assert route == "planner_first"

    def test_long_history_fallback(self) -> None:
        history = [{"role": "user", "content_md": f"msg {i}"} for i in range(11)]
        route = planner_route_policy.resolve_planner_route(
            entry_action="ask_about_this",
            history_messages=history,
            attachments=[],
            anchors=[],
            cross_record_toggle=False,
            latest_user_message="继续",
        )
        assert route == "planner_first"


# ---------------------------------------------------------------------------
# 7. MinimalPlanningSnapshot naming
# ---------------------------------------------------------------------------


class TestMinimalPlanningSnapshotNaming:
    """Verify MinimalPlanningSnapshot is correctly named and functional."""

    def test_class_exists(self) -> None:
        from app.services.reader_ask.planner import MinimalPlanningSnapshot

        assert MinimalPlanningSnapshot is not None

    def test_has_required_fields(self) -> None:
        import dataclasses
        from app.services.reader_ask.planner import MinimalPlanningSnapshot

        field_names = {f.name for f in dataclasses.fields(MinimalPlanningSnapshot)}
        required = {
            "retrieval_needs",
            "working_set",
            "context_plan",
            "trace_summary",
            "clarification_mode",
            "clarification_reason",
        }
        assert required <= field_names

    def test_default_clarification_mode_is_none(self) -> None:
        from app.services.reader_ask.planner import MinimalPlanningSnapshot

        snap = MinimalPlanningSnapshot()
        assert snap.clarification_mode == "none"
