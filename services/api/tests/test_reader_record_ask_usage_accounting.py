"""Provider usage accounting for agentic Reading Record Ask turns.

One turn, one provider run: usage is captured from the same agent run's
public API (``AgentRunResult.usage`` / streamed ``AgentRunResultEvent``),
normalized through the shared ``build_usage_metadata`` entry, costed with
the reader-ask billing config, persisted as one invocation-keyed
``ai_usage_events`` row, and linked on ``reader_ask_turn_runs``.

Frozen invariants under test:

- usage comes only from the same-run public API — never guessed from
  reasoning length, SSE deltas, or character counts;
- unavailable usage stays ``None`` (no fabricated zero usage);
- tool rounds / output-validator retries use the agent-run aggregate —
  never per-event re-accumulation;
- billing is audit-only: ``billed_points`` stays NULL, no credit
  account / ledger mutation;
- a turn_run owns at most one usage event (invocation-keyed replay);
  regenerate creates a new turn_run and therefore a new event;
- usage accounting failures never change the answer or the terminal
  status, and never trigger a second provider call.

No real provider is called (FunctionModel only). DB tests use the
per-test isolated schema pattern (fresh schema, dropped on teardown).
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RunUsage

from app.database.connection import init_connection
from app.schemas.reader_record_ask_stream import EXECUTION_VERSION_AGENTIC_V2
from app.services.reader_record_ask.context_envelope import (
    VerifiedEnvelopeInput,
    build_context_envelope,
)
from app.services.reader_record_ask.document_access import (
    InMemoryDocumentAccess,
    ReadingUnitView,
    build_document_scope,
)
from app.services.reader_record_ask.evidence_registry import EvidenceRegistry
from app.services.reader_record_ask.fence import StaticGenerationFence
from app.services.reader_record_ask.finalizer import FinalizedAskResult
from app.services.reader_record_ask.grounding_validator import (
    AgentAnswerDraftOutput,
)
from app.services.reader_record_ask.production_stream import (
    stream_agentic_thread_message,
)
from app.services.reader_record_ask.runtime import ReadingRecordAskRunResult
from app.services.reader_record_ask.runtime_deps import ReaderRecordAskDeps
from app.services.reader_record_ask.thinking_transport import (
    run_agent_with_thinking_transport,
)
from tests.test_reader_record_ask_production_stream import (
    _fake_facts,
    _FakeRepo,
    _parse_sse,
)

_USER = UUID("11111111-1111-1111-1111-111111111111")
_RECORD = UUID("22222222-2222-2222-2222-222222222222")
_BASE = UUID("33333333-3333-3333-3333-333333333333")
_DOC = UUID("44444444-4444-4444-4444-444444444444")
_SHA = "b" * 64
_THREAD = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

# Privacy sentinel: must never appear in any usage event metadata.
_SENTINEL_SECRET = "SECRET-API-KEY-DO-NOT-LOG-9f3"

_ANSWER_JSON = json.dumps(
    {
        "response_kind": "clarification",
        "clarification_text": "Which aspect?",
        "answer_blocks": [],
    }
)


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------


def _transport_deps() -> ReaderRecordAskDeps:
    envelope = _usage_envelope()
    return ReaderRecordAskDeps(
        envelope=envelope,
        document_access=_usage_access(),
        fence=StaticGenerationFence(live_generation=envelope.record_generation),
        evidence_registry=EvidenceRegistry(envelope_fingerprint=envelope.envelope_fingerprint),
    )


def _usage_envelope():
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


def _usage_access():
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


def _simple_agent(model) -> Agent[ReaderRecordAskDeps, AgentAnswerDraftOutput]:
    return Agent(
        model,
        deps_type=ReaderRecordAskDeps,
        output_type=AgentAnswerDraftOutput,
    )


def _run_result_with_usage(
    *,
    usage_summary: dict[str, Any] | None,
    status: str = "ok",
) -> ReadingRecordAskRunResult:
    finalized = FinalizedAskResult(
        status=status,  # type: ignore[arg-type]
        answer_text=("done" if status == "ok" else None),
        resolved_evidence=(),
        envelope_fingerprint=_usage_envelope().envelope_fingerprint,
    )
    return ReadingRecordAskRunResult(
        final_text="done" if status == "ok" else None,
        finalized=finalized,
        usage_summary=usage_summary,
    )


class _UsageRecorderSpy:
    """Replacement for the production usage-event recorder.

    Captures every call (event + invocation key + observation hash) so
    tests can assert the persisted identity without a database.
    """

    def __init__(self, event_id: UUID | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.event_id = event_id or uuid4()

    async def __call__(self, event: Any, **kwargs: Any) -> tuple[UUID | None, str]:
        self.calls.append({"event": event, **kwargs})
        return self.event_id, "inserted"


class _RaisingRecorder:
    async def __call__(self, event: Any, **kwargs: Any) -> tuple[UUID | None, str]:
        raise RuntimeError(f"simulated recorder crash {_SENTINEL_SECRET}")


async def _stream_with_run(
    run_fn,
    repo: _FakeRepo | None = None,
    *,
    recorder: Any = None,
    execution_snapshot: Any = None,
    model_option_key: str | None = "test-option",
):

    repo = repo if repo is not None else _FakeRepo()
    chunks = []
    async for c in stream_agentic_thread_message(
        user_id=_USER,
        reading_record_id=_RECORD,
        thread_id=_THREAD,
        content="q",
        facts=_fake_facts(),
        request_anchor=None,
        repository=repo,  # type: ignore[arg-type]
        model=_function_model(),
        run_fn=run_fn,
        auto_wire_dependencies=False,
        stable_document_id=_DOC,
        model_option_key=model_option_key,
        execution_snapshot=execution_snapshot,
        usage_event_recorder=recorder,
    ):
        chunks.append(c)
    return _parse_sse(chunks), repo


def _function_model(answer: str = "ok answer"):
    async def model_fn(messages, info):
        del messages, info
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="final_result",
                    args=json.dumps(
                        {
                            "answer_text": answer,
                            "cited_evidence_handles": [],
                            "response_kind": "grounded_answer",
                        }
                    ),
                    tool_call_id="f1",
                )
            ]
        )

    return FunctionModel(model_fn)


# ---------------------------------------------------------------------------
# Section 1 — capture at the transport boundary (same-run public API)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streamed_single_turn_captures_provider_usage() -> None:
    """Streamed success exposes agent-run aggregate usage on the outcome."""

    async def stream_fn(messages, info):
        del messages, info
        yield _ANSWER_JSON

    model = FunctionModel(stream_function=stream_fn)
    outcome = await run_agent_with_thinking_transport(
        agent=_simple_agent(model),
        prompt="question",
        deps=_transport_deps(),
        model=model,
    )

    assert isinstance(outcome.output, AgentAnswerDraftOutput)
    usage = outcome.usage_summary
    assert usage is not None, "streamed run must expose provider usage"
    assert usage["input_tokens"] > 0
    assert usage["output_tokens"] > 0
    assert usage["total_tokens"] == usage["input_tokens"] + usage["output_tokens"]


@pytest.mark.asyncio
async def test_nonstream_fallback_captures_explicit_provider_usage() -> None:
    """FunctionModel without stream_function falls back to agent.run and
    reports the provider-declared usage verbatim."""

    async def fn(messages, info):
        del messages, info
        return ModelResponse(
            parts=[TextPart(content=_ANSWER_JSON)],
            usage=RunUsage(input_tokens=120, output_tokens=45),
        )

    model = FunctionModel(function=fn)
    outcome = await run_agent_with_thinking_transport(
        agent=_simple_agent(model),
        prompt="question",
        deps=_transport_deps(),
        model=model,
    )

    assert outcome.usage_summary == {
        "input_tokens": 120,
        "output_tokens": 45,
        "total_tokens": 165,
        "model_requests": 1,
        "tool_calls": 0,
    }


@pytest.mark.asyncio
async def test_tool_round_usage_aggregated_once_not_per_event() -> None:
    """Two provider responses (tool round) → one aggregate, equal to the
    SDK's own run usage — never per-SSE-event re-accumulation."""

    calls = {"n": 0}

    def expand_evidence(pointer: str = "") -> str:
        del pointer
        return json.dumps({"status": "empty"})

    async def fn(messages, info):
        calls["n"] += 1
        has_tool_return = any(
            type(p).__name__ == "ToolReturnPart"
            for m in messages
            for p in getattr(m, "parts", []) or []
        )
        if not has_tool_return:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="expand_evidence",
                        args=json.dumps({"pointer": ""}),
                        tool_call_id="tc1",
                    )
                ],
                usage=RunUsage(input_tokens=30, output_tokens=10),
            )
        return ModelResponse(
            parts=[TextPart(content=_ANSWER_JSON)],
            usage=RunUsage(input_tokens=20, output_tokens=5),
        )

    model = FunctionModel(function=fn)
    agent = Agent(
        model,
        deps_type=ReaderRecordAskDeps,
        output_type=AgentAnswerDraftOutput,
        tools=[expand_evidence],
    )
    outcome = await run_agent_with_thinking_transport(
        agent=agent,
        prompt="question",
        deps=_transport_deps(),
        model=model,
    )

    assert calls["n"] == 2
    usage = outcome.usage_summary
    assert usage is not None
    # Aggregate across both responses — exactly once each.
    assert usage["input_tokens"] == 50
    assert usage["output_tokens"] == 15
    assert usage["total_tokens"] == 65


