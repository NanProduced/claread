"""Tests for ``ReaderSpanRecorder`` and ``LangSmithIdBridgeProcessor``.

Covers the observability contract introduced by P2-c (publish_fence span)
and P3 (LangSmith run_id backfill):

- ``start_span`` / ``end_span`` lifecycle writes one INSERT + one UPDATE
- ``use_span`` binds the parent so callers can pick it up via
  ``current_span()`` and pass ``parent_span_id`` explicitly
- Recorder swallows DB errors so workers never break
- ``derive_retry_class`` priority: replan > repair > transient
- ``publish_unit_translation`` wrapper writes a ``publish_fence`` span
  row with the right ``status`` / ``failure_class`` / ``failure_code``
  on success / fence violation / generic exception paths
- ``LangSmithIdBridgeProcessor.on_end`` extracts
  ``langsmith.trace.id`` / ``langsmith.span.id`` from a fake span and
  stores them in the ContextVar
- ``end_span`` auto-backfills ``langsmith_run_id`` from the ContextVar
  when the caller does not pass an explicit value (and explicit wins)
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.observability.langsmith_span_processor import (
    LangSmithIdBridgeProcessor,
    LangSmithIds,
    _CURRENT_LANGSMITH_IDS,
    clear_langsmith_ids,
    get_current_langsmith_ids,
)
from app.services.reader_orchestration.job_runtime import FenceViolationError
from app.services.reader_orchestration.layer_publisher import (
    TranslationLayerPublisher,
)
from app.services.reader_orchestration.span_recorder import (
    RETRY_CLASS_REPAIR,
    RETRY_CLASS_REPLAN,
    RETRY_CLASS_TRANSIENT,
    SPAN_KIND_PIPELINE_ROOT,
    SPAN_KIND_PUBLISH_FENCE,
    SPAN_KIND_WORKER_TICK,
    STATUS_FAILED,
    STATUS_STARTED,
    STATUS_SUCCEEDED,
    ReaderSpanRecorder,
    current_span,
    derive_retry_class,
    set_default_recorder,
)
from tests.reader_orchestration_test_support import (
    BASELINE_SQL,
    connect_admin,
    make_pool,
)

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# Fake OTel ReadableSpan for LangSmithIdBridgeProcessor tests
# ---------------------------------------------------------------------------


@dataclass
class _FakeReadableSpan:
    """Minimal duck-typed stand-in for ``opentelemetry.sdk.trace.ReadableSpan``.

    Only the ``attributes`` mapping and ``name`` are accessed by
    ``_extract_langsmith_ids`` / ``LangSmithIdBridgeProcessor.on_end``.
    """

    attributes: Mapping[str, Any]
    name: str = "fake_span"


# ---------------------------------------------------------------------------
# Fixture: per-test schema + pool + recorder
# ---------------------------------------------------------------------------


@pytest.fixture
async def span_recorder_env() -> AsyncIterator[tuple[asyncpg.Pool, ReaderSpanRecorder]]:
    schema_name = f"test_reader_runtime_spans_{uuid4().hex}"
    admin_conn = await connect_admin()
    pool: asyncpg.Pool | None = None
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)
        pool = await make_pool(schema_name)
        recorder = ReaderSpanRecorder(pool=pool)
        set_default_recorder(recorder)
        try:
            yield pool, recorder
        finally:
            set_default_recorder(None)
            clear_langsmith_ids()
    finally:
        if pool is not None:
            await pool.close()
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


# ---------------------------------------------------------------------------
# Helper: fetch span row from PG by span_id
# ---------------------------------------------------------------------------


async def _fetch_span_row(pool: asyncpg.Pool, span_id: UUID) -> asyncpg.Record:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM reader_runtime_spans WHERE id = $1",
            span_id,
        )
    assert row is not None, f"span row {span_id} not found"
    return row


# ---------------------------------------------------------------------------
# Test 1: single span lifecycle writes one INSERT + one UPDATE
# ---------------------------------------------------------------------------


async def test_start_span_end_span_writes_row(
    span_recorder_env: tuple[asyncpg.Pool, ReaderSpanRecorder],
) -> None:
    pool, recorder = span_recorder_env
    trace_id = uuid4()

    span = await recorder.start_span(
        trace_id=trace_id,
        span_kind=SPAN_KIND_PIPELINE_ROOT,
        reading_record_id=None,
        worker_type="translation",
        attempt_number=1,
        retry_class=RETRY_CLASS_TRANSIENT,
        metadata={"source": "test"},
    )

    started_row = await _fetch_span_row(pool, span.span_id)
    assert started_row["status"] == STATUS_STARTED
    assert started_row["trace_id"] == trace_id
    assert started_row["span_kind"] == SPAN_KIND_PIPELINE_ROOT
    assert started_row["worker_type"] == "translation"
    assert started_row["attempt_number"] == 1
    assert started_row["retry_class"] == RETRY_CLASS_TRANSIENT
    assert started_row["metadata_json"]["source"] == "test"
    assert started_row["ended_at"] is None
    assert started_row["duration_ms"] is None

    await recorder.end_span(
        span,
        status=STATUS_SUCCEEDED,
        model_route="reader_layer_translation",
        model_name="gpt-4-test",
        model_provider="openai",
        input_tokens=100,
        output_tokens=50,
        total_tokens=150,
    )

    ended_row = await _fetch_span_row(pool, span.span_id)
    assert ended_row["status"] == STATUS_SUCCEEDED
    assert ended_row["ended_at"] is not None
    assert ended_row["duration_ms"] is not None
    assert ended_row["duration_ms"] >= 0
    assert ended_row["model_name"] == "gpt-4-test"
    assert ended_row["model_provider"] == "openai"
    assert ended_row["input_tokens"] == 100
    assert ended_row["output_tokens"] == 50
    assert ended_row["total_tokens"] == 150
    assert ended_row["failure_class"] is None
    assert ended_row["failure_code"] is None


# ---------------------------------------------------------------------------
# Test 2: child span picks up parent via current_span() ContextVar
# ---------------------------------------------------------------------------


async def test_child_span_picks_up_parent_via_contextvar(
    span_recorder_env: tuple[asyncpg.Pool, ReaderSpanRecorder],
) -> None:
    pool, recorder = span_recorder_env
    trace_id = uuid4()

    parent = await recorder.start_span(
        trace_id=trace_id,
        span_kind=SPAN_KIND_PIPELINE_ROOT,
    )

    async with recorder.use_span(parent):
        # Caller reads current_span() and threads parent_span_id explicitly.
        active = current_span()
        assert active is not None
        assert active.span_id == parent.span_id

        child = await recorder.start_span(
            trace_id=trace_id,
            span_kind=SPAN_KIND_WORKER_TICK,
            parent_span_id=active.span_id,
            worker_type="translation",
        )

    child_row = await _fetch_span_row(pool, child.span_id)
    assert child_row["parent_span_id"] == parent.span_id
    assert child_row["trace_id"] == trace_id
    assert child_row["span_kind"] == SPAN_KIND_WORKER_TICK


# ---------------------------------------------------------------------------
# Test 3: recorder swallows DB errors (best-effort contract)
# ---------------------------------------------------------------------------


async def test_recorder_swallows_db_errors(
    caplog: pytest.LogCaptureFixture,
) -> None:
    recorder = ReaderSpanRecorder(pool=None)
    # Pool is None and DB_POOL is unset → get_pool() raises RuntimeError.
    # Recorder must swallow it so the worker continues.

    span = await recorder.start_span(
        trace_id=uuid4(),
        span_kind=SPAN_KIND_PIPELINE_ROOT,
    )
    # Span context is still returned (synthetic) so callers can keep going.
    assert span.span_id is not None
    assert span.trace_id is not None

    with caplog.at_level("WARNING"):
        await recorder.end_span(span, status=STATUS_FAILED)

    # Best-effort contract: warning was logged, no exception raised.
    assert any(
        "start_span failed" in record.message or "end_span failed" in record.message
        for record in caplog.records
    ), f"expected start_span/end_span warning in caplog, got: {caplog.records}"


# ---------------------------------------------------------------------------
# Test 4: derive_retry_class priority (replan > repair > transient)
# ---------------------------------------------------------------------------


def test_derive_retry_class_priority() -> None:
    # replan wins over repair and transient
    assert (
        derive_retry_class(
            transient_attempt_count=2,
            repair_attempt_count=3,
            replan_attempt_count=1,
        )
        == RETRY_CLASS_REPLAN
    )
    # repair wins over transient when no replan
    assert (
        derive_retry_class(
            transient_attempt_count=2,
            repair_attempt_count=3,
            replan_attempt_count=0,
        )
        == RETRY_CLASS_REPAIR
    )
    # transient only
    assert (
        derive_retry_class(
            transient_attempt_count=2,
            repair_attempt_count=0,
            replan_attempt_count=0,
        )
        == RETRY_CLASS_TRANSIENT
    )
    # all zero → None
    assert (
        derive_retry_class(
            transient_attempt_count=0,
            repair_attempt_count=0,
            replan_attempt_count=0,
        )
        is None
    )


# ---------------------------------------------------------------------------
# Test 5: publish_unit_translation success writes publish_fence span
# ---------------------------------------------------------------------------


async def _make_publisher_with_mocked_inner(
    pool: asyncpg.Pool,
    *,
    inner_return: Any | None = None,
    inner_exc: Exception | None = None,
) -> TranslationLayerPublisher:
    """Build a TranslationLayerPublisher whose ``_publish_unit_translation_inner``
    is replaced with a stub that either returns ``inner_return`` or raises
    ``inner_exc``. The publisher still uses the real pool + default recorder
    so the publish_fence span row is written."""
    publisher = TranslationLayerPublisher(pool=pool)

    async def _stub_inner(
        *,
        job_id: UUID,
        lease_token: UUID,
        output: Any,
        quality_json: dict[str, Any] | None = None,
    ) -> Any:
        if inner_exc is not None:
            raise inner_exc
        return inner_return

    # Monkeypatch on the instance (not the class) so other tests are not
    # affected.
    publisher._publish_unit_translation_inner = _stub_inner  # type: ignore[method-assign]
    return publisher


async def test_publish_fence_span_success(
    span_recorder_env: tuple[asyncpg.Pool, ReaderSpanRecorder],
) -> None:
    pool, recorder = span_recorder_env
    job_id = uuid4()
    lease_token = uuid4()

    fake_result = SimpleNamespace(
        layer_id=uuid4(),
        reading_record_id=uuid4(),
        base_id=uuid4(),
        unit_id="test-unit-success",
        generation=1,
        event=None,
    )

    publisher = await _make_publisher_with_mocked_inner(
        pool, inner_return=fake_result
    )

    result = await publisher.publish_unit_translation(
        job_id=job_id,
        lease_token=lease_token,
        output=object(),  # type: ignore[arg-type]  # output is opaque to the wrapper
    )
    assert result is fake_result  # type: ignore[comparison-overlap]

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM reader_runtime_spans "
            "WHERE span_kind = $1 AND reader_job_id = $2",
            SPAN_KIND_PUBLISH_FENCE,
            job_id,
        )
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == STATUS_SUCCEEDED
    assert row["failure_class"] is None
    assert row["failure_code"] is None
    assert row["metadata_json"]["layer_type"] == "translation"
    assert row["metadata_json"]["layer_id"] == str(fake_result.layer_id)
    assert row["metadata_json"]["unit_id"] == "test-unit-success"
    assert row["metadata_json"]["generation"] == 1
    # publish_fence span starts before reading record id is read; column
    # is NULLable and the wrapper intentionally does not set it.
    assert row["reading_record_id"] is None
    assert row["reader_job_id"] == job_id


# ---------------------------------------------------------------------------
# Test 6: publish_unit_translation fence violation writes failed span
# ---------------------------------------------------------------------------


async def test_publish_fence_span_fence_violation(
    span_recorder_env: tuple[asyncpg.Pool, ReaderSpanRecorder],
) -> None:
    pool, recorder = span_recorder_env
    job_id = uuid4()
    lease_token = uuid4()

    publisher = await _make_publisher_with_mocked_inner(
        pool,
        inner_exc=FenceViolationError("fence check failed: missing_base"),
    )

    with pytest.raises(FenceViolationError):
        await publisher.publish_unit_translation(
            job_id=job_id,
            lease_token=lease_token,
            output=object(),  # type: ignore[arg-type]
        )

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM reader_runtime_spans "
            "WHERE span_kind = $1 AND reader_job_id = $2",
            SPAN_KIND_PUBLISH_FENCE,
            job_id,
        )
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == STATUS_FAILED
    assert row["failure_class"] == "fence_violation"
    assert row["failure_code"] == "fence_failed"
    assert row["metadata_json"]["layer_type"] == "translation"


# ---------------------------------------------------------------------------
# Test 7: publish_unit_translation generic exception writes failed span
# ---------------------------------------------------------------------------


async def test_publish_fence_span_generic_exception(
    span_recorder_env: tuple[asyncpg.Pool, ReaderSpanRecorder],
) -> None:
    pool, recorder = span_recorder_env
    job_id = uuid4()
    lease_token = uuid4()

    publisher = await _make_publisher_with_mocked_inner(
        pool,
        inner_exc=ValueError("boom"),
    )

    with pytest.raises(ValueError, match="boom"):
        await publisher.publish_unit_translation(
            job_id=job_id,
            lease_token=lease_token,
            output=object(),  # type: ignore[arg-type]
        )

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM reader_runtime_spans "
            "WHERE span_kind = $1 AND reader_job_id = $2",
            SPAN_KIND_PUBLISH_FENCE,
            job_id,
        )
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == STATUS_FAILED
    assert row["failure_class"] == "publish_exception"
    assert row["failure_code"] == "ValueError"
    assert row["metadata_json"]["layer_type"] == "translation"


# ---------------------------------------------------------------------------
# Test 8: LangSmithIdBridgeProcessor.on_end extracts IDs into ContextVar
# ---------------------------------------------------------------------------


def test_langsmith_id_bridge_processor_extracts_ids() -> None:
    clear_langsmith_ids()
    assert get_current_langsmith_ids() is None

    processor = LangSmithIdBridgeProcessor()
    fake_span = _FakeReadableSpan(
        attributes={
            "langsmith.trace.id": "trace-abc",
            "langsmith.span.id": "span-def",
            "gen_ai.request.model": "gpt-4-test",
        }
    )

    processor.on_end(fake_span)  # type: ignore[arg-type]

    ids = get_current_langsmith_ids()
    assert ids is not None
    assert ids.trace_id == "trace-abc"
    assert ids.span_id == "span-def"
    assert ids.run_id == "trace-abc/span-def"

    # Reset for other tests
    clear_langsmith_ids()
    assert get_current_langsmith_ids() is None


def test_langsmith_id_bridge_processor_ignores_non_langsmith_spans() -> None:
    clear_langsmith_ids()

    processor = LangSmithIdBridgeProcessor()
    fake_span = _FakeReadableSpan(
        attributes={
            "gen_ai.request.model": "gpt-4-test",
            # no langsmith.* attributes
        }
    )

    processor.on_end(fake_span)  # type: ignore[arg-type]
    assert get_current_langsmith_ids() is None


# ---------------------------------------------------------------------------
# Test 9: end_span auto-backfills langsmith_run_id from ContextVar
# ---------------------------------------------------------------------------


async def test_end_span_auto_backfills_langsmith_run_id(
    span_recorder_env: tuple[asyncpg.Pool, ReaderSpanRecorder],
) -> None:
    pool, recorder = span_recorder_env

    # Simulate that a LangSmith-managed LLM span just ended in this
    # async context (set by LangSmithIdBridgeProcessor.on_end).
    _CURRENT_LANGSMITH_IDS.set(
        LangSmithIds(trace_id="trace-t", span_id="span-s")
    )

    span = await recorder.start_span(
        trace_id=uuid4(),
        span_kind=SPAN_KIND_PIPELINE_ROOT,
    )
    # Caller does NOT pass langsmith_run_id; end_span should auto-backfill.
    await recorder.end_span(span, status=STATUS_SUCCEEDED)

    row = await _fetch_span_row(pool, span.span_id)
    assert row["langsmith_run_id"] == "trace-t/span-s"


# ---------------------------------------------------------------------------
# Test 10: explicit langsmith_run_id wins over ContextVar
# ---------------------------------------------------------------------------


async def test_end_span_explicit_langsmith_run_id_wins(
    span_recorder_env: tuple[asyncpg.Pool, ReaderSpanRecorder],
) -> None:
    pool, recorder = span_recorder_env

    _CURRENT_LANGSMITH_IDS.set(
        LangSmithIds(trace_id="trace-ctx", span_id="span-ctx")
    )

    span = await recorder.start_span(
        trace_id=uuid4(),
        span_kind=SPAN_KIND_PIPELINE_ROOT,
    )
    await recorder.end_span(
        span,
        status=STATUS_SUCCEEDED,
        langsmith_run_id="explicit-trace/explicit-span",
    )

    row = await _fetch_span_row(pool, span.span_id)
    assert row["langsmith_run_id"] == "explicit-trace/explicit-span"
