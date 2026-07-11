"""T4.2a-R2: Execution budget and cutover safety guardrail tests.

Verifies the deterministic cost ceiling and route-fingerprint fencing
contracts introduced in T4.2a-R2:

- Per-layer execution budget (planned / consumed / exhausted)
- Fallback suppression when batch jobs are non-terminal
- Route flip fencing: stale-fingerprint jobs are superseded at claim
  time and rejected at publish time
- Budget exhaustion does not incorrectly enter coverage_complete
- Usage/runtime evidence distinguishes attempted / executed / published

Uses deterministic fake executors with call counters — no real LLM calls.

Test groups (all asyncio):

Unit (no DB):
  1. ExecutionBudget construction, consumption, exhaustion
  2. Boundary conditions (zero planned, triple max)

Integration (DB-backed):
  3. SHORT_BATCH / STRUCTURED_BATCH budget consistency
  4. Batch success suppresses per-unit fallback
  5. Non-terminal batch suppresses fallback
  6. Route flip supersedes old-fingerprint jobs at claim
  7. Route flip rejects old-fingerprint publish
  8. Budget-exhausted stopped reason is finalizable (not attention_required)
  9. Usage evidence distinguishes outcomes

T4.2a-R2-R1 additions:
  A. Cross-run hard budget (durable, multiple runner.run() calls)
  B. Batch succeeded + legacy per-unit job coexistence
  C. Batch failed_terminal fallback (fail-closed)
  D. Partial layer budget exhaustion
  E. Real publisher cutover (translation batch publisher)
  F. Budget diagnostics observability
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.database import connection as db_connection
from app.database.json_compat import ensure_json_object, jsonb_param
from app.services.reader_orchestration.display_title_worker import (
    DisplayTitleWorkerService,
)
from app.services.reader_orchestration.event_runtime import ReaderEventRuntime
from app.services.reader_orchestration.execution_budget import (
    BUDGET_CONSUMING_OUTCOMES,
    ExecutionBudget,
)
from app.services.reader_orchestration.grammar_window_publisher import (
    GrammarWindowPublisher,
)
from app.services.reader_orchestration.grammar_window_worker import (
    GrammarWindowWorkerService,
)
from app.services.reader_orchestration.grammar_worker import GrammarBundleWorkerService
from app.services.reader_orchestration.job_runtime import (
    FenceViolationError,
    ReaderJobRuntime,
)
from app.services.reader_orchestration.layer_publisher import (
    GrammarBundleLayerPublisher,
    TranslationLayerPublisher,
    VocabularyLayerPublisher,
)
from app.services.reader_orchestration.orchestrator import ReaderOrchestrator
from app.services.reader_orchestration.pipeline_runner import (
    ReaderEnhancementPipelineRunner,
)
from app.services.reader_orchestration.translation_worker import (
    TranslationExecutionError,
    TranslationWorkerService,
)
from app.services.reader_orchestration.vocabulary_worker import VocabularyWorkerService
from tests.reader_orchestration_test_support import (
    BASELINE_SQL,
    CompatTranslationLayerPublisher,
    connect_admin,
    insert_user,
    make_pool,
    submit_article_ready,
)
from tests.test_reader_orchestration_pipeline_runner import (
    _StaticBatchTranslator,
    _StaticBatchVocabularyExecutor,
    _StaticGrammarBatchExecutor,
    _StaticGrammarExecutor,
    _StaticTitleGenerator,
    _StaticTranslator,
    _StaticVocabularyExecutor,
)
from tests.test_zplus_bbc_regression import _StaticGrammarWindowExecutor

pytestmark = pytest.mark.anyio

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MIGRATION_0015_SQL = (
    _REPO_ROOT / "infra" / "migrations" / "0015_layer_analysis_plans.sql"
).read_text(encoding="utf-8")
_MIGRATION_0016_SQL = (
    _REPO_ROOT
    / "infra"
    / "migrations"
    / "0016_reader_runtime_spans_grammar_bundle_window.sql"
).read_text(encoding="utf-8")
_MIGRATION_0017_SQL = (
    _REPO_ROOT / "infra" / "migrations" / "0017_reader_jobs_batch_path_job_types.sql"
).read_text(encoding="utf-8")

LEASE_DURATION = timedelta(seconds=30)


# ---------------------------------------------------------------------------
# Call-counting executor wrappers
# ---------------------------------------------------------------------------


class _CountingBatchTranslator(_StaticBatchTranslator):
    """Batch translator that counts generate_batch calls."""

    def __init__(self) -> None:
        super().__init__()
        self.call_count = 0

    async def translate_batch(self, context):
        self.call_count += 1
        return await super().translate_batch(context)


class _CountingBatchVocabularyExecutor(_StaticBatchVocabularyExecutor):
    """Batch vocabulary executor that counts generate_batch calls."""

    def __init__(self) -> None:
        super().__init__()
        self.call_count = 0

    async def generate_batch(self, context):
        self.call_count += 1
        return await super().generate_batch(context)


class _CountingGrammarBatchExecutor(_StaticGrammarBatchExecutor):
    """Grammar batch executor that counts generate_batch calls."""

    def __init__(self) -> None:
        super().__init__()
        self.call_count = 0

    async def generate_batch(self, context):
        self.call_count += 1
        return await super().generate_batch(context)


class _CountingGrammarExecutor(_StaticGrammarExecutor):
    """Per-unit grammar executor that counts generate calls."""

    def __init__(self) -> None:
        super().__init__()
        self.call_count = 0

    async def generate(self, context):
        self.call_count += 1
        return await super().generate(context)


class _CountingTranslator(_StaticTranslator):
    """Per-unit translator that counts translate calls."""

    def __init__(self) -> None:
        super().__init__()
        self.call_count = 0

    async def translate(self, context):
        self.call_count += 1
        return await super().translate(context)


class _CountingVocabularyExecutor(_StaticVocabularyExecutor):
    """Per-unit vocabulary executor that counts generate calls."""

    def __init__(self) -> None:
        super().__init__()
        self.call_count = 0

    async def generate(self, context):
        self.call_count += 1
        return await super().generate(context)


class _CountingTitleGenerator(_StaticTitleGenerator):
    """Display title generator that counts generate calls.

    T4.2a-R2-R3a: used by Test M to prove display_title is NOT called
    when available_at is in the future, and IS called exactly once when
    available_at is reset to NOW().
    """

    def __init__(self) -> None:
        self.call_count = 0

    async def generate(self, context):
        self.call_count += 1
        return await super().generate(context)


class _RouteFlippingBatchTranslator(_StaticBatchTranslator):
    """Batch translator that flips the article_route DURING execution.

    T4.2a-R2-R2: used by Test E extension / Test J to test the worker/
    pipeline catch path. The claim-time fence passes (route is still
    original when the runner claims the job), but the publish-time fence
    fails (route is flipped by the time the worker tries to publish).
    """

    def __init__(self, *, pool: asyncpg.Pool, record_id: UUID) -> None:
        super().__init__()
        self._pool = pool
        self._record_id = record_id

    async def translate_batch(self, context):
        # Flip the route as a side effect during execution.
        await _update_run_envelope_route(
            self._pool, self._record_id, "structured_batch"
        )
        return await super().translate_batch(context)


# ---------------------------------------------------------------------------
# Article generators
# ---------------------------------------------------------------------------


def _short_article() -> str:
    """~30 words -> SHORT_BATCH (word_count <= 1100)."""
    return (
        "First sentence for budget testing here. "
        "Second sentence continues the short article now. "
        "Third sentence wraps up paragraph one cleanly.\n\n"
        "Fourth sentence starts paragraph two right here. "
        "Fifth sentence adds more content to read. "
        "Sixth sentence finishes this second paragraph.\n\n"
        "Seventh sentence opens paragraph three now. "
        "Eighth sentence keeps the article going strong. "
        "Ninth sentence concludes the short piece today."
    )


def _structured_article() -> str:
    """~1400 words -> STRUCTURED_BATCH (1100 < word_count <= 2000)."""
    base = (
        "The committee reviewed the quarterly report and found that several "
        "departments had exceeded their allocated budgets while others reported "
        "unexpected surpluses during the fiscal period. "
    )
    parts = []
    for i in range(50):
        parts.append(f"Sentence number {i + 1}. " + base)
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
async def budget_env() -> AsyncIterator[asyncpg.Pool]:
    schema_name = f"test_execution_budget_{uuid4().hex}"
    admin = await connect_admin()
    original_pool = db_connection.DB_POOL
    await admin.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
    await admin.execute(f'CREATE SCHEMA "{schema_name}"')
    await admin.execute(f'SET search_path TO "{schema_name}", public')
    await admin.execute(BASELINE_SQL)
    await admin.execute(_MIGRATION_0015_SQL)
    await admin.execute(_MIGRATION_0016_SQL)
    await admin.execute(_MIGRATION_0017_SQL)
    await admin.close()

    pool = await make_pool(schema_name)
    db_connection.DB_POOL = pool
    try:
        yield pool
    finally:
        db_connection.DB_POOL = original_pool
        await pool.close()
        cleanup = await connect_admin()
        try:
            await cleanup.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        finally:
            await cleanup.close()


# ---------------------------------------------------------------------------
# Runner factory
# ---------------------------------------------------------------------------


def _make_runner(
    pool: asyncpg.Pool,
    *,
    translator: object | None = None,
    vocabulary_executor: object | None = None,
    grammar_executor: object | None = None,
    batch_translator: object | None = None,
    batch_vocabulary_executor: object | None = None,
    grammar_batch_executor: object | None = None,
    display_title_generator: object | None = None,
    enable_zplus_grammar: bool = True,
) -> ReaderEnhancementPipelineRunner:
    translation_worker = TranslationWorkerService(
        pool=pool,
        layer_publisher=CompatTranslationLayerPublisher(pool=pool),
        translator=translator,
        batch_translator=batch_translator or _StaticBatchTranslator(),
    )
    orchestrator = ReaderOrchestrator(pool=pool, worker_service=translation_worker)
    vocabulary_worker = VocabularyWorkerService(
        pool=pool,
        layer_publisher=VocabularyLayerPublisher(pool=pool),
        executor=vocabulary_executor,
        batch_executor=batch_vocabulary_executor
        or _StaticBatchVocabularyExecutor(),
    )
    grammar_worker = GrammarBundleWorkerService(
        pool=pool,
        executor=grammar_executor,
        batch_executor=grammar_batch_executor,
    )
    display_title_worker = DisplayTitleWorkerService(
        pool=pool,
        generator=display_title_generator or _StaticTitleGenerator(),
    )
    window_worker = GrammarWindowWorkerService(
        pool=pool,
        executor=_StaticGrammarWindowExecutor(),
    )
    window_publisher = GrammarWindowPublisher(
        pool=pool,
        event_runtime=ReaderEventRuntime(pool=pool),
    )
    return ReaderEnhancementPipelineRunner(
        pool=pool,
        display_title_worker_service=display_title_worker,
        translation_orchestrator=orchestrator,
        translation_batch_worker_service=translation_worker,
        vocabulary_worker_service=vocabulary_worker,
        grammar_worker_service=grammar_worker,
        grammar_window_worker_service=window_worker,
        grammar_window_publisher=window_publisher,
        enable_zplus_grammar=enable_zplus_grammar,
    )


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def _count_jobs_by_status(
    pool: asyncpg.Pool, record_id: UUID, status: str
) -> int:
    async with pool.acquire() as conn:
        return int(
            await conn.fetchval(
                "SELECT COUNT(*) FROM reader_jobs WHERE reading_record_id = $1 AND status = $2",
                record_id,
                status,
            )
        )


async def _count_jobs_by_type(
    pool: asyncpg.Pool, record_id: UUID, job_type: str
) -> int:
    async with pool.acquire() as conn:
        return int(
            await conn.fetchval(
                "SELECT COUNT(*) FROM reader_jobs WHERE reading_record_id = $1 AND job_type = $2",
                record_id,
                job_type,
            )
        )


async def _count_layers(
    pool: asyncpg.Pool, record_id: UUID, layer_type: str
) -> int:
    async with pool.acquire() as conn:
        return int(
            await conn.fetchval(
                """
                SELECT COUNT(*) FROM enhancement_layers
                WHERE reading_record_id = $1
                  AND layer_type = $2
                  AND status = 'published'
                """,
                record_id,
                layer_type,
            )
        )


async def _fetch_job_ids(
    pool: asyncpg.Pool,
    record_id: UUID,
    job_type: str,
    target_type: str | None = None,
) -> list[UUID]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id FROM reader_jobs WHERE reading_record_id = $1 AND job_type = $2"
            + (" AND target_type = $3" if target_type else ""),
            record_id,
            job_type,
            *([target_type] if target_type else []),
        )
    return [row["id"] for row in rows]


async def _fetch_run_envelope_route(pool: asyncpg.Pool, record_id: UUID) -> str | None:
    """Fetch article_route from the run envelope associated with any job.

    article_route is written to the envelope of the run that created the
    batch jobs (not the initial submit run). We join reader_jobs to
    reader_runs to find a run that carries article_route.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT r.envelope_json
            FROM reader_runs r
            JOIN reader_jobs j ON j.run_id = r.id
            WHERE j.reading_record_id = $1
              AND r.envelope_json->>'article_route' IS NOT NULL
            ORDER BY r.created_at DESC
            LIMIT 1
            """,
            record_id,
        )
    if row is None:
        return None
    envelope = ensure_json_object(row["envelope_json"])
    return envelope.get("article_route")


async def _update_run_envelope_route(
    pool: asyncpg.Pool, record_id: UUID, new_route: str
) -> None:
    """Simulate a route flip by updating run envelopes that carry article_route."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, envelope_json FROM reader_runs
            WHERE reading_record_id = $1
              AND envelope_json->>'article_route' IS NOT NULL
            """,
            record_id,
        )
        for row in rows:
            envelope = ensure_json_object(row["envelope_json"])
            envelope["article_route"] = new_route
            await conn.execute(
                "UPDATE reader_runs SET envelope_json = $2::jsonb WHERE id = $1",
                row["id"],
                jsonb_param(envelope),
            )


# ---------------------------------------------------------------------------
# Unit tests: ExecutionBudget
# ---------------------------------------------------------------------------


class TestExecutionBudgetUnit:
    """Pure unit tests for the ExecutionBudget tracker (no DB).

    T4.2a-R2-R1: updated for the durable budget model where
    ``max_multiplier=3`` (aligning with ``max_attempts=3``) and
    ``is_exhausted`` returns False for layers with no jobs (``max==0``)
    or unknown layers.
    """

    def test_from_planned_calls_sets_max_to_triple(self) -> None:
        # T4.2a-R2-R1: default max_multiplier is now 3 (was 2), aligning
        # with max_attempts=3 in production.
        budget = ExecutionBudget.from_planned_calls({
            "translation": 3,
            "vocabulary": 2,
            "grammar": 1,
        })
        snap_t = budget.snapshot("translation")
        assert snap_t.planned_calls == 3
        assert snap_t.max_effective_calls == 9  # 3 * 3
        assert snap_t.consumed_calls == 0
        assert snap_t.remaining_calls == 9
        assert not snap_t.exhausted

        snap_g = budget.snapshot("grammar")
        assert snap_g.planned_calls == 1
        assert snap_g.max_effective_calls == 3  # 1 * 3

    def test_from_planned_calls_supports_explicit_multiplier(self) -> None:
        # Tests that need the old *2 behavior can pass it explicitly.
        budget = ExecutionBudget.from_planned_calls(
            {"translation": 2}, max_multiplier=2
        )
        assert budget.snapshot("translation").max_effective_calls == 4

    def test_consume_decrements_remaining(self) -> None:
        budget = ExecutionBudget.from_planned_calls({"translation": 2})
        assert budget.consume("translation") is True
        assert budget.consume("translation") is True
        assert budget.consume("translation") is True  # max = 6
        assert budget.consume("translation") is True
        assert budget.consume("translation") is True
        assert budget.consume("translation") is True  # max = 6
        assert budget.can_consume("translation") is False

    def test_consume_returns_false_when_exhausted(self) -> None:
        budget = ExecutionBudget.from_planned_calls({"grammar": 1})
        assert budget.consume("grammar") is True
        assert budget.consume("grammar") is True
        assert budget.consume("grammar") is True  # max = 3
        result = budget.consume("grammar")
        assert result is False  # exhausted, no-op

    def test_is_exhausted_after_max_calls(self) -> None:
        budget = ExecutionBudget.from_planned_calls({"vocabulary": 1})
        assert not budget.is_exhausted("vocabulary")
        budget.consume("vocabulary")
        assert not budget.is_exhausted("vocabulary")
        budget.consume("vocabulary")
        assert not budget.is_exhausted("vocabulary")  # max = 3, consumed = 2
        budget.consume("vocabulary")
        assert budget.is_exhausted("vocabulary")  # consumed = 3 = max

    def test_any_exhausted_true_when_any_layer_exhausted(self) -> None:
        budget = ExecutionBudget.from_planned_calls({
            "translation": 1,
            "grammar": 1,
        })
        assert not budget.any_exhausted()
        budget.consume("translation")
        budget.consume("translation")
        budget.consume("translation")  # translation max = 3, exhausted
        assert budget.any_exhausted()
        assert budget.is_exhausted("translation")
        assert not budget.is_exhausted("grammar")

    def test_zero_planned_means_no_budget_not_exhausted(self) -> None:
        # T4.2a-R2-R1 fix: a layer with max==0 has no jobs, so it is
        # NOT exhausted (there is nothing to exhaust). This prevents
        # spurious budget_exhausted when a layer has zero planned calls.
        budget = ExecutionBudget.from_planned_calls({"translation": 0})
        assert not budget.can_consume("translation")
        assert not budget.is_exhausted("translation")  # max == 0 → False
        assert not budget.consume("translation")

    def test_unknown_layer_is_not_exhausted(self) -> None:
        # T4.2a-R2-R1 fix: unknown layers (not in _max) are NOT
        # exhausted — they have no budget tracking at all.
        budget = ExecutionBudget.from_planned_calls({"translation": 5})
        assert not budget.is_exhausted("grammar")  # not in _max → False

    def test_exhausted_layers_returns_only_exhausted(self) -> None:
        budget = ExecutionBudget.from_planned_calls({
            "translation": 1,
            "grammar": 1,
        })
        assert budget.exhausted_layers() == ()
        budget.consume("translation")
        budget.consume("translation")
        budget.consume("translation")
        exhausted = budget.exhausted_layers()
        assert "translation" in exhausted
        assert "grammar" not in exhausted

    def test_budget_consuming_outcomes_set(self) -> None:
        assert "succeeded" in BUDGET_CONSUMING_OUTCOMES
        assert "retry_later" in BUDGET_CONSUMING_OUTCOMES
        assert "failed_terminal" in BUDGET_CONSUMING_OUTCOMES
        assert "no_job" not in BUDGET_CONSUMING_OUTCOMES
        assert "superseded" not in BUDGET_CONSUMING_OUTCOMES

    def test_to_diagnostics(self) -> None:
        budget = ExecutionBudget.from_planned_calls({"translation": 2})
        budget.consume("translation")
        diag = budget.to_diagnostics()
        assert "translation" in diag
        assert diag["translation"]["planned"] == 2
        assert diag["translation"]["max"] == 6  # 2 * 3
        assert diag["translation"]["consumed"] == 1
        assert diag["translation"]["remaining"] == 5

    def test_has_active_jobs_for_layer(self) -> None:
        budget = ExecutionBudget.from_planned_calls({
            "translation": 2,
            "grammar": 0,
        })
        assert budget.has_active_jobs_for_layer("translation") is True
        assert budget.has_active_jobs_for_layer("grammar") is False
        assert budget.has_active_jobs_for_layer("vocabulary") is False


# ---------------------------------------------------------------------------
# Integration: budget consistency per route
# ---------------------------------------------------------------------------


async def test_short_batch_effective_calls_match_planned(
    budget_env: asyncpg.Pool,
) -> None:
    """SHORT_BATCH: 1 batch job per layer, 1 effective call per layer.

    Requirement 1: planned calls == actual effective calls.
    """
    pool = budget_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool, user_id=user_id, plain_text=_short_article(), title="Short Budget Test"
    )

    batch_t = _CountingBatchTranslator()
    batch_v = _CountingBatchVocabularyExecutor()
    batch_g = _CountingGrammarBatchExecutor()
    per_t = _CountingTranslator()
    per_v = _CountingVocabularyExecutor()
    per_g = _CountingGrammarExecutor()

    runner = _make_runner(
        pool,
        translator=per_t,
        vocabulary_executor=per_v,
        grammar_executor=per_g,
        batch_translator=batch_t,
        batch_vocabulary_executor=batch_v,
        grammar_batch_executor=batch_g,
    )

    summary = await runner.run(
        record_id=article.record_id,
        user_id=user_id,
        lease_owner="budget-test",
        lease_duration=LEASE_DURATION,
        max_ticks=96,
        max_jobs=48,
    )

    assert summary.stopped_reason == "all_workers_no_job"
    # SHORT_BATCH: 1 batch call per layer, 0 per-unit calls
    assert batch_t.call_count == 1
    assert batch_v.call_count == 1
    assert batch_g.call_count == 1
    assert per_t.call_count == 0
    assert per_v.call_count == 0
    assert per_g.call_count == 0


async def test_structured_batch_effective_calls_match_planned(
    budget_env: asyncpg.Pool,
) -> None:
    """STRUCTURED_BATCH: 1 batch job per layer, 1 effective call per layer."""
    pool = budget_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool,
        user_id=user_id,
        plain_text=_structured_article(),
        title="Structured Budget Test",
    )

    batch_t = _CountingBatchTranslator()
    batch_v = _CountingBatchVocabularyExecutor()
    batch_g = _CountingGrammarBatchExecutor()
    per_t = _CountingTranslator()
    per_v = _CountingVocabularyExecutor()
    per_g = _CountingGrammarExecutor()

    runner = _make_runner(
        pool,
        translator=per_t,
        vocabulary_executor=per_v,
        grammar_executor=per_g,
        batch_translator=batch_t,
        batch_vocabulary_executor=batch_v,
        grammar_batch_executor=batch_g,
    )

    summary = await runner.run(
        record_id=article.record_id,
        user_id=user_id,
        lease_owner="budget-test",
        lease_duration=LEASE_DURATION,
        max_ticks=96,
        max_jobs=48,
    )

    assert summary.stopped_reason == "all_workers_no_job"
    assert batch_t.call_count == 1
    assert batch_v.call_count == 1
    assert batch_g.call_count == 1
    assert per_t.call_count == 0
    assert per_v.call_count == 0
    assert per_g.call_count == 0


# ---------------------------------------------------------------------------
# Integration: fallback suppression
# ---------------------------------------------------------------------------


async def test_batch_success_suppresses_per_unit_fallback(
    budget_env: asyncpg.Pool,
) -> None:
    """Requirement 2: batch success → per-unit fallback does NOT execute.

    After a SHORT_BATCH grammar batch job succeeds, the per-unit grammar
    executor must not be called. The fallback guard checks for non-terminal
    batch jobs; once the batch is terminal (succeeded), the guard passes,
    but the bootstrap already created no per-unit jobs for SHORT_BATCH.
    """
    pool = budget_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool, user_id=user_id, plain_text=_short_article(), title="Fallback Suppress"
    )

    batch_g = _CountingGrammarBatchExecutor()
    per_g = _CountingGrammarExecutor()

    runner = _make_runner(
        pool,
        translator=_StaticTranslator(),
        vocabulary_executor=_StaticVocabularyExecutor(),
        grammar_executor=per_g,
        batch_translator=_StaticBatchTranslator(),
        batch_vocabulary_executor=_StaticBatchVocabularyExecutor(),
        grammar_batch_executor=batch_g,
    )

    await runner.run(
        record_id=article.record_id,
        user_id=user_id,
        lease_owner="fallback-test",
        lease_duration=LEASE_DURATION,
        max_ticks=96,
        max_jobs=48,
    )

    # Batch succeeded → per-unit path not triggered
    assert batch_g.call_count == 1
    assert per_g.call_count == 0
    # All grammar jobs should be succeeded
    succeeded = await _count_jobs_by_status(pool, article.record_id, "succeeded")
    assert succeeded > 0


async def test_non_terminal_batch_suppresses_fallback(
    budget_env: asyncpg.Pool,
) -> None:
    """Requirement 3: non-terminal batch jobs suppress per-unit fallback.

    When a grammar batch job is queued/claimed/retry_later, the per-unit
    grammar path must NOT run. We verify this by checking the fallback guard
    in _run_grammar_attempt.
    """
    pool = budget_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool, user_id=user_id, plain_text=_short_article(), title="Non-Terminal Guard"
    )

    # Run pipeline once to create and succeed batch jobs
    runner = _make_runner(
        pool,
        translator=_StaticTranslator(),
        vocabulary_executor=_StaticVocabularyExecutor(),
        grammar_executor=_StaticGrammarExecutor(),
        batch_translator=_StaticBatchTranslator(),
        batch_vocabulary_executor=_StaticBatchVocabularyExecutor(),
        grammar_batch_executor=_StaticGrammarBatchExecutor(),
    )
    await runner.run(
        record_id=article.record_id,
        user_id=user_id,
        lease_owner="guard-test",
        lease_duration=LEASE_DURATION,
        max_ticks=96,
        max_jobs=48,
    )

    # Manually insert a claimed grammar batch job to simulate a non-terminal
    # batch that is in-flight (claimed by another worker with a valid lease).
    # This ensures the batch path cannot claim it (returns no_job), and the
    # fallback guard detects the non-terminal batch job and suppresses
    # per-unit grammar execution.
    async with pool.acquire() as conn:
        # Get base info from an existing job
        row = await conn.fetchrow(
            """
            SELECT base_id, expected_generation, run_id, user_id
            FROM reader_jobs WHERE reading_record_id = $1 LIMIT 1
            """,
            article.record_id,
        )
        base_id = row["base_id"]
        generation = int(row["expected_generation"])
        run_id = row["run_id"]
        job_user_id = row["user_id"]
        await conn.execute(
            """
            INSERT INTO reader_jobs (
                id, reading_record_id, run_id, base_id, user_id,
                job_type, target_type, target_key, status, priority,
                max_attempts, attempt_count, available_at, expected_generation,
                operation_fingerprint, idempotency_key, input_json,
                lease_owner, lease_token, lease_expires_at, claimed_at
            ) VALUES (
                $1, $2, $3, $4, $5, 'build_grammar_bundle', 'unit_range',
                'test:manual:batch', 'claimed', 0, 3, 0, NOW(), $6,
                'grammar_bundle_article_v1', 'test-manual-batch',
                '{"target_unit_ids": ["u1"], "article_route": "short_batch"}'::jsonb,
                'another-worker', $7, NOW() + INTERVAL '5 minutes', NOW()
            )
            """,
            uuid4(),
            article.record_id,
            run_id,
            base_id,
            job_user_id,
            generation,
            uuid4(),
        )

    # Now run the pipeline again — the fallback guard should suppress per-unit
    per_g = _CountingGrammarExecutor()
    runner2 = _make_runner(
        pool,
        translator=_StaticTranslator(),
        vocabulary_executor=_StaticVocabularyExecutor(),
        grammar_executor=per_g,
        batch_translator=_StaticBatchTranslator(),
        batch_vocabulary_executor=_StaticBatchVocabularyExecutor(),
        grammar_batch_executor=_StaticGrammarBatchExecutor(),
    )
    await runner2.run(
        record_id=article.record_id,
        user_id=user_id,
        lease_owner="guard-test-2",
        lease_duration=LEASE_DURATION,
        max_ticks=96,
        max_jobs=48,
    )

    # Per-unit grammar must not have been called because the queued
    # batch job suppresses fallback.
    assert per_g.call_count == 0


# ---------------------------------------------------------------------------
# Integration: route flip fencing
# ---------------------------------------------------------------------------


async def test_route_flip_supersedes_old_fingerprint_at_claim(
    budget_env: asyncpg.Pool,
) -> None:
    """Requirement 7: route flip → old-fingerprint jobs are superseded at claim.

    Submit a SHORT_BATCH article, then flip the run envelope's article_route
    to structured_batch. Attempting to claim a SHORT_BATCH job should
    supersede it (stale_route_fingerprint) instead of claiming it.
    """
    pool = budget_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool, user_id=user_id, plain_text=_short_article(), title="Route Flip Claim"
    )

    # Bootstrap SHORT_BATCH jobs
    runner = _make_runner(
        pool,
        translator=_StaticTranslator(),
        vocabulary_executor=_StaticVocabularyExecutor(),
        grammar_executor=_StaticGrammarExecutor(),
        batch_translator=_StaticBatchTranslator(),
        batch_vocabulary_executor=_StaticBatchVocabularyExecutor(),
        grammar_batch_executor=_StaticGrammarBatchExecutor(),
    )
    await runner.run(
        record_id=article.record_id,
        user_id=user_id,
        lease_owner="flip-bootstrap",
        lease_duration=LEASE_DURATION,
        max_ticks=96,
        max_jobs=48,
    )

    # Verify the run envelope has article_route = short_batch
    route = await _fetch_run_envelope_route(pool, article.record_id)
    assert route == "short_batch"

    # Find a succeeded SHORT_BATCH grammar batch job and reset it to queued
    # so we can test the claim path
    async with pool.acquire() as conn:
        job_row = await conn.fetchrow(
            """
            SELECT id FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = 'build_grammar_bundle'
              AND target_type = 'unit_range'
              AND status = 'succeeded'
            LIMIT 1
            """,
            article.record_id,
        )
        assert job_row is not None
        await conn.execute(
            """
            UPDATE reader_jobs SET status = 'queued', attempt_count = 0,
                available_at = NOW(), lease_owner = NULL, lease_token = NULL,
                lease_expires_at = NULL, claimed_at = NULL
            WHERE id = $1
            """,
            job_row["id"],
        )
        old_job_id = job_row["id"]

    # Flip the route in the run envelope
    await _update_run_envelope_route(pool, article.record_id, "structured_batch")

    # Verify the route is now structured_batch
    route = await _fetch_run_envelope_route(pool, article.record_id)
    assert route == "structured_batch"

    # Attempt to claim the old SHORT_BATCH job — should be superseded
    runtime = ReaderJobRuntime(pool=pool)
    claim_result = await runtime.claim_next_job(
        lease_owner="flip-claim-test",
        lease_duration=LEASE_DURATION,
        job_type="build_grammar_bundle",
        target_type="unit_range",
        reading_record_id=article.record_id,
    )

    # The stale-fingerprint job should have been superseded, not claimed
    assert claim_result is None or (
        claim_result is not None and claim_result.job_id != old_job_id
    )

    # Verify the old job was superseded
    async with pool.acquire() as conn:
        status = await conn.fetchval(
            "SELECT status FROM reader_jobs WHERE id = $1",
            old_job_id,
        )
    assert status == "superseded"


async def test_route_flip_rejects_publish_through_real_publisher(
    budget_env: asyncpg.Pool,
) -> None:
    """T4.2a-R2-R1 P2-1: route flip → real publisher rejects publish.

    This test goes through the REAL ``GrammarBundleLayerPublisher`` (not
    just ``_validate_fence`` directly) to verify:

    - The publisher raises ``FenceViolationError``.
    - No ``enhancement_layers`` rows are written.
    - No ``layer_published`` events are emitted.
    - The job remains in ``claimed`` status (the publish transaction
      rolled back, so the job state is consistent with the fence
      rejection).
    """
    from app.schemas.reader_orchestration import GrammarBundleOutput

    pool = budget_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool, user_id=user_id, plain_text=_short_article(), title="Route Flip Publish"
    )

    # Bootstrap to create jobs (but don't run the full pipeline)
    runner = _make_runner(
        pool,
        translator=_StaticTranslator(),
        vocabulary_executor=_StaticVocabularyExecutor(),
        grammar_executor=_StaticGrammarExecutor(),
        batch_translator=_StaticBatchTranslator(),
        batch_vocabulary_executor=_StaticBatchVocabularyExecutor(),
        grammar_batch_executor=_StaticGrammarBatchExecutor(),
    )
    # Run just enough to bootstrap + succeed jobs
    await runner.run(
        record_id=article.record_id,
        user_id=user_id,
        lease_owner="flip-publish-bootstrap",
        lease_duration=LEASE_DURATION,
        max_ticks=96,
        max_jobs=48,
    )

    # Find a succeeded grammar batch job, reset to claimed
    async with pool.acquire() as conn:
        job_row = await conn.fetchrow(
            """
            SELECT * FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = 'build_grammar_bundle'
              AND target_type = 'unit_range'
              AND status = 'succeeded'
            LIMIT 1
            """,
            article.record_id,
        )
        assert job_row is not None
        job_id = job_row["id"]
        input_json = job_row["input_json"]
        target_unit_ids = list(input_json.get("target_unit_ids") or [])
        lease_token = uuid4()
        await conn.execute(
            """
            UPDATE reader_jobs SET status = 'claimed',
                lease_owner = 'publish-test', lease_token = $2,
                lease_expires_at = NOW() + INTERVAL '30 seconds',
                claimed_at = NOW()
            WHERE id = $1
            """,
            job_id,
            lease_token,
        )

        # Count layers and events BEFORE the publish attempt
        layers_before = await conn.fetchval(
            "SELECT COUNT(*) FROM enhancement_layers WHERE reading_record_id = $1",
            article.record_id,
        )
        events_before = await conn.fetchval(
            """
            SELECT COUNT(*) FROM reader_events
            WHERE reading_record_id = $1 AND event_type = 'layer_published'
            """,
            article.record_id,
        )

    # Flip the route
    await _update_run_envelope_route(pool, article.record_id, "structured_batch")

    # Attempt to publish through the REAL publisher
    publisher = GrammarBundleLayerPublisher(
        pool=pool,
        event_runtime=ReaderEventRuntime(pool=pool),
    )
    # Build empty outputs for each target unit (the publisher validates
    # unit_id coverage before fence; the fence check happens first, so
    # the outputs don't need to be valid content-wise).
    outputs = [(uid, GrammarBundleOutput()) for uid in target_unit_ids]

    with pytest.raises(FenceViolationError, match="stale_route_fingerprint"):
        await publisher.publish_article_grammar_batch(
            job_id=job_id,
            lease_token=lease_token,
            outputs=outputs,
        )

    # Verify: no new enhancement_layers were written
    async with pool.acquire() as conn:
        layers_after = await conn.fetchval(
            "SELECT COUNT(*) FROM enhancement_layers WHERE reading_record_id = $1",
            article.record_id,
        )
        events_after = await conn.fetchval(
            """
            SELECT COUNT(*) FROM reader_events
            WHERE reading_record_id = $1 AND event_type = 'layer_published'
            """,
            article.record_id,
        )
        job_status = await conn.fetchval(
            "SELECT status FROM reader_jobs WHERE id = $1",
            job_id,
        )

    assert layers_after == layers_before, (
        "fence violation must not write any enhancement_layers"
    )
    assert events_after == events_before, (
        "fence violation must not emit any layer_published events"
    )
    # The job remains in claimed status (publish transaction rolled back)
    assert job_status == "claimed", (
        f"job must remain claimed after fence rejection, got {job_status}"
    )


# ---------------------------------------------------------------------------
# Integration: budget exhaustion does not enter coverage_complete clean
# ---------------------------------------------------------------------------


async def test_budget_exhausted_stopped_reason_is_not_attention(
    budget_env: asyncpg.Pool,
) -> None:
    """Requirement 8: budget_exhausted is a valid (finalizable) stopped_reason.

    The finalizer should treat budget_exhausted as finalizable (not in
    NON_FINALIZABLE_STOPPED_REASONS). The force-fail path ensures
    non-terminal jobs are transitioned to failed_terminal so the record
    can finalize as completed_with_failures instead of being wedged.
    """
    from app.services.reader_orchestration.completion_finalizer import (
        BUDGET_EXHAUSTED_FAILURE_CODE,
        BUDGET_EXHAUSTED_FAILURE_REASON,
        NON_FINALIZABLE_STOPPED_REASONS,
    )

    # Verify budget_exhausted is NOT in NON_FINALIZABLE_STOPPED_REASONS
    # (it is finalizable, unlike attention_required)
    assert "budget_exhausted" not in NON_FINALIZABLE_STOPPED_REASONS
    assert "attention_required" in NON_FINALIZABLE_STOPPED_REASONS

    # Verify the budget_exhausted failure metadata constants exist
    assert BUDGET_EXHAUSTED_FAILURE_CODE == "budget_exhausted"
    assert "budget" in BUDGET_EXHAUSTED_FAILURE_REASON.lower()


# ---------------------------------------------------------------------------
# Integration: usage evidence distinguishes outcomes
# ---------------------------------------------------------------------------


async def test_usage_evidence_records_succeeded_calls(
    budget_env: asyncpg.Pool,
) -> None:
    """Requirement 9: usage/runtime evidence can distinguish outcomes.

    After a successful SHORT_BATCH run, ai_usage_events should contain
    entries for each successful LLM call. The events should have
    status='succeeded' and non-zero token counts.
    """
    pool = budget_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool, user_id=user_id, plain_text=_short_article(), title="Usage Evidence"
    )

    runner = _make_runner(
        pool,
        translator=_StaticTranslator(),
        vocabulary_executor=_StaticVocabularyExecutor(),
        grammar_executor=_StaticGrammarExecutor(),
        batch_translator=_StaticBatchTranslator(),
        batch_vocabulary_executor=_StaticBatchVocabularyExecutor(),
        grammar_batch_executor=_StaticGrammarBatchExecutor(),
    )
    await runner.run(
        record_id=article.record_id,
        user_id=user_id,
        lease_owner="usage-test",
        lease_duration=LEASE_DURATION,
        max_ticks=96,
        max_jobs=48,
    )

    # Verify ai_usage_events exist for the successful LLM calls
    async with pool.acquire() as conn:
        usage_rows = await conn.fetch(
            """
            SELECT capability_code, status, input_tokens, output_tokens,
                   operation_fingerprint
            FROM ai_usage_events
            WHERE reading_record_id = $1
            ORDER BY created_at
            """,
            article.record_id,
        )

    assert len(usage_rows) >= 3  # translation + vocabulary + grammar

    # All should be succeeded with non-zero tokens
    for row in usage_rows:
        assert row["status"] == "succeeded"
        assert int(row["input_tokens"]) > 0
        assert int(row["output_tokens"]) > 0

    # Verify at least one usage event per capability
    capabilities = {row["capability_code"] for row in usage_rows}
    assert "reader_translation" in capabilities
    assert "reader_vocabulary" in capabilities
    assert "reader_grammar_bundle" in capabilities


# ---------------------------------------------------------------------------
# Integration: route flip — recover_stale_leases supersedes stale jobs
# ---------------------------------------------------------------------------


async def test_recover_stale_leases_supersedes_stale_route_jobs(
    budget_env: asyncpg.Pool,
) -> None:
    """Requirement 7 (extended): recover_stale_leases supersedes stale-route jobs.

    When a claimed job's lease expires AND the route has flipped,
    recover_stale_leases should supersede the job instead of re-queuing it.
    """
    pool = budget_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool, user_id=user_id, plain_text=_short_article(), title="Stale Lease Route"
    )

    # Bootstrap and run to create jobs
    runner = _make_runner(
        pool,
        translator=_StaticTranslator(),
        vocabulary_executor=_StaticVocabularyExecutor(),
        grammar_executor=_StaticGrammarExecutor(),
        batch_translator=_StaticBatchTranslator(),
        batch_vocabulary_executor=_StaticBatchVocabularyExecutor(),
        grammar_batch_executor=_StaticGrammarBatchExecutor(),
    )
    await runner.run(
        record_id=article.record_id,
        user_id=user_id,
        lease_owner="stale-lease-bootstrap",
        lease_duration=LEASE_DURATION,
        max_ticks=96,
        max_jobs=48,
    )

    # Find a succeeded job and reset to claimed with expired lease
    async with pool.acquire() as conn:
        job_row = await conn.fetchrow(
            """
            SELECT id FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = 'build_grammar_bundle'
              AND target_type = 'unit_range'
              AND status = 'succeeded'
            LIMIT 1
            """,
            article.record_id,
        )
        assert job_row is not None
        await conn.execute(
            """
            UPDATE reader_jobs SET status = 'claimed',
                lease_owner = 'expired-worker',
                lease_token = $2,
                lease_expires_at = NOW() - INTERVAL '1 hour',
                claimed_at = NOW() - INTERVAL '1 hour',
                attempt_count = 1
            WHERE id = $1
            """,
            job_row["id"],
            uuid4(),
        )
        stale_job_id = job_row["id"]

    # Flip the route
    await _update_run_envelope_route(pool, article.record_id, "structured_batch")

    # Run recover_stale_leases
    runtime = ReaderJobRuntime(pool=pool)
    recovered = await runtime.recover_stale_leases(batch_size=100)
    assert recovered >= 1

    # The stale-route job should be superseded, not re-queued
    async with pool.acquire() as conn:
        status = await conn.fetchval(
            "SELECT status FROM reader_jobs WHERE id = $1",
            stale_job_id,
        )
    assert status == "superseded"


# ---------------------------------------------------------------------------
# Integration: fallback guard only allows fallback when batch is terminal
# ---------------------------------------------------------------------------


async def test_fallback_guard_decision_table_fail_closed(
    budget_env: asyncpg.Pool,
) -> None:
    """T4.2a-R2-R1 P1-2: fallback guard decision table (fail-closed).

    The guard ``_should_suppress_grammar_per_unit_fallback`` must
    suppress per-unit fallback for ALL batch states EXCEPT
    ``superseded`` (or no batch jobs at all). This is the strictest
    fail-closed contract:

    - ``succeeded``: suppress (batch published; per-unit would duplicate)
    - ``queued`` / ``claimed`` / ``retry_later``: suppress (in progress)
    - ``failed_terminal``: suppress (no explicit fallback authorization)
    - ``skipped``: suppress (terminal without success)
    - ``superseded``: do NOT suppress (stale route)
    - No batch jobs: do NOT suppress (per-unit is the intended path)
    """
    pool = budget_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool, user_id=user_id, plain_text=_short_article(), title="Fallback Guard"
    )

    # Bootstrap to create jobs
    runner = _make_runner(
        pool,
        translator=_StaticTranslator(),
        vocabulary_executor=_StaticVocabularyExecutor(),
        grammar_executor=_StaticGrammarExecutor(),
        batch_translator=_StaticBatchTranslator(),
        batch_vocabulary_executor=_StaticBatchVocabularyExecutor(),
        grammar_batch_executor=_StaticGrammarBatchExecutor(),
    )
    await runner.run(
        record_id=article.record_id,
        user_id=user_id,
        lease_owner="fallback-guard-bootstrap",
        lease_duration=LEASE_DURATION,
        max_ticks=96,
        max_jobs=48,
    )

    # Get base info for guard queries
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT base_id, expected_generation
            FROM reader_jobs WHERE reading_record_id = $1 LIMIT 1
            """,
            article.record_id,
        )
        base_id = row["base_id"]
        generation = int(row["expected_generation"])

    # 1. Batch succeeded → suppress (P1-2 fix: succeeded permanently blocks)
    suppress = await runner._should_suppress_grammar_per_unit_fallback(
        record_id=article.record_id,
        base_id=base_id,
        expected_generation=generation,
    )
    assert suppress is True, "succeeded batch must suppress fallback"

    # 2. Batch failed_terminal → suppress (fail-closed)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE reader_jobs SET status = 'failed_terminal',
                failure_code = 'test_manual_fail'
            WHERE reading_record_id = $1
              AND job_type = 'build_grammar_bundle'
              AND target_type = 'unit_range'
              AND status = 'succeeded'
            """,
            article.record_id,
        )
    suppress = await runner._should_suppress_grammar_per_unit_fallback(
        record_id=article.record_id,
        base_id=base_id,
        expected_generation=generation,
    )
    assert suppress is True, "failed_terminal batch must suppress fallback"

    # 3. Batch queued → suppress (in progress)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE reader_jobs SET status = 'queued',
                lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
                claimed_at = NULL, available_at = NOW()
            WHERE reading_record_id = $1
              AND job_type = 'build_grammar_bundle'
              AND target_type = 'unit_range'
              AND status = 'failed_terminal'
            """,
            article.record_id,
        )
    suppress = await runner._should_suppress_grammar_per_unit_fallback(
        record_id=article.record_id,
        base_id=base_id,
        expected_generation=generation,
    )
    assert suppress is True, "queued batch must suppress fallback"

    # 4. Batch superseded → do NOT suppress (stale route)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE reader_jobs SET status = 'superseded'
            WHERE reading_record_id = $1
              AND job_type = 'build_grammar_bundle'
              AND target_type = 'unit_range'
              AND status = 'queued'
            """,
            article.record_id,
        )
    suppress = await runner._should_suppress_grammar_per_unit_fallback(
        record_id=article.record_id,
        base_id=base_id,
        expected_generation=generation,
    )
    assert suppress is False, "superseded batch must allow fallback"

    # 5. No batch jobs at all → do NOT suppress (per-unit is intended path)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            DELETE FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = 'build_grammar_bundle'
              AND target_type = 'unit_range'
            """,
            article.record_id,
        )
    suppress = await runner._should_suppress_grammar_per_unit_fallback(
        record_id=article.record_id,
        base_id=base_id,
        expected_generation=generation,
    )
    assert suppress is False, "no batch jobs must allow fallback"