@pytest.mark.asyncio
async def test_output_validator_retry_does_not_double_count_usage() -> None:
    """Output-validator ModelRetry triggers a second provider response;
    usage stays the run aggregate (both responses counted once each)."""

    from pydantic_ai import ModelRetry

    calls = {"n": 0}

    async def fn(messages, info):
        calls["n"] += 1
        if calls["n"] == 1:
            return ModelResponse(
                parts=[TextPart(content="not valid json")],
                usage=RunUsage(input_tokens=100, output_tokens=7),
            )
        return ModelResponse(
            parts=[TextPart(content=_ANSWER_JSON)],
            usage=RunUsage(input_tokens=110, output_tokens=8),
        )

    model = FunctionModel(function=fn)
    agent = Agent(
        model,
        deps_type=ReaderRecordAskDeps,
        output_type=AgentAnswerDraftOutput,
    )

    @agent.output_validator
    async def reject_first_attempt(output: AgentAnswerDraftOutput) -> AgentAnswerDraftOutput:
        if calls["n"] == 1:
            raise ModelRetry("first attempt rejected")
        return output

    outcome = await run_agent_with_thinking_transport(
        agent=agent,
        prompt="question",
        deps=_transport_deps(),
        model=model,
    )

    assert calls["n"] >= 2
    usage = outcome.usage_summary
    assert usage is not None
    assert usage["input_tokens"] == 210
    assert usage["output_tokens"] == 15


@pytest.mark.asyncio
async def test_unavailable_usage_is_none_not_fabricated_zero() -> None:
    """When the provider result exposes no usage values, the summary is
    None — never a fabricated zero-usage dict."""

    from pydantic_ai.usage import RunUsage

    from app.services.reader_record_ask.thinking_transport import (
        normalize_run_usage,
    )

    assert normalize_run_usage(None) is None
    assert normalize_run_usage(RunUsage()) is None
    assert normalize_run_usage(RunUsage(input_tokens=1)) == {
        "input_tokens": 1,
        "output_tokens": 0,
        "total_tokens": 1,
        "model_requests": 0,
        "tool_calls": 0,
    }


@pytest.mark.asyncio
async def test_agent_failure_after_partial_response_keeps_usage_unavailable() -> None:
    """A provider failure mid-run raises through the transport; no usage
    summary is fabricated for the partial responses."""

    async def fn(messages, info):
        del messages, info
        raise RuntimeError("provider dropped mid-response")

    model = FunctionModel(function=fn)
    with pytest.raises(Exception):  # noqa: B017, PT011 - typed below
        await run_agent_with_thinking_transport(
            agent=_simple_agent(model),
            prompt="question",
            deps=_transport_deps(),
            model=model,
        )


@pytest.mark.asyncio
async def test_reasoning_observer_does_not_change_usage_ownership() -> None:
    """Reasoning on/off (observer present or absent) leaves usage capture
    and the provider call count identical."""

    from app.services.reader_record_ask.thinking_transport import (
        BoundedThinkingObserver,
    )

    calls = {"n": 0}

    async def stream_fn(messages, info):
        calls["n"] += 1
        del messages, info
        yield _ANSWER_JSON

    model = FunctionModel(stream_function=stream_fn)

    outcome_plain = await run_agent_with_thinking_transport(
        agent=_simple_agent(model),
        prompt="question",
        deps=_transport_deps(),
        model=model,
    )
    first_calls = calls["n"]
    plain_usage = outcome_plain.usage_summary

    outcome_observed = await run_agent_with_thinking_transport(
        agent=_simple_agent(model),
        prompt="question",
        deps=_transport_deps(),
        model=model,
        thinking_observer=BoundedThinkingObserver(),
    )

    assert calls["n"] == first_calls * 2, "observer must not add provider calls"
    assert outcome_observed.usage_summary == plain_usage


# ---------------------------------------------------------------------------
# Section 2 — runtime propagation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runtime_propagates_usage_summary_to_run_result() -> None:
    """run_reading_record_ask forwards the transport's usage summary."""

    from app.services.reader_record_ask.runtime import run_reading_record_ask

    async def fn(messages, info):
        del messages, info
        return ModelResponse(
            parts=[TextPart(content=_ANSWER_JSON)],
            usage=RunUsage(input_tokens=64, output_tokens=12),
        )

    model = FunctionModel(function=fn)
    result = await run_reading_record_ask(
        user_message="question",
        envelope=_usage_envelope(),
        document_access=_usage_access(),
        model=model,
    )

    assert result.usage_summary == {
        "input_tokens": 64,
        "output_tokens": 12,
        "total_tokens": 76,
        "model_requests": 1,
        "tool_calls": 0,
    }


# ---------------------------------------------------------------------------
# Section 3 — production stream accounting (fake repo, recorder spy)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_successful_turn_records_usage_event_and_links_turn_run() -> None:
    recorder = _UsageRecorderSpy()

    async def _run(**kwargs):
        return _run_result_with_usage(
            usage_summary={"input_tokens": 100, "output_tokens": 40, "total_tokens": 140},
        )

    events, repo = await _stream_with_run(_run, recorder=recorder)
    names = [name for name, _ in events]
    assert "message.completed" in names

    assert len(recorder.calls) == 1
    call = recorder.calls[0]
    event = call["event"]
    assert event.usage_scope == "user_billed"
    assert event.capability_code == "reader_ask"
    assert event.billing_mode == "user_points"
    assert event.status == "succeeded"
    assert event.user_id == _USER
    assert event.reading_record_id == _RECORD
    assert event.request_id == str(repo.completed_writes[0]["turn_run_id"])
    assert event.usage_data == {
        "input_tokens": 100,
        "output_tokens": 40,
        "total_tokens": 140,
    }
    assert event.billed_points is None
    assert call["invocation_key"] == f"reader_ask:turn:{repo.completed_writes[0]['turn_run_id']}"
    assert call["observation_hash"]

    # turn-run linkage: usage summary + event id persisted together.
    assert len(repo.completed_writes) == 1
    write = repo.completed_writes[0]
    assert write["usage_summary"] == {
        "input_tokens": 100,
        "output_tokens": 40,
        "total_tokens": 140,
    }
    assert write["usage_event_id"] == recorder.event_id


