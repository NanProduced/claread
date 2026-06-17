"""Round 14 regression tests: replan de-plannerization (agent-loop repair).

These tests verify that the live ``agent_loop_first`` path no longer calls
``planner_runtime.resolve_semantic_planning()`` when the main answer is
degenerate — instead, a single agent-loop repair attempt is made using
``run_reader_ask_replan`` (same answer agent + replan model route) with a
repair hint injected into the prompt payload.

Coverage:
1. Degenerate answer triggers repair, not planner.
2. Repair success uses repair content.
3. Repair failure keeps original, no second repair, no planner.
4. Repair exception keeps original, no planner.
5. Non-degenerate answer does not trigger repair.
6. Interrupted answer does not trigger repair.
7. Repair telemetry fields appear in ``_metrics_json``.
8. ``planner_route_used`` stays ``"agent_loop_first"`` after repair.
9. ``retry_thread_message`` live route also repairs instead of planning.

Round 15 update: the forced ``planner_first`` legacy path
(``TestForcedPlannerFirstReplanUnchanged``) has been removed. There is no
longer an executable branch that reaches
``planner_runtime.resolve_semantic_planning``; ``planner_first`` survives
only as a trace/historical value. The ``resolve_semantic_planning``
references that remain in this file are mock attributes used solely for
``assert_not_called`` regression assertions on the fully-mocked
``planner_runtime_svc``.

All tests use mocks — no real LLM is called.
"""

from __future__ import annotations

import contextlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.schemas.reader_ask import ReaderAskPageIdentity

# Reuse Round 13 test helpers (same mock wiring pattern).
from tests.test_reader_ask_round13_regression import (
    _anchor,
    _collect_sse_events,
    _configure_common_mocks,
    _make_record_bundle,
    _make_model_option,
    _patch_service_boundaries,
)


# ---------------------------------------------------------------------------
# Stream helper: build a fake stream that yields a degenerate main answer
# ---------------------------------------------------------------------------


def _make_degenerate_stream_factory(*, content_md: str = "", interrupted: bool = False):
    """Return a side_effect for ``stream_reader_ask_agent_run`` that yields
    a single ``ReaderAskStreamCompleted`` with the given content_md.
    """
    from app.services.reader_ask.agent_invocation import (
        ReaderAskStreamCompleted,
        ReaderAskStreamSseEvent,
    )
    from app.services.reader_ask.agent_runner import AgentStreamOutcome

    async def _fake_stream(*args, **kwargs):
        # Yield a minimal SSE event so the stream is non-empty. Use a
        # plain encoded SSE string (no payload builder needed).
        yield ReaderAskStreamSseEvent(encoded_sse="event: message.delta\ndata: {}\n\n")
        yield ReaderAskStreamCompleted(
            outcome=AgentStreamOutcome(
                content_md=content_md,
                usage_summary=None,
                interrupted=interrupted,
                interruption_detail="test interruption" if interrupted else None,
            ),
            stream_runtime=MagicMock(
                emitted_reasoning=None,
                reasoning_started=False,
                first_token_at=None,
            ),
        )
    return _fake_stream


def _build_stream_body(record_id):
    from app.services.reader_ask import service as service_svc
    return service_svc.ReaderAskMessageStreamRequest(
        content="这篇文章想表达什么？",
        page_identity=ReaderAskPageIdentity(record_id=str(record_id)),
        attachments=[],
        entry_action="ask_about_this",
    )


def _setup_common_mocks_for_repair(mocks, *, record, thread_id, record_id, model_option):
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
    # Override prepare_prompt_payload to return a proper 4-tuple so the
    # main flow and repair helper can unpack (payload, max_output,
    # compaction_audit, context_too_large). The default
    # _configure_common_mocks mock returns a MagicMock which is not
    # unpackable into 4 values.
    mocks["prompt_prep"].prepare_prompt_payload = MagicMock(
        return_value=({}, 1024, {}, False),
    )
    mocks["prompt_prep"].compute_max_input_budget = MagicMock(return_value=8192)
    mocks["prompt_prep"].should_emit_compacting = MagicMock(return_value=False)
    mocks["prompt_prep"].inject_compaction_audit = MagicMock(
        return_value=MagicMock(
            planner_mode="direct_answer",
            reference_resolution_status="not_needed",
            working_set_mode="anchor_local",
            used_known_reference_resolution=False,
            used_external_record_context=False,
            used_structured_asset_lookup=False,
            used_hitp_disambiguation=False,
            used_external_asset_context=False,
            used_external_asset_disambiguation=False,
            supplement_generation_used=False,
            supplement_persisted_count=0,
            supplement_deleted_count=0,
            cross_record_context_allowed=False,
            cross_record_context_used=False,
            tool_steps=[],
            notes=[],
        ),
    )