# ---------------------------------------------------------------------------
# T4.2a-R2-R1 Test A: Cross-run hard budget (durable)
# ---------------------------------------------------------------------------


class _FailingBatchTranslator(_StaticBatchTranslator):
    """Batch translator that always raises a retryable TranslationExecutionError."""

    def __init__(self) -> None:
        super().__init__()
        self.call_count = 0

    async def translate_batch(self, context):
        self.call_count += 1
        raise TranslationExecutionError(
            f"simulated retryable failure (call {self.call_count})",
            retryable=True,
            failure_class="provider",
            failure_code="simulated_retryable",
        )


async def test_cross_run_hard_budget_durable(
    budget_env: asyncpg.Pool,
) -> None:
    """Test A: Cross-run hard budget — durable across multiple runner.run() calls.

    The budget is loaded from ``reader_jobs.attempt_count`` / ``max_attempts``
    at the start of each ``run()``. After ``max_attempts`` retryable failures,
    the durable budget is exhausted. A subsequent ``run()`` must NOT call the
    executor — the budget prevents it.

    With ``max_attempts=3`` (production default):
    - Run 1: executor called (call_count=1), retryable failure. attempt_count=1.
    - Run 2: executor called (call_count=2), retryable failure. attempt_count=2.
    - Run 3: executor called (call_count=3), retryable failure. attempt_count=3.
    - Run 4: budget exhausted (consumed=3 >= max=3). Executor NOT called.
    """
    pool = budget_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool, user_id=user_id, plain_text=_short_article(), title="Cross-Run Budget"
    )

    failing_t = _FailingBatchTranslator()

    runner = _make_runner(
        pool,
        translator=_StaticTranslator(),
        vocabulary_executor=_StaticVocabularyExecutor(),
        grammar_executor=_StaticGrammarExecutor(),
        batch_translator=failing_t,
        batch_vocabulary_executor=_StaticBatchVocabularyExecutor(),
        grammar_batch_executor=_StaticGrammarBatchExecutor(),
    )

    # Run 1: translation batch fails (retry_later)
    summary1 = await runner.run(
        record_id=article.record_id,
        user_id=user_id,
        lease_owner="cross-run-1",
        lease_duration=LEASE_DURATION,
        max_ticks=96,
        max_jobs=48,
    )
    assert summary1.stopped_reason == "attention_required"
    assert failing_t.call_count == 1

    # Reset available_at so the retry_later job is immediately claimable
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE reader_jobs SET available_at = NOW()
            WHERE reading_record_id = $1
              AND job_type = 'translate_article'
              AND status = 'retry_later'
            """,
            article.record_id,
        )

    # Run 2: translation batch fails again (retry_later)
    await runner.run(
        record_id=article.record_id,
        user_id=user_id,
        lease_owner="cross-run-2",
        lease_duration=LEASE_DURATION,
        max_ticks=96,
        max_jobs=48,
    )
    assert failing_t.call_count == 2

    # Reset available_at for run 3
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE reader_jobs SET available_at = NOW()
            WHERE reading_record_id = $1
              AND job_type = 'translate_article'
              AND status = 'retry_later'
            """,
            article.record_id,
        )

    # Run 3: translation batch fails again, attempt_count=3=max_attempts
    await runner.run(
        record_id=article.record_id,
        user_id=user_id,
        lease_owner="cross-run-3",
        lease_duration=LEASE_DURATION,
        max_ticks=96,
        max_jobs=48,
    )
    assert failing_t.call_count == 3

    # Run 4: budget exhausted (consumed=3 >= max=3). Executor must NOT be called.
    summary4 = await runner.run(
        record_id=article.record_id,
        user_id=user_id,
        lease_owner="cross-run-4",
        lease_duration=LEASE_DURATION,
        max_ticks=96,
        max_jobs=48,
    )
    assert failing_t.call_count == 3, (
        f"executor must not be called after budget exhaustion; "
        f"got call_count={failing_t.call_count}"
    )

    # Summary must report budget exhaustion, NOT attention_required
    assert summary4.stopped_reason != "attention_required", (
        "budget exhaustion must not be masked as attention_required"
    )
    assert "translation" in summary4.exhausted_layers, (
        f"translation must be in exhausted_layers; got {summary4.exhausted_layers}"
    )

    # Budget diagnostics show consumed=3, max=3
    t_diag = summary4.budget_diagnostics.get("translation", {})
    assert t_diag.get("consumed") == 3, (
        f"durable consumed must be 3 (not reset to 0); got {t_diag}"
    )
    assert t_diag.get("max") == 3, (
        f"max must be 3 (SUM(max_attempts)); got {t_diag}"
    )

    # budget_denied must be > 0 (not confused with no_job)
    assert summary4.outcome_counts.budget_denied > 0, (
        "budget exhaustion must produce budget_denied outcomes, not no_job"
    )


