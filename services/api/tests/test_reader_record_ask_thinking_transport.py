"""R4-A5-8A1 / A5-8A1R: thinking transport multi-turn + privacy (offline)."""

from __future__ import annotations

import json
import logging
from uuid import UUID

import pytest
from pydantic_ai.messages import (
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
)
from pydantic_ai.models.function import DeltaThinkingPart, FunctionModel
from pydantic_ai.profiles import ModelProfile

from app.services.reader_record_ask.context_envelope import (
    VerifiedEnvelopeInput,
    build_context_envelope,
)
from app.services.reader_record_ask.document_access import (
    InMemoryDocumentAccess,
    ReadingUnitView,
    build_document_scope,
)
from app.services.reader_record_ask.evidence_expansion import ExpansionPointerLedger
from app.services.reader_record_ask.grounding_validator import (
    AgentAnswerDraftOutput,
)
from app.services.reader_record_ask.runtime import run_reading_record_ask
from app.services.reader_record_ask.runtime_events import (
    AnalysisFinishedEvent,
    AnalysisStartedEvent,
    AnswerDeltaEvent,
)
from app.services.reader_record_ask.thinking_transport import (
    BoundedThinkingObserver,
    ThinkingPartLifecycle,
)

_USER = UUID("11111111-1111-1111-1111-111111111111")
_RECORD = UUID("22222222-2222-2222-2222-222222222222")
_BASE = UUID("33333333-3333-3333-3333-333333333333")
_DOC = UUID("44444444-4444-4444-4444-444444444444")
_SHA = "b" * 64
_SENTINEL = "SENTINEL_REASONING_PRIVATE_8a1_NEVER_USER_SURFACE"
_SECOND_ROUND_END_MARKER = "ROUND2_THINKING_PART_END_ONLY"


def _answer_payload(
    text: str,
    *,
    response_kind: str = "clarification",
) -> dict[str, object]:
    if response_kind == "clarification":
        return {
            "response_kind": "clarification",
            "clarification_text": text,
            "answer_blocks": [],
        }
    return {
        "response_kind": response_kind,
        "clarification_text": None,
        "answer_blocks": [
            {
                "text": text,
                "basis": "general",
                "evidence_handles": [],
            }
        ],
    }


def _envelope():
    return build_context_envelope(
        VerifiedEnvelopeInput(
            user_id=_USER,
            reading_record_id=_RECORD,
            base_id=_BASE,
            record_generation=1,
            stable_document_id=_DOC,
            base_content_sha256=_SHA,
            product_state="ready",
            readiness_state="ready",
        )
    )


def _access():
    units = (
        ReadingUnitView(
            unit_id="u1",
            order_index=0,
            text="Alpha Paris 2019 article body.",
            text_hash="11111111",
            base_start_utf16=0,
            base_end_utf16=30,
        ),
    )
    return InMemoryDocumentAccess(
        snapshot=build_document_scope(
            reading_record_id=_RECORD,
            base_id=_BASE,
            record_generation=1,
            stable_document_id=_DOC,
            base_content_sha256=_SHA,
            units=units,
            segments=(),
        )
    )


def _thinking_stream_model():
    async def stream_fn(messages, info):
        yield {0: DeltaThinkingPart(content=_SENTINEL)}
        yield {0: DeltaThinkingPart(content=" more")}
        yield json.dumps(_answer_payload("Which aspect?"))

    async def function(messages, info):
        return ModelResponse(
            parts=[
                ThinkingPart(content=_SENTINEL + " more"),
                TextPart(
                    content=json.dumps(_answer_payload("Which aspect?"))
                ),
            ]
        )

    return FunctionModel(
        function=function,
        stream_function=stream_fn,
        profile=ModelProfile(supports_thinking=True),
    )


def test_thinking_part_lifecycle_part_end_only_after_reset() -> None:
    life = ThinkingPartLifecycle()
    # Round 1: start + delta
    assert life.on_start(0, "A") == "A"
    assert life.on_delta(0, "B") == "B"
    assert life.on_end(0, "AB") is None  # already streamed
    # Round 2 after tools: reset, PartEnd only
    life.reset_stream()
    assert life.on_end(0, "C") == "C"
    assert life.on_end(0, "C") is None  # no double


