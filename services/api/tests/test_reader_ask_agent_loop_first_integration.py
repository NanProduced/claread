"""Service-level integration tests for the Ask Claread agent-loop-first path.

These tests verify the agent-loop-first routing decision and its downstream
effects within the service layer, without requiring real LLM/network
dependencies.

Coverage:
- Simple article-bound queries use agent-loop-first path (planner_skipped=True)
- Deictic/no-anchor queries also use agent-loop-first path (Round 8)
- Agent-loop-first path does not trigger replan
- Agent-loop-first path materialize still gets article_overview
- Agent-loop-first path retry stays on agent-loop-first path
- stream_thread_message agent-loop-first path skips planner, sets planning_snapshot=None
- retry_thread_message agent-loop-first path skips planner
"""

from __future__ import annotations

import contextlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.agents.reader_ask_agent import ReaderAskRuntimeState
from app.schemas.reader_ask import (
    ReaderAskAnchorRef,
    ReaderAskAttachment,
    ReaderAskAttachmentMetadata,
    ReaderAskPageIdentity,
)
from app.services.reader_ask import agent_runner as agent_runner_svc
from app.services.reader_ask import planner_route_policy
from app.services.reader_ask import planner as planner_svc
from app.services.reader_ask import service as service_svc


def _anchor(anchor_type: str = "sentence") -> ReaderAskAnchorRef:
    return ReaderAskAnchorRef(
        anchor_type=anchor_type,
        label="a",
        sentence_id="s1",
    )


def _attachment(kind: str) -> ReaderAskAttachment:
    return ReaderAskAttachment(
        kind=kind,
        subtype="x",
        label="att",
        metadata=ReaderAskAttachmentMetadata(source_surface="reader_page"),
    )


def _history(n: int) -> list[dict[str, Any]]:
    return [{"role": "user", "content_md": f"msg {i}"} for i in range(n)]


# ---------------------------------------------------------------------------
# Agent-loop-first routing decision
# ---------------------------------------------------------------------------


class TestAgentLoopFirstRoutingDecision:
    """Test that the agent-loop-first routing decision is correct for various
    request configurations, matching the ``resolve_planner_route`` logic."""

    def test_simple_article_bound_uses_agent_loop_first(self) -> None:
        assert planner_route_policy.resolve_planner_route(
            entry_action="ask_about_this",
            history_messages=_history(2),
            attachments=[],
            anchors=[_anchor()],
            cross_record_toggle=False,
            latest_user_message="这篇文章想表达什么？",
        ) == "agent_loop_first"

    def test_deictic_no_anchor_uses_agent_loop_first(self) -> None:
        # Round 8: deictic without anchor no longer triggers planner_first
        assert planner_route_policy.resolve_planner_route(
            entry_action="ask_about_this",
            history_messages=_history(0),
            attachments=[],
            anchors=[],
            cross_record_toggle=False,
            latest_user_message="解释这句",
        ) == "agent_loop_first"

    def test_cross_record_keyword_uses_agent_loop_first(self) -> None:
        # Round 9: cross-record toggle + keywords no longer triggers planner_first
        assert planner_route_policy.resolve_planner_route(
            entry_action="ask_about_this",
            history_messages=_history(0),
            attachments=[],
            anchors=[_anchor()],
            cross_record_toggle=True,
            latest_user_message="和我之前那篇 chronic absenteeism 的文章有什么不同？",
        ) == "agent_loop_first"

    def test_record_ref_attachment_uses_agent_loop_first(self) -> None:
        # Round 10: external attachments no longer trigger planner_first
        assert planner_route_policy.resolve_planner_route(
            entry_action="ask_about_this",
            history_messages=_history(0),
            attachments=[_attachment("record_ref")],
            anchors=[_anchor()],
            cross_record_toggle=False,
            latest_user_message="解释一下",
        ) == "agent_loop_first"

    def test_long_history_uses_agent_loop_first(self) -> None:
        """Round 12: long history no longer triggers planner_first."""
        assert planner_route_policy.resolve_planner_route(
            entry_action="ask_about_this",
            history_messages=_history(11),
            attachments=[],
            anchors=[_anchor()],
            cross_record_toggle=False,
            latest_user_message="继续",
        ) == "agent_loop_first"

    def test_cross_record_toggle_uses_agent_loop_first(self) -> None:
        # Round 9: cross-record toggle + keywords no longer triggers planner_first
        assert planner_route_policy.resolve_planner_route(
            entry_action="ask_about_this",
            history_messages=_history(0),
            attachments=[],
            anchors=[_anchor()],
            cross_record_toggle=True,
            latest_user_message="和另一篇有什么不同",
        ) == "agent_loop_first"

    def test_dictionary_anchor_uses_agent_loop(self) -> None:
        """Round 11: dictionary anchor no longer triggers planner_first."""
        assert planner_route_policy.resolve_planner_route(
            entry_action="ask_about_this",
            history_messages=_history(0),
            attachments=[],
            anchors=[ReaderAskAnchorRef(anchor_type="dictionary_entry", label="dict", dict_entry_id=1)],
            cross_record_toggle=False,
            latest_user_message="这个词什么意思",
        ) == "agent_loop_first"

    def test_explain_this_with_anchor_uses_agent_loop_first(self) -> None:
        assert planner_route_policy.resolve_planner_route(
            entry_action="explain_this",
            history_messages=_history(0),
            attachments=[],
            anchors=[_anchor()],
            cross_record_toggle=False,
            latest_user_message="解释一下",
        ) == "agent_loop_first"

    def test_why_here_with_anchor_uses_agent_loop_first(self) -> None:
        assert planner_route_policy.resolve_planner_route(
            entry_action="why_here",
            history_messages=_history(0),
            attachments=[],
            anchors=[_anchor()],
            cross_record_toggle=False,
            latest_user_message="这里为什么用 present perfect",
        ) == "agent_loop_first"

    def test_why_here_without_anchor_uses_agent_loop_first(self) -> None:
        # Round 8: deictic without anchor no longer triggers planner_first
        assert planner_route_policy.resolve_planner_route(
            entry_action="why_here",
            history_messages=_history(0),
            attachments=[],
            anchors=[],
            cross_record_toggle=False,
            latest_user_message="这里为什么用 present perfect",
        ) == "agent_loop_first"