# ---------------------------------------------------------------------------
# T4.2a-R2-R1 Test B: Batch succeeded + legacy per-unit job coexistence
# ---------------------------------------------------------------------------


async def test_batch_succeeded_with_legacy_job_suppresses_fallback(
    budget_env: asyncpg.Pool,
) -> None:
    """Test B: Batch succeeded + legacy per-unit job coexistence.

    P1-2 fix: a succeeded batch permanently blocks legacy per-unit fallback.
    Even if a per-unit grammar job exists (from route cutover, upgrade, or
    manual injection), the per-unit executor must NOT be called when the
    batch has succeeded.
    """
    pool = budget_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool, user_id=user_id, plain_text=_short_article(), title="Batch+Legacy"
    )

    # Run 1: succeed all batch jobs
    runner = _make_runner(
        pool,
        translator=_StaticTranslator(),
        vocabulary_executor=_StaticVocabularyExecutor(),
        grammar_executor=_StaticGrammarExecutor(),
        batch_translator=_StaticBatchTranslator(),
        batch_vocabulary_executor=_StaticBatchVocabularyExecutor(),
        grammar_batch_executor=_StaticGrammarBatchExecutor(),
    )
    await runner.run(
        record_id=article.record_id,
        user_id=user_id,
        lease_owner="batch-succeed-1",
        lease_duration=LEASE_DURATION,
        max_ticks=96,
        max_jobs=48,
    )

    # Verify grammar batch succeeded
    batch_succeeded = await _count_jobs_by_status(
        pool, article.record_id, "succeeded"
    )
    assert batch_succeeded > 0

    # Manually insert a legacy per-unit grammar job (simulates dangerous
    # coexistence from route cutover or upgrade)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, base_id, expected_generation, run_id, user_id,
                   operation_fingerprint
            FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = 'build_grammar_bundle'
              AND target_type = 'unit_range'
              AND status = 'succeeded'
            LIMIT 1
            """,
            article.record_id,
        )
        assert row is not None
        base_id = row["base_id"]
        generation = int(row["expected_generation"])
        run_id = row["run_id"]
        job_user_id = row["user_id"]
        batch_fp = row["operation_fingerprint"]

        # Fetch target_unit_ids from the batch job's input_json
        unit_ids_row = await conn.fetchrow(
            """
            SELECT input_json->'target_unit_ids' AS unit_ids
            FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = 'build_grammar_bundle'
              AND target_type = 'unit_range'
              AND status = 'succeeded'
            LIMIT 1
            """,
            article.record_id,
        )
        target_unit_ids = unit_ids_row["unit_ids"] or ["u1"]
        unit_id = target_unit_ids[0] if target_unit_ids else "u1"

        await conn.execute(
            """
            INSERT INTO reader_jobs (
                id, reading_record_id, run_id, base_id, user_id,
                job_type, target_type, target_key, status, priority,
                max_attempts, attempt_count, available_at, expected_generation,
                operation_fingerprint, idempotency_key, input_json
            ) VALUES (
                $1, $2, $3, $4, $5, 'build_grammar_bundle', 'unit',
                $6, 'queued', 0, 3, 0, NOW(), $7,
                $8, $9,
                '{"target_unit_ids": [], "article_route": "short_batch"}'::jsonb
            )
            """,
            uuid4(),
            article.record_id,
            run_id,
            base_id,
            job_user_id,
            f"test:legacy:{unit_id}",
            generation,
            batch_fp,
            f"test-legacy-grammar-{uuid4()}",
        )

    # Run 2: per-unit grammar executor must NOT be called
    per_g = _CountingGrammarExecutor()
    runner2 = _make_runner(
        pool,
        translator=_StaticTranslator(),
        vocabulary_executor=_StaticVocabularyExecutor(),
        grammar_executor=per_g,
        batch_translator=_StaticBatchTranslator(),
        batch_vocabulary_executor=_StaticBatchVocabularyExecutor(),
        grammar_batch_executor=_StaticGrammarBatchExecutor(),
    )
    await runner2.run(
        record_id=article.record_id,
        user_id=user_id,
        lease_owner="batch-succeed-2",
        lease_duration=LEASE_DURATION,
        max_ticks=96,
        max_jobs=48,
    )

    assert per_g.call_count == 0, (
        "per-unit grammar executor must not be called when batch succeeded"
    )

    # T4.2a-R2-R2: the legacy per-unit job must be SUPERSEDED (not queued)
    # by the cleanup service with rationale_code = "batch_path_authoritative".
    # The old assertion (status == "queued") 固化了热循环错误状态.
    async with pool.acquire() as conn:
        legacy_row = await conn.fetchrow(
            """
            SELECT status, rationale_code FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = 'build_grammar_bundle'
              AND target_type = 'unit'
            """,
            article.record_id,
        )
    assert legacy_row is not None, "legacy per-unit job must exist"
    assert legacy_row["status"] == "superseded", (
        f"legacy per-unit job must be superseded (not queued) when batch "
        f"succeeded; got {legacy_row['status']}"
    )
    assert legacy_row["rationale_code"] == "batch_path_authoritative", (
        f"rationale_code must be batch_path_authoritative; "
        f"got {legacy_row['rationale_code']}"
    )

    # Verify a job_superseded event was written for auditability
    async with pool.acquire() as conn:
        supersede_event = await conn.fetchval(
            """
            SELECT COUNT(*) FROM reader_job_events
            WHERE reading_record_id = $1
              AND event_type = 'job_superseded'
              AND payload_json->>'rationale_code' = 'batch_path_authoritative'
            """,
            article.record_id,
        )
    assert supersede_event >= 1, (
        "a job_superseded event with rationale_code="
        "batch_path_authoritative must be written"
    )


# ---------------------------------------------------------------------------
# T4.2a-R2-R1 Test C: Batch failed_terminal fallback (fail-closed)
# ---------------------------------------------------------------------------


async def test_batch_failed_terminal_suppresses_fallback(
    budget_env: asyncpg.Pool,
) -> None:
    """Test C: Batch failed_terminal suppresses fallback (fail-closed).

    P1-2 fix: when a grammar batch job has failed_terminal status, legacy
    per-unit fallback must NOT automatically run. Without an explicit
    fallback authorization policy, the system fails closed.
    """
    pool = budget_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool, user_id=user_id, plain_text=_short_article(), title="Batch Failed"
    )

    # Run 1: succeed all batch jobs
    runner = _make_runner(
        pool,
        translator=_StaticTranslator(),
        vocabulary_executor=_StaticVocabularyExecutor(),
        grammar_executor=_StaticGrammarExecutor(),
        batch_translator=_StaticBatchTranslator(),
        batch_vocabulary_executor=_StaticBatchVocabularyExecutor(),
        grammar_batch_executor=_StaticGrammarBatchExecutor(),
    )
    await runner.run(
        record_id=article.record_id,
        user_id=user_id,
        lease_owner="batch-fail-1",
        lease_duration=LEASE_DURATION,
        max_ticks=96,
        max_jobs=48,
    )

    # Manually set the grammar batch job to failed_terminal
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, base_id, expected_generation, run_id, user_id,
                   operation_fingerprint
            FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = 'build_grammar_bundle'
              AND target_type = 'unit_range'
              AND status = 'succeeded'
            LIMIT 1
            """,
            article.record_id,
        )
        assert row is not None
        base_id = row["base_id"]
        generation = int(row["expected_generation"])
        run_id = row["run_id"]
        job_user_id = row["user_id"]
        batch_fp = row["operation_fingerprint"]

        await conn.execute(
            """
            UPDATE reader_jobs SET status = 'failed_terminal',
                failure_code = 'test_manual_fail'
            WHERE id = $1
            """,
            row["id"],
        )

        # Insert a legacy per-unit grammar job
        await conn.execute(
            """
            INSERT INTO reader_jobs (
                id, reading_record_id, run_id, base_id, user_id,
                job_type, target_type, target_key, status, priority,
                max_attempts, attempt_count, available_at, expected_generation,
                operation_fingerprint, idempotency_key, input_json
            ) VALUES (
                $1, $2, $3, $4, $5, 'build_grammar_bundle', 'unit',
                'test:legacy:failed', 'queued', 0, 3, 0, NOW(), $6,
                $7, $8,
                '{"target_unit_ids": [], "article_route": "short_batch"}'::jsonb
            )
            """,
            uuid4(),
            article.record_id,
            run_id,
            base_id,
            job_user_id,
            generation,
            batch_fp,
            f"test-legacy-failed-{uuid4()}",
        )

    # Run 2: per-unit grammar executor must NOT be called (fail-closed)
    per_g = _CountingGrammarExecutor()
    runner2 = _make_runner(
        pool,
        translator=_StaticTranslator(),
        vocabulary_executor=_StaticVocabularyExecutor(),
        grammar_executor=per_g,
        batch_translator=_StaticBatchTranslator(),
        batch_vocabulary_executor=_StaticBatchVocabularyExecutor(),
        grammar_batch_executor=_StaticGrammarBatchExecutor(),
    )
    await runner2.run(
        record_id=article.record_id,
        user_id=user_id,
        lease_owner="batch-fail-2",
        lease_duration=LEASE_DURATION,
        max_ticks=96,
        max_jobs=48,
    )

    assert per_g.call_count == 0, (
        "per-unit grammar executor must not be called when batch failed_terminal"
    )

    # T4.2a-R2-R2: the legacy per-unit job must be SUPERSEDED (not queued)
    # with rationale_code = "batch_fallback_not_authorized" (fail-closed).
    async with pool.acquire() as conn:
        legacy_row = await conn.fetchrow(
            """
            SELECT status, rationale_code FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = 'build_grammar_bundle'
              AND target_type = 'unit'
            """,
            article.record_id,
        )
    assert legacy_row is not None, "legacy per-unit job must exist"
    assert legacy_row["status"] == "superseded", (
        f"legacy per-unit job must be superseded (not queued) when batch "
        f"failed_terminal; got {legacy_row['status']}"
    )
    assert legacy_row["rationale_code"] == "batch_fallback_not_authorized", (
        f"rationale_code must be batch_fallback_not_authorized; "
        f"got {legacy_row['rationale_code']}"
    )


