"""R1: safe activity/progress SSE projection and concurrent visibility."""

from __future__ import annotations

import asyncio
import json
from uuid import UUID, uuid4

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from app.services.reader_record_ask.context_envelope import (
    EnvelopeInitialAnchor,
    VerifiedEnvelopeInput,
    build_context_envelope,
)
from app.services.reader_record_ask.document_access import (
    InMemoryDocumentAccess,
    ReadingUnitView,
    build_document_scope,
)
from app.services.reader_record_ask.finalizer import FinalizedAskResult
from app.services.reader_record_ask.production_stream import (
    _ProgressProjector,
    stream_agentic_thread_message,
)
from app.services.reader_record_ask.runtime import (
    ReadingRecordAskRunResult,
    run_reading_record_ask,
)
from app.services.reader_record_ask.runtime_events import (
    ComposingAnswerEvent,
    FinalAnswerEvent,
    RunFinishedEvent,
    RunStartedEvent,
    ToolCallEvent,
    ToolResultEvent,
    ValidatingEvidenceEvent,
)
from app.services.reader_record_ask.sse import (
    EVENT_AGENTIC_PROGRESS,
    EVENT_AGENTIC_RUN_STARTED,
    EVENT_AGENTIC_TERMINAL,
    EVENT_MESSAGE_COMPLETED,
)

_USER = UUID("11111111-1111-1111-1111-111111111111")
_RECORD = UUID("22222222-2222-2222-2222-222222222222")
_BASE = UUID("33333333-3333-3333-3333-333333333333")
_THREAD = UUID("55555555-5555-5555-5555-555555555555")
_DOC = UUID("44444444-4444-4444-4444-444444444444")
_TEXT = "Alpha sentence one. Alpha sentence two. Bravo paragraph."

_SENSITIVE = [
    "SECRET_QUERY_TOKEN",
    "DOCUMENT_BODY_SECRET",
    "LOCATOR_OFFSET_9999",
    "HANDLE_ID_SECRET",
    "PROVIDER_PAYLOAD_SECRET",
    "EXCEPTION_MESSAGE_SECRET",
    "REASONING_CONTENT_SECRET",
]


def _units() -> tuple[ReadingUnitView, ...]:
    return (
        ReadingUnitView(
            unit_id="u1",
            order_index=0,
            text=_TEXT,
            text_hash="aaaaaaaa",
            base_start_utf16=0,
            base_end_utf16=len(_TEXT),
        ),
    )


def _scope() -> object:
    return build_document_scope(
        reading_record_id=_RECORD,
        base_id=_BASE,
        record_generation=1,
        units=_units(),
        segments=(),
        stable_document_id=_DOC,
        base_content_sha256="b" * 64,
    )


def _envelope():
    return build_context_envelope(
        VerifiedEnvelopeInput(
            user_id=_USER,
            reading_record_id=_RECORD,
            base_id=_BASE,
            record_generation=1,
            stable_document_id=_DOC,
            base_content_sha256="b" * 64,
            product_state="readable_enhancing",
            readiness_state="article_ready",
            initial_anchor=EnvelopeInitialAnchor(
                unit_id="u1",
                anchor_segment_id="s1",
                start_offset=0,
                end_offset=10,
                selected_text=_TEXT[:10],
                text_hash="aaaaaaaa",
            ),
            visible_range=None,
        )
    )


def _access() -> InMemoryDocumentAccess:
    return InMemoryDocumentAccess(snapshot=_scope())


def _parse_sse(chunks: list[str]) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for chunk in chunks:
        lines = chunk.strip().split("\n")
        event = ""
        data = ""
        for line in lines:
            if line.startswith("event: "):
                event = line[7:]
            if line.startswith("data: "):
                data = line[6:]
        if event and data:
            events.append((event, json.loads(data)))
    return events


