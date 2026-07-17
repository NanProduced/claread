"""Round-4A: agentic production stream, feature flag, SSE/persistence truth."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from app.config import settings as settings_mod
from app.schemas.reader_record_ask_stream import (
    EXECUTION_VERSION_AGENTIC_V1,
    ReaderRecordAskCompletedDTO,
    ReaderRecordAskEvidenceItem,
    ReaderRecordAskEvidenceScope,
    ReaderRecordAskRagCitationPublic,
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
    TERMINAL_REASON_AGENT_OUTPUT_INVALID,
    TERMINAL_REASON_AGENT_RUN_FAILED,
    TERMINAL_REASON_BASELINE_UNAVAILABLE,
    TERMINAL_REASON_DOCUMENT_UNAVAILABLE,
    TERMINAL_REASON_EVIDENCE_SCOPE_INVARIANT,
    EvidenceScopeInvariantError,
    assert_evidence_scope_matches_items,
    build_completed_dto,
    build_terminal_dto,
    evidence_scope_from_envelope,
    stream_agentic_thread_message,
)
from app.services.reader_record_ask.runtime import ReadingRecordAskRunResult
from app.services.reader_record_ask.sse import (
    EVENT_AGENTIC_TERMINAL,
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
                            "response_kind": "grounded_answer",
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


def test_feature_flag_code_default_is_false() -> None:
    """Settings field default is False; local .env may enable it in development."""
    from app.config.settings import Settings

    field = Settings.model_fields["reader_record_ask_agentic_enabled"]
    assert field.default is False


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
                        "app.services.reader_record_ask.service.thread_service.resolve_and_persist_thread_model_option",
                        new_callable=AsyncMock,
                        return_value=MagicMock(
                            key="deepseek-v4-flash",
                            selection=MagicMock(),
                        ),
                    ):
                        with patch(
                            "app.services.reader_record_ask.service.build_model_for_route",
                            return_value=(_function_model("agentic answer"), MagicMock()),
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
                                                    request=ReaderRecordAskMessageRequest(
                                                        content="hi"
                                                    ),
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
                            "response_kind": "grounded_answer",
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


@pytest.mark.asyncio
async def test_unexpected_model_behavior_maps_to_stable_terminal(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Structured-output exhaustion must not leak pydantic-ai / schema text."""
    repo = _FakeRepo()
    leak = (
        "Exceeded maximum output retries (1) for output validation; "
        'body: {"answer_text": {"min_length": 1}}'
    )

    async def _run(**kwargs):
        del kwargs
        raise UnexpectedModelBehavior(leak)

    with caplog.at_level("WARNING", logger="app.services.reader_record_ask.production_stream"):
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
    events = _parse_sse(chunks)
    names = [n for n, _ in events]
    assert EVENT_MESSAGE_COMPLETED not in names
    assert EVENT_AGENTIC_TERMINAL in names
    assert EVENT_MESSAGE_INTERRUPTED in names
    assert len(repo.terminal_writes) == 1
    tw = repo.terminal_writes[0]
    assert tw["final_status"] == "failed"
    assert tw["run_status"] == "failed"
    assert tw["terminal_reason"] == TERMINAL_REASON_AGENT_OUTPUT_INVALID

    # No framework / schema leakage on any emitted payload.
    blob = json.dumps([d for _, d in events], ensure_ascii=False)
    assert "Exceeded maximum" not in blob
    assert "output retries" not in blob
    assert "min_length" not in blob
    assert "answer_text" not in blob
    assert leak not in blob
    for _name, data in events:
        if _name in {EVENT_AGENTIC_TERMINAL, EVENT_MESSAGE_INTERRUPTED}:
            assert data.get("terminal_reason") == TERMINAL_REASON_AGENT_OUTPUT_INVALID
            assert data.get("final_status") == "failed"
            assert "answer_text" not in data

    # Safe diagnostic log includes model route, never framework exception text.
    log_blob = "\n".join(r.getMessage() for r in caplog.records)
    assert "model_route=" in log_blob
    assert "UnexpectedModelBehavior" in log_blob
    assert "Exceeded maximum" not in log_blob
    assert "min_length" not in log_blob
    assert leak not in log_blob