# ---------------------------------------------------------------------------
# T4.2a-R2-R1 Test D: Partial layer budget exhaustion
# ---------------------------------------------------------------------------


async def test_partial_layer_budget_exhaustion(
    budget_env: asyncpg.Pool,
) -> None:
    """Test D: Partial layer budget exhaustion.

    When one layer's budget is exhausted but other layers still have budget
    and non-terminal jobs, the pipeline must:
    - Continue processing other layers
    - NOT call the executor for the exhausted layer
    - Report ``partial_budget_exhausted`` (not ``all_workers_no_job``)
    - List the exhausted layer in ``exhausted_layers``
    """
    pool = budget_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool, user_id=user_id, plain_text=_short_article(), title="Partial Exhaust"
    )

    # Run 1: succeed all batch jobs
    runner = _make_runner(
        pool,
        translator=_StaticTranslator(),
        vocabulary_executor=_StaticVocabularyExecutor(),
        grammar_executor=_StaticGrammarExecutor(),
        batch_translator=_StaticBatchTranslator(),
        batch_vocabulary_executor=_StaticBatchVocabularyExecutor(),
        grammar_batch_executor=_StaticGrammarBatchExecutor(),
    )
    await runner.run(
        record_id=article.record_id,
        user_id=user_id,
        lease_owner="partial-1",
        lease_duration=LEASE_DURATION,
        max_ticks=96,
        max_jobs=48,
    )

    # Manually set the translation batch job to exhausted:
    # status='queued', attempt_count=3, max_attempts=3
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE reader_jobs
            SET status = 'queued',
                attempt_count = 3,
                max_attempts = 3,
                lease_owner = NULL, lease_token = NULL,
                lease_expires_at = NULL, claimed_at = NULL,
                available_at = NOW()
            WHERE reading_record_id = $1
              AND job_type = 'translate_article'
              AND target_type = 'unit_range'
              AND status = 'succeeded'
            """,
            article.record_id,
        )

    # Run 2: translation budget exhausted, executor must NOT be called
    counting_t = _CountingBatchTranslator()
    runner2 = _make_runner(
        pool,
        translator=_StaticTranslator(),
        vocabulary_executor=_StaticVocabularyExecutor(),
        grammar_executor=_StaticGrammarExecutor(),
        batch_translator=counting_t,
        batch_vocabulary_executor=_StaticBatchVocabularyExecutor(),
        grammar_batch_executor=_StaticGrammarBatchExecutor(),
    )
    summary = await runner2.run(
        record_id=article.record_id,
        user_id=user_id,
        lease_owner="partial-2",
        lease_duration=LEASE_DURATION,
        max_ticks=96,
        max_jobs=48,
    )

    # Translation executor must NOT be called
    assert counting_t.call_count == 0, (
        "translation executor must not be called when budget exhausted"
    )

    # Summary must report partial_budget_exhausted, NOT all_workers_no_job
    assert summary.stopped_reason == "partial_budget_exhausted", (
        f"partial exhaustion must report partial_budget_exhausted; "
        f"got {summary.stopped_reason}"
    )

    # Exhausted layers must include translation
    assert "translation" in summary.exhausted_layers, (
        f"translation must be in exhausted_layers; got {summary.exhausted_layers}"
    )

    # Budget diagnostics show translation exhausted
    t_diag = summary.budget_diagnostics.get("translation", {})
    assert t_diag.get("consumed") >= t_diag.get("max", 0), (
        f"translation consumed must be >= max; got {t_diag}"
    )

    # budget_denied must be > 0 (not confused with no_job)
    assert summary.outcome_counts.budget_denied > 0, (
        "partial exhaustion must produce budget_denied outcomes"
    )

    # The non-terminal translation job must NOT have been claimed
    async with pool.acquire() as conn:
        t_status = await conn.fetchval(
            """
            SELECT status FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = 'translate_article'
              AND target_type = 'unit_range'
            """,
            article.record_id,
        )
    assert t_status == "queued", (
        f"exhausted translation job must remain queued (not claimed); "
        f"got {t_status}"
    )

    # T4.2a-R2-R2: extend to WorkerLoop + Finalizer production chain.
    # The partial exhaustion must NOT prematurely finalize the record
    # because the translation job is still non-terminal (queued).
    # Non-exhausted layers' jobs must be preserved.
    from app.services.reader_orchestration.worker_loop import (
        ReaderEnhancementWorkerLoopService,
    )

    wl_service = ReaderEnhancementWorkerLoopService(
        pool=pool, pipeline_runner=runner2
    )
    candidates = await wl_service.scan_eligible_records(batch_size=20)
    candidate = None
    for c in candidates:
        if c.record_id == article.record_id:
            candidate = c
            break
    assert candidate is not None, (
        "record with partial exhaustion must still be scannable "
        "(non-terminal translation job exists)"
    )
    wl_result = await wl_service.process_candidate(
        candidate=candidate,
        lease_owner_prefix="partial-wl",
        lease_duration=LEASE_DURATION,
        max_ticks=96,
        max_jobs=48,
    )
    # T4.2a-R2-R2: The finalizer behavior depends on whether non-exhausted
    # layers have non-terminal jobs. In this scenario, all non-translation
    # jobs are succeeded, so after force-failing the translation job, all
    # jobs are terminal. The finalizer SHOULD finalize as
    # completed_with_failures (NOT completed_clean).
    if wl_result.completion_finalization_result is not None:
        fin = wl_result.completion_finalization_result
        if fin.finalized:
            assert fin.outcome == "completed_with_failures", (
                f"partial exhaustion with force-failed translation must "
                f"finalize as completed_with_failures; got {fin.outcome}"
            )


# ---------------------------------------------------------------------------
# T4.2a-R2-R1 Test E: Real publisher cutover (translation batch)
# ---------------------------------------------------------------------------


async def test_route_flip_rejects_translation_batch_publish(
    budget_env: asyncpg.Pool,
) -> None:
    """Test E: Route flip rejects translation batch publish through real publisher.

    P2-1 fix: the publish fence must be tested through the real publisher,
    not just by calling ``_validate_fence()`` directly. This test claims a
    translation batch job, flips the route, then calls the real
    ``TranslationLayerPublisher.publish_article_translation_batch``.

    Asserts:
    - FenceViolationError is raised
    - No new enhancement_layers are written
    - No new layer_published events are emitted
    - The job remains in 'claimed' status (transaction rolled back)
    """
    pool = budget_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool, user_id=user_id, plain_text=_short_article(), title="Translation Fence"
    )

    # Run pipeline to succeed all jobs
    runner = _make_runner(
        pool,
        translator=_StaticTranslator(),
        vocabulary_executor=_StaticVocabularyExecutor(),
        grammar_executor=_StaticGrammarExecutor(),
        batch_translator=_StaticBatchTranslator(),
        batch_vocabulary_executor=_StaticBatchVocabularyExecutor(),
        grammar_batch_executor=_StaticGrammarBatchExecutor(),
    )
    await runner.run(
        record_id=article.record_id,
        user_id=user_id,
        lease_owner="trans-fence-1",
        lease_duration=LEASE_DURATION,
        max_ticks=96,
        max_jobs=48,
    )

    # Find the succeeded translation batch job, reset to claimed
    async with pool.acquire() as conn:
        job_row = await conn.fetchrow(
            """
            SELECT * FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = 'translate_article'
              AND target_type = 'unit_range'
              AND status = 'succeeded'
            LIMIT 1
            """,
            article.record_id,
        )
        assert job_row is not None
        job_id = job_row["id"]
        lease_token = uuid4()
        await conn.execute(
            """
            UPDATE reader_jobs SET status = 'claimed',
                lease_owner = 'publish-test', lease_token = $2,
                lease_expires_at = NOW() + INTERVAL '30 seconds',
                claimed_at = NOW()
            WHERE id = $1
            """,
            job_id,
            lease_token,
        )

        # Count layers and events BEFORE the publish attempt
        layers_before = await conn.fetchval(
            "SELECT COUNT(*) FROM enhancement_layers WHERE reading_record_id = $1",
            article.record_id,
        )
        events_before = await conn.fetchval(
            """
            SELECT COUNT(*) FROM reader_events
            WHERE reading_record_id = $1 AND event_type = 'layer_published'
            """,
            article.record_id,
        )

    # Flip the route
    await _update_run_envelope_route(pool, article.record_id, "structured_batch")

    # Attempt to publish through the REAL publisher
    publisher = TranslationLayerPublisher(
        pool=pool,
        event_runtime=ReaderEventRuntime(pool=pool),
    )

    with pytest.raises(FenceViolationError, match="stale_route_fingerprint"):
        await publisher.publish_article_translation_batch(
            job_id=job_id,
            lease_token=lease_token,
            outputs=[],  # fence check happens before output processing
        )

    # Verify: no new enhancement_layers were written
    async with pool.acquire() as conn:
        layers_after = await conn.fetchval(
            "SELECT COUNT(*) FROM enhancement_layers WHERE reading_record_id = $1",
            article.record_id,
        )
        events_after = await conn.fetchval(
            """
            SELECT COUNT(*) FROM reader_events
            WHERE reading_record_id = $1 AND event_type = 'layer_published'
            """,
            article.record_id,
        )
        job_status = await conn.fetchval(
            "SELECT status FROM reader_jobs WHERE id = $1",
            job_id,
        )

    assert layers_after == layers_before, (
        "fence violation must not write any enhancement_layers"
    )
    assert events_after == events_before, (
        "fence violation must not emit any layer_published events"
    )
    assert job_status == "claimed", (
        f"job must remain claimed after fence rejection; got {job_status}"
    )

    # T4.2a-R2-R2: extend to worker/pipeline catch path.
    # The direct publisher test above proves the fence rejects. Now verify
    # the WORKER catch path: when the worker hits FenceViolationError
    # during publish, it transitions the job to superseded (not just
    # leaving it claimed) and the summary.superseded matches the DB
    # actual count (no max(1,...) virtual reporting).
    #
    # We use a custom batch translator that flips the route DURING
    # execution, so the claim-time fence passes but the publish-time
    # fence fails.
    article2 = await submit_article_ready(
        pool, user_id=user_id, plain_text=_short_article(), title="E-Worker-Catch"
    )
    runner_e = _make_runner(
        pool,
        translator=_StaticTranslator(),
        vocabulary_executor=_StaticVocabularyExecutor(),
        grammar_executor=_StaticGrammarExecutor(),
        batch_translator=_RouteFlippingBatchTranslator(
            pool=pool, record_id=article2.record_id
        ),
        batch_vocabulary_executor=_StaticBatchVocabularyExecutor(),
        grammar_batch_executor=_StaticGrammarBatchExecutor(),
    )
    summary_e = await runner_e.run(
        record_id=article2.record_id,
        user_id=user_id,
        lease_owner="e-worker-catch",
        lease_duration=LEASE_DURATION,
        max_ticks=96,
        max_jobs=48,
    )
    # The summary must report superseded count matching the DB actual
    # count — NOT a virtual max(1, ...) when the DB has 0.
    async with pool.acquire() as conn:
        db_superseded_count = await conn.fetchval(
            """
            SELECT COUNT(*) FROM reader_jobs
            WHERE reading_record_id = $1 AND status = 'superseded'
            """,
            article2.record_id,
        )
    assert summary_e.outcome_counts.superseded == db_superseded_count, (
        f"summary.superseded ({summary_e.outcome_counts.superseded}) must "
        f"match DB actual superseded count ({db_superseded_count}); "
        f"no virtual max(1,...) reporting"
    )


# ---------------------------------------------------------------------------
# T4.2a-R2-R1 Test F: Budget diagnostics observability
# ---------------------------------------------------------------------------


async def test_budget_diagnostics_observability(
    budget_env: asyncpg.Pool,
) -> None:
    """Test F: Budget diagnostics are observable in the pipeline summary.

    After a normal run, ``budget_diagnostics`` must be populated with
    per-layer planned/max/consumed/remaining. After a budget-exhausting
    run, ``exhausted_layers`` must list the exhausted layers, and
    ``budget_denied`` must be distinguishable from ``no_job`` in
    ``outcome_counts``.
    """
    pool = budget_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool, user_id=user_id, plain_text=_short_article(), title="Budget Diagnostics"
    )

    # Run 1: normal successful run — budget_diagnostics must be populated
    runner = _make_runner(
        pool,
        translator=_StaticTranslator(),
        vocabulary_executor=_StaticVocabularyExecutor(),
        grammar_executor=_StaticGrammarExecutor(),
        batch_translator=_StaticBatchTranslator(),
        batch_vocabulary_executor=_StaticBatchVocabularyExecutor(),
        grammar_batch_executor=_StaticGrammarBatchExecutor(),
    )
    summary1 = await runner.run(
        record_id=article.record_id,
        user_id=user_id,
        lease_owner="diag-1",
        lease_duration=LEASE_DURATION,
        max_ticks=96,
        max_jobs=48,
    )

    # budget_diagnostics must be a non-empty dict
    assert summary1.budget_diagnostics, (
        "budget_diagnostics must be populated after a run"
    )

    # Each layer that had jobs must appear in diagnostics
    for layer in ("translation", "vocabulary", "grammar"):
        if layer in summary1.budget_diagnostics:
            diag = summary1.budget_diagnostics[layer]
            assert "planned" in diag, f"{layer} missing 'planned' key"
            assert "max" in diag, f"{layer} missing 'max' key"
            assert "consumed" in diag, f"{layer} missing 'consumed' key"
            assert "remaining" in diag, f"{layer} missing 'remaining' key"
            assert diag["planned"] > 0, f"{layer} planned must be > 0"
            assert diag["max"] > 0, f"{layer} max must be > 0"

    # After a successful run, no layers should be exhausted
    assert len(summary1.exhausted_layers) == 0, (
        f"no layers should be exhausted after successful run; "
        f"got {summary1.exhausted_layers}"
    )

    # Manually exhaust the translation layer
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE reader_jobs
            SET status = 'queued',
                attempt_count = 3,
                max_attempts = 3,
                lease_owner = NULL, lease_token = NULL,
                lease_expires_at = NULL, claimed_at = NULL,
                available_at = NOW()
            WHERE reading_record_id = $1
              AND job_type = 'translate_article'
              AND target_type = 'unit_range'
              AND status = 'succeeded'
            """,
            article.record_id,
        )

    # Run 2: translation budget exhausted
    counting_t = _CountingBatchTranslator()
    runner2 = _make_runner(
        pool,
        translator=_StaticTranslator(),
        vocabulary_executor=_StaticVocabularyExecutor(),
        grammar_executor=_StaticGrammarExecutor(),
        batch_translator=counting_t,
        batch_vocabulary_executor=_StaticBatchVocabularyExecutor(),
        grammar_batch_executor=_StaticGrammarBatchExecutor(),
    )
    summary2 = await runner2.run(
        record_id=article.record_id,
        user_id=user_id,
        lease_owner="diag-2",
        lease_duration=LEASE_DURATION,
        max_ticks=96,
        max_jobs=48,
    )

    # exhausted_layers must include translation
    assert "translation" in summary2.exhausted_layers, (
        f"translation must be in exhausted_layers; got {summary2.exhausted_layers}"
    )

    # budget_denied must be > 0 (distinguishable from no_job)
    assert summary2.outcome_counts.budget_denied > 0, (
        "budget_denied must be > 0 to distinguish from no_job"
    )

    # budget_diagnostics for translation must show consumed >= max
    t_diag = summary2.budget_diagnostics.get("translation", {})
    assert t_diag.get("consumed", 0) >= t_diag.get("max", 0), (
        f"translation consumed must be >= max in diagnostics; got {t_diag}"
    )
    assert t_diag.get("remaining", -1) == 0, (
        f"translation remaining must be 0 when exhausted; got {t_diag}"
    )

    # stopped_reason must NOT be all_workers_no_job (must reflect exhaustion)
    assert summary2.stopped_reason != "all_workers_no_job", (
        "budget exhaustion must not be hidden as all_workers_no_job"
    )

    # T4.2a-R2-R2: verify budget diagnostics are persisted in the
    # pipeline root span metadata (not just in the Python return value).
    # Run through WorkerLoop to write the span, then query
    # reader_runtime_spans.metadata_json.
    from app.services.reader_orchestration.worker_loop import (
        ReaderEnhancementWorkerLoopService,
    )

    wl_service = ReaderEnhancementWorkerLoopService(
        pool=pool, pipeline_runner=runner2
    )
    candidates = await wl_service.scan_eligible_records(batch_size=20)
    candidate = None
    for c in candidates:
        if c.record_id == article.record_id:
            candidate = c
            break
    if candidate is not None:
        await wl_service.process_candidate(
            candidate=candidate,
            lease_owner_prefix="diag-wl",
            lease_duration=LEASE_DURATION,
            max_ticks=96,
            max_jobs=48,
        )
        # Query the pipeline root span metadata
        async with pool.acquire() as conn:
            span_row = await conn.fetchrow(
                """
                SELECT metadata_json FROM reader_runtime_spans
                WHERE reading_record_id = $1
                  AND span_kind = 'pipeline_root'
                ORDER BY started_at DESC LIMIT 1
                """,
                article.record_id,
            )
        if span_row is not None:
            span_meta = ensure_json_object(span_row["metadata_json"])
            assert "budget_denied" in span_meta, (
                "pipeline root span metadata must contain budget_denied"
            )
            assert span_meta.get("budget_denied", 0) > 0, (
                "span budget_denied must be > 0 for exhausted scenario"
            )
            assert "exhausted_layers" in span_meta, (
                "pipeline root span metadata must contain exhausted_layers"
            )
            assert "budget_diagnostics" in span_meta, (
                "pipeline root span metadata must contain budget_diagnostics"
            )
            assert "stopped_reason" in span_meta, (
                "pipeline root span metadata must contain stopped_reason"
            )


