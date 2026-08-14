"""Tests for GrammarWindowWorkerService: preflight (§8.2) + heartbeat (§8.6).

Design source:
  docs/architecture/reader-orchestration.md
  §8.2 (window claim / preflight pending→running) + §8.6 (heartbeat)

The preflight tests cover all four §8.2 status branches:
  - pending → UPDATE running, return PROCEED
  - running + same job_id → return PROCEED (retry)
  - running + different job_id → raise IllegalTransitionError
  - completed / no_op / failed → return ALREADY_TERMINAL
  - unknown status → raise IllegalTransitionError (defensive)

The heartbeat test verifies the loop calls job_runtime.heartbeat periodically.
The process_window_job test verifies ALREADY_TERMINAL short-circuits the LLM.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.contracts.annotation import slice_by_utf16_offsets
from app.database import connection as db_connection
from app.services.model_execution_journal import BeginDisposition
from app.services.reader_orchestration.grammar_window_bootstrap import (
    GRAMMAR_WINDOW_OPERATION_FINGERPRINT,
    GrammarWindowBootstrapService,
    _compute_window_budget,
)
from app.services.reader_orchestration.grammar_window_worker import (
    GrammarWindowExecutionError,
    GrammarWindowExecutionResult,
    GrammarWindowWorkerService,
    PreflightResult,
    PydanticAIGrammarWindowExecutor,
    _resolve_window_strategy,
)
from app.services.reader_orchestration.job_runtime import (
    ClaimResult,
    IllegalTransitionError,
)
from app.services.reader_orchestration.reading_strategy import (
    resolve_reader_variant_strategy,
)
from app.services.reader_orchestration.window_selector import CandidateItem
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


# ---------------------------------------------------------------------------
# Base fixture: schema + record + base
# ---------------------------------------------------------------------------


@pytest.fixture
async def test_db_pool_with_record_and_base() -> AsyncIterator[
    tuple[asyncpg.Pool, UUID, UUID]
]:
    """Create test schema (baseline + migration 0015), submit an article,
    return (pool, record_id, base_id).
    """
    schema_name = f"test_grammar_window_worker_{uuid4().hex}"
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
                title="Grammar Window Worker Slice",
                language="en",
            )
            yield pool, article.record_id, article.base_id
        finally:
            await pool.close()
    finally:
        db_connection.DB_POOL = original_pool
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


# ---------------------------------------------------------------------------
# Shared helper: bootstrap plan + windows + jobs, pick first window
# ---------------------------------------------------------------------------


async def _bootstrap_first_window(
    pool: asyncpg.Pool,
    record_id: UUID,
    base_id: UUID,
) -> tuple[UUID, UUID]:
    """Run grammar-window bootstrap and return (job_id, window_id) for the first window."""
    service = GrammarWindowBootstrapService(pool=pool)
    result = await service.bootstrap_grammar_window_plan(
        record_id=record_id, base_id=base_id
    )
    assert len(result.job_ids) >= 1
    job_id = result.job_ids[0]
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id FROM analysis_windows WHERE job_id = $1", job_id
        )
    assert row is not None
    return job_id, row["id"]


# ---------------------------------------------------------------------------
# Preflight fixtures: one per §8.2 branch
# ---------------------------------------------------------------------------


@pytest.fixture
async def test_db_pool_with_window_job(
    test_db_pool_with_record_and_base: tuple[asyncpg.Pool, UUID, UUID],
) -> AsyncIterator[tuple[asyncpg.Pool, UUID, UUID, UUID]]:
    """§8.2 pending window (default state after bootstrap).

    Returns (pool, job_id, lease_token, window_id).
    """
    pool, record_id, base_id = test_db_pool_with_record_and_base
    job_id, window_id = await _bootstrap_first_window(pool, record_id, base_id)
    lease_token = uuid4()
    yield pool, job_id, lease_token, window_id


@pytest.fixture
async def test_db_pool_with_completed_window(
    test_db_pool_with_record_and_base: tuple[asyncpg.Pool, UUID, UUID],
) -> AsyncIterator[tuple[asyncpg.Pool, UUID, UUID, UUID]]:
    """§8.2 terminal window (status='completed')."""
    pool, record_id, base_id = test_db_pool_with_record_and_base
    job_id, window_id = await _bootstrap_first_window(pool, record_id, base_id)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE analysis_windows SET status = 'completed' WHERE id = $1",
            window_id,
        )
    lease_token = uuid4()
    yield pool, job_id, lease_token, window_id


@pytest.fixture
async def test_db_pool_with_running_window(
    test_db_pool_with_record_and_base: tuple[asyncpg.Pool, UUID, UUID],
) -> AsyncIterator[tuple[asyncpg.Pool, UUID, UUID, UUID]]:
    """§8.2 running window with matching job_id (retry path)."""
    pool, record_id, base_id = test_db_pool_with_record_and_base
    job_id, window_id = await _bootstrap_first_window(pool, record_id, base_id)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE analysis_windows
            SET status = 'running', started_at = NOW(), job_id = $2
            WHERE id = $1
            """,
            window_id, job_id,
        )
    lease_token = uuid4()
    yield pool, job_id, lease_token, window_id