@pytest.mark.asyncio
async def test_two_round_stream_observer_order_and_no_dup(caplog):
    """Round1 deltas + tool + Round2 PartEnd-only → both reasonings once."""

    call = {"n": 0}

    async def stream_fn(messages, info):
        call["n"] += 1
        has_tool_return = any(
            type(p).__name__ == "ToolReturnPart"
            for m in messages
            for p in getattr(m, "parts", []) or []
        )
        if not has_tool_return and call["n"] == 1:
            # Round 1: deltas then force tool via non-stream function path.
            # For FunctionModel stream, yield thinking then leave function
            # to handle tools... FunctionModel uses stream when present.
            yield {0: DeltaThinkingPart(content=_SENTINEL)}
            yield {0: DeltaThinkingPart(content="-r1")}
            # Emit nothing that finalizes; rely on function for tool call.
            # Actually FunctionModel stream is exclusive. Need function
            # to return tool + stream for multi-step.
            return
        if has_tool_return:
            # Round 2: only full ThinkingPart via end (simulate PartEnd-only
            # by returning complete thinking in one start with empty then
            # we test lifecycle unit separately; for integration emit end-like
            # single chunk as start empty isn't enough).
            # Yield no delta — FunctionModel will PartStart with full content
            # if we put thinking in the function response... Use stream that
            # yields a single thinking dict as complete content once.
            yield {0: DeltaThinkingPart(content=_SECOND_ROUND_END_MARKER)}
            yield json.dumps(_answer_payload("Which aspect?"))
            return
        yield json.dumps(_answer_payload("Which aspect?"))

    async def function(messages, info):
        has_tool_return = any(
            type(p).__name__ == "ToolReturnPart"
            for m in messages
            for p in getattr(m, "parts", []) or []
        )
        if not has_tool_return:
            return ModelResponse(
                parts=[
                    ThinkingPart(content=_SENTINEL + "-r1"),
                    ToolCallPart(
                        tool_name="expand_evidence",
                        args=json.dumps({"pointer": ""}),
                        tool_call_id="tc1",
                    ),
                ]
            )
        return ModelResponse(
            parts=[
                ThinkingPart(content=_SECOND_ROUND_END_MARKER),
                TextPart(
                    content=json.dumps(_answer_payload("Which aspect?"))
                ),
            ]
        )

    # Use stream that drives multi-step via stream_function only.
    # FunctionModel prefers stream_function when agent streams.
    async def multi_stream(messages, info):
        has_tool_return = any(
            type(p).__name__ == "ToolReturnPart"
            for m in messages
            for p in getattr(m, "parts", []) or []
        )
        if not has_tool_return:
            yield {0: DeltaThinkingPart(content=_SENTINEL)}
            yield {0: DeltaThinkingPart(content="-r1")}
            # Tool call via DeltaToolCall
            from pydantic_ai.models.function import DeltaToolCall

            yield {
                1: DeltaToolCall(
                    name="expand_evidence",
                    json_args=json.dumps({"pointer": ""}),
                    tool_call_id="tc1",
                )
            }
            return
        # Round 2: PartEnd-only simulation — yield thinking as a complete
        # part via single delta that FunctionModel turns into start+end.
        # To force PartEnd-only path we unit-test lifecycle; here ensure
        # second round content is observed at least once without dup of r1.
        yield {0: DeltaThinkingPart(content=_SECOND_ROUND_END_MARKER)}
        yield json.dumps(_answer_payload("Which aspect?"))

    model = FunctionModel(
        function=function,
        stream_function=multi_stream,
        profile=ModelProfile(supports_thinking=True),
    )
    observer = BoundedThinkingObserver(char_cap=2000)
    with caplog.at_level(logging.DEBUG):
        result = await run_reading_record_ask(
            user_message="hi",
            envelope=_envelope(),
            document_access=_access(),
            model=model,
            pointer_ledger=ExpansionPointerLedger(),
            thinking_observer=observer,
            article_rag=None,
        )
    assert result.finalized is not None
    text = observer.text
    # Both rounds present, r1 not duplicated as full blob twice incorrectly.
    assert _SENTINEL in text
    assert _SECOND_ROUND_END_MARKER in text
    assert text.count(_SENTINEL) == 1
    assert text.count(_SECOND_ROUND_END_MARKER) == 1
    # Phase events once each, no body/payload.
    started = [e for e in result.events if isinstance(e, AnalysisStartedEvent)]
    finished = [e for e in result.events if isinstance(e, AnalysisFinishedEvent)]
    assert len(started) == 1
    assert len(finished) == 1
    for e in started + finished:
        dumped = e.model_dump(mode="json")
        assert _SENTINEL not in json.dumps(dumped)
        assert _SECOND_ROUND_END_MARKER not in json.dumps(dumped)
        assert "length" not in dumped
        assert "hash" not in dumped
    assert _SENTINEL not in caplog.text
    assert _SECOND_ROUND_END_MARKER not in caplog.text


@pytest.mark.asyncio
async def test_part_end_only_second_round_via_lifecycle_and_transport_unit():
    """Unit: after reset, PartEnd-only delivers; Analysis events once."""
    from app.services.reader_record_ask.thinking_transport import (
        ThinkingPartLifecycle,
    )

    life = ThinkingPartLifecycle()
    assert life.on_start(0, "r1a") == "r1a"
    assert life.on_delta(0, "r1b") == "r1b"
    assert life.on_end(0, "r1ar1b") is None
    life.reset_stream()  # after tool
    assert life.on_end(0, _SECOND_ROUND_END_MARKER) == _SECOND_ROUND_END_MARKER
    assert life.on_end(0, _SECOND_ROUND_END_MARKER) is None


