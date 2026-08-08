"""LangSmith run id is owned by exactly one worker_tick.

``_CURRENT_LANGSMITH_IDS`` holds the LangSmith trace/span id captured when a
PydanticAI LLM span ends. Within one execution context, that id must reach
exactly one ``reader_runtime_spans`` row — the ``worker_tick`` that owns the
LLM call — and must not leak into any other span.

Worker-tick LangSmith ownership contract:

- only the ``worker_tick`` that owns the LLM call consumes the id (explicitly);
- the id is cleared before the attempt starts and reset after it ends
  (success / failure / exception);
- the generic ``end_span`` does NOT auto-consume the ContextVar, so a
  ``publish_fence`` / ``claim`` / ``no_job`` / ``pipeline_root`` span or the
  next ``worker_tick`` never inherits a stale id.

This file also records why a *generic read-then-clear* inside ``end_span`` is
wrong: after the model call the ``publish_fence`` span normally ends before the
``worker_tick`` span, so a first-read-clear would misattribute the id to the
fence and starve the real owner.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from uuid import uuid4

import asyncpg
import pytest

from app.observability.langsmith_span_processor import (
    _CURRENT_LANGSMITH_IDS,
    LangSmithIds,
    clear_langsmith_ids,
    get_current_langsmith_ids,
)
from app.services.reader_orchestration.pipeline_runner import (
    ReaderEnhancementPipelineRunner,
)
from app.services.reader_orchestration.semantic_outline_worker import (
    SemanticOutlineWorkerService,
)
from app.services.reader_orchestration.span_recorder import (
    SPAN_KIND_CLAIM,
    SPAN_KIND_PIPELINE_ROOT,
    SPAN_KIND_PUBLISH_FENCE,
    SPAN_KIND_WORKER_TICK,
    STATUS_FAILED,
    STATUS_SUCCEEDED,
    ReaderSpanRecorder,
    end_worker_span_execution_error,
    end_worker_span_success,
    set_default_recorder,
)
from tests.reader_orchestration_test_support import (
    BASELINE_SQL,
    connect_admin,
    make_pool,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
async def langsmith_owner_env() -> AsyncIterator[tuple[asyncpg.Pool, ReaderSpanRecorder]]:
    schema_name = f"test_langsmith_single_owner_{uuid4().hex}"
    admin_conn = await connect_admin()
    pool: asyncpg.Pool | None = None
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)
        pool = await make_pool(schema_name)
        recorder = ReaderSpanRecorder(pool=pool)
        set_default_recorder(recorder)
        clear_langsmith_ids()
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


async def _fetch_span(pool: asyncpg.Pool, span_id):
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM reader_runtime_spans WHERE id = $1", span_id
        )
    assert row is not None
    return row


def _set_ids(trace: str, span: str) -> None:
    _CURRENT_LANGSMITH_IDS.set(LangSmithIds(trace_id=trace, span_id=span))


# ---------------------------------------------------------------------------
# Non-owner spans must NOT inherit a stale LangSmith id
# ---------------------------------------------------------------------------


async def test_publish_fence_does_not_inherit_stale_langsmith_id(
    langsmith_owner_env: tuple[asyncpg.Pool, ReaderSpanRecorder],
) -> None:
    pool, recorder = langsmith_owner_env
    # An LLM span ended earlier in this context; the fence ends afterwards.
    _set_ids("trace-stale", "span-stale")

    fence = await recorder.start_span(
        trace_id=uuid4(), span_kind=SPAN_KIND_PUBLISH_FENCE
    )
    await recorder.end_span(fence, status=STATUS_SUCCEEDED)

    row = await _fetch_span(pool, fence.span_id)
    assert row["langsmith_run_id"] is None, (
        f"publish_fence must not inherit stale LangSmith id; "
        f"got {row['langsmith_run_id']!r}"
    )


async def test_claim_and_root_do_not_inherit_stale_langsmith_id(
    langsmith_owner_env: tuple[asyncpg.Pool, ReaderSpanRecorder],
) -> None:
    pool, recorder = langsmith_owner_env
    _set_ids("trace-stale", "span-stale")

    claim = await recorder.start_span(trace_id=uuid4(), span_kind=SPAN_KIND_CLAIM)
    await recorder.end_span(claim, status=STATUS_SUCCEEDED)
    root = await recorder.start_span(
        trace_id=uuid4(), span_kind=SPAN_KIND_PIPELINE_ROOT
    )
    await recorder.end_span(root, status=STATUS_SUCCEEDED)

    claim_row = await _fetch_span(pool, claim.span_id)
    root_row = await _fetch_span(pool, root.span_id)
    assert claim_row["langsmith_run_id"] is None
    assert root_row["langsmith_run_id"] is None


# ---------------------------------------------------------------------------
# Only the owning worker_tick consumes the id, and it clears the ContextVar
# ---------------------------------------------------------------------------


async def test_owning_worker_tick_success_consumes_and_clears_langsmith_id(
    langsmith_owner_env: tuple[asyncpg.Pool, ReaderSpanRecorder],
) -> None:
    pool, recorder = langsmith_owner_env
    _set_ids("trace-owner", "span-owner")

    tick = await recorder.start_span(
        trace_id=uuid4(), span_kind=SPAN_KIND_WORKER_TICK, worker_type="translation"
    )
    async with recorder.use_span(tick):
        await end_worker_span_success(
            ai_usage_event_id=None,
            usage_data=None,
            model_route=None,
            model_name=None,
            model_provider=None,
            capability_code="reader_translation",
        )

    row = await _fetch_span(pool, tick.span_id)
    assert row["langsmith_run_id"] == "trace-owner/span-owner"
    # Single-owner contract: the owner consumed the id; nothing may leak on.
    assert get_current_langsmith_ids() is None, (
        "owning worker_tick must clear the LangSmith ContextVar after consuming"
    )


async def test_owning_worker_tick_failure_consumes_and_clears_langsmith_id(
    langsmith_owner_env: tuple[asyncpg.Pool, ReaderSpanRecorder],
) -> None:
    pool, recorder = langsmith_owner_env
    _set_ids("trace-owner-fail", "span-owner-fail")

    tick = await recorder.start_span(
        trace_id=uuid4(), span_kind=SPAN_KIND_WORKER_TICK, worker_type="translation"
    )
    async with recorder.use_span(tick):
        await end_worker_span_execution_error(
            failure_class="translation_timeout", failure_code="Timeout"
        )

    row = await _fetch_span(pool, tick.span_id)
    assert row["status"] == STATUS_FAILED
    assert row["langsmith_run_id"] == "trace-owner-fail/span-owner-fail"
    assert get_current_langsmith_ids() is None


async def test_next_worker_tick_does_not_inherit_previous_langsmith_id(
    langsmith_owner_env: tuple[asyncpg.Pool, ReaderSpanRecorder],
) -> None:
    pool, recorder = langsmith_owner_env

    # Tick 1 owns an LLM call.
    _set_ids("trace-tick1", "span-tick1")
    tick1 = await recorder.start_span(
        trace_id=uuid4(), span_kind=SPAN_KIND_WORKER_TICK, worker_type="translation"
    )
    async with recorder.use_span(tick1):
        await end_worker_span_success(
            ai_usage_event_id=None,
            usage_data=None,
            model_route=None,
            model_name=None,
            model_provider=None,
            capability_code="reader_translation",
        )

    # Tick 2 makes NO LLM call; it must not inherit tick 1's id.
    tick2 = await recorder.start_span(
        trace_id=uuid4(), span_kind=SPAN_KIND_WORKER_TICK, worker_type="translation"
    )
    async with recorder.use_span(tick2):
        await end_worker_span_success(
            ai_usage_event_id=None,
            usage_data=None,
            model_route=None,
            model_name=None,
            model_provider=None,
            capability_code="reader_translation",
        )

    row1 = await _fetch_span(pool, tick1.span_id)
    row2 = await _fetch_span(pool, tick2.span_id)
    assert row1["langsmith_run_id"] == "trace-tick1/span-tick1"
    assert row2["langsmith_run_id"] is None, (
        f"next worker_tick must not inherit previous LangSmith id; "
        f"got {row2['langsmith_run_id']!r}"
    )


# ---------------------------------------------------------------------------
# Pipeline attempt boundary clears a stale id (crash recovery path)
# ---------------------------------------------------------------------------


async def test_attempt_boundary_clears_stale_langsmith_id(
    langsmith_owner_env: tuple[asyncpg.Pool, ReaderSpanRecorder],
) -> None:
    pool, recorder = langsmith_owner_env
    # Simulate a stale id left behind by a crashed previous attempt.
    _set_ids("trace-crash-leftover", "span-crash-leftover")

    runner = ReaderEnhancementPipelineRunner(
        pool=pool,
        semantic_outline_worker_service=SemanticOutlineWorkerService(pool=pool),
    )
    attempt = await runner._run_worker_attempt(
        worker_type="semantic_outline",
        record_id=uuid4(),
        base_id=uuid4(),
        expected_generation=1,
        lease_owner="outline-stale-clear",
        lease_duration=timedelta(seconds=30),
        translation_retry_delay=timedelta(milliseconds=1),
        vocabulary_retry_delay=timedelta(milliseconds=1),
        grammar_retry_delay=timedelta(milliseconds=1),
        display_title_retry_delay=timedelta(milliseconds=1),
    )

    assert attempt.outcome == "no_job"
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT langsmith_run_id FROM reader_runtime_spans "
            "WHERE span_kind = $1 AND worker_type = 'semantic_outline'",
            SPAN_KIND_WORKER_TICK,
        )
    assert rows, "expected one semantic_outline worker_tick span"
    for row in rows:
        assert row["langsmith_run_id"] is None, (
            f"no_job span must not inherit stale LangSmith id; "
            f"got {row['langsmith_run_id']!r}"
        )
    # The attempt boundary must have cleared the stale id so the next attempt
    # (or its owner) cannot consume it.
    assert get_current_langsmith_ids() is None


# ---------------------------------------------------------------------------
# Guards: behavior that must hold both before and after the fix
# ---------------------------------------------------------------------------


async def test_no_llm_span_keeps_langsmith_run_id_null(
    langsmith_owner_env: tuple[asyncpg.Pool, ReaderSpanRecorder],
) -> None:
    pool, recorder = langsmith_owner_env
    clear_langsmith_ids()

    tick = await recorder.start_span(
        trace_id=uuid4(), span_kind=SPAN_KIND_WORKER_TICK, worker_type="translation"
    )
    async with recorder.use_span(tick):
        await end_worker_span_success(
            ai_usage_event_id=None,
            usage_data=None,
            model_route=None,
            model_name=None,
            model_provider=None,
            capability_code="reader_translation",
        )
    fence = await recorder.start_span(
        trace_id=uuid4(), span_kind=SPAN_KIND_PUBLISH_FENCE
    )
    await recorder.end_span(fence, status=STATUS_SUCCEEDED)

    assert (await _fetch_span(pool, tick.span_id))["langsmith_run_id"] is None
    assert (await _fetch_span(pool, fence.span_id))["langsmith_run_id"] is None


async def test_explicit_langsmith_run_id_still_written(
    langsmith_owner_env: tuple[asyncpg.Pool, ReaderSpanRecorder],
) -> None:
    pool, recorder = langsmith_owner_env
    # Even with a stale ContextVar present, an explicit id must win.
    _set_ids("trace-stale", "span-stale")

    span = await recorder.start_span(
        trace_id=uuid4(), span_kind=SPAN_KIND_WORKER_TICK
    )
    await recorder.end_span(
        span, status=STATUS_SUCCEEDED, langsmith_run_id="explicit-trace/explicit-span"
    )

    row = await _fetch_span(pool, span.span_id)
    assert row["langsmith_run_id"] == "explicit-trace/explicit-span"


# ---------------------------------------------------------------------------
# Evidence: why the FORBIDDEN generic read-then-clear approach misattributes
# ---------------------------------------------------------------------------


async def test_read_then_clear_would_let_publish_fence_steal_id(
    langsmith_owner_env: tuple[asyncpg.Pool, ReaderSpanRecorder],
) -> None:
    """Demonstrate the forbidden alternative.

    If ``end_span`` did a generic read-then-clear of the ContextVar, the span
    that ends FIRST after the LLM call — the ``publish_fence`` — would consume
    the id, leaving the real owner (the ``worker_tick`` that ends second) with
    nothing. This is why consumption must be explicit and owner-scoped instead.
    """

    _set_ids("trace-contested", "span-contested")

    def _generic_read_then_clear() -> str | None:
        ids = get_current_langsmith_ids()
        clear_langsmith_ids()
        return ids.run_id if ids is not None else None

    # publish_fence ends first, worker_tick ends second.
    fence_id = _generic_read_then_clear()
    tick_id = _generic_read_then_clear()

    assert fence_id == "trace-contested/span-contested", (
        "under read-then-clear the earlier-ending fence steals the id"
    )
    assert tick_id is None, (
        "under read-then-clear the real owner is starved of its own id"
    )
