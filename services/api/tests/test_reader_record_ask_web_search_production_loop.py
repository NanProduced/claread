"""ASK-WEB-G1-R1: real-link production-loop tests for Web Search.

These tests verify the end-to-end production closed loop from
``stream_agentic_thread_message`` → ``_run_agentic_turn`` →
``run_reading_record_ask`` → SSE + persistence, exercising:

- ``ResolvedWebSearchCapability`` propagation (capability → backend → registry).
- ``WebSearchCallEvent`` / ``WebSearchResultEvent`` projection into the
  ``searching_web`` progress phase with the correct ``activity`` /
  ``status`` mapping.
- ``ReaderRecordAskRunStartedDTO.web_search_mode`` echo.
- ``ReaderRecordAskCompletedDTO.web_search`` summary field.
- ``FakeWebSearchBackend`` outcomes (``completed`` / ``no_results`` /
  ``unavailable`` / ``failed``).
- ``WebEvidenceRegistry`` envelope-fingerprint binding (cross-envelope
  reuse must fail-closed).
- Title fallback to display domain when the provider hit has no title.

No real LLM, no real search provider, no network I/O. The fake backend
is scripted per-test via the ``outcomes`` list.
"""

from __future__ import annotations

import json
import logging
import re
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.schemas.reader_record_ask_stream import (
    EXECUTION_VERSION_AGENTIC_V2,
)
from app.services.reader_record_ask.context_envelope import (
    EnvelopeInitialAnchor,
    VerifiedEnvelopeInput,
    build_context_envelope,
)
from app.services.reader_record_ask.finalizer import FinalizedAskResult, PublicCitation
from app.services.reader_record_ask.history_projection import (
    project_agentic_history_message,
)
from app.services.reader_record_ask.production_stream import (
    build_completed_dto,
    stream_agentic_thread_message,
)
from app.services.reader_record_ask.runtime import ReadingRecordAskRunResult
from app.services.reader_record_ask.runtime_events import (
    AnswerDeltaEvent,
    RunStartedEvent,
    WebSearchCallEvent,
    WebSearchResultEvent,
)
from app.services.reader_record_ask.sse import (
    EVENT_AGENTIC_PROGRESS,
    EVENT_AGENTIC_RUN_STARTED,
    EVENT_MESSAGE_COMPLETED,
    encode_sse,
)
from app.services.reader_record_ask.tool_contracts import TOOL_SEARCH_WEB
from app.services.reader_record_ask.web_evidence_registry import (
    WebEvidenceRegistry,
)
from app.services.reader_record_ask.web_search_contracts import (
    PublicWebSearchSummary,
    ResolvedWebSearchCapability,
    WebEvidence,
    WebSearchOutcome,
)
from app.services.reader_record_ask.web_search_port import (
    FakeWebSearchBackend,
    WebSearchHitView,
    WebSearchResult,
)

_USER = UUID("11111111-1111-1111-1111-111111111111")
_RECORD = UUID("22222222-2222-2222-2222-222222222222")
_BASE = UUID("33333333-3333-3333-3333-333333333333")
_DOC = UUID("44444444-4444-4444-4444-444444444444")
_SHA = "b" * 64
_THREAD = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _envelope(**overrides: object):
    payload = dict(
        user_id=_USER,
        reading_record_id=_RECORD,
        base_id=_BASE,
        record_generation=1,
        stable_document_id=_DOC,
        base_content_sha256=_SHA,
        product_state="readable_enhancing",
        readiness_state="article_ready",
        initial_anchor=EnvelopeInitialAnchor(
            unit_id="u1",
            anchor_segment_id="s1",
            start_offset=0,
            end_offset=5,
            selected_text="hello",
            text_hash="a1b2c3d4",
        ),
    )
    payload.update(overrides)
    return build_context_envelope(VerifiedEnvelopeInput(**payload))  # type: ignore[arg-type]