@pytest.fixture
async def test_db_pool_with_running_window_other_job(
    test_db_pool_with_record_and_base: tuple[asyncpg.Pool, UUID, UUID],
) -> AsyncIterator[tuple[asyncpg.Pool, UUID, UUID, UUID]]:
    """§8.2 running window with a different job_id (must reject)."""
    pool, record_id, base_id = test_db_pool_with_record_and_base
    job_id, window_id = await _bootstrap_first_window(pool, record_id, base_id)
    other_job_id = uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE analysis_windows
            SET status = 'running', started_at = NOW(), job_id = $2
            WHERE id = $1
            """,
            window_id, other_job_id,
        )
    lease_token = uuid4()
    yield pool, job_id, lease_token, window_id


@pytest.fixture
async def test_db_pool_with_unknown_status_window(
    test_db_pool_with_record_and_base: tuple[asyncpg.Pool, UUID, UUID],
) -> AsyncIterator[tuple[asyncpg.Pool, UUID, UUID, UUID]]:
    """§8.2 defensive: window with an unrecognized status value.

    Requires dropping the CHECK constraint so we can insert a bogus status.
    The schema is dropped at fixture teardown so this is contained.
    """
    pool, record_id, base_id = test_db_pool_with_record_and_base
    job_id, window_id = await _bootstrap_first_window(pool, record_id, base_id)
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE analysis_windows "
            "DROP CONSTRAINT IF EXISTS analysis_windows_status_check"
        )
        await conn.execute(
            "UPDATE analysis_windows SET status = 'bogus_status' WHERE id = $1",
            window_id,
        )
    lease_token = uuid4()
    yield pool, job_id, lease_token, window_id


# ---------------------------------------------------------------------------
# Preflight tests (§8.2)
# ---------------------------------------------------------------------------


async def test_preflight_marks_pending_window_as_running(
    test_db_pool_with_window_job: tuple[asyncpg.Pool, UUID, UUID, UUID],
) -> None:
    """§8.2 pending window → preflight marks it running, returns PROCEED."""
    pool, job_id, lease_token, window_id = test_db_pool_with_window_job
    service = GrammarWindowWorkerService(pool=pool)
    result = await service.preflight_window_job(
        job_id=job_id,
        lease_token=lease_token,
        lease_duration=timedelta(seconds=120),
    )
    assert result == PreflightResult.PROCEED

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, started_at, job_id FROM analysis_windows WHERE id = $1",
            window_id,
        )
    assert row is not None
    assert row["status"] == "running"
    assert row["started_at"] is not None
    assert str(row["job_id"]) == str(job_id)


async def test_preflight_skips_terminal_window(
    test_db_pool_with_completed_window: tuple[asyncpg.Pool, UUID, UUID, UUID],
) -> None:
    """§8.2 already-terminal window → ALREADY_TERMINAL, no mutation."""
    pool, job_id, lease_token, window_id = test_db_pool_with_completed_window
    service = GrammarWindowWorkerService(pool=pool)
    result = await service.preflight_window_job(
        job_id=job_id,
        lease_token=lease_token,
        lease_duration=timedelta(seconds=120),
    )
    assert result == PreflightResult.ALREADY_TERMINAL

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status FROM analysis_windows WHERE id = $1", window_id
        )
    assert row is not None
    assert row["status"] == "completed"


async def test_preflight_allows_retry_same_job(
    test_db_pool_with_running_window: tuple[asyncpg.Pool, UUID, UUID, UUID],
) -> None:
    """§8.2 running window + same job_id → PROCEED (retry path)."""
    pool, job_id, lease_token, window_id = test_db_pool_with_running_window
    service = GrammarWindowWorkerService(pool=pool)
    result = await service.preflight_window_job(
        job_id=job_id,
        lease_token=lease_token,
        lease_duration=timedelta(seconds=120),
    )
    assert result == PreflightResult.PROCEED

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, job_id FROM analysis_windows WHERE id = $1", window_id
        )
    assert row is not None
    assert row["status"] == "running"
    assert str(row["job_id"]) == str(job_id)


async def test_preflight_rejects_running_window_with_different_job_id(
    test_db_pool_with_running_window_other_job: tuple[
        asyncpg.Pool, UUID, UUID, UUID
    ],
) -> None:
    """§8.2 running window + different job_id → IllegalTransitionError."""
    pool, job_id, lease_token, window_id = test_db_pool_with_running_window_other_job
    service = GrammarWindowWorkerService(pool=pool)
    with pytest.raises(IllegalTransitionError):
        await service.preflight_window_job(
            job_id=job_id,
            lease_token=lease_token,
            lease_duration=timedelta(seconds=120),
        )


async def test_preflight_raises_on_unknown_status(
    test_db_pool_with_unknown_status_window: tuple[
        asyncpg.Pool, UUID, UUID, UUID
    ],
) -> None:
    """§8.2 unknown status → IllegalTransitionError (defensive)."""
    pool, job_id, lease_token, window_id = test_db_pool_with_unknown_status_window
    service = GrammarWindowWorkerService(pool=pool)
    with pytest.raises(IllegalTransitionError):
        await service.preflight_window_job(
            job_id=job_id,
            lease_token=lease_token,
            lease_duration=timedelta(seconds=120),
        )


# ---------------------------------------------------------------------------
# Heartbeat test (§8.6)
# ---------------------------------------------------------------------------


async def test_heartbeat_loop_calls_job_runtime_heartbeat() -> None:
    """§8.6 heartbeat loop periodically calls job_runtime.heartbeat."""
    mock_job_runtime = AsyncMock()
    service = GrammarWindowWorkerService(
        pool=MagicMock(),
        job_runtime=mock_job_runtime,
        heartbeat_interval=timedelta(milliseconds=50),
    )
    task = asyncio.create_task(
        service._heartbeat_loop(
            job_id=uuid4(),
            lease_token=uuid4(),
        )
    )
    await asyncio.sleep(0.15)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert mock_job_runtime.heartbeat.call_count >= 2


# ---------------------------------------------------------------------------
# process_window_job short-circuit test
# ---------------------------------------------------------------------------


def _make_claim() -> ClaimResult:
    """Build a minimal valid ClaimResult for tests that mock the DB layer."""
    return ClaimResult(
        job_id=uuid4(),
        run_id=uuid4(),
        reading_record_id=uuid4(),
        user_id=uuid4(),
        base_id=uuid4(),
        job_type="build_grammar_bundle_window",
        target_type="unit_range",
        target_key=str(uuid4()),
        expected_generation=1,
        operation_fingerprint=GRAMMAR_WINDOW_OPERATION_FINGERPRINT,
        attempt_count=1,
        lease_owner="test_window_worker",
        lease_token=uuid4(),
        lease_expires_at=datetime.now(UTC),
    )


async def test_process_window_job_skips_when_already_terminal() -> None:
    """preflight ALREADY_TERMINAL → return early, no LLM call."""
    service = GrammarWindowWorkerService(pool=MagicMock())
    service.preflight_window_job = AsyncMock(  # type: ignore[method-assign]
        return_value=PreflightResult.ALREADY_TERMINAL
    )
    service._load_window_context = AsyncMock()  # type: ignore[method-assign]
    service._call_llm = AsyncMock()  # type: ignore[method-assign]

    claim = _make_claim()
    result = await service.process_window_job(claim=claim)
    assert result["status"] == "already_terminal"
    service._load_window_context.assert_not_called()
    service._call_llm.assert_not_called()


async def test_ground_span_returns_unit_relative_offsets_for_later_unit() -> None:
    """Grounding must emit offsets relative to the target unit, not base text.

    Snapshot validation checks spans against ``anchor_segments.unit_*``. This
    regression covers non-zero ``unit_base_start_utf16`` so grammar-window windows cannot
    accidentally publish base-relative offsets for unit 2+.
    """
    executor = PydanticAIGrammarWindowExecutor()
    base_id = uuid4()

    grounded = executor._ground_span(
        anchor={
            "anchor_segment_id": "s2",
            "unit_id": "u2",
            "base_start_utf16": 120,
            "base_end_utf16": 131,
            "unit_base_start_utf16": 100,
            "unit_base_end_utf16": 180,
            "source_text": "Hello world",
        },
        selected_text="world",
        context={"base_id": str(base_id)},
    )

    assert grounded is not None
    assert grounded.base_id == str(base_id)
    assert grounded.unit_id == "u2"
    assert grounded.anchor_segment_id == "s2"
    assert grounded.start_offset == 26
    assert grounded.end_offset == 31


# ---------------------------------------------------------------------------
# C5a: _load_window_context + _call_llm executor delegation tests
# ---------------------------------------------------------------------------


async def test_load_window_context_loads_source_text(
    test_db_pool_with_window_job: tuple[asyncpg.Pool, UUID, UUID, UUID],
) -> None:
    """_load_window_context returns target_anchors with source_text sliced
    from the base text using UTF-16 code unit offsets."""
    pool, job_id, _lease_token, _window_id = test_db_pool_with_window_job
    service = GrammarWindowWorkerService(pool=pool)
    context = await service._load_window_context(job_id)

    assert "target_anchors" in context
    assert isinstance(context["target_anchors"], list)
    assert len(context["target_anchors"]) >= 1

    for anchor in context["target_anchors"]:
        assert "anchor_segment_id" in anchor
        assert "unit_id" in anchor
        assert "unit_order_index" in anchor
        assert "base_start_utf16" in anchor
        assert "base_end_utf16" in anchor
        assert "unit_base_start_utf16" in anchor
        assert "unit_base_end_utf16" in anchor
        assert "source_text" in anchor
        assert isinstance(anchor["source_text"], str)
        assert len(anchor["source_text"]) > 0

    # Independently verify source_text matches the base text slice.
    async with pool.acquire() as conn:
        job_base = await conn.fetchrow(
            "SELECT base_id FROM reader_jobs WHERE id = $1",
            job_id,
        )
        base_row = await conn.fetchrow(
            "SELECT text FROM reading_bases WHERE id = $1",
            job_base["base_id"],
        )
        first_anchor_id = context["target_anchors"][0]["anchor_segment_id"]
        seg_row = await conn.fetchrow(
            """
            SELECT base_start_utf16, base_end_utf16
            FROM anchor_segments
            WHERE base_id = $1 AND anchor_segment_id = $2
            """,
            job_base["base_id"],
            first_anchor_id,
        )

    expected_text = slice_by_utf16_offsets(
        str(base_row["text"]),
        int(seg_row["base_start_utf16"]),
        int(seg_row["base_end_utf16"]),
    )
    assert context["target_anchors"][0]["source_text"] == expected_text


async def test_load_window_context_includes_context_anchors(
    test_db_pool_with_record_and_base: tuple[asyncpg.Pool, UUID, UUID],
) -> None:
    """_load_window_context populates context_anchor_prev / context_anchor_next
    with anchor metadata when the window has context anchors."""
    pool, record_id, base_id = test_db_pool_with_record_and_base
    service = GrammarWindowBootstrapService(pool=pool)
    result = await service.bootstrap_grammar_window_plan(
        record_id=record_id, base_id=base_id
    )

    # Pick the window with the most context anchors (middle window if any).
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, job_id, context_anchor_prev, context_anchor_next
            FROM analysis_windows
            WHERE plan_id = $1
            ORDER BY window_index
            """,
            result.plan_id,
        )
    best_row = rows[0]
    best_count = -1
    for row in rows:
        prev = list(row["context_anchor_prev"] or [])
        nxt = list(row["context_anchor_next"] or [])
        if len(prev) + len(nxt) > best_count:
            best_count = len(prev) + len(nxt)
            best_row = row

    job_id = best_row["job_id"]
    expected_prev = list(best_row["context_anchor_prev"] or [])
    expected_next = list(best_row["context_anchor_next"] or [])

    worker = GrammarWindowWorkerService(pool=pool)
    context = await worker._load_window_context(job_id)

    assert "context_anchor_prev" in context
    assert "context_anchor_next" in context
    assert isinstance(context["context_anchor_prev"], list)
    assert isinstance(context["context_anchor_next"], list)
    assert len(context["context_anchor_prev"]) == len(expected_prev)
    assert len(context["context_anchor_next"]) == len(expected_next)

    for anchor in context["context_anchor_prev"]:
        assert "anchor_segment_id" in anchor
        assert "source_text" in anchor
    for anchor in context["context_anchor_next"]:
        assert "anchor_segment_id" in anchor
        assert "source_text" in anchor