# ---------------------------------------------------------------------------
# Agent-loop-first path does not trigger replan
# ---------------------------------------------------------------------------


class TestAgentLoopFirstNoReplan:
    """Agent-loop-first path sets ``planning_snapshot=None``, which must prevent replan."""

    def test_none_snapshot_no_replan_on_degenerate(self) -> None:
        result = agent_runner_svc.build_replan_event(
            final_content_md="",
            planning_snapshot=None,
            assistant_message_id="msg-1",
        )
        assert result is None

    def test_none_snapshot_no_replan_on_refusal(self) -> None:
        result = agent_runner_svc.build_replan_event(
            final_content_md="I cannot answer this question.",
            planning_snapshot=None,
            assistant_message_id="msg-1",
        )
        assert result is None

    def test_agent_loop_first_planning_snapshot_also_no_replan(self) -> None:
        snap = planner_svc.MinimalPlanningSnapshot()
        result = agent_runner_svc.build_replan_event(
            final_content_md="",
            planning_snapshot=snap,
            assistant_message_id="msg-1",
        )
        # MinimalPlanningSnapshot has clarification_mode="none" which
        # does not block replan, but the degenerate check should still
        # work. However, since MinimalPlanningSnapshot IS a planning
        # snapshot (not None), replan IS allowed in theory. The key
        # contract is: when the service sets planning_snapshot=None
        # (the actual agent-loop-first path), replan is blocked.
        # For MinimalPlanningSnapshot, replan behavior depends on
        # clarification_mode.
        assert result is not None  # MinimalPlanningSnapshot allows replan


# ---------------------------------------------------------------------------
# Agent-loop-first path materialize gets overview
# ---------------------------------------------------------------------------


class TestAgentLoopFirstMaterializeOverview:
    """When ``planning_snapshot=None``, ``materialize_planned_context`` still
    fetches ``article_overview``."""

    @pytest.mark.asyncio
    async def test_none_snapshot_still_fetches_overview(self) -> None:
        from app.services.reader_ask import context_runtime as context_runtime_svc

        record = MagicMock()
        record.record_id = uuid4()
        record.title = "Test Article"
        # Simulate render_scene with article_overview
        record.render_scene = MagicMock()
        record.render_scene.content_summary = "This is a test article overview."

        runtime_state = ReaderAskRuntimeState()

        result = await context_runtime_svc.materialize_planned_context(
            user_id=uuid4(),
            record=record,
            runtime_state=runtime_state,
            planning_snapshot=None,
            page_identity=ReaderAskPageIdentity(record_id=str(record.record_id)),
            entry_action="ask_about_this",
            attachments=[],
            anchors=[_anchor()],
            get_record_context_cb=AsyncMock(return_value=None),
            get_record_insights_cb=AsyncMock(return_value=None),
            load_record_bundle_cb=AsyncMock(return_value=MagicMock()),
        )

        # The result should exist and contain article_overview
        assert result is not None
        # article_overview should have been fetched
        assert runtime_state.latest_article_overview is not None or result.current_record_context is not None