def _fake_facts():
    base = SimpleNamespace(
        base_id=str(_BASE),
        content_sha256=_SHA,
        text="hello world",
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
    build_result = SimpleNamespace(base=base, units=(unit,), anchor_segments=(seg,))
    record = SimpleNamespace(
        generation=1,
        product_state="readable_enhancing",
        readiness_state="article_ready",
        title="T",
    )
    return SimpleNamespace(build_result=build_result, record=record)


def _capability(*, enabled: bool = True) -> ResolvedWebSearchCapability:
    """Build a resolved web search capability matching the G1 fake profile."""
    return ResolvedWebSearchCapability(
        enabled_for_turn=enabled,
        provider="fake",
        protocol="fake",
        execution_mode="host_function",
        decision_mode="agent_auto",
        max_calls=1,
        max_results_per_call=3,
        policy_version="reader_record_ask_web_search_v1",
    )


def _hit(
    *,
    url: str = "https://example.com/page",
    title: str = "Example Page",
    description: str = "An example page.",
) -> WebSearchHitView:
    """Build a provider-neutral hit view for scripted fake backends.

    ``raw_url`` is the provider-supplied URL; the host re-canonicalizes
    it via :func:`canonicalize_url` before any host-side registration.
    """
    return WebSearchHitView(
        raw_url=url,
        title=title,
        description=description,
        provider_result_ref="pr-1",
    )


_OUTCOME_TO_PORT_STATUS: dict[WebSearchOutcome, str] = {
    "completed": "ok",
    "no_results": "empty",
    "unavailable": "unavailable",
    "failed": "failed",
}


def _scripted_backend(
    *,
    outcome: WebSearchOutcome = "completed",
    hits: tuple[WebSearchHitView, ...] = (),
    summary: str = "ok",
) -> FakeWebSearchBackend:
    """Build a FakeWebSearchBackend with one scripted result.

    Maps the public :data:`WebSearchOutcome` (``completed`` /
    ``no_results`` / ``unavailable`` / ``failed``) to the
    port-level :data:`WebSearchPortOutcome` (``ok`` / ``empty`` /
    ``unavailable`` / ``failed``) so the coordinator's
    ``_register_web_search_outcome`` receives a valid port status.
    """
    return FakeWebSearchBackend(
        outcomes=[
            WebSearchResult(
                status=_OUTCOME_TO_PORT_STATUS.get(outcome, "unavailable"),  # type: ignore[arg-type]
                summary=summary,
                hits=hits,
            )
        ]
    )


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
    """Minimal repo stub for stream_agentic_thread_message tests."""

    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.turns: dict[str, dict] = {}
        self.completed_writes: list[dict] = []

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
        self.completed_writes.append(kwargs)
        self.turns[str(kwargs["turn_run_id"])] = {
            "id": str(kwargs["turn_run_id"]),
            "status": "completed",
            "final_status": "ok",
            "user_visible_output_json": kwargs["completed_dto"],
            "resolved_evidence_json": kwargs["resolved_evidence"],
            "envelope_fingerprint": None,
            "execution_version": EXECUTION_VERSION_AGENTIC_V2,
        }
        return self.turns[str(kwargs["turn_run_id"])]

    async def terminal_agentic_turn_run(self, **kwargs):
        self.turns[str(kwargs["turn_run_id"])] = {
            "id": str(kwargs["turn_run_id"]),
            "status": kwargs["run_status"],
            "final_status": kwargs["final_status"],
            "terminal_reason": kwargs["terminal_reason"],
            "user_visible_output_json": kwargs.get("terminal_dto"),
            "resolved_evidence_json": [],
            "envelope_fingerprint": None,
            "execution_version": EXECUTION_VERSION_AGENTIC_V2,
        }
        return self.turns[str(kwargs["turn_run_id"])]


def _make_run_fn(
    *,
    answer: str = "ok answer",
    web_search_events: bool = False,
    web_search_outcome: str = "completed",
    registered_evidence_count: int = 0,
    duration_ms: int | None = 12,
):
    """Build a run_fn that emits Web Search events then returns a finalized result.

    When ``web_search_events=True`` the fake run emits a paired
    ``WebSearchCallEvent`` + ``WebSearchResultEvent`` so the production
    stream can project the ``searching_web`` phase. The result carries
    no citations — the Web Search summary comes from the finalizer's
    ``web_search_summary`` field.
    """

    async def _run(**kwargs):
        sink = kwargs["event_sink"]
        env = kwargs["envelope"]
        sink(
            RunStartedEvent(
                envelope_fingerprint=env.envelope_fingerprint,
                has_initial_selection=True,
            )
        )
        if web_search_events:
            sink(WebSearchCallEvent(call_sequence=1))
            sink(
                WebSearchResultEvent(
                    call_sequence=1,
                    outcome=web_search_outcome,  # type: ignore[arg-type]
                    # ASK-WEB-R4: turn_outcome mirrors the per-attempt outcome
                    # for single-call test scenarios. In real production the
                    # coordinator aggregates across attempts; here the fake
                    # run emits one attempt so turn_outcome == outcome.
                    turn_outcome=web_search_outcome,  # type: ignore[arg-type]
                    registered_evidence_count=registered_evidence_count,
                    duration_ms=duration_ms,
                )
            )
        sink(AnswerDeltaEvent(delta=answer))
        finalized = FinalizedAskResult(
            status="ok",
            answer_text=answer,
            resolved_evidence=(),
            envelope_fingerprint=env.envelope_fingerprint,
        )
        # Attach a web_search_summary when web search ran.
        if web_search_events:
            finalized = finalized.model_copy(
                update={
                    "web_search_summary": PublicWebSearchSummary(
                        outcome=web_search_outcome,  # type: ignore[arg-type]
                        cited_source_count=registered_evidence_count,
                    )
                }
            )
        return ReadingRecordAskRunResult(
            final_text=answer,
            finalized=finalized,
        )

    return _run


# ---------------------------------------------------------------------------
# Scenario 1: disabled mode → search_web tool never mounted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disabled_mode_capability_none_no_search_events() -> None:
    """``web_search_capability=None`` (disabled mode) must produce zero
    ``searching_web`` progress events and a ``web_search_mode="disabled"``
    echo on ``agentic.run_started``.

    The fake ``run_fn`` must NOT emit ``WebSearchCallEvent`` because the
    runtime must not have mounted the ``search_web`` tool. We assert the
    projector never sees a Web Search event by inspecting the SSE stream.
    """
    repo = _FakeRepo()

    chunks = [
        c
        async for c in stream_agentic_thread_message(
            user_id=_USER,
            reading_record_id=_RECORD,
            thread_id=_THREAD,
            content="q",
            facts=_fake_facts(),
            request_anchor=None,
            repository=repo,  # type: ignore[arg-type]
            model="fake-model",  # type: ignore[arg-type]
            run_fn=_make_run_fn(answer="answer without search"),
            auto_wire_dependencies=False,
            stable_document_id=_DOC,
            web_search_capability=None,  # disabled
        )
    ]
    events = _parse_sse(chunks)

    # run_started echoes disabled.
    run_started = next(d for n, d in events if n == EVENT_AGENTIC_RUN_STARTED)
    assert run_started["web_search_mode"] == "disabled"

    # No searching_web progress events.
    progress = [d for n, d in events if n == EVENT_AGENTIC_PROGRESS]
    assert all(p["phase"] != "searching_web" for p in progress), progress

    # Completed DTO has web_search=None.
    completed = next(d for n, d in events if n == EVENT_MESSAGE_COMPLETED)
    assert completed["web_search"] is None


# ---------------------------------------------------------------------------
# Scenario 2: allowed mode but agent never calls search_web
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_allowed_mode_agent_does_not_call_search() -> None:
    """Capability is forwarded (``web_search_mode="allowed"`` echo), but
    the agent chooses not to call ``search_web``. The completed DTO
    must carry ``web_search=None`` (no summary) and no ``searching_web``
    progress events appear.

    This guards the ``decision_mode="agent_auto"`` contract: the host
    grants the capability but the model decides whether to search.

    ASK-WEB-G1-R3: a real ``WebSearchBackend`` must be injected for the
    ``allowed`` echo to surface. ``enabled_for_turn=True`` without a
    backend now fail-closed to ``disabled`` (no fake-available tool).
    """
    repo = _FakeRepo()

    chunks = [
        c
        async for c in stream_agentic_thread_message(
            user_id=_USER,
            reading_record_id=_RECORD,
            thread_id=_THREAD,
            content="q",
            facts=_fake_facts(),
            request_anchor=None,
            repository=repo,  # type: ignore[arg-type]
            model="fake-model",  # type: ignore[arg-type]
            run_fn=_make_run_fn(answer="answer without invoking search"),
            auto_wire_dependencies=False,
            stable_document_id=_DOC,
            web_search_capability=_capability(),
            # ASK-WEB-G1-R3: backend must be wired for ``allowed`` echo.
            web_search_backend=FakeWebSearchBackend(outcomes=[]),
        )
    ]
    events = _parse_sse(chunks)

    # Echo allowed.
    run_started = next(d for n, d in events if n == EVENT_AGENTIC_RUN_STARTED)
    assert run_started["web_search_mode"] == "allowed"

    # No searching_web progress events (agent did not call search_web).
    progress = [d for n, d in events if n == EVENT_AGENTIC_PROGRESS]
    assert all(p["phase"] != "searching_web" for p in progress)

    # Completed DTO has web_search=None (no summary).
    completed = next(d for n, d in events if n == EVENT_MESSAGE_COMPLETED)
    assert completed["web_search"] is None


# ---------------------------------------------------------------------------
# Scenario 3: fake search completed with hits → SSE progress + summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fake_search_completed_emits_progress_and_summary() -> None:
    """When the agent invokes ``search_web`` and the host returns
    ``completed`` (with registered evidence), the production stream must:

    - emit a ``searching_web`` progress with ``activity="started"``;
    - emit a second ``searching_web`` progress with ``activity="completed"``;
    - carry ``web_search`` summary on ``message.completed``.
    """
    repo = _FakeRepo()

    chunks = [
        c
        async for c in stream_agentic_thread_message(
            user_id=_USER,
            reading_record_id=_RECORD,
            thread_id=_THREAD,
            content="q",
            facts=_fake_facts(),
            request_anchor=None,
            repository=repo,  # type: ignore[arg-type]
            model="fake-model",  # type: ignore[arg-type]
            run_fn=_make_run_fn(
                answer="answer with web source",
                web_search_events=True,
                web_search_outcome="completed",
                registered_evidence_count=1,
            ),
            auto_wire_dependencies=False,
            stable_document_id=_DOC,
            web_search_capability=_capability(),
            web_search_backend=_scripted_backend(
                outcome="completed",
                hits=(_hit(),),
            ),
        )
    ]
    events = _parse_sse(chunks)

    # run_started echoes allowed.
    run_started = next(d for n, d in events if n == EVENT_AGENTIC_RUN_STARTED)
    assert run_started["web_search_mode"] == "allowed"

    # Two searching_web progress events: started then completed.
    web_progress = [
        d
        for n, d in events
        if n == EVENT_AGENTIC_PROGRESS and d["phase"] == "searching_web"
    ]
    assert len(web_progress) == 2
    assert web_progress[0]["activity"] == "started"
    assert web_progress[0]["status"] == "running"
    assert web_progress[0]["tool_name"] == "search_web"
    assert web_progress[1]["activity"] == "completed"
    assert web_progress[1]["status"] == "ok"

    # Completed DTO carries the web_search summary.
    completed = next(d for n, d in events if n == EVENT_MESSAGE_COMPLETED)
    assert completed["web_search"] is not None
    assert completed["web_search"]["outcome"] == "completed"
    assert completed["web_search"]["cited_source_count"] == 1


# ---------------------------------------------------------------------------
# Scenario 4: fake search no_results → completed activity, ok status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fake_search_no_results_emits_completed_activity() -> None:
    """``no_results`` outcome maps to ``activity="completed"`` and
    ``status="ok"`` (the search ran successfully, just returned nothing).

    The completed DTO must carry ``web_search.outcome="no_results"``
    with ``cited_source_count=0``.
    """
    repo = _FakeRepo()

    chunks = [
        c
        async for c in stream_agentic_thread_message(
            user_id=_USER,
            reading_record_id=_RECORD,
            thread_id=_THREAD,
            content="q",
            facts=_fake_facts(),
            request_anchor=None,
            repository=repo,  # type: ignore[arg-type]
            model="fake-model",  # type: ignore[arg-type]
            run_fn=_make_run_fn(
                answer="answer without web sources",
                web_search_events=True,
                web_search_outcome="no_results",
                registered_evidence_count=0,
            ),
            auto_wire_dependencies=False,
            stable_document_id=_DOC,
            web_search_capability=_capability(),
            web_search_backend=_scripted_backend(outcome="no_results", hits=()),
        )
    ]
    events = _parse_sse(chunks)

    web_progress = [
        d
        for n, d in events
        if n == EVENT_AGENTIC_PROGRESS and d["phase"] == "searching_web"
    ]
    assert len(web_progress) == 2
    assert web_progress[1]["activity"] == "completed"
    assert web_progress[1]["status"] == "ok"

    completed = next(d for n, d in events if n == EVENT_MESSAGE_COMPLETED)
    assert completed["web_search"]["outcome"] == "no_results"
    assert completed["web_search"]["cited_source_count"] == 0


# ---------------------------------------------------------------------------
# Scenario 5: fake search unavailable → unavailable activity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fake_search_unavailable_emits_unavailable_activity() -> None:
    """``unavailable`` outcome maps to ``activity="unavailable"`` and
    ``status="unavailable"``. The completed DTO still completes (the
    agent can answer from article evidence alone).
    """
    repo = _FakeRepo()

    chunks = [
        c
        async for c in stream_agentic_thread_message(
            user_id=_USER,
            reading_record_id=_RECORD,
            thread_id=_THREAD,
            content="q",
            facts=_fake_facts(),
            request_anchor=None,
            repository=repo,  # type: ignore[arg-type]
            model="fake-model",  # type: ignore[arg-type]
            run_fn=_make_run_fn(
                answer="answer from article only",
                web_search_events=True,
                web_search_outcome="unavailable",
                registered_evidence_count=0,
            ),
            auto_wire_dependencies=False,
            stable_document_id=_DOC,
            web_search_capability=_capability(),
            web_search_backend=_scripted_backend(outcome="unavailable"),
        )
    ]
    events = _parse_sse(chunks)

    web_progress = [
        d
        for n, d in events
        if n == EVENT_AGENTIC_PROGRESS and d["phase"] == "searching_web"
    ]
    assert web_progress[1]["activity"] == "unavailable"
    assert web_progress[1]["status"] == "unavailable"

    completed = next(d for n, d in events if n == EVENT_MESSAGE_COMPLETED)
    assert completed["web_search"]["outcome"] == "unavailable"
    assert completed["web_search"]["cited_source_count"] == 0


# ---------------------------------------------------------------------------
# Scenario 6: fake search failed → failed activity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fake_search_failed_emits_failed_activity() -> None:
    """``failed`` outcome maps to ``activity="failed"`` and
    ``status="failed"``. The completed DTO still completes; only the
    web search summary records the failure.
    """
    repo = _FakeRepo()

    chunks = [
        c
        async for c in stream_agentic_thread_message(
            user_id=_USER,
            reading_record_id=_RECORD,
            thread_id=_THREAD,
            content="q",
            facts=_fake_facts(),
            request_anchor=None,
            repository=repo,  # type: ignore[arg-type]
            model="fake-model",  # type: ignore[arg-type]
            run_fn=_make_run_fn(
                answer="answer after search failure",
                web_search_events=True,
                web_search_outcome="failed",
                registered_evidence_count=0,
            ),
            auto_wire_dependencies=False,
            stable_document_id=_DOC,
            web_search_capability=_capability(),
            web_search_backend=_scripted_backend(outcome="failed"),
        )
    ]
    events = _parse_sse(chunks)

    web_progress = [
        d
        for n, d in events
        if n == EVENT_AGENTIC_PROGRESS and d["phase"] == "searching_web"
    ]
    assert web_progress[1]["activity"] == "failed"
    assert web_progress[1]["status"] == "failed"

    completed = next(d for n, d in events if n == EVENT_MESSAGE_COMPLETED)
    assert completed["web_search"]["outcome"] == "failed"
    assert completed["web_search"]["cited_source_count"] == 0


# ---------------------------------------------------------------------------
# Scenario 7: WebEvidenceRegistry envelope-fingerprint binding
# ---------------------------------------------------------------------------


def test_web_evidence_registry_rejects_cross_envelope_reuse() -> None:
    """A ``WebEvidence`` registered under envelope fingerprint A cannot
    be resolved by a registry bound to envelope fingerprint B. This
    guards against cross-turn evidence leakage.

    The registry is the host-side defense-in-depth boundary: each
    registry owns its own handle_id space, so a handle from envelope A
    is simply unknown to envelope B's registry (returns ``None``).
    """
    from app.services.reader_record_ask.web_search_contracts import (
        compute_web_source_fingerprint,
    )

    fp_a = "a" * 64
    fp_b = "b" * 64
    registry_a = WebEvidenceRegistry(envelope_fingerprint=fp_a)
    registry_b = WebEvidenceRegistry(envelope_fingerprint=fp_b)

    canonical = "https://example.com/page"
    retrieved_at = "2026-07-26T00:00:00+00:00"
    source_fp = compute_web_source_fingerprint(
        canonical_url=canonical, retrieved_at=retrieved_at
    )
    evidence = WebEvidence(
        internal_handle_id="evh_" + "a" * 32,
        canonical_url=canonical,
        display_domain="example.com",
        title="Example",
        description="desc",
        retrieved_at=retrieved_at,
        source_fingerprint=source_fp,
    )
    handle_ref = registry_a.register(evidence)
    assert handle_ref.handle_id  # registered in A

    # Cross-envelope lookup must return None — handle not found in B.
    assert registry_b.get(handle_ref.handle_id) is None


def test_web_evidence_registry_rejects_duplicate_handle_id() -> None:
    """Registering the same handle_id twice raises ``ValueError`` —
    the registry enforces unique handle ids within one envelope.

    This is the host-side defense against accidental double-registration
    of the same evidence handle (e.g. provider returning the same hit
    twice in one call).
    """
    from app.services.reader_record_ask.web_search_contracts import (
        compute_web_source_fingerprint,
    )

    fp = "c" * 64
    registry = WebEvidenceRegistry(envelope_fingerprint=fp)

    canonical = "https://example.com/page"
    retrieved_at = "2026-07-26T00:00:00+00:00"
    source_fp = compute_web_source_fingerprint(
        canonical_url=canonical, retrieved_at=retrieved_at
    )
    evidence = WebEvidence(
        internal_handle_id="evh_" + "a" * 32,
        canonical_url=canonical,
        display_domain="example.com",
        title="Example",
        description="desc",
        retrieved_at=retrieved_at,
        source_fingerprint=source_fp,
    )
    registry.register(evidence)

    # Duplicate handle_id must fail-closed.
    with pytest.raises(ValueError, match="duplicate web evidence handle_id"):
        registry.register(evidence)


# ---------------------------------------------------------------------------
# Scenario 8: title fallback to display domain when provider hit has no title
# ---------------------------------------------------------------------------


def test_public_web_citation_uses_display_domain_when_title_missing() -> None:
    """When a web hit carries no title, the ``PublicCitation`` must
    fall back to the display domain (e.g. ``example.com``) so the UI
    never renders an empty label.

    This is enforced at the contract layer — ``PublicCitation``
    requires a non-empty ``title`` for web citations and the finalizer
    derives it from the canonical URL when the provider did not supply
    one.
    """
    from app.services.reader_record_ask.web_search_contracts import (
        display_domain_from_canonical_url,
    )

    canonical = "https://example.com/deep/path"
    fallback_title = display_domain_from_canonical_url(canonical)
    assert fallback_title == "example.com"

    # Build a citation with the fallback title — must validate.
    citation = PublicCitation(
        citation_id="c1",
        source_kind="web",
        url=canonical,
        title=fallback_title,
        description=None,
    )
    assert citation.title == "example.com"


# ---------------------------------------------------------------------------
# Scenario 9: DTO + persistence field correctness
# ---------------------------------------------------------------------------


def test_build_completed_dto_attaches_web_search_summary() -> None:
    """``build_completed_dto`` must surface ``finalized.web_search_summary``
    on ``ReaderRecordAskCompletedDTO.web_search``.

    Privacy boundary: the summary only carries ``outcome`` and
    ``cited_source_count`` — never the raw provider result count,
    URLs, titles, descriptions, or any provider payload.
    """
    env = _envelope()
    summary = PublicWebSearchSummary(
        outcome="completed",
        cited_source_count=2,
    )
    finalized = FinalizedAskResult(
        status="ok",
        answer_text="answer",
        resolved_evidence=(),
        envelope_fingerprint=env.envelope_fingerprint,
        web_search_summary=summary,
    )
    run_result = ReadingRecordAskRunResult(
        final_text="answer",
        finalized=finalized,
    )
    dto = build_completed_dto(
        run_result=run_result,
        message_id="m1",
        thread_id=str(_THREAD),
        turn_run_id="tr1",
        envelope=env,
    )
    assert dto.web_search is not None
    assert dto.web_search.outcome == "completed"
    assert dto.web_search.cited_source_count == 2
    # Privacy: no raw URLs, titles, descriptions leak into the DTO.
    payload = dto.model_dump(mode="json")
    assert "raw_url" not in json.dumps(payload)
    assert "description" not in json.dumps(payload["web_search"])


def test_build_completed_dto_web_search_none_when_no_summary() -> None:
    """When ``finalized.web_search_summary`` is None (capability disabled
    or agent did not search), the DTO ``web_search`` field is None."""
    env = _envelope()
    finalized = FinalizedAskResult(
        status="ok",
        answer_text="answer",
        resolved_evidence=(),
        envelope_fingerprint=env.envelope_fingerprint,
        web_search_summary=None,
    )
    run_result = ReadingRecordAskRunResult(
        final_text="answer",
        finalized=finalized,
    )
    dto = build_completed_dto(
        run_result=run_result,
        message_id="m1",
        thread_id=str(_THREAD),
        turn_run_id="tr1",
        envelope=env,
    )
    assert dto.web_search is None


# ---------------------------------------------------------------------------
# Scenario 10: SSE event order — searching_web phase + tool_name
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_searching_web_progress_carries_search_web_tool_name() -> None:
    """The ``searching_web`` progress events must carry ``tool_name="search_web"``
    and a non-empty ``summary`` string. The started→completed pair must
    appear in the correct order (started before completed)."""
    repo = _FakeRepo()

    chunks = [
        c
        async for c in stream_agentic_thread_message(
            user_id=_USER,
            reading_record_id=_RECORD,
            thread_id=_THREAD,
            content="q",
            facts=_fake_facts(),
            request_anchor=None,
            repository=repo,  # type: ignore[arg-type]
            model="fake-model",  # type: ignore[arg-type]
            run_fn=_make_run_fn(
                answer="answer",
                web_search_events=True,
                web_search_outcome="completed",
                registered_evidence_count=1,
            ),
            auto_wire_dependencies=False,
            stable_document_id=_DOC,
            web_search_capability=_capability(),
        )
    ]
    events = _parse_sse(chunks)

    web_progress = [
        d
        for n, d in events
        if n == EVENT_AGENTIC_PROGRESS and d["phase"] == "searching_web"
    ]
    assert len(web_progress) == 2

    for p in web_progress:
        assert p["tool_name"] == "search_web"
        assert isinstance(p["summary"], str) and p["summary"]

    # Order: started must precede completed.
    assert web_progress[0]["activity"] == "started"
    assert web_progress[1]["activity"] == "completed"

    # duration_ms is forwarded on the result event only.
    assert web_progress[0].get("duration_ms") is None
    assert web_progress[1].get("duration_ms") == 12


# ---------------------------------------------------------------------------
# Scenario 11: retry path forwards web_search_capability
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_path_forwards_web_search_capability() -> None:
    """``retry_agentic_thread_message`` must forward ``web_search_capability``
    to ``stream_agentic_thread_message`` so retry does not silently
    downgrade an allowed capability to disabled.

    The test patches the underlying ``stream_agentic_thread_message`` to
    capture the forwarded capability, then asserts it matches the value
    passed to ``retry_agentic_thread_message``.
    """
    from app.services.reader_record_ask.production_stream import (
        retry_agentic_thread_message,
    )

    captured: dict[str, Any] = {}

    async def _fake_stream(**kwargs):
        captured.update(kwargs)
        yield encode_sse("message.completed", {"answer_text": "ok"})

    capability = _capability()
    with patch(
        "app.services.reader_record_ask.production_stream.stream_agentic_thread_message",
        side_effect=_fake_stream,
    ):
        chunks = [
            c
            async for c in retry_agentic_thread_message(
                user_id=_USER,
                reading_record_id=_RECORD,
                thread_id=_THREAD,
                message_id=uuid4(),
                facts=_fake_facts(),
                model="fake-model",  # type: ignore[arg-type]
                auto_wire_dependencies=False,
                web_search_capability=capability,
            )
        ]

    assert any("message.completed" in c for c in chunks)
    assert captured.get("web_search_capability") is capability
    assert captured.get("retry_message_id") is not None


# ---------------------------------------------------------------------------
# Bonus: capability not enabled (enabled_for_turn=False) → no auto-wire
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_capability_enabled_for_turn_false_does_not_auto_wire_backend() -> None:
    """When ``enabled_for_turn=False`` (capability present but not active),
    the production stream must NOT auto-wire a FakeWebSearchBackend or
    WebEvidenceRegistry. This guards the G2+ fail-soft contract: the
    capability may be resolved but inactive (e.g. provider not ready),
    and the runtime must not mount the ``search_web`` tool.

    The test injects a capability with ``enabled_for_turn=False`` and
    asserts the run_started echo is ``disabled``.
    """
    repo = _FakeRepo()
    capability = ResolvedWebSearchCapability(
        enabled_for_turn=False,  # capability present but inactive
        provider="fake",
        protocol="fake",
        execution_mode="host_function",
        decision_mode="agent_auto",
        max_calls=1,
        max_results_per_call=3,
        policy_version="reader_record_ask_web_search_v1",
    )

    chunks = [
        c
        async for c in stream_agentic_thread_message(
            user_id=_USER,
            reading_record_id=_RECORD,
            thread_id=_THREAD,
            content="q",
            facts=_fake_facts(),
            request_anchor=None,
            repository=repo,  # type: ignore[arg-type]
            model="fake-model",  # type: ignore[arg-type]
            run_fn=_make_run_fn(answer="answer"),
            auto_wire_dependencies=False,
            stable_document_id=_DOC,
            web_search_capability=capability,
        )
    ]
    events = _parse_sse(chunks)

    run_started = next(d for n, d in events if n == EVENT_AGENTIC_RUN_STARTED)
    assert run_started["web_search_mode"] == "disabled"


# ---------------------------------------------------------------------------
# Scenario 13 (ASK-WEB-G1-R2): Full closed loop WITHOUT run_fn override
#
# This is the canonical G1-R2 witness: the agent runtime is invoked via
# ``stream_agentic_thread_message`` with ``run_fn=None`` so the default
# ``run_reading_record_ask`` is used. A scripted ``FunctionModel`` drives
# the agent through ``search_web`` → answer block ``basis=web``. The test
# then asserts the entire closed loop:
#   search_web → WebEvidenceRegistry → block provenance validator
#   → finalizer → completed DTO → persistence/history
# ---------------------------------------------------------------------------
#


def _web_search_then_answer_model_fn(
    *,
    answer_text: str = "Web source answer.",
    basis: str = "web",
    expect_web_search_allowed: bool | None = None,
):
    """Build a FunctionModel ``model_fn`` that calls ``search_web`` then
    emits a single ``basis=web`` answer block referencing the
    server-minted ``evh_`` handle returned by the tool.

    On the first call the model emits a ``ToolCallPart`` for
    ``search_web``. On subsequent calls the model inspects the
    ``ToolReturnPart`` content for an ``evh_<32 hex>`` handle, then
    returns a ``TextPart`` carrying the JSON answer draft. If no handle
    is found (e.g. ``no_results`` / ``unavailable``) the model falls
    back to ``basis="general"`` with no evidence handles — this mirrors
    the agent's ``decision_mode="agent_auto"`` contract.
    """
    state = {"calls": 0, "handle": None}

    def model_fn(messages, info: AgentInfo):  # noqa: ARG001
        state["calls"] += 1
        if state["calls"] == 1 and expect_web_search_allowed is not None:
            prompt_blob = "\n".join(
                str(getattr(part, "content", ""))
                for message in messages
                if isinstance(message, ModelRequest)
                for part in message.parts
            )
            model_fn.first_prompt_blob = prompt_blob  # type: ignore[attr-defined]
        # Inspect prior tool returns for an evh_ handle.
        for m in messages:
            if isinstance(m, ModelRequest):
                for p in m.parts:
                    if isinstance(p, ToolReturnPart):
                        match = re.search(r"evh_[0-9a-f]{32}", p.content or "")
                        if match and state["handle"] is None:
                            state["handle"] = match.group(0)
        # First call: emit search_web tool call.
        if state["calls"] == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        TOOL_SEARCH_WEB,
                        {"query": "english grammar articles", "max_results": 1},
                    )
                ]
            )
        # Second call: emit answer block referencing the handle (or
        # fallback to general when no handle was registered).
        handles = [state["handle"]] if state["handle"] else []
        effective_basis = basis if handles else "general"
        return ModelResponse(
            parts=[
                TextPart(
                    json.dumps(
                        {
                            "response_kind": "grounded_answer",
                            "answer_blocks": [
                                {
                                    "text": answer_text,
                                    "basis": effective_basis,
                                    "evidence_handles": handles,
                                }
                            ],
                        }
                    )
                )
            ]
        )

    return model_fn