@pytest.mark.asyncio
async def test_event_metadata_carries_identity_billing_and_cost_only() -> None:
    """Metadata carries identity + billing facts; never prompt/answer/
    reasoning/secrets/exception text."""
    from app.services.reader_record_ask.execution_config import (
        ReaderRecordAskExecutionSnapshot,
    )

    snapshot = ReaderRecordAskExecutionSnapshot(
        option_key="deepseek-v4-flash",
        provider="dashscope",
        model_name="deepseek-v4-flash",
        profile_name="ask-main-deepseek-v4-flash",
        adapter="openai_compatible",
        max_output_tokens=3200,
        max_turn_output_tokens=9600,
        max_input_tokens=24000,
        prompt_buffer_tokens=800,
        policy_version="reader_record_ask_execution_v2",
        budget_fingerprint="f" * 64,
        price_multiplier=2.0,
        billing_policy_version="analysis_weighted_tokens_v1",
    )
    recorder = _UsageRecorderSpy()

    async def _run(**kwargs):
        return _run_result_with_usage(
            usage_summary={"input_tokens": 1000, "output_tokens": 200, "total_tokens": 1200},
        )

    events, repo = await _stream_with_run(
        _run,
        recorder=recorder,
        execution_snapshot=snapshot,
    )
    assert "message.completed" in [name for name, _ in events]

    event = recorder.calls[0]["event"]
    assert event.model_route == "reader_ask"
    assert event.model_provider == "dashscope"
    assert event.model_name == "deepseek-v4-flash"
    assert event.model_profile == "ask-main-deepseek-v4-flash"
    assert event.billing_policy_version == "analysis_weighted_tokens_v1"

    meta = event.metadata_json
    assert meta["model_option_key"] == "deepseek-v4-flash"
    assert meta["price_multiplier"] == 2.0
    assert meta["billing_policy_version"] == "analysis_weighted_tokens_v1"
    # ceil((1000 * 1 + 200 * 5) * 2.0 / 1000) = ceil(4.0) = 4
    assert meta["computed_cost_points"] == 4
    assert meta["thread_id"] == str(_THREAD)
    assert meta["assistant_message_id"] == str(repo.completed_writes[0]["message_id"])
    assert meta["turn_run_id"] == str(repo.completed_writes[0]["turn_run_id"])
    assert meta["run_attempt"] == 1
    assert meta["usage_completeness"] == "complete"

    serialized = json.dumps(meta)
    for leaked in ("question", "Which aspect?", _SENTINEL_SECRET):
        assert leaked not in serialized


@pytest.mark.asyncio
async def test_price_multiplier_scales_computed_cost_points() -> None:
    """price_multiplier flows from the option billing config into
    computed_cost_points (audit-only)."""
    from app.services.ai_usage.billing import (
        DEFAULT_READER_ASK_BILLING_CONFIG,
    )

    recorder = _UsageRecorderSpy()

    async def _run(**kwargs):
        return _run_result_with_usage(
            usage_summary={"input_tokens": 1000, "output_tokens": 200, "total_tokens": 1200},
        )

    await _stream_with_run(_run, recorder=recorder)
    default_meta = recorder.calls[0]["event"].metadata_json
    # ceil((1000 * 1 + 200 * 5) * 1.0 / 1000) = 2
    assert default_meta["computed_cost_points"] == 2
    assert default_meta["price_multiplier"] == DEFAULT_READER_ASK_BILLING_CONFIG.price_multiplier


@pytest.mark.asyncio
async def test_failed_finalizer_status_with_usage_records_failed_event() -> None:
    """Provider usage happened, product terminal failed (context stale):
    the confirmed usage is still persisted with a failed usage event."""
    recorder = _UsageRecorderSpy()

    async def _run(**kwargs):
        return _run_result_with_usage(
            usage_summary={"input_tokens": 55, "output_tokens": 11, "total_tokens": 66},
            status="context_stale",
        )

    events, repo = await _stream_with_run(_run, recorder=recorder)
    assert "agentic.terminal" in [name for name, _ in events]

    assert len(recorder.calls) == 1
    assert recorder.calls[0]["event"].status == "failed"

    assert len(repo.terminal_writes) == 1
    write = repo.terminal_writes[0]
    assert write["usage_summary"] == {
        "input_tokens": 55,
        "output_tokens": 11,
        "total_tokens": 66,
    }
    assert write["usage_event_id"] == recorder.event_id


@pytest.mark.asyncio
async def test_unavailable_usage_records_no_event_and_null_columns() -> None:
    """No confirmed usage (agent raised / provider silent) → no usage
    event, usage_summary NULL, usage_event_id NULL."""
    recorder = _UsageRecorderSpy()

    async def _run(**kwargs):
        raise RuntimeError("agent run failed")

    events, repo = await _stream_with_run(_run, recorder=recorder)
    names = [name for name, _ in events]
    assert "agentic.terminal" in names
    assert "message.completed" not in names

    assert recorder.calls == []
    assert repo.terminal_writes[0]["usage_summary"] is None
    assert repo.terminal_writes[0]["usage_event_id"] is None


@pytest.mark.asyncio
async def test_recorder_failure_never_breaks_the_answer() -> None:
    """Recorder crash → answer still completes, usage summary persisted,
    usage_event_id NULL, zero extra provider calls."""
    recorder = _RaisingRecorder()
    provider_calls = {"n": 0}

    async def _run(**kwargs):
        provider_calls["n"] += 1
        return _run_result_with_usage(
            usage_summary={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )

    events, repo = await _stream_with_run(_run, recorder=recorder)
    names = [name for name, _ in events]
    assert "message.completed" in names
    assert provider_calls["n"] == 1

    write = repo.completed_writes[0]
    assert write["usage_summary"] == {
        "input_tokens": 10,
        "output_tokens": 5,
        "total_tokens": 15,
    }
    assert write["usage_event_id"] is None


@pytest.mark.asyncio
async def test_billed_points_stay_null_no_credit_mutation() -> None:
    """Audit-only billing: billed_points NULL and no credit-account
    functions are touched on the accounting path."""
    import app.services.reader_record_ask.production_stream as stream_mod

    recorder = _UsageRecorderSpy()

    async def _run(**kwargs):
        return _run_result_with_usage(
            usage_summary={"input_tokens": 100, "output_tokens": 40, "total_tokens": 140},
        )

    await _stream_with_run(_run, recorder=recorder)
    event = recorder.calls[0]["event"]
    assert event.billed_points is None
    assert event.token_budget_before is None
    assert event.token_budget_after is None

    # The production stream module must not import the settlement layer.
    assert not any(
        "credits" in name or "reserve" in name or "refund" in name
        for name in dir(stream_mod)
    )


# ---------------------------------------------------------------------------
# Section 4 — invocation-keyed idempotency (real PostgreSQL, isolated schema)
# ---------------------------------------------------------------------------

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
BASELINE_SQL = re.sub(
    r"^\s*SET search_path = public, pg_catalog;\s*$",
    "",
    (REPO_ROOT / "infra" / "migrations" / "0001_initial.sql").read_text(encoding="utf-8"),
    flags=re.MULTILINE,
)


def _database_url() -> str:
    import os

    return os.getenv(
        "DATABASE_URL",
        "postgresql://claread:claread_dev@127.0.0.1:5432/claread",
    )


@pytest.fixture
async def usage_pool() -> AsyncIterator[asyncpg.Pool]:
    schema_name = f"test_ask_usage_accounting_{uuid4().hex}"
    try:
        admin = await asyncpg.connect(_database_url())
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"PostgreSQL unavailable for ask usage accounting tests: {exc}")

    pool: asyncpg.Pool | None = None
    try:
        await admin.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin.execute(f'SET search_path TO "{schema_name}", public')
        await admin.execute(BASELINE_SQL)
        pool = await asyncpg.create_pool(
            _database_url(),
            min_size=1,
            max_size=4,
            init=init_connection,
            server_settings={"search_path": f'"{schema_name}", public'},
        )
        # Seed the user + reading record once — ai_usage_events and
        # reader_ask_* tables carry FKs to both.
        await pool.execute(
            "INSERT INTO users (id) VALUES ($1) ON CONFLICT DO NOTHING",
            _USER,
        )
        await pool.execute(
            """
            INSERT INTO reading_records (id, user_id, source_type, title)
            VALUES ($1, $2, 'markdown', 'usage accounting fixture')
            """,
            _RECORD,
            _USER,
        )
        yield pool
    finally:
        if pool is not None:
            await pool.close()
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin.close()


