"""Per-unit grammar lease-heartbeat and durable-usage regressions.

Mirrors ``test_grammar_batch_lease_heartbeat_usage.py`` for the per-unit
``process_claimed_grammar_job`` path. The per-unit path historically lacked
both:

- Lease heartbeat during the ``executor.generate`` → ``publish_unit_grammar_bundle``
  phase (long model calls could let ``recover_stale_leases`` requeue the job
  and a parallel worker re-process it).
- Durable usage capture before ``FenceViolationError`` — the model call really
  happened, so its captured usage must survive a failed publish fence.

These tests are hermetic (no DB, no real LLM).
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

import app.services.reader_orchestration.grammar_worker as grammar_worker_module
from app.schemas.reader_orchestration import GrammarBundleOutput
from app.services.reader_orchestration.grammar_worker import (
    GrammarBundleWorkerService,
    GrammarExecutionResult,
    GrammarJobContext,
)
from app.services.reader_orchestration.job_runtime import (
    ClaimResult,
    FenceViolationError,
    IllegalTransitionError,
)
from app.services.reader_orchestration.layer_publisher import PublishedGrammarBundle

RECORD_ID = UUID("18b07dfc-bcd1-41b1-8f17-a07073d4e1f7")
USER_ID = UUID("44444444-4444-4444-4444-444444444444")
BASE_ID = UUID("55555555-5555-5555-5555-555555555555")
RUN_ID = UUID("22222222-2222-2222-2222-222222222222")

REAL_USAGE = {
    "aggregate": {"input_tokens": 120, "output_tokens": 45, "total_tokens": 165}
}


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeJobRuntime:
    """In-memory job runtime: heartbeat counter + transition log."""

    def __init__(self, *, fail_heartbeat_after: int | None = None) -> None:
        self.fail_heartbeat_after = fail_heartbeat_after
        self.heartbeat_calls = 0
        self.transitions: list[dict] = []

    async def heartbeat(
        self, *, job_id: UUID, lease_token: UUID, lease_duration: timedelta
    ) -> datetime:
        self.heartbeat_calls += 1
        if (
            self.fail_heartbeat_after is not None
            and self.heartbeat_calls > self.fail_heartbeat_after
        ):
            raise IllegalTransitionError(
                "heartbeat requires status='claimed', got status='queued'"
            )
        return datetime.now(UTC) + lease_duration

    async def transition(self, **kwargs) -> SimpleNamespace:
        self.transitions.append(kwargs)
        return SimpleNamespace(status=kwargs["target_status"])


class FakePerUnitExecutor:
    """Per-unit executor fake: mimics ``GrammarBundleExecutor.generate``."""

    def __init__(
        self,
        *,
        delay: float = 0.0,
        usage_data: dict | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self.delay = delay
        self.usage_data = usage_data if usage_data is not None else dict(REAL_USAGE)
        self.raise_exc = raise_exc
        self.calls = 0

    async def generate(self, context: GrammarJobContext) -> GrammarExecutionResult:
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raise_exc is not None:
            raise self.raise_exc
        return GrammarExecutionResult(
            output=GrammarBundleOutput(),
            usage_data=self.usage_data,
        )


def _published_bundle() -> PublishedGrammarBundle:
    return PublishedGrammarBundle(
        reading_record_id=RECORD_ID,
        base_id=BASE_ID,
        unit_id="u1",
        generation=1,
        grammar_note_layer=None,
        sentence_analysis_layer=None,
        events=(),
        no_op=True,
    )


class FakePerUnitPublisher:
    """Per-unit publisher fake: mimics ``publish_unit_grammar_bundle``."""

    def __init__(self, *, raise_exc: Exception | None = None) -> None:
        self.raise_exc = raise_exc
        self.calls: list[dict] = []

    async def publish_unit_grammar_bundle(self, **kwargs) -> PublishedGrammarBundle:
        self.calls.append(kwargs)
        if self.raise_exc is not None:
            raise self.raise_exc
        return _published_bundle()


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_claim(*, lease_token: UUID | None = None) -> ClaimResult:
    return ClaimResult(
        job_id=uuid4(),
        run_id=RUN_ID,
        reading_record_id=RECORD_ID,
        user_id=USER_ID,
        base_id=BASE_ID,
        job_type="build_grammar_bundle",
        target_type="unit",
        target_key="u1",
        expected_generation=1,
        operation_fingerprint="grammar_bundle_article_v1",
        attempt_count=1,
        lease_owner="phase4-test",
        lease_token=lease_token or uuid4(),
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=120),
        trace_id=None,
        claim_wait_ms=None,
    )


def _per_unit_context() -> GrammarJobContext:
    return GrammarJobContext(
        job_id=uuid4(),
        run_id=RUN_ID,
        reading_record_id=RECORD_ID,
        user_id=USER_ID,
        base_id=BASE_ID,
        unit_id="u1",
        order_index=1,
        expected_generation=1,
        operation_fingerprint="grammar_bundle_article_v1",
        source_language="en",
        source_text="The deadline for police protection passed.",
        text_hash="h1",
        anchor_segments=(),
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        strategy_version="test-strategy",
        strategy_hash="test-strategy-hash",
        layer_policy_hash="test-layer-policy",
        grammar_prompt_lines=(),
    )


@pytest.fixture
def silent_spans(monkeypatch):
    for name in (
        "end_worker_span_success",
        "end_worker_span_fence_violation",
        "end_worker_span_execution_error",
        "end_worker_span_generic_exception",
    ):
        monkeypatch.setattr(grammar_worker_module, name, AsyncMock())


def _build_service(
    runtime: FakeJobRuntime,
    executor: FakePerUnitExecutor,
    publisher: FakePerUnitPublisher,
    *,
    heartbeat_interval: timedelta = timedelta(milliseconds=10),
    lease_duration: timedelta = timedelta(seconds=120),
) -> GrammarBundleWorkerService:
    return GrammarBundleWorkerService(
        pool=MagicMock(),
        job_runtime=runtime,
        layer_publisher=publisher,
        executor=executor,
        batch_executor=MagicMock(),
        batch_lease_duration=lease_duration,
        batch_heartbeat_interval=heartbeat_interval,
    )


def _patch_per_unit_worker_db(
    monkeypatch, service: GrammarBundleWorkerService
) -> tuple[GrammarJobContext, list[dict], AsyncMock, AsyncMock]:
    context = _per_unit_context()
    monkeypatch.setattr(
        service, "_load_job_context", AsyncMock(return_value=context)
    )
    run_status_calls: list[dict] = []

    async def fake_mark_run_status(run_id, **kwargs):
        run_status_calls.append(kwargs)

    monkeypatch.setattr(service, "_mark_run_status", fake_mark_run_status)
    begin_execution = AsyncMock(
        return_value=SimpleNamespace(
            provider_call_allowed=True,
            capture_state="started",
        )
    )
    capture_execution = AsyncMock(return_value=uuid4())
    monkeypatch.setattr(
        service._journal_service,
        "begin_execution",
        begin_execution,
    )
    monkeypatch.setattr(
        service,
        "_capture_grammar_unit_execution",
        capture_execution,
    )
    monkeypatch.setattr(
        service,
        "_reconcile_grammar_unit_usage",
        AsyncMock(return_value=uuid4()),
    )
    return context, run_status_calls, begin_execution, capture_execution


def _lingering_heartbeat_tasks() -> list[asyncio.Task]:
    return [
        task
        for task in asyncio.all_tasks()
        if task.get_name().startswith("lease-heartbeat-") and not task.done()
    ]


# ---------------------------------------------------------------------------
# Per-unit heartbeat renewed during provider call
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_per_unit_lease_heartbeat_renewed_during_llm_call(
    silent_spans, monkeypatch
) -> None:
    """A long provider call renews its lease until durable capture and publish."""
    runtime = FakeJobRuntime()
    executor = FakePerUnitExecutor(delay=0.12)
    publisher = FakePerUnitPublisher()
    service = _build_service(
        runtime,
        executor,
        publisher,
        heartbeat_interval=timedelta(milliseconds=10),
        lease_duration=timedelta(seconds=120),
    )
    _, _, begin_execution, capture_execution = _patch_per_unit_worker_db(
        monkeypatch, service
    )
    claim = _make_claim()

    result = await service.process_claimed_grammar_job(claim=claim)

    assert result.status == "succeeded"
    # Heartbeat must have renewed the lease at least once during the
    # 120ms generate call (10ms interval ⇒ ~12 ticks expected; require
    # at least 3 to be robust on slow CI).
    assert runtime.heartbeat_calls >= 3, (
        f"expected heartbeat to renew during LLM call, got "
        f"{runtime.heartbeat_calls} renewals"
    )
    assert len(publisher.calls) == 1
    begin_execution.assert_awaited_once()
    capture_execution.assert_awaited_once()
    assert _lingering_heartbeat_tasks() == []


# ---------------------------------------------------------------------------
# Per-unit fence violation preserves the captured usage draft
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_per_unit_fence_violation_records_usage_event(
    silent_spans, monkeypatch
) -> None:
    """A publish fence failure preserves the already captured provider usage."""
    runtime = FakeJobRuntime()
    executor = FakePerUnitExecutor()
    publisher = FakePerUnitPublisher(raise_exc=FenceViolationError("lease mismatch"))
    service = _build_service(runtime, executor, publisher)
    _, _, begin_execution, capture_execution = _patch_per_unit_worker_db(
        monkeypatch, service
    )
    claim = _make_claim()

    with pytest.raises(FenceViolationError):
        await service.process_claimed_grammar_job(claim=claim)

    begin_execution.assert_awaited_once()
    capture_execution.assert_awaited_once()
    captured_execution = capture_execution.await_args.kwargs["execution"]
    assert captured_execution.usage_data == REAL_USAGE
    # Job transitioned to superseded (ownership was still valid at fence
    # time; the publisher fence is the authoritative ownership check).
    assert [t["target_status"] for t in runtime.transitions] == ["superseded"]
    assert _lingering_heartbeat_tasks() == []


# ---------------------------------------------------------------------------
# Per-unit success path captures usage before publish
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_per_unit_success_path_persists_usage(
    silent_spans, monkeypatch
) -> None:
    """The success path captures one provider result with its real usage."""
    runtime = FakeJobRuntime()
    executor = FakePerUnitExecutor(delay=0.05)
    publisher = FakePerUnitPublisher()
    service = _build_service(runtime, executor, publisher)
    _, _, begin_execution, capture_execution = _patch_per_unit_worker_db(
        monkeypatch, service
    )
    claim = _make_claim()

    result = await service.process_claimed_grammar_job(claim=claim)

    assert result.status == "succeeded"
    assert len(publisher.calls) == 1
    begin_execution.assert_awaited_once()
    capture_execution.assert_awaited_once()
    captured_execution = capture_execution.await_args.kwargs["execution"]
    assert captured_execution.usage_data == REAL_USAGE
    assert _lingering_heartbeat_tasks() == []
