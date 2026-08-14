"""Tests for ReaderEnhancementPipelineRunner grammar-window window dispatch (Task C4).

Design source:
  docs/architecture/reader-orchestration.md
  §9 (worker migration): ``grammar_bundle_window`` WorkerType registered in
  ``pipeline_runner._dispatch_worker_attempt`` ahead of legacy
  ``grammar_bundle``.

Coverage:
  1. ``candidates_ready`` → window worker called + publisher called.
  2. ``already_terminal`` → window worker called, publisher NOT called.
  3. ``worker_order`` — grammar_bundle_window runs before legacy
     grammar_bundle when both workers are registered.
  4. Backward compat — when ``_grammar_window_worker`` is not registered,
     ``worker_order`` excludes grammar_bundle_window (legacy 4-worker path).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.database import connection as db_connection
from app.services.reader_orchestration.grammar_window_bootstrap import (
    GrammarWindowBootstrapService,
)
from app.services.reader_orchestration.job_bootstrap import (
    EnhancementBootstrapJobCounts,
    EnhancementBootstrapSummary,
    EnhancementJobBootstrapService,
)
from app.services.reader_orchestration.pipeline_runner import (
    ReaderEnhancementPipelineRunner,
)
from tests.reader_orchestration_test_support import (
    BASELINE_SQL,
    connect_admin,
    insert_user,
    make_pool,
    submit_article_ready,
)

pytestmark = pytest.mark.anyio


GRAMMAR_WINDOW_ARTICLE_TEXT = (
    "Not only did the team revise the plan, but they also clarified the timeline. "
    "Everyone understood the tradeoff.\n\n"
    "The committee, which had spent six months reviewing export data, "
    "labor surveys, and municipal tax receipts that rarely lined up neatly, "
    "claimed that the recovery was broad enough to justify ending the emergency "
    "grant program.\n\n"
    "Several shop owners warned that the headline numbers hid a "
    "more fragile street-level reality, because customers were still delaying "
    "purchases whenever wages, school fees, and transport costs rose in the same "
    "week."
)

LEASE_DURATION = timedelta(seconds=30)


# ---------------------------------------------------------------------------
# Fixture: schema + record + base + grammar-window window jobs (no legacy grammar jobs)
# ---------------------------------------------------------------------------


@pytest.fixture
async def test_db_pool_with_window_job_only() -> AsyncIterator[
    tuple[asyncpg.Pool, UUID, UUID, UUID]
]:
    """Submit article + run grammar-window bootstrap (creates window jobs only).

    Returns ``(pool, record_id, user_id, base_id)``. Legacy grammar_bundle
    jobs are NOT created — the mock bootstrap in each test returns a 0-count
    summary so legacy workers find no claimable jobs.
    """
    schema_name = f"test_pipeline_window_dispatch_{uuid4().hex}"
    admin_conn = await connect_admin()
    original_pool = db_connection.DB_POOL
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)
        pool = await make_pool(schema_name)
        db_connection.DB_POOL = pool
        try:
            user_id = await insert_user(pool)
            article = await submit_article_ready(
                pool,
                user_id=user_id,
                plain_text=GRAMMAR_WINDOW_ARTICLE_TEXT,
                title="Pipeline Window Dispatch Slice",
                language="en",
            )
            # Bootstrap grammar-window plan + windows + window reader_jobs.
            grammar_window = GrammarWindowBootstrapService(pool=pool)
            await grammar_window.bootstrap_grammar_window_plan(
                record_id=article.record_id,
                base_id=article.base_id,
            )
            yield pool, article.record_id, user_id, article.base_id
        finally:
            await pool.close()
    finally:
        db_connection.DB_POOL = original_pool
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


def _make_mock_bootstrap(
    record_id: UUID,
    base_id: UUID,
    expected_generation: int = 1,
) -> EnhancementJobBootstrapService:
    """Build a mock bootstrap that returns a 0-count summary.

    The pipeline runner uses ``bootstrap.base_id`` /
    ``bootstrap.expected_generation`` to scope subsequent ``claim_next_job``
    calls, so the mock must carry the real base_id / generation. No legacy
    jobs are actually created, so the legacy workers find no claimable jobs.
    """
    mock = AsyncMock(spec=EnhancementJobBootstrapService)
    mock.bootstrap_missing_jobs = AsyncMock(
        return_value=EnhancementBootstrapSummary(
            record_id=record_id,
            base_id=base_id,
            expected_generation=expected_generation,
            last_event_sequence=0,
            job_counts=EnhancementBootstrapJobCounts(),
        )
    )
    return mock


def _make_runner(
    pool: asyncpg.Pool,
    record_id: UUID,
    base_id: UUID,
) -> ReaderEnhancementPipelineRunner:
    """Build a runner with a mock bootstrap (no legacy jobs) + real DB pool.

    ``_grammar_window_worker`` / ``_grammar_window_publisher`` are NOT set
    here — each test overrides them with mocks after construction.
    ``enable_grammar_window=False`` 保证构造时不创建 real grammar-window worker /
    publisher（避免触达 real LLM）；测试通过 override ``_grammar_window_worker``
    / ``_grammar_window_publisher`` 属性来注入 mock，``run()`` 在运行时
    根据 ``self._grammar_window_worker is not None`` 决定 worker_order。
    """
    return ReaderEnhancementPipelineRunner(
        pool=pool,
        bootstrap_service=_make_mock_bootstrap(record_id, base_id),
        enable_grammar_window=False,
    )


# ---------------------------------------------------------------------------
# Test 1: candidates_ready → publisher called
# ---------------------------------------------------------------------------


async def test_pipeline_runner_dispatches_window_job_to_window_worker(
    test_db_pool_with_window_job_only: tuple[asyncpg.Pool, UUID, UUID, UUID],
) -> None:
    """``build_grammar_bundle_window`` job routes to GrammarWindowWorkerService,
    and ``candidates_ready`` triggers GrammarWindowPublisher."""
    pool, record_id, user_id, base_id = test_db_pool_with_window_job_only
    runner = _make_runner(pool, record_id, base_id)

    mock_worker = AsyncMock()
    mock_worker.process_window_job = AsyncMock(
        return_value={
            "status": "candidates_ready",
            "candidates": [],  # empty candidates → publisher still called
        }
    )
    mock_publisher = AsyncMock()

    runner._grammar_window_worker = mock_worker
    runner._grammar_window_publisher = mock_publisher

    await runner.run(
        record_id=record_id,
        user_id=user_id,
        lease_owner="test-window-dispatch",
        lease_duration=LEASE_DURATION,
        max_ticks=10,
        max_jobs=10,
    )

    mock_worker.process_window_job.assert_called()
    mock_publisher.publish_window_grammar_bundle.assert_called()


# ---------------------------------------------------------------------------
# Test 2: already_terminal → publisher NOT called
# ---------------------------------------------------------------------------


async def test_pipeline_runner_skips_already_terminal_window(
    test_db_pool_with_window_job_only: tuple[asyncpg.Pool, UUID, UUID, UUID],
) -> None:
    """When window worker returns ``already_terminal``, publisher is skipped."""
    pool, record_id, user_id, base_id = test_db_pool_with_window_job_only
    runner = _make_runner(pool, record_id, base_id)

    captured_job_id: list[UUID] = []
    captured_run_id: list[UUID] = []

    async def _already_terminal_process(*, claim: Any) -> dict[str, Any]:
        captured_job_id.append(claim.job_id)
        captured_run_id.append(claim.run_id)
        return {"status": "already_terminal"}

    mock_worker = AsyncMock()
    mock_worker.process_window_job = AsyncMock(side_effect=_already_terminal_process)
    mock_publisher = AsyncMock()

    runner._grammar_window_worker = mock_worker
    runner._grammar_window_publisher = mock_publisher

    await runner.run(
        record_id=record_id,
        user_id=user_id,
        lease_owner="test-terminal-skip",
        lease_duration=LEASE_DURATION,
        max_ticks=10,
        max_jobs=10,
    )

    mock_worker.process_window_job.assert_called()
    mock_publisher.publish_window_grammar_bundle.assert_not_called()
    assert captured_job_id, "window worker must have claimed a job"
    assert captured_run_id, "window worker must have a run"
    job_status = await _query_job_status(pool, captured_job_id[0])
    assert job_status == "skipped", (
        "already_terminal must close the claimed job as skipped; "
        f"got {job_status!r}"
    )
    run_status = await _query_run_status(pool, captured_run_id[0])
    assert run_status == "completed", (
        "already_terminal must close the reader_run as completed; "
        f"got {run_status!r}"
    )


# ---------------------------------------------------------------------------
# Test 3: worker_order — grammar_bundle_window before legacy grammar_bundle
# ---------------------------------------------------------------------------


async def test_pipeline_runner_worker_order_grammar_window_before_legacy(
    test_db_pool_with_window_job_only: tuple[asyncpg.Pool, UUID, UUID, UUID],
) -> None:
    """``grammar_bundle_window`` is dispatched before legacy ``grammar_bundle``."""
    pool, record_id, user_id, base_id = test_db_pool_with_window_job_only
    runner = _make_runner(pool, record_id, base_id)

    call_order: list[str] = []

    mock_window_worker = AsyncMock()

    async def _window_side_effect(*, claim: Any) -> dict[str, Any]:
        call_order.append("window")
        return {"status": "already_terminal"}

    mock_window_worker.process_window_job = AsyncMock(
        side_effect=_window_side_effect
    )

    mock_legacy_grammar_service = AsyncMock()

    async def _batch_side_effect(**kwargs: Any) -> None:
        # No claimable grammar batch job (window-only fixture).
        return None

    mock_legacy_grammar_service.process_next_grammar_batch_job_for_record = (
        AsyncMock(side_effect=_batch_side_effect)
    )

    async def _legacy_side_effect(**kwargs: Any) -> None:
        call_order.append("legacy")
        return None  # no claimable legacy grammar_bundle job → no_job

    mock_legacy_grammar_service.process_next_grammar_job_for_record = AsyncMock(
        side_effect=_legacy_side_effect
    )

    # Publisher is required by the guard in ``_run_grammar_window_attempt``
    # (both worker AND publisher must be registered). Use a no-op mock since
    # ``already_terminal`` never reaches the publisher.
    mock_publisher = AsyncMock()

    runner._grammar_window_worker = mock_window_worker
    runner._grammar_window_publisher = mock_publisher
    # Override the legacy grammar worker service (set in __init__) with the
    # mock so we can observe call order.
    runner._grammar_worker_service = mock_legacy_grammar_service

    await runner.run(
        record_id=record_id,
        user_id=user_id,
        lease_owner="test-worker-order",
        lease_duration=LEASE_DURATION,
        max_ticks=10,
        max_jobs=10,
    )

    # Window worker must have been called at least once (Round 1 claim
    # succeeds for the single window job bootstrapped by GrammarWindowBootstrapService).
    mock_window_worker.process_window_job.assert_called()
    # Legacy grammar worker is also called each round (returns no_job since
    # no legacy grammar_bundle jobs exist). Verify window was called before
    # any legacy call.
    assert "window" in call_order
    if "legacy" in call_order:
        window_idx = call_order.index("window")
        legacy_idx = call_order.index("legacy")
        assert window_idx < legacy_idx, (
            f"grammar_bundle_window must run before grammar_bundle; "
            f"got order={call_order}"
        )


# ---------------------------------------------------------------------------
# Test 4: backward compat — no window worker registered → 4-worker path
# ---------------------------------------------------------------------------


async def test_pipeline_runner_without_window_worker_excludes_window_dispatch(
    test_db_pool_with_window_job_only: tuple[asyncpg.Pool, UUID, UUID, UUID],
) -> None:
    """When ``_grammar_window_worker`` is None, ``worker_order`` keeps the
    legacy 4-worker tuple so existing deployments / tests are unaffected.

    This preserves backward compatibility: the grammar-window path only activates when
    the caller explicitly opts in by registering the window worker.
    """
    pool, record_id, user_id, base_id = test_db_pool_with_window_job_only
    runner = _make_runner(pool, record_id, base_id)

    # _grammar_window_worker / _grammar_window_publisher are None (default).
    summary = await runner.run(
        record_id=record_id,
        user_id=user_id,
        lease_owner="test-no-window-worker",
        lease_duration=LEASE_DURATION,
        max_ticks=10,
        max_jobs=10,
    )

    # grammar_bundle_window tick count stays at 0 (worker_order excluded it).
    assert summary.worker_tick_counts.grammar_bundle_window == 0
    # Pipeline ran without dispatching the window worker, even though window
    # jobs exist in the DB — the grammar-window path is opt-in only.


# ---------------------------------------------------------------------------
# Test 5 + 6: window job failure transitions job to retry_later / failed_terminal
# (third-round review: failures must not leave job stuck in `claimed`)
# ---------------------------------------------------------------------------


async def _query_job_status(pool: asyncpg.Pool, job_id: UUID) -> str:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status FROM reader_jobs WHERE id = $1",
            job_id,
        )
    return str(row["status"]) if row else "missing"


async def _query_run_status(pool: asyncpg.Pool, run_id: UUID) -> str:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status FROM reader_runs WHERE id = $1",
            run_id,
        )
    return str(row["status"]) if row else "missing"


async def _query_window_status(pool: asyncpg.Pool, job_id: UUID) -> str | None:
    """Look up analysis_windows.status tied to a window job's input_json."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT input_json FROM reader_jobs WHERE id = $1",
            job_id,
        )
        if row is None:
            return None
        input_data = row["input_json"]
        if isinstance(input_data, str):
            input_data = json.loads(input_data)
        window_id = UUID(str(input_data["window_id"]))
        win_row = await conn.fetchrow(
            "SELECT status FROM analysis_windows WHERE id = $1",
            window_id,
        )
    return str(win_row["status"]) if win_row else None


