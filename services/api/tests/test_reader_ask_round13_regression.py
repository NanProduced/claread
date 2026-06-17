"""Round 13 regression tests: agent-loop-first route closure and planner cleanup audit.

These tests verify the post-Round-12 state of the Ask Claread routing layer:

1. ``resolve_planner_route()`` always returns ``"agent_loop_first"`` without
   any monkeypatch — covering a matrix of entry_action / history / attachment
   / anchor combinations.
2. The dead-code helpers removed in Round 13
   (``_has_planner_required_attachments`` / ``_EXTERNAL_ATTACHMENT_KINDS``)
   are no longer present on the ``planner_route_policy`` module.
3. ``stream_thread_message`` live route (no monkeypatch on
   ``resolve_planner_route``) does NOT call
   ``planner_runtime.resolve_semantic_planning``.
4. ``retry_thread_message`` live route (same condition) does NOT call
   ``planner_runtime.resolve_semantic_planning``.
5. ``planner_first`` remains a valid ``PlannerRoute`` literal for
   backward-compatible trace serialization.

Round 15 update: the forced ``planner_first`` executable path
(``resolve_semantic_planning`` call sites in ``service.py``) has been
removed. ``planner_first`` survives only as a trace/historical value;
there is no longer an executable branch that reaches
``planner_runtime.resolve_semantic_planning``. The
``TestForcedPlannerFirstStillCallsPlanner`` class that previously
asserted the legacy path was intact has been deleted.

Round 13 only audits and small-scale cleans; it does NOT delete
``planner.py`` / ``planner_runtime.py``. See
``docs/tmp/ask-claread/TMP-ask-claread-round13-planner-cleanup-audit-2026-06-17.md``
for the full dependency audit and Round 14 deletion candidates.
"""

from __future__ import annotations

import contextlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from app.schemas.reader_ask import (
    ReaderAskAnchorRef,
    ReaderAskAttachment,
    ReaderAskAttachmentMetadata,
    ReaderAskPageIdentity,
)
from app.services.reader_ask import planner_route_policy


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _anchor(anchor_type: str = "sentence") -> ReaderAskAnchorRef:
    return ReaderAskAnchorRef(anchor_type=anchor_type, label="a", sentence_id="s1")


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


def _make_record_bundle(record_id: UUID | None = None) -> Any:
    """Build a minimal _RecordBundle-like object for testing."""
    from app.services.reader_ask import service as service_svc

    rid = record_id or uuid4()
    return service_svc._RecordBundle(
        record_id=rid,
        title="Test Article",
        source_text="Some source text for testing.",
        render_scene={"content_summary": "This is a test article overview."},
        page_state_json={},
        workflow_version="1.0.0",
        schema_version="reader-ask-v2",
    )


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
    events: list[str] = []
    async for event in gen:
        events.append(event)
    return events


def _patch_service_boundaries(service_svc: Any, *, agent_loop_first: bool = True) -> dict[str, Any]:
    """Return a dict of patch contexts for all service boundaries.

    If ``agent_loop_first`` is True (default), ``resolve_planner_route`` is
    NOT monkeypatched — the real implementation runs and returns
    ``"agent_loop_first"``. This is the Round 13 live-route test posture.

    If ``agent_loop_first`` is False, ``resolve_planner_route`` is
    monkeypatched to return ``"planner_first"`` to exercise the legacy
    code path.
    """
    patches: dict[str, Any] = {
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
        "refund_points": patch.object(service_svc, "refund_reserved_points", new_callable=AsyncMock),
    }
    # Round 15: agent_loop_first=False no longer patches planning_deps
    # (planning_deps_factory.py deleted). The planner_first executable path
    # has been removed; this parameter is retained for backward compat.
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
    mock_context_runtime.build_agent_loop_context = MagicMock(return_value={})

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


# ---------------------------------------------------------------------------
# 1. resolve_planner_route() always returns agent_loop_first (no monkeypatch)
# ---------------------------------------------------------------------------