async def test_call_llm_delegates_to_executor() -> None:
    """_call_llm delegates to the injected executor.generate()."""
    mock_executor = AsyncMock()
    expected_candidates: list[CandidateItem] = [
        CandidateItem(
            item_type="grammar_note",
            anchor_segment_id="anchor-1",
            spans=[{"unit_id": "unit-1"}],
            semantic_dedup_key="dedup-1",
            pattern_key="pattern-1",
            quality_score=4,
            reading_blocker=False,
            dedup_hint="hint-1",
        )
    ]
    expected_result = GrammarWindowExecutionResult(candidates=expected_candidates)
    mock_executor.generate.return_value = expected_result

    service = GrammarWindowWorkerService(
        pool=MagicMock(),
        executor=mock_executor,
    )
    context = {"window_id": "test-window", "target_anchors": []}
    result = await service._call_llm(context)

    assert result is expected_result
    mock_executor.generate.assert_called_once_with(context)


async def test_process_window_job_calls_executor_and_returns_candidates() -> None:
    """process_window_job with PROCEED path calls executor and returns candidates_ready."""
    mock_executor = AsyncMock()
    expected_candidates: list[CandidateItem] = [
        CandidateItem(
            item_type="grammar_note",
            anchor_segment_id="anchor-1",
            spans=[{"unit_id": "unit-1"}],
            semantic_dedup_key="dedup-1",
            pattern_key="pattern-1",
            quality_score=4,
            reading_blocker=False,
            dedup_hint="hint-1",
        )
    ]
    expected_result = GrammarWindowExecutionResult(candidates=expected_candidates)
    mock_executor.generate.return_value = expected_result

    service = GrammarWindowWorkerService(
        pool=MagicMock(),
        executor=mock_executor,
        heartbeat_interval=timedelta(seconds=100),
    )
    service.preflight_window_job = AsyncMock(  # type: ignore[method-assign]
        return_value=PreflightResult.PROCEED
    )
    service._load_window_context = AsyncMock(  # type: ignore[method-assign]
        return_value={"window_id": "test"}
    )
    service._journal_service.begin_execution = AsyncMock(
        return_value=BeginDisposition(
            journal_id=uuid4(),
            invocation_key="reader:reader_grammar_bundle:test:1:1",
            capture_state="started",
            provider_call_allowed=True,
        )
    )
    service._capture_execution = AsyncMock(  # type: ignore[method-assign]
        return_value=None
    )
    service._job_runtime.heartbeat = AsyncMock(return_value=datetime.now(UTC))  # type: ignore[method-assign]

    claim = _make_claim()
    result = await service.process_window_job(claim=claim)

    assert result["status"] == "candidates_ready"
    assert result["candidates"] == expected_candidates
    mock_executor.generate.assert_called_once()
    service._load_window_context.assert_called_once_with(claim.job_id)