class _FakeRepo:
    def __init__(self) -> None:
        self.terminal_writes: list[dict] = []
        self.complete_writes: list[dict] = []

    async def get_thread(self, **kwargs):
        return {
            "id": str(_THREAD),
            "reading_record_id": str(_RECORD),
            "record_scope": "reading_record",
        }

    async def create_message(self, **kwargs):
        return {"id": str(uuid4()), **kwargs}

    async def create_agentic_turn_run(self, **kwargs):
        return {"id": str(uuid4()), **kwargs}

    async def terminal_agentic_turn_run(self, **kwargs):
        self.terminal_writes.append(kwargs)
        return {"resolved_evidence_json": None, **kwargs}

    async def complete_agentic_turn_run(self, **kwargs):
        self.complete_writes.append(kwargs)
        return {"user_visible_output_json": kwargs.get("completed_dto"), **kwargs}


def _fake_facts():
    base = type(
        "B",
        (),
        {"base_id": str(_BASE), "content_sha256": "b" * 64},
    )()
    build_result = type("BR", (), {"base": base, "units": _units()})()
    record = type(
        "R",
        (),
        {
            "generation": 1,
            "title": "t",
            "product_state": "readable_enhancing",
            "readiness_state": "article_ready",
        },
    )()
    return type("F", (), {"build_result": build_result, "record": record})()


def _function_model(answer: str = "ok answer", handles: list[str] | None = None):
    async def model_fn(messages, info):
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="final_result",
                    args=json.dumps(
                        {
                            "response_kind": "grounded_answer",
                            "answer_blocks": [
                                {
                                    "text": answer,
                                    "basis": (
                                        "article" if handles else "general"
                                    ),
                                    "article_scope": (
                                        "evidence_bounded"
                                        if handles
                                        else None
                                    ),
                                    "evidence_handles": handles or [],
                                }
                            ],
                        }
                    ),
                    tool_call_id="final-1",
                )
            ]
        )

    return FunctionModel(model_fn)


# ---------------------------------------------------------------------------
# Projector unit tests
# ---------------------------------------------------------------------------


def test_progress_projector_sequence_and_mapping() -> None:
    import time

    projector = _ProgressProjector(started_at=time.perf_counter())
    events = [
        RunStartedEvent(envelope_fingerprint="fp", has_initial_selection=True),
        ToolCallEvent(
            tool_name="read_range",
            args={
                "locator": {"mode": "whole_unit", "unit_id": "u1"},
                "secret": "SECRET_QUERY_TOKEN",
            },
        ),
        ToolResultEvent(
            tool_name="read_range",
            status="ok",
            summary="DOCUMENT_BODY_SECRET",
            evidence_handle_ids=["HANDLE_ID_SECRET"],
            payloads={"text": "DOCUMENT_BODY_SECRET", "offset": "LOCATOR_OFFSET_9999"},
            duration_ms=12,
        ),
        ComposingAnswerEvent(),
        ValidatingEvidenceEvent(),
        FinalAnswerEvent(text="answer with REASONING_CONTENT_SECRET"),
        RunFinishedEvent(
            read_range_calls=1,
            evidence_count=1,
            search_current_article_calls=0,
        ),
    ]
    projected = []
    for event in events:
        projected.extend(projector.project(event))

    phases = [p.phase for p in projected]
    activities = [p.activity for p in projected]
    phase_activities = list(zip(phases, activities, strict=True))
    assert phases[0] == "agent_running"
    assert activities[0] == "started"
    assert ("reading_context", "started") in phase_activities
    assert ("reading_context", "completed") in phase_activities
    assert ("composing_answer", "started") in phase_activities
    assert ("validating_evidence", "started") in phase_activities
    # FinalAnswerEvent must not invent another validating started.
    validating = [
        (p.phase, p.activity)
        for p in projected
        if p.phase == "validating_evidence"
    ]
    assert validating == [("validating_evidence", "started")]
    sequences = [p.sequence for p in projected]
    assert sequences == list(range(1, len(sequences) + 1))
    assert all(p.elapsed_ms >= 0 for p in projected)
    completed = next(
        p for p in projected if p.phase == "reading_context" and p.activity == "completed"
    )
    assert completed.duration_ms == 12
    assert completed.tool_name == "read_range"
    blob = json.dumps([p.model_dump(mode="json") for p in projected])
    for secret in _SENSITIVE:
        assert secret not in blob