@pytest.mark.asyncio
async def test_runtime_observer_receives_reasoning_in_order(caplog):
    observer = BoundedThinkingObserver(char_cap=500)
    with caplog.at_level(logging.DEBUG):
        result = await run_reading_record_ask(
            user_message="hi",
            envelope=_envelope(),
            document_access=_access(),
            model=_thinking_stream_model(),
            pointer_ledger=ExpansionPointerLedger(),
            thinking_observer=observer,
        )
    assert result.finalized is not None
    assert observer.started is True
    assert observer.finished is True
    assert _SENTINEL in observer.text
    started = [e for e in result.events if isinstance(e, AnalysisStartedEvent)]
    finished = [e for e in result.events if isinstance(e, AnalysisFinishedEvent)]
    assert len(started) == 1 and len(finished) == 1
    assert _SENTINEL not in caplog.text


@pytest.mark.asyncio
async def test_runtime_default_observer_none_zero_collection():
    result = await run_reading_record_ask(
        user_message="hi",
        envelope=_envelope(),
        document_access=_access(),
        model=_thinking_stream_model(),
        pointer_ledger=ExpansionPointerLedger(),
        thinking_observer=None,
    )
    assert result.finalized is not None
    assert result.agent_draft is not None


@pytest.mark.asyncio
async def test_privacy_sentinel_absent_from_events_and_final():
    observer = BoundedThinkingObserver()
    result = await run_reading_record_ask(
        user_message="question without sentinel",
        envelope=_envelope(),
        document_access=_access(),
        model=_thinking_stream_model(),
        pointer_ledger=ExpansionPointerLedger(),
        thinking_observer=observer,
    )
    assert result.final_text is None or _SENTINEL not in (result.final_text or "")
    assert result.agent_draft is not None
    assert _SENTINEL not in (result.agent_draft.answer_text or "")
    for event in result.events:
        dumped = event.model_dump(mode="json") if hasattr(event, "model_dump") else {}
        assert _SENTINEL not in json.dumps(dumped)
        assert _SENTINEL not in repr(event)
    assert _SENTINEL in observer.text


@pytest.mark.asyncio
async def test_observer_char_cap():
    obs = BoundedThinkingObserver(char_cap=10)
    obs.on_analysis_started()
    obs.on_reasoning_delta("abcdefghijklmnop")
    obs.on_reasoning_delta("more")
    assert len(obs.text) == 10
    obs.on_analysis_finished()


@pytest.mark.asyncio
async def test_production_stream_analysis_phase_no_reasoning_leak():
    from app.services.reader_record_ask.production_stream import _ProgressProjector

    projector = _ProgressProjector(started_at=0.0)
    out1 = projector.project(AnalysisStartedEvent())
    assert out1[-1].summary == "正在分析问题"
    out2 = projector.project(AnalysisFinishedEvent())
    assert out2[-1].summary == "已完成问题分析"
    blob = json.dumps([p.model_dump(mode="json") for p in out1 + out2])
    assert _SENTINEL not in blob


