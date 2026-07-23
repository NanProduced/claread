"""R4-A5-8A1: R4 thinking transport + privacy (offline, no real LLM)."""

from __future__ import annotations

import json
import logging
from uuid import UUID

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ThinkingPart, ToolCallPart
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
from app.services.reader_record_ask.runtime import run_reading_record_ask
from app.services.reader_record_ask.runtime_events import (
    AnalysisFinishedEvent,
    AnalysisStartedEvent,
)
from app.services.reader_record_ask.thinking_transport import (
    BoundedThinkingObserver,
)

_USER = UUID("11111111-1111-1111-1111-111111111111")
_RECORD = UUID("22222222-2222-2222-2222-222222222222")
_BASE = UUID("33333333-3333-3333-3333-333333333333")
_DOC = UUID("44444444-4444-4444-4444-444444444444")
_SHA = "b" * 64
_SENTINEL = "SENTINEL_REASONING_PRIVATE_8a1_NEVER_USER_SURFACE"


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


def _thinking_stream_model(*, with_tool: bool = False):
    """FunctionModel that streams reasoning then answer (optionally tool)."""

    async def stream_fn(messages, info):
        yield {0: DeltaThinkingPart(content=_SENTINEL)}
        yield {0: DeltaThinkingPart(content=" more")}
        if with_tool:
            # After reasoning, emit tool then final on subsequent call.
            # For multi-step, stream_fn is called per model request.
            has_tool_return = any(
                type(p).__name__ == "ToolReturnPart"
                for m in messages
                for p in getattr(m, "parts", []) or []
            )
            if not has_tool_return:
                # Let non-stream path handle tool via function below — for
                # stream-only FunctionModel, yield text final after tool round
                # by checking message history length.
                pass
        yield json.dumps(
            {
                "answer_text": "Which aspect?",
                "cited_evidence_handles": [],
                "response_kind": "clarification",
            }
        )

    async def function(messages, info):
        has_tool_return = any(
            type(p).__name__ == "ToolReturnPart"
            for m in messages
            for p in getattr(m, "parts", []) or []
        )
        if with_tool and not has_tool_return:
            return ModelResponse(
                parts=[
                    ThinkingPart(content=_SENTINEL),
                    ToolCallPart(
                        tool_name="search_current_article",
                        args=json.dumps({"query": "x"}),
                        tool_call_id="tc1",
                    ),
                ]
            )
        return ModelResponse(
            parts=[
                ThinkingPart(content=_SENTINEL + " more"),
                TextPart(
                    content=json.dumps(
                        {
                            "answer_text": "Which aspect?",
                            "cited_evidence_handles": [],
                            "response_kind": "clarification",
                        }
                    )
                ),
            ]
        )

    return FunctionModel(
        function=function,
        stream_function=stream_fn,
        profile=ModelProfile(supports_thinking=True),
    )


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
    # Safe phase events present; no reasoning text on them.
    started = [e for e in result.events if isinstance(e, AnalysisStartedEvent)]
    finished = [e for e in result.events if isinstance(e, AnalysisFinishedEvent)]
    assert started and finished
    assert all(
        _SENTINEL not in repr(e) for e in started + finished
    )
    # Logs must not contain sentinel.
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
    """Fake reasoning sentinel must not appear on events or final answer."""
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
    # Observer holds it privately.
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
    """Progress projector emits 开始分析/分析完成 without reasoning."""
    from app.services.reader_record_ask.production_stream import _ProgressProjector
    from app.services.reader_record_ask.runtime_events import (
        AnalysisFinishedEvent,
        AnalysisStartedEvent,
    )

    projector = _ProgressProjector(started_at=0.0)
    out1 = projector.project(AnalysisStartedEvent())
    assert out1
    assert out1[-1].summary == "开始分析"
    assert _SENTINEL not in json.dumps(out1[-1].model_dump(mode="json"))
    out2 = projector.project(AnalysisFinishedEvent())
    assert out2[-1].summary == "分析完成"
    blob = json.dumps([p.model_dump(mode="json") for p in out1 + out2])
    assert "reasoning" not in blob.lower() or "开始分析" in blob
    assert _SENTINEL not in blob