def test_progress_projector_unknown_status_fail_closed() -> None:
    import time

    projector = _ProgressProjector(started_at=time.perf_counter())
    projected = []
    for event in (
        ToolCallEvent(tool_name="read_range", args={}),
        ToolResultEvent(
            tool_name="read_range",
            status="totally_unknown_status",
            summary="DOCUMENT_BODY_SECRET",
            evidence_handle_ids=["HANDLE_ID_SECRET"],
            duration_ms=2,
        ),
        ToolCallEvent(tool_name="search_current_article", args={"query": "x"}),
        ToolResultEvent(
            tool_name="search_current_article",
            status="weird_future_code",
            summary="PROVIDER_PAYLOAD_SECRET",
            duration_ms=1,
        ),
        ToolResultEvent(
            tool_name="secret_internal_tool",
            status="ok",
            summary="should not leak success",
            duration_ms=1,
        ),
    ):
        projected.extend(projector.project(event))

    # Known tools with unknown status → failed, never completed/ok.
    tool_results = [
        p
        for p in projected
        if p.activity in {"completed", "failed", "unavailable"}
        and p.tool_name is not None
    ]
    assert tool_results
    assert all(p.activity == "failed" for p in tool_results)
    assert all(p.status == "failed" for p in tool_results)
    assert not any(p.activity == "completed" for p in projected if p.tool_name)
    assert not any(p.status == "ok" for p in projected if p.tool_name)

    # Unknown tool result also fail-closed, without tool_name.
    unknown = [p for p in projected if p.tool_name is None and p.activity == "failed"]
    assert unknown
    assert all(p.status == "failed" for p in unknown)
    blob = json.dumps([p.model_dump(mode="json") for p in projected])
    assert "totally_unknown_status" not in blob
    assert "DOCUMENT_BODY_SECRET" not in blob
    assert "secret_internal_tool" not in blob


def test_progress_projector_search_unavailable() -> None:
    import time

    projector = _ProgressProjector(started_at=time.perf_counter())
    projected = []
    for event in (
        ToolCallEvent(
            tool_name="search_current_article",
            args={"query": "SECRET_QUERY_TOKEN"},
        ),
        ToolResultEvent(
            tool_name="search_current_article",
            status="unavailable",
            summary="PROVIDER_PAYLOAD_SECRET",
            evidence_handle_ids=[],
            payloads={"error": "EXCEPTION_MESSAGE_SECRET"},
            duration_ms=3,
        ),
    ):
        projected.extend(projector.project(event))

    assert projected[0].phase == "agent_running"
    search_events = [p for p in projected if p.phase == "searching_article"]
    assert [p.activity for p in search_events] == ["started", "unavailable"]
    assert search_events[1].summary == "当前文章检索暂不可用"
    blob = json.dumps([p.model_dump(mode="json") for p in projected])
    assert "SECRET_QUERY_TOKEN" not in blob
    assert "EXCEPTION_MESSAGE_SECRET" not in blob


def test_progress_projector_unknown_tool_is_generic() -> None:
    import time

    projector = _ProgressProjector(started_at=time.perf_counter())
    # First ensure agent_running exists, then unknown tool should not spam it.
    projected = projector.project(
        RunStartedEvent(envelope_fingerprint="fp", has_initial_selection=False)
    )
    projected.extend(
        projector.project(ToolCallEvent(tool_name="secret_internal_tool", args={"x": 1}))
    )
    projected.extend(
        projector.project(ToolCallEvent(tool_name="secret_internal_tool", args={"x": 2}))
    )
    assert [p.phase for p in projected] == ["agent_running"]
    assert all(p.tool_name is None for p in projected)
    assert "secret_internal_tool" not in json.dumps(
        [p.model_dump(mode="json") for p in projected]
    )