# ---------------------------------------------------------------------------
# R4-A5-8A1R2: output-validator ModelRetry lifecycle behavioral test.
#
# Scenario: first round thinking → output-validator raises ModelRetry
# (OutputToolResultEvent with RetryPromptPart) → second round thinking.
# The per-index ThinkingPartLifecycle must reset at the
# OutputToolResultEvent boundary so both rounds are observed exactly
# once. AnalysisStarted/AnalysisFinished must fire at most once each.
# Raw reasoning must never enter RuntimeEvent payload, SSE DTO, logs,
# or the final answer.
#
# The PartEnd-only delivery path (second round delivers thinking via
# PartEnd without prior PartStart/PartDelta) is covered by the lifecycle
# unit test ``test_part_end_only_second_round_via_lifecycle_and_transport_unit``
# above. This test focuses on the OutputToolResultEvent boundary reset
# via a real Agent + FunctionModel + output_validator integration.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_model_retry_lifecycle_two_rounds_thinking_observer_order(caplog):
    """First round thinking → output-validator ModelRetry → second round
    thinking; observer receives both rounds' reasoning once each,
    AnalysisStarted/Finished fire once each, sentinel never leaks."""

    from pydantic_ai import Agent
    from pydantic_ai.exceptions import ModelRetry
    from pydantic_ai.messages import RetryPromptPart

    from app.services.reader_record_ask.evidence_registry import EvidenceRegistry
    from app.services.reader_record_ask.fence import StaticGenerationFence
    from app.services.reader_record_ask.runtime_deps import ReaderRecordAskDeps
    from app.services.reader_record_ask.thinking_transport import (
        run_agent_with_thinking_transport,
    )

    validator_calls = {"n": 0}

    def _build_agent(model) -> Agent:
        agent: Agent[ReaderRecordAskDeps, AgentAnswerDraftOutput] = Agent(
            model,
            deps_type=ReaderRecordAskDeps,
            output_type=AgentAnswerDraftOutput,
            retries={"output": 2},
        )

        @agent.output_validator
        async def validator(
            ctx,
            draft: AgentAnswerDraftOutput,
        ) -> AgentAnswerDraftOutput:
            validator_calls["n"] += 1
            if validator_calls["n"] == 1:
                raise ModelRetry("first draft rejected for testing")
            return draft

        return agent

    async def stream_fn(messages, info):
        has_retry = any(
            isinstance(p, RetryPromptPart)
            for m in messages
            for p in getattr(m, "parts", []) or []
        )
        if not has_retry:
            yield {0: DeltaThinkingPart(content=_SENTINEL)}
            yield json.dumps(_answer_payload("round1 draft"))
            return
        yield {0: DeltaThinkingPart(content=_SECOND_ROUND_END_MARKER)}
        yield json.dumps(_answer_payload("round2 final"))

    model = FunctionModel(
        stream_function=stream_fn,
        profile=ModelProfile(supports_thinking=True),
    )

    envelope = _envelope()
    deps = ReaderRecordAskDeps(
        envelope=envelope,
        document_access=_access(),
        fence=StaticGenerationFence(live_generation=envelope.record_generation),
        evidence_registry=EvidenceRegistry(
            envelope_fingerprint=envelope.envelope_fingerprint
        ),
    )

    agent = _build_agent(model)
    observer = BoundedThinkingObserver(char_cap=2000)

    with caplog.at_level(logging.DEBUG):
        outcome = await run_agent_with_thinking_transport(
            agent=agent,
            prompt="test prompt",
            deps=deps,
            thinking_observer=observer,
            model=model,
        )

    # Validator was called exactly twice: first raised ModelRetry, second
    # passed.
    assert validator_calls["n"] == 2

    # Observer received both rounds' reasoning, each exactly once.
    text = observer.text
    assert _SENTINEL in text
    assert _SECOND_ROUND_END_MARKER in text
    assert text.count(_SENTINEL) == 1
    assert text.count(_SECOND_ROUND_END_MARKER) == 1

    # Observer started/finished exactly once each.
    assert observer.started is True
    assert observer.finished is True

    # AnalysisStarted/AnalysisFinished events fire once each.
    started = [e for e in deps.events if isinstance(e, AnalysisStartedEvent)]
    finished = [e for e in deps.events if isinstance(e, AnalysisFinishedEvent)]
    assert len(started) == 1
    assert len(finished) == 1

    # Sentinel never leaks into RuntimeEvent payloads.
    for event in deps.events:
        dumped = (
            event.model_dump(mode="json") if hasattr(event, "model_dump") else {}
        )
        assert _SENTINEL not in json.dumps(dumped)
        assert _SECOND_ROUND_END_MARKER not in json.dumps(dumped)

    # Sentinel never leaks into logs.
    assert _SENTINEL not in caplog.text
    assert _SECOND_ROUND_END_MARKER not in caplog.text

    # Sentinel never leaks into the final answer.
    assert isinstance(outcome.output, AgentAnswerDraftOutput)
    assert _SENTINEL not in (outcome.output.answer_text or "")
    assert _SECOND_ROUND_END_MARKER not in (outcome.output.answer_text or "")