async def test_unconfigured_executor_raises_error() -> None:
    """GrammarWindowWorkerService with no executor raises GrammarWindowExecutionError."""
    service = GrammarWindowWorkerService(pool=MagicMock())
    with pytest.raises(GrammarWindowExecutionError):
        await service._call_llm({"window_id": "test"})


# ---------------------------------------------------------------------------
# Variant strategy injection in grammar window worker
# ---------------------------------------------------------------------------


def _make_valid_strategy_input(
    *,
    reading_goal: str = "daily_reading",
    reading_variant: str = "intermediate_reading",
) -> dict[str, Any]:
    """Build a valid window job input_json with strategy metadata.

    Resolves the live strategy and embeds its version/hash/layer_policy_hash
    so ``_resolve_window_strategy`` can cross-validate.
    """
    strategy = resolve_reader_variant_strategy(reading_goal, reading_variant)
    grammar_layer = strategy.layers["grammar_bundle"]
    return {
        "reading_goal": reading_goal,
        "reading_variant": reading_variant,
        "strategy_version": strategy.strategy_version,
        "strategy_hash": strategy.strategy_hash,
        "layer_policy_hash": grammar_layer.policy_hash,
        "window_id": str(uuid4()),
        "plan_id": str(uuid4()),
        "window_index": 0,
        "target_anchor_ids": [],
        "context_anchor_prev": [],
        "context_anchor_next": [],
        "target_unit_ids": [],
        "window_budget": _compute_window_budget(),
    }


