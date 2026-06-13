"""Round 1 baseline tests for the Ask Claread agent-loop fast path.

These tests cover the minimal-helper and decision-helper surface introduced
in Round 1:

- ``fast_path_runtime.should_use_fast_path`` decision logic.
- ``fast_path_runtime.detect_cross_record_in_message`` keyword detection.
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
from app.services.reader_ask import fast_path_runtime
from app.services.reader_ask import planner as planner_svc
from app.services.reader_ask.runtime_contract import ReaderAskAnswerRuntimeInput


def _anchor(anchor_type: str) -> ReaderAskAnchorRef:
    return ReaderAskAnchorRef(anchor_type=anchor_type, label="a", sentence_id="s1")  # type: ignore[arg-type]


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
# should_use_fast_path decision
# ---------------------------------------------------------------------------


class TestShouldUseFastPath:
    def test_simple_article_question(self) -> None:
        assert fast_path_runtime.should_use_fast_path(
            entry_action="ask_about_this",
            history_messages=_history(2),
            attachments=[],
            cross_record_toggle=False,
            latest_user_message="这篇文章想表达什么？",
        ) is True

    def test_false_for_long_history(self) -> None:
        assert fast_path_runtime.should_use_fast_path(
            entry_action="ask_about_this",
            history_messages=_history(5),
            attachments=[],
            cross_record_toggle=False,
            latest_user_message="继续",
        ) is False

    def test_false_for_cross_record_attachment(self) -> None:
        assert fast_path_runtime.should_use_fast_path(
            entry_action="ask_about_this",
            history_messages=_history(2),
            attachments=[_attachment("record_ref", "related_record")],
            cross_record_toggle=False,
            latest_user_message="对照我之前那篇",
        ) is False

    def test_false_for_cross_record_keyword_chinese(self) -> None:
        assert fast_path_runtime.should_use_fast_path(
            entry_action="ask_about_this",
            history_messages=_history(0),
            attachments=[],
            cross_record_toggle=False,
            latest_user_message="和我之前那篇 chronic absenteeism 的文章有什么不同？",
        ) is False

    def test_false_for_cross_record_keyword_english(self) -> None:
        assert fast_path_runtime.should_use_fast_path(
            entry_action="ask_about_this",
            history_messages=_history(0),
            attachments=[],
            cross_record_toggle=False,
            latest_user_message="How does this compare to the previous article on this topic?",
        ) is False

    def test_false_for_unknown_entry_action(self) -> None:
        assert fast_path_runtime.should_use_fast_path(
            entry_action="some_custom_action",  # type: ignore[arg-type]
            history_messages=_history(0),
            attachments=[],
            cross_record_toggle=False,
            latest_user_message="hello",
        ) is False

    def test_false_when_toggle_on(self) -> None:
        assert fast_path_runtime.should_use_fast_path(
            entry_action="ask_about_this",
            history_messages=_history(2),
            attachments=[],
            cross_record_toggle=True,
            latest_user_message="hello",
        ) is False

    def test_explain_this_is_eligible(self) -> None:
        assert fast_path_runtime.should_use_fast_path(
            entry_action="explain_this",
            history_messages=_history(0),
            attachments=[],
            cross_record_toggle=False,
            latest_user_message="解释一下",
        ) is True

    def test_why_here_is_eligible(self) -> None:
        assert fast_path_runtime.should_use_fast_path(
            entry_action="why_here",
            history_messages=_history(0),
            attachments=[],
            cross_record_toggle=False,
            latest_user_message="这里为什么用 present perfect",
        ) is True

    def test_lookup_in_context_not_eligible(self) -> None:
        # ``lookup_in_context`` is not in the fast-path set; it always
        # uses the legacy planner to handle dictionary lookups.
        assert fast_path_runtime.should_use_fast_path(
            entry_action="lookup_in_context",
            history_messages=_history(0),
            attachments=[],
            cross_record_toggle=False,
            latest_user_message="这个词什么意思",
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
        assert fast_path_runtime.detect_cross_record_in_message(text) is True

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
        assert fast_path_runtime.detect_cross_record_in_message(text) is False


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
        plan = fast_path_runtime.build_minimal_context_plan_for_runtime_input(contract)
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
        trace = fast_path_runtime.build_minimal_trace_summary_for_runtime_input(
            self._contract(), planner_skipped=True
        )
        assert trace.planner_mode == "direct_answer"
        assert any("skipped" in note.lower() for note in trace.notes)
        assert trace.cross_record_context_used is False


# ---------------------------------------------------------------------------
# FastPathPlanningSnapshot duck-typed access (smoke)
# ---------------------------------------------------------------------------


class TestFastPathPlanningSnapshotShape:
    def test_default_construction(self) -> None:
        snap = planner_svc.FastPathPlanningSnapshot()
        assert snap.retrieval_needs == "none"
        assert snap.clarification_mode == "none"
        assert snap.context_plan is None
        assert snap.trace_summary is None
        assert snap.working_set is not None
        assert snap.working_set.cross_record_context_allowed is False

    def test_with_minimal_context_plan(self) -> None:
        from app.services.reader_ask.planner import build_minimal_context_plan

        snap = planner_svc.FastPathPlanningSnapshot(
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
    from ``result.output`` via ``finish_reader_ask_agent_stream``. This is the
    end-to-end counterpart to the unit tests in
    ``test_reader_ask_agent_runner.py::TestAuthoritativeFinalContent``."""

    def test_first_delta_lost_backfilled(self) -> None:
        runtime = agent_runner_svc.AgentStreamRuntime()
        runtime.content_parts = ["lo ", "world"]
        runtime.emitted_text = "lo world"
        runtime.producer_result = MagicMock()
        runtime.producer_result.output = "hello world"

        outcome, _ = agent_runner_svc.finish_reader_ask_agent_stream(
            runtime=runtime,
            assistant_message_id="msg-1",
        )

        assert outcome.content_md == "hello world"
        # emitted_text retains the raw stream so checkpoint writer and
        # eval can detect the loss and compare against the authoritative.
        assert runtime.emitted_text == "lo world"
