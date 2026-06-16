"""Round 9 regression tests: cross-record toggle + keywords migration.

These tests verify:
1. Cross-record toggle + keywords now routes to agent_loop_first
2. has_cross_record_intent() is a public API
3. build_agent_loop_context sets cross_record_intent_hint
4. cross_record_intent_hint flows to prompt payload without becoming followup_hint
5. cross_record_context_allowed is correctly passed in agent_loop_first path
6. planner_first fallbacks are preserved (dictionary, external attachments, long history)
7. resolve_known_reference is still agent-callable
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
# 1. Cross-record toggle + keywords routes to agent_loop_first
# ---------------------------------------------------------------------------


class TestCrossRecordRoutesToAgentLoopFirst:
    """Verify that cross-record toggle + keywords no longer triggers planner_first."""

    def test_toggle_on_with_chinese_keyword(self) -> None:
        assert (
            planner_route_policy.resolve_planner_route(
                entry_action="ask_about_this",
                history_messages=[],
                attachments=[],
                anchors=[],
                cross_record_toggle=True,
                latest_user_message="和我之前那篇文章有什么不同？",
            )
            == "agent_loop_first"
        )

    def test_toggle_on_with_english_keyword(self) -> None:
        assert (
            planner_route_policy.resolve_planner_route(
                entry_action="ask_about_this",
                history_messages=[],
                attachments=[],
                anchors=[],
                cross_record_toggle=True,
                latest_user_message="compare with the previous article",
            )
            == "agent_loop_first"
        )

    def test_toggle_on_with_lingyi_keyword(self) -> None:
        assert (
            planner_route_policy.resolve_planner_route(
                entry_action="ask_about_this",
                history_messages=[],
                attachments=[],
                anchors=[],
                cross_record_toggle=True,
                latest_user_message="另一篇讲了什么",
            )
            == "agent_loop_first"
        )

    def test_toggle_off_with_keywords_still_agent_loop_first(self) -> None:
        """Toggle off: agent handles via resolve_known_reference tool."""
        assert (
            planner_route_policy.resolve_planner_route(
                entry_action="ask_about_this",
                history_messages=[],
                attachments=[],
                anchors=[],
                cross_record_toggle=False,
                latest_user_message="和我之前那篇文章有什么不同？",
            )
            == "agent_loop_first"
        )

    def test_toggle_on_without_keywords_agent_loop_first(self) -> None:
        """Toggle on but no keywords: no cross-record intent detected."""
        assert (
            planner_route_policy.resolve_planner_route(
                entry_action="ask_about_this",
                history_messages=[],
                attachments=[],
                anchors=[],
                cross_record_toggle=True,
                latest_user_message="这篇文章的主题是什么",
            )
            == "agent_loop_first"
        )


# ---------------------------------------------------------------------------
# 2. has_cross_record_intent is public API
# ---------------------------------------------------------------------------


class TestHasCrossRecordIntentPublic:
    """Verify has_cross_record_intent is a public, callable API."""

    def test_function_is_callable(self) -> None:
        assert callable(planner_route_policy.has_cross_record_intent)

    def test_returns_true_for_toggle_on_with_keywords(self) -> None:
        assert planner_route_policy.has_cross_record_intent(True, "和我之前那篇") is True

    def test_returns_false_for_toggle_off(self) -> None:
        assert planner_route_policy.has_cross_record_intent(False, "和我之前那篇") is False

    def test_returns_false_for_toggle_on_without_keywords(self) -> None:
        assert planner_route_policy.has_cross_record_intent(True, "这篇文章的主题") is False

    def test_returns_false_for_empty_string(self) -> None:
        assert planner_route_policy.has_cross_record_intent(True, "") is False


# ---------------------------------------------------------------------------
# 3. build_agent_loop_context sets cross_record_intent_hint
# ---------------------------------------------------------------------------


class TestCrossRecordIntentHint:
    """Verify that build_agent_loop_context sets the hint on runtime_state."""

    def _make_record(self) -> MagicMock:
        record = MagicMock()
        record.record_id = uuid4()
        record.title = "Test Record"
        record.render_scene = {}
        record.page_state_json = {}
        return record

    def test_hint_set_when_cross_record_intent(self) -> None:
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
                latest_user_message="和我之前那篇文章有什么不同？",
                cross_record_toggle=True,
            )

        assert runtime_state.cross_record_intent_hint is not None
        assert "跨文章意图" in runtime_state.cross_record_intent_hint
        assert "resolve_known_reference" in runtime_state.cross_record_intent_hint

    def test_hint_not_set_when_toggle_off(self) -> None:
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
                latest_user_message="和我之前那篇文章有什么不同？",
                cross_record_toggle=False,
            )

        assert runtime_state.cross_record_intent_hint is None

    def test_hint_not_set_when_no_keywords(self) -> None:
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
                cross_record_toggle=True,
            )

        assert runtime_state.cross_record_intent_hint is None


# ---------------------------------------------------------------------------
# 4. cross_record_intent_hint flows to prompt payload
# ---------------------------------------------------------------------------


class TestCrossRecordIntentHintInPayload:
    """Verify cross-record hint is included in the prompt payload separately."""

    def _make_contract(
        self,
        *,
        followup_hint: str | None = None,
        cross_record_intent_hint: str | None = None,
        planning_snapshot=None,
    ):
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
            user_message="和另一篇有什么不同",
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
            cross_record_context_allowed=True,
            resolved_context_input=None,
            quick_action_annotation=None,
            reference_resolution=None,
            planning_snapshot=planning_snapshot,
            max_history_messages=10,
            max_message_text=800,
            followup_hint=followup_hint,
            cross_record_intent_hint=cross_record_intent_hint,
        )

    def test_cross_record_hint_in_payload(self) -> None:
        from app.services.reader_ask.runtime_contract import build_prompt_payload

        contract = self._make_contract(
            cross_record_intent_hint="请优先调用 resolve_known_reference(query, top_k=5) 查找相关文章"
        )
        payload = build_prompt_payload(contract)
        assert payload["cross_record_intent_hint"] is not None
        assert "resolve_known_reference" in payload["cross_record_intent_hint"]
        assert payload["followup_hint"] is None

    def test_cross_record_hint_does_not_overwrite_followup_hint(self) -> None:
        from app.services.reader_ask.runtime_contract import build_prompt_payload

        contract = self._make_contract(
            followup_hint="请用户选中具体句子",
            cross_record_intent_hint="请优先调用 resolve_known_reference(query, top_k=5) 查找相关文章",
        )
        payload = build_prompt_payload(contract)
        assert payload["followup_hint"] == "请用户选中具体句子"
        assert "resolve_known_reference" in payload["cross_record_intent_hint"]

    def test_cross_record_context_allowed_in_payload(self) -> None:
        from app.services.reader_ask.runtime_contract import build_prompt_payload

        contract = self._make_contract(followup_hint=None)
        payload = build_prompt_payload(contract)
        assert payload["cross_record_context_allowed"] is True
        assert payload["tooling_contract"]["cross_record_context_requires_explicit_intent"] is True


# ---------------------------------------------------------------------------
# 5. cross_record_context_allowed in agent_loop_first path
# ---------------------------------------------------------------------------


class TestCrossRecordContextAllowedInAgentLoopFirst:
    """Verify that cross_record_context_allowed is correctly passed when
    the agent_loop_first path is used (planning_snapshot=None)."""

    def test_payload_reflects_cross_record_allowed(self) -> None:
        from app.services.reader_ask.runtime_contract import build_prompt_payload, ReaderAskAnswerRuntimeInput
        from app.schemas.reader_ask import ReaderAskPageIdentity

        record = MagicMock()
        record.record_id = uuid4()
        record.title = "Test"
        record.workflow_version = "1"
        record.schema_version = "1"

        contract = ReaderAskAnswerRuntimeInput(
            thread={"id": "t-1", "record_id": "r-1", "title": "Test"},
            record=record,
            user_message="和另一篇有什么不同",
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
            cross_record_context_allowed=True,
            resolved_context_input=None,
            quick_action_annotation=None,
            reference_resolution=None,
            planning_snapshot=None,
            max_history_messages=10,
            max_message_text=800,
        )
        payload = build_prompt_payload(contract)
        # When planning_snapshot is None, cross_record_context_allowed should
        # come from the contract, not be hardcoded to False.
        assert payload["cross_record_context_allowed"] is True
        assert payload["tooling_contract"]["cross_record_context_requires_explicit_intent"] is True


# ---------------------------------------------------------------------------
# 6. planner_first fallbacks preserved
# ---------------------------------------------------------------------------


class TestPlannerFirstFallbacksPreserved:
    """Verify that the remaining planner_first fallbacks are still intact."""

    def test_external_attachment_agent_loop_first(self) -> None:
        # Round 10: external attachments no longer trigger planner_first
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
        assert route == "agent_loop_first"

    def test_dictionary_anchor_routes_agent_loop_first(self) -> None:
        """Round 11: dictionary anchor no longer triggers planner_first."""
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
        assert route == "agent_loop_first"

    def test_dictionary_attachment_routes_agent_loop_first(self) -> None:
        """Round 11: dictionary attachment no longer triggers planner_first."""
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
        assert route == "agent_loop_first"

    def test_long_history_routes_agent_loop_first(self) -> None:
        # Round 12: long history no longer triggers planner_first
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


# ---------------------------------------------------------------------------
# 7. resolve_known_reference is still agent-callable
# ---------------------------------------------------------------------------


class TestResolveKnownReferenceStillCallable:
    """Verify resolve_known_reference remains in the agent-callable tool set."""

    def test_tool_in_registry(self) -> None:
        from app.agents.reader_ask_tool_registry import READER_ASK_TOOL_REGISTRY

        assert "resolve_known_reference" in READER_ASK_TOOL_REGISTRY

    def test_tool_is_agent_callable(self) -> None:
        from app.agents.reader_ask_tool_registry import agent_callable_tool_names

        assert "resolve_known_reference" in agent_callable_tool_names()

    def test_tool_not_reserved(self) -> None:
        from app.agents.reader_ask_tool_registry import RESERVED_TOOL_NAMES

        assert "resolve_known_reference" not in RESERVED_TOOL_NAMES