async def test_resolve_window_strategy_returns_strategy_fields() -> None:
    """_resolve_window_strategy returns reading_goal/reading_variant/
    strategy_hash/layer_policy_hash/grammar_prompt_lines from input_json."""
    input_data = _make_valid_strategy_input()
    result = _resolve_window_strategy(input_data)

    assert result["reading_goal"] == "daily_reading"
    assert result["reading_variant"] == "intermediate_reading"
    assert result["strategy_version"] == input_data["strategy_version"]
    assert result["strategy_hash"] == input_data["strategy_hash"]
    assert result["layer_policy_hash"] == input_data["layer_policy_hash"]
    # grammar_prompt_lines must be a non-empty list of policy lines
    assert isinstance(result["grammar_prompt_lines"], list)
    assert len(result["grammar_prompt_lines"]) >= 1


async def test_resolve_window_strategy_rejects_missing_metadata() -> None:
    """Missing strategy metadata raises GrammarWindowExecutionError
    (fail-closed, no default fallback)."""
    input_data: dict[str, Any] = {
        "window_id": str(uuid4()),
        "plan_id": str(uuid4()),
        "window_index": 0,
    }
    with pytest.raises(GrammarWindowExecutionError) as exc_info:
        _resolve_window_strategy(input_data)
    assert "strategy_metadata_missing" in str(exc_info.value.failure_code)


async def test_resolve_window_strategy_rejects_hash_mismatch() -> None:
    """Strategy_hash mismatch raises GrammarWindowExecutionError."""
    input_data = _make_valid_strategy_input()
    input_data["strategy_hash"] = "stale_hash_value"
    with pytest.raises(GrammarWindowExecutionError) as exc_info:
        _resolve_window_strategy(input_data)
    assert "strategy_hash_mismatch" in str(exc_info.value.failure_code)


async def test_build_window_prompt_injects_reader_strategy_section() -> None:
    """_build_window_prompt injects <reader_strategy> section with
    reading_goal/reading_variant/strategy_hash/layer_policy_hash/policy_lines."""
    strategy = resolve_reader_variant_strategy("daily_reading", "intermediate_reading")
    grammar_layer = strategy.layers["grammar_bundle"]

    executor = PydanticAIGrammarWindowExecutor()
    context: dict[str, Any] = {
        "target_anchors": [
            {
                "anchor_segment_id": "anchor-1",
                "unit_id": "unit-1",
                "unit_order_index": 0,
                "source_text": "Test sentence.",
            }
        ],
        "context_anchor_prev": [],
        "context_anchor_next": [],
        "window_budget": _compute_window_budget(),
        "reading_goal": "daily_reading",
        "reading_variant": "intermediate_reading",
        "strategy_version": strategy.strategy_version,
        "strategy_hash": strategy.strategy_hash,
        "layer_policy_hash": grammar_layer.policy_hash,
        "grammar_prompt_lines": list(grammar_layer.prompt_lines),
    }

    prompt = executor._build_window_prompt(context)

    assert "<reader_strategy>" in prompt
    assert "</reader_strategy>" in prompt
    assert "reading_goal: daily_reading" in prompt
    assert "reading_variant: intermediate_reading" in prompt
    assert f"strategy_hash: {strategy.strategy_hash}" in prompt
    assert f"layer_policy_hash: {grammar_layer.policy_hash}" in prompt
    assert "<policy_lines>" in prompt
    for line in grammar_layer.prompt_lines:
        assert f"- {line}" in prompt


async def test_build_window_prompt_omits_strategy_when_no_prompt_lines() -> None:
    """When grammar_prompt_lines is empty, no <reader_strategy> section."""
    executor = PydanticAIGrammarWindowExecutor()
    context: dict[str, Any] = {
        "target_anchors": [],
        "context_anchor_prev": [],
        "context_anchor_next": [],
        "window_budget": _compute_window_budget(),
        "grammar_prompt_lines": [],
    }

    prompt = executor._build_window_prompt(context)

    assert "<reader_strategy>" not in prompt


# ---------------------------------------------------------------------------
# Budget key consistency (grammar_note.count / sentence_analysis.count)
# ---------------------------------------------------------------------------


async def test_build_window_prompt_reads_nested_budget_keys() -> None:
    """_build_window_prompt reads grammar_note.count / sentence_analysis.count
    from window_budget (the format grammar_window_bootstrap writes), NOT the old
    max_grammar_notes / max_sentence_analyses flat keys."""
    executor = PydanticAIGrammarWindowExecutor()
    context: dict[str, Any] = {
        "target_anchors": [],
        "context_anchor_prev": [],
        "context_anchor_next": [],
        "window_budget": {
            "grammar_note": {"count": 7},
            "sentence_analysis": {"count": 4},
        },
        "grammar_prompt_lines": [],
    }

    prompt = executor._build_window_prompt(context)

    # The nested {grammar_note: {count: 7}} must be read correctly.
    assert "- max_grammar_notes: 7" in prompt
    assert "- max_sentence_analyses: 4" in prompt


async def test_build_window_prompt_budget_keys_match_bootstrap_format() -> None:
    """The budget keys read by the worker match the format written by
    grammar_window_bootstrap._compute_window_budget. This is the regression test for
    the silent budget mismatch bug (old worker read max_grammar_notes /
    max_sentence_analyses which never matched the nested format)."""
    # grammar_window_bootstrap writes this exact shape
    bootstrap_budget = _compute_window_budget()
    assert bootstrap_budget == {
        "grammar_note": {"count": 2},
        "sentence_analysis": {"count": 1},
    }

    executor = PydanticAIGrammarWindowExecutor()
    context: dict[str, Any] = {
        "target_anchors": [],
        "context_anchor_prev": [],
        "context_anchor_next": [],
        "window_budget": bootstrap_budget,
        "grammar_prompt_lines": [],
    }

    prompt = executor._build_window_prompt(context)

    # Worker must read the nested keys correctly, not fall back to 4/3 defaults.
    assert "- max_grammar_notes: 2" in prompt
    assert "- max_sentence_analyses: 1" in prompt
    # Explicitly verify the old-bug defaults (4/3) are NOT present.
    assert "- max_grammar_notes: 4" not in prompt
    assert "- max_sentence_analyses: 3" not in prompt


