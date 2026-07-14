"""Round-4A: agentic production stream, feature flag, SSE/persistence truth."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from app.config import settings as settings_mod
from app.schemas.reader_record_ask_stream import (
    EXECUTION_VERSION_AGENTIC_V1,
    ReaderRecordAskCompletedDTO,
    evidence_item_from_observation,
)
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
from app.services.reader_record_ask.evidence import build_server_evidence_observation
from app.services.reader_record_ask.evidence_registry import EvidenceRegistry
from app.services.reader_record_ask.finalizer import FinalizedAskResult
from app.services.reader_record_ask.production_stream import (
    build_completed_dto,
    stream_agentic_thread_message,
)
from app.services.reader_record_ask.runtime import ReadingRecordAskRunResult
from app.services.reader_record_ask.sse import (
    EVENT_MESSAGE_COMPLETED,
    EVENT_MESSAGE_INTERRUPTED,
    encode_sse,
)

_USER = UUID("11111111-1111-1111-1111-111111111111")
_RECORD = UUID("22222222-2222-2222-2222-222222222222")
_BASE = UUID("33333333-3333-3333-3333-333333333333")
_DOC = UUID("44444444-4444-4444-4444-444444444444")
_SHA = "b" * 64
_THREAD = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def _clear_settings_cache() -> None:
    settings_mod.get_settings.cache_clear()


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


def _function_model(answer: str = "ok answer", handles: list[str] | None = None):
    async def model_fn(messages, info):
        del messages, info
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="final_result",
                    args=json.dumps(
                        {
                            "answer_text": answer,
                            "cited_evidence_handles": handles or [],
                        }
                    ),
                    tool_call_id="f1",
                )
            ]
        )

    return FunctionModel(model_fn)


class _FakeRepo:
    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.turns: dict[str, dict] = {}
        self.completed_writes: list[dict] = []
        self.terminal_writes: list[dict] = []

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
            "execution_version": EXECUTION_VERSION_AGENTIC_V1,
            "envelope_fingerprint": kwargs["envelope_fingerprint"],
        }
        self.turns[tid] = dict(row)
        return row

    async def complete_agentic_turn_run(self, **kwargs):
        self.completed_writes.append(kwargs)
        dto = kwargs["completed_dto"]
        self.turns[str(kwargs["turn_run_id"])] = {
            "id": str(kwargs["turn_run_id"]),
            "status": "completed",
            "final_status": "ok",
            "user_visible_output_json": dto,
            "resolved_evidence_json": kwargs["resolved_evidence"],
            "envelope_fingerprint": dto["envelope_fingerprint"],
            "execution_version": EXECUTION_VERSION_AGENTIC_V1,
        }
        return self.turns[str(kwargs["turn_run_id"])]

    async def terminal_agentic_turn_run(self, **kwargs):
        self.terminal_writes.append(kwargs)
        self.turns[str(kwargs["turn_run_id"])] = {
            "id": str(kwargs["turn_run_id"]),
            "status": kwargs["run_status"],
            "final_status": kwargs["final_status"],
            "terminal_reason": kwargs["terminal_reason"],
            "user_visible_output_json": kwargs.get("terminal_dto"),
            "resolved_evidence_json": [],
            "envelope_fingerprint": None,
            "execution_version": EXECUTION_VERSION_AGENTIC_V1,
        }
        return self.turns[str(kwargs["turn_run_id"])]


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


# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------


def test_feature_flag_defaults_off() -> None:
    _clear_settings_cache()
    assert settings_mod.get_settings().reader_record_ask_agentic_enabled is False
    _clear_settings_cache()


@pytest.mark.asyncio
async def test_flag_off_uses_legacy_stream_service() -> None:
    _clear_settings_cache()
    with patch.object(
        settings_mod.get_settings(),
        "reader_record_ask_agentic_enabled",
        False,
    ):
        AsyncMock()

        async def _legacy(**kwargs):
            yield encode_sse("message.completed", {"legacy": True})

        with patch(
            "app.services.reader_record_ask.service.stream_service.stream_thread_message",
            side_effect=_legacy,
        ) as mock_legacy:
            with patch(
                "app.services.reader_record_ask.service._validate_reading_record_anchor",
                new_callable=AsyncMock,
            ):
                with patch(
                    "app.services.reader_record_ask.service._ensure_default_thread",
                    new_callable=AsyncMock,
                    return_value={"id": str(_THREAD)},
                ):
                    from app.schemas.reader_ask import ReaderRecordAskMessageRequest
                    from app.services.reader_record_ask import service as svc

                    chunks = []
                    async for c in svc.send_reading_record_ask_message(
                        user_id=_USER,
                        reading_record_id=str(_RECORD),
                        request=ReaderRecordAskMessageRequest(content="hi"),
                    ):
                        chunks.append(c)
                    assert mock_legacy.call_count == 1
                    assert any("legacy" in c for c in chunks)
    _clear_settings_cache()


@pytest.mark.asyncio
async def test_flag_on_does_not_call_legacy_stream() -> None:
    _clear_settings_cache()
    with patch.object(
        settings_mod.get_settings(),
        "reader_record_ask_agentic_enabled",
        True,
    ):
        with patch(
            "app.services.reader_record_ask.service.stream_service.stream_thread_message",
            new_callable=AsyncMock,
        ) as mock_legacy:
            with patch(
                "app.services.reader_record_ask.service._validate_reading_record_anchor",
                new_callable=AsyncMock,
            ):
                with patch(
                    "app.services.reader_record_ask.service._ensure_default_thread",
                    new_callable=AsyncMock,
                    return_value={"id": str(_THREAD)},
                ):
                    with patch(
                        "app.services.reader_record_ask.service._load_snapshot_facts",
                        new_callable=AsyncMock,
                        return_value=_fake_facts(),
                    ):
                        repo = _FakeRepo()

                        async def _run(**kwargs):
                            env = kwargs["envelope"]
                            EvidenceRegistry(env.envelope_fingerprint)
                            # Register nothing extra; empty citations ok
                            return ReadingRecordAskRunResult(
                                final_text="agentic answer",
                                finalized=FinalizedAskResult(
                                    status="ok",
                                    answer_text="agentic answer",
                                    resolved_evidence=(),
                                    envelope_fingerprint=env.envelope_fingerprint,
                                ),
                            )

                        with patch(
                            "app.services.reader_record_ask.production_stream.run_reading_record_ask",
                            side_effect=_run,
                        ):
                            with patch(
                                "app.services.reader_record_ask.production_stream.ReaderRecordAskRepository",
                                return_value=repo,
                            ):
                                with patch(
                                    "app.services.reader_record_ask.production_stream.resolve_agentic_model",
                                    return_value=_function_model("agentic answer"),
                                ):
                                    with patch(
                                        "app.services.reader_record_ask.production_stream.load_active_stable_document_id",
                                        new_callable=AsyncMock,
                                        return_value=_DOC,
                                    ):
                                        from app.schemas.reader_ask import (
                                            ReaderRecordAskMessageRequest,
                                        )
                                        from app.services.reader_record_ask import (
                                            service as svc,
                                        )

                                        chunks = []
                                        async for c in svc.stream_reading_record_ask_thread_message(
                                            user_id=_USER,
                                            reading_record_id=str(_RECORD),
                                            thread_id=_THREAD,
                                            request=ReaderRecordAskMessageRequest(content="hi"),
                                        ):
                                            chunks.append(c)
                        assert mock_legacy.call_count == 0
                        events = _parse_sse(chunks)
                        names = [e[0] for e in events]
                        assert EVENT_MESSAGE_COMPLETED in names
    _clear_settings_cache()


# ---------------------------------------------------------------------------
# SSE / DTO consistency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_model_configured_no_completed() -> None:
    """Flag-on without a validated model must not emit completed success."""
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
        model=None,
        auto_wire_dependencies=False,
    ):
        chunks.append(c)
    events = _parse_sse(chunks)
    names = [n for n, _ in events]
    assert EVENT_MESSAGE_COMPLETED not in names
    assert EVENT_MESSAGE_INTERRUPTED in names
    assert len(repo.terminal_writes) == 1
    assert repo.terminal_writes[0]["final_status"] == "failed"
    assert "model_unconfigured" in (repo.terminal_writes[0]["terminal_reason"] or "")


@pytest.mark.asyncio
async def test_stable_document_loaded_into_envelope() -> None:
    repo = _FakeRepo()
    captured: dict = {}

    async def _run(**kwargs):
        captured["envelope"] = kwargs["envelope"]
        return ReadingRecordAskRunResult(
            final_text="ok",
            finalized=FinalizedAskResult(
                status="ok",
                answer_text="ok",
                resolved_evidence=(),
                envelope_fingerprint=kwargs["envelope"].envelope_fingerprint,
            ),
        )

    with patch(
        "app.services.reader_record_ask.production_stream.load_active_stable_document_id",
        new_callable=AsyncMock,
        return_value=_DOC,
    ):
        async for _ in stream_agentic_thread_message(
            user_id=_USER,
            reading_record_id=_RECORD,
            thread_id=_THREAD,
            content="q",
            facts=_fake_facts(),
            request_anchor=None,
            repository=repo,  # type: ignore[arg-type]
            model=_function_model(),
            run_fn=_run,
            auto_wire_dependencies=True,
            article_rag=None,
        ):
            pass
    assert captured["envelope"].stable_document_id == _DOC


@pytest.mark.asyncio
async def test_fake_rag_port_can_produce_search_hit_evidence() -> None:
    from app.services.reader_record_ask.article_rag_port import (
        ArticleRagHitView,
        ArticleRagSearchOutcome,
        FakeArticleRagSearchPort,
    )

    repo = _FakeRepo()
    hit = ArticleRagHitView(
        chunk_id="c1",
        text="climate paragraph",
        source_scope="main_reading_text",
        block_type="paragraph",
        content_sha256="d" * 64,
        canonical_text_start_utf16=0,
        canonical_text_end_utf16=10,
        score=0.9,
        reading_record_id=_RECORD,
        stable_document_id=_DOC,
        base_id=_BASE,
        record_generation=1,
    )
    port = FakeArticleRagSearchPort(
        outcomes=[
            ArticleRagSearchOutcome(
                status="ok",
                summary="ok",
                hits=(hit,),
                rag_substrate_id=UUID("55555555-5555-5555-5555-555555555555"),
                index_version="article_rag_index_v1",
                plan_content_sha256="c" * 64,
                stable_document_id=_DOC,
                base_id=_BASE,
                record_generation=1,
            )
        ]
    )
    # Run real agent with search then final via FunctionModel
    state = {"searched": False}

    async def model_fn(messages, info):
        del info
        if not state["searched"]:
            state["searched"] = True
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="search_current_article",
                        args=json.dumps({"query": "climate"}),
                        tool_call_id="s1",
                    )
                ]
            )
        handle = None
        for msg in messages:
            for part in getattr(msg, "parts", []) or []:
                if type(part).__name__ != "ToolReturnPart":
                    continue
                content = getattr(part, "content", None)
                if isinstance(content, dict):
                    ehs = content.get("evidence_handles") or []
                    if ehs:
                        handle = ehs[0].get("handle_id") or ehs[0]
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="final_result",
                    args=json.dumps(
                        {
                            "answer_text": "about climate",
                            "cited_evidence_handles": [handle] if handle else [],
                        }
                    ),
                    tool_call_id="f1",
                )
            ]
        )

    chunks = [
        c
        async for c in stream_agentic_thread_message(
            user_id=_USER,
            reading_record_id=_RECORD,
            thread_id=_THREAD,
            content="What about climate?",
            facts=_fake_facts(),
            request_anchor=None,
            repository=repo,  # type: ignore[arg-type]
            document_access=InMemoryDocumentAccess(
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
            ),
            article_rag=port,
            model=FunctionModel(model_fn),
            auto_wire_dependencies=False,
            stable_document_id=_DOC,
        )
    ]
    assert port.call_count == 1
    events = _parse_sse(chunks)
    completed = [d for n, d in events if n == EVENT_MESSAGE_COMPLETED]
    assert len(completed) == 1
    kinds = {e["kind"] for e in completed[0]["evidence"]}
    assert "search_hit" in kinds
    rag = next(e for e in completed[0]["evidence"] if e["kind"] == "search_hit")["rag_citation"]
    assert rag["stable_document_id"] == str(_DOC)
    assert rag["base_id"] == str(_BASE)
    assert rag["record_generation"] == 1


@pytest.mark.asyncio
async def test_rag_unconfigured_port_none_tools_unavailable() -> None:
    """When article_rag is None, agent can still complete without search hits."""
    repo = _FakeRepo()

    async def _run(**kwargs):
        assert kwargs.get("article_rag") is None
        return ReadingRecordAskRunResult(
            final_text="no rag",
            finalized=FinalizedAskResult(
                status="ok",
                answer_text="no rag",
                resolved_evidence=(),
                envelope_fingerprint=kwargs["envelope"].envelope_fingerprint,
            ),
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
            article_rag=None,
            model=_function_model("no rag"),
            run_fn=_run,
            auto_wire_dependencies=False,
            stable_document_id=_DOC,
        )
    ]
    assert any(EVENT_MESSAGE_COMPLETED in c for c in chunks)


@pytest.mark.asyncio
async def test_completed_sse_matches_persisted_dto() -> None:
    repo = _FakeRepo()
    _envelope()

    async def _run(**kwargs):
        return ReadingRecordAskRunResult(
            final_text="done",
            finalized=FinalizedAskResult(
                status="ok",
                answer_text="done",
                resolved_evidence=(),
                envelope_fingerprint=kwargs["envelope"].envelope_fingerprint,
            ),
        )

    chunks: list[str] = []
    async for c in stream_agentic_thread_message(
        user_id=_USER,
        reading_record_id=_RECORD,
        thread_id=_THREAD,
        content="q",
        facts=_fake_facts(),
        request_anchor=None,
        repository=repo,  # type: ignore[arg-type]
        document_access=InMemoryDocumentAccess(
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
        ),
        model=_function_model(),
        run_fn=_run,
        auto_wire_dependencies=False,
        stable_document_id=_DOC,
    ):
        chunks.append(c)

    events = _parse_sse(chunks)
    completed_events = [d for n, d in events if n == EVENT_MESSAGE_COMPLETED]
    assert len(completed_events) == 1
    sse_dto = completed_events[0]
    assert len(repo.completed_writes) == 1
    persisted = repo.completed_writes[0]["completed_dto"]
    assert sse_dto == persisted
    assert sse_dto["execution_version"] == EXECUTION_VERSION_AGENTIC_V1
    assert sse_dto["final_status"] == "ok"
    assert sse_dto["answer_text"] == "done"
    # Validate against schema
    ReaderRecordAskCompletedDTO.model_validate(sse_dto)


@pytest.mark.asyncio
async def test_stale_finalizer_no_completed_answer() -> None:
    repo = _FakeRepo()

    async def _run(**kwargs):
        return ReadingRecordAskRunResult(
            final_text=None,
            finalized=FinalizedAskResult(
                status="context_stale",
                answer_text=None,
                reason="generation mismatch",
                envelope_fingerprint=kwargs["envelope"].envelope_fingerprint,
            ),
        )

    chunks: list[str] = []
    async for c in stream_agentic_thread_message(
        user_id=_USER,
        reading_record_id=_RECORD,
        thread_id=_THREAD,
        content="q",
        facts=_fake_facts(),
        request_anchor=None,
        repository=repo,  # type: ignore[arg-type]
        document_access=InMemoryDocumentAccess(
            snapshot=build_document_scope(
                reading_record_id=_RECORD,
                base_id=_BASE,
                record_generation=1,
                base_content_sha256=_SHA,
                units=[
                    ReadingUnitView(
                        unit_id="u1",
                        order_index=0,
                        text="x",
                        text_hash="11111111",
                        base_start_utf16=0,
                        base_end_utf16=1,
                    )
                ],
            )
        ),
        model=_function_model(),
        run_fn=_run,
        auto_wire_dependencies=False,
    ):
        chunks.append(c)

    events = _parse_sse(chunks)
    names = [n for n, _ in events]
    assert EVENT_MESSAGE_COMPLETED not in names
    assert EVENT_MESSAGE_INTERRUPTED in names
    assert len(repo.terminal_writes) == 1
    assert repo.terminal_writes[0]["final_status"] == "context_stale"
    assert repo.terminal_writes[0]["run_status"] == "stale"
    # No evidence written on terminal path
    assert repo.terminal_writes[0].get("terminal_dto", {}).get("final_status") == ("context_stale")


@pytest.mark.asyncio
async def test_invalid_citations_no_completed() -> None:
    repo = _FakeRepo()

    async def _run(**kwargs):
        return ReadingRecordAskRunResult(
            final_text=None,
            finalized=FinalizedAskResult(
                status="invalid_citations",
                answer_text=None,
                reason="bad handle",
                rejected_handles=("evh_" + ("ab" * 16),),
                envelope_fingerprint=kwargs["envelope"].envelope_fingerprint,
            ),
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
            document_access=InMemoryDocumentAccess(
                snapshot=build_document_scope(
                    reading_record_id=_RECORD,
                    base_id=_BASE,
                    record_generation=1,
                    base_content_sha256=_SHA,
                    units=[
                        ReadingUnitView(
                            unit_id="u1",
                            order_index=0,
                            text="x",
                            text_hash="11111111",
                            base_start_utf16=0,
                            base_end_utf16=1,
                        )
                    ],
                )
            ),
            model=_function_model(),
            run_fn=_run,
            auto_wire_dependencies=False,
        )
    ]
    names = [n for n, _ in _parse_sse(chunks)]
    assert EVENT_MESSAGE_COMPLETED not in names
    assert repo.terminal_writes[0]["final_status"] == "invalid_citations"


def test_build_completed_dto_includes_typed_evidence_kinds() -> None:
    env = _envelope()
    reg = EvidenceRegistry(env.envelope_fingerprint)
    anchor = build_server_evidence_observation(
        kind="initial_anchor",
        envelope_fingerprint=env.envelope_fingerprint,
        source_tool="initial_anchor",
        snippet="hello",
        unit_id="u1",
        anchor_segment_id="s1",
    )
    reg.register(anchor)
    read = build_server_evidence_observation(
        kind="read_range",
        envelope_fingerprint=env.envelope_fingerprint,
        source_tool="read_range",
        snippet="world",
        unit_id="u1",
    )
    reg.register(read)
    run = ReadingRecordAskRunResult(
        final_text="ans",
        finalized=FinalizedAskResult(
            status="ok",
            answer_text="ans",
            resolved_evidence=reg.list_observations(),
            envelope_fingerprint=env.envelope_fingerprint,
        ),
    )
    dto = build_completed_dto(
        run_result=run,
        message_id=str(uuid4()),
        thread_id=str(_THREAD),
        turn_run_id=str(uuid4()),
        envelope=env,
    )
    kinds = {e.kind for e in dto.evidence}
    assert kinds == {"initial_anchor", "read_range"}
    assert dto.execution_version == EXECUTION_VERSION_AGENTIC_V1


def test_evidence_item_from_observation_maps_rag() -> None:
    from app.services.reader_record_ask.evidence import ArticleRagCitationEvidence

    env = _envelope()
    substrate = str(uuid4())
    cit = ArticleRagCitationEvidence(
        rag_substrate_id=substrate,
        index_run_id=substrate,
        index_version="article_rag_index_v1",
        plan_content_sha256="c" * 64,
        source_scope="main_reading_text",
        block_type="paragraph",
        chunk_id="ch1",
        content_sha256="d" * 64,
        canonical_text_start_utf16=0,
        canonical_text_end_utf16=5,
        snippet="snip",
        reading_record_id=str(_RECORD),
        stable_document_id=str(_DOC),
        base_id=str(_BASE),
        record_generation=1,
    )
    obs = build_server_evidence_observation(
        kind="search_hit",
        envelope_fingerprint=env.envelope_fingerprint,
        source_tool="search_current_article",
        snippet="snip",
        rag_citation=cit,
    )
    item = evidence_item_from_observation(obs)
    assert item.kind == "search_hit"
    assert item.rag_citation is not None
    assert item.rag_citation.rag_substrate_id == cit.rag_substrate_id
    assert item.rag_citation.stable_document_id == str(_DOC)
    assert item.rag_citation.base_id == str(_BASE)
    assert item.rag_citation.record_generation == 1


def test_production_rag_factory_returns_none_when_article_rag_is_disabled() -> None:
    """Feature-off keeps the search tool typed-unavailable without I/O."""
    from app.services.reader_record_ask.production_wiring import (
        build_production_article_rag_port,
    )

    settings = SimpleNamespace(reader_article_rag_enabled=False)

    assert build_production_article_rag_port(settings) is None


def test_production_rag_factory_builds_retrieval_backed_port_when_enabled() -> None:
    """Feature-on wires the independent Ask port to Article RAG retrieval."""
    from app.services.reader_record_ask.article_rag_adapter import (
        RetrievalBackedArticleRagPort,
    )
    from app.services.reader_record_ask.production_wiring import (
        build_production_article_rag_port,
    )

    settings = SimpleNamespace(reader_article_rag_enabled=True)
    pool = object()
    embedding = object()
    searcher = object()
    retrieval = MagicMock()

    with patch(
        "app.services.reader_orchestration.article_rag_embedding_provider."
        "build_default_article_rag_embedding_provider",
        return_value=embedding,
    ) as build_embedding:
        with patch(
            "app.services.reader_orchestration.article_rag_vector_search."
            "build_default_article_rag_vector_searcher",
            return_value=searcher,
        ) as build_searcher:
            with patch(
                "app.services.reader_orchestration.article_rag_retrieval_service."
                "ArticleRagRetrievalService",
                return_value=retrieval,
            ) as build_retrieval:
                port = build_production_article_rag_port(settings, pool=pool)

    assert isinstance(port, RetrievalBackedArticleRagPort)
    build_embedding.assert_called_once_with(settings)
    build_searcher.assert_called_once_with(settings)
    build_retrieval.assert_called_once_with(
        pool=pool,
        embedding_provider=embedding,
        vector_searcher=searcher,
    )
