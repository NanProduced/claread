"""Round 2 follow-up suggestions payload roundtrip.

Verifies that ``suggest_prompts`` tool output flows all the way through:
  agent tool state  →  ``ReaderAskFollowUpSuggestion`` schema  →
  ``ReaderAskUserVisibleOutput.follow_up_suggestions``  →
  ``ReaderAskMessage.follow_up_suggestions``  →  ``visible_output_from_message``
  →  ``to_completed_payload``.

The frontend reads this field off the completed payload; if it is missing
or not normalized, the chips never render.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from app.agents.reader_ask_agent import (
    ReaderAskAgentDeps,
    ReaderAskRuntimeState,
    _suggest_prompts_tool,
)
from app.schemas.reader_ask import (
    ReaderAskCitation,
    ReaderAskFollowUpSuggestion,
    ReaderAskMessage,
    ReaderAskResolvedContextSummary,
)


# ---------------------------------------------------------------------------
# Schema: ReaderAskFollowUpSuggestion
# ---------------------------------------------------------------------------


def test_follow_up_suggestion_schema_basic() -> None:
    s = ReaderAskFollowUpSuggestion(label="Next", prompt="Tell me more")
    assert s.label == "Next"
    assert s.prompt == "Tell me more"


# ---------------------------------------------------------------------------
# Agent state → schema roundtrip
# ---------------------------------------------------------------------------


def test_suggest_prompts_tool_state_roundtrip() -> None:
    """``_suggest_prompts_tool`` writes the cleaned suggestions to
    ``state.latest_suggestions`` and they survive the round-trip
    through ``ReaderAskFollowUpSuggestion`` validation."""
    from app.agents.reader_ask_tool_registry import TOOL_SUGGEST_PROMPTS
    from app.agents.reader_ask_tool_runtime import run_tool

    captured: list[dict] = []

    async def capture_fn(suggestions: list[dict]) -> dict:
        captured.extend(suggestions)
        return {"status": "success", "suggestions": suggestions}

    event_queue = AsyncMock()
    state = ReaderAskRuntimeState()
    deps = ReaderAskAgentDeps(
        payload={},
        event_queue=event_queue,
        state=state,
        query_seed="test",
        task_mode="general",
        record_id="r1",
        record_title="Test",
        primary_anchor=None,
        get_record_context_fn=AsyncMock(return_value={}),
        get_record_insights_fn=AsyncMock(return_value=[]),
        get_user_vocabulary_book_fn=AsyncMock(return_value=[]),
        resolve_known_reference_fn=AsyncMock(return_value={"status": "not_found"}),
        load_explicit_attachment_context_fn=AsyncMock(return_value={"status": "not_found", "ok": False}),
        generate_sentence_annotation_fn=AsyncMock(return_value=None),
        suggest_prompts_fn=capture_fn,
        vocabulary_item_to_citation_fn=MagicMock(),
    )
    ctx = MagicMock()
    ctx.deps = deps

    suggestions = [
        {"label": "Review 翻译", "prompt": "把这句再细化一下"},
        {"label": "Save as note", "prompt": "保存这段到笔记"},
    ]
    result = asyncio.run(
        _suggest_prompts_tool(ctx, suggestions=suggestions)
    )

    # Tool returns the captured suggestions verbatim.
    assert result["status"] == "success"
    assert len(result["suggestions"]) == 2

    # State was populated and round-trips through the Pydantic schema.
    assert len(state.latest_suggestions) == 2
    parsed = [ReaderAskFollowUpSuggestion.model_validate(s) for s in state.latest_suggestions]
    assert [p.label for p in parsed] == ["Review 翻译", "Save as note"]


# ---------------------------------------------------------------------------
# User visible output: follow_up_suggestions field
# ---------------------------------------------------------------------------


def test_user_visible_output_accepts_follow_up_suggestions() -> None:
    from app.services.reader_ask.output_contract import build_user_visible_output

    output = build_user_visible_output(
        content_md="answer",
        submission_mode="chat",
        resolved_intent=None,
        citations=[],
        action_proposals=[],
        tool_trace=[],
        evidence=[],
        trace_summary=None,
        disambiguation=None,
        external_asset_disambiguation=None,
        response_cards=[],
        usage_summary=None,
        billed_points=0,
        resolved_context=ReaderAskResolvedContextSummary(
            record_id="r1",
            record_title="T",
            anchors=[],
        ),
        context_plan=None,
        resolved_context_input=None,
        run_info=None,
        supplement_candidates=[],
        persisted_supplements=[],
        follow_up_suggestions=[
            ReaderAskFollowUpSuggestion(label="L1", prompt="P1"),
            ReaderAskFollowUpSuggestion(label="L2", prompt="P2"),
        ],
    )
    assert output.follow_up_suggestions is not None
    assert len(output.follow_up_suggestions) == 2
    assert output.follow_up_suggestions[0].label == "L1"


def test_user_visible_output_default_follow_up_suggestions_is_none() -> None:
    from app.services.reader_ask.output_contract import build_user_visible_output

    output = build_user_visible_output(
        content_md="answer",
        submission_mode="chat",
        resolved_intent=None,
        citations=[],
        action_proposals=[],
        tool_trace=[],
        evidence=[],
        trace_summary=None,
        disambiguation=None,
        external_asset_disambiguation=None,
        response_cards=[],
        usage_summary=None,
        billed_points=0,
        resolved_context=ReaderAskResolvedContextSummary(
            record_id="r1",
            record_title="T",
            anchors=[],
        ),
        context_plan=None,
        resolved_context_input=None,
        run_info=None,
        supplement_candidates=[],
        persisted_supplements=[],
    )
    # Tool not called → field stays None so the frontend can omit chips.
    assert output.follow_up_suggestions is None


def test_user_visible_output_accepts_dict_suggestions() -> None:
    """The output contract must accept raw dicts (not just the Pydantic
    model) so the service layer doesn't need to pre-validate every
    suggestion before passing through."""
    from app.services.reader_ask.output_contract import build_user_visible_output

    output = build_user_visible_output(
        content_md="answer",
        submission_mode="chat",
        resolved_intent=None,
        citations=[],
        action_proposals=[],
        tool_trace=[],
        evidence=[],
        trace_summary=None,
        disambiguation=None,
        external_asset_disambiguation=None,
        response_cards=[],
        usage_summary=None,
        billed_points=0,
        resolved_context=ReaderAskResolvedContextSummary(
            record_id="r1",
            record_title="T",
            anchors=[],
        ),
        context_plan=None,
        resolved_context_input=None,
        run_info=None,
        supplement_candidates=[],
        persisted_supplements=[],
        follow_up_suggestions=[
            {"label": "L1", "prompt": "P1"},
            {"label": "L2", "prompt": "P2"},
        ],
    )
    assert output.follow_up_suggestions is not None
    assert output.follow_up_suggestions[0].label == "L1"


# ---------------------------------------------------------------------------
# to_completed_payload / visible_output_from_message
# ---------------------------------------------------------------------------


def test_to_completed_payload_propagates_follow_up_suggestions() -> None:
    from app.services.reader_ask.output_contract import (
        build_user_visible_output,
        to_completed_payload,
    )

    output = build_user_visible_output(
        content_md="answer",
        submission_mode="chat",
        resolved_intent=None,
        citations=[],
        action_proposals=[],
        tool_trace=[],
        evidence=[],
        trace_summary=None,
        disambiguation=None,
        external_asset_disambiguation=None,
        response_cards=[],
        usage_summary=None,
        billed_points=0,
        resolved_context=ReaderAskResolvedContextSummary(
            record_id="r1",
            record_title="T",
            anchors=[],
        ),
        context_plan=None,
        resolved_context_input=None,
        run_info=None,
        supplement_candidates=[],
        persisted_supplements=[],
        follow_up_suggestions=[
            ReaderAskFollowUpSuggestion(label="L", prompt="P"),
        ],
    )
    payload = to_completed_payload(
        message_id="m1", thread_id="t1", output=output,
    )
    assert payload.follow_up_suggestions is not None
    assert payload.follow_up_suggestions[0].label == "L"


def test_visible_output_from_message_propagates_follow_up_suggestions() -> None:
    """End-to-end: a message carrying ``follow_up_suggestions`` must
    round-trip through ``visible_output_from_message`` so the
    completed SSE payload contains the chips."""
    from app.services.reader_ask.output_contract import visible_output_from_message

    now = datetime.now(timezone.utc).isoformat()
    message = ReaderAskMessage(
        id="m1",
        thread_id="t1",
        role="assistant",
        status="completed",
        content_md="answer",
        submission_mode="chat",
        citations=[],
        follow_up_suggestions=[
            ReaderAskFollowUpSuggestion(label="L", prompt="P"),
        ],
        resolved_context=ReaderAskResolvedContextSummary(
            record_id="r1",
            record_title="T",
            anchors=[],
        ),
        created_at=now,
        updated_at=now,
    )
    out = visible_output_from_message(message, {})
    assert out["follow_up_suggestions"] is not None
    assert out["follow_up_suggestions"][0]["label"] == "L"


def test_visible_output_from_message_no_suggestions_yields_null() -> None:
    from app.services.reader_ask.output_contract import visible_output_from_message

    now = datetime.now(timezone.utc).isoformat()
    message = ReaderAskMessage(
        id="m1",
        thread_id="t1",
        role="assistant",
        status="completed",
        content_md="answer",
        submission_mode="chat",
        citations=[],
        resolved_context=ReaderAskResolvedContextSummary(
            record_id="r1",
            record_title="T",
            anchors=[],
        ),
        created_at=now,
        updated_at=now,
    )
    out = visible_output_from_message(message, {})
    # Tool not called → null so the frontend omits the chip row.
    assert out["follow_up_suggestions"] is None


# ---------------------------------------------------------------------------
# Service-level integration: _build_user_visible_output must propagate
# runtime_state.latest_suggestions at the final-stream and retry callsites.
# Without this the completed SSE payload drops the chips even when the
# tool was called and the state was populated.
# ---------------------------------------------------------------------------


def _make_resolved_context() -> ReaderAskResolvedContextSummary:
    return ReaderAskResolvedContextSummary(
        record_id="r1",
        record_title="T",
        anchors=[],
    )


def test_build_user_visible_output_thread_call_carries_suggestions() -> None:
    """The first stream call (stream_thread_message) must read
    ``runtime_state.latest_suggestions`` into the completed output."""
    from app.services.reader_ask import service as svc

    state = ReaderAskRuntimeState()
    state.latest_suggestions = [
        {"label": "Review 翻译", "prompt": "把这句再细化一下"},
        {"label": "Save as note", "prompt": "保存到笔记"},
    ]
    output = svc._build_user_visible_output(  # noqa: SLF001
        content_md="answer",
        submission_mode="chat",
        resolved_intent=None,
        citations=[],
        action_proposals=[],
        tool_trace=[],
        evidence=[],
        trace_summary=None,
        disambiguation=None,
        external_asset_disambiguation=None,
        response_cards=[],
        usage_summary=None,
        billed_points=0,
        resolved_context=_make_resolved_context(),
        context_plan=None,
        resolved_context_input=None,
        run_info=None,
        supplement_candidates=[],
        persisted_supplements=[],
        follow_up_suggestions=state.latest_suggestions or None,
    )
    assert output.follow_up_suggestions is not None
    assert len(output.follow_up_suggestions) == 2
    assert output.follow_up_suggestions[0].label == "Review 翻译"


def test_build_user_visible_output_no_suggestions_yields_null() -> None:
    """When ``runtime_state.latest_suggestions`` is empty, the field
    must be ``None`` (not an empty list) so the frontend omits the
    chip row."""
    from app.services.reader_ask import service as svc

    state = ReaderAskRuntimeState()
    # No tool was called → state.latest_suggestions is [].
    output = svc._build_user_visible_output(  # noqa: SLF001
        content_md="answer",
        submission_mode="chat",
        resolved_intent=None,
        citations=[],
        action_proposals=[],
        tool_trace=[],
        evidence=[],
        trace_summary=None,
        disambiguation=None,
        external_asset_disambiguation=None,
        response_cards=[],
        usage_summary=None,
        billed_points=0,
        resolved_context=_make_resolved_context(),
        context_plan=None,
        resolved_context_input=None,
        run_info=None,
        supplement_candidates=[],
        persisted_supplements=[],
        follow_up_suggestions=state.latest_suggestions or None,
    )
    assert output.follow_up_suggestions is None


def test_completed_payload_via_to_completed_payload_carries_suggestions() -> None:
    """End-to-end: a fully built user-visible output flows into the
    completed SSE payload with chips intact."""
    from app.services.reader_ask import service as svc

    state = ReaderAskRuntimeState()
    state.latest_suggestions = [
        {"label": "L1", "prompt": "P1"},
        {"label": "L2", "prompt": "P2"},
    ]
    output = svc._build_user_visible_output(  # noqa: SLF001
        content_md="answer",
        submission_mode="chat",
        resolved_intent=None,
        citations=[],
        action_proposals=[],
        tool_trace=[],
        evidence=[],
        trace_summary=None,
        disambiguation=None,
        external_asset_disambiguation=None,
        response_cards=[],
        usage_summary=None,
        billed_points=0,
        resolved_context=_make_resolved_context(),
        context_plan=None,
        resolved_context_input=None,
        run_info=None,
        supplement_candidates=[],
        persisted_supplements=[],
        follow_up_suggestions=state.latest_suggestions or None,
    )
    payload = svc._build_completed_payload(  # noqa: SLF001
        message_id="m1", thread_id="t1", output=output,
    )
    assert payload.follow_up_suggestions is not None
    assert [s.label for s in payload.follow_up_suggestions] == ["L1", "L2"]


def test_to_completed_payload_no_suggestions_yields_null() -> None:
    """No-suggestion completed payload must serialize the field as
    null (omitted in the wire JSON) so the frontend does not render
    an empty chip row."""
    from app.services.reader_ask import service as svc

    state = ReaderAskRuntimeState()
    output = svc._build_user_visible_output(  # noqa: SLF001
        content_md="answer",
        submission_mode="chat",
        resolved_intent=None,
        citations=[],
        action_proposals=[],
        tool_trace=[],
        evidence=[],
        trace_summary=None,
        disambiguation=None,
        external_asset_disambiguation=None,
        response_cards=[],
        usage_summary=None,
        billed_points=0,
        resolved_context=_make_resolved_context(),
        context_plan=None,
        resolved_context_input=None,
        run_info=None,
        supplement_candidates=[],
        persisted_supplements=[],
        follow_up_suggestions=state.latest_suggestions or None,
    )
    payload = svc._build_completed_payload(  # noqa: SLF001
        message_id="m1", thread_id="t1", output=output,
    )
    # Pydantic serializes None fields as null in the wire payload.
    serialized = payload.model_dump(mode="json")
    assert serialized["follow_up_suggestions"] is None


def test_suggest_prompts_tool_state_to_completed_payload_roundtrip() -> None:
    """Full chain: ``_suggest_prompts_tool`` writes state, then
    ``_build_user_visible_output`` + ``_build_completed_payload``
    carry it into the completed payload."""
    from app.agents.reader_ask_tool_runtime import run_tool
    from app.services.reader_ask import service as svc

    captured: list[dict] = []

    async def capture_fn(suggestions: list[dict]) -> dict:
        captured.extend(suggestions)
        return {"status": "success", "suggestions": suggestions}

    event_queue = AsyncMock()
    state = ReaderAskRuntimeState()
    deps = ReaderAskAgentDeps(
        payload={},
        event_queue=event_queue,
        state=state,
        query_seed="test",
        task_mode="general",
        record_id="r1",
        record_title="Test",
        primary_anchor=None,
        get_record_context_fn=AsyncMock(return_value={}),
        get_record_insights_fn=AsyncMock(return_value=[]),
        get_user_vocabulary_book_fn=AsyncMock(return_value=[]),
        resolve_known_reference_fn=AsyncMock(return_value={"status": "not_found"}),
        load_explicit_attachment_context_fn=AsyncMock(return_value={"status": "not_found", "ok": False}),
        generate_sentence_annotation_fn=AsyncMock(return_value=None),
        suggest_prompts_fn=capture_fn,
        vocabulary_item_to_citation_fn=MagicMock(),
    )
    ctx = MagicMock()
    ctx.deps = deps

    suggestions = [
        {"label": "L1", "prompt": "P1"},
        {"label": "L2", "prompt": "P2"},
    ]
    result = asyncio.run(
        _suggest_prompts_tool(ctx, suggestions=suggestions)
    )
    assert result["status"] == "success"

    # Now build the completed payload the way the service layer does.
    output = svc._build_user_visible_output(  # noqa: SLF001
        content_md="answer",
        submission_mode="chat",
        resolved_intent=None,
        citations=[],
        action_proposals=[],
        tool_trace=state.tool_trace,
        evidence=[],
        trace_summary=None,
        disambiguation=None,
        external_asset_disambiguation=None,
        response_cards=[],
        usage_summary=None,
        billed_points=0,
        resolved_context=_make_resolved_context(),
        context_plan=None,
        resolved_context_input=None,
        run_info=None,
        supplement_candidates=[],
        persisted_supplements=[],
        follow_up_suggestions=state.latest_suggestions or None,
    )
    payload = svc._build_completed_payload(  # noqa: SLF001
        message_id="m1", thread_id="t1", output=output,
    )
    assert payload.follow_up_suggestions is not None
    assert len(payload.follow_up_suggestions) == 2
    # The wire payload must carry the chips; this is what the SSE
    # ``message.completed`` event delivers to the frontend.
    serialized = payload.model_dump(mode="json")
    assert serialized["follow_up_suggestions"] is not None
    assert serialized["follow_up_suggestions"][0]["label"] == "L1"
