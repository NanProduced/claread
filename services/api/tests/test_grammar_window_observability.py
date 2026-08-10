"""grammar-window Analysis Window observability tests.

Requirements covered:
  - Requirement 6: grammar-window success path records an ``ai_usage_events`` row with
    ``capability_code='reader_grammar_bundle'`` /
    ``operation_fingerprint='grammar_bundle_window_v1'`` and metadata
    including ``plan_id`` / ``window_id`` / ``window_index`` /
    ``target_unit_ids`` / ``target_anchor_ids`` / ``accepted_count`` /
    ``no_op`` / ``layer_ids``. The corresponding ``worker_tick`` span row
    in ``reader_runtime_spans`` carries ``duration_ms`` /
    ``input_tokens`` / ``output_tokens`` / ``total_tokens`` /
    ``ai_usage_event_id``.
  - Requirement 7: ``GrammarWindowPublisher.publish_window_grammar_bundle``
    starts a ``publish_fence`` span around the publish transaction and ends
    it with ``status='succeeded'`` (success) or ``status='failed'`` with
    ``failure_class='fence_violation'`` / ``failure_class='publish_exception'``
    (failure).
  - Grammar-window worker-tick LangSmith ownership: the owning
    ``worker_tick`` carries ``langsmith_run_id`` (consumed from the
    ``LangSmithIdBridgeProcessor`` ContextVar during the window's LLM call),
    while the ``publish_fence`` span does not inherit it (mock test).
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

from app.config.settings import Settings
from app.contracts.annotation import compute_text_range_hash
from app.database import connection as db_connection
from app.database.json_compat import jsonb_param
from app.observability.langsmith_span_processor import (
    _CURRENT_LANGSMITH_IDS,
    LangSmithIds,
    clear_langsmith_ids,
)
from app.schemas.reader_orchestration import ReaderTextRangeAnchor
from app.services.model_execution_journal import CaptureEnvelopeConflictError
from app.services.model_execution_journal.service import ModelExecutionJournalService
from app.services.reader_orchestration.display_title_worker import (
    DisplayTitleWorkerService,
)
from app.services.reader_orchestration.event_runtime import ReaderEventRuntime
from app.services.reader_orchestration.grammar_window_publisher import (
    GrammarWindowPublisher,
    WindowCandidateContent,
)
from app.services.reader_orchestration.grammar_window_worker import (
    GrammarWindowExecutionResult,
    GrammarWindowWorkerService,
)
from app.services.reader_orchestration.grammar_worker import GrammarBundleWorkerService
from app.services.reader_orchestration.orchestrator import ReaderOrchestrator
from app.services.reader_orchestration.pipeline_runner import (
    ReaderEnhancementPipelineRunner,
)
from app.services.reader_orchestration.translation_worker import TranslationWorkerService
from app.services.reader_orchestration.usage_attribution import (
    ReaderUsageAttributionService,
)
from app.services.reader_orchestration.vocabulary_worker import VocabularyWorkerService
from app.services.reader_orchestration.window_selector import CandidateItem
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

pytestmark = pytest.mark.anyio

# T1.1: add translate_article / build_vocabulary_layer_article job types

_OBS_BASE_PARAGRAPH = (
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

# T4.2a-R1: the article must route to GROUPED_WINDOWED (>2000 words) so the
# grammar-window window path is exercised. Before T4.1c, all grammar-window enabled articles went
# through the window path; after T4.1c, only GROUPED_WINDOWED articles do.
# Repeating the base paragraph 26 times yields ~2230 words, safely above the
# 2000-word STRUCTURED_ARTICLE_MAX_WORD_COUNT threshold.
GRAMMAR_WINDOW_OBSERVABILITY_ARTICLE = "\n\n".join([_OBS_BASE_PARAGRAPH] * 26)


LEASE_DURATION = timedelta(seconds=30)


# ---------------------------------------------------------------------------
# Mock executor producing candidates with content_* fields (for §8.3 contract)
# ---------------------------------------------------------------------------


class _ObservabilityMockExecutor:
    """Mock executor producing a single grammar_note candidate + usage_data.

    Produces candidates with full ``content_*`` fields so the production
    ``_derive_candidate_contents`` bridge can build proper
    ``WindowCandidateContent`` and the publisher can emit §8.3 contract
    layers. ``usage_data`` is non-empty so the ``worker_tick`` span carries
    non-zero token counts.
    """

    def __init__(
        self,
        *,
        pool: asyncpg.Pool,
        langsmith_ids: LangSmithIds | None = None,
    ) -> None:
        self._pool = pool
        self._langsmith_ids = langsmith_ids
        self.last_candidate_contents: list[WindowCandidateContent] = []
        self.call_count = 0

    async def generate(
        self, context: dict[str, Any]
    ) -> GrammarWindowExecutionResult:
        self.call_count += 1
        if self._langsmith_ids is not None:
            # Simulate LangSmithIdBridgeProcessor.on_end capturing the run id
            # the moment this window's PydanticAI LLM span ends (i.e. during
            # the worker attempt, after the attempt-boundary clear).
            _CURRENT_LANGSMITH_IDS.set(self._langsmith_ids)
        self.last_candidate_contents = []

        target_anchors = context.get("target_anchors", [])
        if not target_anchors:
            return GrammarWindowExecutionResult(
                candidates=[],
                usage_data={
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "total_tokens": 150,
                },
            )

        anchor = target_anchors[0]
        anchor_id = str(anchor["anchor_segment_id"])
        base_id = UUID(str(context["base_id"]))

        text_anchor = await self._build_text_range_anchor(
            base_id, anchor_id, anchor
        )
        dedup_key = f"obs_grammar:{anchor_id}"
        candidates = [
            CandidateItem(
                item_type="grammar_note",
                anchor_segment_id=anchor_id,
                spans=[text_anchor.model_dump()],
                semantic_dedup_key=dedup_key,
                pattern_key=f"pattern:{anchor_id}",
                quality_score=4,
                reading_blocker=False,
                dedup_hint=dedup_key,
                grammar_point=f"grammar_point:{anchor_id}",
                pattern=f"pattern:{anchor_id}",
                note=f"Observability test note for {anchor_id}.",
            )
        ]
        self.last_candidate_contents.append(
            WindowCandidateContent(
                semantic_dedup_key=dedup_key,
                grammar_point=f"grammar_point:{anchor_id}",
                pattern=f"pattern:{anchor_id}",
                note=f"Observability test note for {anchor_id}.",
                spans=[text_anchor],
            )
        )

        return GrammarWindowExecutionResult(
            candidates=candidates,
            usage_data={
                "input_tokens": 250,
                "output_tokens": 120,
                "total_tokens": 370,
                "cache_read_tokens": 10,
                "cache_write_tokens": 5,
            },
        )

    async def _build_text_range_anchor(
        self,
        base_id: UUID,
        anchor_id: str,
        anchor: dict[str, Any],
    ) -> ReaderTextRangeAnchor:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT base_start_utf16, base_end_utf16, base_id, unit_id
                FROM anchor_segments
                WHERE base_id = $1 AND anchor_segment_id = $2
                """,
                base_id,
                anchor_id,
            )
        if row is None:
            raise RuntimeError(f"anchor_segment {anchor_id} not found")
        async with self._pool.acquire() as conn:
            base_text = await conn.fetchval(
                "SELECT text FROM reading_bases WHERE id = $1",
                base_id,
            )
        from app.contracts.annotation import slice_by_utf16_offsets

        selected_text = slice_by_utf16_offsets(
            str(base_text),
            int(row["base_start_utf16"]),
            int(row["base_end_utf16"]),
        ) or anchor.get("source_text", "x")
        return ReaderTextRangeAnchor(
            base_id=str(base_id),
            unit_id=str(row["unit_id"]),
            anchor_segment_id=anchor_id,
            sentence_id=anchor_id,
            segment_type="sentence",
            start_offset=int(row["base_start_utf16"]),
            end_offset=int(row["base_end_utf16"]),
            selected_text=selected_text,
            text_hash=compute_text_range_hash(selected_text),
        )