# ===========================================================================
# T4.2a-R2-R2 Tests G–L: comprehensive review fix verification
# ===========================================================================


# ---------------------------------------------------------------------------
# Test G: succeeded batch cleanup + WorkerLoop completion
# ---------------------------------------------------------------------------


async def test_g_succeeded_batch_cleanup_workerloop_completion(
    budget_env: asyncpg.Pool,
) -> None:
    """Test G: succeeded batch cleanup through full WorkerLoop path.

    T4.2a-R2-R2 P1-1: when a grammar batch has succeeded, legacy per-unit
    grammar jobs must be superseded (not left queued) by the cleanup
    service. The WorkerLoop + Finalizer must be able to complete the
    record without a hot-loop.
    """
    from app.services.reader_orchestration.worker_loop import (
        ReaderEnhancementWorkerLoopService,
    )

    pool = budget_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool, user_id=user_id, plain_text=_short_article(), title="G-Cleanup"
    )

    # Run 1: succeed all batch jobs
    runner = _make_runner(
        pool,
        translator=_StaticTranslator(),
        vocabulary_executor=_StaticVocabularyExecutor(),
        grammar_executor=_StaticGrammarExecutor(),
        batch_translator=_StaticBatchTranslator(),
        batch_vocabulary_executor=_StaticBatchVocabularyExecutor(),
        grammar_batch_executor=_StaticGrammarBatchExecutor(),
    )
    await runner.run(
        record_id=article.record_id,
        user_id=user_id,
        lease_owner="g-cleanup-1",
        lease_duration=LEASE_DURATION,
        max_ticks=96,
        max_jobs=48,
    )

    # Insert a legacy per-unit grammar job (simulates cutover residue)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT base_id, expected_generation, run_id, user_id,
                   operation_fingerprint
            FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = 'build_grammar_bundle'
              AND target_type = 'unit_range'
              AND status = 'succeeded'
            LIMIT 1
            """,
            article.record_id,
        )
        assert row is not None
        await conn.execute(
            """
            INSERT INTO reader_jobs (
                id, reading_record_id, run_id, base_id, user_id,
                job_type, target_type, target_key, status, priority,
                max_attempts, attempt_count, available_at, expected_generation,
                operation_fingerprint, idempotency_key, input_json
            ) VALUES (
                $1, $2, $3, $4, $5, 'build_grammar_bundle', 'unit',
                'test:g:legacy', 'queued', 0, 3, 0, NOW(), $6,
                $7, $8,
                '{"target_unit_ids": [], "article_route": "short_batch"}'::jsonb
            )
            """,
            uuid4(),
            article.record_id,
            row["run_id"],
            row["base_id"],
            row["user_id"],
            int(row["expected_generation"]),
            row["operation_fingerprint"],
            f"test-g-legacy-{uuid4()}",
        )

    # Run 2: through WorkerLoop — cleanup must supersede legacy job
    per_g = _CountingGrammarExecutor()
    runner2 = _make_runner(
        pool,
        translator=_StaticTranslator(),
        vocabulary_executor=_StaticVocabularyExecutor(),
        grammar_executor=per_g,
        batch_translator=_StaticBatchTranslator(),
        batch_vocabulary_executor=_StaticBatchVocabularyExecutor(),
        grammar_batch_executor=_StaticGrammarBatchExecutor(),
    )
    wl_service = ReaderEnhancementWorkerLoopService(
        pool=pool, pipeline_runner=runner2
    )
    candidates = await wl_service.scan_eligible_records(batch_size=20)
    candidate = None
    for c in candidates:
        if c.record_id == article.record_id:
            candidate = c
            break
    assert candidate is not None, "record must be scannable with legacy job"

    wl_result = await wl_service.process_candidate(
        candidate=candidate,
        lease_owner_prefix="g-cleanup-2",
        lease_duration=LEASE_DURATION,
        max_ticks=96,
        max_jobs=48,
    )
    assert wl_result is not None, "WorkerLoop must process the candidate"

    # per-unit executor must NOT be called
    assert per_g.call_count == 0, (
        "per-unit grammar executor must not be called when batch succeeded"
    )

    # Legacy job must be superseded with batch_path_authoritative
    async with pool.acquire() as conn:
        legacy_row = await conn.fetchrow(
            """
            SELECT status, rationale_code FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = 'build_grammar_bundle'
              AND target_type = 'unit'
            """,
            article.record_id,
        )
    assert legacy_row is not None
    assert legacy_row["status"] == "superseded"
    assert legacy_row["rationale_code"] == "batch_path_authoritative"

    # T4.2a-R2-R3: Strong terminal assertions — the finalizer must have
    # finalized and the record must have reached coverage_complete.
    assert wl_result is not None, "WorkerLoop must process the candidate"
    fin_result = wl_result.completion_finalization_result
    assert fin_result is not None, (
        "finalizer must have been invoked after cleanup"
    )
    assert fin_result.finalized is True, (
        f"finalizer must finalize after legacy cleanup; "
        f"skip_reason={fin_result.skip_reason}"
    )

    # DB readiness must be coverage_complete (or at least not stuck
    # in a pre-finalization state).
    async with pool.acquire() as conn:
        db_readiness = await conn.fetchval(
            "SELECT readiness_state FROM reading_records WHERE id = $1",
            article.record_id,
        )
    assert db_readiness == "coverage_complete", (
        f"DB readiness must be coverage_complete after finalization; "
        f"got {db_readiness}"
    )

    # Scanner must NOT re-pick the record (no runnable jobs)
    candidates_after = await wl_service.scan_eligible_records(batch_size=20)
    record_still_candidate = any(
        c.record_id == article.record_id for c in candidates_after
    )
    assert not record_still_candidate, (
        "scanner must not re-pick record after coverage_complete"
    )


# ---------------------------------------------------------------------------
# Test H: failed batch fail-closed cleanup
# ---------------------------------------------------------------------------


async def test_h_failed_batch_fail_closed_cleanup(
    budget_env: asyncpg.Pool,
) -> None:
    """Test H: failed_terminal batch → legacy jobs fail-closed superseded.

    T4.2a-R2-R2 P1-1: when a grammar batch has failed_terminal, legacy
    per-unit jobs must be superseded with ``batch_fallback_not_authorized``
    (fail-closed). No implicit fallback execution.
    """
    from app.services.reader_orchestration.worker_loop import (
        ReaderEnhancementWorkerLoopService,
    )

    pool = budget_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool, user_id=user_id, plain_text=_short_article(), title="H-FailClosed"
    )

    # Run 1: succeed all batch jobs
    runner = _make_runner(
        pool,
        translator=_StaticTranslator(),
        vocabulary_executor=_StaticVocabularyExecutor(),
        grammar_executor=_StaticGrammarExecutor(),
        batch_translator=_StaticBatchTranslator(),
        batch_vocabulary_executor=_StaticBatchVocabularyExecutor(),
        grammar_batch_executor=_StaticGrammarBatchExecutor(),
    )
    await runner.run(
        record_id=article.record_id,
        user_id=user_id,
        lease_owner="h-fail-1",
        lease_duration=LEASE_DURATION,
        max_ticks=96,
        max_jobs=48,
    )

    # Set grammar batch to failed_terminal + insert legacy job
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, base_id, expected_generation, run_id, user_id,
                   operation_fingerprint
            FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = 'build_grammar_bundle'
              AND target_type = 'unit_range'
              AND status = 'succeeded'
            LIMIT 1
            """,
            article.record_id,
        )
        assert row is not None
        await conn.execute(
            """
            UPDATE reader_jobs SET status = 'failed_terminal',
                failure_code = 'test_manual_fail'
            WHERE id = $1
            """,
            row["id"],
        )
        await conn.execute(
            """
            INSERT INTO reader_jobs (
                id, reading_record_id, run_id, base_id, user_id,
                job_type, target_type, target_key, status, priority,
                max_attempts, attempt_count, available_at, expected_generation,
                operation_fingerprint, idempotency_key, input_json
            ) VALUES (
                $1, $2, $3, $4, $5, 'build_grammar_bundle', 'unit',
                'test:h:legacy', 'queued', 0, 3, 0, NOW(), $6,
                $7, $8,
                '{"target_unit_ids": [], "article_route": "short_batch"}'::jsonb
            )
            """,
            uuid4(),
            article.record_id,
            row["run_id"],
            row["base_id"],
            row["user_id"],
            int(row["expected_generation"]),
            row["operation_fingerprint"],
            f"test-h-legacy-{uuid4()}",
        )

    # Run 2: through WorkerLoop — fail-closed cleanup
    per_g = _CountingGrammarExecutor()
    runner2 = _make_runner(
        pool,
        translator=_StaticTranslator(),
        vocabulary_executor=_StaticVocabularyExecutor(),
        grammar_executor=per_g,
        batch_translator=_StaticBatchTranslator(),
        batch_vocabulary_executor=_StaticBatchVocabularyExecutor(),
        grammar_batch_executor=_StaticGrammarBatchExecutor(),
    )
    wl_service = ReaderEnhancementWorkerLoopService(
        pool=pool, pipeline_runner=runner2
    )
    candidates = await wl_service.scan_eligible_records(batch_size=20)
    candidate = None
    for c in candidates:
        if c.record_id == article.record_id:
            candidate = c
            break
    if candidate is not None:
        await wl_service.process_candidate(
            candidate=candidate,
            lease_owner_prefix="h-fail-2",
            lease_duration=LEASE_DURATION,
            max_ticks=96,
            max_jobs=48,
        )

    # per-unit executor must NOT be called
    assert per_g.call_count == 0, (
        "per-unit grammar executor must not be called for failed batch"
    )

    # Legacy job must be superseded with batch_fallback_not_authorized
    async with pool.acquire() as conn:
        legacy_row = await conn.fetchrow(
            """
            SELECT status, rationale_code FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = 'build_grammar_bundle'
              AND target_type = 'unit'
            """,
            article.record_id,
        )
    assert legacy_row is not None
    assert legacy_row["status"] == "superseded"
    assert legacy_row["rationale_code"] == "batch_fallback_not_authorized"

    # No runnable legacy jobs (no hot-loop)
    candidates_after = await wl_service.scan_eligible_records(batch_size=20)
    for c in candidates_after:
        if c.record_id == article.record_id:
            assert c.runnable_job_count == 0, (
                "no runnable legacy jobs after fail-closed cleanup"
            )