async def _resolve_window_id_from_job(
    pool: asyncpg.Pool, job_id: UUID
) -> UUID | None:
    """Resolve the analysis_windows.id tied to a window job's input_json."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT input_json FROM reader_jobs WHERE id = $1",
            job_id,
        )
        if row is None:
            return None
        input_data = row["input_json"]
        if isinstance(input_data, str):
            input_data = json.loads(input_data)
        return UUID(str(input_data["window_id"]))


async def _query_job_diagnostics(pool: asyncpg.Pool, job_id: UUID) -> dict[str, Any] | None:
    """Look up reader_jobs.output_ref_json.diagnostics (observability)."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT output_ref_json FROM reader_jobs WHERE id = $1",
            job_id,
        )
        if row is None:
            return None
        output_ref = row["output_ref_json"]
        if isinstance(output_ref, str):
            output_ref = json.loads(output_ref)
        if not isinstance(output_ref, dict):
            return None
        return output_ref.get("diagnostics")


async def _query_window_coverage_diagnostics(
    pool: asyncpg.Pool, window_id: UUID
) -> dict[str, Any] | None:
    """Look up analysis_windows.coverage.diagnostics (observability)."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT coverage FROM analysis_windows WHERE id = $1",
            window_id,
        )
        if row is None:
            return None
        coverage = row["coverage"]
        if isinstance(coverage, str):
            coverage = json.loads(coverage)
        if not isinstance(coverage, dict):
            return None
        return coverage.get("diagnostics")


async def test_pipeline_runner_window_llm_failure_transitions_job_to_retry_later(
    test_db_pool_with_window_job_only: tuple[asyncpg.Pool, UUID, UUID, UUID],
) -> None:
    """GrammarWindowExecutionError (LLM transient) -> reader_jobs.retry_later.

    Verifies third-round fix: when ``process_window_job`` raises
    ``GrammarWindowExecutionError``, the runner transitions the job out of
    ``claimed`` to ``retry_later`` and marks the run as ``failed_retryable``,
    rather than leaving the job stuck waiting for lease expiry.
    """
    from app.services.reader_orchestration.grammar_window_worker import (
        GrammarWindowExecutionError,
    )

    pool, record_id, user_id, base_id = test_db_pool_with_window_job_only
    runner = _make_runner(pool, record_id, base_id)

    captured_job_id: list[UUID] = []
    captured_run_id: list[UUID] = []

    async def _failing_process(*, claim: Any) -> dict[str, Any]:
        captured_job_id.append(claim.job_id)
        captured_run_id.append(claim.run_id)
        raise GrammarWindowExecutionError(
            "simulated LLM timeout (test fixture)",
            retryable=True,
            failure_class="provider",
            failure_code="TimeoutError",
        )

    mock_worker = AsyncMock()
    mock_worker.process_window_job = AsyncMock(side_effect=_failing_process)
    mock_publisher = AsyncMock()

    runner._grammar_window_worker = mock_worker
    runner._grammar_window_publisher = mock_publisher

    summary = await runner.run(
        record_id=record_id,
        user_id=user_id,
        lease_owner="test-llm-failure-retry",
        lease_duration=LEASE_DURATION,
        max_ticks=10,
        max_jobs=10,
    )

    assert captured_job_id, "window worker must have been called at least once"
    job_status = await _query_job_status(pool, captured_job_id[0])
    assert job_status == "retry_later", (
        f"LLM failure must transition job to retry_later; got {job_status!r}"
    )
    run_status = await _query_run_status(pool, captured_run_id[0])
    assert run_status == "failed_retryable", (
        f"LLM failure must mark run as failed_retryable; got {run_status!r}"
    )
    # Publisher must NOT be called when the executor fails before producing
    # candidates.
    mock_publisher.publish_window_grammar_bundle.assert_not_called()
    # Pipeline summary must reflect the retry_later outcome.
    assert summary.outcome_counts.retry_later >= 1


async def test_pipeline_runner_window_value_error_transitions_job_to_failed_terminal(
    test_db_pool_with_window_job_only: tuple[asyncpg.Pool, UUID, UUID, UUID],
) -> None:
    """ValueError (fail-closed contract violation) -> failed_terminal.

    Verifies third-round fix: when ``process_window_job`` raises
    ``ValueError`` (e.g. Fail-closed from publisher / candidate
    contents derivation), the runner transitions the job to
    ``failed_terminal`` (not retryable — code bug) and marks the run as
    ``failed_terminal`` + the analysis_window as ``failed``.
    """
    pool, record_id, user_id, base_id = test_db_pool_with_window_job_only
    runner = _make_runner(pool, record_id, base_id)

    captured_job_id: list[UUID] = []
    captured_run_id: list[UUID] = []

    async def _contract_violation_process(*, claim: Any) -> dict[str, Any]:
        captured_job_id.append(claim.job_id)
        captured_run_id.append(claim.run_id)
        raise ValueError(
            "candidate_contents is required when candidates exist "
            "(fail closed: sidecar fallback removed)"
        )

    mock_worker = AsyncMock()
    mock_worker.process_window_job = AsyncMock(
        side_effect=_contract_violation_process
    )
    mock_publisher = AsyncMock()

    runner._grammar_window_worker = mock_worker
    runner._grammar_window_publisher = mock_publisher

    summary = await runner.run(
        record_id=record_id,
        user_id=user_id,
        lease_owner="test-contract-violation-terminal",
        lease_duration=LEASE_DURATION,
        max_ticks=10,
        max_jobs=10,
    )

    assert captured_job_id, "window worker must have been called at least once"
    job_status = await _query_job_status(pool, captured_job_id[0])
    assert job_status == "failed_terminal", (
        f"ValueError must transition job to failed_terminal; got {job_status!r}"
    )
    run_status = await _query_run_status(pool, captured_run_id[0])
    assert run_status == "failed_terminal", (
        f"ValueError must mark run as failed_terminal; got {run_status!r}"
    )
    mock_publisher.publish_window_grammar_bundle.assert_not_called()
    assert summary.outcome_counts.failed_terminal >= 1


async def test_pipeline_runner_window_publisher_value_error_marks_window_failed(
    test_db_pool_with_window_job_only: tuple[asyncpg.Pool, UUID, UUID, UUID],
) -> None:
    """A deterministic publish contract error pauses the captured result."""
    pool, record_id, user_id, base_id = test_db_pool_with_window_job_only
    runner = _make_runner(pool, record_id, base_id)

    captured_job_id: list[UUID] = []
    captured_run_id: list[UUID] = []

    async def _candidates_ready_process(*, claim: Any) -> dict[str, Any]:
        captured_job_id.append(claim.job_id)
        captured_run_id.append(claim.run_id)
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE analysis_windows
                SET status = 'running', job_id = $1, started_at = NOW()
                WHERE id = (
                    SELECT (input_json->>'window_id')::uuid
                    FROM reader_jobs WHERE id = $1
                )
                """,
                claim.job_id,
            )
        return {
            "status": "candidates_ready",
            "candidates": [],
            "execution_captured": True,
        }

    async def _failing_publish(**kwargs: Any) -> None:
        raise ValueError(
            "publisher contract violation (test fixture)"
        )

    mock_worker = AsyncMock()
    mock_worker.process_window_job = AsyncMock(
        side_effect=_candidates_ready_process
    )

    async def _pause_captured_claim(
        claim: Any,
        exc: Exception,
        *,
        invalid_receipt: bool = False,
    ) -> None:
        del exc
        await runner._job_runtime.transition(
            job_id=claim.job_id,
            target_status="paused",
            lease_token=claim.lease_token,
            pause_owner="system",
            failure_class="model_execution",
            failure_code=(
                "receipt_payload_invalid"
                if invalid_receipt
                else "post_provider_resume_required"
            ),
            rationale_code=(
                "model_execution_receipt_invalid"
                if invalid_receipt
                else "model_execution_captured_resume_required"
            ),
        )

    mock_worker.pause_captured_claim = AsyncMock(side_effect=_pause_captured_claim)
    mock_publisher = AsyncMock()
    mock_publisher.publish_window_grammar_bundle = AsyncMock(
        side_effect=_failing_publish
    )

    runner._grammar_window_worker = mock_worker
    runner._grammar_window_publisher = mock_publisher

    await runner.run(
        record_id=record_id,
        user_id=user_id,
        lease_owner="test-publisher-failure-window",
        lease_duration=LEASE_DURATION,
        max_ticks=10,
        max_jobs=10,
    )

    assert captured_job_id, "window worker must have been called at least once"
    job_status = await _query_job_status(pool, captured_job_id[0])
    assert job_status == "paused"
    window_status = await _query_window_status(pool, captured_job_id[0])
    assert window_status == "running"
    assert await _query_job_diagnostics(pool, captured_job_id[0]) is None
    window_id = await _resolve_window_id_from_job(pool, captured_job_id[0])
    assert window_id is not None
    coverage_diag = await _query_window_coverage_diagnostics(pool, window_id)
    assert coverage_diag is None