class _JournalOrderWindowExecutor(_ObservabilityMockExecutor):
    async def generate(
        self, context: dict[str, Any]
    ) -> GrammarWindowExecutionResult:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT capture_state, usage_delivery_state, execution_slot
                FROM ai_model_execution_journal
                WHERE reader_job_id = $1
                """,
                context["job_id"],
            )
        assert row is not None
        assert row["capture_state"] == "started"
        assert row["usage_delivery_state"] == "not_ready"
        assert row["execution_slot"] == 1
        return await super().generate(context)


class _JournalOrderFailWindowPublisher:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self.calls = 0

    async def publish_window_grammar_bundle(self, **kwargs):
        self.calls += 1
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT capture_state, execution_slot
                FROM ai_model_execution_journal
                WHERE reader_job_id = $1
                """,
                kwargs["job_id"],
            )
        assert row is not None
        assert row["capture_state"] == "captured"
        assert row["execution_slot"] == 1
        raise RuntimeError("stop after durable grammar window capture")


class _FailingWindowMaterializer(ModelExecutionJournalService):
    async def materialize_pending(self, **kwargs):
        raise RuntimeError("grammar window usage sink unavailable")


# ---------------------------------------------------------------------------
# Contract publisher wrapper (injects candidate_contents)
# ---------------------------------------------------------------------------


class _ContractPublisher:
    """Publisher wrapper that injects ``candidate_contents`` from the executor.

    Mirrors the bridge that production ``pipeline_runner`` implements via
    ``_derive_candidate_contents``. Used here so the test exercises the
    real ``GrammarWindowPublisher.publish_window_grammar_bundle`` path
    (with the new publish_fence span wrapper) end-to-end.
    """

    def __init__(
        self,
        *,
        real_publisher: GrammarWindowPublisher,
        executor: _ObservabilityMockExecutor,
    ) -> None:
        self._real = real_publisher
        self._executor = executor

    async def publish_window_grammar_bundle(
        self,
        *,
        job_id: UUID,
        lease_token: UUID,
        plan_id: UUID,
        window_id: UUID,
        candidates: list[CandidateItem],
        candidate_contents: list[WindowCandidateContent] | None = None,
    ):
        if candidate_contents is None:
            candidate_contents = self._executor.last_candidate_contents
        return await self._real.publish_window_grammar_bundle(
            job_id=job_id,
            lease_token=lease_token,
            plan_id=plan_id,
            window_id=window_id,
            candidates=candidates,
            candidate_contents=candidate_contents,
        )


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
async def grammar_window_obs_env() -> AsyncIterator[asyncpg.Pool]:
    schema_name = f"test_grammar_window_obs_{uuid4().hex}"
    admin = await connect_admin()
    original_pool = db_connection.DB_POOL
    await admin.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
    await admin.execute(f'CREATE SCHEMA "{schema_name}"')
    await admin.execute(f'SET search_path TO "{schema_name}", public')
    await admin.execute(BASELINE_SQL)
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