def _apply_repair_test_patches(service_svc):
    """Return additional patches needed for repair tests.

    These patch real functions that the main flow and repair helper call
    directly (not via service_svc attributes), so they must be patched at
    their source module.
    """
    return {
        # build_prompt_payload is called by both the main flow and the
        # repair helper; return a plain dict so repair_hint can be injected.
        "build_prompt_payload": patch.object(
            service_svc.runtime_contract_svc, "build_prompt_payload",
            return_value={},
        ),
        # build_reader_ask_agent_deps constructs ReaderAskAgentDeps with
        # tool availability; mock it to avoid needing real tool callbacks.
        "build_agent_deps": patch.object(
            service_svc, "build_reader_ask_agent_deps",
            return_value=MagicMock(),
        ),
    }


# ---------------------------------------------------------------------------
# 1-6. stream_thread_message repair behavior
# ---------------------------------------------------------------------------


class TestAgentLoopRepairOnDegenerateAnswer:
    """Round 14: degenerate answers on the agent_loop_first live route
    trigger a single agent-loop repair, not ``resolve_semantic_planning``.
    """

    @pytest.mark.asyncio
    async def test_degenerate_answer_triggers_repair_not_planner(self) -> None:
        from app.services.reader_ask import service as service_svc

        user_id = uuid4()
        thread_id = uuid4()
        record_id = uuid4()
        record = _make_record_bundle(record_id)
        model_option = _make_model_option()
        body = _build_stream_body(record_id)

        p = _patch_service_boundaries(service_svc)
        # Add a patch for run_reader_ask_replan (the repair entry point).
        p["run_replan"] = patch.object(
            service_svc, "run_reader_ask_replan", new_callable=AsyncMock,
            return_value="repair content that is substantive enough",
        )
        # Add patches for real functions called by main flow + repair helper.
        p.update(_apply_repair_test_patches(service_svc))

        with contextlib.ExitStack() as stack:
            mocks = {k: stack.enter_context(v) for k, v in p.items()}
            _setup_common_mocks_for_repair(
                mocks, record=record, thread_id=thread_id,
                record_id=record_id, model_option=model_option,
            )
            # Main answer is degenerate (empty).
            mocks["stream_run"].side_effect = _make_degenerate_stream_factory(content_md="")

            events = await _collect_sse_events(
                service_svc.stream_thread_message(user_id, thread_id, body)
            )

            # Planner MUST NOT be called.
            mocks["planner_runtime"].resolve_semantic_planning.assert_not_called()
            # Repair (run_reader_ask_replan) MUST be called exactly once.
            mocks["run_replan"].assert_called_once()

    @pytest.mark.asyncio
    async def test_repair_success_uses_repair_content(self) -> None:
        from app.services.reader_ask import service as service_svc

        user_id = uuid4()
        thread_id = uuid4()
        record_id = uuid4()
        record = _make_record_bundle(record_id)
        model_option = _make_model_option()
        body = _build_stream_body(record_id)
        repair_content = "This is a substantive repaired answer about the article."

        p = _patch_service_boundaries(service_svc)
        p["run_replan"] = patch.object(
            service_svc, "run_reader_ask_replan", new_callable=AsyncMock,
            return_value=repair_content,
        )
        p.update(_apply_repair_test_patches(service_svc))

        with contextlib.ExitStack() as stack:
            mocks = {k: stack.enter_context(v) for k, v in p.items()}
            _setup_common_mocks_for_repair(
                mocks, record=record, thread_id=thread_id,
                record_id=record_id, model_option=model_option,
            )
            mocks["stream_run"].side_effect = _make_degenerate_stream_factory(content_md="")

            events = await _collect_sse_events(
                service_svc.stream_thread_message(user_id, thread_id, body)
            )

            # The repair content should be used as the final answer.
            # The output_contract mock captures content_md; verify it was
            # called with the repair content.
            output_calls = mocks["output_contract"].build_user_visible_output.call_args_list
            assert any(
                call.kwargs.get("content_md") == repair_content
                for call in output_calls
            ), f"Expected repair content {repair_content!r} in output calls: {output_calls}"

    @pytest.mark.asyncio
    async def test_repair_failure_keeps_original_no_second_repair(self) -> None:
        from app.services.reader_ask import service as service_svc

        user_id = uuid4()
        thread_id = uuid4()
        record_id = uuid4()
        record = _make_record_bundle(record_id)
        model_option = _make_model_option()
        body = _build_stream_body(record_id)

        p = _patch_service_boundaries(service_svc)
        # Repair also returns degenerate content.
        p["run_replan"] = patch.object(
            service_svc, "run_reader_ask_replan", new_callable=AsyncMock,
            return_value="",  # still degenerate
        )
        p.update(_apply_repair_test_patches(service_svc))

        with contextlib.ExitStack() as stack:
            mocks = {k: stack.enter_context(v) for k, v in p.items()}
            _setup_common_mocks_for_repair(
                mocks, record=record, thread_id=thread_id,
                record_id=record_id, model_option=model_option,
            )
            mocks["stream_run"].side_effect = _make_degenerate_stream_factory(content_md="")

            events = await _collect_sse_events(
                service_svc.stream_thread_message(user_id, thread_id, body)
            )

            # Repair called exactly once (no second attempt).
            mocks["run_replan"].assert_called_once()
            # Planner NOT called.
            mocks["planner_runtime"].resolve_semantic_planning.assert_not_called()

    @pytest.mark.asyncio
    async def test_repair_exception_keeps_original_no_planner(self) -> None:
        from app.services.reader_ask import service as service_svc

        user_id = uuid4()
        thread_id = uuid4()
        record_id = uuid4()
        record = _make_record_bundle(record_id)
        model_option = _make_model_option()
        body = _build_stream_body(record_id)

        p = _patch_service_boundaries(service_svc)
        p["run_replan"] = patch.object(
            service_svc, "run_reader_ask_replan", new_callable=AsyncMock,
            side_effect=RuntimeError("repair exploded"),
        )
        p.update(_apply_repair_test_patches(service_svc))

        with contextlib.ExitStack() as stack:
            mocks = {k: stack.enter_context(v) for k, v in p.items()}
            _setup_common_mocks_for_repair(
                mocks, record=record, thread_id=thread_id,
                record_id=record_id, model_option=model_option,
            )
            mocks["stream_run"].side_effect = _make_degenerate_stream_factory(content_md="")

            # Should not raise — repair exception is caught.
            events = await _collect_sse_events(
                service_svc.stream_thread_message(user_id, thread_id, body)
            )

            mocks["run_replan"].assert_called_once()
            mocks["planner_runtime"].resolve_semantic_planning.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_degenerate_answer_no_repair(self) -> None:
        from app.services.reader_ask import service as service_svc

        user_id = uuid4()
        thread_id = uuid4()
        record_id = uuid4()
        record = _make_record_bundle(record_id)
        model_option = _make_model_option()
        body = _build_stream_body(record_id)

        p = _patch_service_boundaries(service_svc)
        p["run_replan"] = patch.object(
            service_svc, "run_reader_ask_replan", new_callable=AsyncMock,
            return_value="should not be called",
        )
        p.update(_apply_repair_test_patches(service_svc))

        with contextlib.ExitStack() as stack:
            mocks = {k: stack.enter_context(v) for k, v in p.items()}
            _setup_common_mocks_for_repair(
                mocks, record=record, thread_id=thread_id,
                record_id=record_id, model_option=model_option,
            )
            # Main answer is non-degenerate.
            mocks["stream_run"].side_effect = _make_degenerate_stream_factory(
                content_md="This is a valid substantive answer about the article."
            )

            events = await _collect_sse_events(
                service_svc.stream_thread_message(user_id, thread_id, body)
            )

            mocks["run_replan"].assert_not_called()
            mocks["planner_runtime"].resolve_semantic_planning.assert_not_called()

    @pytest.mark.asyncio
    async def test_interrupted_answer_no_repair(self) -> None:
        from app.services.reader_ask import service as service_svc

        user_id = uuid4()
        thread_id = uuid4()
        record_id = uuid4()
        record = _make_record_bundle(record_id)
        model_option = _make_model_option()
        body = _build_stream_body(record_id)

        p = _patch_service_boundaries(service_svc)
        p["run_replan"] = patch.object(
            service_svc, "run_reader_ask_replan", new_callable=AsyncMock,
            return_value="should not be called",
        )
        p.update(_apply_repair_test_patches(service_svc))

        with contextlib.ExitStack() as stack:
            mocks = {k: stack.enter_context(v) for k, v in p.items()}
            _setup_common_mocks_for_repair(
                mocks, record=record, thread_id=thread_id,
                record_id=record_id, model_option=model_option,
            )
            # Main answer is degenerate AND interrupted.
            mocks["stream_run"].side_effect = _make_degenerate_stream_factory(
                content_md="", interrupted=True,
            )

            events = await _collect_sse_events(
                service_svc.stream_thread_message(user_id, thread_id, body)
            )

            # Repair MUST NOT run on interrupted answers.
            mocks["run_replan"].assert_not_called()