def _record_turn_usage(
    pool: asyncpg.Pool,
    *,
    turn_run_id: UUID,
    status: str = "succeeded",
    usage: dict[str, int] | None = None,
    run_attempt: int = 1,
):
    """Call the production usage-event recorder seam directly."""
    from app.services.reader_record_ask.production_stream import (
        record_ask_turn_usage_event,
    )

    if usage is None:
        usage = {"input_tokens": 100, "output_tokens": 40, "total_tokens": 140}

    async def _call() -> tuple[UUID | None, str]:
        return await record_ask_turn_usage_event(
            pool=pool,
            usage_summary=usage,
            user_id=_USER,
            reading_record_id=_RECORD,
            thread_id=_THREAD,
            message_id=UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"),
            turn_run_id=turn_run_id,
            run_attempt=run_attempt,
            final_status="ok" if status == "succeeded" else "failed",
            model_route="reader_ask",
            model_provider="dashscope",
            model_name="deepseek-v4-flash",
            model_profile="ask-main-deepseek-v4-flash",
            model_option_key="deepseek-v4-flash",
            price_multiplier=1.0,
            billing_policy_version="analysis_weighted_tokens_v1",
        )

    return _call()


async def test_terminal_replay_converges_to_single_usage_event(
    usage_pool: asyncpg.Pool,
) -> None:
    turn_run_id = uuid4()

    first_id, first_disposition = await _record_turn_usage(usage_pool, turn_run_id=turn_run_id)
    replay_id, replay_disposition = await _record_turn_usage(usage_pool, turn_run_id=turn_run_id)

    assert first_disposition == "inserted"
    assert replay_disposition == "replayed"
    assert first_id == replay_id

    rows = await usage_pool.fetch(
        "SELECT id FROM ai_usage_events WHERE invocation_key = $1",
        f"reader_ask:turn:{turn_run_id}",
    )
    assert len(rows) == 1


async def test_different_observation_same_key_fails_closed(
    usage_pool: asyncpg.Pool,
) -> None:
    turn_run_id = uuid4()

    first_id, first_disposition = await _record_turn_usage(usage_pool, turn_run_id=turn_run_id)
    conflict_id, conflict_disposition = await _record_turn_usage(
        usage_pool,
        turn_run_id=turn_run_id,
        usage={"input_tokens": 999, "output_tokens": 1, "total_tokens": 1000},
    )

    assert first_disposition == "inserted"
    assert conflict_disposition == "conflict"
    assert conflict_id == first_id

    row = await usage_pool.fetchrow(
        "SELECT input_tokens FROM ai_usage_events WHERE invocation_key = $1",
        f"reader_ask:turn:{turn_run_id}",
    )
    assert row["input_tokens"] == 100, "conflicting observation must not overwrite"


async def test_regenerate_new_turn_run_creates_independent_event(
    usage_pool: asyncpg.Pool,
) -> None:
    first_turn, second_turn = uuid4(), uuid4()

    first_id, _ = await _record_turn_usage(
        usage_pool, turn_run_id=first_turn, run_attempt=1
    )
    second_id, _ = await _record_turn_usage(
        usage_pool, turn_run_id=second_turn, run_attempt=2
    )

    assert first_id != second_id
    count = await usage_pool.fetchval(
        "SELECT COUNT(*) FROM ai_usage_events WHERE invocation_key LIKE 'reader_ask:turn:%'"
    )
    assert count == 2


async def test_usage_event_row_shape_and_totals(usage_pool: asyncpg.Pool) -> None:
    turn_run_id = uuid4()
    event_id, disposition = await _record_turn_usage(usage_pool, turn_run_id=turn_run_id)
    assert disposition == "inserted"

    row = await usage_pool.fetchrow(
        """
        SELECT usage_scope, capability_code, billing_mode, status,
               user_id, reading_record_id, request_id, invocation_key,
               model_route, model_provider, model_name, model_profile,
               input_tokens, output_tokens, total_tokens,
               billed_points, billing_policy_version, latency_ms, metadata_json
        FROM ai_usage_events WHERE id = $1
        """,
        event_id,
    )
    assert row["usage_scope"] == "user_billed"
    assert row["capability_code"] == "reader_ask"
    assert row["billing_mode"] == "user_points"
    assert row["status"] == "succeeded"
    assert row["user_id"] == _USER
    assert row["reading_record_id"] == _RECORD
    assert row["request_id"] == str(turn_run_id)
    assert row["invocation_key"] == f"reader_ask:turn:{turn_run_id}"
    assert row["model_route"] == "reader_ask"
    assert row["model_provider"] == "dashscope"
    assert row["model_name"] == "deepseek-v4-flash"
    assert row["model_profile"] == "ask-main-deepseek-v4-flash"
    assert row["input_tokens"] == 100
    assert row["output_tokens"] == 40
    assert row["total_tokens"] == 140
    assert row["billed_points"] is None
    assert row["billing_policy_version"] == "analysis_weighted_tokens_v1"
    # No provider latency semantics exist for the aggregate run — stays NULL.
    assert row["latency_ms"] is None

    meta = row["metadata_json"]
    assert meta["computed_cost_points"] == 1
    assert meta["price_multiplier"] == 1.0
    assert meta["usage_completeness"] == "complete"
    assert meta["thread_id"] == str(_THREAD)
    assert meta["turn_run_id"] == str(turn_run_id)
    assert meta["run_attempt"] == 1
    assert meta["usage_snapshot"]["input_tokens"] == 100


async def test_user_credit_tables_untouched_by_usage_accounting(
    usage_pool: asyncpg.Pool,
) -> None:
    """Recording a usage event writes zero rows into the credit ledger."""
    turn_run_id = uuid4()
    await _record_turn_usage(usage_pool, turn_run_id=turn_run_id)

    accounts = await usage_pool.fetchval("SELECT COUNT(*) FROM user_credit_accounts")
    ledger = await usage_pool.fetchval("SELECT COUNT(*) FROM user_credit_ledger")
    assert accounts == 0
    assert ledger == 0


# ---------------------------------------------------------------------------
# Section 5 — turn-run persistence + cold history projection
# ---------------------------------------------------------------------------


async def _seed_ask_thread_fixture(
    pool: asyncpg.Pool,
) -> tuple[UUID, UUID, UUID, UUID]:
    """Seed thread + user/assistant messages + one streaming turn_run
    (user + reading_record are seeded by the pool fixture). Returns
    (thread, user_msg, asst_msg, turn_run)."""
    thread_id = await pool.fetchval(
        """
        INSERT INTO reader_ask_threads (
            user_id, reading_record_id, title, is_default, created_at, updated_at
        )
        VALUES ($1, $2, 't', TRUE, NOW(), NOW())
        RETURNING id
        """,
        _USER,
        _RECORD,
    )
    user_msg = await pool.fetchval(
        """
        INSERT INTO reader_ask_messages (
            thread_id, role, status, content_md, created_at, updated_at
        )
        VALUES ($1, 'user', 'completed', 'q', NOW(), NOW())
        RETURNING id
        """,
        thread_id,
    )
    asst_msg = await pool.fetchval(
        """
        INSERT INTO reader_ask_messages (
            thread_id, role, status, content_md, created_at, updated_at
        )
        VALUES ($1, 'assistant', 'streaming', '', NOW(), NOW())
        RETURNING id
        """,
        thread_id,
    )
    turn_run_id = await pool.fetchval(
        """
        INSERT INTO reader_ask_turn_runs (
            message_id, thread_id, user_id, reading_record_id, base_id,
            generation, turn_id, status, execution_version,
            envelope_fingerprint, started_at, created_at, updated_at
        )
        VALUES (
            $1, $2, $3, $4, $5, 1, $6, 'streaming', $7,
            'fp', NOW(), NOW(), NOW()
        )
        RETURNING id
        """,
        asst_msg,
        thread_id,
        _USER,
        _RECORD,
        _BASE,
        user_msg,
        EXECUTION_VERSION_AGENTIC_V2,
    )
    # Claim the assistant message so the terminal CAS sees this run as
    # the owner (mirrors create_agentic_turn_run's claim step).
    await pool.execute(
        "UPDATE reader_ask_messages SET current_turn_run_id = $1 WHERE id = $2",
        turn_run_id,
        asst_msg,
    )
    return thread_id, user_msg, asst_msg, turn_run_id