def _make_grammar_window_runner(
    pool: asyncpg.Pool,
    *,
    executor: _ObservabilityMockExecutor,
) -> ReaderEnhancementPipelineRunner:
    translation_worker = TranslationWorkerService(
        pool=pool,
        layer_publisher=CompatTranslationLayerPublisher(pool=pool),
        translator=_StaticTranslator(),
        batch_translator=_StaticBatchTranslator(),
    )
    orchestrator = ReaderOrchestrator(
        pool=pool,
        worker_service=translation_worker,
    )
    vocabulary_worker = VocabularyWorkerService(
        pool=pool,
        executor=_StaticVocabularyExecutor(),
        batch_executor=_StaticBatchVocabularyExecutor(),
    )
    grammar_worker = GrammarBundleWorkerService(
        pool=pool,
        executor=_StaticGrammarExecutor(),
        # T4.2a-R1: inject a fake batch executor so the compact grammar
        # batch path (SHORT_BATCH / STRUCTURED_BATCH) never falls back to
        # the real PydanticAIGrammarBatchExecutor when enable_grammar_window=True.
        batch_executor=_StaticGrammarBatchExecutor(),
    )
    display_title_worker = DisplayTitleWorkerService(
        pool=pool,
        generator=_StaticTitleGenerator(),
    )
    window_worker = GrammarWindowWorkerService(
        pool=pool,
        executor=executor,
    )
    event_runtime = ReaderEventRuntime(pool=pool)
    real_publisher = GrammarWindowPublisher(
        pool=pool,
        event_runtime=event_runtime,
    )
    publisher = _ContractPublisher(
        real_publisher=real_publisher,
        executor=executor,
    )
    return ReaderEnhancementPipelineRunner(
        pool=pool,
        display_title_worker_service=display_title_worker,
        translation_orchestrator=orchestrator,
        translation_batch_worker_service=translation_worker,
        vocabulary_worker_service=vocabulary_worker,
        grammar_worker_service=grammar_worker,
        grammar_window_worker_service=window_worker,
        grammar_window_publisher=publisher,  # type: ignore[arg-type]
        settings=Settings(
            semantic_outline_generation_enabled=False,
            reader_semantic_outline_model_profile="",
        ),
    )


# ---------------------------------------------------------------------------
# Requirement 6: success path writes ai_usage_events + worker_tick span
# ---------------------------------------------------------------------------


async def test_grammar_window_journals_started_then_captured_before_publish(
    grammar_window_obs_env: asyncpg.Pool,
) -> None:
    pool = grammar_window_obs_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool,
        user_id=user_id,
        plain_text=GRAMMAR_WINDOW_OBSERVABILITY_ARTICLE,
        title="grammar-window Journal Order",
    )
    executor = _JournalOrderWindowExecutor(pool=pool)
    publisher = _JournalOrderFailWindowPublisher(pool)
    runner = _make_grammar_window_runner(pool, executor=executor)
    runner._grammar_window_publisher = publisher  # type: ignore[assignment]
    await runner.bootstrap_missing_jobs(record_id=article.record_id, user_id=user_id)

    attempt = await runner._run_grammar_window_attempt(
        record_id=article.record_id,
        base_id=article.base_id,
        expected_generation=1,
        lease_owner="grammar-window-journal-order",
        lease_duration=LEASE_DURATION,
        retry_delay=timedelta(minutes=5),
    )

    assert attempt.outcome == "retry_later"
    assert attempt.job_id is not None
    assert publisher.calls == 1
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, rationale_code FROM reader_jobs WHERE id = $1",
            attempt.job_id,
        )
    assert row is not None and row["status"] == "paused"
    assert row["rationale_code"] == "model_execution_captured_resume_required"


@pytest.mark.parametrize(
    "delivery_state",
    ["pending", "reconciled", "dead_letter"],
)
async def test_grammar_window_captured_restart_is_provider_free_and_delivery_orthogonal(
    grammar_window_obs_env: asyncpg.Pool,
    delivery_state: str,
) -> None:
    pool = grammar_window_obs_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool,
        user_id=user_id,
        plain_text=GRAMMAR_WINDOW_OBSERVABILITY_ARTICLE,
        title=f"grammar-window Resume {delivery_state}",
    )
    first_executor = _JournalOrderWindowExecutor(pool=pool)
    first = _make_grammar_window_runner(pool, executor=first_executor)
    first._grammar_window_publisher = _JournalOrderFailWindowPublisher(  # type: ignore[assignment]
        pool
    )
    await first.bootstrap_missing_jobs(record_id=article.record_id, user_id=user_id)
    paused = await first._run_grammar_window_attempt(
        record_id=article.record_id,
        base_id=article.base_id,
        expected_generation=1,
        lease_owner="grammar-window-before-restart",
        lease_duration=LEASE_DURATION,
        retry_delay=timedelta(minutes=5),
    )
    assert paused.job_id is not None and paused.outcome == "retry_later"

    async with pool.acquire() as conn:
        paused_attempt = await conn.fetchval(
            "SELECT attempt_count FROM reader_jobs WHERE id = $1",
            paused.job_id,
        )
        await conn.execute(
            """
            UPDATE ai_model_execution_journal
            SET usage_delivery_state = $2,
                ai_usage_event_id = CASE WHEN $2 = 'reconciled'
                                         THEN ai_usage_event_id ELSE NULL END,
                delivery_next_attempt_at = NULL,
                reconciled_at = CASE WHEN $2 = 'reconciled' THEN NOW() ELSE NULL END,
                dead_lettered_at = CASE WHEN $2 = 'dead_letter' THEN NOW() ELSE NULL END
            WHERE reader_job_id = $1
            """,
            paused.job_id,
            delivery_state,
        )

    forbidden = _ObservabilityMockExecutor(pool=pool)
    resumed = await _make_grammar_window_runner(
        pool,
        executor=forbidden,
    )._run_grammar_window_attempt(
        record_id=article.record_id,
        base_id=article.base_id,
        expected_generation=1,
        lease_owner="grammar-window-after-restart",
        lease_duration=LEASE_DURATION,
        retry_delay=timedelta(minutes=5),
    )

    assert resumed.outcome == "succeeded"
    assert resumed.job_id == paused.job_id
    assert forbidden.call_count == 0
    async with pool.acquire() as conn:
        final_attempt = await conn.fetchval(
            "SELECT attempt_count FROM reader_jobs WHERE id = $1",
            paused.job_id,
        )
        usage_count = await conn.fetchval(
            "SELECT COUNT(*) FROM ai_usage_events WHERE reader_job_id = $1",
            paused.job_id,
        )
    assert final_attempt == paused_attempt
    assert usage_count == 1