# ---------------------------------------------------------------------------
# 7-8. Repair telemetry
# ---------------------------------------------------------------------------


class TestAgentLoopRepairTelemetry:
    """Round 14: repair telemetry fields appear in ``_metrics_json`` and
    ``planner_route_used`` stays ``"agent_loop_first"`` after repair."""

    def test_repair_fields_in_metrics_json(self) -> None:
        from app.services.reader_ask import service as service_svc
        from app.agents.reader_ask_agent import ReaderAskRuntimeState

        runtime_state = ReaderAskRuntimeState()
        runtime_state.repair_attempted = True
        runtime_state.repair_reason = "degenerate_answer"
        runtime_state.repair_succeeded = True
        runtime_state.repair_route = "agent_loop_repair"
        runtime_state.planner_route_used = "agent_loop_first"
        runtime_state.planner_skipped = True

        data = service_svc._metrics_json(
            trace_summary=None,
            billed_points=5,
            usage_event_id=None,
            planner_route="agent_loop_first",
            degenerate_detected=True,
            degenerate_reason="degenerate_answer",
            runtime_state=runtime_state,
        )

        assert data["repair_attempted"] is True
        assert data["repair_reason"] == "degenerate_answer"
        assert data["repair_succeeded"] is True
        assert data["repair_route"] == "agent_loop_repair"
        # planner_route_used semantics preserved.
        assert data["planner_route"] == "agent_loop_first"
        assert data["planner_skipped"] is True

    def test_repair_fields_default_when_no_repair(self) -> None:
        from app.services.reader_ask import service as service_svc
        from app.agents.reader_ask_agent import ReaderAskRuntimeState

        runtime_state = ReaderAskRuntimeState()
        data = service_svc._metrics_json(
            trace_summary=None,
            billed_points=0,
            usage_event_id=None,
            runtime_state=runtime_state,
        )

        assert data["repair_attempted"] is False
        assert data["repair_reason"] is None
        assert data["repair_succeeded"] is False
        assert data["repair_route"] is None

    def test_repair_does_not_override_planner_route_in_snapshot(self) -> None:
        """``_planning_snapshot_json`` must keep ``planner_route_used``
        as ``"agent_loop_first"`` even after a repair attempt."""
        from app.services.reader_ask import service as service_svc

        data = service_svc._planning_snapshot_json(
            None, planner_route_used="agent_loop_first",
        )
        assert data["planner_route_used"] == "agent_loop_first"
        assert data["planner_skipped"] is True