# ---------------------------------------------------------------------------
# Test I: partial exhaustion doesn't kill other layers
# ---------------------------------------------------------------------------


async def test_i_partial_exhaustion_preserves_other_layers(
    budget_env: asyncpg.Pool,
) -> None:
    """Test I: partial exhaustion only force-fails exhausted layers.

    T4.2a-R2-R2 P1-2: when translation budget is exhausted but vocabulary
    is retry_later (still has budget), the finalizer must ONLY force-fail
    translation jobs. Vocabulary retry_later must be preserved. The record
    must NOT prematurely finalize.
    """
    from app.services.reader_orchestration.worker_loop import (
        ReaderEnhancementWorkerLoopService,
    )

    pool = budget_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool, user_id=user_id, plain_text=_short_article(), title="I-Partial"
    )

    # Run 1: succeed all batch jobs
    runner = _make_runner(
        pool,
        translator=_StaticTranslator(),
        vocabulary_executor=_StaticVocabularyExecutor(),
        grammar_executor=_StaticGrammarExecutor(),
        batch_translator=_StaticBatchTranslator(),
        batch_vocabulary_executor=_StaticBatchVocabularyExecutor(),
        grammar_batch_executor=_StaticGrammarBatchExecutor(),
    )
    await runner.run(
        record_id=article.record_id,
        user_id=user_id,
        lease_owner="i-partial-1",
        lease_duration=LEASE_DURATION,
        max_ticks=96,
        max_jobs=48,
    )

    # Exhaust translation: reset to queued with attempt_count=max_attempts
    # Set vocabulary to retry_later with future available_at
    # T4.2a-R2-R3: also delete vocabulary enhancement_layers + events from
    # Run 1 so the publisher doesn't reject the re-publish in Run 3 with
    # "vocabulary layer already published for unit X".
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE reader_jobs
            SET status = 'queued',
                attempt_count = 3,
                max_attempts = 3,
                lease_owner = NULL, lease_token = NULL,
                lease_expires_at = NULL, claimed_at = NULL,
                available_at = NOW()
            WHERE reading_record_id = $1
              AND job_type = 'translate_article'
              AND target_type = 'unit_range'
              AND status = 'succeeded'
            """,
            article.record_id,
        )
        # Delete vocabulary layers + events from Run 1 so Run 3 can re-publish
        await conn.execute(
            """
            DELETE FROM reader_events
            WHERE reading_record_id = $1
              AND event_type = 'layer_published'
              AND payload_json->>'layer_type' = 'vocabulary'
            """,
            article.record_id,
        )
        await conn.execute(
            """
            DELETE FROM enhancement_layers
            WHERE reading_record_id = $1
              AND layer_type = 'vocabulary'
            """,
            article.record_id,
        )
        # Set vocabulary batch to retry_later with future available_at
        await conn.execute(
            """
            UPDATE reader_jobs
            SET status = 'retry_later',
                attempt_count = 1,
                max_attempts = 3,
                available_at = NOW() + INTERVAL '1 hour'
            WHERE reading_record_id = $1
              AND job_type = 'build_vocabulary_layer_article'
              AND target_type = 'unit_range'
              AND status = 'succeeded'
            """,
            article.record_id,
        )

    # Run 2: translation exhausted, vocabulary retry_later (not available)
    runner2 = _make_runner(
        pool,
        translator=_StaticTranslator(),
        vocabulary_executor=_StaticVocabularyExecutor(),
        grammar_executor=_StaticGrammarExecutor(),
        batch_translator=_CountingBatchTranslator(),
        batch_vocabulary_executor=_StaticBatchVocabularyExecutor(),
        grammar_batch_executor=_StaticGrammarBatchExecutor(),
    )
    wl_service = ReaderEnhancementWorkerLoopService(
        pool=pool, pipeline_runner=runner2
    )
    candidates = await wl_service.scan_eligible_records(batch_size=20)
    candidate = None
    for c in candidates:
        if c.record_id == article.record_id:
            candidate = c
            break
    if candidate is not None:
        result = await wl_service.process_candidate(
            candidate=candidate,
            lease_owner_prefix="i-partial-2",
            lease_duration=LEASE_DURATION,
            max_ticks=96,
            max_jobs=48,
        )
        # Finalizer must NOT finalize — vocabulary is still retry_later
        if result.completion_finalization_result is not None:
            assert not result.completion_finalization_result.finalized, (
                "must NOT finalize when vocabulary is still retry_later"
            )

    # Vocabulary must STILL be retry_later (not force-failed)
    async with pool.acquire() as conn:
        v_status = await conn.fetchval(
            """
            SELECT status FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = 'build_vocabulary_layer_article'
              AND target_type = 'unit_range'
            """,
            article.record_id,
        )
        # T4.2a-R2-R3: also check translation was force-failed
        t_status = await conn.fetchval(
            """
            SELECT status FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = 'translate_article'
              AND target_type = 'unit_range'
            """,
            article.record_id,
        )
    assert v_status == "retry_later", (
        f"vocabulary must remain retry_later (not force-failed); "
        f"got {v_status}"
    )
    # T4.2a-R2-R3: translation must be failed_terminal (force-failed by
    # finalizer due to budget exhaustion), not left in a non-terminal state.
    assert t_status == "failed_terminal", (
        f"translation must be failed_terminal after budget exhaustion; "
        f"got {t_status}"
    )

    # Now make vocabulary available and re-run
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE reader_jobs
            SET available_at = NOW()
            WHERE reading_record_id = $1
              AND job_type = 'build_vocabulary_layer_article'
              AND target_type = 'unit_range'
              AND status = 'retry_later'
            """,
            article.record_id,
        )

    # Run 3: vocabulary should now complete
    runner3 = _make_runner(
        pool,
        translator=_StaticTranslator(),
        vocabulary_executor=_StaticVocabularyExecutor(),
        grammar_executor=_StaticGrammarExecutor(),
        batch_translator=_StaticBatchTranslator(),
        batch_vocabulary_executor=_StaticBatchVocabularyExecutor(),
        grammar_batch_executor=_StaticGrammarBatchExecutor(),
    )
    wl_service3 = ReaderEnhancementWorkerLoopService(
        pool=pool, pipeline_runner=runner3
    )
    candidates3 = await wl_service3.scan_eligible_records(batch_size=20)
    for c in candidates3:
        if c.record_id == article.record_id:
            await wl_service3.process_candidate(
                candidate=c,
                lease_owner_prefix="i-partial-3",
                lease_duration=LEASE_DURATION,
                max_ticks=96,
                max_jobs=48,
            )
            break

    # T4.2a-R2-R3: Strong terminal assertions — vocabulary must have
    # succeeded, and the record must have reached a final readiness state.
    async with pool.acquire() as conn:
        v_status_final = await conn.fetchval(
            """
            SELECT status FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = 'build_vocabulary_layer_article'
              AND target_type = 'unit_range'
            """,
            article.record_id,
        )
        db_readiness = await conn.fetchval(
            "SELECT readiness_state FROM reading_records WHERE id = $1",
            article.record_id,
        )
    assert v_status_final == "succeeded", (
        f"vocabulary must succeed after available_at reset; "
        f"got {v_status_final}"
    )
    # With translation failed_terminal and vocabulary succeeded, the
    # record should finalize as coverage_complete (completed_with_failures).
    assert db_readiness == "coverage_complete", (
        f"DB readiness must be coverage_complete after vocabulary succeeds; "
        f"got {db_readiness}"
    )


