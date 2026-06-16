"""Round 11 regression tests: dictionary anchor/attachment migration.

These tests verify:
1. Dictionary anchors/attachments route to agent_loop_first (no longer planner_first)
2. has_dictionary_anchor_or_attachment() is a public API
3. build_agent_loop_context sets dictionary_anchor_hint
4. dictionary_anchor_hint flows to prompt payload
5. Long history (>10) now returns agent_loop_first (Round 12)
6. Old dictionary tools are not exposed to the agent
7. Tool registry invariants still hold
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
# 1. Dictionary anchors/attachments route to agent_loop_first
# ---------------------------------------------------------------------------


class TestDictionaryRoutesToAgentLoopFirst:
    """Verify that dictionary anchors/attachments no longer trigger planner_first."""

    def test_dictionary_anchor_returns_agent_loop_first(self) -> None:
        dict_anchor = ReaderAskAnchorRef(
            anchor_type="dictionary_entry", label="dict", dict_entry_id=1
        )
        assert (
            planner_route_policy.resolve_planner_route(
                entry_action="ask_about_this",
                history_messages=[],
                attachments=[],
                anchors=[dict_anchor],
                cross_record_toggle=False,
                latest_user_message="这个词什么意思",
            )
            == "agent_loop_first"
        )

    def test_dictionary_attachment_returns_agent_loop_first(self) -> None:
        dict_att = ReaderAskAttachment(
            kind="text_selection",
            subtype="dictionary_entry",
            label="dict",
            metadata=ReaderAskAttachmentMetadata(source_surface="reader_page"),
        )
        assert (
            planner_route_policy.resolve_planner_route(
                entry_action="ask_about_this",
                history_messages=[],
                attachments=[dict_att],
                anchors=[],
                cross_record_toggle=False,
                latest_user_message="这个词什么意思",
            )
            == "agent_loop_first"
        )

    def test_dictionary_anchor_with_long_history_agent_loop_first(self) -> None:
        """Dictionary + long history: Round 12 — long history no longer triggers planner_first."""
        dict_anchor = ReaderAskAnchorRef(
            anchor_type="dictionary_entry", label="dict", dict_entry_id=1
        )
        history = [{"role": "user", "content_md": f"msg {i}"} for i in range(11)]
        assert (
            planner_route_policy.resolve_planner_route(
                entry_action="ask_about_this",
                history_messages=history,
                attachments=[],
                anchors=[dict_anchor],
                cross_record_toggle=False,
                latest_user_message="这个词什么意思",
            )
            == "agent_loop_first"
        )


# ---------------------------------------------------------------------------
# 2. has_dictionary_anchor_or_attachment is public API
# ---------------------------------------------------------------------------


class TestHasDictionaryAnchorOrAttachmentPublic:
    """Verify has_dictionary_anchor_or_attachment is a public, callable API."""

    def test_function_is_callable(self) -> None:
        assert callable(planner_route_policy.has_dictionary_anchor_or_attachment)

    def test_returns_true_for_dictionary_anchor(self) -> None:
        anchors = [
            ReaderAskAnchorRef(
                anchor_type="dictionary_entry", label="dict", dict_entry_id=1
            )
        ]
        assert planner_route_policy.has_dictionary_anchor_or_attachment(anchors, []) is True

    def test_returns_true_for_dictionary_attachment_subtype(self) -> None:
        atts = [
            ReaderAskAttachment(
                kind="text_selection",
                subtype="dictionary_entry",
                label="dict",
                metadata=ReaderAskAttachmentMetadata(source_surface="reader_page"),
            )
        ]
        assert planner_route_policy.has_dictionary_anchor_or_attachment([], atts) is True

    def test_returns_false_for_sentence_anchor(self) -> None:
        anchors = [
            ReaderAskAnchorRef(
                anchor_type="sentence", label="s1", sentence_id="sen_1"
            )
        ]
        assert planner_route_policy.has_dictionary_anchor_or_attachment(anchors, []) is False

    def test_returns_false_for_empty(self) -> None:
        assert planner_route_policy.has_dictionary_anchor_or_attachment([], []) is False


# ---------------------------------------------------------------------------
# 3. build_agent_loop_context sets dictionary_anchor_hint
# ---------------------------------------------------------------------------


class TestDictionaryAnchorHint:
    """Verify that build_agent_loop_context sets the hint on runtime_state."""

    def _make_record(self) -> MagicMock:
        record = MagicMock()
        record.record_id = uuid4()
        record.title = "Test Record"
        record.render_scene = {}
        record.page_state_json = {}
        return record

    def test_hint_set_when_dictionary_anchor_present(self) -> None:
        from app.services.reader_ask.context_runtime import build_agent_loop_context

        record = self._make_record()
        runtime_state = ReaderAskRuntimeState()
        dict_anchor = ReaderAskAnchorRef(
            anchor_type="dictionary_entry", label="dict", dict_entry_id=1
        )

        with patch("app.services.reader_ask.context_runtime.planner.build_resolved_context_input", return_value={}):
            build_agent_loop_context(
                record=record,
                runtime_state=runtime_state,
                anchors=[dict_anchor],
                attachments=[],
                user_id=uuid4(),
                page_identity=MagicMock(),
                entry_action="ask_about_this",
                latest_user_message="这个词什么意思",
            )

        assert runtime_state.dictionary_anchor_hint is not None
        assert "词典" in runtime_state.dictionary_anchor_hint
        assert "dictionary_entry" in runtime_state.dictionary_anchor_hint
        # P2 fix: hint must mention both anchors and attachments.
        assert "anchors" in runtime_state.dictionary_anchor_hint
        assert "attachments" in runtime_state.dictionary_anchor_hint

    def test_hint_set_when_dictionary_attachment_present(self) -> None:
        from app.services.reader_ask.context_runtime import build_agent_loop_context

        record = self._make_record()
        runtime_state = ReaderAskRuntimeState()
        dict_att = ReaderAskAttachment(
            kind="text_selection",
            subtype="dictionary_entry",
            label="dict",
            metadata=ReaderAskAttachmentMetadata(source_surface="reader_page"),
        )

        with patch("app.services.reader_ask.context_runtime.planner.build_resolved_context_input", return_value={}):
            build_agent_loop_context(
                record=record,
                runtime_state=runtime_state,
                anchors=[],
                attachments=[dict_att],
                user_id=uuid4(),
                page_identity=MagicMock(),
                entry_action="ask_about_this",
                latest_user_message="这个词什么意思",
            )

        assert runtime_state.dictionary_anchor_hint is not None

    def test_hint_not_set_when_no_dictionary(self) -> None:
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
                latest_user_message="解释一下",
            )

        assert runtime_state.dictionary_anchor_hint is None


# ---------------------------------------------------------------------------
# 4. dictionary_anchor_hint flows to prompt payload
# ---------------------------------------------------------------------------


class TestDictionaryAnchorHintInPayload:
    """Verify dictionary_anchor_hint is included in the prompt payload."""

    def _make_contract(self, *, dictionary_anchor_hint: str | None = None):
        from app.services.reader_ask.runtime_contract import ReaderAskAnswerRuntimeInput
        from app.schemas.reader_ask import ReaderAskPageIdentity

        record = MagicMock()
        record.record_id = uuid4()
        record.title = "Test"
        record.workflow_version = "1"
        record.schema_version = "1"

        dict_anchor = ReaderAskAnchorRef(
            anchor_type="dictionary_entry", label="dict", dict_entry_id=1
        )

        return ReaderAskAnswerRuntimeInput(
            thread={"id": "t-1", "record_id": "r-1", "title": "Test"},
            record=record,
            user_message="这个词什么意思",
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
            anchors=[dict_anchor],
            resolved_intent="vocabulary",
            resolved_intent_label="Vocabulary",
            entry_action="ask_about_this",
            submission_mode="chat",
            cross_record_context_allowed=False,
            resolved_context_input=None,
            quick_action_annotation=None,
            reference_resolution=None,
            planning_snapshot=None,
            max_history_messages=10,
            max_message_text=800,
            dictionary_anchor_hint=dictionary_anchor_hint,
        )

    def test_dictionary_anchor_hint_in_payload(self) -> None:
        from app.services.reader_ask.runtime_contract import build_prompt_payload

        contract = self._make_contract(
            dictionary_anchor_hint="用户查询了词典条目。请基于当前文章语境回答。"
        )
        payload = build_prompt_payload(contract)
        assert payload["dictionary_anchor_hint"] is not None
        assert "词典" in payload["dictionary_anchor_hint"]

    def test_dictionary_anchor_hint_none_in_payload(self) -> None:
        from app.services.reader_ask.runtime_contract import build_prompt_payload

        contract = self._make_contract(dictionary_anchor_hint=None)
        payload = build_prompt_payload(contract)
        assert payload["dictionary_anchor_hint"] is None

    def test_anchor_payload_includes_dict_entry_id(self) -> None:
        """P1 fix: dictionary anchor key fields must appear in anchor payload."""
        from app.services.reader_ask.runtime_contract import build_prompt_payload

        dict_anchor = ReaderAskAnchorRef(
            anchor_type="dictionary_entry",
            label="ephemeral",
            dict_entry_id=42,
            query="ephemeral",
            payload_json={"pos": "adj", "def": "lasting for a very short time"},
        )
        contract = self._make_contract_with_anchor(dict_anchor)
        payload = build_prompt_payload(contract)
        anchors = payload["canonical_context"]["anchors"]
        assert len(anchors) == 1
        a = anchors[0]
        assert a["anchor_type"] == "dictionary_entry"
        assert a["dict_entry_id"] == 42
        assert a["query"] == "ephemeral"
        assert a["payload_json"] == {"pos": "adj", "def": "lasting for a very short time"}

    def test_anchor_payload_dict_fields_none_for_non_dict(self) -> None:
        """Non-dictionary anchors should have None for dict-specific fields."""
        from app.services.reader_ask.runtime_contract import build_prompt_payload

        sentence_anchor = ReaderAskAnchorRef(
            anchor_type="sentence", label="s1", sentence_id="sen_1"
        )
        contract = self._make_contract_with_anchor(sentence_anchor)
        payload = build_prompt_payload(contract)
        a = payload["canonical_context"]["anchors"][0]
        assert a["dict_entry_id"] is None
        assert a["query"] is None
        assert a["payload_json"] is None

    def _make_contract_with_anchor(self, anchor: ReaderAskAnchorRef):
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
            user_message="这个词什么意思",
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
            anchors=[anchor],
            resolved_intent="vocabulary",
            resolved_intent_label="Vocabulary",
            entry_action="ask_about_this",
            submission_mode="chat",
            cross_record_context_allowed=False,
            resolved_context_input=None,
            quick_action_annotation=None,
            reference_resolution=None,
            planning_snapshot=None,
            max_history_messages=10,
            max_message_text=800,
            dictionary_anchor_hint=None,
        )


# ---------------------------------------------------------------------------
# 5. Long history now returns agent_loop_first (Round 12)


class TestLongHistoryRoutesToAgentLoopFirst:
    """Verify that long history no longer triggers planner_first (Round 12)."""

    def test_long_history_returns_agent_loop_first(self) -> None:
        history = [{"role": "user", "content_md": f"msg {i}"} for i in range(11)]
        route = planner_route_policy.resolve_planner_route(
            entry_action="ask_about_this",
            history_messages=history,
            attachments=[],
            anchors=[],
            cross_record_toggle=False,
            latest_user_message="继续",
        )
        assert route == "agent_loop_first"

    def test_short_history_returns_agent_loop_first(self) -> None:
        history = [{"role": "user", "content_md": f"msg {i}"} for i in range(5)]
        route = planner_route_policy.resolve_planner_route(
            entry_action="ask_about_this",
            history_messages=history,
            attachments=[],
            anchors=[],
            cross_record_toggle=False,
            latest_user_message="继续",
        )
        assert route == "agent_loop_first"

    def test_empty_history_returns_agent_loop_first(self) -> None:
        route = planner_route_policy.resolve_planner_route(
            entry_action="ask_about_this",
            history_messages=[],
            attachments=[],
            anchors=[],
            cross_record_toggle=False,
            latest_user_message="解释一下",
        )
        assert route == "agent_loop_first"


# ---------------------------------------------------------------------------
# 6. Old dictionary tools are not exposed to the agent
# ---------------------------------------------------------------------------


class TestOldDictionaryToolsNotExposed:
    """Verify that lookup_dictionary_entry and run_dictionary_ai_context_explain
    are NOT in the agent-callable tool set."""

    def test_lookup_dictionary_entry_not_agent_callable(self) -> None:
        from app.agents.reader_ask_tool_registry import agent_callable_tool_names

        names = agent_callable_tool_names()
        assert "lookup_dictionary_entry" not in names

    def test_run_dictionary_ai_context_explain_not_agent_callable(self) -> None:
        from app.agents.reader_ask_tool_registry import agent_callable_tool_names

        names = agent_callable_tool_names()
        assert "run_dictionary_ai_context_explain" not in names

    def test_lookup_dictionary_entry_not_in_registry(self) -> None:
        from app.agents.reader_ask_tool_registry import READER_ASK_TOOL_REGISTRY

        assert "lookup_dictionary_entry" not in READER_ASK_TOOL_REGISTRY

    def test_run_dictionary_ai_context_explain_not_in_registry(self) -> None:
        from app.agents.reader_ask_tool_registry import READER_ASK_TOOL_REGISTRY

        assert "run_dictionary_ai_context_explain" not in READER_ASK_TOOL_REGISTRY


# ---------------------------------------------------------------------------
# 7. Tool registry invariants still hold
# ---------------------------------------------------------------------------


class TestToolRegistryInvariantsRound11:
    """Verify that Round 11 changes don't break registry invariants."""

    def test_registry_invariants_hold(self) -> None:
        from app.agents.reader_ask_tool_registry import assert_registry_invariants

        assert_registry_invariants()

    def test_agent_callable_count_unchanged(self) -> None:
        from app.agents.reader_ask_tool_registry import agent_callable_tool_names

        names = agent_callable_tool_names()
        # Round 11 does not add or remove tools — same 9 agent-callable tools.
        assert len(names) == 9

    def test_runtime_state_has_dictionary_anchor_hint(self) -> None:
        state = ReaderAskRuntimeState()
        assert hasattr(state, "dictionary_anchor_hint")
        assert state.dictionary_anchor_hint is None
