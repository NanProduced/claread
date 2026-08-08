"""Semantic outline processed outcomes close their worker span.

Every enhancement worker (translation / vocabulary / grammar / display_title)
ends its own ``worker_tick`` span inside ``process_claimed_*_job`` via the
``end_worker_span_*`` helpers. The semantic outline worker does the same, so a
semantic_outline tick that actually processed a job never leaves its
``worker_tick`` row stuck at ``status='started'`` (a dangling-span leak).

These tests drive the semantic outline worker under an active ``worker_tick``
span (exactly how ``ReaderEnhancementPipelineRunner._run_worker_attempt`` wraps
``_dispatch_worker_attempt`` in ``use_span``) and assert the terminal span state
for each processed-job outcome:

- succeeded          -> worker_tick ``succeeded`` + ``ended_at`` non-empty
- retryable failure  -> worker_tick ``failed`` (ended once)
- terminal failure   -> worker_tick ``failed`` (ended once)
- fence / superseded -> worker_tick ``superseded`` (ended once)

The ``no_job`` and uncaught-exception convergence is owned by the pipeline
runner (not the worker) and already terminates the span correctly; the last two
tests lock that existing behavior so the worker-side change cannot double-end
those spans.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.database import connection as db_connection
from app.observability.langsmith_span_processor import clear_langsmith_ids
from app.services.ai_usage import CAPABILITY_READER_SEMANTIC_OUTLINE
from app.services.reader_orchestration.job_runtime import FenceViolationError
from app.services.reader_orchestration.pipeline_runner import (
    ReaderEnhancementPipelineRunner,
)
from app.services.reader_orchestration.semantic_outline_publisher import (
    SemanticOutlineCandidateNode,
)
from app.services.reader_orchestration.semantic_outline_worker import (
    FakeSemanticOutlineGenerator,
    SemanticOutlineWorkerService,
)
from app.services.reader_orchestration.span_recorder import (
    SPAN_KIND_WORKER_TICK,
    STATUS_FAILED,
    STATUS_SKIPPED,
    STATUS_SUCCEEDED,
    STATUS_SUPERSEDED,
    ReaderSpanRecorder,
    set_default_recorder,
)
from tests.reader_orchestration_test_support import (
    BASELINE_SQL,
    connect_admin,
    insert_user,
    make_pool,
    submit_article_ready,
)
from tests.test_reader_semantic_outline_worker import _bootstrap_outline

pytestmark = pytest.mark.anyio


@pytest.fixture
async def outline_span_env() -> AsyncIterator[tuple[asyncpg.Pool, ReaderSpanRecorder]]:
    """Isolated schema + pool + default span recorder for span-closure tests."""

    schema_name = f"test_semantic_outline_span_{uuid4().hex}"
    admin_conn = await connect_admin()
    original_pool = db_connection.DB_POOL
    pool: asyncpg.Pool | None = None
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)
        pool = await make_pool(schema_name)
        db_connection.DB_POOL = pool
        recorder = ReaderSpanRecorder(pool=pool)
        set_default_recorder(recorder)
        try:
            yield pool, recorder
        finally:
            set_default_recorder(None)
            clear_langsmith_ids()
    finally:
        db_connection.DB_POOL = original_pool
        if pool is not None:
            await pool.close()
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


async def _fetch_span(pool: asyncpg.Pool, span_id: UUID) -> asyncpg.Record:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM reader_runtime_spans WHERE id = $1", span_id
        )
    assert row is not None, f"span row {span_id} not found"
    return row


async def _article_unit_ids(pool: asyncpg.Pool, record_id: UUID) -> tuple[str, str]:
    async with pool.acquire() as conn:
        units = await conn.fetch(
            "SELECT unit_id FROM reading_units "
            "WHERE reading_record_id = $1 ORDER BY order_index",
            record_id,
        )
    assert len(units) >= 1
    return units[0]["unit_id"], units[-1]["unit_id"]


def _valid_candidates(start_uid: str, end_uid: str) -> tuple[SemanticOutlineCandidateNode, ...]:
    return (
        SemanticOutlineCandidateNode(
            candidate_ref="c_root",
            parent_candidate_ref=None,
            depth=1,
            title="Chapter One",
            start_unit_id=start_uid,
            end_unit_id=end_uid,
        ),
        SemanticOutlineCandidateNode(
            candidate_ref="c_child",
            parent_candidate_ref="c_root",
            depth=2,
            title="Detail",
            start_unit_id=start_uid,
            end_unit_id=start_uid,
        ),
    )


async def _run_outline_under_worker_tick_span(
    pool: asyncpg.Pool,
    recorder: ReaderSpanRecorder,
    *,
    service: SemanticOutlineWorkerService,
) -> tuple[UUID, object]:
    """Start a worker_tick span, run the outline worker inside it, return
    (span_id, result). Mirrors ``_run_worker_attempt``'s ``use_span`` wrapper.

    A ``FenceViolationError`` is allowed to propagate after the worker ends its
    own span (mirrors display_title_worker); it is returned as a sentinel so the
    caller can still assert the span terminal state.
    """

    span_ctx = await recorder.start_span(
        trace_id=uuid4(),
        span_kind=SPAN_KIND_WORKER_TICK,
        worker_type="semantic_outline",
    )
    async with recorder.use_span(span_ctx):
        try:
            result = await service.process_next_semantic_outline_job(
                lease_owner="outline-span-closure",
                lease_duration=timedelta(seconds=30),
            )
        except FenceViolationError:
            result = "fence_violation_raised"
    return span_ctx.span_id, result


async def _seed_ready_article_with_outline_job(
    pool: asyncpg.Pool,
) -> tuple[UUID, UUID]:
    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool,
        user_id=user_id,
        plain_text="First paragraph.\n\nSecond paragraph.",
    )
    await _bootstrap_outline(pool, record_id=article.record_id, user_id=user_id)
    return article.record_id, article.base_id


# ---------------------------------------------------------------------------
# Semantic outline processed outcomes close their worker span
# ---------------------------------------------------------------------------


async def test_semantic_outline_success_closes_worker_tick_span(
    outline_span_env: tuple[asyncpg.Pool, ReaderSpanRecorder],
) -> None:
    pool, recorder = outline_span_env
    record_id, _base_id = await _seed_ready_article_with_outline_job(pool)
    start_uid, end_uid = await _article_unit_ids(pool, record_id)
    service = SemanticOutlineWorkerService(
        pool=pool,
        generator=FakeSemanticOutlineGenerator(_valid_candidates(start_uid, end_uid)),
    )

    span_id, result = await _run_outline_under_worker_tick_span(
        pool, recorder, service=service
    )

    assert result is not None
    assert getattr(result, "status", None) == "succeeded"

    row = await _fetch_span(pool, span_id)
    assert row["status"] == STATUS_SUCCEEDED, (
        f"semantic_outline success must end worker_tick as succeeded, "
        f"got status={row['status']!r} (started => dangling span leak)"
    )
    assert row["ended_at"] is not None
    assert row["duration_ms"] is not None
    assert row["capability_code"] == CAPABILITY_READER_SEMANTIC_OUTLINE


async def test_semantic_outline_retryable_failure_closes_worker_tick_span(
    outline_span_env: tuple[asyncpg.Pool, ReaderSpanRecorder],
) -> None:
    pool, recorder = outline_span_env
    record_id, _base_id = await _seed_ready_article_with_outline_job(pool)
    # Empty candidates -> publish yields V=0 / not_published; attempt < max and
    # no worker_failure -> the job is parked as retry_later (retryable failure).
    service = SemanticOutlineWorkerService(
        pool=pool,
        generator=FakeSemanticOutlineGenerator(()),
    )

    span_id, result = await _run_outline_under_worker_tick_span(
        pool, recorder, service=service
    )

    assert result is not None
    assert getattr(result, "status", None) == "retry_later"

    row = await _fetch_span(pool, span_id)
    assert row["status"] == STATUS_FAILED, (
        f"semantic_outline retryable failure must end worker_tick as failed, "
        f"got status={row['status']!r} (started => dangling span leak)"
    )
    assert row["ended_at"] is not None


async def test_semantic_outline_terminal_failure_closes_worker_tick_span(
    outline_span_env: tuple[asyncpg.Pool, ReaderSpanRecorder],
) -> None:
    pool, recorder = outline_span_env
    await _seed_ready_article_with_outline_job(pool)
    # No generator injected -> fail-closed UnconfiguredSemanticOutlineGenerator
    # raises a permanent (retryable=False) SemanticOutlineGenerationError ->
    # failed_terminal.
    service = SemanticOutlineWorkerService(pool=pool)

    span_id, result = await _run_outline_under_worker_tick_span(
        pool, recorder, service=service
    )

    assert result is not None
    assert getattr(result, "status", None) == "failed_terminal"

    row = await _fetch_span(pool, span_id)
    assert row["status"] == STATUS_FAILED, (
        f"semantic_outline terminal failure must end worker_tick as failed, "
        f"got status={row['status']!r} (started => dangling span leak)"
    )
    assert row["ended_at"] is not None


class _FenceViolatingOutlinePublisher:
    """Publisher double that always fails the publish fence."""

    async def publish_from_candidates(self, **_kwargs) -> object:
        raise FenceViolationError("outline publish fence failed")


async def test_semantic_outline_fence_violation_closes_worker_tick_span(
    outline_span_env: tuple[asyncpg.Pool, ReaderSpanRecorder],
) -> None:
    pool, recorder = outline_span_env
    record_id, _base_id = await _seed_ready_article_with_outline_job(pool)
    start_uid, end_uid = await _article_unit_ids(pool, record_id)
    service = SemanticOutlineWorkerService(
        pool=pool,
        generator=FakeSemanticOutlineGenerator(_valid_candidates(start_uid, end_uid)),
        publisher=_FenceViolatingOutlinePublisher(),  # type: ignore[arg-type]
    )

    span_id, result = await _run_outline_under_worker_tick_span(
        pool, recorder, service=service
    )

    assert result == "fence_violation_raised"

    row = await _fetch_span(pool, span_id)
    assert row["status"] == STATUS_SUPERSEDED, (
        f"semantic_outline fence violation must end worker_tick as superseded, "
        f"got status={row['status']!r} (started => dangling span leak)"
    )
    assert row["ended_at"] is not None
    assert row["failure_class"] == "publish_fence"


# ---------------------------------------------------------------------------
# no_job / uncaught-exception convergence is owned by the pipeline runner and
# already terminates the span. These lock the existing behavior and ensure the
# worker-side change never double-ends those spans.
# ---------------------------------------------------------------------------


async def test_semantic_outline_no_job_converges_skipped_once(
    outline_span_env: tuple[asyncpg.Pool, ReaderSpanRecorder],
) -> None:
    pool, recorder = outline_span_env
    # No outline job bootstrapped -> the worker finds nothing to claim.
    runner = ReaderEnhancementPipelineRunner(
        pool=pool,
        semantic_outline_worker_service=SemanticOutlineWorkerService(pool=pool),
    )

    attempt = await runner._run_worker_attempt(
        worker_type="semantic_outline",
        record_id=uuid4(),
        base_id=uuid4(),
        expected_generation=1,
        lease_owner="outline-no-job",
        lease_duration=timedelta(seconds=30),
        translation_retry_delay=timedelta(milliseconds=1),
        vocabulary_retry_delay=timedelta(milliseconds=1),
        grammar_retry_delay=timedelta(milliseconds=1),
        display_title_retry_delay=timedelta(milliseconds=1),
    )

    assert attempt.outcome == "no_job"
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT status, ended_at FROM reader_runtime_spans "
            "WHERE span_kind = $1 AND worker_type = 'semantic_outline'",
            SPAN_KIND_WORKER_TICK,
        )
    assert len(rows) == 1
    assert rows[0]["status"] == STATUS_SKIPPED
    assert rows[0]["ended_at"] is not None


class _ExplodingOutlineService:
    """Outline worker double that raises an uncaught, non-fence exception."""

    async def process_next_semantic_outline_job_for_record(self, **_kwargs) -> object:
        raise RuntimeError("outline worker exploded")


async def test_semantic_outline_uncaught_exception_converges_failed_once(
    outline_span_env: tuple[asyncpg.Pool, ReaderSpanRecorder],
) -> None:
    pool, recorder = outline_span_env
    runner = ReaderEnhancementPipelineRunner(
        pool=pool,
        semantic_outline_worker_service=_ExplodingOutlineService(),  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="exploded"):
        await runner._run_worker_attempt(
            worker_type="semantic_outline",
            record_id=uuid4(),
            base_id=uuid4(),
            expected_generation=1,
            lease_owner="outline-exception",
            lease_duration=timedelta(seconds=30),
            translation_retry_delay=timedelta(milliseconds=1),
            vocabulary_retry_delay=timedelta(milliseconds=1),
            grammar_retry_delay=timedelta(milliseconds=1),
            display_title_retry_delay=timedelta(milliseconds=1),
        )

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT status, ended_at, failure_class FROM reader_runtime_spans "
            "WHERE span_kind = $1 AND worker_type = 'semantic_outline'",
            SPAN_KIND_WORKER_TICK,
        )
    assert len(rows) == 1
    assert rows[0]["status"] == STATUS_FAILED
    assert rows[0]["ended_at"] is not None
    assert rows[0]["failure_class"] == "worker_exception"
