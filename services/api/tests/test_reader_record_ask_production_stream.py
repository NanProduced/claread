"""Round-4A: agentic production stream, feature flag, SSE/persistence truth."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from app.config import settings as settings_mod
from app.schemas.reader_record_ask_stream import (
    EXECUTION_VERSION_AGENTIC_V1,  # noqa: F401 — legacy fixture labels
    EXECUTION_VERSION_AGENTIC_V2,
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
    TERMINAL_REASON_PERSIST_FAILED,
    EvidenceScopeInvariantError,
    assert_evidence_scope_matches_items,
    build_completed_dto,
    build_terminal_dto,
    evidence_scope_from_envelope,
    retry_agentic_thread_message,
    stream_agentic_thread_message,
)
from app.services.reader_record_ask.runtime import ReadingRecordAskRunResult
from app.services.reader_record_ask.runtime_events import (
    AnalysisFinishedEvent,
    AnalysisStartedEvent,
    AnswerDeltaEvent,
    RunStartedEvent,
)
from app.services.reader_record_ask.sse import (
    EVENT_AGENTIC_PROGRESS,
    EVENT_AGENTIC_RUN_STARTED,
    EVENT_AGENTIC_TERMINAL,
    EVENT_MESSAGE_COMPLETED,
    EVENT_MESSAGE_DELTA,
    EVENT_MESSAGE_INTERRUPTED,
    EVENT_REASONING_COMPLETED,
    EVENT_REASONING_STARTED,
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


def _make_execution_config(
    *,
    option_key: str,
    model: object,
    max_output_tokens: int = 3200,
    max_turn_output_tokens: int = 9600,
):
    """Build a real ReaderRecordAskExecutionConfig for service-layer tests.

    ASK-M1: service.py no longer calls ``build_model_for_route`` directly;
    tests now patch ``resolve_reader_record_ask_execution`` and return
    this config so a real model + budget propagates into
    ``stream_agentic_thread_message``.

    ASK-M1-R1: the config now also carries ``model_settings_payload``
    (with ``max_tokens``) and ``usage_limits`` so budget-capture tests
    can assert both the provider cap and the host guard.
    """
    from app.services.reader_ask.model_options import ReaderAskRuntimeBudgetConfig
    from app.services.reader_record_ask.execution_config import (
        ReaderRecordAskExecutionConfig,
    )

    return ReaderRecordAskExecutionConfig(
        option_key=option_key,
        model=model,  # type: ignore[arg-type]
        model_settings_payload={"max_tokens": max_output_tokens},
        usage_limits=_make_usage_limits(max_turn_output_tokens),
        runtime_budget=ReaderAskRuntimeBudgetConfig(
            max_input_tokens=24000,
            max_output_tokens=max_output_tokens,
            max_turn_output_tokens=max_turn_output_tokens,
            prompt_buffer_tokens=800,
        ),
    )


def _make_usage_limits(output_tokens_limit: int):
    """Build a PydanticAI UsageLimits with only output_tokens_limit set."""
    from pydantic_ai.usage import UsageLimits

    return UsageLimits(output_tokens_limit=output_tokens_limit)


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
        # H1: when True, complete_agentic_turn_run raises to simulate DB
        # persistence failure on the success path.
        self.complete_should_fail: bool = False
        # H3b: pre-populated assistant/user pair returned by the retry
        # lookup. When None, get_assistant_message_with_preceding_user_message
        # returns (None, None).
        self.retry_assistant: dict[str, Any] | None = None
        self.retry_user: dict[str, Any] | None = None
        self.reset_calls: list[UUID] = []

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

    async def get_assistant_message_with_preceding_user_message(
        self,
        *,
        thread_id: UUID,
        message_id: UUID,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        if self.retry_assistant is None:
            return None, None
        return dict(self.retry_assistant), (
            dict(self.retry_user) if self.retry_user is not None else None
        )

    async def reset_assistant_message_for_retry(
        self,
        *,
        message_id: UUID,
    ) -> dict[str, Any]:
        self.reset_calls.append(message_id)
        if self.retry_assistant is None:
            return {
                "id": str(message_id),
                "thread_id": str(_THREAD),
                "role": "assistant",
                "status": "streaming",
                "content_md": "",
            }
        reset = dict(self.retry_assistant)
        reset["status"] = "streaming"
        reset["content_md"] = ""
        return reset

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
        self.completed_writes.append(kwargs)
        dto = kwargs["completed_dto"]
        self.turns[str(kwargs["turn_run_id"])] = {
            "id": str(kwargs["turn_run_id"]),
            "status": "completed",
            "final_status": "ok",
            "user_visible_output_json": dto,
            "resolved_evidence_json": kwargs["resolved_evidence"],
            "envelope_fingerprint": None,
            "execution_version": EXECUTION_VERSION_AGENTIC_V2,
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
            "execution_version": EXECUTION_VERSION_AGENTIC_V2,
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
                            "app.services.reader_record_ask.service.resolve_reader_record_ask_execution",
                            return_value=_make_execution_config(
                                option_key="deepseek-v4-flash",
                                model=_function_model("agentic answer"),
                            ),
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
                                                stream = (
                                                    svc.stream_reading_record_ask_thread_message
                                                )
                                                async for c in stream(
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
                plan_content_sha256="c" * 64,
                stable_document_id=_DOC,
                base_id=_BASE,
                record_generation=1,
            )
        ]
    )
    # Run real agent with search then final via FunctionModel.
    # The search tool returns a canonical JSON string (produced by
    # ModelViewRenderer.render_tool_view → render_json), so a real LLM
    # must parse that JSON to extract evidence handle ids. The model_fn
    # below mirrors that: it json.loads the ToolReturnPart.content string
    # and pulls ``evidence_handles[0].handle_id``.
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
                # Tool return content is a canonical JSON string, not a
                # dict — a real LLM receives this string and parses it.
                if isinstance(content, str):
                    try:
                        content_obj = json.loads(content)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if not isinstance(content_obj, dict):
                        continue
                    ehs = content_obj.get("evidence_handles") or []
                    if ehs and isinstance(ehs[0], dict):
                        handle = ehs[0].get("handle_id")
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="final_result",
                        args=json.dumps(
                            {
                                "response_kind": "grounded_answer",
                                "answer_blocks": [
                                    {
                                        "text": "about climate",
                                        "basis": "article",
                                        "article_scope": "evidence_bounded",
                                        "evidence_handles": (
                                            [handle] if handle else []
                                        ),
                                    }
                                ],
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
    # Public completed is no-evh; restricted evidence lives only in persist.
    assert "evidence" not in completed[0]
    assert "envelope_fingerprint" not in completed[0]
    assert completed[0]["execution_version"] == EXECUTION_VERSION_AGENTIC_V2
    assert len(repo.completed_writes) == 1
    restricted = repo.completed_writes[0]["resolved_evidence"]
    assert any(item.get("kind") == "search_hit" for item in restricted)
    hit = next(item for item in restricted if item.get("kind") == "search_hit")
    rag = hit["rag_citation"]
    assert rag["stable_document_id"] == str(_DOC)
    assert rag["base_id"] == str(_BASE)
    assert rag["record_generation"] == 1


@pytest.mark.asyncio
async def test_fabricated_handle_after_search_hit_never_completes() -> None:
    """A model that cites a fabricated (mint-shaped but unregistered) handle
    after a successful search_current_article hit must NEVER produce a
    completed message or stale source UI state.

    Validates fail-closed safety boundary: provisional evidence registered
    by the search tool does NOT leak to the wire when the model fabricates
    a citation. The grounding_validator rejects the fabricated handle with
    ModelRetry; retries exhaust → UnexpectedModelBehavior → typed terminal.
    """
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
                plan_content_sha256="c" * 64,
                stable_document_id=_DOC,
                base_id=_BASE,
                record_generation=1,
            )
        ]
    )
    # Mint-shaped but NEVER registered in this turn's EvidenceRegistry.
    fabricated_handle = "evh_" + "ff" * 16
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
        # Always cite the FABRICATED handle — never the real one returned
        # by the search tool. This must be rejected by grounding_validator.
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="final_result",
                    args=json.dumps(
                        {
                            "answer_text": "fabricated citation",
                            "cited_evidence_handles": [fabricated_handle],
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
                    base_content_sha256=_SHA,
                    stable_document_id=_DOC,
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
    # Search DID execute (provisional evidence was registered server-side).
    events = _parse_sse(chunks)
    names = [n for n, _ in events]
    assert port.call_count == 1

    # No completed message — fabricated citation must never succeed.
    assert EVENT_MESSAGE_COMPLETED not in names
    assert EVENT_AGENTIC_TERMINAL in names
    assert EVENT_MESSAGE_INTERRUPTED in names

    # Typed terminal: fabricated citation must fail closed (no completed).
    assert len(repo.terminal_writes) == 1
    tw = repo.terminal_writes[0]
    assert tw["final_status"] == "failed"
    assert tw["run_status"] == "failed"
    assert tw["terminal_reason"] in {
        TERMINAL_REASON_AGENT_OUTPUT_INVALID,
        TERMINAL_REASON_AGENT_RUN_FAILED,
    }

    # Safety boundary: no provisional source data leaks to the wire.
    # The search hit snippet ("climate paragraph") must NEVER appear in
    # any SSE payload, terminal_dto, or persisted terminal row.
    # default=str is needed because the raw kwargs dict captured by the
    # fake repo contains UUID values (turn_run_id, message_id, etc.).
    blob = json.dumps([d for _, d in events], ensure_ascii=False)
    assert "climate paragraph" not in blob
    assert "climate paragraph" not in json.dumps(tw, ensure_ascii=False, default=str)
    # Terminal DTO has no evidence field at all (only rejected_handles).
    terminal_dto = tw.get("terminal_dto") or {}
    assert "evidence" not in terminal_dto
    # resolved_evidence_json on terminal path is always empty.
    assert tw.get("resolved_evidence_json") in (None, [], "[]")


@pytest.mark.asyncio
async def test_persist_failure_after_search_hit_does_not_leak_provisional_evidence() -> None:
    """When complete_agentic_turn_run raises AFTER a successful search hit
    + valid citation + ok finalizer, the persist-failed terminal must NOT
    preserve or leak the provisional source evidence.

    Validates fail-closed safety boundary: terminal_agentic_turn_run on the
    persist-failed path receives finalized=None → terminal_dto carries no
    evidence, no snippet, no source identity. The provisional evidence
    registered by the search tool is discarded, not persisted.
    """
    from app.services.reader_record_ask.article_rag_port import (
        ArticleRagHitView,
        ArticleRagSearchOutcome,
        FakeArticleRagSearchPort,
    )

    repo = _FakeRepo()
    repo.complete_should_fail = True
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
                plan_content_sha256="c" * 64,
                stable_document_id=_DOC,
                base_id=_BASE,
                record_generation=1,
            )
        ]
    )
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
        # Cite the REAL handle returned by the search tool — finalizer
        # succeeds, but complete_agentic_turn_run will raise.
        handle = None
        for msg in messages:
            for part in getattr(msg, "parts", []) or []:
                if type(part).__name__ != "ToolReturnPart":
                    continue
                content = getattr(part, "content", None)
                if isinstance(content, str):
                    try:
                        content_obj = json.loads(content)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if not isinstance(content_obj, dict):
                        continue
                    ehs = content_obj.get("evidence_handles") or []
                    if ehs and isinstance(ehs[0], dict):
                        handle = ehs[0].get("handle_id")
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="final_result",
                        args=json.dumps(
                            {
                                "response_kind": "grounded_answer",
                                "answer_blocks": [
                                    {
                                        "text": "about climate",
                                        "basis": "article",
                                        "article_scope": "evidence_bounded",
                                        "evidence_handles": (
                                            [handle] if handle else []
                                        ),
                                    }
                                ],
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
    # Search executed + finalizer succeeded (provisional evidence existed).
    assert port.call_count == 1
    events = _parse_sse(chunks)
    names = [n for n, _ in events]

    # No completed message — persist failure must never emit success.
    assert EVENT_MESSAGE_COMPLETED not in names
    assert EVENT_AGENTIC_TERMINAL in names
    assert EVENT_MESSAGE_INTERRUPTED in names

    # Typed terminal: persist_failed.
    assert len(repo.terminal_writes) == 1
    tw = repo.terminal_writes[0]
    assert tw["final_status"] == "failed"
    assert tw["run_status"] == "failed"
    assert tw["terminal_reason"] == TERMINAL_REASON_PERSIST_FAILED
    # complete_agentic_turn_run was attempted (and failed) — no success row.
    assert len(repo.completed_writes) == 0

    # Safety boundary: provisional source data must NOT leak.
    # The search hit snippet, source identity (stable_document_id,
    # base_id, chunk_id), and raw DB error text must never appear in
    # any SSE payload, terminal_dto, or persisted terminal row.
    # default=str is needed because the raw kwargs dict captured by the
    # fake repo contains UUID values (turn_run_id, message_id, etc.).
    blob = json.dumps([d for _, d in events], ensure_ascii=False)
    tw_blob = json.dumps(tw, ensure_ascii=False, default=str)
    # Snippet text must not leak.
    assert "climate paragraph" not in blob
    assert "climate paragraph" not in tw_blob
    # Source identity must not leak.
    assert str(_DOC) not in blob
    assert str(_DOC) not in tw_blob
    assert str(_BASE) not in blob
    assert str(_BASE) not in tw_blob
    assert "chunk_id" not in blob
    assert "chunk_id" not in tw_blob
    # Terminal DTO has no evidence field at all (only rejected_handles).
    terminal_dto = tw.get("terminal_dto") or {}
    assert "evidence" not in terminal_dto
    # resolved_evidence_json on terminal path is always empty.
    assert tw.get("resolved_evidence_json") in (None, [], "[]")
    # Raw DB error text must not leak.
    assert "simulated DB connection drop" not in blob
    assert "simulated DB connection drop" not in tw_blob


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
    assert sse_dto["execution_version"] == EXECUTION_VERSION_AGENTIC_V2
    assert sse_dto["final_status"] == "ok"
    assert sse_dto["answer_text"] == "done"
    assert "evidence" not in sse_dto
    assert "envelope_fingerprint" not in sse_dto
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
    """ReaderRecordAskCompletedDTO must never expose internal-only fields."""
    from app.schemas.reader_record_ask_stream import ReaderRecordAskCompletedDTO

    dto = ReaderRecordAskCompletedDTO(
        answer_text="answer",
        message_id="m-1",
        thread_id="t-1",
        turn_run_id="tr-1",
    )
    dto_json = dto.model_dump(mode="json")
    assert dto.execution_version == EXECUTION_VERSION_AGENTIC_V2
    for forbidden in (
        "response_kind",
        "coverage",
        "is_complete",
        "model_visible_chars",
        "article_total_chars",
        "baseline_status",
        "envelope_fingerprint",
        "evidence",
        "evidence_scope",
        "handle_id",
        "evh_",
    ):
        assert forbidden not in json.dumps(dto_json)


def test_build_completed_dto_public_v2_no_evidence() -> None:
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
    assert dto.execution_version == EXECUTION_VERSION_AGENTIC_V2
    assert dto.answer_text == "ans"
    wire = dto.model_dump(mode="json")
    for forbidden in (
        "evidence",
        "evidence_scope",
        "envelope_fingerprint",
        "handle_id",
        "evh_",
    ):
        assert forbidden not in json.dumps(wire)


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


def test_build_completed_dto_outputs_v2_public_fields() -> None:
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
    wire = dto.model_dump(mode="json")
    assert wire["execution_version"] == EXECUTION_VERSION_AGENTIC_V2
    assert wire["answer_text"] == "ans"
    assert "evidence_scope" not in wire
    assert "evidence" not in wire
    assert "envelope_fingerprint" not in wire


def test_build_completed_dto_rag_off_stable_null_ok() -> None:
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
    assert dto.answer_text == "ans"
    assert "evidence" not in dto.model_dump(mode="json")


def test_completed_dto_rejects_legacy_v1_public_fields() -> None:
    from pydantic import ValidationError

    base = {
        "execution_version": EXECUTION_VERSION_AGENTIC_V2,
        "final_status": "ok",
        "answer_text": "legacy answer",
        "message_id": "m1",
        "thread_id": "t1",
        "turn_run_id": "tr1",
    }
    ok = ReaderRecordAskCompletedDTO.model_validate(base)
    assert ok.answer_text == "legacy answer"

    with pytest.raises(ValidationError):
        ReaderRecordAskCompletedDTO.model_validate(
            {**base, "envelope_fingerprint": "f" * 64}
        )
    with pytest.raises(ValidationError):
        ReaderRecordAskCompletedDTO.model_validate({**base, "evidence": []})
    with pytest.raises(ValidationError):
        ReaderRecordAskCompletedDTO.model_validate(
            {
                **base,
                "evidence_scope": {
                    "reading_record_id": "r",
                    "base_id": "b",
                    "record_generation": 1,
                    "stable_document_id": None,
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
    from app.services.reader_record_ask.production_stream import (
        build_restricted_evidence_json,
    )

    run = _ok_run((obs,))
    dto = build_completed_dto(
        run_result=run,
        message_id=str(uuid4()),
        thread_id=str(_THREAD),
        turn_run_id=str(uuid4()),
        envelope=env,
    )
    assert "evidence" not in dto.model_dump(mode="json")
    restricted = build_restricted_evidence_json(run_result=run, envelope=env)
    assert restricted
    assert restricted[0]["rag_citation"]["stable_document_id"] == str(_DOC)


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

    with patch(
        "app.services.reader_orchestration.article_rag_embedding_provider."
        "build_default_article_rag_embedding_provider",
    ) as build_embedding:
        with patch(
            "app.services.reader_orchestration.article_rag_vector_search."
            "build_default_article_rag_vector_searcher",
        ) as build_searcher:
            with patch(
                "app.services.reader_orchestration.article_rag_retrieval_service."
                "ArticleRagRetrievalService",
            ) as build_retrieval:
                assert build_production_article_rag_port(settings) is None

    build_embedding.assert_not_called()
    build_searcher.assert_not_called()
    build_retrieval.assert_not_called()


def test_production_rag_factory_returns_none_when_providers_incomplete() -> None:
    """Enabled but Unconfigured* providers must not open a retrieval port."""
    from app.services.reader_orchestration.article_rag_index_worker import (
        UnconfiguredArticleRagEmbeddingProvider,
    )
    from app.services.reader_orchestration.article_rag_vector_search import (
        UnconfiguredArticleRagVectorSearcher,
    )
    from app.services.reader_record_ask.production_wiring import (
        build_production_article_rag_port,
    )

    settings = SimpleNamespace(reader_article_rag_enabled=True)

    with patch(
        "app.services.reader_orchestration.article_rag_embedding_provider."
        "build_default_article_rag_embedding_provider",
        return_value=UnconfiguredArticleRagEmbeddingProvider(),
    ):
        with patch(
            "app.services.reader_orchestration.article_rag_vector_search."
            "build_default_article_rag_vector_searcher",
            return_value=UnconfiguredArticleRagVectorSearcher(),
        ):
            with patch(
                "app.services.reader_orchestration.article_rag_retrieval_service."
                "ArticleRagRetrievalService",
            ) as build_retrieval:
                assert build_production_article_rag_port(settings) is None

    build_retrieval.assert_not_called()


def test_production_rag_factory_builds_retrieval_backed_port_when_ready() -> None:
    """Feature-on + complete providers wires the independent Ask port."""
    from app.services.reader_record_ask.article_rag_adapter import (
        RetrievalBackedArticleRagPort,
    )
    from app.services.reader_record_ask.production_wiring import (
        build_production_article_rag_port,
    )

    settings = SimpleNamespace(reader_article_rag_enabled=True)
    pool = object()
    # Non-Unconfigured stand-ins: any object that is not the Unconfigured* types.
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


# ---------------------------------------------------------------------------
# R4-A6-T2: reasoning lifecycle SSE events (phase-only "thinking…" signal)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reasoning_lifecycle_events_on_successful_run() -> None:
    """Successful run emits reasoning.started/completed — no reasoning.delta."""
    repo = _FakeRepo()

    async def _run(**kwargs):
        sink = kwargs["event_sink"]
        sink(
            RunStartedEvent(
                envelope_fingerprint=kwargs["envelope"].envelope_fingerprint,
                has_initial_selection=True,
            )
        )
        sink(AnalysisStartedEvent())
        sink(AnalysisFinishedEvent())
        return ReadingRecordAskRunResult(
            final_text="done",
            finalized=FinalizedAskResult(
                status="ok",
                answer_text="done",
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
            model=_function_model(),
            run_fn=_run,
            auto_wire_dependencies=False,
            stable_document_id=_DOC,
        )
    ]
    events = _parse_sse(chunks)
    names = [name for name, _ in events]

    assert names.count(EVENT_REASONING_STARTED) == 1
    assert names.count(EVENT_REASONING_COMPLETED) == 1
    # No reasoning content channel exists on the agentic path.
    assert "reasoning.delta" not in names

    run_started_at = names.index(EVENT_AGENTIC_RUN_STARTED)
    started_at = names.index(EVENT_REASONING_STARTED)
    completed_at = names.index(EVENT_REASONING_COMPLETED)
    message_completed_at = names.index(EVENT_MESSAGE_COMPLETED)

    # reasoning.started: after agentic.run_started, before the analysis
    # agentic.progress it introduces.
    assert run_started_at < started_at
    assert names[started_at + 1] == EVENT_AGENTIC_PROGRESS
    # reasoning.completed: after the analysis agentic.progress ("分析完成"),
    # before message.completed.
    assert names[completed_at - 1] == EVENT_AGENTIC_PROGRESS
    assert started_at < completed_at < message_completed_at

    # Payloads are phase signals only: message_id correlation and nothing
    # else — never reasoning text/length/hash.
    message_id = next(
        data for name, data in events if name == "message.started"
    )["message_id"]
    for name, data in events:
        if name in (EVENT_REASONING_STARTED, EVENT_REASONING_COMPLETED):
            assert data == {"message_id": message_id}
        # No event on the agentic path may carry reasoning content fields.
        assert "delta" not in data
        assert "reasoning_md" not in data
        assert "thinking" not in data


@pytest.mark.asyncio
async def test_reasoning_started_without_completed_when_run_fails() -> None:
    """Failed run: AnalysisFinishedEvent never fires → no reasoning.completed.

    Mirrors thinking_transport behavior: the finished phase signal is only
    emitted on normal analysis completion, so an interrupted run leaves the
    lifecycle half-open and the client falls back to message.interrupted.
    """
    repo = _FakeRepo()

    async def _run(**kwargs):
        sink = kwargs["event_sink"]
        sink(AnalysisStartedEvent())
        raise RuntimeError("boom")

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
            model=_function_model(),
            run_fn=_run,
            auto_wire_dependencies=False,
            stable_document_id=_DOC,
        )
    ]
    events = _parse_sse(chunks)
    names = [name for name, _ in events]

    assert names.count(EVENT_REASONING_STARTED) == 1
    assert EVENT_REASONING_COMPLETED not in names
    assert "reasoning.delta" not in names
    # Interrupted terminal, never message.completed.
    assert EVENT_MESSAGE_COMPLETED not in names
    assert EVENT_AGENTIC_TERMINAL in names
    assert EVENT_MESSAGE_INTERRUPTED in names
    terminal = next(d for n, d in events if n == EVENT_AGENTIC_TERMINAL)
    assert terminal["terminal_reason"] == TERMINAL_REASON_AGENT_RUN_FAILED


# ---------------------------------------------------------------------------
# R4-A6: message.delta token-level answer_text streaming
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_message_delta_streams_answer_text_on_success() -> None:
    """AnswerDeltaEvents map 1:1 to message.delta SSE, ordered inside the
    reasoning lifecycle and completed by message.completed."""
    repo = _FakeRepo()

    async def _run(**kwargs):
        sink = kwargs["event_sink"]
        sink(
            RunStartedEvent(
                envelope_fingerprint=kwargs["envelope"].envelope_fingerprint,
                has_initial_selection=True,
            )
        )
        sink(AnalysisStartedEvent())
        sink(AnswerDeltaEvent(delta="Hello"))
        sink(AnswerDeltaEvent(delta=" world"))
        sink(AnalysisFinishedEvent())
        return ReadingRecordAskRunResult(
            final_text="Hello world",
            finalized=FinalizedAskResult(
                status="ok",
                answer_text="Hello world",
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
            model=_function_model(),
            run_fn=_run,
            auto_wire_dependencies=False,
            stable_document_id=_DOC,
        )
    ]
    events = _parse_sse(chunks)
    names = [name for name, _ in events]

    # Exactly two message.delta events carrying the raw increments.
    deltas = [data for name, data in events if name == EVENT_MESSAGE_DELTA]
    assert deltas == [{"delta": "Hello"}, {"delta": " world"}]

    # Final completed DTO carries the full answer.
    completed = next(d for n, d in events if n == EVENT_MESSAGE_COMPLETED)
    assert completed["answer_text"] == "Hello world"

    # Order: reasoning.started → first delta → last delta →
    # reasoning.completed → message.completed.
    reasoning_started_at = names.index(EVENT_REASONING_STARTED)
    first_delta_at = names.index(EVENT_MESSAGE_DELTA)
    last_delta_at = len(names) - 1 - names[::-1].index(EVENT_MESSAGE_DELTA)
    reasoning_completed_at = names.index(EVENT_REASONING_COMPLETED)
    message_completed_at = names.index(EVENT_MESSAGE_COMPLETED)
    assert reasoning_started_at < first_delta_at
    assert first_delta_at < last_delta_at
    assert last_delta_at < reasoning_completed_at
    assert reasoning_completed_at < message_completed_at

    # Privacy: no reasoning channel, no reasoning fields in delta payloads.
    assert "reasoning.delta" not in names
    for data in deltas:
        assert "reasoning_md" not in data
        assert "thinking" not in data


@pytest.mark.asyncio
async def test_message_delta_partial_then_failure_no_completed() -> None:
    """Deltas already streamed when the run fails are kept; the turn
    terminates interrupted without message.completed."""
    repo = _FakeRepo()

    async def _run(**kwargs):
        sink = kwargs["event_sink"]
        sink(AnalysisStartedEvent())
        sink(AnswerDeltaEvent(delta="partial"))
        raise RuntimeError("boom")

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
            model=_function_model(),
            run_fn=_run,
            auto_wire_dependencies=False,
            stable_document_id=_DOC,
        )
    ]
    events = _parse_sse(chunks)
    names = [name for name, _ in events]

    deltas = [data for name, data in events if name == EVENT_MESSAGE_DELTA]
    assert deltas == [{"delta": "partial"}]
    assert EVENT_MESSAGE_COMPLETED not in names
    assert EVENT_AGENTIC_TERMINAL in names
    assert EVENT_MESSAGE_INTERRUPTED in names
    terminal = next(d for n, d in events if n == EVENT_AGENTIC_TERMINAL)
    assert terminal["terminal_reason"] == TERMINAL_REASON_AGENT_RUN_FAILED


# ---------------------------------------------------------------------------
# H1: success-path DB persistence failure emits typed terminal
# (regression: stream used to end without message.completed/interrupted
#  when repo.complete_agentic_turn_run raised, leaving the frontend hanging)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_failed_emits_typed_terminal_no_completed() -> None:
    """When complete_agentic_turn_run raises on the success path, the stream
    emits agentic.terminal + message.interrupted with the typed
    persist_failed reason and never emits message.completed.

    Privacy: the underlying DB error text must not leak into the SSE
    payload or terminal_reason.
    """
    repo = _FakeRepo()
    repo.complete_should_fail = True

    async def _run(**kwargs):
        env = kwargs["envelope"]
        EvidenceRegistry(env.envelope_fingerprint)
        return ReadingRecordAskRunResult(
            final_text="ok answer",
            finalized=FinalizedAskResult(
                status="ok",
                answer_text="ok answer",
                resolved_evidence=(),
                envelope_fingerprint=env.envelope_fingerprint,
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
            model=_function_model("ok answer"),
            run_fn=_run,
            auto_wire_dependencies=False,
            stable_document_id=_DOC,
        )
    ]
    events = _parse_sse(chunks)
    names = [name for name, _ in events]

    # No success terminal.
    assert EVENT_MESSAGE_COMPLETED not in names

    # Typed failure terminal.
    assert EVENT_AGENTIC_TERMINAL in names
    assert EVENT_MESSAGE_INTERRUPTED in names
    terminal = next(d for n, d in events if n == EVENT_AGENTIC_TERMINAL)
    assert terminal["final_status"] == "failed"
    assert terminal["terminal_reason"] == TERMINAL_REASON_PERSIST_FAILED

    # Persistence: terminal_agentic_turn_run called with the typed reason.
    assert len(repo.terminal_writes) == 1
    term_write = repo.terminal_writes[0]
    assert term_write["final_status"] == "failed"
    assert term_write["terminal_reason"] == TERMINAL_REASON_PERSIST_FAILED
    # complete_agentic_turn_run was attempted (and failed).
    assert len(repo.completed_writes) == 0

    # Privacy: raw DB error text must not leak into any SSE payload.
    raw = json.dumps([d for _, d in events])
    assert "simulated DB connection drop" not in raw
    assert "RuntimeError" not in raw


# ---------------------------------------------------------------------------
# H3b: retry_agentic_thread_message resets existing assistant message and
# reuses the preceding user message content (no new user message created)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_agentic_resets_message_and_reuses_user_content() -> None:
    """retry_agentic_thread_message loads the existing assistant + preceding
    user message, resets the assistant message to streaming, and re-runs the
    agent with the original user content. No new user message is created.
    """
    repo = _FakeRepo()
    existing_assistant_id = UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")
    existing_user_id = UUID("55555555-5555-5555-5555-555555555555")
    original_user_content = "What is the main thesis of this article?"
    repo.retry_assistant = {
        "id": str(existing_assistant_id),
        "thread_id": str(_THREAD),
        "role": "assistant",
        "status": "completed",
        "content_md": "old answer",
    }
    repo.retry_user = {
        "id": str(existing_user_id),
        "thread_id": str(_THREAD),
        "role": "user",
        "status": "completed",
        "content_md": original_user_content,
    }

    captured: dict[str, Any] = {}

    async def _run(**kwargs):
        env = kwargs["envelope"]
        captured["envelope"] = env
        captured["user_message"] = kwargs.get("user_message")
        EvidenceRegistry(env.envelope_fingerprint)
        return ReadingRecordAskRunResult(
            final_text="new retry answer",
            finalized=FinalizedAskResult(
                status="ok",
                answer_text="new retry answer",
                resolved_evidence=(),
                envelope_fingerprint=env.envelope_fingerprint,
            ),
        )

    chunks = [
        c
        async for c in retry_agentic_thread_message(
            user_id=_USER,
            reading_record_id=_RECORD,
            thread_id=_THREAD,
            message_id=existing_assistant_id,
            facts=_fake_facts(),
            repository=repo,  # type: ignore[arg-type]
            model=_function_model("new retry answer"),
            run_fn=_run,
            auto_wire_dependencies=False,
        )
    ]
    events = _parse_sse(chunks)
    names = [name for name, _ in events]

    # reset_assistant_message_for_retry was called with the existing id.
    assert repo.reset_calls == [existing_assistant_id]

    # No new user message was created (retry mode reuses existing).
    new_user_msgs = [
        m for m in repo.messages if m.get("role") == "user"
    ]
    assert new_user_msgs == []

    # Agent received the original user content.
    assert captured["user_message"] == original_user_content

    # Completed event carries the new answer.
    assert EVENT_MESSAGE_COMPLETED in names
    completed = next(d for n, d in events if n == EVENT_MESSAGE_COMPLETED)
    assert completed["answer_text"] == "new retry answer"
    # Completed message_id is the existing assistant message (was reset).
    assert completed["message_id"] == str(existing_assistant_id)


@pytest.mark.asyncio
async def test_retry_agentic_missing_assistant_emits_error_no_turn() -> None:
    """When the retried message_id does not resolve to an assistant message
    in the thread, retry emits an SSE error frame and creates no turn_run.
    """
    repo = _FakeRepo()
    # retry_assistant stays None → lookup returns (None, None)
    missing_id = UUID("deadbeef-dead-beef-dead-beefdeadbeef")

    chunks = [
        c
        async for c in retry_agentic_thread_message(
            user_id=_USER,
            reading_record_id=_RECORD,
            thread_id=_THREAD,
            message_id=missing_id,
            facts=_fake_facts(),
            repository=repo,  # type: ignore[arg-type]
            model=_function_model(),
            auto_wire_dependencies=False,
        )
    ]
    events = _parse_sse(chunks)
    names = [name for name, _ in events]

    # Error frame, no turn/run events.
    assert "error" in names
    assert EVENT_AGENTIC_RUN_STARTED not in names
    assert EVENT_MESSAGE_COMPLETED not in names
    # No reset, no completed, no terminal writes.
    assert repo.reset_calls == []
    assert repo.completed_writes == []
    assert repo.terminal_writes == []


# ---------------------------------------------------------------------------
# H3c: service.retry_reading_record_ask_message routes by feature flag
# (flag on → agentic retry; flag off → legacy stream_service.retry_thread_message)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_service_flag_off_uses_legacy_stream() -> None:
    _clear_settings_cache()
    with patch.object(
        settings_mod.get_settings(),
        "reader_record_ask_agentic_enabled",
        False,
    ):
        async def _legacy(**kwargs):
            yield encode_sse("message.completed", {"legacy": True})

        with patch(
            "app.services.reader_record_ask.service._load_snapshot_facts",
            new_callable=AsyncMock,
            return_value=_fake_facts(),
        ):
            with patch(
                "app.services.reader_record_ask.service.stream_service.retry_thread_message",
                side_effect=_legacy,
            ) as mock_legacy:
                from app.schemas.reader_ask import ReaderAskMessageRetryRequest
                from app.services.reader_record_ask import service as svc

                msg_id = uuid4()
                chunks = []
                async for c in svc.retry_reading_record_ask_message(
                    user_id=_USER,
                    reading_record_id=str(_RECORD),
                    thread_id=_THREAD,
                    message_id=msg_id,
                    request=ReaderAskMessageRetryRequest(),
                ):
                    chunks.append(c)
                assert mock_legacy.call_count == 1
                assert any("legacy" in c for c in chunks)
    _clear_settings_cache()


@pytest.mark.asyncio
async def test_retry_service_flag_on_uses_agentic_path() -> None:
    _clear_settings_cache()
    with patch.object(
        settings_mod.get_settings(),
        "reader_record_ask_agentic_enabled",
        True,
    ):
        with patch(
            "app.services.reader_record_ask.service._load_snapshot_facts",
            new_callable=AsyncMock,
            return_value=_fake_facts(),
        ):
            with patch(
                "app.services.reader_record_ask.service.stream_service.retry_thread_message",
                new_callable=AsyncMock,
            ) as mock_legacy:
                repo = _FakeRepo()
                existing_assistant_id = UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")
                repo.retry_assistant = {
                    "id": str(existing_assistant_id),
                    "thread_id": str(_THREAD),
                    "role": "assistant",
                    "status": "completed",
                    "content_md": "old",
                }
                repo.retry_user = {
                    "id": str(UUID("55555555-5555-5555-5555-555555555555")),
                    "thread_id": str(_THREAD),
                    "role": "user",
                    "status": "completed",
                    "content_md": "original question",
                }

                async def _run(**kwargs):
                    env = kwargs["envelope"]
                    EvidenceRegistry(env.envelope_fingerprint)
                    return ReadingRecordAskRunResult(
                        final_text="agentic retry answer",
                        finalized=FinalizedAskResult(
                            status="ok",
                            answer_text="agentic retry answer",
                            resolved_evidence=(),
                            envelope_fingerprint=env.envelope_fingerprint,
                        ),
                    )

                with patch(
                    "app.services.reader_record_ask.service.thread_service.resolve_and_persist_thread_model_option",
                    new_callable=AsyncMock,
                    return_value=MagicMock(
                        key="deepseek-v4-flash",
                        selection=MagicMock(),
                    ),
                ):
                    with patch(
                        "app.services.reader_record_ask.service.resolve_reader_record_ask_execution",
                        return_value=_make_execution_config(
                            option_key="deepseek-v4-flash",
                            model=_function_model("agentic retry answer"),
                        ),
                    ):
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
                                    return_value=_function_model("agentic retry answer"),
                                ):
                                    with patch(
                                        "app.services.reader_record_ask.production_stream.load_active_stable_document_id",
                                        new_callable=AsyncMock,
                                        return_value=_DOC,
                                    ):
                                        from app.schemas.reader_ask import (
                                            ReaderAskMessageRetryRequest,
                                        )
                                        from app.services.reader_record_ask import (
                                            service as svc,
                                        )

                                        chunks = []
                                        async for c in svc.retry_reading_record_ask_message(
                                            user_id=_USER,
                                            reading_record_id=str(_RECORD),
                                            thread_id=_THREAD,
                                            message_id=existing_assistant_id,
                                            request=ReaderAskMessageRetryRequest(),
                                        ):
                                            chunks.append(c)
                # Legacy retry never called.
                assert mock_legacy.call_count == 0
                events = _parse_sse(chunks)
                names = [n for n, _ in events]
                assert EVENT_MESSAGE_COMPLETED in names
                completed = next(
                    d for n, d in events if n == EVENT_MESSAGE_COMPLETED
                )
                assert completed["answer_text"] == "agentic retry answer"
                # Assistant message was reset via agentic path.
                assert repo.reset_calls == [existing_assistant_id]
    _clear_settings_cache()