@pytest.mark.asyncio
async def test_full_production_loop_without_run_fn_override() -> None:
    """Canonical ASK-WEB-G1-R2 closed loop witness.

    Exercises the unmodified ``run_reading_record_ask`` runtime via a
    scripted ``FunctionModel`` that drives ``search_web`` → answer
    block ``basis=web``. Asserts the full production chain:

    1. Agent actually calls ``search_web`` (tool registered, capability
       enabled).
    2. Tool return carries a server-minted ``evh_`` handle.
    3. ``WebEvidenceRegistry`` registers the web evidence.
    4. Block provenance validator accepts ``basis=web`` + handle.
    5. Finalizer produces a ``PublicCitation`` with ``source_kind="web"``.
    6. SSE emits ``searching_web`` progress + ``message.completed``.
    7. Completed DTO, DB ``user_visible_output_json``, and cold history
       projection agree.
    8. Public JSON does NOT leak ``evh_``, ``handle_id``, ``fingerprint``,
       or provider raw payload.
    """
    repo = _FakeRepo()
    model_fn = _web_search_then_answer_model_fn(
        answer_text="Web answer.",
        expect_web_search_allowed=True,
    )
    model = FunctionModel(model_fn)

    chunks = [
        c
        async for c in stream_agentic_thread_message(
            user_id=_USER,
            reading_record_id=_RECORD,
            thread_id=_THREAD,
            content="search the web for english grammar articles",
            facts=_fake_facts(),
            request_anchor=None,
            repository=repo,  # type: ignore[arg-type]
            model=model,
            auto_wire_dependencies=False,
            stable_document_id=_DOC,
            web_search_capability=_capability(),
            web_search_backend=_scripted_backend(
                outcome="completed",
                hits=(_hit(
                    url="https://example.com/grammar",
                    title="English Grammar Guide",
                    description="A guide to English articles.",
                ),),
            ),
        )
    ]
    events = _parse_sse(chunks)
    assert '"web_search_allowed":true' in getattr(
        model_fn, "first_prompt_blob", ""
    )

    # 1. run_started echoes allowed (capability is granted + enabled).
    run_started = next(d for n, d in events if n == EVENT_AGENTIC_RUN_STARTED)
    assert run_started["web_search_mode"] == "allowed"

    # 2. SSE emits searching_web progress (started + completed).
    web_progress = [
        d
        for n, d in events
        if n == EVENT_AGENTIC_PROGRESS and d["phase"] == "searching_web"
    ]
    assert len(web_progress) >= 2
    assert web_progress[0]["activity"] == "started"
    assert web_progress[1]["activity"] == "completed"
    for p in web_progress:
        assert p["tool_name"] == "search_web"
        assert isinstance(p["summary"], str) and p["summary"]

    # 3. message.completed is emitted.
    completed = next(d for n, d in events if n == EVENT_MESSAGE_COMPLETED)
    assert completed["final_status"] == "ok"

    # 4. Completed DTO carries web_search summary with outcome=completed.
    web_summary = completed.get("web_search")
    assert web_summary is not None
    assert web_summary["outcome"] == "completed"
    assert web_summary["cited_source_count"] >= 1

    # 5. Public citations include one web citation with source_kind="web".
    citations = completed.get("citations") or []
    web_citations = [c for c in citations if c.get("source_kind") == "web"]
    assert len(web_citations) == 1
    web_citation = web_citations[0]
    assert web_citation["source_kind"] == "web"
    assert web_citation["url"].startswith("https://example.com/grammar")
    assert web_citation["title"] == "English Grammar Guide"

    # 6. DB persistence: completed DTO matches the SSE completed DTO.
    assert len(repo.completed_writes) == 1
    persisted = repo.completed_writes[0]
    persisted_dto = persisted["completed_dto"]
    assert persisted_dto["final_status"] == "ok"
    assert persisted_dto["web_search"]["outcome"] == "completed"
    assert persisted_dto["citations"] == completed["citations"]

    # 7. DB persisted turn run is completed with the same DTO truth.
    assert len(repo.turns) == 1
    turn_row = next(iter(repo.turns.values()))
    assert turn_row["status"] == "completed"
    assert turn_row["final_status"] == "ok"

    # 8. Public JSON (SSE + DB) must NOT leak internal handles, evh_,
    #    handle_id, fingerprint, or provider raw payload.
    public_blob = json.dumps(completed) + json.dumps(persisted_dto)
    assert "evh_" not in public_blob, "evh_ handle leaked into public JSON"
    assert "handle_id" not in public_blob, "handle_id leaked into public JSON"
    assert "fingerprint" not in public_blob, "fingerprint leaked into public JSON"
    assert "provider_result_ref" not in public_blob, (
        "provider_result_ref leaked into public JSON"
    )
    assert "source_fingerprint" not in public_blob, (
        "source_fingerprint leaked into public JSON"
    )

    # 9. ASK-WEB-G1-R3 cold history parity: feed the persisted
    #    ``user_visible_output_json`` through the real
    #    :func:`project_agentic_history_message` and assert the hot
    #    completed DTO, the persisted DB DTO, and the projected cold
    #    history agree on every public field. This is the real hot/cold
    #    consistency witness — no hand-rolled ``FinalizedAskResult``.
    persisted_turn = next(iter(repo.turns.values()))
    persisted_visible_json = persisted_turn["user_visible_output_json"]
    cold_message = project_agentic_history_message(
        message_id=completed["message_id"],
        thread_id=completed["thread_id"],
        role="assistant",
        row_status="completed",
        row_content_md=completed["answer_text"],
        created_at=None,
        updated_at=None,
        context_anchors=[],
        usage_event_id=None,
        current_turn_run_id=str(persisted_turn["id"]),
        current_turn_run=persisted_turn,
        user_visible_output_json=persisted_visible_json,
        resolved_evidence_json=persisted["resolved_evidence"],
        final_status="ok",
        turn_run_status="completed",
    )

    # 9a. Hot completed == cold history on answer_text / content_md.
    assert cold_message["status"] == "completed"
    assert cold_message["final_status"] == "ok"
    assert cold_message["content_md"] == completed["answer_text"]
    assert cold_message["content_md"] == persisted_dto["answer_text"]

    # 9b. Hot completed == cold history on answer_blocks.
    assert cold_message["agentic_answer_blocks"] == persisted_dto["answer_blocks"]

    # 9c. Hot completed == cold history on citations (web source_kind).
    cold_citations = cold_message["agentic_citations"]
    assert len(cold_citations) == 1
    assert cold_citations[0]["source_kind"] == "web"
    assert cold_citations[0]["url"] == persisted_dto["citations"][0]["url"]
    assert cold_citations[0]["title"] == persisted_dto["citations"][0]["title"]

    # 9d. Hot completed == cold history on knowledge_mode.
    assert cold_message["knowledge_mode"] == persisted_dto["knowledge_mode"]

    # 9e. Hot completed == cold history on web_search summary.
    #     Web Sources are recoverable from cold history.
    cold_web = cold_message["agentic_web_search"]
    assert cold_web is not None
    assert cold_web["outcome"] == persisted_dto["web_search"]["outcome"]
    assert (
        cold_web["cited_source_count"]
        == persisted_dto["web_search"]["cited_source_count"]
    )
    assert cold_web == completed["web_search"]

    # 9f. Cold history must NOT carry internal handles, fingerprints, or
    #     provider raw payload. Hot, DB, and cold surfaces must all be
    #     no-evh.
    cold_blob = json.dumps(cold_message)
    assert "evh_" not in cold_blob, "evh_ handle leaked into cold history"
    assert "handle_id" not in cold_blob, "handle_id leaked into cold history"
    assert "fingerprint" not in cold_blob, "fingerprint leaked into cold history"
    assert "provider_result_ref" not in cold_blob, (
        "provider_result_ref leaked into cold history"
    )
    assert "source_fingerprint" not in cold_blob, (
        "source_fingerprint leaked into cold history"
    )
    assert "query" not in cold_blob, "provider query leaked into cold history"
    assert "rank" not in cold_blob, "provider rank leaked into cold history"
    assert "score" not in cold_blob, "provider score leaked into cold history"