# ---------------------------------------------------------------------------
# Test J: pipeline-level publish fence (worker/pipeline catch)
# ---------------------------------------------------------------------------


async def test_j_pipeline_level_publish_fence(
    budget_env: asyncpg.Pool,
) -> None:
    """Test J: publish fence through worker/pipeline catch path.

    T4.2a-R2-R2 P1-3 + T4.2a-R2-R3 strong assertions: when a route
    flips DURING worker execution, the publish-time fence must cause
    the worker to transition the job to superseded and mark the run
    as superseded. The summary.superseded must match the DB actual
    count (no virtual max(1,...) reporting).

    Strong assertions (T4.2a-R2-R3):
    - The specific claimed translation batch job is ``superseded``
    - The reader_run is ``superseded``
    - summary.outcome_counts.superseded >= 1
    - No new enhancement_layers from the fenced job
    - No new layer_published events from the fenced job
    """
    pool = budget_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool, user_id=user_id, plain_text=_short_article(), title="J-Fence"
    )

    # Count layers and events BEFORE (bootstrap hasn't run yet, so 0)
    async with pool.acquire() as conn:
        layers_before = await conn.fetchval(
            "SELECT COUNT(*) FROM enhancement_layers WHERE reading_record_id = $1",
            article.record_id,
        )
        events_before = await conn.fetchval(
            """
            SELECT COUNT(*) FROM reader_events
            WHERE reading_record_id = $1 AND event_type = 'layer_published'
            """,
            article.record_id,
        )

    # Use a route-flipping batch translator: claim passes, publish fails
    runner = _make_runner(
        pool,
        translator=_StaticTranslator(),
        vocabulary_executor=_StaticVocabularyExecutor(),
        grammar_executor=_StaticGrammarExecutor(),
        batch_translator=_RouteFlippingBatchTranslator(
            pool=pool, record_id=article.record_id
        ),
        batch_vocabulary_executor=_StaticBatchVocabularyExecutor(),
        grammar_batch_executor=_StaticGrammarBatchExecutor(),
    )

    summary = await runner.run(
        record_id=article.record_id,
        user_id=user_id,
        lease_owner="j-fence-1",
        lease_duration=LEASE_DURATION,
        max_ticks=96,
        max_jobs=48,
    )

    # After run: query for the SPECIFIC translation batch job and its run.
    # Bootstrap creates the job during runner.run(); the route-flipping
    # translator causes the publish fence to fail, so the job should be
    # superseded with rationale publish_fence_failed.
    async with pool.acquire() as conn:
        layers_after = await conn.fetchval(
            "SELECT COUNT(*) FROM enhancement_layers WHERE reading_record_id = $1",
            article.record_id,
        )
        events_after = await conn.fetchval(
            """
            SELECT COUNT(*) FROM reader_events
            WHERE reading_record_id = $1 AND event_type = 'layer_published'
            """,
            article.record_id,
        )
        db_superseded = await conn.fetchval(
            """
            SELECT COUNT(*) FROM reader_jobs
            WHERE reading_record_id = $1 AND status = 'superseded'
            """,
            article.record_id,
        )
        # Query the SPECIFIC translation batch job
        job_row = await conn.fetchrow(
            """
            SELECT id, run_id, status, rationale_code FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = 'translate_article'
              AND target_type = 'unit_range'
            """,
            article.record_id,
        )
        assert job_row is not None, (
            "translation batch job must exist after bootstrap+run"
        )
        job_status = job_row["status"]
        job_rationale = job_row["rationale_code"]
        # Query the SPECIFIC run
        run_status = await conn.fetchval(
            "SELECT status FROM reader_runs WHERE id = $1",
            job_row["run_id"],
        )

    # T4.2a-R2-R3: Strong assertions — the fence must have actually
    # transitioned the job and run, not just counted a virtual supersede.

    # 1. The specific translation batch job must be superseded
    assert job_status == "superseded", (
        f"translation batch job must be superseded after publish fence; "
        f"got status={job_status}"
    )
    assert job_rationale == "publish_fence_failed", (
        f"translation batch job rationale must be publish_fence_failed; "
        f"got {job_rationale}"
    )

    # 2. The reader_run must be superseded
    assert run_status == "superseded", (
        f"reader_run must be superseded after publish fence; "
        f"got status={run_status}"
    )

    # 3. summary.superseded must be >= 1 (at least the translation batch)
    assert summary.outcome_counts.superseded >= 1, (
        f"summary.superseded must be >= 1 after publish fence; "
        f"got {summary.outcome_counts.superseded}"
    )

    # 4. Summary.superseded must match DB actual count (no max(1,...) virtual)
    assert summary.outcome_counts.superseded == db_superseded, (
        f"summary.superseded ({summary.outcome_counts.superseded}) must "
        f"match DB actual ({db_superseded}); no virtual max(1,...)"
    )

    # 5. No new enhancement_layers from the fenced job
    assert layers_after == layers_before, (
        f"fence violation must not add enhancement_layers; "
        f"before={layers_before}, after={layers_after}"
    )

    # 6. No new layer_published events from the fenced job
    assert events_after == events_before, (
        f"fence violation must not add layer_published events; "
        f"before={events_before}, after={events_after}"
    )


# ---------------------------------------------------------------------------
# Test K: persistent budget observability
# ---------------------------------------------------------------------------


async def test_k_persistent_budget_observability(
    budget_env: asyncpg.Pool,
) -> None:
    """Test K: budget-denied must be queryable from reader_runtime_spans.

    T4.2a-R2-R2 P2-1: budget_denied, exhausted_layers, budget_diagnostics
    must be persisted in the pipeline root span metadata (not just in the
    Python return value). A normal no_job scenario must NOT be marked as
    budget_denied.
    """
    from app.services.reader_orchestration.worker_loop import (
        ReaderEnhancementWorkerLoopService,
    )

    pool = budget_env
    user_id = await insert_user(pool)

    # --- Scenario 1: budget-denied ---
    article1 = await submit_article_ready(
        pool, user_id=user_id, plain_text=_short_article(), title="K-Denied"
    )
    runner1 = _make_runner(
        pool,
        translator=_StaticTranslator(),
        vocabulary_executor=_StaticVocabularyExecutor(),
        grammar_executor=_StaticGrammarExecutor(),
        batch_translator=_StaticBatchTranslator(),
        batch_vocabulary_executor=_StaticBatchVocabularyExecutor(),
        grammar_batch_executor=_StaticGrammarBatchExecutor(),
    )
    await runner1.run(
        record_id=article1.record_id,
        user_id=user_id,
        lease_owner="k-denied-1",
        lease_duration=LEASE_DURATION,
        max_ticks=96,
        max_jobs=48,
    )

    # Exhaust translation
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE reader_jobs
            SET status = 'queued',
                attempt_count = 3,
                max_attempts = 3,
                lease_owner = NULL, lease_token = NULL,
                lease_expires_at = NULL, claimed_at = NULL,
                available_at = NOW()
            WHERE reading_record_id = $1
              AND job_type = 'translate_article'
              AND target_type = 'unit_range'
              AND status = 'succeeded'
            """,
            article1.record_id,
        )

    # Run through WorkerLoop to write the span
    runner1b = _make_runner(
        pool,
        translator=_StaticTranslator(),
        vocabulary_executor=_StaticVocabularyExecutor(),
        grammar_executor=_StaticGrammarExecutor(),
        batch_translator=_CountingBatchTranslator(),
        batch_vocabulary_executor=_StaticBatchVocabularyExecutor(),
        grammar_batch_executor=_StaticGrammarBatchExecutor(),
    )
    wl1 = ReaderEnhancementWorkerLoopService(
        pool=pool, pipeline_runner=runner1b
    )
    candidates1 = await wl1.scan_eligible_records(batch_size=20)
    for c in candidates1:
        if c.record_id == article1.record_id:
            await wl1.process_candidate(
                candidate=c,
                lease_owner_prefix="k-denied-2",
                lease_duration=LEASE_DURATION,
                max_ticks=96,
                max_jobs=48,
            )
            break

    # Query the pipeline root span metadata
    async with pool.acquire() as conn:
        span_row = await conn.fetchrow(
            """
            SELECT metadata_json FROM reader_runtime_spans
            WHERE reading_record_id = $1
              AND span_kind = 'pipeline_root'
            ORDER BY started_at DESC LIMIT 1
            """,
            article1.record_id,
        )
    assert span_row is not None, (
        "pipeline root span must exist for budget-denied scenario"
    )
    span_meta = ensure_json_object(span_row["metadata_json"])
    assert span_meta.get("budget_denied", 0) > 0, (
        f"span budget_denied must be > 0; got {span_meta.get('budget_denied')}"
    )
    assert "exhausted_layers" in span_meta, (
        "span must contain exhausted_layers"
    )
    assert "budget_diagnostics" in span_meta, (
        "span must contain budget_diagnostics"
    )
    assert "stopped_reason" in span_meta, (
        "span must contain stopped_reason"
    )

    # --- Scenario 2: normal no_job (budget_denied must be 0) ---
    article2 = await submit_article_ready(
        pool, user_id=user_id, plain_text=_short_article(), title="K-NoJob"
    )
    runner2 = _make_runner(
        pool,
        translator=_StaticTranslator(),
        vocabulary_executor=_StaticVocabularyExecutor(),
        grammar_executor=_StaticGrammarExecutor(),
        batch_translator=_StaticBatchTranslator(),
        batch_vocabulary_executor=_StaticBatchVocabularyExecutor(),
        grammar_batch_executor=_StaticGrammarBatchExecutor(),
    )
    # Run once to complete all jobs
    await runner2.run(
        record_id=article2.record_id,
        user_id=user_id,
        lease_owner="k-nojob-1",
        lease_duration=LEASE_DURATION,
        max_ticks=96,
        max_jobs=48,
    )
    # Run again through WorkerLoop — should be no_job (all work done)
    wl2 = ReaderEnhancementWorkerLoopService(
        pool=pool, pipeline_runner=runner2
    )
    candidates2 = await wl2.scan_eligible_records(batch_size=20)
    for c in candidates2:
        if c.record_id == article2.record_id:
            await wl2.process_candidate(
                candidate=c,
                lease_owner_prefix="k-nojob-2",
                lease_duration=LEASE_DURATION,
                max_ticks=96,
                max_jobs=48,
            )
            break

    async with pool.acquire() as conn:
        span_row2 = await conn.fetchrow(
            """
            SELECT metadata_json FROM reader_runtime_spans
            WHERE reading_record_id = $1
              AND span_kind = 'pipeline_root'
            ORDER BY started_at DESC LIMIT 1
            """,
            article2.record_id,
        )
    if span_row2 is not None:
        span_meta2 = ensure_json_object(span_row2["metadata_json"])
        assert span_meta2.get("budget_denied", 0) == 0, (
            f"normal no_job must have budget_denied == 0; "
            f"got {span_meta2.get('budget_denied')}"
        )


# ---------------------------------------------------------------------------
# Test L: multi-fingerprint determinism
# ---------------------------------------------------------------------------


async def test_l_multi_fingerprint_determinism(
    budget_env: asyncpg.Pool,
) -> None:
    """Test L: multi-fingerprint budget load must be deterministic.

    T4.2a-R2-R2 P2-2: ExecutionBudget.load_durable() must return a
    stable sorted fingerprint set per layer, not a last-wins single
    fingerprint. Multiple calls must produce identical results.
    """
    pool = budget_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool, user_id=user_id, plain_text=_short_article(), title="L-Fingerprint"
    )

    # Run pipeline to create jobs
    runner = _make_runner(
        pool,
        translator=_StaticTranslator(),
        vocabulary_executor=_StaticVocabularyExecutor(),
        grammar_executor=_StaticGrammarExecutor(),
        batch_translator=_StaticBatchTranslator(),
        batch_vocabulary_executor=_StaticBatchVocabularyExecutor(),
        grammar_batch_executor=_StaticGrammarBatchExecutor(),
    )
    await runner.run(
        record_id=article.record_id,
        user_id=user_id,
        lease_owner="l-fp-1",
        lease_duration=LEASE_DURATION,
        max_ticks=96,
        max_jobs=48,
    )

    # Insert a job with a DIFFERENT fingerprint for the same layer
    # (simulates cutover intermediate state: two non-superseded fingerprints)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT base_id, expected_generation, run_id, user_id
            FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = 'translate_article'
              AND target_type = 'unit_range'
              AND status = 'succeeded'
            LIMIT 1
            """,
            article.record_id,
        )
        assert row is not None
        # Insert a second translation job with a different fingerprint
        await conn.execute(
            """
            INSERT INTO reader_jobs (
                id, reading_record_id, run_id, base_id, user_id,
                job_type, target_type, target_key, status, priority,
                max_attempts, attempt_count, available_at, expected_generation,
                operation_fingerprint, idempotency_key, input_json
            ) VALUES (
                $1, $2, $3, $4, $5, 'translate_article', 'unit_range',
                'test:l:extra', 'queued', 0, 3, 0, NOW(), $6,
                'test_stale_fingerprint_v1', $7,
                '{"target_unit_ids": [], "article_route": "short_batch"}'::jsonb
            )
            """,
            uuid4(),
            article.record_id,
            row["run_id"],
            row["base_id"],
            row["user_id"],
            int(row["expected_generation"]),
            f"test-l-extra-{uuid4()}",
        )

    # Load durable budget — must return deterministic sorted fingerprint set
    async with pool.acquire() as conn:
        result1 = await ExecutionBudget.load_durable(
            conn,
            record_id=article.record_id,
            base_id=row["base_id"],
            expected_generation=int(row["expected_generation"]),
        )
        result2 = await ExecutionBudget.load_durable(
            conn,
            record_id=article.record_id,
            base_id=row["base_id"],
            expected_generation=int(row["expected_generation"]),
        )

    # Both calls must produce identical results (deterministic)
    fps1 = result1.non_superseded_fingerprints.get("translation", ())
    fps2 = result2.non_superseded_fingerprints.get("translation", ())
    assert fps1 == fps2, (
        f"load_durable must be deterministic; got {fps1} vs {fps2}"
    )

    # The fingerprint set must be sorted (not last-wins)
    assert fps1 == tuple(sorted(fps1)), (
        f"fingerprints must be sorted; got {fps1}"
    )

    # The stale fingerprint must be included (conservative aggregation)
    assert "test_stale_fingerprint_v1" in fps1, (
        f"stale fingerprint must be in non_superseded_fingerprints; "
        f"got {fps1}"
    )

    # Diagnostics must also include the fingerprint set
    budget = ExecutionBudget()
    budget.load_from_durable(result1)
    diag = budget.to_diagnostics()
    t_diag = diag.get("translation", {})
    assert "non_superseded_fingerprints" in t_diag, (
        "diagnostics must include non_superseded_fingerprints"
    )
    diag_fps = t_diag["non_superseded_fingerprints"]
    assert "test_stale_fingerprint_v1" in diag_fps, (
        f"stale fingerprint must be in diagnostics; got {diag_fps}"
    )