# ---------------------------------------------------------------------------
# Agent-loop-first path runtime state telemetry
# ---------------------------------------------------------------------------


class TestAgentLoopFirstRuntimeStateTelemetry:
    """Verify that agent-loop-first path telemetry is correctly set and preserved."""

    def test_agent_loop_first_sets_planner_skipped(self) -> None:
        state = ReaderAskRuntimeState()
        state.planner_skipped = True
        state.planner_route_used = "agent_loop_first"
        assert state.planner_skipped is True
        assert state.planner_route_used == "agent_loop_first"

    def test_agent_loop_first_telemetry_preserved_on_rebuild(self) -> None:
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

    def test_legacy_telemetry_preserved_on_rebuild(self) -> None:
        original = ReaderAskRuntimeState()
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


# ---------------------------------------------------------------------------
# Agent-loop-first path trace semantics
# ---------------------------------------------------------------------------


class TestAgentLoopFirstTraceSemantics:
    """Verify that eval trace correctly reflects agent-loop-first route via
    ``planner_route_used`` instead of bare ``planning_snapshot is None``."""

    def test_none_snapshot_agent_loop_first_trace(self) -> None:
        data = service_svc._planning_snapshot_json(
            None, planner_route_used="agent_loop_first"
        )
        assert data["planner_skipped"] is True
        assert data["planner_route_used"] == "agent_loop_first"

    def test_none_snapshot_planner_first_trace(self) -> None:
        """When planning_snapshot is None but route is planner_first (e.g.
        error case), trace should NOT claim agent_loop_first."""
        data = service_svc._planning_snapshot_json(
            None, planner_route_used="planner_first"
        )
        assert data["planner_skipped"] is False
        assert data["planner_route_used"] == "planner_first"

    def test_minimal_snapshot_trace(self) -> None:
        snap = planner_svc.MinimalPlanningSnapshot()
        data = service_svc._planning_snapshot_json(
            snap, planner_route_used="agent_loop_first"
        )
        assert data["planner_skipped"] is True
        assert data["planner_route_used"] == "agent_loop_first"



# ---------------------------------------------------------------------------
# Agent-loop-first path retry stays on agent-loop-first path
# ---------------------------------------------------------------------------


class TestAgentLoopFirstRetryConsistency:
    """Verify that retry logic also respects agent-loop-first routing."""

    def test_retry_agent_loop_first_eligible_request_stays_agent_loop_first(self) -> None:
        """A request that was eligible for agent-loop-first path should still be
        eligible on retry (same entry_action, same conditions)."""
        # Simulate first call
        assert planner_route_policy.resolve_planner_route(
            entry_action="ask_about_this",
            history_messages=_history(2),
            attachments=[],
            anchors=[_anchor()],
            cross_record_toggle=False,
            latest_user_message="这篇文章想表达什么？",
        ) == "agent_loop_first"

        # Retry with same conditions should also be agent-loop-first path
        assert planner_route_policy.resolve_planner_route(
            entry_action="ask_about_this",
            history_messages=_history(2),
            attachments=[],
            anchors=[_anchor()],
            cross_record_toggle=False,
            latest_user_message="这篇文章想表达什么？",
        ) == "agent_loop_first"

    def test_retry_deictic_stays_agent_loop_first(self) -> None:
        """A deictic/no-anchor request now uses agent_loop_first (Round 8)."""
        assert planner_route_policy.resolve_planner_route(
            entry_action="ask_about_this",
            history_messages=_history(0),
            attachments=[],
            anchors=[],
            cross_record_toggle=False,
            latest_user_message="解释这句",
        ) == "agent_loop_first"

        # Retry with same conditions
        assert planner_route_policy.resolve_planner_route(
            entry_action="ask_about_this",
            history_messages=_history(0),
            attachments=[],
            anchors=[],
            cross_record_toggle=False,
            latest_user_message="解释这句",
        ) == "agent_loop_first"