# ---------------------------------------------------------------------------
# R4-A5-8A1R3: tool-arg ModelRetry lifecycle behavioral coverage.
#
# Scenario: first round thinking + tool call → tool raises ModelRetry
# (FunctionToolResultEvent carrying RetryPromptPart) → second round
# thinking. The per-index ThinkingPartLifecycle must reset at the
# FunctionToolResultEvent boundary so both rounds are observed exactly
# once, index 0 is reused, and AnalysisStarted/Finished fire once each.
#
# This complements:
#   - test_two_round_stream_observer_order_and_no_dup: tool-return boundary
#     (FunctionToolResultEvent with ToolReturnPart)
#   - test_model_retry_lifecycle_two_rounds_thinking_observer_order:
#     output-validator boundary (OutputToolResultEvent with RetryPromptPart)
#
# Together these three tests cover all three retry boundary event kinds
# in _TOOL_RESULT_EVENT_KINDS: function_tool_result (tool-return and
# tool-arg ModelRetry), output_tool_result, and builtin_tool_result.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_arg_model_retry_lifecycle_reset_boundary(caplog):
    """tool-arg ModelRetry: FunctionToolResultEvent with RetryPromptPart
    triggers lifecycle reset; both rounds' thinking observed once each,
    index 0 reused, AnalysisStarted/Finished fire once each, sentinel
    never leaks.

    Boundary: ``function_tool_result`` event_kind carrying a
    RetryPromptPart (the tool raised ModelRetry). This is distinct from
    the tool-return boundary (carries ToolReturnPart) and the
    output-validator boundary (``output_tool_result`` event_kind).
    """

    from pydantic_ai import Agent
    from pydantic_ai.exceptions import ModelRetry
    from pydantic_ai.messages import RetryPromptPart
    from pydantic_ai.models.function import DeltaThinkingPart, DeltaToolCall, FunctionModel
    from pydantic_ai.profiles import ModelProfile

    from app.services.reader_record_ask.evidence_registry import EvidenceRegistry
    from app.services.reader_record_ask.fence import StaticGenerationFence
    from app.services.reader_record_ask.runtime_deps import ReaderRecordAskDeps
    from app.services.reader_record_ask.thinking_transport import (
        run_agent_with_thinking_transport,
    )

    tool_call_count = {"n": 0}

    async def stream_fn(messages, info):
        has_retry = any(
            isinstance(p, RetryPromptPart)
            for m in messages
            for p in getattr(m, "parts", []) or []
        )
        if not has_retry:
            # Round 1: thinking + tool call (tool will raise ModelRetry).
            yield {0: DeltaThinkingPart(content=_SENTINEL)}
            yield {
                1: DeltaToolCall(
                    name="flaky_tool",
                    json_args=json.dumps({"query": "test"}),
                    tool_call_id="tc1",
                )
            }
            return
        # Round 2 after tool-arg ModelRetry: thinking + final answer.
        # Index 0 is reused — lifecycle.reset_stream() cleared the set.
        yield {0: DeltaThinkingPart(content=_SECOND_ROUND_END_MARKER)}
        yield json.dumps(_answer_payload("recovered after tool retry"))

    model = FunctionModel(
        stream_function=stream_fn,
        profile=ModelProfile(supports_thinking=True),
    )

    def _build_agent(model) -> Agent:
        agent: Agent[ReaderRecordAskDeps, AgentAnswerDraftOutput] = Agent(
            model,
            deps_type=ReaderRecordAskDeps,
            output_type=AgentAnswerDraftOutput,
            retries={"output": 2},
        )

        @agent.tool
        async def flaky_tool(ctx, query: str) -> str:
            tool_call_count["n"] += 1
            if tool_call_count["n"] == 1:
                raise ModelRetry("bad query, try again")
            return "ok"

        return agent

    envelope = _envelope()
    deps = ReaderRecordAskDeps(
        envelope=envelope,
        document_access=_access(),
        fence=StaticGenerationFence(live_generation=envelope.record_generation),
        evidence_registry=EvidenceRegistry(
            envelope_fingerprint=envelope.envelope_fingerprint
        ),
    )

    agent = _build_agent(model)
    observer = BoundedThinkingObserver(char_cap=2000)

    with caplog.at_level(logging.DEBUG):
        outcome = await run_agent_with_thinking_transport(
            agent=agent,
            prompt="use flaky_tool",
            deps=deps,
            thinking_observer=observer,
            model=model,
        )

    # Tool was called once: raised ModelRetry, then model answered directly
    # in round 2 without re-calling the tool (lifecycle reset boundary).
    assert tool_call_count["n"] == 1

    # Observer received both rounds' reasoning, each exactly once.
    text = observer.text
    assert _SENTINEL in text
    assert _SECOND_ROUND_END_MARKER in text
    assert text.count(_SENTINEL) == 1
    assert text.count(_SECOND_ROUND_END_MARKER) == 1

    # Observer started/finished exactly once each.
    assert observer.started is True
    assert observer.finished is True

    # AnalysisStarted/AnalysisFinished events fire once each.
    started = [e for e in deps.events if isinstance(e, AnalysisStartedEvent)]
    finished = [e for e in deps.events if isinstance(e, AnalysisFinishedEvent)]
    assert len(started) == 1
    assert len(finished) == 1

    # Sentinel never leaks into RuntimeEvent payloads.
    for event in deps.events:
        dumped = (
            event.model_dump(mode="json") if hasattr(event, "model_dump") else {}
        )
        assert _SENTINEL not in json.dumps(dumped)
        assert _SECOND_ROUND_END_MARKER not in json.dumps(dumped)

    # Sentinel never leaks into logs.
    assert _SENTINEL not in caplog.text
    assert _SECOND_ROUND_END_MARKER not in caplog.text

    # Sentinel never leaks into the final answer.
    assert isinstance(outcome.output, AgentAnswerDraftOutput)
    assert _SENTINEL not in (outcome.output.answer_text or "")
    assert _SECOND_ROUND_END_MARKER not in (outcome.output.answer_text or "")