async def test_build_window_prompt_falls_back_when_budget_missing() -> None:
    """When window_budget is missing or empty, worker falls back to
    safe defaults (4/3). This is defensive, not the happy path."""
    executor = PydanticAIGrammarWindowExecutor()
    context: dict[str, Any] = {
        "target_anchors": [],
        "context_anchor_prev": [],
        "context_anchor_next": [],
        "window_budget": {},
        "grammar_prompt_lines": [],
    }

    prompt = executor._build_window_prompt(context)

    assert "- max_grammar_notes: 4" in prompt
    assert "- max_sentence_analyses: 3" in prompt


# ---------------------------------------------------------------------------
# Markdown output contract + variant policy injection tests
# ---------------------------------------------------------------------------


async def test_window_system_prompt_contains_markdown_output_contract() -> None:
    """The composed window system prompt must declare the Markdown output
    contract via the shared teaching instructions: note/analysis are Simplified
    Chinese Markdown, allow bold/inline-code/short bullets, forbid raw HTML and
    headings (hard forbid, no "unless explicitly needed").
    """
    from app.services.reader_orchestration.grammar_window_worker import (
        get_window_grammar_system_prompt,
    )

    prompt = get_window_grammar_system_prompt()
    assert "Markdown" in prompt
    assert "**加粗**" in prompt
    assert "`inline code`" in prompt
    assert "raw HTML" in prompt
    # Forbidden: do not include literal HTML open-tags as allowed syntax
    assert "<b>" not in prompt
    assert "<span>" not in prompt
    # Heading forbid must be hard — no soft "unless explicitly needed" escape
    assert "unless explicitly needed" not in prompt
    assert "除非确有需要" not in prompt
    # Fixed length template pressure must be gone
    assert "2-4 句" not in prompt
    assert "2–4 句" not in prompt
    assert "偏好短段落（2-4 句）" not in prompt
    # The conflicting legacy line must be gone
    assert "Write grammar_point, note, and analysis in Chinese" not in prompt


async def test_window_system_prompt_language_requirements_unified() -> None:
    """The composed window system prompt must declare unified language
    requirements: note/analysis in Simplified Chinese; grammar_point can be
    Chinese or Chinese-English mixed; pattern/dedup_hint stay English.
    """
    from app.services.reader_orchestration.grammar_window_worker import (
        get_window_grammar_system_prompt,
    )

    prompt = get_window_grammar_system_prompt()
    # grammar_point can be Chinese or mixed (not "stay in English")
    assert "grammar_point" in prompt
    assert "中英混合" in prompt
    # pattern / dedup_hint must stay English. YAML currently phrases this as
    # "`pattern`、`dedup_hint`：英文。" — match the actual contract instead
    # of the older "保持英文" wording that was removed in e5314cb56.
    assert "pattern" in prompt
    assert "dedup_hint" in prompt
    assert "`pattern`、`dedup_hint`：英文" in prompt
    # The old conflicting line "grammar_point ... stay in English" must be gone
    assert "grammar_point`, `pattern`, and `dedup_hint` stay in English" not in prompt


async def test_window_prompt_injects_gaokao_policy_lines() -> None:
    """Gaokao grammar_bundle policy lines must enter the user prompt via
    <reader_strategy>. This verifies the variant policy reaches the LLM.
    """
    strategy = resolve_reader_variant_strategy("exam", "gaokao")
    grammar_layer = strategy.layers["grammar_bundle"]

    executor = PydanticAIGrammarWindowExecutor()
    context: dict[str, Any] = {
        "target_anchors": [
            {
                "anchor_segment_id": "anchor-1",
                "unit_id": "unit-1",
                "unit_order_index": 0,
                "source_text": "Test sentence.",
            }
        ],
        "context_anchor_prev": [],
        "context_anchor_next": [],
        "window_budget": _compute_window_budget(),
        "reading_goal": "exam",
        "reading_variant": "gaokao",
        "strategy_version": strategy.strategy_version,
        "strategy_hash": strategy.strategy_hash,
        "layer_policy_hash": grammar_layer.policy_hash,
        "grammar_prompt_lines": list(grammar_layer.prompt_lines),
    }

    prompt = executor._build_window_prompt(context)

    assert "<reader_strategy>" in prompt
    assert "reading_goal: exam" in prompt
    assert "reading_variant: gaokao" in prompt
    assert f"layer_policy_hash: {grammar_layer.policy_hash}" in prompt
    # Gaokao-specific soft-lens content must reach the prompt
    assert "高考" in prompt
    assert "中学" in prompt
    assert "显性教学" not in prompt
    # No stale field names
    assert "note_zh" not in prompt
    assert "analysis_zh" not in prompt