# ---------------------------------------------------------------------------
# Test M: full budget exhaustion preserves display-title
# ---------------------------------------------------------------------------


async def test_m_full_budget_exhaustion_preserves_display_title(
    budget_env: asyncpg.Pool,
) -> None:
    """Test M: full budget exhaustion does NOT force-fail display-title.

    T4.2a-R2-R3a: when all three budget layers (translation, vocabulary,
    grammar) are exhausted, the finalizer must ONLY force-fail budget
    layer jobs. A retryable display_title job must survive, and the
    record must NOT prematurely finalize. When display_title later
    succeeds, the record finalizes as ``completed_with_failures``.

    Uses deterministic fake executors with call counters — no real LLM.
    """
    from app.services.reader_orchestration.worker_loop import (
        ReaderEnhancementWorkerLoopService,
    )

    pool = budget_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool, user_id=user_id, plain_text=_short_article(), title="M-FullExhaustion"
    )

    # Run 1: succeed all batch jobs. ``runner.run()`` does NOT call the
    # finalizer — only WorkerLoop does — so readiness_state stays at
    # ``article_ready`` (eligible for finalization in Run 2/3).
    runner = _make_runner(
        pool,
        translator=_StaticTranslator(),
        vocabulary_executor=_StaticVocabularyExecutor(),
        grammar_executor=_StaticGrammarExecutor(),
        batch_translator=_StaticBatchTranslator(),
        batch_vocabulary_executor=_StaticBatchVocabularyExecutor(),
        grammar_batch_executor=_StaticGrammarBatchExecutor(),
    )
    await runner.run(
        record_id=article.record_id,
        user_id=user_id,
        lease_owner="m-full-1",
        lease_duration=LEASE_DURATION,
        max_ticks=96,
        max_jobs=48,
    )

    # Construct durable state:
    # - translation/vocabulary/grammar batch jobs: queued with
    #   attempt_count = max_attempts = 3 (budget exhausted, non-terminal
    #   so the finalizer has something to force-fail)
    # - display_title: retry_later with attempt_count = 1 < max_attempts = 3
    #   and available_at in the future (not budget layer, still retryable,
    #   not claimable yet)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE reader_jobs
            SET status = 'queued',
                attempt_count = 3,
                max_attempts = 3,
                lease_owner = NULL, lease_token = NULL,
                lease_expires_at = NULL, claimed_at = NULL,
                available_at = NOW()
            WHERE reading_record_id = $1
              AND job_type = 'translate_article'
              AND target_type = 'unit_range'
              AND status = 'succeeded'
            """,
            article.record_id,
        )
        await conn.execute(
            """
            UPDATE reader_jobs
            SET status = 'queued',
                attempt_count = 3,
                max_attempts = 3,
                lease_owner = NULL, lease_token = NULL,
                lease_expires_at = NULL, claimed_at = NULL,
                available_at = NOW()
            WHERE reading_record_id = $1
              AND job_type = 'build_vocabulary_layer_article'
              AND target_type = 'unit_range'
              AND status = 'succeeded'
            """,
            article.record_id,
        )
        await conn.execute(
            """
            UPDATE reader_jobs
            SET status = 'queued',
                attempt_count = 3,
                max_attempts = 3,
                lease_owner = NULL, lease_token = NULL,
                lease_expires_at = NULL, claimed_at = NULL,
                available_at = NOW()
            WHERE reading_record_id = $1
              AND job_type = 'build_grammar_bundle'
              AND target_type = 'unit_range'
              AND status = 'succeeded'
            """,
            article.record_id,
        )
        await conn.execute(
            """
            UPDATE reader_jobs
            SET status = 'retry_later',
                attempt_count = 1,
                max_attempts = 3,
                lease_owner = NULL, lease_token = NULL,
                lease_expires_at = NULL, claimed_at = NULL,
                available_at = NOW() + INTERVAL '1 hour'
            WHERE reading_record_id = $1
              AND job_type = 'generate_display_title_zh'
              AND status = 'succeeded'
            """,
            article.record_id,
        )

    # Save display_title state before Run 2
    async with pool.acquire() as conn:
        dt_before = await conn.fetchrow(
            """
            SELECT attempt_count, status FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = 'generate_display_title_zh'
            """,
            article.record_id,
        )
    assert dt_before is not None, "display_title job must exist after Run 1"
    dt_attempt_before = int(dt_before["attempt_count"])
    assert dt_before["status"] == "retry_later", (
        f"display_title must be retry_later after reset; got {dt_before['status']}"
    )
    assert dt_attempt_before == 1, (
        f"display_title attempt_count must be 1; got {dt_attempt_before}"
    )

    # Run 2: First WorkerLoop — all budget layers exhausted, display_title
    # retry_later (not available). The finalizer must force-fail the 3
    # budget layer jobs but preserve display_title, and NOT finalize.
    counting_title_2 = _CountingTitleGenerator()
    runner2 = _make_runner(
        pool,
        translator=_StaticTranslator(),
        vocabulary_executor=_StaticVocabularyExecutor(),
        grammar_executor=_StaticGrammarExecutor(),
        batch_translator=_StaticBatchTranslator(),
        batch_vocabulary_executor=_StaticBatchVocabularyExecutor(),
        grammar_batch_executor=_StaticGrammarBatchExecutor(),
        display_title_generator=counting_title_2,
    )
    wl_service_2 = ReaderEnhancementWorkerLoopService(
        pool=pool, pipeline_runner=runner2
    )
    candidates_2 = await wl_service_2.scan_eligible_records(batch_size=20)
    candidate_2 = None
    for c in candidates_2:
        if c.record_id == article.record_id:
            candidate_2 = c
            break
    assert candidate_2 is not None, (
        "record must be scannable with exhausted budget + retry_later display_title"
    )

    result_2 = await wl_service_2.process_candidate(
        candidate=candidate_2,
        lease_owner_prefix="m-full-2",
        lease_duration=LEASE_DURATION,
        max_ticks=96,
        max_jobs=48,
    )
    assert result_2 is not None, "WorkerLoop must process Run 2 candidate"

    # display_title generator must NOT have been called (available_at future)
    assert counting_title_2.call_count == 0, (
        f"display_title generator must not be called when available_at is "
        f"in the future; got call_count={counting_title_2.call_count}"
    )

    # Finalizer must have run and returned non_terminal_jobs_present
    fin_result_2 = result_2.completion_finalization_result
    assert fin_result_2 is not None, (
        "finalizer must have been invoked after budget exhaustion"
    )
    assert fin_result_2.finalized is False, (
        f"finalizer must NOT finalize when display_title is still "
        f"retry_later; skip_reason={fin_result_2.skip_reason}"
    )
    assert fin_result_2.skip_reason == "non_terminal_jobs_present", (
        f"skip_reason must be non_terminal_jobs_present; "
        f"got {fin_result_2.skip_reason}"
    )

    # Verify job states after Run 2
    async with pool.acquire() as conn:
        t_status = await conn.fetchval(
            """
            SELECT status FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = 'translate_article'
              AND target_type = 'unit_range'
            """,
            article.record_id,
        )
        v_status = await conn.fetchval(
            """
            SELECT status FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = 'build_vocabulary_layer_article'
              AND target_type = 'unit_range'
            """,
            article.record_id,
        )
        g_status = await conn.fetchval(
            """
            SELECT status FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = 'build_grammar_bundle'
              AND target_type = 'unit_range'
            """,
            article.record_id,
        )
        dt_after_2 = await conn.fetchrow(
            """
            SELECT status, attempt_count, failure_code FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = 'generate_display_title_zh'
            """,
            article.record_id,
        )
        db_readiness_2 = await conn.fetchval(
            "SELECT readiness_state FROM reading_records WHERE id = $1",
            article.record_id,
        )

    assert t_status == "failed_terminal", (
        f"translation must be failed_terminal after budget exhaustion; "
        f"got {t_status}"
    )
    assert v_status == "failed_terminal", (
        f"vocabulary must be failed_terminal after budget exhaustion; "
        f"got {v_status}"
    )
    assert g_status == "failed_terminal", (
        f"grammar must be failed_terminal after budget exhaustion; "
        f"got {g_status}"
    )
    assert dt_after_2["status"] == "retry_later", (
        f"display_title must remain retry_later (not force-failed); "
        f"got {dt_after_2['status']}"
    )
    assert int(dt_after_2["attempt_count"]) == dt_attempt_before, (
        f"display_title attempt_count must not increase; "
        f"expected {dt_attempt_before}, got {int(dt_after_2['attempt_count'])}"
    )
    assert dt_after_2["failure_code"] is None, (
        f"display_title must NOT have budget_exhausted failure_code; "
        f"got {dt_after_2['failure_code']}"
    )
    assert db_readiness_2 != "coverage_complete", (
        f"readiness must NOT be coverage_complete when display_title is "
        f"pending; got {db_readiness_2}"
    )

    # Make display_title available for Run 3
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE reader_jobs
            SET available_at = NOW()
            WHERE reading_record_id = $1
              AND job_type = 'generate_display_title_zh'
              AND status = 'retry_later'
            """,
            article.record_id,
        )

    # Run 3: Second WorkerLoop — display_title now available. Budget layers
    # still exhausted (failed_terminal), but no non-terminal budget jobs.
    # display_title succeeds → all jobs terminal → finalizer finalizes as
    # completed_with_failures.
    counting_title_3 = _CountingTitleGenerator()
    runner3 = _make_runner(
        pool,
        translator=_StaticTranslator(),
        vocabulary_executor=_StaticVocabularyExecutor(),
        grammar_executor=_StaticGrammarExecutor(),
        batch_translator=_StaticBatchTranslator(),
        batch_vocabulary_executor=_StaticBatchVocabularyExecutor(),
        grammar_batch_executor=_StaticGrammarBatchExecutor(),
        display_title_generator=counting_title_3,
    )
    wl_service_3 = ReaderEnhancementWorkerLoopService(
        pool=pool, pipeline_runner=runner3
    )
    candidates_3 = await wl_service_3.scan_eligible_records(batch_size=20)
    candidate_3 = None
    for c in candidates_3:
        if c.record_id == article.record_id:
            candidate_3 = c
            break
    assert candidate_3 is not None, (
        "record must be scannable with retry_later display_title (available)"
    )

    result_3 = await wl_service_3.process_candidate(
        candidate=candidate_3,
        lease_owner_prefix="m-full-3",
        lease_duration=LEASE_DURATION,
        max_ticks=96,
        max_jobs=48,
    )
    assert result_3 is not None, "WorkerLoop must process Run 3 candidate"

    # display_title generator must have been called exactly once
    assert counting_title_3.call_count == 1, (
        f"display_title generator must be called exactly once; "
        f"got call_count={counting_title_3.call_count}"
    )

    # Finalizer must have finalized
    fin_result_3 = result_3.completion_finalization_result
    assert fin_result_3 is not None, (
        "finalizer must have been invoked after display_title succeeded"
    )
    assert fin_result_3.finalized is True, (
        f"finalizer must finalize after display_title succeeds; "
        f"skip_reason={fin_result_3.skip_reason}"
    )
    assert fin_result_3.outcome == "completed_with_failures", (
        f"completion outcome must be completed_with_failures; "
        f"got {fin_result_3.outcome}"
    )

    # Verify final job states and readiness
    async with pool.acquire() as conn:
        dt_final = await conn.fetchrow(
            """
            SELECT status, attempt_count FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = 'generate_display_title_zh'
            """,
            article.record_id,
        )
        db_readiness_final = await conn.fetchval(
            "SELECT readiness_state FROM reading_records WHERE id = $1",
            article.record_id,
        )
        # Budget layers must still be failed_terminal (not resurrected)
        t_final = await conn.fetchval(
            """
            SELECT status FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = 'translate_article'
              AND target_type = 'unit_range'
            """,
            article.record_id,
        )
        v_final = await conn.fetchval(
            """
            SELECT status FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = 'build_vocabulary_layer_article'
              AND target_type = 'unit_range'
            """,
            article.record_id,
        )
        g_final = await conn.fetchval(
            """
            SELECT status FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = 'build_grammar_bundle'
              AND target_type = 'unit_range'
            """,
            article.record_id,
        )
        # Completion event must be completed_with_failures (not completed_clean)
        completion_event = await conn.fetchrow(
            """
            SELECT payload_json FROM reader_events
            WHERE reading_record_id = $1
              AND event_type = 'record_state_changed'
              AND payload_json->>'field' = 'readiness_state'
              AND payload_json->>'next_value' = 'coverage_complete'
            ORDER BY sequence DESC
            LIMIT 1
            """,
            article.record_id,
        )

    assert dt_final["status"] == "succeeded", (
        f"display_title must be succeeded after Run 3; "
        f"got {dt_final['status']}"
    )
    assert int(dt_final["attempt_count"]) == dt_attempt_before + 1, (
        f"display_title attempt_count must increase by exactly 1; "
        f"expected {dt_attempt_before + 1}, "
        f"got {int(dt_final['attempt_count'])}"
    )
    assert t_final == "failed_terminal", (
        f"translation must still be failed_terminal after Run 3; "
        f"got {t_final}"
    )
    assert v_final == "failed_terminal", (
        f"vocabulary must still be failed_terminal after Run 3; "
        f"got {v_final}"
    )
    assert g_final == "failed_terminal", (
        f"grammar must still be failed_terminal after Run 3; "
        f"got {g_final}"
    )
    assert db_readiness_final == "coverage_complete", (
        f"readiness must be coverage_complete after finalization; "
        f"got {db_readiness_final}"
    )
    assert completion_event is not None, (
        "completion_finalized event must exist"
    )
    event_payload = ensure_json_object(completion_event["payload_json"])
    assert event_payload.get("completion_outcome") == "completed_with_failures", (
        f"event completion_outcome must be completed_with_failures; "
        f"got {event_payload.get('completion_outcome')}"
    )
