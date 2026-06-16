"""S1–S6 minimal eval scenarios for Ask Claread.

All scenarios are deterministic/offline — no real LLM calls.
Uses the conftest autouse ``fail_on_real_llm_attempts`` fixture.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.agents.reader_ask_agent import ReaderAskAgentDeps, ReaderAskRuntimeState
from app.agents.reader_ask_tool_policy import (
    ToolAvailabilityInput,
    ToolAvailabilityResult,
    build_tool_availability,
)
from app.agents.reader_ask_tool_registry import (
    TOOL_GET_RECORD_CONTEXT,
    TOOL_GET_RECORD_INSIGHTS,
    TOOL_GET_USER_VOCABULARY_BOOK,
    TOOL_PROPOSE_SAVE_HIGHLIGHT,
    TOOL_PROPOSE_SAVE_NOTE,
    TOOL_RESOLVE_KNOWN_REFERENCE,
    agent_callable_tool_names,
)
from app.agents.reader_ask_tool_runtime import run_tool
from app.schemas.reader_ask import ReaderAskAnchorRef, ReaderAskTraceSummary
from app.services.reader_ask.planner_route_policy import resolve_planner_route


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_anchor() -> ReaderAskAnchorRef:
    return ReaderAskAnchorRef(
        anchor_type="sentence",
        target_key="record:r1:sentence:s1",
        sentence_id="s1",
        selected_text="test sentence",
    )


def _make_deps(**overrides) -> ReaderAskAgentDeps:
    defaults = dict(
        payload={},
        event_queue=AsyncMock(),
        state=ReaderAskRuntimeState(),
        query_seed="test",
        task_mode="explain",
        record_id="r1",
        record_title="Test",
        primary_anchor=None,
        get_record_context_fn=AsyncMock(return_value={"summary": "Context loaded"}),
        get_record_insights_fn=AsyncMock(return_value=[]),
        get_user_vocabulary_book_fn=AsyncMock(return_value=[]),
        resolve_known_reference_fn=AsyncMock(return_value={"status": "not_found"}),
        generate_sentence_annotation_fn=AsyncMock(return_value={"status": "ok"}),
        suggest_prompts_fn=AsyncMock(return_value={"status": "warning", "summary": "No suggestions"}),
        vocabulary_item_to_citation_fn=lambda item: None,
    )
    defaults.update(overrides)
    return ReaderAskAgentDeps(**defaults)


def _default_route_kwargs(**overrides):
    """Build default kwargs for resolve_planner_route."""
    defaults = dict(
        entry_action="general",
        history_messages=[],
        attachments=[],
        anchors=[],
        cross_record_toggle=False,
        latest_user_message="What does this word mean?",
    )
    defaults.update(overrides)
    return defaults


# ===========================================================================
# S1: 当前文章普通解释
# ===========================================================================


def test_s1_route_agent_loop_first_for_general_action():
    """S1: 当前文章普通解释 — entry_action='general' defaults to agent_loop_first."""
    route = resolve_planner_route(**_default_route_kwargs(entry_action="general"))
    assert route == "agent_loop_first"


def test_s1_tool_availability_allows_all_8_tools():
    """S1: 默认输入允许全部 8 个 agent-callable 工具。"""
    inp = ToolAvailabilityInput(
        task_mode="explain",
        entry_action="ask_about_this",
        has_primary_anchor=True,
    )
    result = build_tool_availability(inp)
    expected = agent_callable_tool_names()
    assert result.allowed_tool_names == expected
    assert len(result.allowed_tool_names) == 8


# ===========================================================================
# S2: 有 anchor 的句子解释
# ===========================================================================


def test_s2_read_tools_available_with_anchor():
    """S2: 有 anchor 时 get_record_context / get_record_insights 可用。"""
    inp = ToolAvailabilityInput(
        task_mode="explain",
        entry_action="explain_this",
        has_primary_anchor=True,
    )
    result = build_tool_availability(inp)
    assert TOOL_GET_RECORD_CONTEXT in result.allowed_tool_names
    assert TOOL_GET_RECORD_INSIGHTS in result.allowed_tool_names


def test_s2_write_proposals_allowed_with_anchor():
    """S2: 有 anchor 时 write-proposal 工具可用且不在 unavailable_reasons 中。"""
    inp = ToolAvailabilityInput(
        task_mode="explain",
        entry_action="explain_this",
        has_primary_anchor=True,
    )
    result = build_tool_availability(inp)
    assert TOOL_PROPOSE_SAVE_NOTE in result.allowed_tool_names
    assert TOOL_PROPOSE_SAVE_HIGHLIGHT in result.allowed_tool_names
    assert TOOL_PROPOSE_SAVE_NOTE not in result.unavailable_reasons
    assert TOOL_PROPOSE_SAVE_HIGHLIGHT not in result.unavailable_reasons


def test_s2_write_proposals_flagged_without_anchor():
    """S2: 无 anchor 时 write-proposal 工具仍在 allowed 中但被标记在 unavailable_reasons。"""
    inp = ToolAvailabilityInput(
        task_mode="explain",
        entry_action="explain_this",
        has_primary_anchor=False,
    )
    result = build_tool_availability(inp)
    assert TOOL_PROPOSE_SAVE_NOTE in result.allowed_tool_names
    assert TOOL_PROPOSE_SAVE_HIGHLIGHT in result.allowed_tool_names
    assert result.unavailable_reasons[TOOL_PROPOSE_SAVE_NOTE] == "requires_primary_anchor"
    assert result.unavailable_reasons[TOOL_PROPOSE_SAVE_HIGHLIGHT] == "requires_primary_anchor"


# ===========================================================================
# S3: 指代但无 anchor — agent_loop_first (Round 8)
# ===========================================================================


def test_s3_deictic_without_anchor_uses_agent_loop_first():
    """S3: 指代表达 + 无 anchor → agent_loop_first (Round 8: deictic no longer triggers planner_first)。"""
    route = resolve_planner_route(
        **_default_route_kwargs(
            latest_user_message="解释这句话",
            anchors=[],
        )
    )
    assert route == "agent_loop_first"


def test_s3_deictic_with_anchor_uses_agent_loop_first():
    """S3: 指代表达 + 有 anchor → agent_loop_first（anchor 接地了指代）。"""
    route = resolve_planner_route(
        **_default_route_kwargs(
            latest_user_message="解释这句话",
            anchors=[_make_anchor()],
        )
    )
    assert route == "agent_loop_first"


def test_s3_no_deictic_no_anchor_uses_agent_loop_first():
    """S3: 无指代表达 + 无 anchor → 仍为 agent_loop_first。"""
    route = resolve_planner_route(
        **_default_route_kwargs(
            latest_user_message="What does ephemeral mean?",
            anchors=[],
        )
    )
    assert route == "agent_loop_first"


# ===========================================================================
# S4: 跨文章引用 — resolve_known_reference
# ===========================================================================


def test_s4_resolve_known_reference_in_allowed_tools():
    """S4: resolve_known_reference 在 allowed_tool_names 中。"""
    inp = ToolAvailabilityInput(
        task_mode="explain",
        entry_action="ask_about_this",
        has_primary_anchor=True,
    )
    result = build_tool_availability(inp)
    assert TOOL_RESOLVE_KNOWN_REFERENCE in result.allowed_tool_names


def test_s4_resolve_known_reference_is_agent_callable():
    """S4: resolve_known_reference 是 agent-callable 工具。"""
    assert TOOL_RESOLVE_KNOWN_REFERENCE in agent_callable_tool_names()


# ===========================================================================
# S5: 生词本查询 — get_user_vocabulary_book
# ===========================================================================


def test_s5_vocabulary_mode_includes_get_user_vocabulary_book():
    """S5: vocabulary task_mode 下 get_user_vocabulary_book 可用。"""
    inp = ToolAvailabilityInput(
        task_mode="vocabulary",
        entry_action="ask_about_this",
        has_primary_anchor=True,
    )
    result = build_tool_availability(inp)
    assert TOOL_GET_USER_VOCABULARY_BOOK in result.allowed_tool_names


def test_s5_search_user_vocabulary_removed():
    """S5: search_user_vocabulary 已在 Round 5 移除，不在 allowed_tool_names 中。"""
    inp = ToolAvailabilityInput(
        task_mode="vocabulary",
        entry_action="ask_about_this",
        has_primary_anchor=True,
    )
    result = build_tool_availability(inp)
    assert "search_user_vocabulary" not in result.allowed_tool_names


# ===========================================================================
# S6: write proposal — write gate
# ===========================================================================


def test_s6_propose_save_note_allowed_tool_runs_normally():
    """S6: propose_save_note 在 allowed 中时 run_tool 正常执行。

    注意：实际的 write gate（无 anchor → 返回 error、不消耗 budget）
    在 _propose_save_note_tool 内部实现，不在 run_tool 层。
    此处验证 run_tool 层面：当 tool 在 allowed 中时正常执行。
    Write gate 行为由 test_reader_ask_quality_assertions.py 覆盖。
    """
    deps = _make_deps(
        primary_anchor=None,
        tool_availability=ToolAvailabilityResult(
            allowed_tool_names=agent_callable_tool_names(),
            unavailable_reasons={},
        ),
    )

    async def runner() -> dict:
        return {"status": "success", "summary": "should not reach"}

    result = asyncio.run(run_tool(deps, TOOL_PROPOSE_SAVE_NOTE, runner))

    # run_tool 层面：tool 在 allowed 中，正常执行 runner
    assert deps.state.tool_call_count == 1


def test_s6_propose_save_note_with_anchor_succeeds():
    """S6: propose_save_note 有 anchor → 成功（mock runner 返回成功）。"""
    deps = _make_deps(
        primary_anchor=_make_anchor(),
        tool_availability=ToolAvailabilityResult(
            allowed_tool_names=agent_callable_tool_names(),
            unavailable_reasons={},
        ),
    )

    async def runner() -> dict:
        return {
            "status": "success",
            "summary": "Prepared save_note confirmation",
            "next_actions": ["Wait for user confirmation."],
            "artifacts": ["record:r1"],
        }

    result = asyncio.run(run_tool(deps, TOOL_PROPOSE_SAVE_NOTE, runner))
    assert result["status"] == "success"
    assert deps.state.tool_call_count == 1
    # tool_trace: started + completed
    assert len(deps.state.tool_trace) == 2
    assert deps.state.tool_trace[1].status == "completed"


def test_s6_budget_enforcement_raises_runtime_error():
    """S6: tool_call_count > max_tool_calls → RuntimeError。"""
    deps = _make_deps(
        tool_availability=ToolAvailabilityResult(
            allowed_tool_names=agent_callable_tool_names(),
            unavailable_reasons={},
        ),
    )
    deps.state.tool_call_count = 5
    deps.state.max_tool_calls = 5

    async def runner() -> dict:
        return {"status": "success", "summary": "should not reach"}

    with pytest.raises(RuntimeError, match="Tool call limit exceeded"):
        asyncio.run(run_tool(deps, TOOL_PROPOSE_SAVE_NOTE, runner))


# ===========================================================================
# _metrics_json with runtime_state — all new fields
# ===========================================================================


def test_metrics_json_includes_all_runtime_state_fields():
    """_metrics_json 在 runtime_state 存在时包含所有 Round 6 新字段。"""
    from app.services.reader_ask.service import _metrics_json

    state = ReaderAskRuntimeState(
        tool_call_count=3,
        max_tool_calls=5,
        planner_skipped=True,
        run_started_at="2026-06-16T00:00:00+00:00",
        first_token_at="2026-06-16T00:00:01+00:00",
    )
    # Add a completed trace with duration
    from app.schemas.reader_ask import ReaderAskToolTraceEntry

    state.tool_trace.append(
        ReaderAskToolTraceEntry(
            tool_name="get_record_context",
            status="completed",
            started_at="2026-06-16T00:00:00+00:00",
            completed_at="2026-06-16T00:00:00.050+00:00",
            metadata_json={"duration_ms": 50},
        )
    )
    # Add a failed trace
    state.tool_trace.append(
        ReaderAskToolTraceEntry(
            tool_name="resolve_known_reference",
            status="failed",
            started_at="2026-06-16T00:00:01+00:00",
            completed_at="2026-06-16T00:00:01.100+00:00",
        )
    )
    state.citations.append(
        __import__("app.schemas.reader_ask", fromlist=["ReaderAskCitation"]).ReaderAskCitation(
            citation_id="c1",
            kind="anchor",
            label="Test citation",
        )
    )
    state.latest_suggestions = [{"label": "More", "prompt": "Tell me more"}]
    state.action_requests.append(
        __import__("app.agents.reader_ask_agent", fromlist=["ReaderAskRuntimeActionRequest"]).ReaderAskRuntimeActionRequest(
            action_type="save_note",
            label="保存为笔记",
            description="test",
        )
    )

    trace_summary = ReaderAskTraceSummary()
    metrics = _metrics_json(
        trace_summary=trace_summary,
        billed_points=1,
        usage_event_id=None,
        planner_route="agent_loop_first",
        runtime_state=state,
    )

    # Tool metrics
    assert metrics["tool_call_count"] == 3
    assert metrics["tool_completed_count"] == 1
    assert metrics["tool_failed_count"] == 1
    assert metrics["tool_budget_exceeded"] is False
    assert metrics["tool_durations_ms"] == {"get_record_context": 50}

    # Latency
    assert metrics["run_started_at"] == "2026-06-16T00:00:00+00:00"
    assert metrics["first_token_at"] == "2026-06-16T00:00:01+00:00"
    assert metrics["ttft_ms"] == 1000

    # Output metrics
    assert metrics["citations_count"] == 1
    assert metrics["follow_up_suggestions_count"] == 1
    assert metrics["action_proposals_count"] == 1

    # Route
    assert metrics["planner_skipped"] is True


def test_eval_trace_upsert_runtime_only_metrics_preserves_existing_usage(monkeypatch):
    """runtime-only eval trace upsert 写入 Round 6 metrics，但不清空旧 usage/billing。"""
    from app.services.reader_ask import service as reader_ask_service

    usage_id = str(uuid4())
    captured: dict = {}

    async def fake_get_eval_trace(turn_run_id):  # type: ignore[no-untyped-def]
        return {
            "metrics_json": {
                "planner_mode": "direct_answer",
                "working_set_mode": "anchor_local",
                "billed_points": 9,
                "usage_event_id": usage_id,
            }
        }

    async def fake_upsert_eval_trace(**kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return kwargs

    monkeypatch.setattr(reader_ask_service.repo, "get_eval_trace", fake_get_eval_trace)
    monkeypatch.setattr(reader_ask_service.repo, "upsert_eval_trace", fake_upsert_eval_trace)

    state = ReaderAskRuntimeState()
    state.planner_route_used = "agent_loop_first"
    state.planner_skipped = True
    state.tool_call_count = 1
    state.max_tool_calls = 5
    state.run_started_at = "2026-06-16T00:00:00+00:00"
    state.first_token_at = "2026-06-16T00:00:00.250000+00:00"

    asyncio.run(
        reader_ask_service._upsert_eval_trace_record(
            turn_run_id=uuid4(),
            planning_snapshot=None,
            runtime_state=state,
            context_plan=None,
            trace_summary=None,
        )
    )

    metrics = captured["metrics_json"]
    assert metrics["planner_mode"] == "direct_answer"
    assert metrics["working_set_mode"] == "anchor_local"
    assert metrics["billed_points"] == 9
    assert metrics["usage_event_id"] == usage_id
    assert metrics["planner_route"] == "agent_loop_first"
    assert metrics["planner_skipped"] is True
    assert metrics["tool_call_count"] == 1
    assert metrics["ttft_ms"] == 250


# ===========================================================================
# Tool trace entries have duration_ms in metadata_json after run_tool
# ===========================================================================


def test_tool_trace_has_duration_ms_after_run_tool():
    """run_tool 完成后，tool_trace 的 completed 条目 metadata_json 包含 duration_ms。"""
    deps = _make_deps(
        primary_anchor=_make_anchor(),
        tool_availability=ToolAvailabilityResult(
            allowed_tool_names=agent_callable_tool_names(),
            unavailable_reasons={},
        ),
    )

    async def runner() -> dict:
        return {"summary": "Done", "next_actions": [], "artifacts": []}

    asyncio.run(run_tool(deps, TOOL_GET_RECORD_CONTEXT, runner))

    # Find the started/completed trace entries.
    started = [t for t in deps.state.tool_trace if t.status == "started"]
    completed = [t for t in deps.state.tool_trace if t.status == "completed"]
    assert len(started) == 1
    assert len(completed) == 1
    assert started[0].started_at == completed[0].started_at
    assert "duration_ms" in completed[0].metadata_json
    assert isinstance(completed[0].metadata_json["duration_ms"], int)
    assert completed[0].metadata_json["duration_ms"] >= 0