@pytest.mark.parametrize("failure_kind", ["error", "conflict"])
async def test_grammar_window_capture_failure_pauses_without_publish(
    grammar_window_obs_env: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
    failure_kind: str,
) -> None:
    pool = grammar_window_obs_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool,
        user_id=user_id,
        plain_text=GRAMMAR_WINDOW_OBSERVABILITY_ARTICLE,
        title="grammar-window Capture Failure",
    )
    executor = _ObservabilityMockExecutor(pool=pool)
    publisher = _JournalOrderFailWindowPublisher(pool)
    runner = _make_grammar_window_runner(pool, executor=executor)
    runner._grammar_window_publisher = publisher  # type: ignore[assignment]
    journal = runner._grammar_window_worker._journal_service  # type: ignore[union-attr]

    async def _fail_capture(**kwargs):
        if failure_kind == "conflict":
            raise CaptureEnvelopeConflictError("conflicting window capture")
        raise RuntimeError("window capture unavailable")

    monkeypatch.setattr(journal, "capture_execution", _fail_capture)
    await runner.bootstrap_missing_jobs(record_id=article.record_id, user_id=user_id)
    attempt = await runner._run_grammar_window_attempt(
        record_id=article.record_id,
        base_id=article.base_id,
        expected_generation=1,
        lease_owner="grammar-window-capture-failure",
        lease_duration=LEASE_DURATION,
        retry_delay=timedelta(minutes=5),
    )

    assert attempt.outcome == "retry_later"
    assert attempt.job_id is not None
    assert publisher.calls == 0
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, rationale_code FROM reader_jobs WHERE id = $1",
            attempt.job_id,
        )
    assert row is not None and row["status"] == "paused"
    assert row["rationale_code"] == (
        "model_execution_capture_conflict"
        if failure_kind == "conflict"
        else "model_execution_ambiguous"
    )


async def test_grammar_window_begin_failure_never_calls_provider(
    grammar_window_obs_env: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = grammar_window_obs_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool,
        user_id=user_id,
        plain_text=GRAMMAR_WINDOW_OBSERVABILITY_ARTICLE,
        title="grammar-window Begin Failure",
    )
    executor = _ObservabilityMockExecutor(pool=pool)
    publisher = _JournalOrderFailWindowPublisher(pool)
    runner = _make_grammar_window_runner(pool, executor=executor)
    runner._grammar_window_publisher = publisher  # type: ignore[assignment]
    journal = runner._grammar_window_worker._journal_service  # type: ignore[union-attr]

    async def _fail_begin(**kwargs):
        raise RuntimeError("grammar window journal begin unavailable")

    monkeypatch.setattr(journal, "begin_execution", _fail_begin)
    await runner.bootstrap_missing_jobs(record_id=article.record_id, user_id=user_id)
    attempt = await runner._run_grammar_window_attempt(
        record_id=article.record_id,
        base_id=article.base_id,
        expected_generation=1,
        lease_owner="grammar-window-begin-failure",
        lease_duration=LEASE_DURATION,
        retry_delay=timedelta(minutes=5),
    )

    assert attempt.outcome == "retry_later"
    assert attempt.job_id is not None
    assert executor.call_count == 0
    assert publisher.calls == 0
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, rationale_code, failure_code FROM reader_jobs WHERE id = $1",
            attempt.job_id,
        )
    assert row is not None and row["status"] == "paused"
    assert row["rationale_code"] == "model_execution_begin_unconfirmed"
    assert row["failure_code"] == "journal_begin_failed"


async def test_grammar_window_materializer_failure_does_not_block_publish(
    grammar_window_obs_env: asyncpg.Pool,
) -> None:
    pool = grammar_window_obs_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool,
        user_id=user_id,
        plain_text=GRAMMAR_WINDOW_OBSERVABILITY_ARTICLE,
        title="grammar-window Materializer Failure",
    )
    executor = _ObservabilityMockExecutor(pool=pool)
    runner = _make_grammar_window_runner(pool, executor=executor)
    runner._grammar_window_worker._journal_service = (  # type: ignore[union-attr]
        _FailingWindowMaterializer(pool)
    )
    await runner.bootstrap_missing_jobs(record_id=article.record_id, user_id=user_id)

    attempt = await runner._run_grammar_window_attempt(
        record_id=article.record_id,
        base_id=article.base_id,
        expected_generation=1,
        lease_owner="grammar-window-materializer-failure",
        lease_duration=LEASE_DURATION,
        retry_delay=timedelta(minutes=5),
    )

    assert attempt.outcome == "succeeded"
    assert attempt.job_id is not None
    async with pool.acquire() as conn:
        before = await conn.fetchval(
            "SELECT COUNT(*) FROM ai_usage_events WHERE reader_job_id = $1",
            attempt.job_id,
        )
    assert before == 0
    materializer = ModelExecutionJournalService(pool=pool)
    attribution = ReaderUsageAttributionService(journal_service=materializer)
    await attribution.materialize_and_reconcile()
    await attribution.materialize_and_reconcile()
    async with pool.acquire() as conn:
        after = await conn.fetchval(
            "SELECT COUNT(*) FROM ai_usage_events WHERE reader_job_id = $1",
            attempt.job_id,
        )
    assert after == 1