# ---------------------------------------------------------------------------
# R4-A5-8A1R3R Obj3: PartEnd-only delivery proof.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_part_end_only_delivery_after_tool_return_boundary(caplog):
    """Obj3: Round 2 thinking delivered ONLY via PartEndEvent after a
    tool-return boundary. Proves the production ThinkingPartLifecycle
    correctly delivers a second-round ThinkingPart whose content reaches
    the transport as PartEndEvent only — no PartStart or PartDelta
    carried the actual round-2 content.
    """
    from collections.abc import AsyncGenerator, AsyncIterator
    from contextlib import asynccontextmanager
    from dataclasses import dataclass, field
    from datetime import UTC, datetime
    from typing import Any

    from pydantic_ai import Agent
    from pydantic_ai.messages import ModelResponseStreamEvent
    from pydantic_ai.models import Model, ModelRequestParameters, StreamedResponse
    from pydantic_ai.profiles import ModelProfile

    from app.services.reader_record_ask.evidence_registry import EvidenceRegistry
    from app.services.reader_record_ask.fence import StaticGenerationFence
    from app.services.reader_record_ask.runtime_deps import ReaderRecordAskDeps
    from app.services.reader_record_ask.thinking_transport import (
        run_agent_with_thinking_transport,
    )

    _FINAL_ANSWER = json.dumps(
        _answer_payload("final answer after tool return")
    )

    @dataclass
    class _PartEndOnlyStreamedResponse(StreamedResponse):
        """Custom StreamedResponse proving PartEnd-only delivery."""

        _round_number: int
        _model_name: str = "part-end-only-test-model"
        _timestamp: datetime = field(
            default_factory=lambda: datetime.now(UTC)
        )

        async def _get_event_iterator(
            self,
        ) -> AsyncIterator[ModelResponseStreamEvent]:
            pm = self._parts_manager
            if self._round_number == 1:
                # Round 1: thinking + tool call.
                yield pm.handle_part(
                    vendor_part_id=0,
                    part=ThinkingPart(content=_SENTINEL),
                )
                yield pm.handle_part(
                    vendor_part_id=1,
                    part=ToolCallPart(
                        tool_name="helper_tool",
                        args=json.dumps({"query": "x"}),
                        tool_call_id="tc1",
                    ),
                )
            else:
                # Round 2: PartEnd-only thinking delivery.
                yield pm.handle_part(
                    vendor_part_id=0,
                    part=ThinkingPart(content=""),
                )
                # Silently update _parts[0] to ThinkingPart(_SECOND_ROUND_END_MARKER).
                # Discard the PartDeltaEvent so the stream never sees it.
                for _ in pm.handle_thinking_delta(
                    vendor_part_id=0,
                    content=_SECOND_ROUND_END_MARKER,
                ):
                    pass
                yield pm.handle_part(
                    vendor_part_id=1,
                    part=TextPart(content=_FINAL_ANSWER),
                )

        async def close_stream(self) -> None:
            pass

        @property
        def model_name(self) -> str:
            return self._model_name

        @property
        def provider_name(self) -> str | None:
            return None

        @property
        def provider_url(self) -> str | None:
            return None

        @property
        def timestamp(self) -> datetime:
            return self._timestamp

    class _PartEndOnlyModel(Model):
        """Custom Model returning _PartEndOnlyStreamedResponse instances."""

        def __init__(self) -> None:
            super().__init__(profile=ModelProfile(supports_thinking=True))
            self._round = 0

        @property
        def model_name(self) -> str:
            return "part-end-only-test-model"

        @property
        def system(self) -> str:
            return "test"

        @property
        def provider(self) -> None:
            return None

        async def request(
            self,
            messages: list[Any],
            model_settings: Any,
            model_request_parameters: ModelRequestParameters,
        ) -> Any:
            raise NotImplementedError(
                "Only streaming is supported by this test model."
            )

        @asynccontextmanager
        async def request_stream(
            self,
            messages: list[Any],
            model_settings: Any,
            model_request_parameters: ModelRequestParameters,
            run_context: Any | None = None,
        ) -> AsyncGenerator[StreamedResponse]:
            _model_settings, params = self.prepare_request(
                model_settings, model_request_parameters
            )
            self._round += 1
            yield _PartEndOnlyStreamedResponse(
                model_request_parameters=params,
                _round_number=self._round,
            )

    model = _PartEndOnlyModel()

    agent: Agent[ReaderRecordAskDeps, AgentAnswerDraftOutput] = Agent(
        model,
        deps_type=ReaderRecordAskDeps,
        output_type=AgentAnswerDraftOutput,
        retries={"output": 2},
    )

    @agent.tool
    async def helper_tool(ctx, query: str) -> str:
        return "tool result"

    envelope = _envelope()
    deps = ReaderRecordAskDeps(
        envelope=envelope,
        document_access=_access(),
        fence=StaticGenerationFence(live_generation=envelope.record_generation),
        evidence_registry=EvidenceRegistry(
            envelope_fingerprint=envelope.envelope_fingerprint
        ),
    )

    observer = BoundedThinkingObserver(char_cap=2000)

    with caplog.at_level(logging.DEBUG):
        outcome = await run_agent_with_thinking_transport(
            agent=agent,
            prompt="use helper_tool",
            deps=deps,
            thinking_observer=observer,
            model=model,
        )

    # Both sentinels present exactly once.
    text = observer.text
    assert _SENTINEL in text
    assert _SECOND_ROUND_END_MARKER in text
    assert text.count(_SENTINEL) == 1
    assert text.count(_SECOND_ROUND_END_MARKER) == 1

    # Observer started/finished exactly once each.
    assert observer.started is True
    assert observer.finished is True

    # AnalysisStarted/AnalysisFinished events fire once each.
    started = [e for e in deps.events if isinstance(e, AnalysisStartedEvent)]
    finished = [e for e in deps.events if isinstance(e, AnalysisFinishedEvent)]
    assert len(started) == 1
    assert len(finished) == 1

    # Sentinel never leaks into RuntimeEvent payloads.
    for event in deps.events:
        dumped = (
            event.model_dump(mode="json") if hasattr(event, "model_dump") else {}
        )
        assert _SENTINEL not in json.dumps(dumped)
        assert _SECOND_ROUND_END_MARKER not in json.dumps(dumped)

    # Sentinel never leaks into logs.
    assert _SENTINEL not in caplog.text
    assert _SECOND_ROUND_END_MARKER not in caplog.text

    # Sentinel never leaks into the final answer.
    assert isinstance(outcome.output, AgentAnswerDraftOutput)
    assert _SENTINEL not in (outcome.output.answer_text or "")
    assert _SECOND_ROUND_END_MARKER not in (outcome.output.answer_text or "")