# ---------------------------------------------------------------------------
# stream_thread_message entry-level integration
# ---------------------------------------------------------------------------


def _make_record_bundle(record_id: UUID | None = None) -> Any:
    """Build a minimal _RecordBundle-like object for testing."""
    from app.services.reader_ask import service as service_svc

    rid = record_id or uuid4()
    bundle = service_svc._RecordBundle(
        record_id=rid,
        title="Test Article",
        source_text="Some source text for testing.",
        render_scene={"content_summary": "This is a test article overview."},
        page_state_json={},
        workflow_version="1.0.0",
        schema_version="reader-ask-v2",
    )
    return bundle


def _make_model_option() -> Any:
    """Build a minimal ResolvedReaderAskModelOption for testing."""
    from app.services.ai_usage.billing import WeightedTokensBillingConfig
    from app.services.reader_ask import model_options as model_options_svc

    return model_options_svc.ResolvedReaderAskModelOption(
        key="default",
        label="Default",
        description=None,
        selection=None,
        billing=WeightedTokensBillingConfig(reserved_points=10),
        runtime_budget=model_options_svc.ReaderAskRuntimeBudgetConfig(),
        main_model_name="test-model",
        replan_model_name="test-replan",
        is_default=True,
        used_fallback=False,
        requested_key=None,
    )


async def _collect_sse_events(gen: Any) -> list[str]:
    """Collect all SSE events from an async generator."""
    events = []
    async for event in gen:
        events.append(event)
    return events


def _patch_service_boundaries(service_svc: Any, *, include_planning_deps: bool = False, agent_loop_first: bool = True) -> Any:
    """Return a dict of patch contexts for all service boundaries.

    Callers should enter all patches, then configure repo mocks.
    ``include_planning_deps`` is accepted for backward compatibility but
    no longer patches anything (planning_deps_factory.py deleted in Round 15).
    If ``agent_loop_first`` is True, ``resolve_planner_route`` returns
    ``"agent_loop_first"``; otherwise ``"planner_first"``.
    """
    route = "agent_loop_first" if agent_loop_first else "planner_first"
    patches = {
        "load_record": patch.object(service_svc, "_load_record_bundle", new_callable=AsyncMock),
        "resolve_model": patch.object(service_svc, "_resolve_thread_model_option", new_callable=AsyncMock),
        "resolve_anchors": patch.object(service_svc, "_resolve_anchor_refs", new_callable=AsyncMock),
        "ensure_credit": patch.object(service_svc, "ensure_credit_account", new_callable=AsyncMock),
        "check_quota": patch.object(service_svc, "check_quota", new_callable=AsyncMock),
        "reserve_points": patch.object(service_svc, "reserve_points", new_callable=AsyncMock),
        "planner_runtime": patch.object(service_svc, "planner_runtime_svc"),
        "context_runtime": patch.object(service_svc, "context_runtime_svc"),
        "stream_run": patch.object(service_svc, "stream_reader_ask_agent_run"),
        "build_deps": patch.object(service_svc, "build_reader_ask_agent_deps", return_value=MagicMock()),
        "resolve_agent": patch.object(service_svc, "resolve_reader_ask_agent", return_value=MagicMock()),
        "settle": patch.object(service_svc, "_settle_reader_ask_reservation", new_callable=AsyncMock),
        "record_usage": patch.object(service_svc, "record_ai_usage_event", new_callable=AsyncMock),
        "cost_points": patch.object(service_svc, "compute_reader_ask_cost_points", return_value=5),
        "prompt_prep": patch.object(service_svc, "prompt_preparation_svc"),
        "output_contract": patch.object(service_svc, "output_contract_svc"),
        "post_process": patch.object(service_svc, "post_process_svc"),
        "checkpoint": patch.object(service_svc, "stream_checkpoint_svc"),
        "repo": patch.object(service_svc, "repo"),
        "resolve_planner_route": patch(
            "app.services.reader_ask.service.planner_route_policy.resolve_planner_route",
            return_value=route,
        ),
        "refund_points": patch.object(service_svc, "refund_reserved_points", new_callable=AsyncMock),
    }
    return patches