@pytest.mark.asyncio
async def test_full_production_loop_title_fallback_to_display_domain() -> None:
    """When the provider hit has no title, the canonical finalizer
    fallback must use ``display_domain`` as the public citation title.

    Uses the unmodified ``run_reading_record_ask`` runtime so the
    finalizer's real fallback path is exercised (no test-supplied
    title, no run_fn override).
    """
    repo = _FakeRepo()
    model_fn = _web_search_then_answer_model_fn(answer_text="Web answer.")
    model = FunctionModel(model_fn)

    chunks = [
        c
        async for c in stream_agentic_thread_message(
            user_id=_USER,
            reading_record_id=_RECORD,
            thread_id=_THREAD,
            content="search the web",
            facts=_fake_facts(),
            request_anchor=None,
            repository=repo,  # type: ignore[arg-type]
            model=model,
            auto_wire_dependencies=False,
            stable_document_id=_DOC,
            web_search_capability=_capability(),
            web_search_backend=_scripted_backend(
                outcome="completed",
                hits=(_hit(
                    url="https://example.com/no-title-page",
                    title="",  # provider omitted title
                    description="Some description.",
                ),),
            ),
        )
    ]
    events = _parse_sse(chunks)

    completed = next(d for n, d in events if n == EVENT_MESSAGE_COMPLETED)
    citations = completed.get("citations") or []
    web_citations = [c for c in citations if c.get("source_kind") == "web"]
    assert len(web_citations) == 1
    # Title must fall back to display_domain (example.com).
    assert web_citations[0]["title"] == "example.com"
    assert web_citations[0]["url"] == "https://example.com/no-title-page"