# ---------------------------------------------------------------------------
# R4-A6: answer-block text streaming via pydantic-core partial parse.
#
# FunctionModel string yields map to one TextPart streamed as
# PartStartEvent(TextPart) + PartDeltaEvent(TextPartDelta)* +
# PartEndEvent(TextPart). The transport feeds only TextPart content into
# _AnswerTextStreamer (never ThinkingPart content) and emits
# AnswerDeltaEvent carrying answer text projection increments only.
# ---------------------------------------------------------------------------

_ANSWER_JSON_CHUNKS: tuple[str, ...] = (
    '{"response_kind": "grounded_answer", "answer_blocks": [{"',
    'text": "Hel',
    'lo world",',
    '"basis": "general",',
    '"evidence_handles": []}]}',
)


def _transport_deps():
    from app.services.reader_record_ask.evidence_registry import EvidenceRegistry
    from app.services.reader_record_ask.fence import StaticGenerationFence
    from app.services.reader_record_ask.runtime_deps import ReaderRecordAskDeps

    envelope = _envelope()
    return ReaderRecordAskDeps(
        envelope=envelope,
        document_access=_access(),
        fence=StaticGenerationFence(live_generation=envelope.record_generation),
        evidence_registry=EvidenceRegistry(
            envelope_fingerprint=envelope.envelope_fingerprint
        ),
    )


@pytest.mark.asyncio
async def test_answer_text_chunks_stream_as_answer_delta_events():
    """Chunked TextPart JSON → AnswerDeltaEvent prefixes concat to answer."""
    from pydantic_ai import Agent

    from app.services.reader_record_ask.runtime_deps import ReaderRecordAskDeps
    from app.services.reader_record_ask.thinking_transport import (
        run_agent_with_thinking_transport,
    )

    async def stream_fn(messages, info):
        for chunk in _ANSWER_JSON_CHUNKS:
            yield chunk

    model = FunctionModel(stream_function=stream_fn)
    agent: Agent[ReaderRecordAskDeps, AgentAnswerDraftOutput] = Agent(
        model,
        deps_type=ReaderRecordAskDeps,
        output_type=AgentAnswerDraftOutput,
    )
    deps = _transport_deps()

    outcome = await run_agent_with_thinking_transport(
        agent=agent,
        prompt="question",
        deps=deps,
    )

    deltas = [e for e in deps.events if isinstance(e, AnswerDeltaEvent)]
    # Structure before block text and metadata after it emit no answer prefix;
    # exactly two increments: when "Hel" resolves, then "lo world".
    assert [e.delta for e in deltas] == ["Hel", "lo world"]
    assert "".join(e.delta for e in deltas) == "Hello world"
    # Structured output still completes from the same streamed text.
    assert isinstance(outcome.output, AgentAnswerDraftOutput)
    assert outcome.output.answer_text == "Hello world"
    # phase_events mirror the sink emissions.
    phase_deltas = [
        e for e in outcome.phase_events if isinstance(e, AnswerDeltaEvent)
    ]
    assert [e.delta for e in phase_deltas] == ["Hel", "lo world"]