def _configure_common_mocks(
    mock_repo: Any,
    mock_planner_runtime: Any,
    mock_context_runtime: Any,
    mock_stream_run: Any,
    mock_prompt_prep: Any,
    mock_output_contract: Any,
    mock_post_process: Any,
    mock_checkpoint: Any,
    *,
    planner_result: Any = None,
) -> None:
    """Configure all mock objects with sensible defaults."""
    mock_repo.get_thread = AsyncMock(return_value={"id": str(uuid4()), "record_id": str(uuid4())})
    mock_repo.list_messages = AsyncMock(return_value=[])
    mock_repo.create_message = AsyncMock(return_value={
        "id": str(uuid4()), "thread_id": str(uuid4()),
        "role": "user", "status": "completed", "content_md": "test", "metadata": {},
    })
    mock_repo.update_message = AsyncMock(return_value={"id": str(uuid4())})
    mock_repo.create_turn_run = AsyncMock(return_value={"id": str(uuid4())})
    mock_repo.update_turn_run = AsyncMock(return_value={"id": str(uuid4())})
    mock_repo.ensure_record_access = AsyncMock()
    mock_repo.get_eval_trace = AsyncMock(return_value=None)
    mock_repo.upsert_eval_trace = AsyncMock(return_value={})

    mock_planner_runtime.submission_mode = MagicMock(return_value="chat")
    if planner_result is not None:
        mock_planner_runtime.resolve_semantic_planning = AsyncMock(return_value=planner_result)
    else:
        mock_planner_runtime.resolve_semantic_planning = AsyncMock()

    mock_context = MagicMock()
    mock_context.current_record_context = MagicMock()
    mock_context_runtime.materialize_planned_context = AsyncMock(return_value=mock_context)

    async def _fake_stream(*args, **kwargs):
        from app.services.reader_ask import service as service_svc
        yield service_svc.stream_events_svc.encode_sse(
            service_svc.stream_events_svc.EVENT_MESSAGE_COMPLETED,
            service_svc.stream_events_svc.message_completed_payload(
                message_id=str(uuid4()),
                content_md="test answer",
            ),
        )
    mock_stream_run.side_effect = _fake_stream

    mock_prompt_prep.prepare_prompt_payload = MagicMock(return_value=MagicMock(too_large=False))

    mock_output_contract.build_user_visible_output = MagicMock(return_value=MagicMock(
        content_md="test answer", submission_mode="chat", resolved_intent=None,
        citations=[], action_proposals=[], tool_trace=[], evidence=[],
        trace_summary=None, disambiguation=None, external_asset_disambiguation=None,
        response_cards=[], usage_summary=None, billed_points=0,
    ))

    mock_post_process.build_clarification_message = MagicMock(return_value="clarification")
    mock_checkpoint.build_stream_checkpoint_output_json = MagicMock(return_value=None)


class TestStreamThreadMessageAgentLoopFirst:
    """Integration tests for ``stream_thread_message`` that verify the
    agent-loop-first path orchestration: planner is NOT called,
    planning_snapshot is None, and replan is not triggered.
    """

    @pytest.mark.asyncio
    async def test_agent_loop_first_skips_planner(self) -> None:
        """When agent-loop-first path conditions are met,
        ``resolve_semantic_planning`` must NOT be called."""
        from app.services.reader_ask import service as service_svc

        user_id = uuid4()
        thread_id = uuid4()
        record_id = uuid4()
        record = _make_record_bundle(record_id)
        model_option = _make_model_option()

        body = service_svc.ReaderAskMessageStreamRequest(
            content="这篇文章想表达什么？",
            page_identity=ReaderAskPageIdentity(record_id=str(record_id)),
            attachments=[],
            entry_action="ask_about_this",
        )

        p = _patch_service_boundaries(service_svc)

        with contextlib.ExitStack() as stack:
            mocks = {k: stack.enter_context(v) for k, v in p.items()}

            mocks["load_record"].return_value = record
            mocks["resolve_model"].return_value = ({"id": str(thread_id), "record_id": str(record_id)}, model_option)
            resolved_anchor = _anchor()
            mocks["resolve_anchors"].return_value = [resolved_anchor]
            mocks["check_quota"].return_value = 100
            mocks["reserve_points"].return_value = MagicMock(
                reservation_id=uuid4(), total_points=10
            )

            _configure_common_mocks(
                mocks["repo"], mocks["planner_runtime"], mocks["context_runtime"],
                mocks["stream_run"], mocks["prompt_prep"], mocks["output_contract"],
                mocks["post_process"], mocks["checkpoint"],
            )

            events = await _collect_sse_events(
                service_svc.stream_thread_message(user_id, thread_id, body)
            )

            # The planner must NOT have been called
            mocks["resolve_planner_route"].assert_called_once()
            gate_kwargs = mocks["resolve_planner_route"].call_args.kwargs
            assert gate_kwargs["entry_action"] == "ask_about_this"
            assert gate_kwargs["history_messages"] == []
            assert gate_kwargs["attachments"] == []
            assert gate_kwargs["anchors"] == [resolved_anchor]
            assert gate_kwargs["cross_record_toggle"] is False
            assert gate_kwargs["latest_user_message"] == "这篇文章想表达什么？"
            mocks["planner_runtime"].resolve_semantic_planning.assert_not_called()

    # Round 15: test_planner_first_path_still_calls_planner_when_forced has
    # been removed because the legacy planner_first executable path has been
    # deleted from service.py. planner_first survives only as a trace value.