class TestResolvePlannerRouteAlwaysAgentLoopFirstRound13:
    """Round 13: verify resolve_planner_route() unconditionally returns
    ``"agent_loop_first"`` across a matrix of inputs, without any monkeypatch."""

    def test_simple_article_bound(self) -> None:
        assert planner_route_policy.resolve_planner_route(
            entry_action="ask_about_this",
            history_messages=_history(2),
            attachments=[],
            anchors=[_anchor()],
            cross_record_toggle=False,
            latest_user_message="这篇文章想表达什么？",
        ) == "agent_loop_first"

    def test_empty_history_no_anchor(self) -> None:
        assert planner_route_policy.resolve_planner_route(
            entry_action="ask_about_this",
            history_messages=[],
            attachments=[],
            anchors=[],
            cross_record_toggle=False,
            latest_user_message="解释一下",
        ) == "agent_loop_first"

    def test_deictic_no_anchor(self) -> None:
        assert planner_route_policy.resolve_planner_route(
            entry_action="ask_about_this",
            history_messages=_history(0),
            attachments=[],
            anchors=[],
            cross_record_toggle=False,
            latest_user_message="解释这句",
        ) == "agent_loop_first"

    def test_cross_record_keyword_with_toggle(self) -> None:
        assert planner_route_policy.resolve_planner_route(
            entry_action="ask_about_this",
            history_messages=_history(0),
            attachments=[],
            anchors=[_anchor()],
            cross_record_toggle=True,
            latest_user_message="和我之前那篇 chronic absenteeism 的文章有什么不同？",
        ) == "agent_loop_first"

    def test_record_ref_attachment(self) -> None:
        assert planner_route_policy.resolve_planner_route(
            entry_action="ask_about_this",
            history_messages=_history(0),
            attachments=[_attachment("record_ref", subtype="related_record")],
            anchors=[_anchor()],
            cross_record_toggle=False,
            latest_user_message="解释一下",
        ) == "agent_loop_first"

    def test_dictionary_anchor(self) -> None:
        assert planner_route_policy.resolve_planner_route(
            entry_action="ask_about_this",
            history_messages=_history(0),
            attachments=[],
            anchors=[_dict_anchor()],
            cross_record_toggle=False,
            latest_user_message="这个词什么意思",
        ) == "agent_loop_first"

    def test_long_history(self) -> None:
        assert planner_route_policy.resolve_planner_route(
            entry_action="ask_about_this",
            history_messages=_history(50),
            attachments=[],
            anchors=[_anchor()],
            cross_record_toggle=False,
            latest_user_message="继续",
        ) == "agent_loop_first"

    def test_why_here_without_anchor(self) -> None:
        assert planner_route_policy.resolve_planner_route(
            entry_action="why_here",
            history_messages=_history(0),
            attachments=[],
            anchors=[],
            cross_record_toggle=False,
            latest_user_message="这里为什么用 present perfect",
        ) == "agent_loop_first"

    def test_explain_this_with_anchor(self) -> None:
        assert planner_route_policy.resolve_planner_route(
            entry_action="explain_this",
            history_messages=_history(0),
            attachments=[],
            anchors=[_anchor()],
            cross_record_toggle=False,
            latest_user_message="解释一下",
        ) == "agent_loop_first"


# ---------------------------------------------------------------------------
# 2. Dead-code helpers removed in Round 13
# ---------------------------------------------------------------------------


class TestRound13DeadCodeRemoval:
    """Verify the dead-code helpers removed in Round 13 are no longer
    present on the ``planner_route_policy`` module."""

    def test_has_planner_required_attachments_removed(self) -> None:
        assert not hasattr(planner_route_policy, "_has_planner_required_attachments")

    def test_external_attachment_kinds_removed(self) -> None:
        assert not hasattr(planner_route_policy, "_EXTERNAL_ATTACHMENT_KINDS")

    def test_still_used_constants_present(self) -> None:
        """Constants still used by live helpers must remain."""
        assert hasattr(planner_route_policy, "_CROSS_RECORD_KEYWORDS")
        assert hasattr(planner_route_policy, "_DEICTIC_PATTERNS")
        assert hasattr(planner_route_policy, "_LONG_HISTORY_THRESHOLD")


# ---------------------------------------------------------------------------
# 3. stream_thread_message live route skips planner
# ---------------------------------------------------------------------------