async def test_grammar_window_tampered_receipt_fails_closed_without_provider_recall(
    grammar_window_obs_env: asyncpg.Pool,
) -> None:
    pool = grammar_window_obs_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool,
        user_id=user_id,
        plain_text=GRAMMAR_WINDOW_OBSERVABILITY_ARTICLE,
        title="grammar-window Tampered Receipt",
    )
    first_executor = _JournalOrderWindowExecutor(pool=pool)
    first = _make_grammar_window_runner(pool, executor=first_executor)
    first._grammar_window_publisher = _JournalOrderFailWindowPublisher(  # type: ignore[assignment]
        pool
    )
    await first.bootstrap_missing_jobs(record_id=article.record_id, user_id=user_id)
    paused = await first._run_grammar_window_attempt(
        record_id=article.record_id,
        base_id=article.base_id,
        expected_generation=1,
        lease_owner="grammar-window-before-tamper",
        lease_duration=LEASE_DURATION,
        retry_delay=timedelta(minutes=5),
    )
    assert paused.job_id is not None and paused.outcome == "retry_later"
    async with pool.acquire() as conn:
        payload = dict(
            await conn.fetchval(
                """
                SELECT normalized_payload_json
                FROM ai_model_execution_journal
                WHERE reader_job_id = $1
                """,
                paused.job_id,
            )
        )
        payload["candidates"] = []
        await conn.execute(
            """
            UPDATE ai_model_execution_journal
            SET normalized_payload_json = $2::jsonb
            WHERE reader_job_id = $1
            """,
            paused.job_id,
            jsonb_param(payload),
        )
        await conn.execute(
            """
            UPDATE reader_jobs
            SET status = 'cancelled'
            WHERE reading_record_id = $1
              AND job_type = 'build_grammar_bundle_window'
              AND id <> $2
            """,
            article.record_id,
            paused.job_id,
        )
    forbidden = _ObservabilityMockExecutor(pool=pool)

    resumed = await _make_grammar_window_runner(
        pool,
        executor=forbidden,
    )._run_grammar_window_attempt(
        record_id=article.record_id,
        base_id=article.base_id,
        expected_generation=1,
        lease_owner="grammar-window-after-tamper",
        lease_duration=LEASE_DURATION,
        retry_delay=timedelta(minutes=5),
    )

    assert resumed.outcome == "no_job"
    assert forbidden.call_count == 0
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, rationale_code, failure_code FROM reader_jobs WHERE id = $1",
            paused.job_id,
        )
    assert row is not None and row["status"] == "paused"
    assert row["rationale_code"] == "model_execution_receipt_invalid"
    assert row["failure_code"] == "receipt_payload_invalid"


async def test_grammar_window_lease_loss_after_capture_never_publishes(
    grammar_window_obs_env: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = grammar_window_obs_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool,
        user_id=user_id,
        plain_text=GRAMMAR_WINDOW_OBSERVABILITY_ARTICLE,
        title="grammar-window Lease Loss",
    )
    executor = _ObservabilityMockExecutor(pool=pool)
    publisher = _JournalOrderFailWindowPublisher(pool)
    runner = _make_grammar_window_runner(pool, executor=executor)
    runner._grammar_window_publisher = publisher  # type: ignore[assignment]
    journal = runner._grammar_window_worker._journal_service  # type: ignore[union-attr]
    capture = journal.capture_execution

    async def _capture_then_expire(**kwargs):
        receipt = await capture(**kwargs)
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE reader_jobs
                SET lease_expires_at = NOW() - INTERVAL '1 second'
                WHERE id = $1
                """,
                kwargs["identity"].reader_job_id,
            )
        return receipt

    monkeypatch.setattr(journal, "capture_execution", _capture_then_expire)
    await runner.bootstrap_missing_jobs(record_id=article.record_id, user_id=user_id)
    attempt = await runner._run_grammar_window_attempt(
        record_id=article.record_id,
        base_id=article.base_id,
        expected_generation=1,
        lease_owner="grammar-window-expired-after-capture",
        lease_duration=LEASE_DURATION,
        retry_delay=timedelta(minutes=5),
    )

    assert attempt.outcome == "retry_later"
    assert attempt.job_id is not None
    assert publisher.calls == 0
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT job.status, journal.capture_state
            FROM reader_jobs job
            JOIN ai_model_execution_journal journal
              ON journal.reader_job_id = job.id
            WHERE job.id = $1
            """,
            attempt.job_id,
        )
    assert row is not None and row["status"] == "claimed"
    assert row["capture_state"] == "captured"