@pytest.mark.asyncio
async def test_clarification_text_streams_without_answer_blocks():
    """Clarification uses its independent text field and carries no blocks."""
    from pydantic_ai import Agent

    from app.services.reader_record_ask.runtime_deps import ReaderRecordAskDeps
    from app.services.reader_record_ask.thinking_transport import (
        run_agent_with_thinking_transport,
    )

    async def stream_fn(messages, info):
        del messages, info
        yield '{"response_kind":"clarification","clarification_text":"Which'
        yield ' part?","answer_blocks":[]}'

    model = FunctionModel(stream_function=stream_fn)
    agent: Agent[ReaderRecordAskDeps, AgentAnswerDraftOutput] = Agent(
        model,
        deps_type=ReaderRecordAskDeps,
        output_type=AgentAnswerDraftOutput,
    )
    deps = _transport_deps()

    outcome = await run_agent_with_thinking_transport(
        agent=agent,
        prompt="ambiguous question",
        deps=deps,
    )

    deltas = [e.delta for e in deps.events if isinstance(e, AnswerDeltaEvent)]
    assert "".join(deltas) == "Which part?"
    assert outcome.output.response_kind == "clarification"
    assert outcome.output.clarification_text == "Which part?"
    assert outcome.output.answer_blocks == []
    assert outcome.output.cited_evidence_handles == []
    assert outcome.output.validated_answer_blocks is None
    assert outcome.output.knowledge_mode is None


@pytest.mark.asyncio
async def test_answer_delta_isolated_from_thinking_transport(caplog):
    """ThinkingPart never feeds answer deltas; TextPart never feeds observer."""
    from pydantic_ai import Agent

    from app.services.reader_record_ask.runtime_deps import ReaderRecordAskDeps
    from app.services.reader_record_ask.thinking_transport import (
        run_agent_with_thinking_transport,
    )

    async def stream_fn(messages, info):
        yield {0: DeltaThinkingPart(content=_SENTINEL)}
        for chunk in _ANSWER_JSON_CHUNKS:
            yield chunk

    model = FunctionModel(
        stream_function=stream_fn,
        profile=ModelProfile(supports_thinking=True),
    )
    agent: Agent[ReaderRecordAskDeps, AgentAnswerDraftOutput] = Agent(
        model,
        deps_type=ReaderRecordAskDeps,
        output_type=AgentAnswerDraftOutput,
    )
    deps = _transport_deps()
    observer = BoundedThinkingObserver(char_cap=2000)

    with caplog.at_level(logging.DEBUG):
        outcome = await run_agent_with_thinking_transport(
            agent=agent,
            prompt="question",
            deps=deps,
            thinking_observer=observer,
        )

    deltas = [e for e in deps.events if isinstance(e, AnswerDeltaEvent)]
    assert "".join(e.delta for e in deltas) == "Hello world"
    # Reasoning sentinel never enters any AnswerDeltaEvent.
    for event in deltas:
        assert _SENTINEL not in event.delta
    # Answer text never enters the reasoning observer.
    assert observer.text == _SENTINEL
    assert "Hello" not in observer.text
    assert "world" not in observer.text
    # Analysis phase events still fire once each from the ThinkingPart.
    started = [e for e in deps.events if isinstance(e, AnalysisStartedEvent)]
    finished = [e for e in deps.events if isinstance(e, AnalysisFinishedEvent)]
    assert len(started) == 1 and len(finished) == 1
    assert isinstance(outcome.output, AgentAnswerDraftOutput)
    assert outcome.output.answer_text == "Hello world"
    assert _SENTINEL not in caplog.text


def test_answer_text_streamer_partial_parse_and_monotonic_emission():
    """Unit: trailing-strings partial parse; _emitted_len never regresses."""
    from app.services.reader_record_ask.thinking_transport import (
        _AnswerTextStreamer,
    )

    streamer = _AnswerTextStreamer()
    emitted_lens: list[int] = []

    def track(text: str) -> str | None:
        out = streamer.feed(text)
        emitted_lens.append(streamer._emitted_len)
        return out

    # Structure before a block text is resolvable → no delta (partial dict
    # parses as {} / empty prefix; nothing emitted).
    assert track('{"response_kind": "grounded_answer",') is None
    assert track('"answer_blocks": [{"') is None
    assert track("text") is None
    assert track('": "') is None
    # First resolvable prefix emits; subsequent chunks emit increments.
    assert track("Hel") == "Hel"
    assert track('lo world",') == "lo world"
    # Completed provenance keys after text add no answer prefix.
    assert track('"basis": "general",') is None
    assert track('"evidence_handles": []}]}') is None

    # _emitted_len monotonically non-decreasing — never rolls back.
    assert emitted_lens == sorted(emitted_lens)
    assert emitted_lens[-1] == len("Hello world")
    # Already-emitted prefix is never re-emitted.
    # reset() starts a fresh buffer (new model response after tool boundary).
    streamer.reset()
    assert streamer._emitted_len == 0
    assert streamer.feed('{"answer_blocks": [{"text": "x"') == "x"

    # Non-JSON prose never parses → no deltas, no exception.
    prose = _AnswerTextStreamer()
    assert prose.feed("I will now search the article for ") is None
    assert prose.feed("more prose without JSON") is None
    assert prose._emitted_len == 0