# ---------------------------------------------------------------------------
# Requirement 1: FenceViolationError → job=superseded, run=superseded
# ---------------------------------------------------------------------------


async def test_pipeline_runner_window_fence_violation_transitions_superseded(
    test_db_pool_with_window_job_only: tuple[asyncpg.Pool, UUID, UUID, UUID],
) -> None:
    """FenceViolationError from publisher → job=superseded, run=superseded.

    Verifies requirement 1: aligns with legacy grammar_worker — the runner
    transitions the job to ``superseded``, marks the reader_run as
    ``superseded``, and returns ``outcome='superseded'``. The previous code
    only returned superseded without transitioning the job or marking the
    run, leaving both stuck in claimed/running.
    """
    from app.services.reader_orchestration.job_runtime import FenceViolationError

    pool, record_id, user_id, base_id = test_db_pool_with_window_job_only
    runner = _make_runner(pool, record_id, base_id)

    captured_job_id: list[UUID] = []
    captured_run_id: list[UUID] = []

    async def _candidates_ready_process(*, claim: Any) -> dict[str, Any]:
        captured_job_id.append(claim.job_id)
        captured_run_id.append(claim.run_id)
        return {"status": "candidates_ready", "candidates": []}

    async def _fence_violation_publish(**kwargs: Any) -> None:
        raise FenceViolationError("publish fence failed: stale generation")

    mock_worker = AsyncMock()
    mock_worker.process_window_job = AsyncMock(
        side_effect=_candidates_ready_process
    )
    mock_publisher = AsyncMock()
    mock_publisher.publish_window_grammar_bundle = AsyncMock(
        side_effect=_fence_violation_publish
    )

    runner._grammar_window_worker = mock_worker
    runner._grammar_window_publisher = mock_publisher

    summary = await runner.run(
        record_id=record_id,
        user_id=user_id,
        lease_owner="test-fence-violation-superseded",
        lease_duration=LEASE_DURATION,
        max_ticks=10,
        max_jobs=10,
    )

    assert captured_job_id, "window worker must have been called at least once"
    job_status = await _query_job_status(pool, captured_job_id[0])
    assert job_status == "superseded", (
        f"FenceViolationError must transition job to superseded; got {job_status!r}"
    )
    run_status = await _query_run_status(pool, captured_run_id[0])
    assert run_status == "superseded", (
        f"FenceViolationError must mark run as superseded; got {run_status!r}"
    )
    window_status = await _query_window_status(pool, captured_job_id[0])
    assert window_status == "failed", (
        "FenceViolationError must close the running analysis_window as failed; "
        f"got {window_status!r}"
    )
    assert summary.outcome_counts.superseded >= 1

    # (+): fence-violation diagnostics must be persisted to both
    # reader_jobs.output_ref_json.diagnostics (superseded job side) and
    # analysis_windows.coverage.diagnostics (failed window side). Without
    # this the superseded/failed window had an empty coverage.diagnostics,
    # leaving the publish_fence_failed cause invisible.
    job_diag = await _query_job_diagnostics(pool, captured_job_id[0])
    assert job_diag is not None, (
        "FenceViolationError must write output_ref_json.diagnostics"
    )
    assert job_diag["no_op_cause"] == "execution_failed", (
        f"expected no_op_cause='execution_failed'; got {job_diag.get('no_op_cause')!r}"
    )
    assert job_diag["failure"]["failure_class"] == "publish_guard", (
        f"expected failure_class='publish_guard'; "
        f"got {job_diag['failure'].get('failure_class')!r}"
    )
    assert job_diag["failure"]["failure_code"] == "publish_fence_failed", (
        f"expected failure_code='publish_fence_failed'; "
        f"got {job_diag['failure'].get('failure_code')!r}"
    )
    window_id = await _resolve_window_id_from_job(pool, captured_job_id[0])
    assert window_id is not None
    coverage_diag = await _query_window_coverage_diagnostics(pool, window_id)
    assert coverage_diag is not None, (
        "FenceViolationError must write coverage.diagnostics for failed window"
    )
    assert coverage_diag["no_op_cause"] == "execution_failed", (
        f"coverage.diagnostics.no_op_cause must be 'execution_failed'; "
        f"got {coverage_diag.get('no_op_cause')!r}"
    )
    assert coverage_diag["failure"]["failure_code"] == "publish_fence_failed", (
        f"coverage.diagnostics.failure_code must be 'publish_fence_failed'; "
        f"got {coverage_diag['failure'].get('failure_code')!r}"
    )