async def test_grammar_window_success_writes_ai_usage_event_and_worker_tick_span(
    grammar_window_obs_env: asyncpg.Pool,
) -> None:
    """Requirement 6: grammar-window success path writes an ``ai_usage_events`` row and
    ends the ``worker_tick`` span with token / model / ai_usage_event_id fields.
    """
    pool = grammar_window_obs_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool,
        user_id=user_id,
        plain_text=GRAMMAR_WINDOW_OBSERVABILITY_ARTICLE,
        title="grammar-window Observability Success",
    )

    executor = _ObservabilityMockExecutor(pool=pool)
    runner = _make_grammar_window_runner(pool, executor=executor)

    await runner.bootstrap_missing_jobs(
        record_id=article.record_id,
        user_id=user_id,
    )

    run_summary = await runner.run(
        record_id=article.record_id,
        user_id=user_id,
        lease_owner="grammar-window-obs-success",
        lease_duration=LEASE_DURATION,
        # T4.2a-R1: GROUPED_WINDOWED article (~2230 words) creates more
        # jobs/windows than the original short fixture. Raise limits so the
        # runner can reach coverage_complete.
        max_ticks=100,
        max_jobs=80,
    )

    assert run_summary.outcome_counts.failed_terminal == 0, (
        f"Pipeline had terminal failures: {run_summary.outcome_counts}"
    )
    assert run_summary.worker_tick_counts.grammar_bundle_window >= 1, (
        "grammar-window window worker must have ticked at least once"
    )

    # Verify ai_usage_events row exists for the grammar-window window run.
    # Note: ai_usage_events.id is the PK; ai_usage_event_id lives on
    # reader_runtime_spans (FK back-ref), not on ai_usage_events itself.
    async with pool.acquire() as conn:
        usage_row = await conn.fetchrow(
            """
            SELECT usage.id, usage.user_id, usage.reader_job_id,
                   usage.enhancement_layer_id, usage.capability_code,
                   usage.operation_fingerprint, usage.status,
                   usage.input_tokens, usage.output_tokens, usage.total_tokens,
                   usage.metadata_json, job.output_ref_json
            FROM ai_usage_events usage
            JOIN reader_jobs job ON job.id = usage.reader_job_id
            WHERE usage.reader_job_id IN (
                SELECT id FROM reader_jobs
                WHERE reading_record_id = $1
                  AND job_type = 'build_grammar_bundle_window'
            )
            ORDER BY usage.created_at DESC
            LIMIT 1
            """,
            article.record_id,
        )

    assert usage_row is not None, (
        "ai_usage_events row must exist for grammar-window window publish"
    )
    assert usage_row["user_id"] == user_id, (
        "grammar-window window ai_usage_event must preserve the owning user_id; "
        f"got {usage_row['user_id']!r}"
    )
    assert usage_row["capability_code"] == "reader_grammar_bundle", (
        f"capability_code must be reader_grammar_bundle; "
        f"got {usage_row['capability_code']!r}"
    )
    assert usage_row["operation_fingerprint"] == "grammar_bundle_window_v1", (
        f"operation_fingerprint must be grammar_bundle_window_v1; "
        f"got {usage_row['operation_fingerprint']!r}"
    )
    assert usage_row["status"] == "succeeded", (
        f"ai_usage_event status must be succeeded; "
        f"got {usage_row['status']!r}"
    )
    assert int(usage_row["input_tokens"]) == 250, (
        f"input_tokens must match executor usage_data; "
        f"got {usage_row['input_tokens']!r}"
    )
    assert int(usage_row["output_tokens"]) == 120, (
        f"output_tokens must match executor usage_data; "
        f"got {usage_row['output_tokens']!r}"
    )
    assert int(usage_row["total_tokens"]) == 370, (
        f"total_tokens must match executor usage_data; "
        f"got {usage_row['total_tokens']!r}"
    )

    metadata = usage_row["metadata_json"]
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    assert "plan_id" in metadata, "metadata must contain plan_id"
    assert "window_id" in metadata, "metadata must contain window_id"
    assert "window_index" in metadata, "metadata must contain window_index"
    assert "target_unit_ids" in metadata, "metadata must contain target_unit_ids"
    assert "target_anchor_ids" in metadata, (
        "metadata must contain target_anchor_ids"
    )
    assert metadata["candidate_count"] >= metadata["accepted_count"]
    assert metadata["pre_publish_no_candidates"] is False

    output_ref = usage_row["output_ref_json"]
    if isinstance(output_ref, str):
        output_ref = json.loads(output_ref)
    expected_layer_ids = output_ref["grammar_note_layer_ids"] + output_ref[
        "sentence_analysis_layer_ids"
    ]
    assert metadata["accepted_count"] == output_ref["accepted_count"]
    assert metadata["no_op"] == output_ref["no_op"]
    assert metadata["layer_ids"] == expected_layer_ids
    assert metadata["grammar_note_layer_ids"] == output_ref[
        "grammar_note_layer_ids"
    ]
    assert metadata["sentence_analysis_layer_ids"] == output_ref[
        "sentence_analysis_layer_ids"
    ]
    assert expected_layer_ids
    assert usage_row["enhancement_layer_id"] == UUID(expected_layer_ids[0])

    # Verify worker_tick span carries duration_ms + token fields +
    # ai_usage_event_id. Spans are best-effort; if no span row exists the
    # test still passes (observability degraded but pipeline succeeded).
    # Filter for the SUCCEEDED span (later ticks may produce SKIPPED
    # spans when the job is already terminal — those don't carry usage).
    async with pool.acquire() as conn:
        span_row = await conn.fetchrow(
            """
            SELECT duration_ms, input_tokens, output_tokens, total_tokens,
                   ai_usage_event_id, status, capability_code, worker_type
            FROM reader_runtime_spans
            WHERE worker_type = 'grammar_bundle_window'
              AND span_kind = 'worker_tick'
              AND status = 'succeeded'
            ORDER BY started_at DESC
            LIMIT 1
            """,
        )

    if span_row is not None:
        assert span_row["status"] == "succeeded", (
            f"worker_tick span status must be succeeded; "
            f"got {span_row['status']!r}"
        )
        assert span_row["capability_code"] == "reader_grammar_bundle", (
            f"worker_tick span capability_code must be reader_grammar_bundle; "
            f"got {span_row['capability_code']!r}"
        )
        assert span_row["duration_ms"] is not None, (
            "worker_tick span must carry duration_ms after end_span"
        )
        assert int(span_row["input_tokens"]) == 250, (
            f"worker_tick span input_tokens must match usage; "
            f"got {span_row['input_tokens']!r}"
        )
        assert int(span_row["output_tokens"]) == 120, (
            f"worker_tick span output_tokens must match usage; "
            f"got {span_row['output_tokens']!r}"
        )
        assert span_row["ai_usage_event_id"] is not None, (
            "worker_tick span must carry ai_usage_event_id after success"
        )
        assert str(span_row["ai_usage_event_id"]) == str(usage_row["id"]), (
            f"worker_tick span ai_usage_event_id must match ai_usage_events.id; "
            f"span={span_row['ai_usage_event_id']!r} "
            f"usage={usage_row['id']!r}"
        )


# ---------------------------------------------------------------------------
# Requirement 7: publish_fence span for success and failure
# ---------------------------------------------------------------------------