async def _seed_usage_event_row(pool: asyncpg.Pool) -> UUID:
    """Insert a minimal ai_usage_events row (FK target for turn runs)."""
    return await pool.fetchval(
        """
        INSERT INTO ai_usage_events (
            usage_scope, capability_code, billing_mode, status, created_at
        )
        VALUES ('user_billed', 'reader_ask', 'user_points', 'succeeded', NOW())
        RETURNING id
        """
    )


async def test_repository_terminal_writes_persist_usage_columns(
    usage_pool: asyncpg.Pool,
) -> None:
    """complete_agentic_turn_run persists usage_summary_json and
    usage_event_id on the winning row."""
    from app.services.reader_record_ask.repository import ReaderRecordAskRepository

    _thread_id, _user_msg, asst_msg, turn_run_id = await _seed_ask_thread_fixture(
        usage_pool
    )
    event_id = await _seed_usage_event_row(usage_pool)

    repo = ReaderRecordAskRepository(pool=usage_pool)
    usage = {"input_tokens": 70, "output_tokens": 30, "total_tokens": 100}
    await repo.complete_agentic_turn_run(
        turn_run_id=turn_run_id,
        message_id=asst_msg,
        answer_text="done",
        completed_dto={"answer_text": "done"},
        resolved_evidence=[],
        final_status="ok",
        usage_summary=usage,
        usage_event_id=event_id,
    )

    row = await usage_pool.fetchrow(
        """
        SELECT status, final_status, usage_summary_json, usage_event_id
        FROM reader_ask_turn_runs WHERE id = $1
        """,
        turn_run_id,
    )
    assert row["status"] == "completed"
    assert row["usage_summary_json"] == usage
    assert row["usage_event_id"] == event_id

    # Cold history: the winning turn run projects its usage_event_id.
    message = await repo.get_message(message_id=asst_msg)
    assert message is not None
    assert message["current_turn_run"]["usage_event_id"] == str(event_id)


async def test_repository_terminal_failure_path_persists_usage_columns(
    usage_pool: asyncpg.Pool,
) -> None:
    from app.services.reader_record_ask.repository import ReaderRecordAskRepository

    _thread_id, _user_msg, asst_msg, turn_run_id = await _seed_ask_thread_fixture(
        usage_pool
    )
    event_id = await _seed_usage_event_row(usage_pool)

    repo = ReaderRecordAskRepository(pool=usage_pool)
    usage = {"input_tokens": 33, "output_tokens": 7, "total_tokens": 40}
    await repo.terminal_agentic_turn_run(
        turn_run_id=turn_run_id,
        message_id=asst_msg,
        run_status="failed",
        final_status="context_stale",
        terminal_reason="context_stale",
        terminal_dto=None,
        usage_summary=usage,
        usage_event_id=event_id,
    )

    row = await usage_pool.fetchrow(
        """
        SELECT status, final_status, usage_summary_json, usage_event_id
        FROM reader_ask_turn_runs WHERE id = $1
        """,
        turn_run_id,
    )
    assert row["final_status"] == "context_stale"
    assert row["usage_summary_json"] == usage
    assert row["usage_event_id"] == event_id


def test_usage_event_id_not_fabricated_when_missing() -> None:
    """Message-level usage_event_id stays None when the turn run has no
    event — cold history never invents linkage."""
    from app.services.reader_record_ask.repository import _message_row_to_history

    row = {
        "id": str(uuid4()),
        "thread_id": str(_THREAD),
        "role": "assistant",
        "status": "completed",
        "content_md": "answer",
        "context_anchors_json": [],
        "citations_json": [],
        "action_proposals_json": [],
        "tool_trace_json": [],
        "metadata_json": {},
        "message_current_turn_run_id": None,
        "usage_event_id": None,
        "created_at": None,
        "updated_at": None,
        "turn_run_id": None,
        "turn_run_user_id": None,
        "turn_run_reading_record_id": None,
        "turn_run_status": None,
        "turn_run_final_status": None,
        "turn_run_terminal_reason": None,
        "turn_run_execution_version": None,
        "user_visible_output_json": None,
        "turn_run_resolved_evidence_json": None,
        "turn_run_reasoning_projection_json": None,
        "turn_run_envelope_fingerprint": None,
        "turn_run_usage_event_id": None,
        "turn_run_started_at": None,
        "turn_run_completed_at": None,
        "turn_run_failed_at": None,
        "turn_run_created_at": None,
        "turn_run_updated_at": None,
    }
    message = _message_row_to_history(row)
    assert message["usage_event_id"] is None


# ---------------------------------------------------------------------------
# Section 6 — usage accounting repair: partial-usage retention on
# failed/cancelled runs, invocation-conflict no-link, typed final status.
# ---------------------------------------------------------------------------


def _round2_failing_function_model():
    """Tool-round model: round 1 confirms usage (30/10), round 2 raises."""

    def expand_evidence(pointer: str = "") -> str:
        del pointer
        return json.dumps({"status": "empty"})

    async def fn(messages, info):
        has_tool_return = any(
            type(p).__name__ == "ToolReturnPart"
            for m in messages
            for p in getattr(m, "parts", []) or []
        )
        if not has_tool_return:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="expand_evidence",
                        args=json.dumps({"pointer": ""}),
                        tool_call_id="tc1",
                    )
                ],
                usage=RunUsage(input_tokens=30, output_tokens=10),
            )
        raise RuntimeError("provider dropped before round 2 response")

    return FunctionModel(function=fn), expand_evidence


async def test_transport_failure_after_confirmed_round_retains_partial_usage() -> None:
    """Non-stream fallback: round-1 usage survives the round-2 failure on
    the shared RunUsage accumulator and is published to the observation."""

    from app.services.reader_record_ask.runtime_deps import RuntimeObservation
    from app.services.reader_record_ask.thinking_transport import (
        run_agent_with_thinking_transport,
    )

    model, expand_evidence = _round2_failing_function_model()
    agent = Agent(
        model,
        deps_type=ReaderRecordAskDeps,
        output_type=AgentAnswerDraftOutput,
        tools=[expand_evidence],
    )
    deps = _transport_deps()
    observation = RuntimeObservation()
    deps.observation = observation

    with pytest.raises(Exception):  # noqa: B017, PT011 - typed by transport contract
        await run_agent_with_thinking_transport(
            agent=agent,
            prompt="question",
            deps=deps,
            model=model,
        )

    assert observation.usage_summary == {
        "input_tokens": 30,
        "output_tokens": 10,
        "total_tokens": 40,
        "model_requests": 1,
        "tool_calls": 1,
    }
    assert observation.usage_completeness == "partial"