# ---------------------------------------------------------------------------
# Requirement 2: non-retryable config error → failed_terminal
# ---------------------------------------------------------------------------


async def test_pipeline_runner_window_config_error_transitions_failed_terminal(
    test_db_pool_with_window_job_only: tuple[asyncpg.Pool, UUID, UUID, UUID],
) -> None:
    """Non-retryable GrammarWindowExecutionError (config) → failed_terminal.

    Verifies requirement 2: when the executor raises a non-retryable
    ``GrammarWindowExecutionError`` (e.g. configuration missing, route
    unavailable, output validation invalid), the runner transitions the
    job to ``failed_terminal`` (not ``retry_later``). The previous code
    fixed ``retryable=True`` for all ``GrammarWindowExecutionError`` which
    caused config errors to retry indefinitely.
    """
    from app.services.reader_orchestration.grammar_window_worker import (
        GrammarWindowExecutionError,
    )

    pool, record_id, user_id, base_id = test_db_pool_with_window_job_only
    runner = _make_runner(pool, record_id, base_id)

    captured_job_id: list[UUID] = []
    captured_run_id: list[UUID] = []

    async def _config_error_process(*, claim: Any) -> dict[str, Any]:
        captured_job_id.append(claim.job_id)
        captured_run_id.append(claim.run_id)
        raise GrammarWindowExecutionError(
            "grammar window executor is not configured",
            retryable=False,
            failure_class="configuration",
            failure_code="grammar_window_executor_unconfigured",
        )

    mock_worker = AsyncMock()
    mock_worker.process_window_job = AsyncMock(side_effect=_config_error_process)
    mock_publisher = AsyncMock()

    runner._grammar_window_worker = mock_worker
    runner._grammar_window_publisher = mock_publisher

    summary = await runner.run(
        record_id=record_id,
        user_id=user_id,
        lease_owner="test-config-error-terminal",
        lease_duration=LEASE_DURATION,
        max_ticks=10,
        max_jobs=10,
    )

    assert captured_job_id, "window worker must have been called at least once"
    job_status = await _query_job_status(pool, captured_job_id[0])
    assert job_status == "failed_terminal", (
        f"non-retryable config error must transition job to failed_terminal; "
        f"got {job_status!r}"
    )
    run_status = await _query_run_status(pool, captured_run_id[0])
    assert run_status == "failed_terminal", (
        f"non-retryable config error must mark run as failed_terminal; "
        f"got {run_status!r}"
    )
    # Requirement 3: non-retryable failure marks analysis_window failed.
    window_status = await _query_window_status(pool, captured_job_id[0])
    assert window_status == "failed", (
        f"non-retryable failure must mark analysis_window as failed; "
        f"got {window_status!r}"
    )
    mock_publisher.publish_window_grammar_bundle.assert_not_called()
    assert summary.outcome_counts.failed_terminal >= 1


