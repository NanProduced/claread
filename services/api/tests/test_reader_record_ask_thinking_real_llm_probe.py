"""Real-provider reasoning probes for the current production model options.

Each probe targets one published option from
``config/reader-ask-model-options.json`` (deepseek-v4-flash / qwen-max /
deepseek-pro). Probes are skip-by-default and run ONLY when the operator
opens the standard real_llm triple gate:

1. ``CLAREAD_ALLOW_REAL_LLM_TESTS=1``
2. ``CLAREAD_REAL_LLM_MODEL=<exact resolved model short name>``
3. pytest invoked with exactly ``-m real_llm``

(enforced by ``tests/conftest.py`` and re-checked inside each probe before
any model build, key read, or network request).

Privacy contract: reports carry provider/model/profile names, phase
order, request counts, delta/character counts, token totals and computed
cost points ONLY. Raw reasoning, prompts, answers, provider payloads,
credentials and exception texts are never printed or persisted.

Hard cost caps per probe: at most 2 provider requests, no retries, no
model fallback. Total output budget is 512 tokens, enforced as a
per-request ``max_tokens`` of 246 — Qwen documents up to 10 output
tokens beyond the ``max_completion_tokens`` setting, so the split absorbs
the tolerance: (246 + 10) × 2 = 512. (PydanticAI checks
``output_tokens_limit`` only AFTER a response arrives, so the per-request
split is the only hard guarantee.) The cumulative limit stays as an
after-the-fact backstop. Wire names differ by provider: DeepSeek's
official parameter is ``max_tokens`` (DirectDeepSeekChatModel converts
per request and never sends ``max_completion_tokens``); Qwen receives
``max_completion_tokens`` via the OpenAI-compatible default mapping.
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.models.function import (
    DeltaThinkingPart,
    DeltaToolCall,
    FunctionModel,
)
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import Tool
from pydantic_ai.usage import UsageLimits
from test_reader_record_ask_reasoning_wire_config import _runtime_profiles_json

from app.config.settings import Settings
from app.llm.call_guard import real_llm_tests_allowed
from app.observability import disabled_tracing
from app.services.ai_usage.billing import (
    DEFAULT_READER_ASK_BILLING_CONFIG,
    compute_reader_ask_cost_points,
)
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
from app.services.reader_record_ask.grounding_validator import (
    AgentAnswerDraftOutput,
)
from app.services.reader_record_ask.reasoning_projection import (
    ProviderReasoningObserver,
)
from app.services.reader_record_ask.runtime_deps import ReaderRecordAskDeps
from app.services.reader_record_ask.runtime_events import (
    AgenticReasoningDeltaEvent,
    AnswerDeltaEvent,
    AnswerPreviewResetEvent,
)
from app.services.reader_record_ask.thinking_transport import (
    run_agent_with_thinking_transport,
)

# The published production options this probe family covers. Update only
# when the public catalog changes; the offline contract tests in
# test_reader_record_ask_execution_config.py fail-closed on drift.
PROBE_OPTION_KEYS: tuple[str, ...] = (
    "deepseek-v4-flash",
    "qwen-max",
    "deepseek-pro",
)

# Process-env credentials the fixed three probe options resolve through.
# Catalog validation builds ALL enabled options for any probe, so each
# probe requires exactly this pair up front; missing keys fail closed
# before any resolver / model build / provider call (no .env fallback).
_PROBE_REQUIRED_KEY_ENVS: tuple[str, ...] = (
    "DEEPSEEK_API_KEY",
    "DASHSCOPE_API_KEY",
)

_PROBE_MAX_REQUESTS = 2
# Total output budget for one probe run. Enforced two ways:
# 1. per-request ``max_tokens`` — the ONLY hard guarantee (PydanticAI's
#    UsageLimits checks below fire only after a response arrives);
# 2. cumulative ``UsageLimits.output_tokens_limit`` as an after-the-fact
#    backstop, plus a report invariant assertion.
_PROBE_TOTAL_OUTPUT_TOKEN_BUDGET = 512
# Qwen (DashScope OpenAI-compatible) documents that actual output may
# exceed the ``max_completion_tokens`` setting by up to this many tokens.
# The per-request cap must absorb the tolerance: (246 + 10) × 2 = 512.
_PROBE_PROVIDER_OUTPUT_TOLERANCE = 10
_PROBE_PER_REQUEST_MAX_OUTPUT_TOKENS = (
    _PROBE_TOTAL_OUTPUT_TOKEN_BUDGET
    - _PROBE_PROVIDER_OUTPUT_TOLERANCE * _PROBE_MAX_REQUESTS
) // _PROBE_MAX_REQUESTS
# The transport only accepts the production structured output, so the
# probe prompt pins the minimal clarification JSON shape.
_PROBE_ANSWER_JSON = (
    '{"response_kind": "clarification", "clarification_text": "pong", '
    '"answer_blocks": []}'
)
_PROBE_PROMPT = (
    "Call the echo tool with the exact text 'ping'. Then reply ONLY with "
    "this JSON object: " + _PROBE_ANSWER_JSON
)
_REAL_LLM_MODEL_ENV = "CLAREAD_REAL_LLM_MODEL"

_API_ROOT = Path(__file__).resolve().parents[1]
_PORTABLE_OPTIONS_JSON = (
    _API_ROOT / "config" / "reader-ask-model-options.json"
).read_text(encoding="utf-8")


def _portable_settings() -> Settings:
    """Offline settings backed by the TRACKED example catalogs.

    Portable: no machine-local ``config/model-profiles.json`` (gitignored),
    no ``.env``, no local provider keys. The tracked
    ``model-profiles.example.json`` (notes stripped) reproduces the
    model-level prompted merge for qwen37-max with the provider reasoning
    fields intact.
    """
    return Settings(
        model_profiles_json=_runtime_profiles_json(),
        reader_ask_model_options_json=_PORTABLE_OPTIONS_JSON,
        _env_file=None,
    )


def _gate_reason() -> str | None:
    """Triple-gate check re-run inside each probe (defense in depth).

    The ``-m real_llm`` leg is enforced by ``tests/conftest.py``; this
    helper covers the two env legs before any model build or key read.
    """
    if not real_llm_tests_allowed():
        return (
            "real_llm probe; set CLAREAD_ALLOW_REAL_LLM_TESTS=1 and "
            f"{_REAL_LLM_MODEL_ENV}=<model> to enable"
        )
    if not os.environ.get(_REAL_LLM_MODEL_ENV):
        return f"real_llm probe; {_REAL_LLM_MODEL_ENV} must name the authorized model"
    return None


@dataclass
class ReasoningProbeReport:
    """Privacy-safe probe report — counts and identities only."""

    option_key: str
    provider: str
    model_name: str
    profile_name: str
    phases: list[str] = field(default_factory=list)
    provider_request_count: int = 0
    reasoning_delta_count: int = 0
    reasoning_char_count: int = 0
    answer_delta_char_count: int = 0
    canonical_answer_char_count: int = 0
    canonical_output_present: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    computed_cost_points: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "option_key": self.option_key,
            "provider": self.provider,
            "model": self.model_name,
            "profile": self.profile_name,
            "phases": list(self.phases),
            "provider_request_count": self.provider_request_count,
            "reasoning_delta_count": self.reasoning_delta_count,
            "reasoning_char_count": self.reasoning_char_count,
            "answer_delta_char_count": self.answer_delta_char_count,
            "canonical_answer_char_count": self.canonical_answer_char_count,
            "canonical_output_present": self.canonical_output_present,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "computed_cost_points": self.computed_cost_points,
        }


class _ProbeEventCollector:
    """Sink that records the production transport phase order."""

    def __init__(self) -> None:
        self.phases: list[str] = []
        self.reasoning_delta_count = 0
        self.reasoning_char_count = 0
        self.answer_delta_char_count = 0

    def __call__(self, event: Any) -> None:
        if isinstance(event, AgenticReasoningDeltaEvent):
            if "reasoning" not in self.phases:
                self.phases.append("reasoning")
            self.reasoning_delta_count += 1
            self.reasoning_char_count += len(event.delta)
        elif isinstance(event, AnswerPreviewResetEvent):
            if "tool_round" not in self.phases:
                self.phases.append("tool_round")
        elif isinstance(event, AnswerDeltaEvent):
            if "answer" not in self.phases:
                self.phases.append("answer")
            self.answer_delta_char_count += len(event.delta)


class _RequestCountingModel(WrapperModel):
    """Count provider requests on the wrapped model.

    Rides pydantic-ai's native ``WrapperModel`` — every call delegates to
    the resolver-built model untouched; only a counter is added. Works
    for both the streamed and non-streamed entry points.
    """

    def __init__(self, wrapped: Model) -> None:
        super().__init__(wrapped)
        self.request_count = 0

    async def request(self, messages, model_settings, model_request_parameters):
        self.request_count += 1
        return await super().request(messages, model_settings, model_request_parameters)

    @asynccontextmanager
    async def request_stream(
        self, messages, model_settings, model_request_parameters, run_context=None
    ):
        self.request_count += 1
        async with super().request_stream(
            messages, model_settings, model_request_parameters, run_context
        ) as stream:
            yield stream


def _probe_echo(text: str) -> str:
    """Minimal echo tool for the tool-round leg."""
    return text


_ECHO_TOOL = Tool(_probe_echo, name="echo")


def _probe_deps(event_sink: Any) -> ReaderRecordAskDeps:
    """Minimal in-memory deps for the transport (no DB, no article)."""
    user_id = "11111111-1111-1111-1111-111111111111"
    record_id = "22222222-2222-2222-2222-222222222222"
    base_id = "33333333-3333-3333-3333-333333333333"
    doc_id = "44444444-4444-4444-4444-444444444444"
    sha = "b" * 64
    envelope = build_context_envelope(
        VerifiedEnvelopeInput(
            user_id=user_id,  # type: ignore[arg-type]
            reading_record_id=record_id,  # type: ignore[arg-type]
            base_id=base_id,  # type: ignore[arg-type]
            record_generation=1,
            stable_document_id=doc_id,  # type: ignore[arg-type]
            base_content_sha256=sha,
            product_state="ready",
            readiness_state="ready",
        )
    )
    units = (
        ReadingUnitView(
            unit_id="u1",
            order_index=0,
            text="probe fixture",
            text_hash="11111111",
            base_start_utf16=0,
            base_end_utf16=13,
        ),
    )
    access = InMemoryDocumentAccess(
        snapshot=build_document_scope(
            reading_record_id=record_id,  # type: ignore[arg-type]
            base_id=base_id,  # type: ignore[arg-type]
            record_generation=1,
            stable_document_id=doc_id,  # type: ignore[arg-type]
            base_content_sha256=sha,
            units=units,
            segments=(),
        )
    )
    return ReaderRecordAskDeps(
        envelope=envelope,
        document_access=access,
        fence=StaticGenerationFence(live_generation=envelope.record_generation),
        evidence_registry=EvidenceRegistry(envelope_fingerprint=envelope.envelope_fingerprint),
        event_sink=event_sink,
    )


async def run_reasoning_probe(
    model: Model,
    *,
    option_key: str,
    provider: str,
    model_name: str,
    profile_name: str,
    price_multiplier: float = 1.0,
    billing_policy_version: str | None = None,
    base_model_settings: dict[str, Any] | None = None,
) -> ReasoningProbeReport:
    """Run one reasoning+tool+answer probe through the production transport.

    Works with any model instance (offline FunctionModel or a resolved
    production model). ``base_model_settings`` carries the RESOLVED
    production model settings (thinking wire params included); only
    ``max_tokens`` is overridden to the per-request probe cap, so a real
    run verifies the actual outbound request, not just the config file.
    Output budget: per-request ``max_tokens`` 246 with the cumulative
    ``UsageLimits.output_tokens_limit`` of 512 as an after-the-fact
    backstop (PydanticAI only checks the limit once a response arrives).
    No retries, no fallback. Tracing is disabled per-call via the
    repository's ``disabled_tracing`` context (no env mutation) and the
    agent runs with ``instrument=False`` — reasoning must never leave
    the process.
    """
    counting_model = _RequestCountingModel(model)
    collector = _ProbeEventCollector()
    deps = _probe_deps(collector)
    observer = ProviderReasoningObserver(
        emit=collector,
        message_id="probe-message",
        thread_id="probe-thread",
        turn_run_id="probe-turn",
    )
    agent: Agent[ReaderRecordAskDeps, AgentAnswerDraftOutput] = Agent(
        counting_model,
        deps_type=ReaderRecordAskDeps,
        output_type=AgentAnswerDraftOutput,
        tools=[_ECHO_TOOL],
        retries=0,
    )
    agent.instrument = False
    merged_settings: dict[str, Any] = dict(base_model_settings or {})
    merged_settings["max_tokens"] = _PROBE_PER_REQUEST_MAX_OUTPUT_TOKENS
    with disabled_tracing():
        # The probe owns the model instance it is running and is the sole
        # consumer for its lifetime, so enter the model's async lifecycle to
        # deterministically close the provider-owned httpx client (created by
        # the resolver/model factory) when the probe exits. Without this the
        # resolver-built provider keeps its connection pool open and an
        # abandoned httpcore ``PoolByteStream`` is finalized at loop teardown
        # (``aclose`` never awaited — the real-provider qwen warning this
        # repair addresses). Provider-less models (offline FunctionModel
        # probes) expose ``provider=None`` and this is a safe no-op.
        async with counting_model:
            outcome = await run_agent_with_thinking_transport(
                agent=agent,
                prompt=_PROBE_PROMPT,
                deps=deps,
                thinking_observer=observer,
                model=counting_model,
                model_settings=ModelSettings(merged_settings),
                usage_limits=UsageLimits(
                    request_limit=_PROBE_MAX_REQUESTS,
                    output_tokens_limit=_PROBE_TOTAL_OUTPUT_TOKEN_BUDGET,
                ),
            )
    usage = outcome.usage_summary or {}
    billing_config = DEFAULT_READER_ASK_BILLING_CONFIG
    if price_multiplier != billing_config.price_multiplier or billing_policy_version:
        updates: dict[str, Any] = {"price_multiplier": price_multiplier}
        if billing_policy_version:
            updates["billing_policy_version"] = billing_policy_version
        billing_config = billing_config.model_copy(update=updates)
    canonical_output = outcome.output
    canonical_answer_text = canonical_output.clarification_text or "\n\n".join(
        block.text for block in canonical_output.answer_blocks
    )
    return ReasoningProbeReport(
        option_key=option_key,
        provider=provider,
        model_name=model_name,
        profile_name=profile_name,
        phases=collector.phases,
        provider_request_count=counting_model.request_count,
        reasoning_delta_count=collector.reasoning_delta_count,
        reasoning_char_count=collector.reasoning_char_count,
        answer_delta_char_count=collector.answer_delta_char_count,
        canonical_answer_char_count=len(canonical_answer_text),
        canonical_output_present=isinstance(
            canonical_output, AgentAnswerDraftOutput
        ),
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        total_tokens=int(usage.get("total_tokens") or 0),
        computed_cost_points=compute_reader_ask_cost_points(usage or None, billing_config),
    )


def _assert_probe_invariants(report: ReasoningProbeReport) -> None:
    """Privacy-safe pass criteria shared by every probe run."""
    assert report.reasoning_delta_count > 0, "no reasoning delta observed"
    assert report.reasoning_char_count > 0, "no reasoning characters observed"
    assert "tool_round" in report.phases, "tool round did not continue"
    assert report.phases.index("reasoning") < report.phases.index("tool_round")
    # Answer checks come BEFORE the phase-order comparison so a run that
    # produced no streamed answer fails with the answer-delta assertion
    # (never a bare ValueError from phases.index on a missing phase).
    assert report.answer_delta_char_count > 0, "no streamed answer delta observed"
    assert "answer" in report.phases, "answer phase did not occur"
    assert report.phases.index("tool_round") < report.phases.index("answer")
    assert report.canonical_output_present, "canonical final output missing"
    assert report.canonical_answer_char_count > 0, (
        "no canonical answer characters observed"
    )
    assert report.provider_request_count <= _PROBE_MAX_REQUESTS
    assert report.input_tokens > 0 and report.output_tokens > 0
    assert report.output_tokens <= _PROBE_TOTAL_OUTPUT_TOKEN_BUDGET, (
        "probe exceeded its total output budget"
    )
    assert report.total_tokens >= report.input_tokens + report.output_tokens - 1
    assert report.computed_cost_points >= 0


async def _run_real_probe(
    option_key: str,
    *,
    settings: Settings | None = None,
) -> None:
    """Real-provider probe body — all gates checked before any build.

    ``settings`` is a test seam: offline machinery tests inject a portable
    settings built from the tracked example catalogs; the real run defaults
    to the machine-local production registry (``config/model-profiles.json``)
    with ``_env_file=None`` so the owner's keys come only from the process
    environment.
    """
    reason = _gate_reason()
    if reason is not None:
        pytest.skip(reason)

    # Credential preflight BEFORE any Settings / resolver / model build:
    # the owner's keys must be in the process environment; a missing key
    # fails closed with a fixed message and never falls back to .env.
    for key_env in _PROBE_REQUIRED_KEY_ENVS:
        if not os.environ.get(key_env):
            pytest.fail(
                f"probe requires {key_env} in the process environment; "
                "refusing provider call"
            )

    from app.llm.router import resolve_model_config
    from app.services.reader_record_ask.execution_config import (
        resolve_reader_record_ask_execution,
    )
    from app.services.reader_record_ask.model_options import (
        resolve_reader_ask_model_option,
    )

    if settings is None:
        settings = Settings(
            model_profiles_json="config/model-profiles.json",
            reader_ask_model_options_json="config/reader-ask-model-options.json",
            _env_file=None,
        )
    expected_model = os.environ[_REAL_LLM_MODEL_ENV]
    option = resolve_reader_ask_model_option(settings, option_key, strict=True)

    # Model-name identity check BEFORE any model build or key access. With
    # the triple gate open, a mismatch is a hard failure (fixed,
    # desensitised message) — never a silent skip.
    config = resolve_model_config(settings, "reader_ask", option.selection)
    resolved_name = config.model_name if config is not None else None
    if resolved_name != expected_model:
        pytest.fail("probe model identity mismatch; refusing provider call")
    if config is None or not config.model_settings or not config.model_settings.thinking_enabled():
        pytest.fail(
            "probe profile does not request provider thinking — "
            "production thinking contract violated before any provider call"
        )
    # Qwen fail-closed preflight: prompted structured output must be the
    # resolved mode BEFORE the execution resolver, model build, or any
    # provider request. Fixed, desensitised message only.
    if option_key == "qwen-max":
        resolved_profile = config.openai_profile
        if (
            resolved_profile is None
            or resolved_profile.default_structured_output_mode != "prompted"
        ):
            pytest.fail(
                "qwen-max structured output mode is not prompted; "
                "refusing provider call"
            )

    execution = resolve_reader_record_ask_execution(option, settings=settings)
    snapshot = execution.snapshot
    assert snapshot is not None

    # Tracing isolation is per-call inside run_reasoning_probe
    # (disabled_tracing context + instrument=False); the process
    # environment is never touched.
    try:
        report = await run_reasoning_probe(
            execution.model,
            option_key=option_key,
            provider=snapshot.provider,
            model_name=snapshot.model_name,
            profile_name=snapshot.profile_name,
            price_multiplier=snapshot.price_multiplier,
            billing_policy_version=snapshot.billing_policy_version,
            base_model_settings=dict(execution.model_settings() or {}),
        )
    except BaseException as exc:  # noqa: BLE001 - fixed error category only
        pytest.fail(
            f"probe provider error category={type(exc).__name__} "
            f"option={option_key} model={snapshot.model_name}"
        )

    # Safe report FIRST so a failing invariant still surfaces the
    # counts (privacy-safe fields only — never raw content).
    print(json.dumps(report.to_dict(), ensure_ascii=False))
    _assert_probe_invariants(report)


@pytest.mark.asyncio
@pytest.mark.real_llm
async def test_probe_deepseek_v4_flash_thinking_tool_echo() -> None:
    """Production option deepseek-v4-flash: reasoning → tool → answer."""
    await _run_real_probe("deepseek-v4-flash")


@pytest.mark.asyncio
@pytest.mark.real_llm
async def test_probe_qwen_max_thinking_tool_echo() -> None:
    """Production option qwen-max: reasoning → tool → answer."""
    await _run_real_probe("qwen-max")


@pytest.mark.asyncio
@pytest.mark.real_llm
async def test_probe_deepseek_pro_thinking_tool_echo() -> None:
    """Production option deepseek-pro: reasoning → tool → answer."""
    await _run_real_probe("deepseek-pro")


# ---------------------------------------------------------------------------
# Offline validation of the probe machinery (no real_llm marker, no network).
# ---------------------------------------------------------------------------

_RAW_SENTINEL = "RAW-REASONING-SENTINEL-NEVER-LEAK-4e1"


def _offline_probe_model() -> FunctionModel:
    """FunctionModel: reasoning delta → tool call → round-2 answer JSON."""

    async def stream_fn(messages, info):
        has_tool_return = any(
            type(p).__name__ == "ToolReturnPart"
            for m in messages
            for p in getattr(m, "parts", []) or []
        )
        if not has_tool_return:
            yield {
                0: DeltaThinkingPart(content=f"thinking about the echo {_RAW_SENTINEL}")
            }
            yield {
                1: DeltaToolCall(
                    name="echo",
                    json_args=json.dumps({"text": "ping"}),
                    tool_call_id="tc1",
                )
            }
            return
        yield _PROBE_ANSWER_JSON

    return FunctionModel(stream_function=stream_fn)


@pytest.mark.asyncio
async def test_probe_collector_offline_function_model() -> None:
    """The probe collector/report machinery works end-to-end offline."""
    report = await run_reasoning_probe(
        _offline_probe_model(),
        option_key="offline-function",
        provider="function",
        model_name="offline-function-model",
        profile_name="offline-profile",
    )

    assert report.phases == ["reasoning", "tool_round", "answer"]
    assert report.provider_request_count == 2
    assert report.reasoning_delta_count >= 1
    assert report.reasoning_char_count > 0
    assert report.answer_delta_char_count == len("pong")
    assert report.canonical_output_present is True
    assert report.canonical_answer_char_count == len("pong")
    assert report.input_tokens > 0
    assert report.output_tokens > 0
    assert report.total_tokens == report.input_tokens + report.output_tokens
    assert report.computed_cost_points >= 1

    serialized = json.dumps(report.to_dict())
    assert _RAW_SENTINEL not in serialized
    assert "ping" not in serialized


@pytest.mark.asyncio
async def test_probe_observer_produces_persistence_payload_offline() -> None:
    """The probe observer yields a validated persistence payload whose
    text is the redacted projection — secret patterns never survive."""
    collector = _ProbeEventCollector()
    observer = ProviderReasoningObserver(
        emit=collector,
        message_id="m",
        thread_id="t",
        turn_run_id="r",
    )
    observer.on_reasoning_delta("safe reasoning sk-LEAKKEY1234567890 tail")
    observer.on_analysis_finished()

    payload = observer.persistence_payload()
    assert payload is not None
    assert payload["projection_policy_version"] == "provider_reasoning_v1"
    assert payload["char_count"] == len(payload["text"])
    assert payload["text"] == observer.projection_text
    assert "sk-LEAKKEY" not in json.dumps(payload)


def test_probe_report_contains_only_safe_fields() -> None:
    """The report surface is exactly the fixed safe field set."""
    report = ReasoningProbeReport(
        option_key="k",
        provider="p",
        model_name="m",
        profile_name="f",
    )
    assert set(report.to_dict()) == {
        "option_key",
        "provider",
        "model",
        "profile",
        "phases",
        "provider_request_count",
        "reasoning_delta_count",
        "reasoning_char_count",
        "answer_delta_char_count",
        "canonical_answer_char_count",
        "canonical_output_present",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "computed_cost_points",
    }


@pytest.mark.asyncio
async def test_real_probe_skips_before_any_model_build_when_gate_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate closed → the probe entry skips before touching the resolver."""
    monkeypatch.delenv("CLAREAD_ALLOW_REAL_LLM_TESTS", raising=False)
    monkeypatch.delenv("CLAREAD_REAL_LLM_MODEL", raising=False)

    import app.services.reader_record_ask.model_options as model_options_mod

    def _must_not_build(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("model option resolver must not run with the gate closed")

    monkeypatch.setattr(
        model_options_mod,
        "resolve_reader_ask_model_option",
        _must_not_build,
    )

    with pytest.raises(pytest.skip.Exception):
        await _run_real_probe("deepseek-v4-flash")


@pytest.mark.asyncio
async def test_real_probe_fails_closed_on_model_identity_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the triple gate open, a CLAREAD_REAL_LLM_MODEL naming another
    model FAILS CLOSED with a fixed, desensitised message before any model
    build — never a silent skip. Zero .env fallback reads along the way."""
    monkeypatch.setenv("CLAREAD_ALLOW_REAL_LLM_TESTS", "1")
    monkeypatch.setenv("CLAREAD_REAL_LLM_MODEL", "some-other-model")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only-not-a-secret")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-only-not-a-secret")

    import app.config.settings as settings_mod
    import app.services.reader_record_ask.execution_config as execution_config_mod

    fallback_calls = {"count": 0}

    def _spy_local_env() -> dict:
        fallback_calls["count"] += 1
        return {}

    monkeypatch.setattr(settings_mod, "_load_local_env_values", _spy_local_env)

    def _must_not_build(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("model must not be built when names mismatch")

    monkeypatch.setattr(
        execution_config_mod,
        "resolve_reader_record_ask_execution",
        _must_not_build,
    )

    with pytest.raises(pytest.fail.Exception, match="model identity mismatch"):
        await _run_real_probe("deepseek-v4-flash", settings=_portable_settings())

    assert fallback_calls["count"] == 0


@pytest.mark.asyncio
async def test_real_probe_qwen_fails_closed_before_any_build_when_not_prompted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A qwen-max config whose resolved structured-output mode is not
    ``prompted`` must fail closed BEFORE the execution resolver, the model
    factory, or any provider request — with a fixed, desensitised message
    and zero calls on every downstream boundary (zero .env fallback too)."""
    monkeypatch.setenv("CLAREAD_ALLOW_REAL_LLM_TESTS", "1")
    monkeypatch.setenv("CLAREAD_REAL_LLM_MODEL", "fake-qwen-model")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only-not-a-secret")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-only-not-a-secret")

    import app.config.settings as settings_mod
    import app.llm.router as router_mod
    import app.services.reader_record_ask.execution_config as exec_mod
    import app.services.reader_record_ask.model_options as mo_mod
    from app.llm.types import (
        OpenAIProfileConfig,
        ResolvedModelConfig,
        RunModelSettings,
    )

    fallback_calls = {"count": 0}

    def _spy_local_env() -> dict:
        fallback_calls["count"] += 1
        return {}

    monkeypatch.setattr(settings_mod, "_load_local_env_values", _spy_local_env)

    def _fake_option(*args: Any, **kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(selection=None)

    def _wrong_mode_config(*args: Any, **kwargs: Any) -> ResolvedModelConfig:
        return ResolvedModelConfig(
            route="reader_ask",
            profile_name="ask-main-qwen37-max",
            provider="dashscope_compat",
            adapter="openai_compatible",
            model_name="fake-qwen-model",
            base_url="https://example.invalid/v1",
            model_settings=RunModelSettings(extra_body={"enable_thinking": True}),
            openai_profile=OpenAIProfileConfig(
                default_structured_output_mode="json_schema"
            ),
        )

    monkeypatch.setattr(mo_mod, "resolve_reader_ask_model_option", _fake_option)
    monkeypatch.setattr(router_mod, "resolve_model_config", _wrong_mode_config)

    calls = {"execution_resolver": 0, "model_factory": 0, "run_reasoning_probe": 0}

    def _must_not_execute(*args: Any, **kwargs: Any) -> None:
        calls["execution_resolver"] += 1
        raise AssertionError("execution resolver must not run on wrong mode")

    def _must_not_build_model(*args: Any, **kwargs: Any) -> None:
        calls["model_factory"] += 1
        raise AssertionError("model factory must not run on wrong mode")

    def _must_not_probe(*args: Any, **kwargs: Any) -> None:
        calls["run_reasoning_probe"] += 1
        raise AssertionError("probe must not run on wrong mode")

    monkeypatch.setattr(
        exec_mod, "resolve_reader_record_ask_execution", _must_not_execute
    )
    monkeypatch.setattr(router_mod, "build_model_for_route", _must_not_build_model)

    import test_reader_record_ask_thinking_real_llm_probe as probe_mod

    monkeypatch.setattr(probe_mod, "run_reasoning_probe", _must_not_probe)

    with pytest.raises(pytest.fail.Exception, match="refusing provider call"):
        await _run_real_probe("qwen-max")

    assert calls["execution_resolver"] == 0
    assert calls["model_factory"] == 0
    assert calls["run_reasoning_probe"] == 0
    assert fallback_calls["count"] == 0


@pytest.mark.asyncio
async def test_real_probe_fails_closed_before_any_resolver_when_key_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the triple gate open but the fixed option's process credential
    absent, the probe fails closed with a fixed message BEFORE the option
    resolver / model factory / any .env fallback read."""
    monkeypatch.setenv("CLAREAD_ALLOW_REAL_LLM_TESTS", "1")
    monkeypatch.setenv("CLAREAD_REAL_LLM_MODEL", "fake-qwen-model")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    import test_reader_record_ask_thinking_real_llm_probe as probe_mod

    import app.config.settings as settings_mod
    import app.llm.router as router_mod
    import app.services.reader_record_ask.model_options as mo_mod

    fallback_calls = {"count": 0}

    def _spy_local_env() -> dict:
        fallback_calls["count"] += 1
        return {}

    monkeypatch.setattr(settings_mod, "_load_local_env_values", _spy_local_env)

    def _must_not_resolve(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("resolver must not run when the key is missing")

    monkeypatch.setattr(mo_mod, "resolve_reader_ask_model_option", _must_not_resolve)
    monkeypatch.setattr(router_mod, "resolve_model_config", _must_not_resolve)
    monkeypatch.setattr(probe_mod, "run_reasoning_probe", _must_not_resolve)

    with pytest.raises(pytest.fail.Exception, match="process environment"):
        await _run_real_probe("qwen-max")

    assert fallback_calls["count"] == 0


def test_probe_option_keys_match_published_catalog() -> None:
    """The probe family covers exactly the published production options."""
    catalog = json.loads(
        (_API_ROOT / "config" / "reader-ask-model-options.json").read_text(encoding="utf-8")
    )
    enabled = {key for key, opt in catalog["options"].items() if opt.get("enabled", True)}
    assert set(PROBE_OPTION_KEYS) == enabled


# ---------------------------------------------------------------------------
# Probe tooling contract: production settings, tracing isolation, wrapper.
# ---------------------------------------------------------------------------


class _SettingsCapturingModel(WrapperModel):
    """Capture the outbound ModelSettings on every provider request."""

    def __init__(self, wrapped: Model) -> None:
        super().__init__(wrapped)
        self.captured_settings: list[dict[str, Any] | None] = []

    async def request(self, messages, model_settings, model_request_parameters):
        self.captured_settings.append(dict(model_settings) if model_settings else None)
        return await super().request(messages, model_settings, model_request_parameters)

    @asynccontextmanager
    async def request_stream(
        self, messages, model_settings, model_request_parameters, run_context=None
    ):
        self.captured_settings.append(dict(model_settings) if model_settings else None)
        async with super().request_stream(
            messages, model_settings, model_request_parameters, run_context
        ) as stream:
            yield stream


@pytest.mark.asyncio
async def test_probe_forwards_resolved_model_settings_with_max_tokens_override() -> None:
    """The probe must send the resolved production model settings (thinking
    wire params) with ONLY ``max_tokens`` overridden to the probe cap —
    otherwise a real run would verify config-file claims, not the actual
    outbound request."""

    capturing = _SettingsCapturingModel(_offline_probe_model())
    report = await run_reasoning_probe(
        capturing,
        option_key="offline",
        provider="function",
        model_name="offline-function-model",
        profile_name="offline-profile",
        base_model_settings={
            "extra_body": {"thinking": {"type": "enabled"}},
            "temperature": 0.7,
        },
    )

    assert report.provider_request_count == 2
    assert capturing.captured_settings, "no provider request captured"
    for settings in capturing.captured_settings:
        assert settings is not None
        assert settings["max_tokens"] == _PROBE_PER_REQUEST_MAX_OUTPUT_TOKENS
        assert settings["extra_body"] == {"thinking": {"type": "enabled"}}
        assert settings["temperature"] == 0.7


@pytest.mark.asyncio
async def test_probe_default_settings_still_cap_max_tokens() -> None:
    """Without base settings the probe still sends the per-request
    max-token cap (and nothing else is required)."""
    capturing = _SettingsCapturingModel(_offline_probe_model())
    await run_reasoning_probe(
        capturing,
        option_key="offline",
        provider="function",
        model_name="offline-function-model",
        profile_name="offline-profile",
    )
    for settings in capturing.captured_settings:
        assert settings is not None
        assert settings["max_tokens"] == _PROBE_PER_REQUEST_MAX_OUTPUT_TOKENS


def test_probe_output_budget_constants_are_consistent() -> None:
    """Per-request cap × request cap + provider tolerance must fit the
    total output budget: PydanticAI only enforces ``output_tokens_limit``
    AFTER a response arrives, so the per-request ``max_tokens`` split is
    the only hard guarantee — and Qwen may exceed the setting by up to
    its documented tolerance, which the split must absorb."""
    assert _PROBE_TOTAL_OUTPUT_TOKEN_BUDGET == 512
    assert _PROBE_PER_REQUEST_MAX_OUTPUT_TOKENS == 246
    assert _PROBE_PROVIDER_OUTPUT_TOLERANCE == 10
    assert (
        (_PROBE_PER_REQUEST_MAX_OUTPUT_TOKENS + _PROBE_PROVIDER_OUTPUT_TOLERANCE)
        * _PROBE_MAX_REQUESTS
        <= _PROBE_TOTAL_OUTPUT_TOKEN_BUDGET
    )


def test_probe_invariants_reject_output_over_budget() -> None:
    """A report whose provider-reported output tokens exceed the total
    budget fails the probe invariants (cumulative after-the-fact guard)."""
    over_budget = ReasoningProbeReport(
        option_key="k",
        provider="p",
        model_name="m",
        profile_name="f",
        phases=["reasoning", "tool_round", "answer"],
        provider_request_count=2,
        reasoning_delta_count=1,
        reasoning_char_count=10,
        answer_delta_char_count=4,
        canonical_answer_char_count=4,
        canonical_output_present=True,
        input_tokens=50,
        output_tokens=_PROBE_TOTAL_OUTPUT_TOKEN_BUDGET + 1,
        total_tokens=50 + _PROBE_TOTAL_OUTPUT_TOKEN_BUDGET + 1,
        computed_cost_points=1,
    )
    with pytest.raises(AssertionError, match="output budget"):
        _assert_probe_invariants(over_budget)


def test_probe_invariants_no_streamed_answer_raises_delta_error() -> None:
    """A run with reasoning + tool phases but zero streamed answer deltas
    must fail the answer-delta assertion (AssertionError with the delta
    message) — never a ValueError from ``phases.index("answer")``."""
    no_answer = ReasoningProbeReport(
        option_key="k",
        provider="p",
        model_name="m",
        profile_name="f",
        phases=["reasoning", "tool_round"],
        provider_request_count=1,
        reasoning_delta_count=1,
        reasoning_char_count=10,
        answer_delta_char_count=0,
        canonical_answer_char_count=4,
        canonical_output_present=True,
        input_tokens=10,
        output_tokens=10,
        total_tokens=20,
        computed_cost_points=1,
    )
    with pytest.raises(AssertionError, match="no streamed answer delta observed"):
        _assert_probe_invariants(no_answer)


@pytest.mark.asyncio
async def test_probe_leaves_environment_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The probe never mutates process env (no LANGSMITH_* writes, no key
    deletion): the environment before and after a probe run is identical."""
    monkeypatch.setenv("LANGSMITH_ENABLED", "sentinel-value")
    monkeypatch.setenv("LANGSMITH_API_KEY", "sentinel-key")

    before = dict(os.environ)
    await run_reasoning_probe(
        _offline_probe_model(),
        option_key="offline",
        provider="function",
        model_name="offline-function-model",
        profile_name="offline-profile",
    )
    after = dict(os.environ)

    assert after == before
    assert os.environ["LANGSMITH_ENABLED"] == "sentinel-value"
    assert os.environ["LANGSMITH_API_KEY"] == "sentinel-key"


@pytest.mark.asyncio
async def test_probe_uses_disabled_tracing_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tracing isolation goes through the repository's concurrent-safe
    ``app.observability.disabled_tracing`` — never process env mutation."""

    import test_reader_record_ask_thinking_real_llm_probe as probe_mod

    entered = {"count": 0}

    @contextmanager
    def _spy_disabled_tracing():
        entered["count"] += 1
        yield

    monkeypatch.setattr(probe_mod, "disabled_tracing", _spy_disabled_tracing)

    await run_reasoning_probe(
        _offline_probe_model(),
        option_key="offline",
        provider="function",
        model_name="offline-function-model",
        profile_name="offline-profile",
    )
    assert entered["count"] >= 1, "probe must wrap its run in disabled_tracing()"


@pytest.mark.asyncio
async def test_probe_agent_runs_with_instrument_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The probe agent must run with ``instrument=False`` so no OTEL/
    instrumentation path can carry reasoning out of the process."""

    import test_reader_record_ask_thinking_real_llm_probe as probe_mod

    created: list[Any] = []

    class _SpyAgent(probe_mod.Agent):  # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            created.append(self)

    monkeypatch.setattr(probe_mod, "Agent", _SpyAgent)

    await run_reasoning_probe(
        _offline_probe_model(),
        option_key="offline",
        provider="function",
        model_name="offline-function-model",
        profile_name="offline-profile",
    )
    assert created, "probe must construct its agent through the module-level Agent"
    assert created[0].instrument is False


def test_request_counter_uses_wrapper_model() -> None:
    """Request counting rides on pydantic-ai's native WrapperModel — no
    dynamic subclassing, no __dict__ copying."""

    counting_model = _RequestCountingModel(_offline_probe_model())
    assert isinstance(counting_model, WrapperModel)
    assert counting_model.request_count == 0
    assert isinstance(counting_model.wrapped, FunctionModel)


# ---------------------------------------------------------------------------
# Provider wire-name captures (offline HTTP, MockTransport pattern).
# ---------------------------------------------------------------------------


class _WireCaptureTransport:
    """httpx transport recording request JSON without network access."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    async def handle_async_request(self, request: Any) -> Any:
        import json as _json

        import httpx

        body = request.content.decode("utf-8") if request.content else ""
        self.requests.append(_json.loads(body) if body else {})
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-wire",
                "object": "chat.completion",
                "created": 1,
                "model": "qwen3.7-max-2026-05-20",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
            request=request,
        )


@pytest.mark.asyncio
async def test_qwen_wire_sends_max_completion_tokens_with_budget() -> None:
    """Qwen (DashScope OpenAI-compatible) receives the per-request budget
    as ``max_completion_tokens`` — the OpenAI wire name PydanticAI maps
    ``max_tokens`` to — together with the production ``enable_thinking``
    payload. Offline HTTP capture; no network."""
    import httpx
    from openai import AsyncOpenAI
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    transport = _WireCaptureTransport()
    openai_client = AsyncOpenAI(
        api_key="test-key-not-real",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        http_client=httpx.AsyncClient(
            transport=transport,  # type: ignore[arg-type]
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        ),
        max_retries=0,
    )
    model = OpenAIChatModel(
        "qwen3.7-max-2026-05-20",
        provider=OpenAIProvider(openai_client=openai_client),
        settings={"extra_body": {"enable_thinking": True}},
    )
    agent: Agent[Any, str] = Agent(model, output_type=str)
    await agent.run(
        "hi",
        model_settings={
            "max_tokens": _PROBE_PER_REQUEST_MAX_OUTPUT_TOKENS,
            "extra_body": {"enable_thinking": True},
        },
    )

    assert transport.requests
    first = transport.requests[0]
    assert first.get("max_completion_tokens") == _PROBE_PER_REQUEST_MAX_OUTPUT_TOKENS
    assert first.get("enable_thinking") is True


# ---------------------------------------------------------------------------
# Offline two-round SDK-shape probe: production qwen-max model + SSE
# MockTransport (no real_llm marker, no network).
#
# Reproduces the real-provider qwen-max failure shape offline and locks the
# repaired contract: the production-built OpenAI-compatible model must
# deliver the canonical final answer as streamed TextPart content (prompted
# structured output) so run_agent_with_thinking_transport emits
# AnswerDeltaEvent. Before the repair the final answer rode the Pydantic
# output-tool lane: canonical output present, zero streamed answer deltas.
# ---------------------------------------------------------------------------

_QWEN_OFFLINE_REASONING_SENTINEL = "QWEN-OFFLINE-REASONING-SENTINEL-7f3"
# Multi-word reasoning mirrors the real provider: the streaming redactor
# releases the plain prefix during the round and holds back only the
# trailing sentinel word until flush, so the reasoning phase is observed
# in-round (not only at final flush).
_QWEN_ROUND1_REASONING = (
    "offline qwen round one reasoning before calling the echo tool "
    + _QWEN_OFFLINE_REASONING_SENTINEL
)
_QWEN_ROUND2_REASONING = (
    "offline qwen round two reasoning before the final answer "
    + _QWEN_OFFLINE_REASONING_SENTINEL
    + "-r2"
)


def _qwen_sse_chunk(
    *,
    delta: dict[str, Any] | None = None,
    finish_reason: str | None = None,
    usage: dict[str, Any] | None = None,
) -> str:
    payload: dict[str, Any] = {
        "id": "chatcmpl-qwen-offline",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "qwen3.7-max-2026-05-20",
        "choices": [],
    }
    if delta is not None or finish_reason is not None:
        choice: dict[str, Any] = {
            "index": 0,
            "delta": delta if delta is not None else {},
        }
        if finish_reason is not None:
            choice["finish_reason"] = finish_reason
        payload["choices"] = [choice]
    if usage is not None:
        payload["usage"] = usage
    return f"data: {json.dumps(payload)}\n\n"


class _QwenTwoRoundSSETransport(httpx.AsyncBaseTransport):
    """Offline DashScope-compatible SSE responder for the two-round contract.

    Round 1 always streams ``reasoning_content`` then the echo tool call.
    Round 2 adapts to the request like the real endpoint:

    - output-tool lane present (pre-repair default structured-output mode)
      → the final answer is delivered as the ``final_result`` tool call
      (canonical output, zero streamed answer text);
    - prompted mode (repaired) → the final answer streams as TextPart
      JSON content deltas.
    """

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def _round1_body(self) -> str:
        chunks = [
            _qwen_sse_chunk(delta={"role": "assistant"}),
            _qwen_sse_chunk(delta={"reasoning_content": _QWEN_ROUND1_REASONING}),
            _qwen_sse_chunk(
                delta={
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "tc-echo-1",
                            "type": "function",
                            "function": {
                                "name": "echo",
                                "arguments": json.dumps({"text": "ping"}),
                            },
                        }
                    ]
                }
            ),
            _qwen_sse_chunk(finish_reason="tool_calls"),
            _qwen_sse_chunk(
                usage={
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "total_tokens": 120,
                }
            ),
        ]
        return "".join(chunks) + "data: [DONE]\n\n"

    def _round2_body(self, *, has_output_tool: bool) -> str:
        chunks = [
            _qwen_sse_chunk(delta={"role": "assistant"}),
            _qwen_sse_chunk(delta={"reasoning_content": _QWEN_ROUND2_REASONING}),
        ]
        if has_output_tool:
            chunks.append(
                _qwen_sse_chunk(
                    delta={
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "tc-final-1",
                                "type": "function",
                                "function": {
                                    "name": "final_result",
                                    "arguments": _PROBE_ANSWER_JSON,
                                },
                            }
                        ]
                    }
                )
            )
            chunks.append(_qwen_sse_chunk(finish_reason="tool_calls"))
        else:
            # Prompted mode: the canonical answer streams as TextPart
            # JSON content deltas.
            for piece in (
                '{"response_kind": "clarification", ',
                '"clarification_text": "pong", ',
                '"answer_blocks": []}',
            ):
                chunks.append(_qwen_sse_chunk(delta={"content": piece}))
            chunks.append(_qwen_sse_chunk(finish_reason="stop"))
        chunks.append(
            _qwen_sse_chunk(
                usage={
                    "prompt_tokens": 90,
                    "completion_tokens": 15,
                    "total_tokens": 105,
                }
            )
        )
        return "".join(chunks) + "data: [DONE]\n\n"

    async def handle_async_request(self, request: Any) -> Any:
        body = request.content.decode("utf-8") if request.content else ""
        payload = json.loads(body) if body else {}
        self.requests.append(payload)
        tools = payload.get("tools") or []
        tool_names = {
            t.get("function", {}).get("name")
            for t in tools
            if isinstance(t, dict)
        }
        has_output_tool = "final_result" in tool_names
        if len(self.requests) == 1:
            text = self._round1_body()
        else:
            text = self._round2_body(has_output_tool=has_output_tool)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=text.encode("utf-8"),
            request=request,
        )


class _RecordingEventSink:
    """Event sink recording phase order plus reasoning/answer delta texts."""

    def __init__(self) -> None:
        self.phases: list[str] = []
        self.reasoning_delta_count = 0
        self.reasoning_text: list[str] = []
        self.answer_text: list[str] = []

    def __call__(self, event: Any) -> None:
        if isinstance(event, AgenticReasoningDeltaEvent):
            if "reasoning" not in self.phases:
                self.phases.append("reasoning")
            self.reasoning_delta_count += 1
            self.reasoning_text.append(event.delta)
        elif isinstance(event, AnswerPreviewResetEvent):
            if "tool_round" not in self.phases:
                self.phases.append("tool_round")
        elif isinstance(event, AnswerDeltaEvent):
            if "answer" not in self.phases:
                self.phases.append("answer")
            self.answer_text.append(event.delta)


@pytest.mark.asyncio
async def test_qwen_prompted_two_round_streams_answer_deltas_offline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production qwen-max model + SSE MockTransport: prompted structured
    output must stream the canonical final answer as TextPart content so
    the production transport emits AnswerDeltaEvent (offline; no network)."""
    import warnings

    import httpx
    from openai import AsyncOpenAI
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    from app.services.reader_record_ask import model_options as model_options_svc
    from app.services.reader_record_ask.execution_config import (
        resolve_reader_record_ask_execution,
    )

    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-only-not-a-secret")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only-not-a-secret")
    settings = _portable_settings()
    option = model_options_svc.resolve_reader_ask_model_option(
        settings, "qwen-max", strict=True
    )
    execution = resolve_reader_record_ask_execution(option, settings=settings)
    raw_model = execution.model
    assert isinstance(raw_model, OpenAIChatModel), (
        "production qwen-max option must build an OpenAI-compatible chat model"
    )

    transport = _QwenTwoRoundSSETransport()
    base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    http_client = httpx.AsyncClient(transport=transport, base_url=base_url)
    openai_client = AsyncOpenAI(
        api_key="test-only-not-a-secret",
        base_url=base_url,
        http_client=http_client,
        max_retries=0,
    )
    try:
        # Single test seam: swap the transport-bearing provider onto the
        # production-built model. Profile merge, thinking normalization,
        # and resolved settings all stay on the production path.
        raw_model._provider = OpenAIProvider(openai_client=openai_client)

        sink = _RecordingEventSink()
        observer = ProviderReasoningObserver(
            emit=sink,
            message_id="probe-message",
            thread_id="probe-thread",
            turn_run_id="probe-turn",
        )
        counting_model = _RequestCountingModel(raw_model)
        agent: Agent[ReaderRecordAskDeps, AgentAnswerDraftOutput] = Agent(
            counting_model,
            deps_type=ReaderRecordAskDeps,
            output_type=AgentAnswerDraftOutput,
            tools=[_ECHO_TOOL],
            retries=0,
        )
        agent.instrument = False
        merged_settings: dict[str, Any] = dict(execution.model_settings() or {})
        merged_settings["max_tokens"] = _PROBE_PER_REQUEST_MAX_OUTPUT_TOKENS
        with (
            disabled_tracing(),
            warnings.catch_warnings(record=True) as caught,
        ):
            warnings.simplefilter("always")
            outcome = await run_agent_with_thinking_transport(
                agent=agent,
                prompt=_PROBE_PROMPT,
                deps=_probe_deps(sink),
                thinking_observer=observer,
                model=counting_model,
                model_settings=ModelSettings(merged_settings),
                usage_limits=UsageLimits(
                    request_limit=_PROBE_MAX_REQUESTS,
                    output_tokens_limit=_PROBE_TOTAL_OUTPUT_TOKEN_BUDGET,
                ),
            )
    finally:
        await http_client.aclose()

    # Request accounting: wrapper == transport == 2; every request went
    # through the mock transport (no real network path exists).
    assert counting_model.request_count == 2
    assert len(transport.requests) == 2
    assert counting_model.request_count == len(transport.requests)

    # Phase order and streamed-answer evidence.
    assert sink.phases == ["reasoning", "tool_round", "answer"]
    assert sink.reasoning_delta_count > 0
    answer_text = "".join(sink.answer_text)
    assert len(answer_text) > 0

    # Canonical output contract.
    canonical = outcome.output
    assert isinstance(canonical, AgentAnswerDraftOutput)
    canonical_text = canonical.clarification_text or "\n\n".join(
        block.text for block in canonical.answer_blocks
    )
    assert canonical_text, "canonical answer text must be non-empty"
    assert answer_text == canonical_text

    # No cross-stream between thinking and answer lanes.
    assert _QWEN_OFFLINE_REASONING_SENTINEL not in answer_text
    assert "pong" not in "".join(sink.reasoning_text)

    # Wire contract on BOTH requests: thinking payload, Qwen wire budget
    # name, and the echo tool as the only tool — the Pydantic final-output
    # tool must not be the second-round output mechanism.
    for req in transport.requests:
        assert req.get("enable_thinking") is True
        assert req.get("max_completion_tokens") == _PROBE_PER_REQUEST_MAX_OUTPUT_TOKENS
        tool_names = {
            t.get("function", {}).get("name") for t in (req.get("tools") or [])
        }
        assert tool_names == {"echo"}

    # Provider-level thinking fields survive the model-level profile
    # merge: the round-2 request sends the round-1 assistant reasoning
    # back in the reasoning_content field.
    second = transport.requests[1]
    assistants = [
        m for m in second.get("messages", []) if m.get("role") == "assistant"
    ]
    tool_assistant = next((m for m in assistants if m.get("tool_calls")), None)
    assert tool_assistant is not None
    assert tool_assistant.get("reasoning_content") == _QWEN_ROUND1_REASONING

    # Resource cleanup: the mock http client is closed and no coroutine /
    # stream was left un-awaited in this offline scenario.
    never_awaited = [w for w in caught if "never awaited" in str(w.message)]
    assert not never_awaited