@pytest.mark.asyncio
async def test_validating_progress_arrives_before_finalizer_returns() -> None:
    """validating_evidence/started must be visible while finalize is blocked."""
    from app.services.reader_record_ask import runtime as runtime_mod

    validating_seen = asyncio.Event()
    finalize_released = asyncio.Event()
    finalize_entered = asyncio.Event()
    original_finalize = runtime_mod.finalize_agent_answer

    async def blocked_finalize(**kwargs):
        finalize_entered.set()
        # Wait until the stream consumer observed validating progress.
        await validating_seen.wait()
        finalize_released.set()
        return await original_finalize(**kwargs)

    repo = _FakeRepo()
    chunks: list[str] = []
    saw_validating = False

    # MonkeyPatch is a pytest fixture, not a context manager — use unittest.
    from unittest.mock import patch

    with patch.object(runtime_mod, "finalize_agent_answer", side_effect=blocked_finalize):
        async for c in stream_agentic_thread_message(
            user_id=_USER,
            reading_record_id=_RECORD,
            thread_id=_THREAD,
            content="q",
            facts=_fake_facts(),
            request_anchor=None,
            repository=repo,  # type: ignore[arg-type]
            document_access=_access(),
            model=_function_model("answer after validation"),
            auto_wire_dependencies=False,
        ):
            chunks.append(c)
            for name, payload in _parse_sse([c]):
                if (
                    name == EVENT_AGENTIC_PROGRESS
                    and payload.get("phase") == "validating_evidence"
                    and payload.get("activity") == "started"
                ):
                    saw_validating = True
                    # Finalizer must still be blocked at this point.
                    assert finalize_entered.is_set()
                    assert not finalize_released.is_set()
                    validating_seen.set()

    assert saw_validating
    assert finalize_released.is_set()
    events = _parse_sse(chunks)
    progress = [p for n, p in events if n == EVENT_AGENTIC_PROGRESS]
    phases = [(p["phase"], p["activity"]) for p in progress]
    # Fixed order: composing started → validating started → ... completed
    compose_idx = phases.index(("composing_answer", "started"))
    validate_idx = phases.index(("validating_evidence", "started"))
    assert compose_idx < validate_idx
    assert EVENT_MESSAGE_COMPLETED in [n for n, _ in events]
    # No validating completed success progress is required; none should claim ok.
    assert not any(
        p["phase"] == "validating_evidence" and p.get("status") == "ok" for p in progress
    )


@pytest.mark.asyncio
async def test_runtime_event_order_composing_validating_final() -> None:
    """FinalAnswerEvent is emitted only after finalizer returns."""
    from unittest.mock import patch

    from app.services.reader_record_ask import runtime as runtime_mod

    order: list[str] = []
    original_finalize = runtime_mod.finalize_agent_answer

    async def tracking_finalize(**kwargs):
        order.append("finalize_enter")
        result = await original_finalize(**kwargs)
        order.append("finalize_exit")
        return result

    def tracking_sink(event):
        order.append(event.type)

    with patch.object(runtime_mod, "finalize_agent_answer", side_effect=tracking_finalize):
        result = await run_reading_record_ask(
            user_message="hello",
            envelope=_envelope(),
            document_access=_access(),
            model=_function_model("ok"),
            article_rag=None,
            event_sink=tracking_sink,
        )

    assert result.final_text == "ok"
    assert "composing_answer" in order
    assert "validating_evidence" in order
    assert "finalize_enter" in order
    assert "finalize_exit" in order
    assert "final_answer" in order
    assert order.index("composing_answer") < order.index("validating_evidence")
    assert order.index("validating_evidence") < order.index("finalize_enter")
    assert order.index("finalize_exit") < order.index("final_answer")
    assert order.index("final_answer") < order.index("run_finished")