# ---------------------------------------------------------------------------
# 9. Forced planner_first legacy path — REMOVED in Round 15
# ---------------------------------------------------------------------------
# The TestForcedPlannerFirstReplanUnchanged class has been removed in
# Round 15 because the legacy planner_first executable path has been deleted
# from service.py. planner_first survives only as a trace/historical value.


# ---------------------------------------------------------------------------
# 10. retry_thread_message live route also repairs
# ---------------------------------------------------------------------------


class TestRetryThreadMessageRepair:
    """Round 14: ``retry_thread_message`` live route also repairs instead
    of calling ``resolve_semantic_planning``."""

    @pytest.mark.asyncio
    async def test_retry_degenerate_triggers_repair_not_planner(self) -> None:
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

        p = _patch_service_boundaries(service_svc)
        p["run_replan"] = patch.object(
            service_svc, "run_reader_ask_replan", new_callable=AsyncMock,
            return_value="repair content that is substantive enough",
        )
        p.update(_apply_repair_test_patches(service_svc))

        with contextlib.ExitStack() as stack:
            mocks = {k: stack.enter_context(v) for k, v in p.items()}
            _setup_common_mocks_for_repair(
                mocks, record=record, thread_id=thread_id,
                record_id=record_id, model_option=model_option,
            )
            mocks["repo"].get_message = AsyncMock(return_value=assistant_msg)
            mocks["repo"].list_messages = AsyncMock(return_value=[user_msg, assistant_msg])
            # Main answer is degenerate.
            mocks["stream_run"].side_effect = _make_degenerate_stream_factory(content_md="")

            events = await _collect_sse_events(
                service_svc.retry_thread_message(user_id, thread_id, message_id)
            )

            mocks["planner_runtime"].resolve_semantic_planning.assert_not_called()
            mocks["run_replan"].assert_called_once()