async def test_transport_stream_failure_after_confirmed_round_retains_partial_usage() -> None:
    """Stream entry: the same shared accumulator keeps round-1 confirmed
    output tokens when round 2 fails mid-run."""

    from pydantic_ai.models.function import DeltaToolCall

    from app.services.reader_record_ask.runtime_deps import RuntimeObservation
    from app.services.reader_record_ask.thinking_transport import (
        run_agent_with_thinking_transport,
    )

    def expand_evidence(pointer: str = "") -> str:
        del pointer
        return json.dumps({"status": "empty"})

    calls = {"n": 0}

    async def stream_fn(messages, info):
        calls["n"] += 1
        if calls["n"] == 1:
            yield "confirmed answer prefix "
            yield {
                0: DeltaToolCall(
                    name="expand_evidence",
                    json_args=json.dumps({"pointer": ""}),
                    tool_call_id="tc1",
                )
            }
            return
        raise RuntimeError("stream round 2 dropped")

    model = FunctionModel(stream_function=stream_fn)
    agent = Agent(
        model,
        deps_type=ReaderRecordAskDeps,
        output_type=AgentAnswerDraftOutput,
        tools=[expand_evidence],
    )
    deps = _transport_deps()
    observation = RuntimeObservation()
    deps.observation = observation

    with pytest.raises(Exception):  # noqa: B017, PT011
        await run_agent_with_thinking_transport(
            agent=agent,
            prompt="question",
            deps=deps,
            model=model,
        )

    assert observation.usage_summary is not None
    assert observation.usage_summary["output_tokens"] > 0
    assert observation.usage_completeness == "partial"


async def test_runtime_failure_propagates_partial_usage_on_observation() -> None:
    """run_reading_record_ask failures surface the confirmed partial usage
    through the caller-supplied RuntimeObservation."""

    from app.services.reader_record_ask.runtime import run_reading_record_ask
    from app.services.reader_record_ask.runtime_deps import RuntimeObservation

    model, _tool = _round2_failing_function_model()
    observation = RuntimeObservation()

    with pytest.raises(Exception):  # noqa: B017, PT011
        await run_reading_record_ask(
            user_message="question",
            envelope=_usage_envelope(),
            document_access=_usage_access(),
            model=model,
            observation=observation,
        )

    assert observation.usage_summary == {
        "input_tokens": 30,
        "output_tokens": 10,
        "total_tokens": 40,
        "model_requests": 1,
        "tool_calls": 1,
    }
    assert observation.usage_completeness == "partial"


async def test_failed_turn_after_confirmed_usage_records_partial_event() -> None:
    """Agent failure with confirmed partial usage: one usage event with
    completeness=partial, and the terminal write keeps summary + event."""

    recorder = _UsageRecorderSpy()

    async def _run(**kwargs):
        observation = kwargs["observation"]
        observation.usage_summary = {
            "input_tokens": 30,
            "output_tokens": 10,
            "total_tokens": 40,
        }
        observation.usage_completeness = "partial"
        raise RuntimeError("agent run failed after round 1")

    events, repo = await _stream_with_run(_run, recorder=recorder)
    names = [name for name, _ in events]
    assert "agentic.terminal" in names
    assert "message.completed" not in names

    assert len(recorder.calls) == 1
    event = recorder.calls[0]["event"]
    assert event.status == "failed"
    assert event.usage_data == {
        "input_tokens": 30,
        "output_tokens": 10,
        "total_tokens": 40,
    }
    assert event.metadata_json["usage_completeness"] == "partial"
    assert event.metadata_json["final_status"] == "failed"

    assert len(repo.terminal_writes) == 1
    write = repo.terminal_writes[0]
    assert write["usage_summary"] == {
        "input_tokens": 30,
        "output_tokens": 10,
        "total_tokens": 40,
    }
    assert write["usage_event_id"] == recorder.event_id


async def test_cancelled_turn_persists_confirmed_partial_usage() -> None:
    """Cancellation with confirmed partial usage: the cancelled terminal
    write keeps summary + event; usage event carries final_status=cancelled."""

    import asyncio as _asyncio

    recorder = _UsageRecorderSpy()

    async def _run(**kwargs):
        observation = kwargs["observation"]
        observation.usage_summary = {
            "input_tokens": 30,
            "output_tokens": 10,
            "total_tokens": 40,
        }
        observation.usage_completeness = "partial"
        raise _asyncio.CancelledError()

    repo = _FakeRepo()
    chunks: list[str] = []
    with pytest.raises(_asyncio.CancelledError):
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
            usage_event_recorder=recorder,
        ):
            chunks.append(c)
    events = _parse_sse(chunks)
    names = [name for name, _ in events]

    assert "message.completed" not in names
    assert repo.completed_writes == []
    terminal = next(d for n, d in events if n == "agentic.terminal")
    assert terminal["final_status"] == "cancelled"

    assert len(recorder.calls) == 1
    event = recorder.calls[0]["event"]
    assert event.status == "failed"
    assert event.metadata_json["final_status"] == "cancelled"
    assert event.metadata_json["usage_completeness"] == "partial"

    write = repo.terminal_writes[0]
    assert write["final_status"] == "cancelled"
    assert write["usage_summary"] == {
        "input_tokens": 30,
        "output_tokens": 10,
        "total_tokens": 40,
    }
    assert write["usage_event_id"] == recorder.event_id