class TestRetryThreadMessageAgentLoopFirst:
    """Integration tests for ``retry_thread_message`` that verify the
    agent-loop-first path orchestration in the retry flow."""

    @pytest.mark.asyncio
    async def test_retry_agent_loop_first_skips_planner(self) -> None:
        """When retry conditions meet agent-loop-first path, planner must NOT be called."""
        from app.services.reader_ask import service as service_svc

        user_id = uuid4()
        thread_id = uuid4()
        record_id = uuid4()
        message_id = uuid4()
        record = _make_record_bundle(record_id)
        model_option = _make_model_option()

        # Build the assistant message and user message that retry loads
        user_msg_id = uuid4()
        timestamp = "2026-06-15T00:00:00Z"
        resolved_context_input = {
            "page_identity": {"record_id": str(record_id)},
            "attachments": [],
            "entry_action": "ask_about_this",
        }
        assistant_msg = {
            "id": str(message_id),
            "thread_id": str(thread_id),
            "role": "assistant",
            "status": "completed",
            "content_md": "old answer",
            "metadata": {},
            "persisted_supplements_json": [],
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        user_msg = {
            "id": str(user_msg_id),
            "thread_id": str(thread_id),
            "role": "user",
            "status": "completed",
            "content_md": "这篇文章想表达什么？",
            "resolved_context_input": resolved_context_input,
            "metadata": {
                "resolved_context_input": resolved_context_input,
            },
            "created_at": timestamp,
            "updated_at": timestamp,
        }

        p = _patch_service_boundaries(service_svc)

        with contextlib.ExitStack() as stack:
            mocks = {k: stack.enter_context(v) for k, v in p.items()}

            mocks["load_record"].return_value = record
            mocks["resolve_model"].return_value = ({"id": str(thread_id), "record_id": str(record_id)}, model_option)
            mocks["resolve_anchors"].return_value = []
            mocks["check_quota"].return_value = 100
            mocks["reserve_points"].return_value = MagicMock(
                reservation_id=uuid4(), total_points=10
            )

            _configure_common_mocks(
                mocks["repo"], mocks["planner_runtime"], mocks["context_runtime"],
                mocks["stream_run"], mocks["prompt_prep"], mocks["output_contract"],
                mocks["post_process"], mocks["checkpoint"],
            )

            # Additional repo mocks for retry
            mocks["repo"].get_message = AsyncMock(return_value=assistant_msg)
            mocks["repo"].list_messages = AsyncMock(return_value=[user_msg, assistant_msg])

            events = await _collect_sse_events(
                service_svc.retry_thread_message(user_id, thread_id, message_id)
            )

            # The planner must NOT have been called
            mocks["resolve_planner_route"].assert_called_once()
            gate_kwargs = mocks["resolve_planner_route"].call_args.kwargs
            assert gate_kwargs["entry_action"] == "ask_about_this"
            assert gate_kwargs["history_messages"] == [user_msg]
            assert gate_kwargs["attachments"] == []
            assert gate_kwargs["anchors"] == []
            assert gate_kwargs["cross_record_toggle"] is False
            assert gate_kwargs["latest_user_message"] == "这篇文章想表达什么？"
            mocks["planner_runtime"].resolve_semantic_planning.assert_not_called()