@pytest.mark.asyncio
async def test_generic_exception_does_not_complete_or_leak_as_answer(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Non-structured failures stay interrupted; no message.completed."""
    repo = _FakeRepo()
    sensitive = "provider boom with secrets XYZ"

    async def _run(**kwargs):
        del kwargs
        raise RuntimeError(sensitive)

    with caplog.at_level("WARNING", logger="app.services.reader_record_ask.production_stream"):
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
    events = _parse_sse(chunks)
    names = [n for n, _ in events]
    assert EVENT_MESSAGE_COMPLETED not in names
    assert EVENT_MESSAGE_INTERRUPTED in names
    assert EVENT_AGENTIC_TERMINAL in names
    assert repo.terminal_writes[0]["final_status"] == "failed"
    assert repo.terminal_writes[0]["terminal_reason"] == TERMINAL_REASON_AGENT_RUN_FAILED
    blob = json.dumps([d for _, d in events], ensure_ascii=False)
    assert sensitive not in blob
    assert "provider boom" not in blob
    assert "XYZ" not in blob
    for _name, data in events:
        if _name in {EVENT_AGENTIC_TERMINAL, EVENT_MESSAGE_INTERRUPTED}:
            assert data.get("terminal_reason") == TERMINAL_REASON_AGENT_RUN_FAILED
            assert "answer_text" not in data

    log_blob = "\n".join(r.getMessage() for r in caplog.records)
    assert "model_route=" in log_blob
    assert "RuntimeError" in log_blob
    assert sensitive not in log_blob
    assert "provider boom" not in log_blob
    assert "XYZ" not in log_blob
    # Must not use logger.exception (no traceback leakage into message text).
    assert "Traceback" not in log_blob


# ---------------------------------------------------------------------------
# R4-A2 baseline-unavailable typed terminal tests (scenarios 4, 16)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_baseline_document_unavailable_emits_typed_terminal() -> None:
    """Baseline document_scope_unavailable → wire failed + document_unavailable.

    The runtime maps a baseline failure to an internal
    ``FinalizedAskResult(status="unavailable", reason="document_unavailable")``.
    Production stream must:
      - emit ``final_status="failed"`` on the wire (NOT "unavailable");
      - emit ``terminal_reason="document_unavailable"`` (typed);
      - never emit the legacy ``"missing_finalizer_result"`` reason.
    """
    repo = _FakeRepo()

    async def _run(**kwargs):
        return ReadingRecordAskRunResult(
            final_text=None,
            finalized=FinalizedAskResult(
                status="unavailable",
                answer_text=None,
                reason="document_unavailable",
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
            # raise_missing=True short-circuits load before snapshot is
            # consulted for identity comparison, but the dataclass field
            # is still required — supply a minimal valid snapshot.
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
                ),
                raise_missing=True,
            ),
            model=_function_model(),
            run_fn=_run,
            auto_wire_dependencies=False,
        )
    ]
    events = _parse_sse(chunks)
    names = [n for n, _ in events]
    assert EVENT_MESSAGE_COMPLETED not in names
    assert EVENT_MESSAGE_INTERRUPTED in names
    assert len(repo.terminal_writes) == 1
    # Wire final_status is "failed" — internal "unavailable" must NEVER
    # leak to the wire (the wire FinalStatus Literal has 5 values and
    # does not include "unavailable").
    assert repo.terminal_writes[0]["final_status"] == "failed"
    assert repo.terminal_writes[0]["run_status"] == "failed"
    # Typed terminal_reason (not "missing_finalizer_result").
    assert (
        repo.terminal_writes[0]["terminal_reason"]
        == TERMINAL_REASON_DOCUMENT_UNAVAILABLE
    )
    # SSE terminal event carries the same typed reason.
    for _name, data in events:
        if _name == EVENT_AGENTIC_TERMINAL:
            assert data.get("final_status") == "failed"
            assert (
                data.get("terminal_reason")
                == TERMINAL_REASON_DOCUMENT_UNAVAILABLE
            )
            assert data.get("terminal_reason") != "missing_finalizer_result"


@pytest.mark.asyncio
async def test_baseline_envelope_mismatch_emits_baseline_unavailable_terminal() -> None:
    """Baseline envelope_mismatch → wire failed + baseline_unavailable.

    The runtime maps any non-document baseline failure to
    ``reason="baseline_unavailable"``. Production stream must emit the
    corresponding typed terminal_reason.
    """
    repo = _FakeRepo()

    async def _run(**kwargs):
        return ReadingRecordAskRunResult(
            final_text=None,
            finalized=FinalizedAskResult(
                status="unavailable",
                answer_text=None,
                reason="baseline_unavailable",
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
    events = _parse_sse(chunks)
    names = [n for n, _ in events]
    assert EVENT_MESSAGE_COMPLETED not in names
    assert EVENT_MESSAGE_INTERRUPTED in names
    assert len(repo.terminal_writes) == 1
    assert repo.terminal_writes[0]["final_status"] == "failed"
    assert (
        repo.terminal_writes[0]["terminal_reason"]
        == TERMINAL_REASON_BASELINE_UNAVAILABLE
    )
    for _name, data in events:
        if _name == EVENT_AGENTIC_TERMINAL:
            assert data.get("final_status") == "failed"
            assert (
                data.get("terminal_reason")
                == TERMINAL_REASON_BASELINE_UNAVAILABLE
            )


@pytest.mark.asyncio
async def test_unavailable_internal_status_does_not_leak_to_wire() -> None:
    """build_terminal_dto must NEVER emit final_status="unavailable" on wire.

    Scenario 16: the wire FinalStatus Literal has 5 values
    (ok / failed / interrupted / stale / cancelled — none is "unavailable").
    Even if a caller passes final_status="failed", build_terminal_dto
    must not override it with the internal "unavailable" status from
    FinalizedAskResult.
    """
    envelope_fingerprint = "a" * 64
    finalized = FinalizedAskResult(
        status="unavailable",
        answer_text=None,
        reason=TERMINAL_REASON_BASELINE_UNAVAILABLE,
        envelope_fingerprint=envelope_fingerprint,
    )
    # Caller maps internal "unavailable" to wire "failed" before calling.
    terminal = build_terminal_dto(
        finalized=finalized,
        message_id="m-1",
        thread_id="t-1",
        turn_run_id="tr-1",
        envelope_fingerprint=envelope_fingerprint,
        final_status="failed",
        terminal_reason=TERMINAL_REASON_BASELINE_UNAVAILABLE,
    )
    assert terminal.final_status == "failed"
    assert terminal.terminal_reason == TERMINAL_REASON_BASELINE_UNAVAILABLE
    # DTO must not carry any internal-only fields
    dto_json = terminal.model_dump(mode="json")
    assert "response_kind" not in dto_json
    assert "is_complete" not in dto_json
    assert "model_visible_chars" not in dto_json
    assert "coverage" not in dto_json


@pytest.mark.asyncio
async def test_completed_dto_excludes_internal_response_kind_and_coverage() -> None:
    """ReaderRecordAskCompletedDTO must never expose internal-only fields.

    Scenario 16: ``response_kind`` and ``coverage`` / ``is_complete`` /
    ``model_visible_chars`` are internal-only. They must not appear in
    the public completed DTO, the persisted row, or the SSE payload.
    """
    from app.schemas.reader_record_ask_stream import ReaderRecordAskCompletedDTO

    # Build a minimal completed DTO and inspect its serialized form.
    dto = ReaderRecordAskCompletedDTO(
        answer_text="answer",
        message_id="m-1",
        thread_id="t-1",
        turn_run_id="tr-1",
        envelope_fingerprint="a" * 64,
        evidence_scope=evidence_scope_from_envelope(_envelope()),
        evidence=[],
    )
    dto_json = dto.model_dump(mode="json")
    # Internal-only fields must NOT appear on the public completed DTO.
    assert "response_kind" not in dto_json
    assert "coverage" not in dto_json
    assert "is_complete" not in dto_json
    assert "model_visible_chars" not in dto_json
    assert "article_total_chars" not in dto_json
    assert "baseline_status" not in dto_json


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
    # R3B0: new production always emits non-null message-level scope from envelope.
    assert dto.evidence_scope is not None
    assert dto.evidence_scope.reading_record_id == str(_RECORD)
    assert dto.evidence_scope.base_id == str(_BASE)
    assert dto.evidence_scope.record_generation == 1
    assert dto.evidence_scope.stable_document_id == str(_DOC)


def _ok_run(observations: tuple, answer: str = "ans") -> ReadingRecordAskRunResult:
    return ReadingRecordAskRunResult(
        final_text=answer,
        finalized=FinalizedAskResult(
            status="ok",
            answer_text=answer,
            resolved_evidence=observations,
            envelope_fingerprint=observations[0].handle.envelope_fingerprint
            if observations
            else "a" * 64,
        ),
    )


def test_evidence_scope_from_envelope_projects_uuid_strings() -> None:
    env = _envelope(stable_document_id=None)
    scope = evidence_scope_from_envelope(env)
    assert scope.reading_record_id == str(_RECORD)
    assert scope.base_id == str(_BASE)
    assert scope.record_generation == 1
    assert scope.stable_document_id is None
    dumped = scope.model_dump(mode="json")
    assert dumped == {
        "reading_record_id": str(_RECORD),
        "base_id": str(_BASE),
        "record_generation": 1,
        "stable_document_id": None,
    }


def test_build_completed_dto_outputs_full_non_null_scope() -> None:
    env = _envelope()
    reg = EvidenceRegistry(env.envelope_fingerprint)
    anchor = build_server_evidence_observation(
        kind="initial_anchor",
        envelope_fingerprint=env.envelope_fingerprint,
        source_tool="initial_anchor",
        snippet="hello",
    )
    reg.register(anchor)
    dto = build_completed_dto(
        run_result=_ok_run(reg.list_observations()),
        message_id=str(uuid4()),
        thread_id=str(_THREAD),
        turn_run_id=str(uuid4()),
        envelope=env,
    )
    assert dto.evidence_scope is not None
    assert dto.evidence_scope.stable_document_id == str(_DOC)
    wire = dto.model_dump(mode="json")
    assert wire["evidence_scope"]["reading_record_id"] == str(_RECORD)
    assert wire["evidence_scope"]["base_id"] == str(_BASE)
    assert wire["evidence_scope"]["record_generation"] == 1


def test_build_completed_dto_rag_off_stable_null_still_has_scope() -> None:
    """RAG disabled / no stable doc: scope still required; stable id may be null."""
    env = _envelope(stable_document_id=None)
    reg = EvidenceRegistry(env.envelope_fingerprint)
    anchor = build_server_evidence_observation(
        kind="initial_anchor",
        envelope_fingerprint=env.envelope_fingerprint,
        source_tool="initial_anchor",
        snippet="sel",
        unit_id="u1",
        anchor_segment_id="s1",
    )
    reg.register(anchor)
    dto = build_completed_dto(
        run_result=_ok_run(reg.list_observations()),
        message_id=str(uuid4()),
        thread_id=str(_THREAD),
        turn_run_id=str(uuid4()),
        envelope=env,
    )
    assert dto.evidence_scope is not None
    assert dto.evidence_scope.stable_document_id is None
    assert dto.evidence_scope.reading_record_id == str(_RECORD)
    assert {e.kind for e in dto.evidence} == {"initial_anchor"}


def test_completed_dto_accepts_legacy_missing_and_explicit_null_scope() -> None:
    base = {
        "execution_version": EXECUTION_VERSION_AGENTIC_V1,
        "final_status": "ok",
        "answer_text": "legacy answer",
        "message_id": "m1",
        "thread_id": "t1",
        "turn_run_id": "tr1",
        "envelope_fingerprint": "f" * 64,
        "evidence": [],
    }
    missing = ReaderRecordAskCompletedDTO.model_validate(base)
    assert missing.evidence_scope is None

    explicit_null = ReaderRecordAskCompletedDTO.model_validate(
        {**base, "evidence_scope": None}
    )
    assert explicit_null.evidence_scope is None


def test_completed_dto_rejects_malformed_scope_and_bad_generation() -> None:
    base = {
        "execution_version": EXECUTION_VERSION_AGENTIC_V1,
        "final_status": "ok",
        "answer_text": "a",
        "message_id": "m1",
        "thread_id": "t1",
        "turn_run_id": "tr1",
        "envelope_fingerprint": "f" * 64,
        "evidence": [],
    }
    with pytest.raises(Exception):
        ReaderRecordAskCompletedDTO.model_validate(
            {**base, "evidence_scope": {"reading_record_id": "r"}}
        )
    with pytest.raises(Exception):
        ReaderRecordAskCompletedDTO.model_validate(
            {
                **base,
                "evidence_scope": {
                    "reading_record_id": "r",
                    "base_id": "b",
                    "record_generation": 0,
                    "stable_document_id": None,
                },
            }
        )
    with pytest.raises(Exception):
        ReaderRecordAskCompletedDTO.model_validate(
            {
                **base,
                "evidence_scope": {
                    "reading_record_id": "r",
                    "base_id": "b",
                    "record_generation": -1,
                    "stable_document_id": None,
                },
            }
        )
    with pytest.raises(Exception):
        ReaderRecordAskCompletedDTO.model_validate(
            {
                **base,
                "evidence_scope": {
                    "reading_record_id": "r",
                    "base_id": "b",
                    "record_generation": 1,
                    "stable_document_id": None,
                    "extra": "nope",
                },
            }
        )


def test_evidence_scope_strict_rejects_coerced_generation_and_empty_stable() -> None:
    """Align with Web guard: no str/float/bool generation; no empty stable id."""
    from pydantic import ValidationError

    good = {
        "reading_record_id": "r",
        "base_id": "b",
        "record_generation": 1,
        "stable_document_id": None,
    }
    assert ReaderRecordAskEvidenceScope.model_validate(good).record_generation == 1

    for bad_generation in ("1", 1.0, True, False):
        with pytest.raises(ValidationError):
            ReaderRecordAskEvidenceScope.model_validate(
                {**good, "record_generation": bad_generation}
            )

    with pytest.raises(ValidationError):
        ReaderRecordAskEvidenceScope.model_validate(
            {**good, "stable_document_id": ""}
        )

    # Nested on completed DTO must also reject (no half-coerce into ok payload).
    completed_base = {
        "execution_version": EXECUTION_VERSION_AGENTIC_V1,
        "final_status": "ok",
        "answer_text": "a",
        "message_id": "m1",
        "thread_id": "t1",
        "turn_run_id": "tr1",
        "envelope_fingerprint": "f" * 64,
        "evidence": [],
    }
    with pytest.raises(ValidationError):
        ReaderRecordAskCompletedDTO.model_validate(
            {
                **completed_base,
                "evidence_scope": {**good, "record_generation": "1"},
            }
        )
    with pytest.raises(ValidationError):
        ReaderRecordAskCompletedDTO.model_validate(
            {
                **completed_base,
                "evidence_scope": {**good, "record_generation": 1.0},
            }
        )
    with pytest.raises(ValidationError):
        ReaderRecordAskCompletedDTO.model_validate(
            {
                **completed_base,
                "evidence_scope": {**good, "stable_document_id": ""},
            }
        )


def _rag_public(**overrides: object) -> ReaderRecordAskRagCitationPublic:
    payload = dict(
        rag_substrate_id="substrate-1",
        index_run_id="substrate-1",
        index_version="v1",
        plan_content_sha256="c" * 64,
        source_scope="main_reading_text",
        block_type="paragraph",
        chunk_id="ch1",
        content_sha256="d" * 64,
        canonical_text_start_utf16=0,
        canonical_text_end_utf16=5,
        snippet="snip",
        stable_document_id=str(_DOC),
        base_id=str(_BASE),
        record_generation=1,
        block_ids=["b1"],
        unit_ids=["u1"],
        anchor_segment_ids=["s1"],
    )
    payload.update(overrides)
    return ReaderRecordAskRagCitationPublic(**payload)  # type: ignore[arg-type]


def test_search_hit_scope_match_allows_completed_dto() -> None:
    env = _envelope()
    from app.services.reader_record_ask.evidence import ArticleRagCitationEvidence

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
    dto = build_completed_dto(
        run_result=_ok_run((obs,)),
        message_id=str(uuid4()),
        thread_id=str(_THREAD),
        turn_run_id=str(uuid4()),
        envelope=env,
    )
    assert dto.evidence_scope is not None
    assert dto.evidence_scope.stable_document_id == str(_DOC)
    assert dto.evidence[0].rag_citation is not None
    assert dto.evidence[0].rag_citation.stable_document_id == str(_DOC)


@pytest.mark.parametrize(
    "field",
    ["stable_document_id", "base_id", "record_generation"],
)
def test_search_hit_scope_mismatch_fail_closed(field: str) -> None:
    scope = ReaderRecordAskEvidenceScope(
        reading_record_id=str(_RECORD),
        base_id=str(_BASE),
        record_generation=1,
        stable_document_id=str(_DOC),
    )
    overrides: dict[str, object] = {}
    if field == "stable_document_id":
        overrides["stable_document_id"] = str(uuid4())
    elif field == "base_id":
        overrides["base_id"] = str(uuid4())
    else:
        overrides["record_generation"] = 99
    item = ReaderRecordAskEvidenceItem(
        handle_id="evh_" + ("ab" * 16),
        kind="search_hit",
        source_tool="search_current_article",
        snippet="x",
        rag_citation=_rag_public(**overrides),
    )
    with pytest.raises(EvidenceScopeInvariantError):
        assert_evidence_scope_matches_items(scope, [item])


def test_search_hit_with_null_scope_stable_fail_closed() -> None:
    scope = ReaderRecordAskEvidenceScope(
        reading_record_id=str(_RECORD),
        base_id=str(_BASE),
        record_generation=1,
        stable_document_id=None,
    )
    item = ReaderRecordAskEvidenceItem(
        handle_id="evh_" + ("cd" * 16),
        kind="search_hit",
        source_tool="search_current_article",
        snippet="x",
        rag_citation=_rag_public(),
    )
    with pytest.raises(EvidenceScopeInvariantError):
        assert_evidence_scope_matches_items(scope, [item])


def test_build_completed_dto_search_hit_mismatch_raises_not_ok() -> None:
    """Production must not emit final_status=ok when search_hit identity diverges."""
    env = _envelope()
    from app.services.reader_record_ask.evidence import ArticleRagCitationEvidence

    substrate = str(uuid4())
    wrong_stable = str(uuid4())
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
        stable_document_id=wrong_stable,  # wrong stable
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
    with pytest.raises(EvidenceScopeInvariantError):
        build_completed_dto(
            run_result=_ok_run((obs,)),
            message_id=str(uuid4()),
            thread_id=str(_THREAD),
            turn_run_id=str(uuid4()),
            envelope=env,
        )


@pytest.mark.asyncio
async def test_scope_invariant_violation_stream_terminals_without_completed() -> None:
    """Core production promise: invariant failure never emits ok completed.

    Through ``stream_agentic_thread_message``:
    - no message.completed
    - agentic.terminal + message.interrupted
    - DB terminal final_status=failed
    - terminal_reason=evidence_scope_invariant_violation
    - no completed write / no answer or evidence persistence
    - conflict identity not leaked on the wire
    """
    from app.services.reader_record_ask.evidence import ArticleRagCitationEvidence

    repo = _FakeRepo()
    wrong_stable = str(uuid4())
    secret_answer = "MUST_NOT_PERSIST_SCOPE_INVARIANT_ANSWER"
    secret_snippet = "MUST_NOT_LEAK_CONFLICTING_SNIPPET"

    async def _run(**kwargs):
        env = kwargs["envelope"]
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
            snippet=secret_snippet,
            reading_record_id=str(_RECORD),
            stable_document_id=wrong_stable,
            base_id=str(_BASE),
            record_generation=1,
        )
        obs = build_server_evidence_observation(
            kind="search_hit",
            envelope_fingerprint=env.envelope_fingerprint,
            source_tool="search_current_article",
            snippet=secret_snippet,
            rag_citation=cit,
        )
        return ReadingRecordAskRunResult(
            final_text=secret_answer,
            finalized=FinalizedAskResult(
                status="ok",
                answer_text=secret_answer,
                resolved_evidence=(obs,),
                envelope_fingerprint=env.envelope_fingerprint,
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
    names = [n for n, _ in events]
    assert EVENT_MESSAGE_COMPLETED not in names
    assert EVENT_AGENTIC_TERMINAL in names
    assert EVENT_MESSAGE_INTERRUPTED in names

    assert repo.completed_writes == []
    assert len(repo.terminal_writes) == 1
    tw = repo.terminal_writes[0]
    assert tw["final_status"] == "failed"
    assert tw["run_status"] == "failed"
    assert tw["terminal_reason"] == TERMINAL_REASON_EVIDENCE_SCOPE_INVARIANT
    assert tw.get("terminal_dto", {}).get("final_status") == "failed"
    assert (
        tw.get("terminal_dto", {}).get("terminal_reason")
        == TERMINAL_REASON_EVIDENCE_SCOPE_INVARIANT
    )
    # Terminal path must not persist displayable answer/evidence payloads.
    assert "answer_text" not in (tw.get("terminal_dto") or {})
    assert "evidence" not in (tw.get("terminal_dto") or {})
    assert tw.get("resolved_evidence_json") in (None, [], "[]") or not tw.get(
        "resolved_evidence_json"
    )

    wire_blob = json.dumps([d for _, d in events], ensure_ascii=False)
    assert secret_answer not in wire_blob
    assert secret_snippet not in wire_blob
    assert wrong_stable not in wire_blob
    for _name, data in events:
        if _name in {EVENT_AGENTIC_TERMINAL, EVENT_MESSAGE_INTERRUPTED}:
            assert data.get("terminal_reason") == TERMINAL_REASON_EVIDENCE_SCOPE_INVARIANT
            assert data.get("final_status") == "failed"
            assert "answer_text" not in data
            assert "evidence" not in data
            assert "evidence_scope" not in data


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