class TestStreamThreadMessageLiveRouteSkipsPlanner:
    """Round 13: without monkeypatching ``resolve_planner_route``, the live
    route in ``stream_thread_message`` must NOT call
    ``planner_runtime.resolve_semantic_planning``.
    """

    @pytest.mark.asyncio
    async def test_live_route_skips_planner(self) -> None:
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

        # NOTE: agent_loop_first=True (default) → resolve_planner_route is
        # NOT monkeypatched; the real implementation runs.
        p = _patch_service_boundaries(service_svc)

        with contextlib.ExitStack() as stack:
            mocks = {k: stack.enter_context(v) for k, v in p.items()}

            mocks["load_record"].return_value = record
            mocks["resolve_model"].return_value = (
                {"id": str(thread_id), "record_id": str(record_id)},
                model_option,
            )
            mocks["resolve_anchors"].return_value = [_anchor()]
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

            # The planner MUST NOT have been called on the live route.
            mocks["planner_runtime"].resolve_semantic_planning.assert_not_called()


# ---------------------------------------------------------------------------
# 4. retry_thread_message live route skips planner
# ---------------------------------------------------------------------------


class TestRetryThreadMessageLiveRouteSkipsPlanner:
    """Round 13: without monkeypatching ``resolve_planner_route``, the live
    route in ``retry_thread_message`` must NOT call
    ``planner_runtime.resolve_semantic_planning``.
    """

    @pytest.mark.asyncio
    async def test_live_route_skips_planner(self) -> None:
        from app.services.reader_ask import service as service_svc

        user_id = uuid4()
        thread_id = uuid4()
        record_id = uuid4()
        message_id = uuid4()
        user_msg_id = uuid4()
        record = _make_record_bundle(record_id)
        model_option = _make_model_option()

        timestamp = "2026-06-17T00:00:00Z"
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

        # NOTE: agent_loop_first=True (default) → resolve_planner_route is
        # NOT monkeypatched; the real implementation runs.
        p = _patch_service_boundaries(service_svc)

        with contextlib.ExitStack() as stack:
            mocks = {k: stack.enter_context(v) for k, v in p.items()}

            mocks["load_record"].return_value = record
            mocks["resolve_model"].return_value = (
                {"id": str(thread_id), "record_id": str(record_id)},
                model_option,
            )
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

            mocks["repo"].get_message = AsyncMock(return_value=assistant_msg)
            mocks["repo"].list_messages = AsyncMock(return_value=[user_msg, assistant_msg])

            events = await _collect_sse_events(
                service_svc.retry_thread_message(user_id, thread_id, message_id)
            )

            # The planner MUST NOT have been called on the live route.
            mocks["planner_runtime"].resolve_semantic_planning.assert_not_called()


# ---------------------------------------------------------------------------
# 5. planner_first remains a valid PlannerRoute literal
# ---------------------------------------------------------------------------


class TestPlannerFirstBackwardCompatRound13:
    """Round 13: ``planner_first`` is still a valid ``PlannerRoute`` literal
    for backward-compatible trace serialization, even though no live
    condition triggers it."""

    def test_planner_first_is_valid_route_value(self) -> None:
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

    def test_agent_loop_first_trace_serialization(self) -> None:
        from app.services.reader_ask import service as service_svc

        data = service_svc._planning_snapshot_json(
            None, planner_route_used="agent_loop_first"
        )
        assert data["planner_route_used"] == "agent_loop_first"
        assert data["planner_skipped"] is True


# ---------------------------------------------------------------------------
# 6. Forced planner_first route — REMOVED in Round 15
# ---------------------------------------------------------------------------
# The TestForcedPlannerFirstStillCallsPlanner class has been removed in
# Round 15 because the legacy planner_first executable path has been deleted
# from service.py. planner_first survives only as a trace/historical value.


# ---------------------------------------------------------------------------
# 7. Tool registry invariants still hold (Round 13)
# ---------------------------------------------------------------------------


class TestToolRegistryInvariantsRound13:
    """Round 13: verify the cleanup did not break tool registry invariants."""

    def test_registry_invariants_hold(self) -> None:
        from app.agents.reader_ask_tool_registry import assert_registry_invariants

        assert_registry_invariants()

    def test_agent_callable_count_unchanged(self) -> None:
        from app.agents.reader_ask_tool_registry import agent_callable_tool_names

        names = agent_callable_tool_names()
        # Round 13 does not add or remove tools — same 9 agent-callable tools.
        assert len(names) == 9