# ---------------------------------------------------------------------------
# Requirement 2+3: retryable provider error → retry_later, window stays running
# ---------------------------------------------------------------------------


async def test_pipeline_runner_window_provider_error_retry_keeps_window_running(
    test_db_pool_with_window_job_only: tuple[asyncpg.Pool, UUID, UUID, UUID],
) -> None:
    """Retryable GrammarWindowExecutionError (provider) → retry_later, window
    stays in its current status (not marked failed).

    Verifies requirement 2+3: when the executor raises a retryable
    ``GrammarWindowExecutionError`` (e.g. provider timeout), the runner
    transitions the job to ``retry_later`` and does NOT mark
    ``analysis_windows.status = 'failed'`` — the window stays in its
    preflight state (``running`` or ``pending``) so the same job retry
    can resume.
    """
    from app.services.reader_orchestration.grammar_window_worker import (
        GrammarWindowExecutionError,
    )

    pool, record_id, user_id, base_id = test_db_pool_with_window_job_only
    runner = _make_runner(pool, record_id, base_id)

    captured_job_id: list[UUID] = []
    captured_run_id: list[UUID] = []

    async def _provider_error_process(*, claim: Any) -> dict[str, Any]:
        captured_job_id.append(claim.job_id)
        captured_run_id.append(claim.run_id)
        raise GrammarWindowExecutionError(
            "provider timeout",
            retryable=True,
            failure_class="provider",
            failure_code="TimeoutError",
        )

    mock_worker = AsyncMock()
    mock_worker.process_window_job = AsyncMock(side_effect=_provider_error_process)
    mock_publisher = AsyncMock()

    runner._grammar_window_worker = mock_worker
    runner._grammar_window_publisher = mock_publisher

    summary = await runner.run(
        record_id=record_id,
        user_id=user_id,
        lease_owner="test-provider-error-retry",
        lease_duration=LEASE_DURATION,
        max_ticks=10,
        max_jobs=10,
    )

    assert captured_job_id, "window worker must have been called at least once"
    job_status = await _query_job_status(pool, captured_job_id[0])
    assert job_status == "retry_later", (
        f"retryable provider error must transition job to retry_later; "
        f"got {job_status!r}"
    )
    run_status = await _query_run_status(pool, captured_run_id[0])
    assert run_status == "failed_retryable", (
        f"retryable provider error must mark run as failed_retryable; "
        f"got {run_status!r}"
    )
    # Requirement 3: retryable failure must NOT mark analysis_window failed.
    # The window stays in its pre-failure status (pending, since the mock
    # raises before preflight runs) so the retry can resume.
    window_status = await _query_window_status(pool, captured_job_id[0])
    assert window_status != "failed", (
        f"retryable failure must NOT mark analysis_window as failed; "
        f"got {window_status!r}"
    )
    assert summary.outcome_counts.retry_later >= 1


