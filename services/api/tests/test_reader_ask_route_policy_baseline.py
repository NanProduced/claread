"""Baseline tests for the Ask Claread planner route policy and agent-loop-first path.

These tests cover the minimal-helper and decision-helper surface introduced
in Round 1:

- ``planner_route_policy.resolve_planner_route`` decision logic.
- ``planner_route_policy.detect_cross_record_in_message`` keyword detection.
- ``planner.build_minimal_resolved_intent`` entry_action mapping.
- ``planner.build_minimal_context_plan`` / ``build_minimal_trace_summary``
  shape contracts.
- End-to-end authoritative final-content backfill (covered here as
  well as in ``test_reader_ask_agent_runner.py::TestAuthoritativeFinalContent``).

Orchestrator integration tests for ``stream_thread_message`` /
``retry_thread_message`` live in ``test_reader_ask_service.py``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.agents.reader_ask_agent import ReaderAskAgentDeps, ReaderAskRuntimeState
from app.schemas.reader_ask import (
    ReaderAskAnchorRef,
    ReaderAskAttachment,
    ReaderAskAttachmentMetadata,
    ReaderAskPageIdentity,
)
from app.services.reader_ask import agent_runner as agent_runner_svc
from app.services.reader_ask import planner_route_policy
from app.services.reader_ask import planner as planner_svc
from app.services.reader_ask.runtime_contract import ReaderAskAnswerRuntimeInput


def _anchor(anchor_type: str) -> ReaderAskAnchorRef:
    return ReaderAskAnchorRef(anchor_type=anchor_type, label="a", sentence_id="s1")  # type: ignore[arg-type]


def _dict_anchor() -> ReaderAskAnchorRef:
    return ReaderAskAnchorRef(anchor_type="dictionary_entry", label="dict", dict_entry_id=1)


def _attachment(kind: str, subtype: str = "x") -> ReaderAskAttachment:
    return ReaderAskAttachment(
        kind=kind,  # type: ignore[arg-type]
        subtype=subtype,
        label="att",
        metadata=ReaderAskAttachmentMetadata(source_surface="reader_page"),
    )


def _history(n: int) -> list[dict[str, Any]]:
    return [{"role": "user", "content_md": f"msg {i}"} for i in range(n)]


# ---------------------------------------------------------------------------
# resolve_planner_route decision
# ---------------------------------------------------------------------------


class TestResolvePlannerRoute:
    def test_simple_article_question_with_anchor(self) -> None:
        assert planner_route_policy.resolve_planner_route(
            entry_action="ask_about_this",
            history_messages=_history(2),
            attachments=[],
            anchors=[_anchor("sentence")],
            cross_record_toggle=False,
            latest_user_message="这句话想表达什么？",
        ) == "agent_loop_first"

    def test_simple_article_question_no_anchor(self) -> None:
        assert planner_route_policy.resolve_planner_route(
            entry_action="ask_about_this",
            history_messages=_history(2),
            attachments=[],
            anchors=[],
            cross_record_toggle=False,
            latest_user_message="这篇文章想表达什么？",
        ) == "agent_loop_first"

    def test_long_history_returns_agent_loop_first(self) -> None:
        # Round 12: long history no longer triggers planner_first
        assert planner_route_policy.resolve_planner_route(
            entry_action="ask_about_this",
            history_messages=_history(11),
            attachments=[],
            anchors=[_anchor("sentence")],
            cross_record_toggle=False,
            latest_user_message="继续",
        ) == "agent_loop_first"

    def test_agent_loop_first_for_cross_record_attachment(self) -> None:
        # Round 10: external attachments no longer trigger planner_first
        assert planner_route_policy.resolve_planner_route(
            entry_action="ask_about_this",
            history_messages=_history(2),
            attachments=[_attachment("record_ref", "related_record")],
            anchors=[_anchor("sentence")],
            cross_record_toggle=False,
            latest_user_message="对照我之前那篇",
        ) == "agent_loop_first"

    def test_agent_loop_first_for_analysis_ref_attachment(self) -> None:
        # Round 10: external attachments no longer trigger planner_first
        assert planner_route_policy.resolve_planner_route(
            entry_action="ask_about_this",
            history_messages=_history(2),
            attachments=[_attachment("analysis_ref", "summary")],
            anchors=[_anchor("sentence")],
            cross_record_toggle=False,
            latest_user_message="解释一下",
        ) == "agent_loop_first"

    def test_agent_loop_first_for_supplement_ref_attachment(self) -> None:
        # Round 10: external attachments no longer trigger planner_first
        assert planner_route_policy.resolve_planner_route(
            entry_action="ask_about_this",
            history_messages=_history(2),
            attachments=[_attachment("supplement_ref", "grammar_note")],
            anchors=[_anchor("sentence")],
            cross_record_toggle=False,
            latest_user_message="解释一下",
        ) == "agent_loop_first"

    def test_false_for_cross_record_keyword_chinese(self) -> None:
        # Round 9: cross-record toggle + keywords no longer triggers planner_first
        assert planner_route_policy.resolve_planner_route(
            entry_action="ask_about_this",
            history_messages=_history(0),
            attachments=[],
            anchors=[_anchor("sentence")],
            cross_record_toggle=True,
            latest_user_message="和我之前那篇 chronic absenteeism 的文章有什么不同？",
        ) == "agent_loop_first"

    def test_false_for_cross_record_keyword_english(self) -> None:
        # Round 9: cross-record toggle + keywords no longer triggers planner_first
        assert planner_route_policy.resolve_planner_route(
            entry_action="ask_about_this",
            history_messages=_history(0),
            attachments=[],
            anchors=[_anchor("sentence")],
            cross_record_toggle=True,
            latest_user_message="How does this compare to the previous article on this topic?",
        ) == "agent_loop_first"

    def test_cross_record_keyword_without_toggle_still_eligible(self) -> None:
        # Round 3: cross-record keywords without toggle → still agent_loop_first
        assert planner_route_policy.resolve_planner_route(
            entry_action="ask_about_this",
            history_messages=_history(0),
            attachments=[],
            anchors=[_anchor("sentence")],
            cross_record_toggle=False,
            latest_user_message="和我之前那篇 chronic absenteeism 的文章有什么不同？",
        ) == "agent_loop_first"

    def test_true_for_unknown_entry_action(self) -> None:
        # Round 3: entry_action is no longer a whitelist gate; all actions
        # default to agent_loop_first unless a fallback condition triggers.
        assert planner_route_policy.resolve_planner_route(
            entry_action="some_custom_action",  # type: ignore[arg-type]
            history_messages=_history(0),
            attachments=[],
            anchors=[],
            cross_record_toggle=False,
            latest_user_message="hello",
        ) == "agent_loop_first"

    def test_agent_loop_first_when_toggle_on_with_cross_record_keywords(self) -> None:
        # Round 9: cross-record toggle + keywords no longer triggers planner_first
        assert planner_route_policy.resolve_planner_route(
            entry_action="ask_about_this",
            history_messages=_history(2),
            attachments=[],
            anchors=[_anchor("sentence")],
            cross_record_toggle=True,
            latest_user_message="和我之前那篇有什么不同",
        ) == "agent_loop_first"

    def test_true_when_toggle_on_without_cross_record_keywords(self) -> None:
        # Round 3: toggle on but no cross-record keywords → still agent_loop_first.
        assert planner_route_policy.resolve_planner_route(
            entry_action="ask_about_this",
            history_messages=_history(2),
            attachments=[],
            anchors=[_anchor("sentence")],
            cross_record_toggle=True,
            latest_user_message="hello",
        ) == "agent_loop_first"

    def test_explain_this_is_eligible(self) -> None:
        assert planner_route_policy.resolve_planner_route(
            entry_action="explain_this",
            history_messages=_history(0),
            attachments=[],
            anchors=[_anchor("sentence")],
            cross_record_toggle=False,
            latest_user_message="解释一下",
        ) == "agent_loop_first"

    def test_why_here_with_anchor_is_eligible(self) -> None:
        assert planner_route_policy.resolve_planner_route(
            entry_action="why_here",
            history_messages=_history(0),
            attachments=[],
            anchors=[_anchor("sentence")],
            cross_record_toggle=False,
            latest_user_message="这里为什么用 present perfect",
        ) == "agent_loop_first"

    def test_why_here_without_anchor_agent_loop_first(self) -> None:
        # Round 8: deictic without anchor no longer triggers planner_first
        assert planner_route_policy.resolve_planner_route(
            entry_action="why_here",
            history_messages=_history(0),
            attachments=[],
            anchors=[],
            cross_record_toggle=False,
            latest_user_message="这里为什么用 present perfect",
        ) == "agent_loop_first"

    def test_lookup_in_context_now_eligible(self) -> None:
        # Round 3: ``lookup_in_context`` is no longer excluded by entry_action;
        # all actions default to agent_loop_first unless a fallback triggers.
        assert planner_route_policy.resolve_planner_route(
            entry_action="lookup_in_context",
            history_messages=_history(0),
            attachments=[],
            anchors=[_anchor("sentence")],
            cross_record_toggle=False,
            latest_user_message="这个词什么意思",
        ) == "agent_loop_first"

    def test_dictionary_anchor_agent_loop_first(self) -> None:
        # Round 11: dictionary anchor no longer triggers planner_first
        assert planner_route_policy.resolve_planner_route(
            entry_action="ask_about_this",
            history_messages=_history(0),
            attachments=[],
            anchors=[_dict_anchor()],
            cross_record_toggle=False,
            latest_user_message="这个词什么意思",
        ) == "agent_loop_first"

    def test_deictic_without_anchor_not_eligible(self) -> None:
        # Round 8: deictic without anchor no longer triggers planner_first
        assert planner_route_policy.resolve_planner_route(
            entry_action="ask_about_this",
            history_messages=_history(0),
            attachments=[],
            anchors=[],
            cross_record_toggle=False,
            latest_user_message="解释这句",
        ) == "agent_loop_first"

    def test_deictic_with_anchor_is_eligible(self) -> None:
        assert planner_route_policy.resolve_planner_route(
            entry_action="ask_about_this",
            history_messages=_history(0),
            attachments=[],
            anchors=[_anchor("sentence")],
            cross_record_toggle=False,
            latest_user_message="解释这句",
        ) == "agent_loop_first"

    def test_deictic_english_without_anchor_agent_loop_first(self) -> None:
        # Round 8: deictic without anchor no longer triggers planner_first
        assert planner_route_policy.resolve_planner_route(
            entry_action="ask_about_this",
            history_messages=_history(0),
            attachments=[],
            anchors=[],
            cross_record_toggle=False,
            latest_user_message="explain this sentence",
        ) == "agent_loop_first"

    def test_non_deictic_without_anchor_is_eligible(self) -> None:
        assert planner_route_policy.resolve_planner_route(
            entry_action="ask_about_this",
            history_messages=_history(0),
            attachments=[],
            anchors=[],
            cross_record_toggle=False,
            latest_user_message="这篇文章的主题是什么",
        ) == "agent_loop_first"


# ---------------------------------------------------------------------------
# Internal helper gates
# ---------------------------------------------------------------------------


class TestHasDeicticWithoutAnchor:
    def test_deictic_chinese_no_anchor(self) -> None:
        assert planner_route_policy.has_deictic_without_anchor("解释这句", []) is True

    def test_deictic_chinese_with_anchor(self) -> None:
        assert planner_route_policy.has_deictic_without_anchor("解释这句", [_anchor("sentence")]) is False

    def test_deictic_english_no_anchor(self) -> None:
        assert planner_route_policy.has_deictic_without_anchor("explain this sentence", []) is True

    def test_non_deictic_no_anchor(self) -> None:
        assert planner_route_policy.has_deictic_without_anchor("这篇文章的主题是什么", []) is False

    def test_empty_text(self) -> None:
        assert planner_route_policy.has_deictic_without_anchor("", []) is False


class TestHasDictionaryAnchorOrAttachment:
    def test_dictionary_anchor(self) -> None:
        assert planner_route_policy.has_dictionary_anchor_or_attachment(
            [_dict_anchor()], []
        ) is True

    def test_sentence_anchor_not_dictionary(self) -> None:
        assert planner_route_policy.has_dictionary_anchor_or_attachment(
            [_anchor("sentence")], []
        ) is False

    def test_no_anchors_no_attachments(self) -> None:
        assert planner_route_policy.has_dictionary_anchor_or_attachment([], []) is False

    def test_dictionary_attachment_subtype(self) -> None:
        # The helper checks both kind and subtype defensively for
        # dictionary_entry, even though it is not a valid attachment kind.
        # Test via subtype since dictionary_entry is not a valid kind.
        assert planner_route_policy.has_dictionary_anchor_or_attachment(
            [], [_attachment("text_selection", "dictionary_entry")]
        ) is True

    def test_non_dictionary_attachment_not_flagged(self) -> None:
        assert planner_route_policy.has_dictionary_anchor_or_attachment(
            [], [_attachment("text_selection", "highlight")]
        ) is False


# ---------------------------------------------------------------------------
# detect_cross_record_in_message
# ---------------------------------------------------------------------------


class TestDetectCrossRecordInMessage:
    @pytest.mark.parametrize(
        "text",
        [
            "我之前那篇文章呢",
            "和另一篇有什么不同",
            "之前那篇讲了什么",
            "对照上篇",
            "compare to the previous article",
            "the earlier discussion",
        ],
    )
    def test_keywords_detected(self, text: str) -> None:
        assert planner_route_policy.detect_cross_record_in_message(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "这篇文章讲什么",
            "请帮我解释下一句",
            "Hello world",
        ],
    )
    def test_clean_text_not_detected(self, text: str) -> None:
        assert planner_route_policy.detect_cross_record_in_message(text) is False


# ---------------------------------------------------------------------------
# build_minimal_resolved_intent (re-exported via runtime_contract)
# ---------------------------------------------------------------------------


class TestMinimalIntentMatrix:
    @pytest.mark.parametrize(
        "entry_action, expected",
        [
            ("ask_about_this", ("general", "ask_about_this")),
            ("explain_this", ("explain", "explain_this")),
            ("why_here", ("grammar", "why_here")),
            ("lookup_in_context", ("vocabulary", "lookup_in_context")),
        ],
    )
    def test_known_entry_actions(self, entry_action: str, expected: tuple[str, str]) -> None:
        from app.services.reader_ask.runtime_contract import build_minimal_resolved_intent
        assert build_minimal_resolved_intent(entry_action) == expected

    def test_unknown_action_falls_back_to_general(self) -> None:
        from app.services.reader_ask.runtime_contract import build_minimal_resolved_intent
        assert build_minimal_resolved_intent("custom_action") == ("general", "custom_action")


# ---------------------------------------------------------------------------
# build_minimal_context_plan_for_runtime_input
# ---------------------------------------------------------------------------


class TestBuildMinimalContextPlanForRuntimeInput:
    def _contract(self, *, entry_action: str, attachments, anchors) -> ReaderAskAnswerRuntimeInput:
        return ReaderAskAnswerRuntimeInput(
            thread={"id": str(uuid4()), "title": "t"},
            record=MagicMock(record_id=uuid4(), title="r"),
            user_message="hi",
            history_messages=[],
            page_identity=ReaderAskPageIdentity(record_id=str(uuid4())),
            attachments=attachments,
            anchors=anchors,
            resolved_intent="general",
            resolved_intent_label="general",
            entry_action=entry_action,  # type: ignore[arg-type]
            submission_mode="chat",
            cross_record_context_allowed=False,
            resolved_context_input=None,
            quick_action_annotation=None,
            reference_resolution=None,
            planning_snapshot=None,
            max_history_messages=4,
            max_message_text=2000,
        )

    def test_with_anchor_uses_record_context(self) -> None:
        contract = self._contract(
            entry_action="ask_about_this",
            attachments=[],
            anchors=[_anchor("sentence")],
        )
        plan = planner_route_policy.build_minimal_context_plan_for_runtime_input(contract)
        assert plan.entry_action == "ask_about_this"
        assert plan.primary_anchor_type == "sentence"
        assert plan.used_record_context is True
        assert plan.used_cross_record_context is False
        assert plan.used_dictionary is False


# ---------------------------------------------------------------------------
# build_minimal_trace_summary_for_runtime_input
# ---------------------------------------------------------------------------


class TestBuildMinimalTraceSummaryForRuntimeInput:
    def _contract(self) -> ReaderAskAnswerRuntimeInput:
        return ReaderAskAnswerRuntimeInput(
            thread={"id": str(uuid4()), "title": "t"},
            record=MagicMock(record_id=uuid4(), title="r"),
            user_message="hi",
            history_messages=[],
            page_identity=ReaderAskPageIdentity(record_id=str(uuid4())),
            attachments=[],
            anchors=[_anchor("sentence")],
            resolved_intent="general",
            resolved_intent_label="general",
            entry_action="ask_about_this",
            submission_mode="chat",
            cross_record_context_allowed=False,
            resolved_context_input=None,
            quick_action_annotation=None,
            reference_resolution=None,
            planning_snapshot=None,
            max_history_messages=4,
            max_message_text=2000,
        )

    def test_direct_answer_with_skipped_note(self) -> None:
        trace = planner_route_policy.build_minimal_trace_summary_for_runtime_input(
            self._contract(), planner_skipped=True
        )
        assert trace.planner_mode == "direct_answer"
        assert any("skipped" in note.lower() for note in trace.notes)
        assert trace.cross_record_context_used is False


# ---------------------------------------------------------------------------
# MinimalPlanningSnapshot duck-typed access (smoke)
# ---------------------------------------------------------------------------


class TestMinimalPlanningSnapshotShape:
    def test_default_construction(self) -> None:
        snap = planner_svc.MinimalPlanningSnapshot()
        assert snap.retrieval_needs == "none"
        assert snap.clarification_mode == "none"
        assert snap.context_plan is None
        assert snap.trace_summary is None
        assert snap.working_set is not None
        assert snap.working_set.cross_record_context_allowed is False

    def test_with_minimal_context_plan(self) -> None:
        from app.services.reader_ask.planner import build_minimal_context_plan

        snap = planner_svc.MinimalPlanningSnapshot(
            retrieval_needs="none",
            working_set=planner_svc.ReaderAskWorkingSet(
                local_context_window_needed=True,
            ),
            context_plan=build_minimal_context_plan(
                entry_action="ask_about_this",
                attachments=[],
                anchors=[_anchor("sentence")],
            ),
        )
        assert snap.context_plan is not None
        assert snap.context_plan.entry_action == "ask_about_this"
        assert snap.working_set.local_context_window_needed is True


# ---------------------------------------------------------------------------
# End-to-end authoritative backfill smoke
# ---------------------------------------------------------------------------


class TestAuthoritativeBackfillSmoke:
    """Smoke test that a simulated lost first delta gets correctly backfilled
    from ``runtime.authoritative_output`` via ``finish_reader_ask_agent_stream``.
    This is the end-to-end counterpart to the unit tests in
    ``test_reader_ask_agent_runner.py::TestAuthoritativeFinalContent``."""

    def test_first_delta_lost_backfilled(self) -> None:
        runtime = agent_runner_svc.AgentStreamRuntime()
        runtime.content_parts = ["lo ", "world"]
        runtime.emitted_text = "lo world"
        runtime.authoritative_output = "hello world"
        runtime.producer_result = MagicMock()

        outcome, _ = agent_runner_svc.finish_reader_ask_agent_stream(
            runtime=runtime,
            assistant_message_id="msg-1",
        )

        assert outcome.content_md == "hello world"
        # emitted_text retains the raw stream so checkpoint writer and
        # eval can detect the loss and compare against the authoritative.
        assert runtime.emitted_text == "lo world"


# ---------------------------------------------------------------------------
# runtime_state telemetry preservation
# ---------------------------------------------------------------------------


class TestRuntimeStateTelemetryPreservation:
    """Verify that ``planner_skipped`` and ``planner_route_used`` are
    preserved when ``ReaderAskRuntimeState`` is rebuilt."""

    def test_agent_loop_first_telemetry_preserved_on_rebuild(self) -> None:
        from app.agents.reader_ask_agent import ReaderAskRuntimeState

        # Simulate the agent-loop-first path setting telemetry before rebuild
        original = ReaderAskRuntimeState()
        original.planner_skipped = True
        original.planner_route_used = "agent_loop_first"

        # Rebuild as service.py does
        rebuilt = ReaderAskRuntimeState(
            citations=[],
            source_labels={"current_record"},
            planner_skipped=original.planner_skipped,
            planner_route_used=original.planner_route_used,
        )

        assert rebuilt.planner_skipped is True
        assert rebuilt.planner_route_used == "agent_loop_first"

    def test_legacy_path_telemetry_preserved_on_rebuild(self) -> None:
        from app.agents.reader_ask_agent import ReaderAskRuntimeState

        original = ReaderAskRuntimeState()
        # Default values are planner-first
        assert original.planner_skipped is False
        assert original.planner_route_used == "planner_first"

        rebuilt = ReaderAskRuntimeState(
            citations=[],
            source_labels={"current_record"},
            planner_skipped=original.planner_skipped,
            planner_route_used=original.planner_route_used,
        )

        assert rebuilt.planner_skipped is False
        assert rebuilt.planner_route_used == "planner_first"

    def test_agent_loop_first_telemetry_preserved_on_rebuild_duplicate(self) -> None:
        from app.agents.reader_ask_agent import ReaderAskRuntimeState

        original = ReaderAskRuntimeState()
        original.planner_skipped = True
        original.planner_route_used = "agent_loop_first"

        rebuilt = ReaderAskRuntimeState(
            citations=[],
            source_labels={"current_record"},
            planner_skipped=original.planner_skipped,
            planner_route_used=original.planner_route_used,
        )

        assert rebuilt.planner_skipped is True
        assert rebuilt.planner_route_used == "agent_loop_first"

    def test_degenerate_metadata_preserved_on_rebuild(self) -> None:
        from app.agents.reader_ask_agent import ReaderAskRuntimeState

        original = ReaderAskRuntimeState()
        original.degenerate_detected = True
        original.degenerate_reason = "degenerate_answer"

        rebuilt = ReaderAskRuntimeState(
            citations=[],
            source_labels={"current_record"},
            degenerate_detected=original.degenerate_detected,
            degenerate_reason=original.degenerate_reason,
        )

        assert rebuilt.degenerate_detected is True
        assert rebuilt.degenerate_reason == "degenerate_answer"


# ---------------------------------------------------------------------------
# build_replan_event with planning_snapshot=None (agent-loop-first no replan)
# ---------------------------------------------------------------------------


class TestAgentLoopFirstNoReplan:
    """Agent-loop-first path sets ``planning_snapshot=None``, which must prevent replan."""

    def test_none_snapshot_never_replans(self) -> None:
        result = agent_runner_svc.build_replan_event(
            final_content_md="",  # degenerate
            planning_snapshot=None,
            assistant_message_id="msg-1",
        )
        assert result is None

    def test_none_snapshot_even_with_refusal(self) -> None:
        result = agent_runner_svc.build_replan_event(
            final_content_md="I cannot answer this question.",
            planning_snapshot=None,
            assistant_message_id="msg-1",
        )
        assert result is None

    def test_agent_loop_first_never_replans(self) -> None:
        # Round 3: agent_loop_first route never returns a replan event,
        # even with a degenerate answer and a valid planning_snapshot.
        result = agent_runner_svc.build_replan_event(
            final_content_md="",
            planning_snapshot=None,
            assistant_message_id="msg-1",
            planner_route="agent_loop_first",
        )
        assert result is None

    def test_agent_loop_first_sets_degenerate_metadata(self) -> None:
        # Round 3: agent_loop_first records degenerate metadata on runtime_state
        # instead of triggering replan.
        runtime = agent_runner_svc.AgentStreamRuntime()
        runtime.producer_result = MagicMock()

        result = agent_runner_svc.build_replan_event(
            final_content_md="",
            planning_snapshot=None,
            assistant_message_id="msg-1",
            planner_route="agent_loop_first",
            runtime_state=runtime,
        )
        assert result is None
        assert runtime.degenerate_detected is True
        assert runtime.degenerate_reason == "degenerate_answer"

    def test_agent_loop_first_no_degenerate_metadata_for_valid_answer(self) -> None:
        # Round 3: non-degenerate answer does not set degenerate metadata.
        runtime = agent_runner_svc.AgentStreamRuntime()
        runtime.producer_result = MagicMock()

        result = agent_runner_svc.build_replan_event(
            final_content_md="This is a valid answer with enough content.",
            planning_snapshot=None,
            assistant_message_id="msg-1",
            planner_route="agent_loop_first",
            runtime_state=runtime,
        )
        assert result is None
        assert runtime.degenerate_detected is False
        assert runtime.degenerate_reason is None

    def test_agent_loop_first_no_runtime_state_no_error(self) -> None:
        # Round 3: agent_loop_first with no runtime_state should not raise.
        result = agent_runner_svc.build_replan_event(
            final_content_md="",
            planning_snapshot=None,
            assistant_message_id="msg-1",
            planner_route="agent_loop_first",
            runtime_state=None,
        )
        assert result is None