async def test_usage_conflict_keeps_summary_without_event_link(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A conflict disposition must NOT link the stale event id to the fresh
    turn: summary persists, usage_event_id stays NULL, sanitized error logged."""

    import logging

    stale_event_id = uuid4()

    class _ConflictRecorder:
        async def __call__(self, event: Any, **kwargs: Any) -> tuple[UUID | None, str]:
            return stale_event_id, "conflict"

    recorder = _ConflictRecorder()

    async def _run(**kwargs):
        return _run_result_with_usage(
            usage_summary={"input_tokens": 100, "output_tokens": 40, "total_tokens": 140},
        )

    with caplog.at_level(logging.ERROR, logger="app.services.reader_record_ask.production_stream"):
        events, repo = await _stream_with_run(_run, recorder=recorder)
    names = [name for name, _ in events]
    assert "message.completed" in names

    assert len(repo.completed_writes) == 1
    write = repo.completed_writes[0]
    assert write["usage_summary"] == {
        "input_tokens": 100,
        "output_tokens": 40,
        "total_tokens": 140,
    }
    assert write["usage_event_id"] is None

    conflict_logs = [r for r in caplog.records if "usage_invocation_conflict" in r.getMessage()]
    assert conflict_logs, "expected a sanitized conflict error log"
    serialized = "\n".join(r.getMessage() for r in caplog.records)
    assert "input_tokens" not in serialized


@pytest.mark.parametrize("typed_status", ["context_stale", "invalid_citations"])
async def test_typed_terminal_status_reaches_usage_event(typed_status: str) -> None:
    """context_stale / invalid_citations keep their typed identity in the
    usage event metadata.final_status and error_code (status column stays
    the schema-conventional succeeded/failed binary)."""

    recorder = _UsageRecorderSpy()

    async def _run(**kwargs):
        return _run_result_with_usage(
            usage_summary={"input_tokens": 55, "output_tokens": 11, "total_tokens": 66},
            status=typed_status,
        )

    events, repo = await _stream_with_run(_run, recorder=recorder)
    assert "agentic.terminal" in [name for name, _ in events]

    assert len(recorder.calls) == 1
    event = recorder.calls[0]["event"]
    assert event.status == "failed"
    assert event.metadata_json["final_status"] == typed_status
    assert event.error_code == typed_status
    assert event.metadata_json["usage_completeness"] == "complete"

    write = repo.terminal_writes[0]
    assert write["final_status"] == typed_status
    assert write["usage_event_id"] == recorder.event_id


# ---------------------------------------------------------------------------
# Section 7 — non-budget ExceptionGroup routing + persist-failure outcome
# amendment.
# ---------------------------------------------------------------------------


async def test_nonbudget_exception_group_enters_failure_chain_with_usage() -> None:
    """A non-budget ExceptionGroup must collapse into the existing failure
    chain: typed terminal SSE, one failed partial usage event, terminal
    write with summary + event id — and the plain member exception must
    NOT escape to the caller."""

    recorder = _UsageRecorderSpy()
    provider_calls = {"n": 0}

    async def _run(**kwargs):
        provider_calls["n"] += 1
        observation = kwargs["observation"]
        observation.usage_summary = {
            "input_tokens": 30,
            "output_tokens": 10,
            "total_tokens": 40,
        }
        observation.usage_completeness = "partial"
        raise ExceptionGroup("tool path failures", [RuntimeError("tool executor crashed")])

    events, repo = await _stream_with_run(_run, recorder=recorder)
    names = [name for name, _ in events]
    assert "agentic.terminal" in names
    assert "message.completed" not in names
    assert provider_calls["n"] == 1

    terminal = next(d for n, d in events if n == "agentic.terminal")
    assert terminal["final_status"] == "failed"

    assert len(recorder.calls) == 1
    event = recorder.calls[0]["event"]
    assert event.status == "failed"
    assert event.usage_data == {
        "input_tokens": 30,
        "output_tokens": 10,
        "total_tokens": 40,
    }
    assert event.metadata_json["usage_completeness"] == "partial"
    assert event.metadata_json["final_status"] == "failed"

    assert len(repo.terminal_writes) == 1
    write = repo.terminal_writes[0]
    assert write["terminal_reason"] == "agent_run_failed"
    assert write["usage_summary"] == {
        "input_tokens": 30,
        "output_tokens": 10,
        "total_tokens": 40,
    }
    assert write["usage_event_id"] == recorder.event_id


async def test_base_exception_group_with_cancellation_is_not_swallowed() -> None:
    """Groups carrying non-Exception members (cancellation) must propagate
    untouched — never collapsed into a product terminal."""

    import asyncio as _asyncio

    async def _run(**kwargs):
        observation = kwargs["observation"]
        observation.usage_summary = {
            "input_tokens": 30,
            "output_tokens": 10,
            "total_tokens": 40,
        }
        observation.usage_completeness = "partial"
        raise BaseExceptionGroup("cancelled mid-run", [_asyncio.CancelledError()])

    repo = _FakeRepo()
    chunks: list[str] = []
    with pytest.raises(BaseExceptionGroup) as excinfo:
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
        ):
            chunks.append(c)

    assert not isinstance(excinfo.value, ExceptionGroup)
    events = _parse_sse(chunks)
    assert "message.completed" not in [name for name, _ in events]
    assert "agentic.terminal" not in [name for name, _ in events]
    assert repo.terminal_writes == []
    assert repo.completed_writes == []


async def test_persist_failure_amends_usage_event_outcome(monkeypatch) -> None:
    """Persist failure after an ok usage event: the SAME event is amended
    to failed/persist_failed via the shared outcome updater, and only a
    successful amendment keeps the event linked on the failed terminal."""

    import app.services.reader_record_ask.production_stream as production_stream_mod

    amend_calls: list[dict] = []

    async def _fake_updater(event_id, *, status, metadata_patch=None, error_code=None):
        amend_calls.append(
            {
                "event_id": event_id,
                "status": status,
                "metadata_patch": dict(metadata_patch or {}),
                "error_code": error_code,
            }
        )
        return True

    monkeypatch.setattr(
        production_stream_mod,
        "update_ai_usage_event_outcome",
        _fake_updater,
    )

    recorder = _UsageRecorderSpy()
    repo = _FakeRepo()
    repo.complete_should_fail = True

    async def _run(**kwargs):
        return _run_result_with_usage(
            usage_summary={"input_tokens": 100, "output_tokens": 40, "total_tokens": 140},
        )

    events, repo = await _stream_with_run(_run, repo=repo, recorder=recorder)
    names = [name for name, _ in events]
    assert "message.completed" not in names
    terminal = next(d for n, d in events if n == "agentic.terminal")
    assert terminal["final_status"] == "failed"
    assert terminal["terminal_reason"] == "persist_failed"

    # The event was created as ok BEFORE the persist attempt…
    assert len(recorder.calls) == 1
    assert recorder.calls[0]["event"].status == "succeeded"
    assert recorder.calls[0]["event"].metadata_json["final_status"] == "ok"

    # …and amended in place afterwards — exactly one outcome call.
    assert len(amend_calls) == 1
    amend = amend_calls[0]
    assert amend["event_id"] == recorder.event_id
    assert amend["status"] == "failed"
    assert amend["metadata_patch"] == {
        "final_status": "failed",
        "terminal_reason": "persist_failed",
    }
    assert amend["error_code"] == "persist_failed"

    # Successful amendment keeps the link; summary preserved.
    assert len(repo.terminal_writes) == 1
    write = repo.terminal_writes[0]
    assert write["usage_summary"] == {
        "input_tokens": 100,
        "output_tokens": 40,
        "total_tokens": 140,
    }
    assert write["usage_event_id"] == recorder.event_id


@pytest.mark.parametrize("updater_mode", ["returns_false", "raises"])
async def test_persist_failure_updater_fail_closed(monkeypatch, updater_mode: str) -> None:
    """When the outcome update fails (False or raises), the failed terminal
    keeps the usage summary but does NOT link the stale ok event."""

    import app.services.reader_record_ask.production_stream as production_stream_mod

    async def _failing_updater(event_id, *, status, metadata_patch=None, error_code=None):
        if updater_mode == "raises":
            raise RuntimeError("updater transport dropped")
        return False

    monkeypatch.setattr(
        production_stream_mod,
        "update_ai_usage_event_outcome",
        _failing_updater,
    )

    recorder = _UsageRecorderSpy()
    repo = _FakeRepo()
    repo.complete_should_fail = True

    async def _run(**kwargs):
        return _run_result_with_usage(
            usage_summary={"input_tokens": 100, "output_tokens": 40, "total_tokens": 140},
        )

    events, repo = await _stream_with_run(_run, repo=repo, recorder=recorder)
    names = [name for name, _ in events]
    assert "message.completed" not in names
    terminal = next(d for n, d in events if n == "agentic.terminal")
    assert terminal["final_status"] == "failed"
    assert terminal["terminal_reason"] == "persist_failed"

    # Event still created (ok) but never linked to the failed terminal.
    assert len(recorder.calls) == 1
    assert len(repo.terminal_writes) == 1
    write = repo.terminal_writes[0]
    assert write["usage_summary"] == {
        "input_tokens": 100,
        "output_tokens": 40,
        "total_tokens": 140,
    }
    assert write["usage_event_id"] is None


# ---------------------------------------------------------------------------
# Section 8 — reasoning observation facts (non-sensitive audit fields).
# ---------------------------------------------------------------------------

_RAW_REASONING_SENTINEL = "RAW-REASONING-SENTINEL-NEVER-LEAK-7f3"


def _thinking_execution_snapshot(**overrides: Any):
    from app.services.reader_record_ask.execution_config import (
        ReaderRecordAskExecutionSnapshot,
    )

    payload = dict(
        option_key="deepseek-v4-flash",
        provider="dashscope",
        model_name="deepseek-v4-flash",
        profile_name="ask-main-deepseek-v4-flash",
        adapter="openai_compatible",
        max_output_tokens=3200,
        max_turn_output_tokens=9600,
        max_input_tokens=24000,
        prompt_buffer_tokens=800,
        policy_version="reader_record_ask_execution_v2",
        budget_fingerprint="f" * 64,
        price_multiplier=1.0,
        billing_policy_version="analysis_weighted_tokens_v1",
        thinking_requested=True,
    )
    payload.update(overrides)
    return ReaderRecordAskExecutionSnapshot(**payload)


class _OutcomeObserver:
    """Duck-typed observer for deterministic outcome-mapping tests."""

    def __init__(
        self,
        *,
        has_content: bool,
        char_count: int,
        visibility_status: str,
    ) -> None:
        self._has_content = has_content
        self.projection_text = "x" * char_count
        self._visibility_status = visibility_status

    @property
    def has_content(self) -> bool:
        return self._has_content

    @property
    def visibility_status(self) -> str:
        return self._visibility_status


def test_reasoning_outcome_deterministic_mapping() -> None:
    """Six-outcome matrix: projection switch > request flag > observation."""
    from app.services.reader_record_ask.production_stream import (
        build_reasoning_observation,
    )

    def observe(
        *,
        requested: bool | None,
        enabled: bool,
        observer: _OutcomeObserver,
    ) -> dict[str, Any]:
        return build_reasoning_observation(
            reasoning_requested=requested,
            reasoning_projection_enabled=enabled,
            observer=observer,
        )

    empty = _OutcomeObserver(has_content=False, char_count=0, visibility_status="complete")
    complete = _OutcomeObserver(has_content=True, char_count=9, visibility_status="complete")
    truncated = _OutcomeObserver(has_content=True, char_count=99, visibility_status="truncated")
    blocked = _OutcomeObserver(has_content=True, char_count=5, visibility_status="blocked")

    # kill switch off dominates: host projection disabled.
    assert observe(requested=True, enabled=False, observer=complete)["reasoning_outcome"] == (
        "projection_disabled"
    )
    assert observe(requested=False, enabled=False, observer=empty)["reasoning_outcome"] == (
        "projection_disabled"
    )
    # requested flag next.
    assert observe(requested=False, enabled=True, observer=empty)["reasoning_outcome"] == (
        "not_requested"
    )
    assert observe(requested=None, enabled=True, observer=empty)["reasoning_outcome"] == (
        "provider_empty"
    )
    # observation last: empty → provider_empty, then buffer visibility.
    assert observe(requested=True, enabled=True, observer=empty)["reasoning_outcome"] == (
        "provider_empty"
    )
    assert observe(requested=True, enabled=True, observer=complete)["reasoning_outcome"] == (
        "complete"
    )
    assert observe(requested=True, enabled=True, observer=truncated)["reasoning_outcome"] == (
        "truncated"
    )
    assert observe(requested=True, enabled=True, observer=blocked)["reasoning_outcome"] == (
        "blocked"
    )

    # Auditable field shape (fixed keys, non-sensitive types only).
    facts = observe(requested=True, enabled=True, observer=truncated)
    assert set(facts) == {
        "reasoning_requested",
        "reasoning_projection_enabled",
        "reasoning_observed",
        "reasoning_outcome",
        "reasoning_char_count",
        "projection_policy_version",
    }
    assert facts["reasoning_observed"] is True
    assert facts["reasoning_char_count"] == 99
    assert facts["projection_policy_version"] == "provider_reasoning_v1"


async def test_usage_event_metadata_carries_reasoning_observation() -> None:
    """A successful turn with a thinking-requested snapshot records the
    six non-sensitive reasoning facts in the usage event metadata."""

    recorder = _UsageRecorderSpy()

    async def _run(**kwargs):
        return _run_result_with_usage(
            usage_summary={"input_tokens": 100, "output_tokens": 40, "total_tokens": 140},
        )

    events, repo = await _stream_with_run(
        _run,
        recorder=recorder,
        execution_snapshot=_thinking_execution_snapshot(),
    )
    assert "message.completed" in [name for name, _ in events]

    meta = recorder.calls[0]["event"].metadata_json
    assert meta["reasoning_requested"] is True
    assert meta["reasoning_projection_enabled"] is True
    assert meta["reasoning_observed"] is False
    assert meta["reasoning_outcome"] == "provider_empty"
    assert meta["reasoning_char_count"] == 0
    assert meta["projection_policy_version"] == "provider_reasoning_v1"

    serialized = json.dumps(meta)
    assert _RAW_REASONING_SENTINEL not in serialized


async def test_reasoning_observation_reflects_observed_content() -> None:
    """When the observer actually published reasoning, the metadata
    reports observed=True with the projected char count and outcome."""

    recorder = _UsageRecorderSpy()

    async def _run(**kwargs):
        observer = kwargs["thinking_observer"]
        observer.on_reasoning_delta("安全的推理内容。" * 3)
        observer.on_analysis_finished()
        return _run_result_with_usage(
            usage_summary={"input_tokens": 100, "output_tokens": 40, "total_tokens": 140},
        )

    events, repo = await _stream_with_run(
        _run,
        recorder=recorder,
        execution_snapshot=_thinking_execution_snapshot(),
    )
    assert "message.completed" in [name for name, _ in events]

    meta = recorder.calls[0]["event"].metadata_json
    assert meta["reasoning_observed"] is True
    assert meta["reasoning_outcome"] == "complete"
    assert meta["reasoning_char_count"] == len("安全的推理内容。" * 3)


async def test_reasoning_observation_logged_without_usage_event(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Provider reported no usage → no usage event, but the same
    non-sensitive observation survives in one fixed terminal log line."""

    import logging

    recorder = _UsageRecorderSpy()

    async def _run(**kwargs):
        raise RuntimeError("agent run failed before usage")

    with caplog.at_level(
        logging.INFO,
        logger="app.services.reader_record_ask.production_stream",
    ):
        events, repo = await _stream_with_run(
            _run,
            recorder=recorder,
            execution_snapshot=_thinking_execution_snapshot(),
        )

    assert recorder.calls == []
    observation_logs = [
        r
        for r in caplog.records
        if "reader_ask reasoning observation" in r.getMessage()
    ]
    assert len(observation_logs) == 1
    message = observation_logs[0].getMessage()
    assert "reasoning_requested=True" in message
    assert "reasoning_projection_enabled=True" in message
    assert "reasoning_observed=False" in message
    assert "reasoning_outcome=provider_empty" in message
    assert "reasoning_char_count=0" in message
    assert "projection_policy_version=provider_reasoning_v1" in message


async def test_reasoning_observation_zero_content_leak(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Raw reasoning text, secrets, connection strings and exception text
    never reach the observation metadata or logs."""

    import logging

    recorder = _UsageRecorderSpy()
    secret_reasoning = f"sk-LEAKKEY123456789 {_RAW_REASONING_SENTINEL} postgres://u:p@h/db"

    async def _run(**kwargs):
        observer = kwargs["thinking_observer"]
        observer.on_reasoning_delta(secret_reasoning)
        observer.on_analysis_finished()
        return _run_result_with_usage(
            usage_summary={"input_tokens": 100, "output_tokens": 40, "total_tokens": 140},
        )

    with caplog.at_level(
        logging.INFO,
        logger="app.services.reader_record_ask.production_stream",
    ):
        events, repo = await _stream_with_run(
            _run,
            recorder=recorder,
            execution_snapshot=_thinking_execution_snapshot(),
        )
    assert "message.completed" in [name for name, _ in events]

    serialized_meta = json.dumps(recorder.calls[0]["event"].metadata_json)
    serialized_logs = "\n".join(r.getMessage() for r in caplog.records)
    for surface in (serialized_meta, serialized_logs):
        assert _RAW_REASONING_SENTINEL not in surface
        assert "sk-LEAKKEY" not in surface
        assert "postgres://" not in surface


async def test_blocked_at_first_char_maps_to_blocked_outcome() -> None:
    """Hard-block at the very first character leaves the observer with
    ``has_content=False`` AND ``visibility_status='blocked'`` — the
    observation must report ``blocked``, not ``provider_empty`` (the
    redactor sealed before any content was emitted)."""

    from app.services.reader_record_ask.production_stream import (
        build_reasoning_observation,
    )
    from app.services.reader_record_ask.reasoning_projection import (
        ProviderReasoningObserver,
    )

    emitted: list[Any] = []
    observer = ProviderReasoningObserver(
        emit=emitted.append,
        message_id="m",
        thread_id="t",
        turn_run_id="r",
    )
    # Hard-block regex (Bearer credential) inside the very first delta:
    # the streaming redactor seals before releasing anything.
    observer.on_reasoning_delta("Bearer ABCDEFGHIJKLMNOP secret cargo")
    observer.on_analysis_finished()

    assert observer.has_content is False
    assert observer.visibility_status == "blocked"
    assert emitted == []

    facts = build_reasoning_observation(
        reasoning_requested=True,
        reasoning_projection_enabled=True,
        observer=observer,
    )
    assert facts["reasoning_outcome"] == "blocked"
    assert facts["reasoning_observed"] is False
    assert facts["reasoning_char_count"] == 0