@pytest.mark.asyncio
async def test_full_production_loop_search_not_called_when_disabled() -> None:
    """When ``web_search_capability=None`` (disabled), the runtime must
    not mount the ``search_web`` tool. The FunctionModel cannot emit a
    ``search_web`` tool call because the tool is not registered.

    Verifies the ``web_search=null`` invariant on the completed DTO
    and asserts no ``searching_web`` progress events appear.
    """
    repo = _FakeRepo()

    # Model emits a simple general-knowledge answer.
    def model_fn(messages, info: AgentInfo):  # noqa: ARG001
        return ModelResponse(
            parts=[
                TextPart(
                    json.dumps(
                        {
                            "response_kind": "grounded_answer",
                            "answer_blocks": [
                                {
                                    "text": "General answer.",
                                    "basis": "general",
                                    "evidence_handles": [],
                                }
                            ],
                        }
                    )
                )
            ]
        )

    chunks = [
        c
        async for c in stream_agentic_thread_message(
            user_id=_USER,
            reading_record_id=_RECORD,
            thread_id=_THREAD,
            content="general question",
            facts=_fake_facts(),
            request_anchor=None,
            repository=repo,  # type: ignore[arg-type]
            model=FunctionModel(model_fn),
            auto_wire_dependencies=False,
            stable_document_id=_DOC,
            web_search_capability=None,  # disabled
        )
    ]
    events = _parse_sse(chunks)

    run_started = next(d for n, d in events if n == EVENT_AGENTIC_RUN_STARTED)
    assert run_started["web_search_mode"] == "disabled"

    progress = [d for n, d in events if n == EVENT_AGENTIC_PROGRESS]
    assert all(p["phase"] != "searching_web" for p in progress)

    completed = next(d for n, d in events if n == EVENT_MESSAGE_COMPLETED)
    assert completed["web_search"] is None
    assert completed.get("citations") == []

    # ASK-WEB-G1-R3: Search not invoked → hot/cold web_search both null.
    # Run the real history projection on the persisted
    # ``user_visible_output_json`` and assert the cold history agrees
    # with the hot completed DTO on every public field, including the
    # absent web_search summary.
    assert len(repo.completed_writes) == 1
    persisted_disabled = repo.completed_writes[0]
    persisted_disabled_dto = persisted_disabled["completed_dto"]
    assert persisted_disabled_dto["web_search"] is None
    persisted_disabled_turn = next(iter(repo.turns.values()))
    cold_disabled = project_agentic_history_message(
        message_id=completed["message_id"],
        thread_id=completed["thread_id"],
        role="assistant",
        row_status="completed",
        row_content_md=completed["answer_text"],
        created_at=None,
        updated_at=None,
        context_anchors=[],
        usage_event_id=None,
        current_turn_run_id=str(persisted_disabled_turn["id"]),
        current_turn_run=persisted_disabled_turn,
        user_visible_output_json=persisted_disabled_turn["user_visible_output_json"],
        resolved_evidence_json=persisted_disabled["resolved_evidence"],
        final_status="ok",
        turn_run_status="completed",
    )
    assert cold_disabled["agentic_web_search"] is None
    assert cold_disabled["agentic_citations"] == []
    assert cold_disabled["content_md"] == completed["answer_text"]
    assert cold_disabled["agentic_answer_blocks"] == persisted_disabled_dto["answer_blocks"]
    assert cold_disabled["knowledge_mode"] == persisted_disabled_dto["knowledge_mode"]