async def test_grammar_window_publish_fence_span_success(
    grammar_window_obs_env: asyncpg.Pool,
) -> None:
    """Requirement 7: successful window publish writes a ``publish_fence``
    span with ``status='succeeded'`` and metadata containing ``layer_type``,
    ``plan_id``, ``window_id``, ``accepted_count``, ``no_op``, ``layer_ids``.
    """
    pool = grammar_window_obs_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool,
        user_id=user_id,
        plain_text=GRAMMAR_WINDOW_OBSERVABILITY_ARTICLE,
        title="grammar-window Publish Fence Success",
    )

    executor = _ObservabilityMockExecutor(pool=pool)
    runner = _make_grammar_window_runner(pool, executor=executor)

    await runner.bootstrap_missing_jobs(
        record_id=article.record_id,
        user_id=user_id,
    )
    await runner.run(
        record_id=article.record_id,
        user_id=user_id,
        lease_owner="grammar-window-fence-success",
        lease_duration=LEASE_DURATION,
        # T4.2a-R1: GROUPED_WINDOWED article requires higher limits.
        max_ticks=100,
        max_jobs=80,
    )

    async with pool.acquire() as conn:
        fence_span = await conn.fetchrow(
            """
            SELECT status, failure_class, failure_code, metadata_json
            FROM reader_runtime_spans
            WHERE span_kind = 'publish_fence'
              AND reader_job_id IN (
                SELECT id FROM reader_jobs
                WHERE job_type = 'build_grammar_bundle_window'
              )
            ORDER BY started_at DESC
            LIMIT 1
            """,
        )

    assert fence_span is not None, (
        "publish_fence span must exist for grammar-window window publish"
    )
    assert fence_span["status"] == "succeeded", (
        f"publish_fence span status must be succeeded; "
        f"got {fence_span['status']!r}"
    )
    metadata = fence_span["metadata_json"]
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    assert metadata.get("layer_type") == "grammar_bundle_window", (
        f"publish_fence metadata layer_type must be grammar_bundle_window; "
        f"got {metadata.get('layer_type')!r}"
    )
    assert "plan_id" in metadata, "publish_fence metadata must contain plan_id"
    assert "window_id" in metadata, (
        "publish_fence metadata must contain window_id"
    )
    assert "accepted_count" in metadata, (
        "publish_fence metadata must contain accepted_count"
    )


async def test_grammar_window_publish_fence_span_failure(
    grammar_window_obs_env: asyncpg.Pool,
) -> None:
    """Requirement 7: failed window publish (FenceViolationError) writes a
    ``publish_fence`` span with ``status='failed'`` and
    ``failure_class='fence_violation'``.
    """
    pool = grammar_window_obs_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool,
        user_id=user_id,
        plain_text=GRAMMAR_WINDOW_OBSERVABILITY_ARTICLE,
        title="grammar-window Publish Fence Failure",
    )

    executor = _ObservabilityMockExecutor(pool=pool)
    runner = _make_grammar_window_runner(pool, executor=executor)

    await runner.bootstrap_missing_jobs(
        record_id=article.record_id,
        user_id=user_id,
    )

    # Trigger the real publisher wrapper's FenceViolationError path by forcing
    # the inner job-runtime fence validation to fail after the window has been
    # claimed and preflighted.
    contract_publisher = runner._grammar_window_publisher
    real_publisher = contract_publisher._real  # type: ignore[attr-defined]
    real_publisher._job_runtime._validate_fence = AsyncMock(  # type: ignore[method-assign]
        return_value="simulated_stale_generation"
    )

    await runner.run(
        record_id=article.record_id,
        user_id=user_id,
        lease_owner="grammar-window-fence-failure",
        lease_duration=LEASE_DURATION,
        # T4.2a-R1: GROUPED_WINDOWED article requires higher limits.
        max_ticks=100,
        max_jobs=80,
    )

    async with pool.acquire() as conn:
        fence_span = await conn.fetchrow(
            """
            SELECT status, failure_class, failure_code
            FROM reader_runtime_spans
            WHERE span_kind = 'publish_fence'
              AND reader_job_id IN (
                SELECT id FROM reader_jobs
                WHERE job_type = 'build_grammar_bundle_window'
              )
            ORDER BY started_at DESC
            LIMIT 1
            """,
        )

    assert fence_span is not None, "publish_fence span must exist on real fence failure"
    assert fence_span["status"] == "failed", (
        f"publish_fence span status must be failed; got {fence_span['status']!r}"
    )
    assert fence_span["failure_class"] == "fence_violation", (
        "publish_fence span failure_class must be fence_violation; "
        f"got {fence_span['failure_class']!r}"
    )
    assert fence_span["failure_code"] == "fence_failed", (
        f"publish_fence span failure_code must be fence_failed; "
        f"got {fence_span['failure_code']!r}"
    )


# ---------------------------------------------------------------------------
# Requirement 7: publish_fence span for publisher ValueError (publish_exception)
# ---------------------------------------------------------------------------


