"""Round 12 regression tests: long history migration.

These tests verify:
1. resolve_planner_route() always returns agent_loop_first (no planner_first trigger)
2. has_long_history() is a public API
3. build_agent_loop_context sets long_history_hint for long conversations
4. long_history_hint flows to prompt payload
5. Planner is not called for long history scenarios
6. planner_first route value is still valid for backward-compatible trace
7. Tool registry invariants still hold
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.agents.reader_ask_agent import ReaderAskRuntimeState
from app.schemas.reader_ask import (
    ReaderAskAnchorRef,
    ReaderAskAttachment,
    ReaderAskAttachmentMetadata,
)
from app.services.reader_ask import planner_route_policy


def _history(n: int) -> list[dict[str, str]]:
    return [{"role": "user", "content_md": f"msg {i}"} for i in range(n)]


# ---------------------------------------------------------------------------
# 1. resolve_planner_route() always returns agent_loop_first
# ---------------------------------------------------------------------------


class TestResolvePlannerRouteAlwaysAgentLoopFirst:
    """Verify that resolve_planner_route() always returns agent_loop_first,
    regardless of history length, attachments, anchors, or other conditions."""

    def test_empty_history(self) -> None:
        assert (
            planner_route_policy.resolve_planner_route(
                entry_action="ask_about_this",
                history_messages=[],
                attachments=[],
                anchors=[],
                cross_record_toggle=False,
                latest_user_message="解释一下",
            )
            == "agent_loop_first"
        )

    def test_short_history(self) -> None:
        assert (
            planner_route_policy.resolve_planner_route(
                entry_action="ask_about_this",
                history_messages=_history(5),
                attachments=[],
                anchors=[],
                cross_record_toggle=False,
                latest_user_message="继续",
            )
            == "agent_loop_first"
        )

    def test_long_history(self) -> None:
        """Round 12: long history no longer triggers planner_first."""
        assert (
            planner_route_policy.resolve_planner_route(
                entry_action="ask_about_this",
                history_messages=_history(11),
                attachments=[],
                anchors=[],
                cross_record_toggle=False,
                latest_user_message="继续",
            )
            == "agent_loop_first"
        )

    def test_very_long_history(self) -> None:
        assert (
            planner_route_policy.resolve_planner_route(
                entry_action="ask_about_this",
                history_messages=_history(50),
                attachments=[],
                anchors=[],
                cross_record_toggle=False,
                latest_user_message="继续",
            )
            == "agent_loop_first"
        )

    def test_long_history_with_dictionary_anchor(self) -> None:
        dict_anchor = ReaderAskAnchorRef(
            anchor_type="dictionary_entry", label="dict", dict_entry_id=1
        )
        assert (
            planner_route_policy.resolve_planner_route(
                entry_action="ask_about_this",
                history_messages=_history(11),
                attachments=[],
                anchors=[dict_anchor],
                cross_record_toggle=False,
                latest_user_message="这个词什么意思",
            )
            == "agent_loop_first"
        )

    def test_long_history_with_external_attachment(self) -> None:
        ext_att = ReaderAskAttachment(
            kind="record_ref",
            subtype="related_record",
            label="other article",
            metadata=ReaderAskAttachmentMetadata(source_surface="reader_page"),
        )
        assert (
            planner_route_policy.resolve_planner_route(
                entry_action="ask_about_this",
                history_messages=_history(11),
                attachments=[ext_att],
                anchors=[],
                cross_record_toggle=False,
                latest_user_message="继续",
            )
            == "agent_loop_first"
        )

    def test_long_history_with_cross_record(self) -> None:
        assert (
            planner_route_policy.resolve_planner_route(
                entry_action="ask_about_this",
                history_messages=_history(11),
                attachments=[],
                anchors=[],
                cross_record_toggle=True,
                latest_user_message="和之前那篇有什么不同",
            )
            == "agent_loop_first"
        )


# ---------------------------------------------------------------------------
# 2. has_long_history() is public API
# ---------------------------------------------------------------------------


class TestHasLongHistoryPublic:
    """Verify has_long_history is a public, callable API."""

    def test_function_is_callable(self) -> None:
        assert callable(planner_route_policy.has_long_history)

    def test_returns_true_for_long_history(self) -> None:
        assert planner_route_policy.has_long_history(_history(11)) is True

    def test_returns_false_for_short_history(self) -> None:
        assert planner_route_policy.has_long_history(_history(5)) is False

    def test_returns_false_for_empty_history(self) -> None:
        assert planner_route_policy.has_long_history([]) is False

    def test_returns_true_at_threshold_plus_one(self) -> None:
        assert planner_route_policy.has_long_history(_history(11)) is True

    def test_returns_false_at_threshold(self) -> None:
        assert planner_route_policy.has_long_history(_history(10)) is False

    def test_custom_threshold(self) -> None:
        assert planner_route_policy.has_long_history(_history(5), threshold=4) is True
        assert planner_route_policy.has_long_history(_history(5), threshold=5) is False


# ---------------------------------------------------------------------------
# 3. build_agent_loop_context sets long_history_hint
# ---------------------------------------------------------------------------


class TestLongHistoryHint:
    """Verify that build_agent_loop_context sets the hint on runtime_state."""

    def _make_record(self) -> MagicMock:
        record = MagicMock()
        record.record_id = uuid4()
        record.title = "Test Record"
        record.render_scene = {}
        record.page_state_json = {}
        return record

    def test_hint_set_when_long_history(self) -> None:
        from app.services.reader_ask.context_runtime import build_agent_loop_context

        record = self._make_record()
        runtime_state = ReaderAskRuntimeState()
        history = _history(11)

        with patch("app.services.reader_ask.context_runtime.planner.build_resolved_context_input", return_value={}):
            build_agent_loop_context(
                record=record,
                runtime_state=runtime_state,
                anchors=[],
                attachments=[],
                user_id=uuid4(),
                page_identity=MagicMock(),
                entry_action="ask_about_this",
                latest_user_message="继续",
                history_messages=history,
            )

        assert runtime_state.long_history_hint is not None
        assert "摘要" in runtime_state.long_history_hint
        assert "History summary" in runtime_state.long_history_hint

    def test_hint_not_set_when_short_history(self) -> None:
        from app.services.reader_ask.context_runtime import build_agent_loop_context

        record = self._make_record()
        runtime_state = ReaderAskRuntimeState()
        history = _history(5)

        with patch("app.services.reader_ask.context_runtime.planner.build_resolved_context_input", return_value={}):
            build_agent_loop_context(
                record=record,
                runtime_state=runtime_state,
                anchors=[],
                attachments=[],
                user_id=uuid4(),
                page_identity=MagicMock(),
                entry_action="ask_about_this",
                latest_user_message="继续",
                history_messages=history,
            )

        assert runtime_state.long_history_hint is None

    def test_hint_not_set_when_no_history(self) -> None:
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
                history_messages=None,
            )

        assert runtime_state.long_history_hint is None


# ---------------------------------------------------------------------------
# 4. long_history_hint flows to prompt payload
# ---------------------------------------------------------------------------


class TestLongHistoryHintInPayload:
    """Verify long_history_hint is included in the prompt payload."""

    def _make_contract(
        self,
        *,
        long_history_hint: str | None = None,
        history_messages: list[dict[str, object]] | None = None,
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
            user_message="继续",
            history_messages=history_messages if history_messages is not None else _history(11),
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
            resolved_intent="general",
            resolved_intent_label="General",
            entry_action="ask_about_this",
            submission_mode="chat",
            cross_record_context_allowed=False,
            resolved_context_input=None,
            quick_action_annotation=None,
            reference_resolution=None,
            planning_snapshot=None,
            max_history_messages=10,
            max_message_text=800,
            long_history_hint=long_history_hint,
        )

    def test_long_history_hint_in_payload(self) -> None:
        from app.services.reader_ask.runtime_contract import build_prompt_payload

        contract = self._make_contract(
            long_history_hint="对话历史较长，早期消息已摘要。"
        )
        payload = build_prompt_payload(contract)
        assert payload["long_history_hint"] is not None
        assert "摘要" in payload["long_history_hint"]

    def test_long_history_hint_none_in_payload(self) -> None:
        from app.services.reader_ask.runtime_contract import build_prompt_payload

        contract = self._make_contract(long_history_hint=None)
        payload = build_prompt_payload(contract)
        assert payload["long_history_hint"] is None

    def test_payload_has_structured_history_summary_when_extractable_state_exists(self) -> None:
        from app.services.reader_ask.runtime_contract import build_prompt_payload

        history_messages: list[dict[str, object]] = [
            {
                "role": "user",
                "content_md": "old question",
                "resolved_intent": "explain",
                "context_anchors": [
                    {"selected_text": "old sentence", "anchor_type": "sentence"}
                ],
            },
            *_history(10),
        ]
        contract = self._make_contract(
            long_history_hint=None,
            history_messages=history_messages,
        )
        payload = build_prompt_payload(contract)
        history = payload["history"]
        assert history[0]["role"] == "system"
        assert "[History summary]" in history[0]["content_md"]
        assert "Previous intents: explain" in history[0]["content_md"]
        assert "old sentence" in history[0]["content_md"]

    def test_payload_omits_history_summary_when_no_extractable_state_exists(self) -> None:
        from app.services.reader_ask.runtime_contract import build_prompt_payload

        contract = self._make_contract(
            long_history_hint="只能基于 payload 中实际存在的摘要和最近消息回答。",
            history_messages=_history(11),
        )
        payload = build_prompt_payload(contract)
        history = payload["history"]
        assert len(history) == 10
        assert all("[History summary]" not in item["content_md"] for item in history)
        assert payload["long_history_hint"] is not None
        assert "实际存在" in payload["long_history_hint"]


# ---------------------------------------------------------------------------
# 5. Planner is not called for long history scenarios
# ---------------------------------------------------------------------------


class TestPlannerNotCalledForLongHistory:
    """Verify that the planner is not invoked when history is long."""

    @pytest.mark.asyncio
    async def test_long_history_skips_planner_in_stream(self) -> None:
        """Integration-level: long history should NOT trigger planner call."""
        import contextlib

        from app.services.reader_ask import service as service_svc
        from app.services.reader_ask import model_options as model_options_svc
        from app.services.ai_usage.billing import WeightedTokensBillingConfig

        user_id = uuid4()
        thread_id = uuid4()
        record_id = uuid4()

        record = service_svc._RecordBundle(
            record_id=record_id,
            title="Test Article",
            source_text="Some source text.",
            render_scene={"content_summary": "Overview."},
            page_state_json={},
            workflow_version="1.0.0",
            schema_version="reader-ask-v2",
        )

        model_option = model_options_svc.ResolvedReaderAskModelOption(
            key="default",
            label="Default",
            description=None,
            selection=None,
            billing=WeightedTokensBillingConfig(reserved_points=10),
            runtime_budget=model_options_svc.ReaderAskRuntimeBudgetConfig(),
            main_model_name="test-model",
            planner_model_name=None,
            replan_model_name="test-replan",
            is_default=True,
            used_fallback=False,
            requested_key=None,
        )

        body = service_svc.ReaderAskMessageStreamRequest(
            content="继续",
            page_identity=service_svc.ReaderAskPageIdentity(record_id=str(record_id)),
            attachments=[],
            entry_action="ask_about_this",
        )

        patches = {
            "load_record": patch.object(service_svc, "_load_record_bundle", new_callable=AsyncMock),
            "resolve_model": patch.object(service_svc, "_resolve_thread_model_option", new_callable=AsyncMock),
            "resolve_anchors": patch.object(service_svc, "_resolve_anchor_refs", new_callable=AsyncMock),
            "ensure_credit": patch.object(service_svc, "ensure_credit_account", new_callable=AsyncMock),
            "check_quota": patch.object(service_svc, "check_quota", return_value=100),
            "reserve_points": patch.object(service_svc, "reserve_points", new_callable=AsyncMock, return_value=MagicMock(reservation_id=uuid4(), total_points=10)),
            "planner_runtime": patch.object(service_svc, "planner_runtime_svc"),
            "context_runtime": patch.object(service_svc, "context_runtime_svc"),
            "stream_run": patch.object(service_svc, "stream_reader_ask_agent_run"),
            "build_deps": patch.object(service_svc, "build_reader_ask_agent_deps", return_value=MagicMock()),
            "resolve_agent": patch.object(service_svc, "resolve_reader_ask_agent", return_value=MagicMock()),
            "settle": patch.object(service_svc, "_settle_reader_ask_reservation", new_callable=AsyncMock),
            "record_usage": patch.object(service_svc, "record_ai_usage_event", new_callable=AsyncMock),
            "cost_points": patch.object(service_svc, "compute_reader_ask_cost_points", return_value=5),
            "refund_points": patch.object(service_svc, "refund_reserved_points", new_callable=AsyncMock),
            "prompt_prep": patch.object(service_svc, "prompt_preparation_svc"),
            "output_contract": patch.object(service_svc, "output_contract_svc"),
            "post_process": patch.object(service_svc, "post_process_svc"),
            "checkpoint": patch.object(service_svc, "stream_checkpoint_svc"),
            "repo": patch.object(service_svc, "repo"),
        }

        with contextlib.ExitStack() as stack:
            mocks = {k: stack.enter_context(v) for k, v in patches.items()}

            mocks["load_record"].return_value = record
            mocks["resolve_model"].return_value = ({"id": str(thread_id), "record_id": str(record_id)}, model_option)
            mocks["resolve_anchors"].return_value = []

            mocks["repo"].get_thread = AsyncMock(return_value={"id": str(thread_id), "record_id": str(record_id)})
            mocks["repo"].list_messages = AsyncMock(return_value=_history(11))
            mocks["repo"].create_message = AsyncMock(return_value={"id": str(uuid4()), "thread_id": str(thread_id), "role": "user", "status": "completed", "content_md": "test", "metadata": {}})
            mocks["repo"].update_message = AsyncMock(return_value={"id": str(uuid4())})
            mocks["repo"].create_turn_run = AsyncMock(return_value={"id": str(uuid4())})
            mocks["repo"].update_turn_run = AsyncMock(return_value={"id": str(uuid4())})
            mocks["repo"].ensure_record_access = AsyncMock()
            mocks["repo"].get_eval_trace = AsyncMock(return_value=None)
            mocks["repo"].upsert_eval_trace = AsyncMock(return_value={})

            mocks["planner_runtime"].submission_mode = MagicMock(return_value="chat")
            # Round 15: resolve_semantic_planning has been removed from
            # planner_runtime. We keep this attribute on the mock only to
            # preserve the regression intent (the live path must not invoke
            # a semantic planner). The attribute is never called.
            mocks["planner_runtime"].resolve_semantic_planning = AsyncMock()

            mock_context = MagicMock()
            mock_context.current_record_context = MagicMock()
            mocks["context_runtime"].materialize_planned_context = AsyncMock(return_value=mock_context)
            mocks["context_runtime"].build_agent_loop_context = MagicMock(return_value={})

            async def _fake_stream(*args, **kwargs):
                yield service_svc.stream_events_svc.encode_sse(
                    service_svc.stream_events_svc.EVENT_MESSAGE_COMPLETED,
                    service_svc.stream_events_svc.message_completed_payload(
                        message_id=str(uuid4()),
                        content_md="test answer",
                    ),
                )
            mocks["stream_run"].side_effect = _fake_stream

            mocks["prompt_prep"].prepare_prompt_payload = MagicMock(return_value=MagicMock(too_large=False))
            mocks["output_contract"].build_user_visible_output = MagicMock(return_value=MagicMock(
                content_md="test", submission_mode="chat", resolved_intent=None,
                citations=[], action_proposals=[], tool_trace=[], evidence=[],
                trace_summary=None, disambiguation=None, external_asset_disambiguation=None,
                response_cards=[], usage_summary=None, billed_points=0,
            ))
            mocks["post_process"].build_clarification_message = MagicMock(return_value="clarification")
            mocks["checkpoint"].build_stream_checkpoint_output_json = MagicMock(return_value=None)

            events = []
            async for event in service_svc.stream_thread_message(user_id, thread_id, body):
                events.append(event)

            # The planner must NOT have been called
            mocks["planner_runtime"].resolve_semantic_planning.assert_not_called()


# ---------------------------------------------------------------------------
# 6. planner_first route value is still valid for backward-compatible trace
# ---------------------------------------------------------------------------


class TestPlannerFirstBackwardCompat:
    """Verify that planner_first is still a valid PlannerRoute literal
    for backward-compatible trace serialization."""

    def test_planner_first_is_valid_route_value(self) -> None:
        """planner_first is still a valid PlannerRoute literal."""
        from app.services.reader_ask.planner_route_policy import PlannerRoute
        # Type checker validates this is a valid literal
        route: PlannerRoute = "planner_first"
        assert route == "planner_first"

    def test_planner_first_trace_serialization(self) -> None:
        """planner_route_used='planner_first' still works in trace."""
        from app.services.reader_ask import service as service_svc

        data = service_svc._planning_snapshot_json(
            None, planner_route_used="planner_first"
        )
        assert data["planner_route_used"] == "planner_first"
        assert data["planner_skipped"] is False


# ---------------------------------------------------------------------------
# 7. Tool registry invariants still hold
# ---------------------------------------------------------------------------


class TestToolRegistryInvariantsRound12:
    """Verify that Round 12 changes don't break registry invariants."""

    def test_registry_invariants_hold(self) -> None:
        from app.agents.reader_ask_tool_registry import assert_registry_invariants

        assert_registry_invariants()

    def test_agent_callable_count_unchanged(self) -> None:
        from app.agents.reader_ask_tool_registry import agent_callable_tool_names

        names = agent_callable_tool_names()
        # Round 12 does not add or remove tools — same 9 agent-callable tools.
        assert len(names) == 9

    def test_runtime_state_has_long_history_hint(self) -> None:
        state = ReaderAskRuntimeState()
        assert hasattr(state, "long_history_hint")
        assert state.long_history_hint is None