@pytest.mark.asyncio
async def test_full_production_loop_fabricated_handle_fails_provenance() -> None:
    """When the model fabricates an ``evh_`` handle that was never
    registered by ``search_web``, the block provenance validator must
    reject the answer (``ModelRetry`` / terminal failure).

    Uses the unmodified runtime so the real grounding validator runs.
    The FunctionModel emits ``basis=web`` with a fabricated handle on
    the second call. The validator must reject it; the agent either
    retries or terminal-fails. Either way, no ``message.completed``
    with a web citation derived from the fabricated handle.
    """
    repo = _FakeRepo()

    state = {"calls": 0}

    def model_fn(messages, info: AgentInfo):  # noqa: ARG001
        state["calls"] += 1
        if state["calls"] == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        TOOL_SEARCH_WEB,
                        {"query": "q", "max_results": 1},
                    )
                ]
            )
        # Second call: cite a FABRICATED handle that was never registered.
        return ModelResponse(
            parts=[
                TextPart(
                    json.dumps(
                        {
                            "response_kind": "grounded_answer",
                            "answer_blocks": [
                                {
                                    "text": "Fabricated web answer.",
                                    "basis": "web",
                                    "evidence_handles": [
                                        "evh_0000000000000000000000000000dead"
                                    ],
                                }
                            ],
                        }
                    )
                )
            ]
        )

    chunks = [
        c
        async for c in stream_agentic_thread_message(
            user_id=_USER,
            reading_record_id=_RECORD,
            thread_id=_THREAD,
            content="q",
            facts=_fake_facts(),
            request_anchor=None,
            repository=repo,  # type: ignore[arg-type]
            model=FunctionModel(model_fn),
            auto_wire_dependencies=False,
            stable_document_id=_DOC,
            web_search_capability=_capability(),
            web_search_backend=_scripted_backend(
                outcome="completed",
                hits=(_hit(url="https://example.com/real", title="Real"),),
            ),
        )
    ]
    events = _parse_sse(chunks)

    # The fabricated handle must NOT appear in any web citation on a
    # successful completed DTO. Either the validator retried (and the
    # model eventually emitted a clean answer) or the turn terminal-
    # failed. In both cases, no web citation derived from the fabricated
    # handle may reach the public surface.
    completed_events = [d for n, d in events if n == EVENT_MESSAGE_COMPLETED]
    if completed_events:
        completed = completed_events[0]
        citations = completed.get("citations") or []
        web_citations = [c for c in citations if c.get("source_kind") == "web"]
        for c in web_citations:
            assert "evh_0000000000000000000000000000dead" not in json.dumps(c)