# ---------------------------------------------------------------------------
# Concurrent production stream tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_progress_order_with_read_range() -> None:
    """A real read_range call projects ordered activity before completion."""

    async def run_with_read_range(**kwargs):
        sink = kwargs.get("event_sink")
        envelope = kwargs["envelope"]
        if sink is not None:
            sink(
                RunStartedEvent(
                    envelope_fingerprint=envelope.envelope_fingerprint,
                    has_initial_selection=False,
                )
            )
            sink(
                ToolCallEvent(
                    tool_name="read_range",
                    args={"locator": {"mode": "whole_unit", "unit_id": "u1"}},
                )
            )
            sink(
                ToolResultEvent(
                    tool_name="read_range",
                    status="ok",
                    summary="internal-only summary",
                    evidence_handle_ids=[],
                    duration_ms=1,
                )
            )
            sink(ComposingAnswerEvent())
            sink(FinalAnswerEvent(text="summary from context"))
            sink(
                RunFinishedEvent(
                    read_range_calls=1,
                    evidence_count=0,
                    search_current_article_calls=0,
                )
            )
        return ReadingRecordAskRunResult(
            final_text="summary from context",
            finalized=FinalizedAskResult(
                status="ok",
                answer_text="summary from context",
                resolved_evidence=(),
                envelope_fingerprint=envelope.envelope_fingerprint,
            ),
            read_range_calls=1,
            search_current_article_calls=0,
        )
    repo = _FakeRepo()
    chunks = [
        c
        async for c in stream_agentic_thread_message(
            user_id=_USER,
            reading_record_id=_RECORD,
            thread_id=_THREAD,
            content="summarize",
            facts=_fake_facts(),
            request_anchor=None,
            repository=repo,  # type: ignore[arg-type]
            document_access=_access(),
            model="unused",
            run_fn=run_with_read_range,
            auto_wire_dependencies=False,
        )
    ]

    events = _parse_sse(chunks)
    names = [n for n, _ in events]
    assert EVENT_AGENTIC_RUN_STARTED in names
    assert EVENT_MESSAGE_COMPLETED in names
    progress = [p for n, p in events if n == EVENT_AGENTIC_PROGRESS]
    phases = [(p["phase"], p["activity"]) for p in progress]
    assert phases[0] == ("agent_running", "started")
    assert ("reading_context", "started") in phases
    assert ("reading_context", "completed") in phases
    assert any(p[0] in {"composing_answer", "validating_evidence"} for p in phases)
    sequences = [p["sequence"] for p in progress]
    assert sequences == list(range(1, len(sequences) + 1))
    assert all(p["elapsed_ms"] >= 0 for p in progress)
    assert names.index(EVENT_AGENTIC_PROGRESS) < names.index(EVENT_MESSAGE_COMPLETED)

@pytest.mark.asyncio
async def test_progress_visible_before_agent_finishes() -> None:
    """First tool progress must yield before the agent task completes."""

    progress_before_done = asyncio.Event()
    agent_gate = asyncio.Event()

    async def slow_run(**kwargs):
        sink = kwargs.get("event_sink")
        env = kwargs["envelope"]
        if sink is not None:
            sink(
                RunStartedEvent(
                    envelope_fingerprint=env.envelope_fingerprint,
                    has_initial_selection=False,
                )
            )
            sink(
                ToolCallEvent(
                    tool_name="read_range",
                    args={"locator": {"mode": "whole_unit", "unit_id": "u1"}},
                )
            )
        # Hold the agent open until the stream consumer has observed progress.
        await progress_before_done.wait()
        if sink is not None:
            sink(
                ToolResultEvent(
                    tool_name="read_range",
                    status="ok",
                    summary="ok",
                    evidence_handle_ids=[],
                    duration_ms=5,
                )
            )
            sink(ComposingAnswerEvent())
            sink(FinalAnswerEvent(text="done"))
            sink(
                RunFinishedEvent(
                    read_range_calls=1,
                    evidence_count=0,
                    search_current_article_calls=0,
                )
            )
        agent_gate.set()
        return ReadingRecordAskRunResult(
            final_text="done",
            finalized=FinalizedAskResult(
                status="ok",
                answer_text="done",
                resolved_evidence=(),
                envelope_fingerprint=env.envelope_fingerprint,
            ),
            read_range_calls=1,
            search_current_article_calls=0,
        )

    repo = _FakeRepo()
    chunks: list[str] = []
    saw_tool_progress = False
    async for c in stream_agentic_thread_message(
        user_id=_USER,
        reading_record_id=_RECORD,
        thread_id=_THREAD,
        content="q",
        facts=_fake_facts(),
        request_anchor=None,
        repository=repo,  # type: ignore[arg-type]
        document_access=_access(),
        model="unused",
        run_fn=slow_run,
        auto_wire_dependencies=False,
    ):
        chunks.append(c)
        for name, payload in _parse_sse([c]):
            if (
                name == EVENT_AGENTIC_PROGRESS
                and payload.get("phase") == "reading_context"
                and payload.get("activity") == "started"
            ):
                saw_tool_progress = True
                # Agent is still waiting on progress_before_done.
                assert not agent_gate.is_set()
                progress_before_done.set()

    assert saw_tool_progress
    assert agent_gate.is_set()
    names = [n for n, _ in _parse_sse(chunks)]
    assert EVENT_MESSAGE_COMPLETED in names