# ---------------------------------------------------------------------------
# Offline model lifecycle regression: the probe owns the model it runs and
# must enter and exit the model's async context manager deterministically,
# so provider-owned HTTP clients and connection pools are closed on exit.
# ---------------------------------------------------------------------------


class _LifecycleTrackingModel(WrapperModel):
    """Track model async context manager lifecycle entry and exit."""

    def __init__(self, wrapped: Model) -> None:
        super().__init__(wrapped)
        self.enter_count = 0
        self.exit_count = 0
        self.exited = False

    async def __aenter__(self):
        self.enter_count += 1
        return await super().__aenter__()

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.exit_count += 1
        self.exited = True
        return await super().__aexit__(exc_type, exc_val, exc_tb)


@pytest.mark.asyncio
async def test_probe_manages_model_lifecycle_offline() -> None:
    """run_reasoning_probe must enter and exit the model context manager so
    that probe-owned resources (such as provider HTTP clients) are closed
    deterministically on exit.

    Offline contract test; no HTTP client, no loopback server, no network.
    """
    tracking = _LifecycleTrackingModel(_offline_probe_model())
    report = await run_reasoning_probe(
        tracking,
        option_key="offline-lifecycle",
        provider="function",
        model_name="offline-function-model",
        profile_name="offline-profile",
    )

    assert tracking.enter_count == 1
    assert tracking.exit_count == 1
    assert tracking.exited is True
    assert report.phases == ["reasoning", "tool_round", "answer"]
    assert report.provider_request_count == 2
    assert report.canonical_output_present is True


@pytest.mark.asyncio
async def test_probe_manages_model_lifecycle_on_error_offline() -> None:
    """run_reasoning_probe exits the model context manager even if the
    underlying model raises during execution."""

    async def failing_stream_fn(messages, info):
        yield {0: DeltaThinkingPart(content="about to fail")}
        raise RuntimeError("probe-stream-failure")

    tracking = _LifecycleTrackingModel(FunctionModel(stream_function=failing_stream_fn))
    with pytest.raises(RuntimeError, match="probe-stream-failure"):
        await run_reasoning_probe(
            tracking,
            option_key="offline-lifecycle-error",
            provider="function",
            model_name="offline-function-model",
            profile_name="offline-profile",
        )

    assert tracking.enter_count == 1
    assert tracking.exit_count == 1
    assert tracking.exited is True
