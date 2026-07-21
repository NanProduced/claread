"""R7-3 / R7-3b hermetic regressions: grammar batch lease heartbeat +
model-invocation usage (no DB, no real LLM).

Covers:

LeaseHeartbeat manager:
- periodic renewal + clean stop, no residual task;
- renewal failure recorded (lost/error), never silently swallowed;
- stop idempotent; context manager surfaces loss on clean exit;
- interval clamped strictly below the lease.

Usage persistence (R7-3b):
- generate returns → usage persisted IMMEDIATELY
  (model_call_completed) → publish → SAME row updated
  (layer_published);
- heartbeat lost before publish → publish skipped, row updated
  ownership_lost, NO job/run writes;
- publish/fence failure → row updated publication_failed;
- cancellation during publish → row updated publication_interrupted
  from a detached task, exactly one row, no residual heartbeat;
- persistence failure → NOT marked recorded, idempotent retry yields
  exactly one row;
- model failure before usage → STATUS_FAILED event, usage_data=None
  (never fabricated);
- same invocation key → one row; different lease token → two rows;
- real service functions (record_model_invocation_usage_event /
  update_ai_usage_event_outcome) are idempotent by key against a fake
  pool.

Expiry-aware runtime (real lease_expires_at + publisher fence):
- positive: 40ms lease / 10ms interval / 120ms generate → published
  exactly once;
- negative: no renewal → lease expires → publisher fence fails.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

import app.services.ai_usage.service as usage_service_module
import app.services.reader_orchestration.grammar_worker as grammar_worker_module
import app.services.reader_orchestration.lease_heartbeat as lease_heartbeat_module
from app.schemas.reader_orchestration import GrammarBundleOutput
from app.services.ai_usage.service import AIUsageEventCreate
from app.services.reader_orchestration.grammar_worker import (
    GRAMMAR_USAGE_STATUS_LAYER_PUBLISHED,
    GRAMMAR_USAGE_STATUS_OWNERSHIP_LOST,
    GRAMMAR_USAGE_STATUS_PUBLICATION_FAILED,
    GRAMMAR_USAGE_STATUS_PUBLICATION_INTERRUPTED,
    GrammarBatchExecutionResult,
    GrammarBatchJobContext,
    GrammarBatchUnitContext,
    GrammarBundleWorkerService,
    GrammarExecutionError,
)
from app.services.reader_orchestration.job_runtime import (
    ClaimResult,
    FenceViolationError,
    IllegalTransitionError,
)
from app.services.reader_orchestration.layer_publisher import PublishedGrammarBatch
from app.services.reader_orchestration.lease_heartbeat import LeaseHeartbeat

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


class ExpiryAwareRuntime:
    """Runtime that REALLY tracks lease_expires_at (R7-3b test A).

    heartbeat() extends the lease iff it has not expired; an expired
    lease raises IllegalTransitionError like the real runtime after
    stale-lease recovery. ``lease_valid()`` lets the fake publisher
    enforce the fence the way publish_article_grammar_batch does
    in-transaction.
    """

    def __init__(self, lease_duration: timedelta) -> None:
        self.lease_expires_at = datetime.now(UTC) + lease_duration
        self.heartbeat_calls = 0
        self.transitions: list[dict] = []

    def lease_valid(self) -> bool:
        return datetime.now(UTC) < self.lease_expires_at

    async def heartbeat(
        self, *, job_id: UUID, lease_token: UUID, lease_duration: timedelta
    ) -> datetime:
        if not self.lease_valid():
            raise IllegalTransitionError(
                "heartbeat on expired lease (recovered by stale-lease "
                "recovery)"
            )
        self.heartbeat_calls += 1
        self.lease_expires_at = datetime.now(UTC) + lease_duration
        return self.lease_expires_at

    async def transition(self, **kwargs) -> SimpleNamespace:
        self.transitions.append(kwargs)
        return SimpleNamespace(status=kwargs["target_status"])


class FakeBatchExecutor:
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

    async def generate_batch(self, context: GrammarBatchJobContext):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raise_exc is not None:
            raise self.raise_exc
        return GrammarBatchExecutionResult(
            outputs=[("u1", GrammarBundleOutput())],
            usage_data=self.usage_data,
        )


def _published_batch() -> PublishedGrammarBatch:
    return PublishedGrammarBatch(
        reading_record_id=RECORD_ID,
        base_id=BASE_ID,
        generation=1,
        layers=(),
        layer_ids=(),
        layer_types=(),
        no_op=True,
    )


class FakePublisher:
    def __init__(self, *, raise_exc: Exception | None = None) -> None:
        self.raise_exc = raise_exc
        self.calls: list[dict] = []

    async def publish_article_grammar_batch(self, **kwargs) -> PublishedGrammarBatch:
        self.calls.append(kwargs)
        if self.raise_exc is not None:
            raise self.raise_exc
        return _published_batch()


class LeaseValidatingPublisher:
    """Publisher that enforces the lease fence (R7-3b test A)."""

    def __init__(self, runtime: ExpiryAwareRuntime) -> None:
        self.runtime = runtime
        self.calls: list[dict] = []

    async def publish_article_grammar_batch(self, **kwargs) -> PublishedGrammarBatch:
        if not self.runtime.lease_valid():
            raise FenceViolationError("lease expired at publish fence")
        self.calls.append(kwargs)
        return _published_batch()


class InMemoryUsageStore:
    """Emulates ai_usage persistence with invocation-key idempotency.

    ``record`` mirrors record_model_invocation_usage_event (SELECT by
    request_id first; insert otherwise; None on injected failure).
    ``record_failed`` mirrors the pre-existing STATUS_FAILED error
    event path (no invocation key, no tokens). ``update`` mirrors
    update_ai_usage_event_outcome (same row, never a second event).
    """

    def __init__(self) -> None:
        self.rows: dict[UUID, dict] = {}
        self.record_attempts = 0
        self.insert_count = 0
        self.update_attempts = 0
        self.updates: list[dict] = []
        self.fail_next_records = 0
        self.fail_next_updates = 0
        self.record_started = asyncio.Event()
        self.record_release: asyncio.Event | None = None

    async def record(self, event: AIUsageEventCreate) -> UUID | None:
        self.record_attempts += 1
        self.record_started.set()
        if self.record_release is not None:
            await self.record_release.wait()
        if event.request_id:
            for row_id, row in self.rows.items():
                if row["request_id"] == event.request_id:
                    return row_id  # idempotent: same invocation, same row
        if self.fail_next_records > 0:
            self.fail_next_records -= 1
            return None  # DB failure: caller must NOT mark as recorded
        self.insert_count += 1
        row_id = uuid4()
        self.rows[row_id] = {
            "request_id": event.request_id,
            "status": event.status,
            "usage_data": event.usage_data,
            "metadata": dict(event.metadata_json or {}),
            "error_code": event.error_code,
        }
        return row_id

    async def record_failed(self, event: AIUsageEventCreate) -> UUID | None:
        row_id = uuid4()
        self.rows[row_id] = {
            "request_id": event.request_id,
            "status": event.status,
            "usage_data": event.usage_data,
            "metadata": dict(event.metadata_json or {}),
            "error_code": event.error_code,
        }
        return row_id

    async def update(
        self,
        event_id: UUID,
        *,
        status: str,
        metadata_patch: dict | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> bool:
        self.update_attempts += 1
        if self.fail_next_updates > 0:
            self.fail_next_updates -= 1
            return False
        row = self.rows.get(event_id)
        if row is None:
            return False
        row["status"] = status
        row["metadata"].update(metadata_patch or {})
        if error_code is not None:
            row["error_code"] = error_code
        self.updates.append({"event_id": event_id, "status": status})
        return True


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
        target_type="unit_range",
        target_key="unit_range",
        expected_generation=1,
        operation_fingerprint="grammar_bundle_article_v1",
        attempt_count=1,
        lease_owner="r7-3-test",
        lease_token=lease_token or uuid4(),
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=120),
        trace_id=None,
        claim_wait_ms=None,
    )


def _batch_context() -> GrammarBatchJobContext:
    return GrammarBatchJobContext(
        job_id=uuid4(),
        run_id=RUN_ID,
        reading_record_id=RECORD_ID,
        user_id=USER_ID,
        base_id=BASE_ID,
        expected_generation=1,
        operation_fingerprint="grammar_bundle_article_v1",
        source_language="en",
        units=(
            GrammarBatchUnitContext(
                unit_id="u1",
                order_index=1,
                source_text="The deadline for police protection passed.",
                text_hash="h1",
                anchor_segments=(),
            ),
        ),
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        strategy_version="test-strategy",
        strategy_hash="test-strategy-hash",
        layer_policy_hash="test-layer-policy",
        grammar_prompt_lines=(),
        article_route="structured_batch",
        document_features=None,
    )


@pytest.fixture
def usage_store(monkeypatch) -> InMemoryUsageStore:
    store = InMemoryUsageStore()
    monkeypatch.setattr(
        grammar_worker_module,
        "record_model_invocation_usage_event",
        store.record,
    )
    monkeypatch.setattr(
        grammar_worker_module, "update_ai_usage_event_outcome", store.update
    )
    monkeypatch.setattr(
        grammar_worker_module, "record_ai_usage_event", store.record_failed
    )
    return store


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
    runtime,
    executor: FakeBatchExecutor,
    publisher,
    *,
    heartbeat_interval: timedelta = timedelta(milliseconds=10),
) -> GrammarBundleWorkerService:
    return GrammarBundleWorkerService(
        pool=MagicMock(),
        job_runtime=runtime,
        layer_publisher=publisher,
        executor=MagicMock(),
        batch_executor=executor,
        batch_lease_duration=timedelta(seconds=120),
        batch_heartbeat_interval=heartbeat_interval,
    )


def _patch_worker_db(monkeypatch, service: GrammarBundleWorkerService):
    context = _batch_context()
    monkeypatch.setattr(
        service, "_load_batch_job_context", AsyncMock(return_value=context)
    )
    run_status_calls: list[dict] = []

    async def fake_mark_run_status(run_id, **kwargs):
        run_status_calls.append(kwargs)

    monkeypatch.setattr(service, "_mark_run_status", fake_mark_run_status)
    return context, run_status_calls


def _lingering_heartbeat_tasks() -> list[asyncio.Task]:
    return [
        task
        for task in asyncio.all_tasks()
        if task.get_name().startswith("lease-heartbeat-") and not task.done()
    ]


def _expected_invocation_key(claim: ClaimResult) -> str:
    return f"reader_grammar_batch:{claim.job_id}:{claim.lease_token}"


# ---------------------------------------------------------------------------
# LeaseHeartbeat manager
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_heartbeat_renews_periodically_and_stops_cleanly() -> None:
    runtime = FakeJobRuntime()
    heartbeat = LeaseHeartbeat(
        job_runtime=runtime,
        job_id=uuid4(),
        lease_token=uuid4(),
        lease_duration=timedelta(seconds=120),
        heartbeat_interval=timedelta(milliseconds=10),
    )
    await heartbeat.start()
    await asyncio.sleep(0.075)
    await heartbeat.stop()

    assert runtime.heartbeat_calls >= 3
    assert not heartbeat.lost
    assert heartbeat.error is None
    assert _lingering_heartbeat_tasks() == []


@pytest.mark.anyio
async def test_heartbeat_records_renewal_failure_without_swallowing() -> None:
    runtime = FakeJobRuntime(fail_heartbeat_after=0)
    heartbeat = LeaseHeartbeat(
        job_runtime=runtime,
        job_id=uuid4(),
        lease_token=uuid4(),
        lease_duration=timedelta(seconds=120),
        heartbeat_interval=timedelta(milliseconds=10),
    )
    await heartbeat.start()
    for _ in range(100):
        if heartbeat.lost:
            break
        await asyncio.sleep(0.005)
    await heartbeat.stop()

    assert heartbeat.lost
    assert isinstance(heartbeat.error, IllegalTransitionError)
    with pytest.raises(IllegalTransitionError):
        heartbeat.assert_ownership()
    assert runtime.heartbeat_calls == 1
    assert _lingering_heartbeat_tasks() == []


@pytest.mark.anyio
async def test_heartbeat_stop_is_idempotent() -> None:
    heartbeat = LeaseHeartbeat(
        job_runtime=FakeJobRuntime(),
        job_id=uuid4(),
        lease_token=uuid4(),
        lease_duration=timedelta(seconds=120),
        heartbeat_interval=timedelta(milliseconds=10),
    )
    await heartbeat.start()
    await heartbeat.stop()
    await heartbeat.stop()
    assert heartbeat._task is None


@pytest.mark.anyio
async def test_heartbeat_context_manager_surfaces_loss_on_clean_exit() -> None:
    runtime = FakeJobRuntime(fail_heartbeat_after=0)
    with pytest.raises(IllegalTransitionError):
        async with LeaseHeartbeat(
            job_runtime=runtime,
            job_id=uuid4(),
            lease_token=uuid4(),
            lease_duration=timedelta(seconds=120),
            heartbeat_interval=timedelta(milliseconds=10),
        ):
            await asyncio.sleep(0.05)


def test_heartbeat_interval_clamps_when_not_shorter_than_lease() -> None:
    heartbeat = LeaseHeartbeat(
        job_runtime=FakeJobRuntime(),
        job_id=uuid4(),
        lease_token=uuid4(),
        lease_duration=timedelta(seconds=120),
        heartbeat_interval=timedelta(seconds=120),
    )
    assert heartbeat.interval == timedelta(seconds=60)


def test_heartbeat_default_interval_derives_from_lease() -> None:
    heartbeat = LeaseHeartbeat(
        job_runtime=FakeJobRuntime(),
        job_id=uuid4(),
        lease_token=uuid4(),
        lease_duration=timedelta(seconds=120),
    )
    assert heartbeat.interval == timedelta(seconds=30)


@pytest.mark.anyio
async def test_verify_ownership_probes_the_live_lease() -> None:
    # Invalid lease: the immediate probe raises (even though the loop
    # never ran, so heartbeat.lost alone would be False).
    failing = LeaseHeartbeat(
        job_runtime=FakeJobRuntime(fail_heartbeat_after=0),
        job_id=uuid4(),
        lease_token=uuid4(),
        lease_duration=timedelta(seconds=120),
        heartbeat_interval=timedelta(seconds=30),
    )
    with pytest.raises(IllegalTransitionError):
        await failing.verify_ownership()

    # Valid lease: the probe renews and returns cleanly.
    healthy = LeaseHeartbeat(
        job_runtime=FakeJobRuntime(),
        job_id=uuid4(),
        lease_token=uuid4(),
        lease_duration=timedelta(seconds=120),
        heartbeat_interval=timedelta(seconds=30),
    )
    await healthy.verify_ownership()


# ---------------------------------------------------------------------------
# Usage persistence: immediate persist + same-row outcome update
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_usage_persisted_immediately_and_outcome_updates_same_row(
    usage_store: InMemoryUsageStore, silent_spans, monkeypatch
) -> None:
    runtime = FakeJobRuntime()
    executor = FakeBatchExecutor(delay=0.12)
    publisher = FakePublisher()
    service = _build_service(runtime, executor, publisher)
    _patch_worker_db(monkeypatch, service)
    claim = _make_claim()

    result = await service.process_claimed_grammar_batch_job(
        claim=claim, lease_duration=timedelta(seconds=120)
    )

    assert result.status == "succeeded"
    assert runtime.heartbeat_calls >= 3
    assert len(publisher.calls) == 1
    assert runtime.transitions == []
    # Exactly ONE usage row for this invocation: inserted with
    # model_call_completed, then UPDATED (never a second insert) to
    # layer_published.
    assert usage_store.insert_count == 1
    assert len(usage_store.rows) == 1
    row = next(iter(usage_store.rows.values()))
    assert row["request_id"] == _expected_invocation_key(claim)
    assert row["status"] == GRAMMAR_USAGE_STATUS_LAYER_PUBLISHED
    assert row["usage_data"] == REAL_USAGE
    assert row["metadata"]["model_call_completed"] is True
    assert row["metadata"]["attempt_lease_token"] == str(claim.lease_token)
    assert [u["status"] for u in usage_store.updates] == [
        GRAMMAR_USAGE_STATUS_LAYER_PUBLISHED
    ]
    assert _lingering_heartbeat_tasks() == []


@pytest.mark.anyio
async def test_heartbeat_lost_before_publish_skips_publish_and_writes_nothing_to_job_or_run(
    usage_store: InMemoryUsageStore, silent_spans, monkeypatch
) -> None:
    runtime = FakeJobRuntime(fail_heartbeat_after=0)
    executor = FakeBatchExecutor(delay=0.05)
    publisher = FakePublisher()
    service = _build_service(runtime, executor, publisher)
    _, run_status_calls = _patch_worker_db(monkeypatch, service)

    result = await service.process_claimed_grammar_batch_job(
        claim=_make_claim(), lease_duration=timedelta(seconds=120)
    )

    assert result.status == "retry_later"
    assert result.ownership_lost is True
    assert result.published_batch is None
    assert publisher.calls == []
    # R7-3b: NO unfenced writes after ownership loss — neither
    # reader_jobs transitions nor reader_runs status updates.
    assert runtime.transitions == []
    assert run_status_calls == []
    # The completed invocation's usage still recorded exactly once,
    # updated to ownership_lost on the SAME row.
    assert usage_store.insert_count == 1
    assert len(usage_store.rows) == 1
    row = next(iter(usage_store.rows.values()))
    assert row["status"] == GRAMMAR_USAGE_STATUS_OWNERSHIP_LOST
    assert row["usage_data"] == REAL_USAGE
    assert row["error_code"] == "heartbeat_lost"
    assert _lingering_heartbeat_tasks() == []


@pytest.mark.anyio
async def test_publish_fence_failure_updates_same_usage_row(
    usage_store: InMemoryUsageStore, silent_spans, monkeypatch
) -> None:
    runtime = FakeJobRuntime()
    executor = FakeBatchExecutor()
    publisher = FakePublisher(raise_exc=FenceViolationError("claim lease mismatch"))
    service = _build_service(runtime, executor, publisher)
    _patch_worker_db(monkeypatch, service)

    with pytest.raises(FenceViolationError):
        await service.process_claimed_grammar_batch_job(
            claim=_make_claim(), lease_duration=timedelta(seconds=120)
        )

    assert usage_store.insert_count == 1
    assert len(usage_store.rows) == 1
    row = next(iter(usage_store.rows.values()))
    assert row["status"] == GRAMMAR_USAGE_STATUS_PUBLICATION_FAILED
    assert row["usage_data"] == REAL_USAGE
    assert row["error_code"] == "publish_fence_failed"
    assert [t["target_status"] for t in runtime.transitions] == ["superseded"]


@pytest.mark.anyio
async def test_model_failure_before_usage_records_no_fabricated_tokens(
    usage_store: InMemoryUsageStore, silent_spans, monkeypatch
) -> None:
    runtime = FakeJobRuntime()
    executor = FakeBatchExecutor(
        raise_exc=GrammarExecutionError(
            "model exploded",
            retryable=True,
            failure_class="model",
            failure_code="model_exploded",
        )
    )
    publisher = FakePublisher()
    service = _build_service(runtime, executor, publisher)
    _patch_worker_db(monkeypatch, service)

    result = await service.process_claimed_grammar_batch_job(
        claim=_make_claim(), lease_duration=timedelta(seconds=120)
    )

    assert result.status == "retry_later"
    assert publisher.calls == []
    # Exactly one FAILED error event carrying NO token usage.
    assert len(usage_store.rows) == 1
    row = next(iter(usage_store.rows.values()))
    assert row["status"] == "failed"
    assert row["usage_data"] is None
    assert row["error_code"] == "model_exploded"
    # No invocation-keyed row exists (no real invocation completed).
    assert usage_store.insert_count == 0


@pytest.mark.anyio
async def test_generic_exception_after_model_updates_same_row_once(
    usage_store: InMemoryUsageStore, silent_spans, monkeypatch
) -> None:
    runtime = FakeJobRuntime()
    executor = FakeBatchExecutor()

    class _Boom(Exception):
        pass

    publisher = FakePublisher(raise_exc=_Boom("unexpected"))
    service = _build_service(runtime, executor, publisher)
    _patch_worker_db(monkeypatch, service)

    result = await service.process_claimed_grammar_batch_job(
        claim=_make_claim(), lease_duration=timedelta(seconds=120)
    )

    assert result.status == "failed_terminal"
    assert usage_store.insert_count == 1
    assert len(usage_store.rows) == 1
    row = next(iter(usage_store.rows.values()))
    assert row["status"] == GRAMMAR_USAGE_STATUS_PUBLICATION_FAILED
    assert row["usage_data"] == REAL_USAGE
    assert [t["target_status"] for t in runtime.transitions] == ["failed_terminal"]


@pytest.mark.anyio
async def test_retry_invocations_are_separate_usage_rows(
    usage_store: InMemoryUsageStore, silent_spans, monkeypatch
) -> None:
    runtime = FakeJobRuntime()
    executor = FakeBatchExecutor()
    publisher = FakePublisher()
    service = _build_service(runtime, executor, publisher)
    _patch_worker_db(monkeypatch, service)

    claim_a = _make_claim()
    claim_b = _make_claim()
    result_a = await service.process_claimed_grammar_batch_job(
        claim=claim_a, lease_duration=timedelta(seconds=120)
    )
    result_b = await service.process_claimed_grammar_batch_job(
        claim=claim_b, lease_duration=timedelta(seconds=120)
    )

    assert result_a.status == "succeeded"
    assert result_b.status == "succeeded"
    assert executor.calls == 2
    # Two real invocations (different lease tokens) → two rows, never
    # wrongly deduplicated.
    assert usage_store.insert_count == 2
    assert len(usage_store.rows) == 2
    keys = {row["request_id"] for row in usage_store.rows.values()}
    assert keys == {
        _expected_invocation_key(claim_a),
        _expected_invocation_key(claim_b),
    }
    assert all(
        row["status"] == GRAMMAR_USAGE_STATUS_LAYER_PUBLISHED
        for row in usage_store.rows.values()
    )


# ---------------------------------------------------------------------------
# R7-3b B: cancellation after the model returned
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cancellation_after_model_return_finalizes_interrupted_once(
    usage_store: InMemoryUsageStore, silent_spans, monkeypatch
) -> None:
    runtime = FakeJobRuntime()
    executor = FakeBatchExecutor(delay=0.0)  # returns immediately
    publish_started = asyncio.Event()

    class BlockingPublisher:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def publish_article_grammar_batch(self, **kwargs):
            publish_started.set()
            await asyncio.sleep(30)  # blocked until cancelled
            self.calls.append(kwargs)
            return _published_batch()

    publisher = BlockingPublisher()
    service = _build_service(runtime, executor, publisher)
    _patch_worker_db(monkeypatch, service)

    task = asyncio.create_task(
        service.process_claimed_grammar_batch_job(
            claim=_make_claim(), lease_duration=timedelta(seconds=120)
        )
    )
    await asyncio.wait_for(publish_started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # The detached finalization task flips the existing row to
    # publication_interrupted; wait for it (bounded).
    row = None
    for _ in range(200):
        rows = list(usage_store.rows.values())
        if rows and rows[0]["status"] == GRAMMAR_USAGE_STATUS_PUBLICATION_INTERRUPTED:
            row = rows[0]
            break
        await asyncio.sleep(0.01)
    assert row is not None, usage_store.rows
    # Exactly one usage row, outcome updated on the SAME row.
    assert usage_store.insert_count == 1
    assert len(usage_store.rows) == 1
    assert row["usage_data"] == REAL_USAGE
    assert row["error_code"] == "cancelled_during_publish"
    # No residual heartbeat task; no publish completed; no transitions.
    assert _lingering_heartbeat_tasks() == []
    assert publisher.calls == []
    assert runtime.transitions == []


@pytest.mark.anyio
async def test_cancellation_during_initial_usage_persistence_still_records_once(
    usage_store: InMemoryUsageStore, silent_spans, monkeypatch
) -> None:
    """A completed model invocation survives cancellation during its first DB write."""
    runtime = FakeJobRuntime()
    executor = FakeBatchExecutor()
    publisher = FakePublisher()
    service = _build_service(runtime, executor, publisher)
    _patch_worker_db(monkeypatch, service)
    usage_store.record_release = asyncio.Event()

    task = asyncio.create_task(
        service.process_claimed_grammar_batch_job(
            claim=_make_claim(), lease_duration=timedelta(seconds=120)
        )
    )
    await asyncio.wait_for(usage_store.record_started.wait(), timeout=5)
    assert publisher.calls == []

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    usage_store.record_release.set()

    row = None
    for _ in range(200):
        rows = list(usage_store.rows.values())
        if (
            rows
            and rows[0]["status"]
            == GRAMMAR_USAGE_STATUS_PUBLICATION_INTERRUPTED
        ):
            row = rows[0]
            break
        await asyncio.sleep(0.01)

    assert row is not None, usage_store.rows
    assert usage_store.insert_count == 1
    assert len(usage_store.rows) == 1
    assert row["usage_data"] == REAL_USAGE
    assert row["error_code"] == "cancelled_during_usage_persistence"
    assert publisher.calls == []
    assert runtime.transitions == []
    assert _lingering_heartbeat_tasks() == []


# ---------------------------------------------------------------------------
# R7-3b C: usage persistence failure must not be marked recorded
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_persistence_failure_is_retried_idempotently_to_one_row(
    usage_store: InMemoryUsageStore, silent_spans, monkeypatch
) -> None:
    runtime = FakeJobRuntime()
    executor = FakeBatchExecutor()
    publisher = FakePublisher()
    service = _build_service(runtime, executor, publisher)
    _patch_worker_db(monkeypatch, service)
    # First persistence attempt "fails" (returns None) → must NOT be
    # treated as recorded; the retry succeeds and the outcome update
    # still lands on the single row.
    usage_store.fail_next_records = 1

    result = await service.process_claimed_grammar_batch_job(
        claim=_make_claim(), lease_duration=timedelta(seconds=120)
    )

    assert result.status == "succeeded"
    assert usage_store.record_attempts == 2  # failure + successful retry
    assert usage_store.insert_count == 1
    assert len(usage_store.rows) == 1
    row = next(iter(usage_store.rows.values()))
    assert row["status"] == GRAMMAR_USAGE_STATUS_LAYER_PUBLISHED


@pytest.mark.anyio
async def test_outcome_update_failure_retries_same_usage_row(
    usage_store: InMemoryUsageStore, silent_spans, monkeypatch
) -> None:
    runtime = FakeJobRuntime()
    executor = FakeBatchExecutor()
    publisher = FakePublisher()
    service = _build_service(runtime, executor, publisher)
    _patch_worker_db(monkeypatch, service)
    usage_store.fail_next_updates = 1

    result = await service.process_claimed_grammar_batch_job(
        claim=_make_claim(), lease_duration=timedelta(seconds=120)
    )

    assert result.status == "succeeded"
    assert usage_store.update_attempts == 2
    assert usage_store.insert_count == 1
    assert len(usage_store.rows) == 1
    row = next(iter(usage_store.rows.values()))
    assert row["status"] == GRAMMAR_USAGE_STATUS_LAYER_PUBLISHED


@pytest.mark.anyio
async def test_same_invocation_key_never_inserts_twice(
    usage_store: InMemoryUsageStore
) -> None:
    event = AIUsageEventCreate(
        usage_scope="system_internal",
        capability_code="reader_grammar_bundle",
        billing_mode="internal_only",
        status="model_call_completed",
        request_id="reader_grammar_batch:j:t",
        usage_data=dict(REAL_USAGE),
        metadata_json={},
    )
    first = await usage_store.record(event)
    second = await usage_store.record(event)  # retried persistence
    assert first is not None
    assert second == first
    assert usage_store.insert_count == 1
    assert len(usage_store.rows) == 1


# ---------------------------------------------------------------------------
# R7-3b D: ownership race — old attempt writes nothing
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ownership_lost_retryable_error_writes_neither_job_nor_run(
    usage_store: InMemoryUsageStore, silent_spans, monkeypatch
) -> None:
    # Simulate: lease lost during generate (a new attempt may already
    # own the run); the model then raises a retryable error. The old
    # attempt must not touch reader_jobs or reader_runs at all.
    runtime = FakeJobRuntime(fail_heartbeat_after=0)
    executor = FakeBatchExecutor(
        delay=0.05,
        raise_exc=GrammarExecutionError(
            "transient",
            retryable=True,
            failure_class="model",
            failure_code="transient_model_error",
        ),
    )
    publisher = FakePublisher()
    service = _build_service(runtime, executor, publisher)
    _, run_status_calls = _patch_worker_db(monkeypatch, service)

    result = await service.process_claimed_grammar_batch_job(
        claim=_make_claim(), lease_duration=timedelta(seconds=120)
    )

    assert result.status == "retry_later"
    assert result.ownership_lost is True
    assert runtime.transitions == []
    assert run_status_calls == []
    # Model failed before returning usage → only the token-less FAILED
    # error event, no invocation row.
    assert usage_store.insert_count == 0
    statuses = [row["status"] for row in usage_store.rows.values()]
    assert statuses == ["failed"]


# ---------------------------------------------------------------------------
# R7-3b E: idempotency of the REAL service functions (fake pool)
# ---------------------------------------------------------------------------


class _FakePoolConn:
    """SELECT-by-request_id then INSERT ... RETURNING id, in memory."""

    def __init__(self, store: dict[str, UUID]) -> None:
        self.store = store
        self.inserts = 0
        self.execute_calls: list[tuple[str, tuple]] = []

    async def fetchval(self, sql: str, *args):
        if "SELECT id FROM ai_usage_events" in sql:
            return self.store.get(args[0])
        if "INSERT INTO ai_usage_events" in sql:
            self.inserts += 1
            new_id = uuid4()
            self.store[args[13]] = new_id  # request_id is $14 (index 13)
            return new_id
        raise AssertionError(f"unexpected fetchval: {sql}")

    async def execute(self, sql: str, *args):
        self.execute_calls.append((sql, args))
        return "UPDATE 1"

    def acquire(self):
        pool_conn = self

        class _Acquire:
            async def __aenter__(self_inner):
                return pool_conn

            async def __aexit__(self_inner, *exc):
                return False

        return _Acquire()


@pytest.mark.anyio
async def test_real_record_function_is_idempotent_by_invocation_key(monkeypatch):
    store: dict[str, UUID] = {}
    conn = _FakePoolConn(store)
    fake_db = SimpleNamespace(acquire=conn.acquire)
    monkeypatch.setattr(usage_service_module.db_connection, "DB_POOL", fake_db)

    event = AIUsageEventCreate(
        usage_scope="system_internal",
        capability_code="reader_grammar_bundle",
        billing_mode="internal_only",
        status="model_call_completed",
        request_id="reader_grammar_batch:job-1:lease-1",
        usage_data=dict(REAL_USAGE),
        metadata_json={},
    )
    first = await usage_service_module.record_model_invocation_usage_event(event)
    second = await usage_service_module.record_model_invocation_usage_event(event)
    assert first is not None
    assert second == first
    assert conn.inserts == 1  # second call answered from SELECT, no insert


@pytest.mark.anyio
async def test_real_outcome_update_patches_same_row(monkeypatch):
    conn = _FakePoolConn({})
    fake_db = SimpleNamespace(acquire=conn.acquire)
    monkeypatch.setattr(usage_service_module.db_connection, "DB_POOL", fake_db)
    event_id = uuid4()

    updated = await usage_service_module.update_ai_usage_event_outcome(
        event_id,
        status="layer_published",
        metadata_patch={"publication_status": "layer_published"},
    )
    assert updated is True
    sql, args = conn.execute_calls[0]
    assert "UPDATE ai_usage_events" in sql
    assert args[0] == event_id
    assert args[1] == "layer_published"


# ---------------------------------------------------------------------------
# R7-3b A: expiry-aware runtime — positive / negative controls
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_expiry_aware_generate_longer_than_lease_publishes_once_with_heartbeat(
    usage_store: InMemoryUsageStore, silent_spans, monkeypatch
) -> None:
    # Lease 40ms, heartbeat every 10ms, generate takes 120ms (3x the
    # lease). The runtime REALLY expires the lease without renewals,
    # and the publisher REALLY validates the lease — so this success
    # is only possible because the heartbeat kept renewing.
    runtime = ExpiryAwareRuntime(timedelta(milliseconds=40))
    executor = FakeBatchExecutor(delay=0.12)
    publisher = LeaseValidatingPublisher(runtime)
    service = _build_service(
        runtime,
        executor,
        publisher,
        heartbeat_interval=timedelta(milliseconds=10),
    )
    _patch_worker_db(monkeypatch, service)

    result = await service.process_claimed_grammar_batch_job(
        claim=_make_claim(), lease_duration=timedelta(milliseconds=40)
    )

    assert result.status == "succeeded"
    assert runtime.heartbeat_calls >= 3
    assert len(publisher.calls) == 1
    assert usage_store.insert_count == 1
    row = next(iter(usage_store.rows.values()))
    assert row["status"] == GRAMMAR_USAGE_STATUS_LAYER_PUBLISHED
    assert _lingering_heartbeat_tasks() == []


@pytest.mark.anyio
async def test_expiry_aware_without_renewal_lease_expires_and_fence_fails() -> None:
    # Negative control: without renewals the 40ms lease really expires,
    # and the publisher fence really rejects.
    runtime = ExpiryAwareRuntime(timedelta(milliseconds=40))
    publisher = LeaseValidatingPublisher(runtime)
    await asyncio.sleep(0.06)  # longer than the lease, no heartbeat
    assert not runtime.lease_valid()
    with pytest.raises(FenceViolationError):
        await publisher.publish_article_grammar_batch(
            job_id=uuid4(), lease_token=uuid4(), outputs=[]
        )
    assert publisher.calls == []


@pytest.mark.anyio
async def test_expiry_aware_worker_without_renewals_ownership_lost_path(
    usage_store: InMemoryUsageStore, silent_spans, monkeypatch
) -> None:
    # Worker-level negative: renewals neutered (run_forever sleeps
    # without calling heartbeat), 40ms lease, 120ms generate → the
    # lease expires during generate; the heartbeat tick fails; the
    # worker takes the ownership_lost path and never publishes.
    runtime = ExpiryAwareRuntime(timedelta(milliseconds=40))
    executor = FakeBatchExecutor(delay=0.12)
    publisher = LeaseValidatingPublisher(runtime)
    service = _build_service(
        runtime,
        executor,
        publisher,
        heartbeat_interval=timedelta(milliseconds=10),
    )
    _, run_status_calls = _patch_worker_db(monkeypatch, service)

    async def _sleep_forever(self_hb):
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            raise

    monkeypatch.setattr(
        lease_heartbeat_module.LeaseHeartbeat, "run_forever", _sleep_forever
    )

    result = await service.process_claimed_grammar_batch_job(
        claim=_make_claim(), lease_duration=timedelta(milliseconds=40)
    )

    # Without renewals the publish fence would fail — the worker must
    # detect the dead lease BEFORE publishing (fence never reached)
    # and write nothing to jobs/runs.
    assert publisher.calls == []
    assert runtime.transitions == []
    assert run_status_calls == []
    assert result.ownership_lost is True
    assert result.status == "retry_later"
    assert usage_store.insert_count == 1
    row = next(iter(usage_store.rows.values()))
    assert row["status"] == GRAMMAR_USAGE_STATUS_OWNERSHIP_LOST
    assert _lingering_heartbeat_tasks() == []


# ---------------------------------------------------------------------------
# Heartbeat task cleanup on success / failure / cancellation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_heartbeat_task_exits_after_cancellation_during_generate(
    usage_store: InMemoryUsageStore, silent_spans, monkeypatch
) -> None:
    runtime = FakeJobRuntime()
    executor = FakeBatchExecutor(delay=0.3)
    publisher = FakePublisher()
    service = _build_service(runtime, executor, publisher)
    _patch_worker_db(monkeypatch, service)

    task = asyncio.create_task(
        service.process_claimed_grammar_batch_job(
            claim=_make_claim(), lease_duration=timedelta(seconds=120)
        )
    )
    await asyncio.sleep(0.05)  # heartbeat running, model call in flight
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert _lingering_heartbeat_tasks() == []
    assert publisher.calls == []
    # Model never returned → no usage row at all.
    assert usage_store.rows == {}