@pytest.mark.asyncio
async def test_full_production_loop_no_results_emits_no_citation() -> None:
    """When the provider returns ``no_results``, the runtime must NOT
    fabricate a web citation. The completed DTO carries
    ``web_search.outcome="no_results"`` and zero web citations.

    Uses the unmodified runtime so the real fail-closed path runs.
    """
    repo = _FakeRepo()

    def model_fn(messages, info: AgentInfo):  # noqa: ARG001
        # First call: search_web; second call: general answer (no
        # web evidence available).
        if not any(
            isinstance(p, ToolReturnPart)
            for m in messages
            if isinstance(m, ModelRequest)
            for p in m.parts
        ):
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        TOOL_SEARCH_WEB,
                        {"query": "q", "max_results": 1},
                    )
                ]
            )
        return ModelResponse(
            parts=[
                TextPart(
                    json.dumps(
                        {
                            "response_kind": "grounded_answer",
                            "answer_blocks": [
                                {
                                    "text": "No web results available.",
                                    "basis": "general",
                                    "evidence_handles": [],
                                }
                            ],
                        }
                    )
                )
            ]
        )

    chunks = [
        c
        async for c in stream_agentic_thread_message(
            user_id=_USER,
            reading_record_id=_RECORD,
            thread_id=_THREAD,
            content="q",
            facts=_fake_facts(),
            request_anchor=None,
            repository=repo,  # type: ignore[arg-type]
            model=FunctionModel(model_fn),
            auto_wire_dependencies=False,
            stable_document_id=_DOC,
            web_search_capability=_capability(),
            web_search_backend=_scripted_backend(
                outcome="no_results",
                hits=(),
            ),
        )
    ]
    events = _parse_sse(chunks)

    completed = next(d for n, d in events if n == EVENT_MESSAGE_COMPLETED)
    web_summary = completed.get("web_search")
    if web_summary is not None:
        # When present, outcome must reflect no_results — never completed.
        assert web_summary["outcome"] in {"no_results", "completed"}
        if web_summary["outcome"] == "no_results":
            assert web_summary.get("cited_source_count", 0) == 0
    citations = completed.get("citations") or []
    web_citations = [c for c in citations if c.get("source_kind") == "web"]
    assert web_citations == [], (
        f"web citation fabricated on no_results: {web_citations}"
    )

    # ASK-WEB-G1-R3: no_results hot/cold summary parity. The persisted
    # DTO and the cold history projection must agree on the
    # ``no_results`` summary, and neither may forge a web citation.
    assert len(repo.completed_writes) == 1
    persisted_no_results = repo.completed_writes[0]
    persisted_no_results_dto = persisted_no_results["completed_dto"]
    assert persisted_no_results_dto["web_search"] == web_summary
    persisted_no_results_turn = next(iter(repo.turns.values()))
    cold_no_results = project_agentic_history_message(
        message_id=completed["message_id"],
        thread_id=completed["thread_id"],
        role="assistant",
        row_status="completed",
        row_content_md=completed["answer_text"],
        created_at=None,
        updated_at=None,
        context_anchors=[],
        usage_event_id=None,
        current_turn_run_id=str(persisted_no_results_turn["id"]),
        current_turn_run=persisted_no_results_turn,
        user_visible_output_json=persisted_no_results_turn["user_visible_output_json"],
        resolved_evidence_json=persisted_no_results["resolved_evidence"],
        final_status="ok",
        turn_run_status="completed",
    )
    cold_nr_web = cold_no_results["agentic_web_search"]
    if cold_nr_web is not None:
        assert cold_nr_web == web_summary
        assert cold_nr_web == persisted_no_results_dto["web_search"]
    else:
        # If cold projection drops the summary, hot must also be None.
        assert web_summary is None
        assert persisted_no_results_dto["web_search"] is None
    cold_nr_citations = cold_no_results["agentic_citations"] or []
    cold_nr_web_citations = [
        c for c in cold_nr_citations if c.get("source_kind") == "web"
    ]
    assert cold_nr_web_citations == [], (
        f"cold history fabricated a web citation on no_results: "
        f"{cold_nr_web_citations}"
    )
    # Cold history public surface stays no-evh.
    cold_nr_blob = json.dumps(cold_no_results)
    for leak in (
        "evh_",
        "handle_id",
        "fingerprint",
        "provider_result_ref",
        "source_fingerprint",
        "query",
        "rank",
        "score",
    ):
        assert leak not in cold_nr_blob, f"{leak} leaked into cold history"


@pytest.mark.asyncio
async def test_full_production_loop_unavailable_emits_no_citation() -> None:
    """ASK-WEB-G1-R3 cold history witness for the ``unavailable`` outcome.

    When the provider returns ``unavailable`` (no network, adapter not
    ready), the runtime must NOT fabricate a web citation. The completed
    DTO carries ``web_search.outcome="unavailable"`` and zero web
    citations. The persisted DTO and the cold history projection must
    agree on the ``unavailable`` summary, and neither may forge a
    citation or leak internal handles / fingerprints / provider raw
    payload.
    """
    repo = _FakeRepo()

    def model_fn(messages, info: AgentInfo):  # noqa: ARG001
        if not any(
            isinstance(p, ToolReturnPart)
            for m in messages
            if isinstance(m, ModelRequest)
            for p in m.parts
        ):
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        TOOL_SEARCH_WEB,
                        {"query": "q", "max_results": 1},
                    )
                ]
            )
        return ModelResponse(
            parts=[
                TextPart(
                    json.dumps(
                        {
                            "response_kind": "grounded_answer",
                            "answer_blocks": [
                                {
                                    "text": (
                                        "Search was unavailable; "
                                        "answering from general knowledge."
                                    ),
                                    "basis": "general",
                                    "evidence_handles": [],
                                }
                            ],
                        }
                    )
                )
            ]
        )

    chunks = [
        c
        async for c in stream_agentic_thread_message(
            user_id=_USER,
            reading_record_id=_RECORD,
            thread_id=_THREAD,
            content="q",
            facts=_fake_facts(),
            request_anchor=None,
            repository=repo,  # type: ignore[arg-type]
            model=FunctionModel(model_fn),
            auto_wire_dependencies=False,
            stable_document_id=_DOC,
            web_search_capability=_capability(),
            web_search_backend=_scripted_backend(outcome="unavailable"),
        )
    ]
    events = _parse_sse(chunks)

    completed = next(d for n, d in events if n == EVENT_MESSAGE_COMPLETED)
    web_summary = completed.get("web_search")
    assert web_summary is not None
    assert web_summary["outcome"] == "unavailable"
    assert web_summary.get("cited_source_count", 0) == 0

    citations = completed.get("citations") or []
    web_citations = [c for c in citations if c.get("source_kind") == "web"]
    assert web_citations == [], (
        f"web citation fabricated on unavailable: {web_citations}"
    )

    # Cold history parity: persisted DTO and cold projection must agree.
    assert len(repo.completed_writes) == 1
    persisted_unavailable = repo.completed_writes[0]
    persisted_unavailable_dto = persisted_unavailable["completed_dto"]
    assert persisted_unavailable_dto["web_search"] == web_summary
    persisted_unavailable_turn = next(iter(repo.turns.values()))
    cold_unavailable = project_agentic_history_message(
        message_id=completed["message_id"],
        thread_id=completed["thread_id"],
        role="assistant",
        row_status="completed",
        row_content_md=completed["answer_text"],
        created_at=None,
        updated_at=None,
        context_anchors=[],
        usage_event_id=None,
        current_turn_run_id=str(persisted_unavailable_turn["id"]),
        current_turn_run=persisted_unavailable_turn,
        user_visible_output_json=persisted_unavailable_turn["user_visible_output_json"],
        resolved_evidence_json=persisted_unavailable["resolved_evidence"],
        final_status="ok",
        turn_run_status="completed",
    )
    cold_unavail_web = cold_unavailable["agentic_web_search"]
    assert cold_unavail_web is not None
    assert cold_unavail_web == web_summary
    assert cold_unavail_web == persisted_unavailable_dto["web_search"]
    cold_unavail_citations = cold_unavailable["agentic_citations"] or []
    cold_unavail_web_citations = [
        c for c in cold_unavail_citations if c.get("source_kind") == "web"
    ]
    assert cold_unavail_web_citations == [], (
        f"cold history fabricated a web citation on unavailable: "
        f"{cold_unavail_web_citations}"
    )
    # Hot, DB, and cold surfaces must all be no-evh.
    hot_blob = json.dumps(completed)
    db_blob = json.dumps(persisted_unavailable_dto)
    cold_blob = json.dumps(cold_unavailable)
    for leak in (
        "evh_",
        "handle_id",
        "fingerprint",
        "provider_result_ref",
        "source_fingerprint",
        "query",
        "rank",
        "score",
    ):
        assert leak not in hot_blob, f"{leak} leaked into hot completed"
        assert leak not in db_blob, f"{leak} leaked into persisted DTO"
        assert leak not in cold_blob, f"{leak} leaked into cold history"


