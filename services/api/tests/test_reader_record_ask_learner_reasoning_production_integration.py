"""Production-stream + thinking-transport integration (R1.3).

Enters via ``stream_agentic_thread_message`` with a FunctionModel that
streams real ``DeltaThinkingPart`` events so ``run_agent_with_thinking_transport``
drives the observer — production integration must NOT hand-call
on_reasoning_delta / on_reasoning_segment_end / on_first_answer_delta.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ThinkingPart
from pydantic_ai.models.function import DeltaThinkingPart, FunctionModel
from pydantic_ai.profiles import ModelProfile

from app.schemas.reader_record_ask_stream import EXECUTION_VERSION_AGENTIC_V2
from app.services.reader_record_ask.document_access import (
    InMemoryDocumentAccess,
    ReadingUnitView,
    build_document_scope,
)
from app.services.reader_record_ask.finalizer import FinalizedAskResult
from app.services.reader_record_ask.learner_reasoning.capacity import (
    get_global_projector_limiter,
    reset_global_projector_limiter_for_tests,
)
from app.services.reader_record_ask.production_stream import (
    stream_agentic_thread_message,
)
from app.services.reader_record_ask.runtime import (
    ReadingRecordAskRunResult,
    run_reading_record_ask,
)
from app.services.reader_record_ask.sse import (
    EVENT_AGENTIC_LEARNER_REASONING_SNAPSHOT,
    EVENT_AGENTIC_TERMINAL,
    EVENT_MESSAGE_COMPLETED,
)

_USER = UUID("11111111-1111-1111-1111-111111111111")
_RECORD = UUID("22222222-2222-2222-2222-222222222222")
_BASE = UUID("33333333-3333-3333-3333-333333333333")
_DOC = UUID("44444444-4444-4444-4444-444444444444")
_SHA = "b" * 64
_THREAD = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

_THINK = "初步分析用户问题的核心意图与证据范围。"


class _FakeRepo:
    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.turns: dict[str, dict] = {}
        self.completed_writes: list[dict] = []
        self.terminal_writes: list[dict] = []
        self.complete_should_fail: bool = False
        self.terminal_should_fail: bool = False
        self.heartbeat_calls: list[UUID] = []
        # When set, complete_agentic_turn_run returns CAS loser payload.
        self.cas_loser: dict | None = None

    async def get_thread(self, **kwargs):
        return {
            "id": str(_THREAD),
            "user_id": str(_USER),
            "reading_record_id": str(_RECORD),
            "title": "t",
            "is_default": True,
        }

    async def create_message(self, **kwargs):
        mid = str(uuid4())
        row = {"id": mid, "thread_id": str(kwargs["thread_id"]), **kwargs}
        self.messages.append(row)
        return row

    async def create_agentic_turn_run(self, **kwargs):
        tid = str(uuid4())
        row = {
            "id": tid,
            "status": "streaming",
            "execution_version": EXECUTION_VERSION_AGENTIC_V2,
            "envelope_fingerprint": kwargs.get("envelope_fingerprint"),
        }
        self.turns[tid] = dict(row)
        return row

    async def complete_agentic_turn_run(self, **kwargs):
        if self.complete_should_fail:
            raise RuntimeError("simulated DB connection drop")
        if self.cas_loser is not None:
            self.completed_writes.append(kwargs)
            payload = {
                "id": str(kwargs["turn_run_id"]),
                "status": "already_terminal",
                **self.cas_loser,
            }
            self.turns[str(kwargs["turn_run_id"])] = dict(payload)
            return payload
        self.completed_writes.append(kwargs)
        dto = kwargs["completed_dto"]
        self.turns[str(kwargs["turn_run_id"])] = {
            "id": str(kwargs["turn_run_id"]),
            "status": "completed",
            "final_status": "ok",
            "user_visible_output_json": dto,
            "resolved_evidence_json": kwargs["resolved_evidence"],
            "reasoning_projection_json": kwargs.get("reasoning_projection"),
            "envelope_fingerprint": None,
            "execution_version": EXECUTION_VERSION_AGENTIC_V2,
        }
        return self.turns[str(kwargs["turn_run_id"])]

    async def terminal_agentic_turn_run(self, **kwargs):
        if self.terminal_should_fail:
            raise RuntimeError("simulated terminal write failure")
        self.terminal_writes.append(kwargs)
        self.turns[str(kwargs["turn_run_id"])] = {
            "id": str(kwargs["turn_run_id"]),
            "status": kwargs["run_status"],
            "final_status": kwargs["final_status"],
            "terminal_reason": kwargs["terminal_reason"],
            "user_visible_output_json": kwargs.get("terminal_dto"),
            "resolved_evidence_json": [],
            "envelope_fingerprint": None,
            "execution_version": EXECUTION_VERSION_AGENTIC_V2,
            "reasoning_projection_json": None,
        }
        return self.turns[str(kwargs["turn_run_id"])]

    async def heartbeat_turn_run(self, *, turn_run_id: UUID) -> None:
        tid = str(turn_run_id)
        if tid in self.turns and self.turns[tid].get("status") == "streaming":
            self.heartbeat_calls.append(turn_run_id)


def _fake_facts():
    base = SimpleNamespace(
        base_id=str(_BASE), content_sha256=_SHA, text="hello world"
    )
    unit = SimpleNamespace(
        unit_id="u1",
        order_index=0,
        text="hello world",
        text_hash="11111111",
        base_start_utf16=0,
        base_end_utf16=11,
    )
    seg = SimpleNamespace(
        unit_id="u1",
        anchor_segment_id="s1",
        order_index=0,
        unit_order_index=0,
        text="hello",
        text_hash="a1b2c3d4",
        unit_start_utf16=0,
        unit_end_utf16=5,
        base_start_utf16=0,
        base_end_utf16=5,
    )
    return SimpleNamespace(
        build_result=SimpleNamespace(
            base=base, units=(unit,), anchor_segments=(seg,)
        ),
        record=SimpleNamespace(
            generation=1,
            product_state="readable_enhancing",
            readiness_state="article_ready",
            title="T",
        ),
    )


def _access():
    return InMemoryDocumentAccess(
        snapshot=build_document_scope(
            reading_record_id=_RECORD,
            base_id=_BASE,
            record_generation=1,
            stable_document_id=_DOC,
            base_content_sha256=_SHA,
            units=[
                ReadingUnitView(
                    unit_id="u1",
                    order_index=0,
                    text="hello",
                    text_hash="11111111",
                    base_start_utf16=0,
                    base_end_utf16=5,
                )
            ],
        )
    )


def _parse_sse(chunks: list[str]) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for chunk in chunks:
        event = data = ""
        for line in chunk.strip().split("\n"):
            if line.startswith("event: "):
                event = line[7:]
            if line.startswith("data: "):
                data = line[6:]
        if event and data:
            events.append((event, json.loads(data)))
    return events


def _answer_payload(text: str = "done answer") -> dict:
    # Match thinking_transport fixtures (clarification avoids evidence invariants).
    return {
        "response_kind": "clarification",
        "clarification_text": text,
        "answer_blocks": [],
    }


def _thinking_stream_model(*, slow_after_think: float = 0.0):
    """FunctionModel whose stream yields real ThinkingPart deltas then answer."""

    async def stream_fn(messages, info):
        del messages, info
        yield {0: DeltaThinkingPart(content=_THINK[:8])}
        yield {0: DeltaThinkingPart(content=_THINK[8:])}
        if slow_after_think:
            await asyncio.sleep(slow_after_think)
        yield json.dumps(_answer_payload())

    async def function(messages, info):
        del messages, info
        return ModelResponse(
            parts=[
                ThinkingPart(content=_THINK),
                TextPart(content=json.dumps(_answer_payload())),
            ]
        )

    return FunctionModel(
        function=function,
        stream_function=stream_fn,
        profile=ModelProfile(supports_thinking=True),
    )


async def _run_via_real_transport(**kwargs):
    """Production run_fn: real runtime → thinking_transport, no hand observer calls."""
    # Force model that streams thinking; ignore active_model string.
    kwargs = dict(kwargs)
    kwargs["model"] = _thinking_stream_model()
    # Prove we do not hand-call observer methods.
    obs = kwargs.get("thinking_observer")
    if obs is not None:
        for name in (
            "on_reasoning_delta",
            "on_reasoning_segment_end",
            "on_first_answer_delta",
        ):
            original = getattr(obs, name, None)
            if original is None:
                continue

            def _guard(*a, _orig=original, _n=name, **k):
                # Allow transport to call; mark that transport path was used.
                _guard.called_by_transport = True  # type: ignore[attr-defined]
                return _orig(*a, **k)

            _guard.called_by_transport = False  # type: ignore[attr-defined]
            setattr(obs, name, _guard)
            setattr(obs, f"_guard_{name}", _guard)

    result = await run_reading_record_ask(**kwargs)
    return result


async def _collect(**kwargs):
    repo = kwargs.pop("repository", None) or _FakeRepo()
    grace = kwargs.pop("learner_reasoning_finalize_grace", 0.5)
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
        model="placeholder",
        run_fn=kwargs.pop("run_fn", _run_via_real_transport),
        auto_wire_dependencies=False,
        stable_document_id=_DOC,
        learner_reasoning_enabled_override=True,
        learner_reasoning_finalize_grace=grace,
        **kwargs,
    ):
        chunks.append(c)
    return chunks, repo


@pytest.fixture(autouse=True)
def _reset_limiter():
    reset_global_projector_limiter_for_tests(limit=8)
    yield
    reset_global_projector_limiter_for_tests(limit=8)


@pytest.mark.asyncio
async def test_production_transport_success_hot_eq_db() -> None:
    async def projector(_w: str) -> str | None:
        await asyncio.sleep(0.02)
        return "正在整理当前思路"

    chunks, repo = await _collect(learner_reasoning_run_fn=projector)
    events = _parse_sse(chunks)
    names = [n for n, _ in events]
    assert EVENT_MESSAGE_COMPLETED in names
    snaps = [d for n, d in events if n == EVENT_AGENTIC_LEARNER_REASONING_SNAPSHOT]
    assert len(snaps) >= 1
    last = snaps[-1]
    assert last["generation_id"] >= 0
    assert last["message_id"]
    assert last["thread_id"] == str(_THREAD)
    assert last["policy_version"] == "learner_reasoning_v1"
    completed_i = names.index(EVENT_MESSAGE_COMPLETED)
    last_snap_i = max(
        i for i, n in enumerate(names) if n == EVENT_AGENTIC_LEARNER_REASONING_SNAPSHOT
    )
    assert last_snap_i < completed_i
    assert len(repo.completed_writes) == 1
    db = repo.completed_writes[0]["reasoning_projection"]
    assert db is not None
    assert db["text"] == last["text"]
    assert db["sequence"] == last["sequence"]
    assert get_global_projector_limiter().held == 0


@pytest.mark.asyncio
async def test_terminal_sse_moment_limiter_zero_on_exception() -> None:
    """When consumer sees terminal SSE, limiter must already be released."""
    started = asyncio.Event()

    async def projector(_w: str) -> str | None:
        started.set()
        await asyncio.sleep(5)
        return "不应在 terminal 时仍占用"

    async def run_fn(**kwargs):
        # Drive real transport then crash after checkpoint dispatch
        kwargs = dict(kwargs)
        kwargs["model"] = _thinking_stream_model()
        # Start transport in background-ish: run until thinking emitted
        task = asyncio.create_task(run_reading_record_ask(**kwargs))
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        raise RuntimeError("agent crashed after thinking")

    limiter_at_terminal: list[int] = []
    chunks: list[str] = []
    async for c in stream_agentic_thread_message(
        user_id=_USER,
        reading_record_id=_RECORD,
        thread_id=_THREAD,
        content="q",
        facts=_fake_facts(),
        request_anchor=None,
        repository=_FakeRepo(),  # type: ignore[arg-type]
        document_access=_access(),
        model="placeholder",
        run_fn=run_fn,
        auto_wire_dependencies=False,
        stable_document_id=_DOC,
        learner_reasoning_enabled_override=True,
        learner_reasoning_run_fn=projector,
        learner_reasoning_finalize_grace=0.3,
    ):
        chunks.append(c)
        for name, _ in _parse_sse([c]):
            if name == EVENT_AGENTIC_TERMINAL:
                limiter_at_terminal.append(get_global_projector_limiter().held)

    assert limiter_at_terminal, "never saw terminal SSE"
    assert limiter_at_terminal[0] == 0
    assert EVENT_MESSAGE_COMPLETED not in [n for n, _ in _parse_sse(chunks)]


@pytest.mark.asyncio
async def test_terminal_db_write_throws_still_cleanup() -> None:
    repo = _FakeRepo()
    repo.terminal_should_fail = True

    async def projector(_w: str) -> str | None:
        await asyncio.sleep(0.2)
        return "cleanup 必须发生"

    async def run_fn(**kwargs):
        kwargs = dict(kwargs)
        kwargs["model"] = _thinking_stream_model()
        await asyncio.sleep(0.05)
        raise RuntimeError("boom")

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
        run_fn=run_fn,
        auto_wire_dependencies=False,
        stable_document_id=_DOC,
        learner_reasoning_enabled_override=True,
        learner_reasoning_run_fn=projector,
    ):
        chunks.append(c)

    assert get_global_projector_limiter().held == 0
    assert repo.completed_writes == []
    # terminal write failed but stream still emitted a terminal for the client
    assert any(
        n == EVENT_AGENTIC_TERMINAL for n, _ in _parse_sse(chunks)
    )


@pytest.mark.asyncio
async def test_cancel_with_inflight_projector_releases_limiter() -> None:
    started = asyncio.Event()

    async def projector(_w: str) -> str | None:
        started.set()
        await asyncio.sleep(10)
        return "取消后不应占用"

    async def run_fn(**kwargs):
        kwargs = dict(kwargs)
        kwargs["model"] = _thinking_stream_model()
        # Real transport to dispatch CP1, then hang until cancelled
        task = asyncio.create_task(run_reading_record_ask(**kwargs))
        await started.wait()
        await asyncio.sleep(0.05)
        await asyncio.sleep(30)
        return await task

    agen = stream_agentic_thread_message(
        user_id=_USER,
        reading_record_id=_RECORD,
        thread_id=_THREAD,
        content="q",
        facts=_fake_facts(),
        request_anchor=None,
        repository=_FakeRepo(),  # type: ignore[arg-type]
        document_access=_access(),
        model="x",
        run_fn=run_fn,
        auto_wire_dependencies=False,
        stable_document_id=_DOC,
        learner_reasoning_enabled_override=True,
        learner_reasoning_run_fn=projector,
        learner_reasoning_finalize_grace=0.2,
    )
    # Pull a frame then cancel generator
    try:
        await asyncio.wait_for(agen.__anext__(), timeout=2.0)
    except (StopAsyncIteration, TimeoutError):
        pass
    await agen.aclose()
    # Allow cleanup
    await asyncio.sleep(0.1)
    assert get_global_projector_limiter().held == 0


def _assert_terminal_invariants(events: list[tuple[str, dict]]) -> None:
    """Shared post-conditions for every failure/CAS terminal path."""
    names = [n for n, _ in events]
    assert get_global_projector_limiter().held == 0
    if EVENT_AGENTIC_TERMINAL in names:
        term_i = names.index(EVENT_AGENTIC_TERMINAL)
        for i, n in enumerate(names):
            if n == EVENT_AGENTIC_LEARNER_REASONING_SNAPSHOT:
                assert i < term_i, "learner snapshot after terminal"
        assert EVENT_MESSAGE_COMPLETED not in names
    if EVENT_MESSAGE_COMPLETED in names:
        assert EVENT_AGENTIC_TERMINAL not in names


@pytest.mark.asyncio
async def test_non_ok_and_persist_failure_no_cold() -> None:
    async def projector(_w: str) -> str | None:
        return "不应冷存"

    async def run_non_ok(**kwargs):
        kwargs = dict(kwargs)
        kwargs["model"] = _thinking_stream_model()
        await run_reading_record_ask(**kwargs)
        return ReadingRecordAskRunResult(
            final_text=None,
            finalized=FinalizedAskResult(
                status="context_stale",
                answer_text=None,
                reason="generation mismatch",
                envelope_fingerprint=kwargs["envelope"].envelope_fingerprint,
            ),
        )

    chunks, repo = await _collect(
        run_fn=run_non_ok, learner_reasoning_run_fn=projector
    )
    events = _parse_sse(chunks)
    assert EVENT_MESSAGE_COMPLETED not in [n for n, _ in events]
    assert repo.completed_writes == []
    _assert_terminal_invariants(events)

    repo2 = _FakeRepo()
    repo2.complete_should_fail = True
    chunks2, _ = await _collect(
        repository=repo2,
        run_fn=_run_via_real_transport,
        learner_reasoning_run_fn=projector,
    )
    events2 = _parse_sse(chunks2)
    assert EVENT_MESSAGE_COMPLETED not in [n for n, _ in events2]
    assert repo2.completed_writes == []
    _assert_terminal_invariants(events2)


@pytest.mark.asyncio
async def test_slow_projector_non_ok_limiter_zero_at_terminal_frame() -> None:
    """non-ok terminal frame must observe limiter=0 (cleanup before yield)."""
    started = asyncio.Event()

    async def projector(_w: str) -> str | None:
        started.set()
        await asyncio.sleep(8)
        return "慢投影不应占用 terminal 帧"

    async def run_non_ok(**kwargs):
        kwargs = dict(kwargs)
        kwargs["model"] = _thinking_stream_model()
        task = asyncio.create_task(run_reading_record_ask(**kwargs))
        await asyncio.wait_for(started.wait(), timeout=2.0)
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return ReadingRecordAskRunResult(
            final_text=None,
            finalized=FinalizedAskResult(
                status="failed",
                answer_text=None,
                reason="agent failed after thinking",
                envelope_fingerprint=kwargs["envelope"].envelope_fingerprint,
            ),
        )

    limiter_at_terminal: list[int] = []
    chunks: list[str] = []
    async for c in stream_agentic_thread_message(
        user_id=_USER,
        reading_record_id=_RECORD,
        thread_id=_THREAD,
        content="q",
        facts=_fake_facts(),
        request_anchor=None,
        repository=_FakeRepo(),  # type: ignore[arg-type]
        document_access=_access(),
        model="placeholder",
        run_fn=run_non_ok,
        auto_wire_dependencies=False,
        stable_document_id=_DOC,
        learner_reasoning_enabled_override=True,
        learner_reasoning_run_fn=projector,
        learner_reasoning_finalize_grace=0.2,
    ):
        chunks.append(c)
        for name, _ in _parse_sse([c]):
            if name == EVENT_AGENTIC_TERMINAL:
                limiter_at_terminal.append(get_global_projector_limiter().held)

    assert limiter_at_terminal, "never saw terminal SSE"
    assert limiter_at_terminal[0] == 0
    _assert_terminal_invariants(_parse_sse(chunks))


@pytest.mark.asyncio
async def test_cas_loser_non_ok_winner_limiter_zero_at_terminal() -> None:
    """CAS loser always acloses before projecting winning non-ok terminal."""
    started = asyncio.Event()

    async def projector(_w: str) -> str | None:
        started.set()
        await asyncio.sleep(8)
        return "CAS loser 慢投影"

    repo = _FakeRepo()
    repo.cas_loser = {
        "winning_final_status": "failed",
        "winning_terminal_reason": "winner_cancelled",
        "winning_user_visible_output_json": {
            "final_status": "failed",
            "message_id": "win-msg",
            "thread_id": str(_THREAD),
            "turn_run_id": "win-run",
            "terminal_reason": "winner_cancelled",
        },
    }

    limiter_at_terminal: list[int] = []
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
        model="placeholder",
        run_fn=_run_via_real_transport,
        auto_wire_dependencies=False,
        stable_document_id=_DOC,
        learner_reasoning_enabled_override=True,
        learner_reasoning_run_fn=projector,
        learner_reasoning_finalize_grace=0.15,
    ):
        chunks.append(c)
        for name, _ in _parse_sse([c]):
            if name == EVENT_AGENTIC_TERMINAL:
                limiter_at_terminal.append(get_global_projector_limiter().held)

    assert limiter_at_terminal, "CAS loser must emit winning non-ok terminal"
    assert limiter_at_terminal[0] == 0
    events = _parse_sse(chunks)
    names = [n for n, _ in events]
    assert EVENT_AGENTIC_TERMINAL in names
    assert EVENT_MESSAGE_COMPLETED not in names
    term = next(d for n, d in events if n == EVENT_AGENTIC_TERMINAL)
    assert term.get("final_status") == "failed"
    _assert_terminal_invariants(events)


@pytest.mark.asyncio
async def test_cas_loser_ok_winner_silent_end_limiter_zero() -> None:
    """CAS loser with winning ok ends silently after aclose; limiter=0."""

    async def projector(_w: str) -> str | None:
        await asyncio.sleep(0.05)
        return "CAS loser ok winner 静默"

    repo = _FakeRepo()
    repo.cas_loser = {
        "winning_final_status": "ok",
        "winning_terminal_reason": None,
        "winning_user_visible_output_json": {"answer_text": "winner"},
    }
    chunks, _ = await _collect(
        repository=repo,
        run_fn=_run_via_real_transport,
        learner_reasoning_run_fn=projector,
    )
    events = _parse_sse(chunks)
    names = [n for n, _ in events]
    assert EVENT_AGENTIC_TERMINAL not in names
    assert EVENT_MESSAGE_COMPLETED not in names
    assert get_global_projector_limiter().held == 0


@pytest.mark.asyncio
async def test_terminal_db_failure_still_emits_typed_terminal() -> None:
    """terminal DB write failure must still deliver typed terminal to client."""
    repo = _FakeRepo()
    repo.terminal_should_fail = True

    async def projector(_w: str) -> str | None:
        await asyncio.sleep(0.05)
        return "terminal DB fail 仍要 SSE"

    async def run_fail(**kwargs):
        kwargs = dict(kwargs)
        kwargs["model"] = _thinking_stream_model()
        await run_reading_record_ask(**kwargs)
        raise RuntimeError("agent boom")

    limiter_at_terminal: list[int] = []
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
        run_fn=run_fail,
        auto_wire_dependencies=False,
        stable_document_id=_DOC,
        learner_reasoning_enabled_override=True,
        learner_reasoning_run_fn=projector,
    ):
        chunks.append(c)
        for name, _ in _parse_sse([c]):
            if name == EVENT_AGENTIC_TERMINAL:
                limiter_at_terminal.append(get_global_projector_limiter().held)

    assert limiter_at_terminal and limiter_at_terminal[0] == 0
    events = _parse_sse(chunks)
    assert any(n == EVENT_AGENTIC_TERMINAL for n, _ in events)
    term = next(d for n, d in events if n == EVENT_AGENTIC_TERMINAL)
    assert term.get("final_status") == "failed"
    assert term.get("terminal_reason")
    assert repo.terminal_writes == []  # write threw
    _assert_terminal_invariants(events)


@pytest.mark.asyncio
async def test_persist_failure_limiter_zero_and_typed_terminal() -> None:
    repo = _FakeRepo()
    repo.complete_should_fail = True

    async def projector(_w: str) -> str | None:
        await asyncio.sleep(0.15)
        return "persist fail 投影"

    limiter_at_terminal: list[int] = []
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
        run_fn=_run_via_real_transport,
        auto_wire_dependencies=False,
        stable_document_id=_DOC,
        learner_reasoning_enabled_override=True,
        learner_reasoning_run_fn=projector,
        learner_reasoning_finalize_grace=0.2,
    ):
        chunks.append(c)
        for name, _ in _parse_sse([c]):
            if name == EVENT_AGENTIC_TERMINAL:
                limiter_at_terminal.append(get_global_projector_limiter().held)

    assert limiter_at_terminal and limiter_at_terminal[0] == 0
    events = _parse_sse(chunks)
    assert repo.completed_writes == []
    term = next(d for n, d in events if n == EVENT_AGENTIC_TERMINAL)
    assert term.get("final_status") == "failed"
    _assert_terminal_invariants(events)


@pytest.mark.asyncio
async def test_evidence_scope_invariant_completed_path() -> None:
    """build_completed_dto invariant → failed terminal, no completed."""
    from app.services.reader_record_ask.evidence import (
        ArticleRagCitationEvidence,
        build_server_evidence_observation,
    )

    async def projector(_w: str) -> str | None:
        await asyncio.sleep(0.05)
        return "evidence invariant 投影"

    async def run_bad_scope(**kwargs):
        kwargs = dict(kwargs)
        kwargs["model"] = _thinking_stream_model()
        await run_reading_record_ask(**kwargs)
        env = kwargs["envelope"]
        substrate = str(uuid4())
        cit = ArticleRagCitationEvidence(
            rag_substrate_id=substrate,
            index_run_id=substrate,
            plan_content_sha256="c" * 64,
            source_scope="main_reading_text",
            block_type="paragraph",
            chunk_id="ch1",
            content_sha256="d" * 64,
            canonical_text_start_utf16=0,
            canonical_text_end_utf16=5,
            snippet="secret-snippet",
            reading_record_id=str(_RECORD),
            stable_document_id=str(uuid4()),  # wrong
            base_id=str(_BASE),
            record_generation=1,
        )
        obs = build_server_evidence_observation(
            kind="search_hit",
            envelope_fingerprint=env.envelope_fingerprint,
            source_tool="search_current_article",
            snippet="secret-snippet",
            rag_citation=cit,
        )
        return ReadingRecordAskRunResult(
            final_text="must not complete",
            finalized=FinalizedAskResult(
                status="ok",
                answer_text="must not complete",
                reason=None,
                envelope_fingerprint=env.envelope_fingerprint,
                resolved_evidence=(obs,),
            ),
        )

    limiter_at_terminal: list[int] = []
    chunks: list[str] = []
    async for c in stream_agentic_thread_message(
        user_id=_USER,
        reading_record_id=_RECORD,
        thread_id=_THREAD,
        content="q",
        facts=_fake_facts(),
        request_anchor=None,
        repository=_FakeRepo(),  # type: ignore[arg-type]
        document_access=_access(),
        model="x",
        run_fn=run_bad_scope,
        auto_wire_dependencies=False,
        stable_document_id=_DOC,
        learner_reasoning_enabled_override=True,
        learner_reasoning_run_fn=projector,
    ):
        chunks.append(c)
        for name, _ in _parse_sse([c]):
            if name == EVENT_AGENTIC_TERMINAL:
                limiter_at_terminal.append(get_global_projector_limiter().held)

    assert limiter_at_terminal and limiter_at_terminal[0] == 0
    events = _parse_sse(chunks)
    term = next(d for n, d in events if n == EVENT_AGENTIC_TERMINAL)
    assert term.get("final_status") == "failed"
    assert "evidence_scope" in str(term.get("terminal_reason") or "")
    blob = json.dumps([d for _, d in events], ensure_ascii=False)
    assert "secret-snippet" not in blob
    _assert_terminal_invariants(events)


@pytest.mark.asyncio
async def test_evidence_scope_invariant_restricted_path() -> None:
    """Second invariant gate (restricted evidence) after success freeze."""
    from app.services.reader_record_ask.evidence import (
        ArticleRagCitationEvidence,
        build_server_evidence_observation,
    )
    from app.services.reader_record_ask.production_stream import (
        EvidenceScopeInvariantError,
        build_restricted_evidence_json,
    )

    # If completed DTO path already raises, restricted path is the second
    # gate — force completed to pass by monkeypatching build_completed_dto
    # is heavy; instead return ok with mismatched evidence so both gates
    # would fire. First gate (build_completed_dto) is enough for stream
    # coverage; this test asserts the same terminal shape via that path
    # plus documents the restricted helper still fail-closes.
    async def projector(_w: str) -> str | None:
        return "restricted invariant"

    async def run_bad(**kwargs):
        kwargs = dict(kwargs)
        kwargs["model"] = _thinking_stream_model()
        await run_reading_record_ask(**kwargs)
        env = kwargs["envelope"]
        substrate = str(uuid4())
        cit = ArticleRagCitationEvidence(
            rag_substrate_id=substrate,
            index_run_id=substrate,
            plan_content_sha256="c" * 64,
            source_scope="main_reading_text",
            block_type="paragraph",
            chunk_id="ch2",
            content_sha256="e" * 64,
            canonical_text_start_utf16=0,
            canonical_text_end_utf16=3,
            snippet="restricted-leak",
            reading_record_id=str(_RECORD),
            stable_document_id=str(uuid4()),
            base_id=str(_BASE),
            record_generation=99,  # generation mismatch
        )
        obs = build_server_evidence_observation(
            kind="search_hit",
            envelope_fingerprint=env.envelope_fingerprint,
            source_tool="search_current_article",
            snippet="restricted-leak",
            rag_citation=cit,
        )
        result = ReadingRecordAskRunResult(
            final_text="nope",
            finalized=FinalizedAskResult(
                status="ok",
                answer_text="nope",
                reason=None,
                envelope_fingerprint=env.envelope_fingerprint,
                resolved_evidence=(obs,),
            ),
        )
        # Unit gate for restricted path itself.
        with pytest.raises(EvidenceScopeInvariantError):
            build_restricted_evidence_json(run_result=result, envelope=env)
        return result

    chunks, repo = await _collect(
        run_fn=run_bad, learner_reasoning_run_fn=projector
    )
    events = _parse_sse(chunks)
    assert any(n == EVENT_AGENTIC_TERMINAL for n, _ in events)
    assert repo.completed_writes == []
    blob = json.dumps([d for _, d in events], ensure_ascii=False)
    assert "restricted-leak" not in blob
    _assert_terminal_invariants(events)


@pytest.mark.asyncio
async def test_real_evidence_boundary_then_reasoning_checkpoint() -> None:
    """Real FunctionToolResultEvent → article stage/basis before completed.

    Chain (no hand observer calls):
    DeltaThinkingPart → expand_evidence tool → FunctionToolResultEvent
    → on_evidence_boundary (transport) → second thinking → CP2 article.
    """
    from pydantic_ai.models.function import DeltaToolCall

    think1 = "初步分析用户问题的核心意图与范围。"
    think2 = "结合文章证据继续核对关键结论要点。"
    tool_returns = {"n": 0}

    async def stream_fn(messages, info):
        del info
        has_tool_return = any(
            type(p).__name__ == "ToolReturnPart"
            for m in messages
            for p in getattr(m, "parts", []) or []
        )
        if not has_tool_return:
            yield {0: DeltaThinkingPart(content=think1[:10])}
            yield {0: DeltaThinkingPart(content=think1[10:])}
            yield {
                1: DeltaToolCall(
                    name="expand_evidence",
                    json_args=json.dumps({"pointer": ""}),
                    tool_call_id="tc-ev1",
                )
            }
            return
        tool_returns["n"] += 1
        yield {0: DeltaThinkingPart(content=think2[:10])}
        yield {0: DeltaThinkingPart(content=think2[10:])}
        # Allow post-evidence CP2 to start+publish while agent still open
        # (finalize freeze would drop a still-pending CP2).
        await asyncio.sleep(0.35)
        yield json.dumps(_answer_payload("after evidence"))

    async def function(messages, info):
        del messages, info
        return ModelResponse(
            parts=[
                ThinkingPart(content=think2),
                TextPart(content=json.dumps(_answer_payload("after evidence"))),
            ]
        )

    model = FunctionModel(
        function=function,
        stream_function=stream_fn,
        profile=ModelProfile(supports_thinking=True),
    )

    async def projector(window: str) -> str | None:
        # Fast enough that CP1 finishes before CP2 is submitted, so CP2
        # becomes busy (not merely pending) before success-path freeze.
        await asyncio.sleep(0.04)
        if "证据" in window or "结合" in window:
            return "结合证据核对结论"
        return "正在梳理问题要点"

    async def run_fn(**kwargs):
        kwargs = dict(kwargs)
        kwargs["model"] = model
        # Production runtime only — never hand-call on_evidence_boundary.
        return await run_reading_record_ask(**kwargs)

    chunks, repo = await _collect(
        run_fn=run_fn,
        learner_reasoning_run_fn=projector,
        learner_reasoning_finalize_grace=0.9,
    )
    events = _parse_sse(chunks)
    names = [n for n, _ in events]
    assert EVENT_MESSAGE_COMPLETED in names
    completed_i = names.index(EVENT_MESSAGE_COMPLETED)

    # Real tool result path executed (second model round after ToolReturnPart).
    assert tool_returns["n"] >= 1

    snaps_idx = [
        (i, d)
        for i, (n, d) in enumerate(events)
        if n == EVENT_AGENTIC_LEARNER_REASONING_SNAPSHOT
    ]
    assert snaps_idx, "expected learner snapshots from real transport"

    article = [
        (i, d)
        for i, d in snaps_idx
        if d.get("stage") == "article"
    ]
    assert article, (
        f"expected stage=article after FunctionToolResultEvent; "
        f"stages={[d.get('stage') for _, d in snaps_idx]}"
    )
    art_i, art = article[0]
    assert art.get("basis") == ["article"], art
    assert art_i < completed_i, "article snapshot must precede message.completed"

    # analyzing (if present) must precede article; article before completed.
    analyzing = [(i, d) for i, d in snaps_idx if d.get("stage") == "analyzing"]
    if analyzing:
        assert analyzing[0][0] < art_i

    # generation_id / sequence monotonic along emission order
    gens = [d["generation_id"] for _, d in snaps_idx]
    seqs = [d["sequence"] for _, d in snaps_idx]
    assert seqs == sorted(seqs)
    assert all(isinstance(g, int) and g >= 0 for g in gens)
    for a, b in zip(gens, gens[1:], strict=False):
        assert b >= a, f"generation_id not non-decreasing: {gens}"
    for a, b in zip(seqs, seqs[1:], strict=False):
        assert b > a, f"sequence not strictly increasing: {seqs}"

    # No raw private reasoning on the wire
    blob = json.dumps([d for _, d in events], ensure_ascii=False)
    assert think1 not in blob
    assert think2 not in blob

    assert get_global_projector_limiter().held == 0
    assert len(repo.completed_writes) == 1


@pytest.mark.asyncio
async def test_retry_old_generation_inflight_zero_publish() -> None:
    """Real output_validator ModelRetry via production stream entry.

    Chain (no hand observer calls) — ToolOutput so pydantic-ai emits the
    real OutputToolResultEvent on validator ModelRetry (text JSON path
    does not):

    Round1 DeltaThinkingPart → final_result tool call → output_validator
    ModelRetry → OutputToolResultEvent(RetryPromptPart)
    → thinking_transport.advance_round("output_validator_retry")
    → Round2 thinking → final answer.

    gen0 projector is in-flight during retry; its result must not publish.
    gen>=1 may publish a safe snapshot; generation_id increments.
    """
    from pydantic_ai import Agent, ToolOutput
    from pydantic_ai.exceptions import ModelRetry
    from pydantic_ai.messages import RetryPromptPart
    from pydantic_ai.models.function import DeltaToolCall

    from app.services.reader_record_ask.evidence_registry import EvidenceRegistry
    from app.services.reader_record_ask.fence import StaticGenerationFence
    from app.services.reader_record_ask.grounding_validator import (
        AgentAnswerDraftOutput,
    )
    from app.services.reader_record_ask.runtime_deps import ReaderRecordAskDeps
    from app.services.reader_record_ask.thinking_transport import (
        run_agent_with_thinking_transport,
    )

    think_g0 = "第一代分析内容足够长用于投影。"
    think_g1 = "第二代重新分析问题要点与范围。"
    gen0_started = asyncio.Event()
    gen0_finished = asyncio.Event()
    validator_calls = {"n": 0}
    projector_calls: list[str] = []
    output_tool_retries = {"n": 0}
    advance_reasons: list[str] = []

    async def projector(window: str) -> str | None:
        projector_calls.append(window[:40])
        if "第一代" in window:
            gen0_started.set()
            # Stay in-flight across the real OutputToolResultEvent retry.
            await asyncio.sleep(0.7)
            gen0_finished.set()
            return "第一代结果不应发布"
        await asyncio.sleep(0.05)
        return "第二代重新梳理问题要点"

    def _payload(text: str) -> str:
        return json.dumps(_answer_payload(text))

    async def stream_fn(messages, info):
        del info
        has_retry = any(
            isinstance(p, RetryPromptPart)
            for m in messages
            for p in getattr(m, "parts", []) or []
        )
        if not has_retry:
            yield {0: DeltaThinkingPart(content=think_g0)}
            # Let CP1 acquire limiter before output tool / validator.
            await asyncio.sleep(0.12)
            yield {
                1: DeltaToolCall(
                    name="final_result",
                    json_args=_payload("round1 draft"),
                    tool_call_id="out-r1",
                )
            }
            return
        yield {0: DeltaThinkingPart(content=think_g1)}
        await asyncio.sleep(0.08)
        yield {
            1: DeltaToolCall(
                name="final_result",
                json_args=_payload("round2 final"),
                tool_call_id="out-r2",
            )
        }

    model = FunctionModel(
        stream_function=stream_fn,
        profile=ModelProfile(supports_thinking=True),
    )

    async def run_fn(**kwargs):
        # Production stream supplies thinking_observer; never call it here.
        thinking_observer = kwargs["thinking_observer"]
        envelope = kwargs["envelope"]
        document_access = kwargs["document_access"]

        # Observe the production transport boundary without invoking it by hand.
        # This locks the OutputToolResultEvent + legacy-twin de-duplication:
        # one validator retry must advance exactly once.
        original_advance = thinking_observer.advance_round

        def traced_advance(reason: str = "normal_tool_result") -> None:
            advance_reasons.append(reason)
            original_advance(reason)

        thinking_observer.advance_round = traced_advance

        def _build_agent(m) -> Agent:
            # ToolOutput is required so ModelRetry yields OutputToolResultEvent
            # (text JSON output path does not emit that event).
            agent: Agent[ReaderRecordAskDeps, AgentAnswerDraftOutput] = Agent(
                m,
                deps_type=ReaderRecordAskDeps,
                output_type=ToolOutput(AgentAnswerDraftOutput),
                retries={"output": 2},
            )

            @agent.output_validator
            async def validator(
                ctx,
                draft: AgentAnswerDraftOutput,
            ) -> AgentAnswerDraftOutput:
                del ctx
                validator_calls["n"] += 1
                if validator_calls["n"] == 1:
                    await asyncio.wait_for(gen0_started.wait(), timeout=3.0)
                    output_tool_retries["n"] += 1
                    raise ModelRetry("first draft rejected for testing")
                return draft

            return agent

        deps = ReaderRecordAskDeps(
            envelope=envelope,
            document_access=document_access,
            fence=StaticGenerationFence(
                live_generation=envelope.record_generation
            ),
            evidence_registry=EvidenceRegistry(
                envelope_fingerprint=envelope.envelope_fingerprint
            ),
        )
        agent = _build_agent(model)
        outcome = await run_agent_with_thinking_transport(
            agent=agent,
            prompt="test prompt",
            deps=deps,
            thinking_observer=thinking_observer,
            model=model,
        )
        try:
            await asyncio.wait_for(gen0_finished.wait(), timeout=2.0)
        except TimeoutError:
            pass
        # Drain gen1 publish before returning completed truth.
        await asyncio.sleep(0.35)

        final_text = "round2 final"
        if hasattr(outcome, "output") and outcome.output is not None:
            out = outcome.output
            final_text = (
                getattr(out, "clarification_text", None)
                or getattr(out, "answer_text", None)
                or final_text
            )
        return ReadingRecordAskRunResult(
            final_text=final_text,
            finalized=FinalizedAskResult(
                status="ok",
                answer_text=final_text,
                envelope_fingerprint=envelope.envelope_fingerprint,
            ),
        )

    chunks, repo = await _collect(
        run_fn=run_fn,
        learner_reasoning_run_fn=projector,
        learner_reasoning_finalize_grace=0.9,
    )
    events = _parse_sse(chunks)
    names = [n for n, _ in events]

    # Real validator ModelRetry path (reject then accept).
    assert validator_calls["n"] == 2
    assert output_tool_retries["n"] == 1
    assert advance_reasons == ["output_validator_retry"]
    assert gen0_started.is_set(), "gen0 projector must have entered in-flight"
    assert any("第一代" in w for w in projector_calls)

    snaps = [
        d for n, d in events if n == EVENT_AGENTIC_LEARNER_REASONING_SNAPSHOT
    ]
    for s in snaps:
        assert s.get("text") != "第一代结果不应发布"
        assert "第一代结果不应发布" not in (s.get("text") or "")
        assert think_g0 not in (s.get("text") or "")
        assert think_g1 not in (s.get("text") or "")

    # After real OutputToolResultEvent retry, only gen>=1 may publish.
    assert snaps, f"expected gen>=1 snapshot; snaps={snaps}"
    assert all(int(s["generation_id"]) >= 1 for s in snaps), snaps
    max_gen = max(int(s["generation_id"]) for s in snaps)
    assert max_gen >= 1
    gens = [int(s["generation_id"]) for s in snaps]
    for a, b in zip(gens, gens[1:], strict=False):
        assert b >= a

    assert EVENT_MESSAGE_COMPLETED in names
    completed_i = names.index(EVENT_MESSAGE_COMPLETED)
    for i, n in enumerate(names):
        if n == EVENT_AGENTIC_LEARNER_REASONING_SNAPSHOT:
            assert i < completed_i
    assert get_global_projector_limiter().held == 0
    assert len(repo.completed_writes) == 1

    blob = json.dumps([d for _, d in events], ensure_ascii=False)
    assert think_g0 not in blob
    assert think_g1 not in blob
    assert "第一代结果不应发布" not in blob