# ---------------------------------------------------------------------------
# 11. Repair runtime state merge — citations/tool_trace from repair enter
#     the completed payload (P1 fix).
# ---------------------------------------------------------------------------


class TestAgentLoopRepairRuntimeStateMerge:
    """Round 14 P1 fix: when repair succeeds, the citations / tool_trace /
    suggestions produced during the repair run must replace the stale
    evidence from the degenerate run in the completed payload.
    """

    @pytest.mark.asyncio
    async def test_repair_citations_and_tool_trace_enter_completed_payload(self) -> None:
        """Repair run produces citations + tool_trace via tool calls; the
        completed payload must carry the repair's evidence, not the
        degenerate run's empty evidence."""
        from app.agents.reader_ask_agent import ReaderAskRuntimeState
        from app.schemas.reader_ask import (
            ReaderAskCitation,
            ReaderAskToolTraceEntry,
        )
        from app.services.reader_ask import service as service_svc

        user_id = uuid4()
        thread_id = uuid4()
        record_id = uuid4()
        record = _make_record_bundle(record_id)
        model_option = _make_model_option()
        body = _build_stream_body(record_id)
        repair_content = "Repaired answer with proper evidence from get_record_context."

        # The repair run will produce these via tool calls.
        repair_citation = ReaderAskCitation(
            citation_id="repair-cit-1",
            kind="anchor",
            label="repair sentence",
            sentence_id="s42",
            selected_text="repair selected text",
        )
        repair_tool_trace = ReaderAskToolTraceEntry(
            tool_name="get_record_context",
            status="completed",
            summary="repair run fetched paragraph context",
        )

        async def _repair_side_effect(*, replan_deps, **kwargs):
            # Simulate the repair agent calling get_record_context and
            # producing a citation + tool_trace entry in the repair state.
            repair_state = replan_deps.state
            assert isinstance(repair_state, ReaderAskRuntimeState)
            repair_state.citations.append(repair_citation)
            repair_state.tool_trace.append(repair_tool_trace)
            repair_state.latest_suggestions = [
                {"label": "repair suggestion", "prompt": "repair prompt?"}
            ]
            return repair_content

        p = _patch_service_boundaries(service_svc)
        p["run_replan"] = patch.object(
            service_svc, "run_reader_ask_replan", new_callable=AsyncMock,
            side_effect=_repair_side_effect,
        )
        p.update(_apply_repair_test_patches(service_svc))
        # Override build_reader_ask_agent_deps to return a deps-like object
        # whose .state is the real repair_runtime_state constructed by
        # _run_agent_loop_repair (passed through from build_reader_ask_agent_deps).
        # The default _apply_repair_test_patches mock returns MagicMock() which
        # has .state as a MagicMock — but _run_agent_loop_repair passes
        # state=repair_runtime_state into build_reader_ask_agent_deps, so we
        # need a mock that captures and exposes that state.
        def _build_deps_capture_state(*, payload, state, **kwargs):
            deps = MagicMock()
            deps.state = state
            deps.payload = payload
            return deps
        p["build_agent_deps"] = patch.object(
            service_svc, "build_reader_ask_agent_deps",
            side_effect=_build_deps_capture_state,
        )

        with contextlib.ExitStack() as stack:
            mocks = {k: stack.enter_context(v) for k, v in p.items()}
            _setup_common_mocks_for_repair(
                mocks, record=record, thread_id=thread_id,
                record_id=record_id, model_option=model_option,
            )
            # Main answer is degenerate (empty) — no citations/tool_trace.
            mocks["stream_run"].side_effect = _make_degenerate_stream_factory(content_md="")

            events = await _collect_sse_events(
                service_svc.stream_thread_message(user_id, thread_id, body)
            )

            # The output_contract mock captures runtime_state.citations and
            # runtime_state.tool_trace via _build_user_visible_output.
            output_calls = mocks["output_contract"].build_user_visible_output.call_args_list
            assert output_calls, "expected at least one build_user_visible_output call"

            # Find the call that used the repair content (the final
            # successful output). The degenerate run produces content_md=""
            # so the repair content call is the one we want.
            repair_output_call = None
            for call in output_calls:
                if call.kwargs.get("content_md") == repair_content:
                    repair_output_call = call
                    break
            assert repair_output_call is not None, (
                f"expected a build_user_visible_output call with repair content "
                f"{repair_content!r}; got content_md values: "
                f"{[c.kwargs.get('content_md') for c in output_calls]}"
            )

            # The citations passed to the output must include the repair
            # citation, proving the repair runtime state was merged.
            citations_arg = repair_output_call.kwargs.get("citations")
            assert citations_arg is not None
            assert any(
                getattr(c, "citation_id", None) == "repair-cit-1"
                for c in citations_arg
            ), f"expected repair citation in output; got: {citations_arg}"

            # The tool_trace passed to the output must include the repair
            # tool_trace entry.
            tool_trace_arg = repair_output_call.kwargs.get("tool_trace")
            assert tool_trace_arg is not None
            assert any(
                getattr(t, "tool_name", None) == "get_record_context"
                for t in tool_trace_arg
            ), f"expected repair tool_trace in output; got: {tool_trace_arg}"

            # The follow_up_suggestions must come from the repair state.
            suggestions_arg = repair_output_call.kwargs.get("follow_up_suggestions")
            assert suggestions_arg is not None
            assert any(
                s.get("label") == "repair suggestion" for s in suggestions_arg
            ), f"expected repair suggestions in output; got: {suggestions_arg}"

    @pytest.mark.asyncio
    async def test_repair_failure_does_not_merge_stale_state(self) -> None:
        """When repair fails (still degenerate), the original runtime_state
        evidence is preserved — no merge happens."""
        from app.agents.reader_ask_agent import ReaderAskRuntimeState
        from app.schemas.reader_ask import ReaderAskCitation
        from app.services.reader_ask import service as service_svc

        user_id = uuid4()
        thread_id = uuid4()
        record_id = uuid4()
        record = _make_record_bundle(record_id)
        model_option = _make_model_option()
        body = _build_stream_body(record_id)

        # The repair run produces a citation but returns degenerate content.
        # The citation must NOT be merged because the repair failed.
        stale_repair_citation = ReaderAskCitation(
            citation_id="stale-repair-cit",
            kind="anchor",
            label="stale repair sentence",
        )

        async def _repair_side_effect(*, replan_deps, **kwargs):
            repair_state = replan_deps.state
            repair_state.citations.append(stale_repair_citation)
            return ""  # still degenerate

        p = _patch_service_boundaries(service_svc)
        p["run_replan"] = patch.object(
            service_svc, "run_reader_ask_replan", new_callable=AsyncMock,
            side_effect=_repair_side_effect,
        )
        p.update(_apply_repair_test_patches(service_svc))
        # Override build_reader_ask_agent_deps to expose real state (same
        # as the success test — see comment there).
        def _build_deps_capture_state(*, payload, state, **kwargs):
            deps = MagicMock()
            deps.state = state
            deps.payload = payload
            return deps
        p["build_agent_deps"] = patch.object(
            service_svc, "build_reader_ask_agent_deps",
            side_effect=_build_deps_capture_state,
        )

        with contextlib.ExitStack() as stack:
            mocks = {k: stack.enter_context(v) for k, v in p.items()}
            _setup_common_mocks_for_repair(
                mocks, record=record, thread_id=thread_id,
                record_id=record_id, model_option=model_option,
            )
            mocks["stream_run"].side_effect = _make_degenerate_stream_factory(content_md="")

            events = await _collect_sse_events(
                service_svc.stream_thread_message(user_id, thread_id, body)
            )

            # Repair was attempted but failed (still degenerate).
            mocks["run_replan"].assert_called_once()
            # The stale repair citation must NOT appear in any output call.
            output_calls = mocks["output_contract"].build_user_visible_output.call_args_list
            for call in output_calls:
                citations_arg = call.kwargs.get("citations") or []
                assert not any(
                    getattr(c, "citation_id", None) == "stale-repair-cit"
                    for c in citations_arg
                ), f"stale repair citation must not appear in failed-repair output: {citations_arg}"