async def test_window_field_descriptions_forbid_raw_html() -> None:
    """Pydantic Field descriptions for note/analysis must declare the Markdown
    contract and explicitly forbid raw HTML. The description is what the LLM
    agent framework sees as the field-level instruction. Must say "前端" (not
    "后端") for deserialization, and hard-forbid headings (no "除非确有需要").
    """
    from app.services.reader_orchestration.grammar_window_worker import (
        _WindowGrammarNoteCandidate,
        _WindowSentenceAnalysisCandidate,
    )

    note_desc = _WindowGrammarNoteCandidate.model_fields["note"].description or ""
    analysis_desc = (
        _WindowSentenceAnalysisCandidate.model_fields["analysis"].description or ""
    )

    assert "Markdown" in note_desc
    assert "raw HTML" in note_desc
    assert "Markdown" in analysis_desc
    assert "raw HTML" in analysis_desc
    # Deserialization is done by the frontend, not the backend
    assert "前端" in note_desc
    assert "前端" in analysis_desc
    assert "后端" not in note_desc
    assert "后端" not in analysis_desc
    # Heading forbid must be hard
    assert "除非确有需要" not in note_desc
    assert "除非确有需要" not in analysis_desc


# ---------------------------------------------------------------------------
# window candidate schema validation (mirrors per-unit / batch)
# ---------------------------------------------------------------------------
# ``Reason_code`` / ``confidence`` ``low_value`` / ``GrammarReasonCode``
# were removed from the self-rating contract. The window schemas now carry
# exactly three required fields: ``quality_score`` / ``reading_blocker`` /
# ``dedup_hint``. Legacy fields are rejected via ``extra="forbid"``.


def _minimal_window_grammar_note_kwargs() -> dict[str, Any]:
    """Return the minimum kwargs required to construct a valid
    _WindowGrammarNoteCandidate under the tightened schema (3
    self-rating fields: quality_score / reading_blocker / dedup_hint)."""
    from app.services.reader_orchestration.grammar_window_worker import (
        _WindowGrammarSpan,
    )

    return {
        "anchor_segment_id": "anchor-1",
        "spans": [
            _WindowGrammarSpan(
                anchor_segment_id="anchor-1", selected_text="team"
            )
        ],
        "grammar_point": "p",
        "pattern": None,
        "note": "n",
        "quality_score": 3,
        "reading_blocker": False,
        "dedup_hint": "k",
    }


def _minimal_window_sentence_analysis_kwargs() -> dict[str, Any]:
    """Return the minimum kwargs required to construct a valid
    _WindowSentenceAnalysisCandidate under the tightened schema (3
    self-rating fields: quality_score / reading_blocker / dedup_hint)."""
    from app.services.reader_orchestration.grammar_window_worker import (
        _WindowSentenceChunk,
    )

    return {
        "anchor_segment_id": "anchor-1",
        "selected_text": "x",
        "label": "l",
        "analysis": "a",
        "chunks": [_WindowSentenceChunk(order=1, label="c", text="x")],
        "quality_score": 3,
        "reading_blocker": False,
        "dedup_hint": "k",
    }


def test_window_grammar_note_candidate_rejects_missing_self_rating_fields() -> None:
    """_WindowGrammarNoteCandidate must reject candidates that omit
    any of the three required self-rating fields, mirroring per-unit / batch."""
    from pydantic import ValidationError

    from app.services.reader_orchestration.grammar_window_worker import (
        _WindowGrammarNoteCandidate,
    )

    base = _minimal_window_grammar_note_kwargs()
    for field in (
        "quality_score",
        "reading_blocker",
        "dedup_hint",
    ):
        incomplete = dict(base)
        del incomplete[field]
        with pytest.raises(ValidationError) as exc_info:
            _WindowGrammarNoteCandidate(**incomplete)
        assert field in str(exc_info.value)


def test_window_sentence_analysis_candidate_rejects_missing_self_rating_fields() -> None:
    """_WindowSentenceAnalysisCandidate must reject candidates that
    omit any of the three required self-rating fields, mirroring per-unit / batch."""
    from pydantic import ValidationError

    from app.services.reader_orchestration.grammar_window_worker import (
        _WindowSentenceAnalysisCandidate,
    )

    base = _minimal_window_sentence_analysis_kwargs()
    for field in (
        "quality_score",
        "reading_blocker",
        "dedup_hint",
    ):
        incomplete = dict(base)
        del incomplete[field]
        with pytest.raises(ValidationError) as exc_info:
            _WindowSentenceAnalysisCandidate(**incomplete)
        assert field in str(exc_info.value)


def test_window_candidate_rejects_out_of_range_quality_score() -> None:
    """Window schemas must reject quality_score outside [1, 5]."""
    from pydantic import ValidationError

    from app.services.reader_orchestration.grammar_window_worker import (
        _WindowGrammarNoteCandidate,
        _WindowSentenceAnalysisCandidate,
    )

    note_base = _minimal_window_grammar_note_kwargs()
    sa_base = _minimal_window_sentence_analysis_kwargs()
    for bad in (0, 6, -1):
        with pytest.raises(ValidationError):
            _WindowGrammarNoteCandidate(**{**note_base, "quality_score": bad})
        with pytest.raises(ValidationError):
            _WindowSentenceAnalysisCandidate(**{**sa_base, "quality_score": bad})


def test_window_candidate_rejects_legacy_reason_code_field() -> None:
    """``Reason_code`` was removed from the window schemas too.
    ``extra="forbid"`` rejects any payload that still carries it,
    regardless of the value (so ``long_sentence`` and the legacy valid
    codes are both rejected the same way)."""
    from pydantic import ValidationError

    from app.services.reader_orchestration.grammar_window_worker import (
        _WindowGrammarNoteCandidate,
        _WindowSentenceAnalysisCandidate,
    )

    note_base = _minimal_window_grammar_note_kwargs()
    sa_base = _minimal_window_sentence_analysis_kwargs()
    for legacy in ("long_sentence", "grammar_pattern", "meaning_blocker", ""):
        with pytest.raises(ValidationError):
            _WindowGrammarNoteCandidate(**{**note_base, "reason_code": legacy})
        with pytest.raises(ValidationError):
            _WindowSentenceAnalysisCandidate(**{**sa_base, "reason_code": legacy})