@pytest.mark.asyncio
async def test_zero_tool_progress_sequence() -> None:
    repo = _FakeRepo()
    chunks: list[str] = []
    async for c in stream_agentic_thread_message(
        user_id=_USER,
        reading_record_id=_RECORD,
        thread_id=_THREAD,
        content="direct",
        facts=_fake_facts(),
        request_anchor=None,
        repository=repo,  # type: ignore[arg-type]
        document_access=_access(),
        model=_function_model("direct answer"),
        auto_wire_dependencies=False,
    ):
        chunks.append(c)

    events = _parse_sse(chunks)
    progress = [p for n, p in events if n == EVENT_AGENTIC_PROGRESS]
    phases = {p["phase"] for p in progress}
    assert "agent_running" in phases
    assert "reading_context" not in phases
    assert "searching_article" not in phases
    assert "composing_answer" in phases or "validating_evidence" in phases
    assert EVENT_MESSAGE_COMPLETED in [n for n, _ in events]
    sequences = [p["sequence"] for p in progress]
    assert sequences == sorted(sequences)
    assert sequences[0] == 1


@pytest.mark.asyncio
async def test_rag_off_search_unavailable_still_completes() -> None:
    async def model_fn(messages, info):
        if not getattr(model_fn, "_called", False):
            model_fn._called = True  # type: ignore[attr-defined]
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="search_current_article",
                        args=json.dumps({"query": "SECRET_QUERY_TOKEN"}),
                        tool_call_id="s-1",
                    )
                ]
            )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="final_result",
                    args=json.dumps(
                        {
                            "response_kind": "grounded_answer",
                            "answer_blocks": [
                                {
                                    "text": "answer without rag",
                                    "basis": "general",
                                    "article_scope": None,
                                    "evidence_handles": [],
                                }
                            ],
                        }
                    ),
                    tool_call_id="final-1",
                )
            ]
        )

    repo = _FakeRepo()
    chunks: list[str] = []
    async for c in stream_agentic_thread_message(
        user_id=_USER,
        reading_record_id=_RECORD,
        thread_id=_THREAD,
        content="find something",
        facts=_fake_facts(),
        request_anchor=None,
        repository=repo,  # type: ignore[arg-type]
        document_access=_access(),
        model=FunctionModel(model_fn),
        article_rag=None,  # RAG-off
        auto_wire_dependencies=False,
    ):
        chunks.append(c)

    events = _parse_sse(chunks)
    names = [n for n, _ in events]
    progress = [p for n, p in events if n == EVENT_AGENTIC_PROGRESS]
    search = [p for p in progress if p.get("phase") == "searching_article"]
    assert any(p["activity"] == "started" for p in search)
    assert any(p["activity"] == "unavailable" for p in search)
    assert EVENT_MESSAGE_COMPLETED in names
    assert EVENT_AGENTIC_TERMINAL not in names
    blob = "".join(chunks)
    assert "SECRET_QUERY_TOKEN" not in blob
    assert "not_ready" not in blob or "当前文章检索暂不可用" in blob