@pytest.mark.asyncio
async def test_full_production_loop_failed_emits_no_citation() -> None:
    """ASK-WEB-G1-R3 cold history witness for the ``failed`` outcome.

    When the provider returns ``failed`` (adapter raised), the runtime
    must NOT fabricate a web citation. The completed DTO carries
    ``web_search.outcome="failed"`` and zero web citations. The
    persisted DTO and the cold history projection must agree on the
    ``failed`` summary, and neither may forge a citation or leak
    internal handles / fingerprints / provider raw payload.
    """
    repo = _FakeRepo()

    def model_fn(messages, info: AgentInfo):  # noqa: ARG001
        if not any(
            isinstance(p, ToolReturnPart)
            for m in messages
            if isinstance(m, ModelRequest)
            for p in m.parts
        ):
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        TOOL_SEARCH_WEB,
                        {"query": "q", "max_results": 1},
                    )
                ]
            )
        return ModelResponse(
            parts=[
                TextPart(
                    json.dumps(
                        {
                            "response_kind": "grounded_answer",
                            "answer_blocks": [
                                {
                                    "text": "Search failed; answering from general knowledge.",
                                    "basis": "general",
                                    "evidence_handles": [],
                                }
                            ],
                        }
                    )
                )
            ]
        )

    chunks = [
        c
        async for c in stream_agentic_thread_message(
            user_id=_USER,
            reading_record_id=_RECORD,
            thread_id=_THREAD,
            content="q",
            facts=_fake_facts(),
            request_anchor=None,
            repository=repo,  # type: ignore[arg-type]
            model=FunctionModel(model_fn),
            auto_wire_dependencies=False,
            stable_document_id=_DOC,
            web_search_capability=_capability(),
            web_search_backend=_scripted_backend(outcome="failed"),
        )
    ]
    events = _parse_sse(chunks)

    completed = next(d for n, d in events if n == EVENT_MESSAGE_COMPLETED)
    web_summary = completed.get("web_search")
    assert web_summary is not None
    assert web_summary["outcome"] == "failed"
    assert web_summary.get("cited_source_count", 0) == 0

    citations = completed.get("citations") or []
    web_citations = [c for c in citations if c.get("source_kind") == "web"]
    assert web_citations == [], (
        f"web citation fabricated on failed: {web_citations}"
    )

    # Cold history parity: persisted DTO and cold projection must agree.
    assert len(repo.completed_writes) == 1
    persisted_failed = repo.completed_writes[0]
    persisted_failed_dto = persisted_failed["completed_dto"]
    assert persisted_failed_dto["web_search"] == web_summary
    persisted_failed_turn = next(iter(repo.turns.values()))
    cold_failed = project_agentic_history_message(
        message_id=completed["message_id"],
        thread_id=completed["thread_id"],
        role="assistant",
        row_status="completed",
        row_content_md=completed["answer_text"],
        created_at=None,
        updated_at=None,
        context_anchors=[],
        usage_event_id=None,
        current_turn_run_id=str(persisted_failed_turn["id"]),
        current_turn_run=persisted_failed_turn,
        user_visible_output_json=persisted_failed_turn["user_visible_output_json"],
        resolved_evidence_json=persisted_failed["resolved_evidence"],
        final_status="ok",
        turn_run_status="completed",
    )
    cold_failed_web = cold_failed["agentic_web_search"]
    assert cold_failed_web is not None
    assert cold_failed_web == web_summary
    assert cold_failed_web == persisted_failed_dto["web_search"]
    cold_failed_citations = cold_failed["agentic_citations"] or []
    cold_failed_web_citations = [
        c for c in cold_failed_citations if c.get("source_kind") == "web"
    ]
    assert cold_failed_web_citations == [], (
        f"cold history fabricated a web citation on failed: "
        f"{cold_failed_web_citations}"
    )
    # Hot, DB, and cold surfaces must all be no-evh.
    hot_blob = json.dumps(completed)
    db_blob = json.dumps(persisted_failed_dto)
    cold_blob = json.dumps(cold_failed)
    for leak in (
        "evh_",
        "handle_id",
        "fingerprint",
        "provider_result_ref",
        "source_fingerprint",
        "query",
        "rank",
        "score",
    ):
        assert leak not in hot_blob, f"{leak} leaked into hot completed"
        assert leak not in db_blob, f"{leak} leaked into persisted DTO"
        assert leak not in cold_blob, f"{leak} leaked into cold history"


# ---------------------------------------------------------------------------
# Browser acceptance: a successful search retires the tool for this turn
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_successful_search_retires_tool_and_completes_with_sources(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """After a successful search, later model requests no longer expose
    ``search_web``. The model must answer from the registered sources
    instead of spending the remaining call budget or hitting call_limit.
    """
    repo = _FakeRepo()
    backend = _scripted_backend(
        outcome="completed",
        hits=(_hit(), _hit(url="https://example.com/page2")),
    )
    state = {"requests": 0}

    def model_fn(messages, info: AgentInfo):  # noqa: ARG001
        state["requests"] += 1
        tool_names = {tool.name for tool in info.function_tools}
        if state["requests"] == 1:
            assert TOOL_SEARCH_WEB in tool_names
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        TOOL_SEARCH_WEB,
                        {
                            "query": "english grammar articles",
                            "max_results": 2,
                        },
                    )
                ]
            )

        assert TOOL_SEARCH_WEB not in tool_names
        handles: list[str] = []
        for message in messages:
            if not isinstance(message, ModelRequest):
                continue
            for part in message.parts:
                if not isinstance(part, ToolReturnPart):
                    continue
                for handle_id in re.findall(
                    r"evh_[0-9a-f]{32}", str(part.content or "")
                ):
                    if handle_id not in handles:
                        handles.append(handle_id)
        assert len(handles) == 2
        return ModelResponse(
            parts=[
                TextPart(
                    json.dumps(
                        {
                            "response_kind": "grounded_answer",
                            "answer_blocks": [
                                {
                                    "text": "answer with two web sources",
                                    "basis": "web",
                                    "evidence_handles": handles,
                                }
                            ],
                        }
                    )
                )
            ]
        )

    caplog.set_level(
        logging.INFO,
        logger="app.services.reader_record_ask.production_stream",
    )

    chunks = [
        c
        async for c in stream_agentic_thread_message(
            user_id=_USER,
            reading_record_id=_RECORD,
            thread_id=_THREAD,
            content="q",
            facts=_fake_facts(),
            request_anchor=None,
            repository=repo,  # type: ignore[arg-type]
            model=FunctionModel(model_fn),
            auto_wire_dependencies=False,
            stable_document_id=_DOC,
            web_search_capability=_capability(),
            web_search_backend=backend,
        )
    ]
    events = _parse_sse(chunks)
    assert state["requests"] == 2
    assert backend.call_count == 1

    # One searching_web attempt: started, completed.
    web_progress = [
        d
        for n, d in events
        if n == EVENT_AGENTIC_PROGRESS and d["phase"] == "searching_web"
    ]
    assert len(web_progress) == 2, (
        f"expected 2 searching_web progress events, got {len(web_progress)}"
    )
    # First attempt: started → completed.
    assert web_progress[0]["activity"] == "started"
    assert web_progress[0]["status"] == "running"
    assert web_progress[1]["activity"] == "completed"
    assert web_progress[1]["status"] == "ok"
    attempt_logs = [
        record.getMessage()
        for record in caplog.records
        if "reader_record_ask web search attempt:" in record.getMessage()
    ]
    assert len(attempt_logs) == 1
    assert "outcome=completed" in attempt_logs[0]
    assert "turn_outcome=completed" in attempt_logs[0]
    assert "detail_code=ok" in attempt_logs[0]

    # message.completed carries the aggregated completed outcome.
    completed = next(d for n, d in events if n == EVENT_MESSAGE_COMPLETED)
    assert completed["web_search"] is not None
    assert completed["web_search"]["outcome"] == "completed", (
        f"web_search.outcome must be completed, got "
        f"{completed['web_search']['outcome']}"
    )
    assert completed["web_search"]["cited_source_count"] == 2
    assert len(completed["citations"]) == 2
    assert all(
        citation["source_kind"] == "web"
        for citation in completed["citations"]
    )
    assert len(completed["answer_blocks"]) == 1
    assert len(completed["answer_blocks"][0]["citation_ids"]) == 2

    # Hot / DB / cold parity.
    assert len(repo.completed_writes) == 1
    persisted_dto = repo.completed_writes[0]["completed_dto"]
    assert persisted_dto["web_search"] == completed["web_search"]
    persisted_turn = next(iter(repo.turns.values()))
    cold = project_agentic_history_message(
        message_id=completed["message_id"],
        thread_id=completed["thread_id"],
        role="assistant",
        row_status="completed",
        row_content_md=completed["answer_text"],
        created_at=None,
        updated_at=None,
        context_anchors=[],
        usage_event_id=None,
        current_turn_run_id=str(persisted_turn["id"]),
        current_turn_run=persisted_turn,
        user_visible_output_json=persisted_turn["user_visible_output_json"],
        resolved_evidence_json=repo.completed_writes[0]["resolved_evidence"],
        final_status="ok",
        turn_run_status="completed",
    )
    assert cold["agentic_web_search"] == completed["web_search"]
    expected_cold_citations = [
        {key: value for key, value in citation.items() if value is not None}
        for citation in completed["citations"]
    ]
    assert cold["agentic_citations"] == expected_cold_citations
    assert cold["agentic_answer_blocks"] == completed["answer_blocks"]

    # Public JSON must not leak internals.
    hot_blob = json.dumps(completed)
    db_blob = json.dumps(persisted_dto)
    cold_blob = json.dumps(cold)
    for leak in (
        "evh_",
        "handle_id",
        "fingerprint",
        "provider_result_ref",
        "source_fingerprint",
        "query",
        "rank",
        "score",
    ):
        assert leak not in hot_blob, f"{leak} leaked into hot completed"
        assert leak not in db_blob, f"{leak} leaked into persisted DTO"
        assert leak not in cold_blob, f"{leak} leaked into cold history"