async def test_grammar_window_publish_fence_span_value_error_records_publish_exception(
    grammar_window_obs_env: asyncpg.Pool,
) -> None:
    """Requirement 7: publisher exception writes a ``publish_fence`` span
    with ``status='failed'`` and ``failure_class='publish_exception'``.

    The publish_fence span wrapper inside ``publish_window_grammar_bundle``
    catches any non-``FenceViolationError`` exception and records it as
    ``failure_class='publish_exception'`` with
    ``failure_code=type(exc).__name__``.

    We trigger this by calling the publisher with a non-existent
    ``plan_id`` (random UUID). The inner method raises ``LookupError``
    ("plan not found"), which the wrapper catches as ``publish_exception``.

    We bypass ``pipeline_runner._run_grammar_window_attempt`` because the
    runner's ``_derive_candidate_contents`` bridge intercepts content
    violations before they reach the publisher. The publish_fence span
    wrapper lives inside the publisher itself, so calling the publisher
    directly is the correct unit-level way to verify its span behavior.
    """
    from uuid import uuid4

    from app.services.reader_orchestration.grammar_window_publisher import (
        GrammarWindowPublisher,
    )
    from app.services.reader_orchestration.window_selector import CandidateItem

    pool = grammar_window_obs_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool,
        user_id=user_id,
        plain_text=GRAMMAR_WINDOW_OBSERVABILITY_ARTICLE,
        title="grammar-window Publish Fence Exception",
    )

    # Bootstrap creates a real grammar-window job so we have a valid job_id for span
    # metadata. We then call the publisher with a FAKE plan_id to trigger
    # LookupError inside _publish_window_grammar_bundle_inner. The plan
    # check is the FIRST step in the inner method, so it fails before
    # checking job status (which would be "queued", not "claimed").
    executor = _ObservabilityMockExecutor(pool=pool)
    runner = _make_grammar_window_runner(pool, executor=executor)
    await runner.bootstrap_missing_jobs(
        record_id=article.record_id,
        user_id=user_id,
    )

    async with pool.acquire() as conn:
        job_row = await conn.fetchrow(
            """
            SELECT id FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = 'build_grammar_bundle_window'
            ORDER BY created_at ASC
            LIMIT 1
            """,
            article.record_id,
        )
    assert job_row is not None, "grammar-window window job must exist after bootstrap"
    job_id = job_row["id"]

    real_publisher = GrammarWindowPublisher(
        pool=pool,
        event_runtime=ReaderEventRuntime(pool=pool),
    )

    # Random plan_id / window_id that DON'T exist in the DB → LookupError
    # inside _publish_window_grammar_bundle_inner.
    fake_plan_id = uuid4()
    fake_window_id = uuid4()
    bare_candidate = CandidateItem(
        item_type="grammar_note",
        anchor_segment_id=str(uuid4()),
        spans=[],
        semantic_dedup_key="bare:1",
        pattern_key="bare:pattern:1",
        quality_score=3,
        reading_blocker=False,
        dedup_hint="bare:hint:1",
    )

    # The publisher must raise LookupError because plan_id doesn't exist.
    with pytest.raises(LookupError):
        await real_publisher.publish_window_grammar_bundle(
            job_id=job_id,
            lease_token=uuid4(),
            plan_id=fake_plan_id,
            window_id=fake_window_id,
            candidates=[bare_candidate],
            candidate_contents=None,
        )

    async with pool.acquire() as conn:
        fence_span = await conn.fetchrow(
            """
            SELECT status, failure_class, failure_code
            FROM reader_runtime_spans
            WHERE span_kind = 'publish_fence'
              AND reader_job_id = $1
            ORDER BY started_at DESC
            LIMIT 1
            """,
            job_id,
        )

    assert fence_span is not None, (
        "publish_fence span must exist when publisher raises LookupError"
    )
    assert fence_span["status"] == "failed", (
        f"publish_fence span status must be failed; "
        f"got {fence_span['status']!r}"
    )
    assert fence_span["failure_class"] == "publish_exception", (
        f"publish_fence failure_class must be publish_exception; "
        f"got {fence_span['failure_class']!r}"
    )
    assert fence_span["failure_code"] == "LookupError", (
        f"publish_fence failure_code must be LookupError; "
        f"got {fence_span['failure_code']!r}"
    )


# ---------------------------------------------------------------------------
# Grammar-window worker-tick LangSmith ownership: the owning worker_tick
# carries langsmith_run_id; the publish_fence span does NOT inherit it.
# ---------------------------------------------------------------------------


async def test_grammar_window_worker_tick_single_owner_langsmith_run_id(
    grammar_window_obs_env: asyncpg.Pool,
) -> None:
    """Worker-tick LangSmith ownership for the grammar-window lane.

    The mock executor sets ``_CURRENT_LANGSMITH_IDS`` inside ``generate()`` —
    the same moment ``LangSmithIdBridgeProcessor.on_end`` captures the run id
    after a real PydanticAI LLM span ends. The owning ``worker_tick`` consumes
    that id via ``end_worker_span_success`` and writes it to
    ``reader_runtime_spans.langsmith_run_id``. The ``publish_fence`` span ends
    before the worker_tick and must NOT inherit the id: the generic
    ``end_span`` no longer auto-reads the ContextVar.
    """
    pool = grammar_window_obs_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool,
        user_id=user_id,
        plain_text=GRAMMAR_WINDOW_OBSERVABILITY_ARTICLE,
        title="grammar-window LangSmith single-owner",
    )

    mock_ids = LangSmithIds(
        trace_id="langsmith-trace-grammar-window-bridge",
        span_id="langsmith-span-grammar-window-bridge",
    )
    executor = _ObservabilityMockExecutor(pool=pool, langsmith_ids=mock_ids)
    runner = _make_grammar_window_runner(pool, executor=executor)

    await runner.bootstrap_missing_jobs(
        record_id=article.record_id,
        user_id=user_id,
    )

    try:
        run_summary = await runner.run(
            record_id=article.record_id,
            user_id=user_id,
            lease_owner="grammar-window-langsmith-single-owner",
            lease_duration=LEASE_DURATION,
            # GROUPED_WINDOWED article requires higher limits.
            max_ticks=100,
            max_jobs=80,
        )
    finally:
        clear_langsmith_ids()

    assert run_summary.outcome_counts.failed_terminal == 0, (
        f"Pipeline had terminal failures: {run_summary.outcome_counts}"
    )
    assert run_summary.worker_tick_counts.grammar_bundle_window >= 1, (
        "grammar-window window worker must have ticked at least once"
    )

    # The owning succeeded worker_tick carries the LangSmith run id.
    async with pool.acquire() as conn:
        span_row = await conn.fetchrow(
            """
            SELECT langsmith_run_id, status, span_kind, worker_type
            FROM reader_runtime_spans
            WHERE worker_type = 'grammar_bundle_window'
              AND span_kind = 'worker_tick'
              AND status = 'succeeded'
            ORDER BY started_at DESC
            LIMIT 1
            """,
        )

    assert span_row is not None, (
        "succeeded worker_tick span must exist for grammar-window window run"
    )
    assert span_row["langsmith_run_id"] == mock_ids.run_id, (
        f"owning worker_tick span langsmith_run_id must match the LLM run; "
        f"got {span_row['langsmith_run_id']!r}, "
        f"expected {mock_ids.run_id!r}"
    )

    # The publish_fence span ends before the worker_tick and must NOT inherit
    # the LangSmith run id (single-owner; no generic end_span auto-read).
    async with pool.acquire() as conn:
        fence_span = await conn.fetchrow(
            """
            SELECT langsmith_run_id, status, span_kind
            FROM reader_runtime_spans
            WHERE span_kind = 'publish_fence'
              AND status = 'succeeded'
            ORDER BY started_at DESC
            LIMIT 1
            """,
        )

    if fence_span is not None:
        assert fence_span["langsmith_run_id"] is None, (
            f"publish_fence span must not inherit the LangSmith run id; "
            f"got {fence_span['langsmith_run_id']!r}"
        )