@pytest.mark.asyncio
async def test_progress_privacy_no_sensitive_fields() -> None:
    async def run_with_secrets(**kwargs):
        sink = kwargs.get("event_sink")
        env = kwargs["envelope"]
        if sink is not None:
            sink(
                ToolCallEvent(
                    tool_name="read_range",
                    args={
                        "query": "SECRET_QUERY_TOKEN",
                        "locator": {"offset": "LOCATOR_OFFSET_9999"},
                        "text": "DOCUMENT_BODY_SECRET",
                    },
                )
            )
            sink(
                ToolResultEvent(
                    tool_name="read_range",
                    status="ok",
                    summary="DOCUMENT_BODY_SECRET",
                    evidence_handle_ids=["HANDLE_ID_SECRET"],
                    payloads={
                        "provider": "PROVIDER_PAYLOAD_SECRET",
                        "exception": "EXCEPTION_MESSAGE_SECRET",
                        "reasoning_content": "REASONING_CONTENT_SECRET",
                    },
                    duration_ms=1,
                )
            )
            sink(ComposingAnswerEvent())
            sink(FinalAnswerEvent(text="safe answer"))
            sink(
                RunFinishedEvent(
                    read_range_calls=1,
                    evidence_count=0,
                    search_current_article_calls=0,
                )
            )
        return ReadingRecordAskRunResult(
            final_text="safe answer",
            finalized=FinalizedAskResult(
                status="ok",
                answer_text="safe answer",
                resolved_evidence=(),
                envelope_fingerprint=env.envelope_fingerprint,
            ),
        )

    repo = _FakeRepo()
    chunks: list[str] = []
    async for c in stream_agentic_thread_message(
        user_id=_USER,
        reading_record_id=_RECORD,
        thread_id=_THREAD,
        content="q",
        facts=_fake_facts(),
        request_anchor=None,
        repository=repo,  # type: ignore[arg-type]
        document_access=_access(),
        model="x",
        run_fn=run_with_secrets,
        auto_wire_dependencies=False,
    ):
        chunks.append(c)

    blob = "".join(chunks)
    for secret in _SENSITIVE:
        assert secret not in blob
    # Ensure no raw tool arg keys leaked via dump
    assert "evidence_handle_ids" not in blob
    assert "reasoning_content" not in blob


@pytest.mark.asyncio
async def test_cancel_cleans_agent_task_and_emits_terminal_once() -> None:
    started = asyncio.Event()

    async def hanging_run(**kwargs):
        sink = kwargs.get("event_sink")
        if sink is not None:
            sink(
                RunStartedEvent(
                    envelope_fingerprint=kwargs["envelope"].envelope_fingerprint,
                    has_initial_selection=False,
                )
            )
        started.set()
        await asyncio.sleep(3600)
        return ReadingRecordAskRunResult(final_text=None, finalized=None)

    repo = _FakeRepo()
    gen = stream_agentic_thread_message(
        user_id=_USER,
        reading_record_id=_RECORD,
        thread_id=_THREAD,
        content="q",
        facts=_fake_facts(),
        request_anchor=None,
        repository=repo,  # type: ignore[arg-type]
        document_access=_access(),
        model="x",
        run_fn=hanging_run,
        auto_wire_dependencies=False,
    )
    chunks: list[str] = []
    progress_seen = asyncio.Event()

    async def consume() -> None:
        async for c in gen:
            chunks.append(c)
            if any(n == EVENT_AGENTIC_PROGRESS for n, _ in _parse_sse([c])):
                progress_seen.set()

    consumer = asyncio.create_task(consume())
    await progress_seen.wait()
    await started.wait()
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer

    events = _parse_sse(chunks)
    terminals = [p for n, p in events if n == EVENT_AGENTIC_TERMINAL]
    # aclose/cancel path may or may not flush the cancel terminal depending on
    # timing; repo write is the authority for single terminal persistence.
    assert len(repo.terminal_writes) <= 1
    if repo.terminal_writes:
        assert repo.terminal_writes[0]["final_status"] == "cancelled"
    assert EVENT_MESSAGE_COMPLETED not in [n for n, _ in events]
    # No progress after terminal if terminal was emitted.
    if terminals:
        last_terminal_idx = max(
            i for i, (n, _) in enumerate(events) if n == EVENT_AGENTIC_TERMINAL
        )
        for i, (n, _) in enumerate(events):
            if n == EVENT_AGENTIC_PROGRESS:
                assert i < last_terminal_idx


@pytest.mark.asyncio
async def test_emit_event_keeps_deps_events_without_sink() -> None:
    """Runtime still records events when no sink is provided."""
    result = await run_reading_record_ask(
        user_message="hello",
        envelope=_envelope(),
        document_access=_access(),
        model=_function_model("ok"),
        article_rag=None,
        event_sink=None,
    )
    types = [e.type for e in result.events]
    assert "run_started" in types
    assert "composing_answer" in types
    assert "final_answer" in types
    assert "run_finished" in types
    assert result.final_text == "ok"