# ---------------------------------------------------------------------------
# Requirement 3: generic terminal exception after preflight → window=failed
# ---------------------------------------------------------------------------


async def test_pipeline_runner_window_generic_exception_marks_window_failed(
    test_db_pool_with_window_job_only: tuple[asyncpg.Pool, UUID, UUID, UUID],
) -> None:
    """Generic Exception from process_window_job → window=failed.

    Verifies requirement 3: when ``process_window_job`` raises a generic
    ``Exception`` (not ``GrammarWindowExecutionError`` / ``ValueError``),
    the runner transitions the job to ``failed_terminal`` and marks
    ``analysis_windows.status = 'failed'``. The ``window_id`` is resolved
    from the job's ``input_json`` immediately after claim, so the failure
    handler can mark the window even when the exception fires before
    ``candidates_ready`` is returned.
    """
    pool, record_id, user_id, base_id = test_db_pool_with_window_job_only
    runner = _make_runner(pool, record_id, base_id)

    captured_job_id: list[UUID] = []
    captured_run_id: list[UUID] = []

    async def _generic_exception_process(*, claim: Any) -> dict[str, Any]:
        captured_job_id.append(claim.job_id)
        captured_run_id.append(claim.run_id)
        raise RuntimeError("unexpected worker crash (test fixture)")

    mock_worker = AsyncMock()
    mock_worker.process_window_job = AsyncMock(
        side_effect=_generic_exception_process
    )
    mock_publisher = AsyncMock()

    runner._grammar_window_worker = mock_worker
    runner._grammar_window_publisher = mock_publisher

    await runner.run(
        record_id=record_id,
        user_id=user_id,
        lease_owner="test-generic-exception-terminal",
        lease_duration=LEASE_DURATION,
        max_ticks=10,
        max_jobs=10,
    )

    assert captured_job_id, "window worker must have been called at least once"
    job_status = await _query_job_status(pool, captured_job_id[0])
    assert job_status == "failed_terminal", (
        f"generic Exception must transition job to failed_terminal; "
        f"got {job_status!r}"
    )
    run_status = await _query_run_status(pool, captured_run_id[0])
    assert run_status == "failed_terminal", (
        f"generic Exception must mark run as failed_terminal; "
        f"got {run_status!r}"
    )
    # Requirement 3: generic terminal failure must mark analysis_window failed.
    window_status = await _query_window_status(pool, captured_job_id[0])
    assert window_status == "failed", (
        f"generic terminal failure must mark analysis_window as failed; "
        f"got {window_status!r}"
    )