def test_window_candidate_rejects_legacy_confidence_field() -> None:
    """``Confidence`` was removed from the window schemas too.
    ``extra="forbid"`` rejects any payload that still carries it."""
    from pydantic import ValidationError

    from app.services.reader_orchestration.grammar_window_worker import (
        _WindowGrammarNoteCandidate,
        _WindowSentenceAnalysisCandidate,
    )

    note_base = _minimal_window_grammar_note_kwargs()
    sa_base = _minimal_window_sentence_analysis_kwargs()
    for legacy in (0.0, 0.5, 1.0, -0.1, 1.1):
        with pytest.raises(ValidationError):
            _WindowGrammarNoteCandidate(**{**note_base, "confidence": legacy})
        with pytest.raises(ValidationError):
            _WindowSentenceAnalysisCandidate(**{**sa_base, "confidence": legacy})


def test_window_candidate_rejects_empty_dedup_hint() -> None:
    """Window schemas must reject empty dedup_hint."""
    from pydantic import ValidationError

    from app.services.reader_orchestration.grammar_window_worker import (
        _WindowGrammarNoteCandidate,
        _WindowSentenceAnalysisCandidate,
    )

    note_base = _minimal_window_grammar_note_kwargs()
    sa_base = _minimal_window_sentence_analysis_kwargs()
    with pytest.raises(ValidationError):
        _WindowGrammarNoteCandidate(**{**note_base, "dedup_hint": ""})
    with pytest.raises(ValidationError):
        _WindowSentenceAnalysisCandidate(**{**sa_base, "dedup_hint": ""})


def test_window_candidate_rejects_overlong_dedup_hint() -> None:
    """Window schemas must reject dedup_hint longer than
    MAX_GRAMMAR_DEDUP_HINT_LENGTH, mirroring per-unit / batch."""
    from pydantic import ValidationError

    from app.services.reader_orchestration.grammar_window_worker import (
        _WindowGrammarNoteCandidate,
        _WindowSentenceAnalysisCandidate,
    )
    from app.services.reader_orchestration.grammar_worker import (
        MAX_GRAMMAR_DEDUP_HINT_LENGTH,
    )

    note_base = _minimal_window_grammar_note_kwargs()
    sa_base = _minimal_window_sentence_analysis_kwargs()
    too_long = "x" * (MAX_GRAMMAR_DEDUP_HINT_LENGTH + 1)
    with pytest.raises(ValidationError):
        _WindowGrammarNoteCandidate(**{**note_base, "dedup_hint": too_long})
    with pytest.raises(ValidationError):
        _WindowSentenceAnalysisCandidate(**{**sa_base, "dedup_hint": too_long})


def test_window_candidate_saves_normalized_dedup_hint() -> None:
    """reader-grammar-candidate-selection: window schemas must run
    ``dedup_hint`` through ``validate_dedup_hint`` and persist the
    normalized (trimmed, whitespace-collapsed, lowercased) value — not
    the raw input string. Mirrors per-unit / batch contract."""
    from app.services.reader_orchestration.grammar_window_worker import (
        _WindowGrammarNoteCandidate,
        _WindowSentenceAnalysisCandidate,
    )

    note_base = _minimal_window_grammar_note_kwargs()
    sa_base = _minimal_window_sentence_analysis_kwargs()
    raw_hint = "  Though   Concession  "
    expected_normalized = "though concession"

    note = _WindowGrammarNoteCandidate(**{**note_base, "dedup_hint": raw_hint})
    sa = _WindowSentenceAnalysisCandidate(**{**sa_base, "dedup_hint": raw_hint})

    assert note.dedup_hint == expected_normalized
    assert sa.dedup_hint == expected_normalized


def test_window_operational_rules_no_longer_duplicate_self_rating_section() -> None:
    """The window operational rules must NOT carry the hardcoded
    field-by-field self-rating explanation (items 6 and 8 in the old
    layout). The shared YAML is now the single authoritative source.
    Legacy ``reason_code`` / ``confidence`` references must also be gone."""
    from app.services.reader_orchestration.grammar_window_worker import (
        _WINDOW_GRAMMAR_OPERATIONAL_RULES,
    )

    # The old hardcoded self-rating section must be gone.
    assert "quality_score (1-5)" not in _WINDOW_GRAMMAR_OPERATIONAL_RULES
    assert "dedup_hint：此语法点的短英文" not in _WINDOW_GRAMMAR_OPERATIONAL_RULES
    assert "reason_code：取值之一" not in _WINDOW_GRAMMAR_OPERATIONAL_RULES
    # Legacy self-rating concepts must not appear in the operational rules.
    for legacy in ("reason_code", "confidence", "low_value"):
        assert legacy not in _WINDOW_GRAMMAR_OPERATIONAL_RULES, (
            f"window operational rules must not reference legacy concept {legacy!r}"
        )
    # The window rules must still carry window-only operational concerns.
    assert "[TARGET]" in _WINDOW_GRAMMAR_OPERATIONAL_RULES
    assert "[WINDOW_BUDGET]" in _WINDOW_GRAMMAR_OPERATIONAL_RULES
    assert "同 unit span 约束" in _WINDOW_GRAMMAR_OPERATIONAL_RULES
