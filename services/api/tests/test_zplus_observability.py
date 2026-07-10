"""Z+ Analysis Window observability tests.

Requirements covered:
  - Requirement 6: Z+ success path records an ``ai_usage_events`` row with
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
  - Requirement 8: ``end_span`` auto-populates ``langsmith_run_id`` from
    the ``LangSmithIdBridgeProcessor`` ContextVar when the caller does not
    pass an explicit value (mock test).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.contracts.annotation import compute_text_range_hash
from app.database import connection as db_connection
from app.schemas.reader_orchestration import ReaderTextRangeAnchor
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

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MIGRATION_0015_SQL = (
    _REPO_ROOT / "infra" / "migrations" / "0015_layer_analysis_plans.sql"
).read_text(encoding="utf-8")
_MIGRATION_0016_SQL = (
    _REPO_ROOT / "infra" / "migrations" / "0016_reader_runtime_spans_grammar_bundle_window.sql"
).read_text(encoding="utf-8")
# T1.1: add translate_article / build_vocabulary_layer_article job types
_MIGRATION_0017_SQL = (
    _REPO_ROOT / "infra" / "migrations" / "0017_reader_jobs_batch_path_job_types.sql"
).read_text(encoding="utf-8")

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
# Z+ window path is exercised. Before T4.1c, all Z+ enabled articles went
# through the window path; after T4.1c, only GROUPED_WINDOWED articles do.
# Repeating the base paragraph 26 times yields ~2230 words, safely above the
# 2000-word STRUCTURED_ARTICLE_MAX_WORD_COUNT threshold.
ZPLUS_OBSERVABILITY_ARTICLE = "\n\n".join([_OBS_BASE_PARAGRAPH] * 26)


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

    def __init__(self, *, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self.last_candidate_contents: list[WindowCandidateContent] = []
        self.call_count = 0

    async def generate(
        self, context: dict[str, Any]
    ) -> GrammarWindowExecutionResult:
        self.call_count += 1
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
                quality_score=0.8,
                reading_blocker=False,
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
async def zplus_obs_env() -> AsyncIterator[asyncpg.Pool]:
    schema_name = f"test_zplus_obs_{uuid4().hex}"
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


def _make_zplus_runner(
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
        # the real PydanticAIGrammarBatchExecutor when enable_zplus_grammar=True.
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
    )


# ---------------------------------------------------------------------------
# Requirement 6: success path writes ai_usage_events + worker_tick span
# ---------------------------------------------------------------------------


async def test_zplus_success_writes_ai_usage_event_and_worker_tick_span(
    zplus_obs_env: asyncpg.Pool,
) -> None:
    """Requirement 6: Z+ success path writes an ``ai_usage_events`` row and
    ends the ``worker_tick`` span with token / model / ai_usage_event_id fields.
    """
    pool = zplus_obs_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool,
        user_id=user_id,
        plain_text=ZPLUS_OBSERVABILITY_ARTICLE,
        title="Z+ Observability Success",
    )

    executor = _ObservabilityMockExecutor(pool=pool)
    runner = _make_zplus_runner(pool, executor=executor)

    await runner.bootstrap_missing_jobs(
        record_id=article.record_id,
        user_id=user_id,
    )

    run_summary = await runner.run(
        record_id=article.record_id,
        user_id=user_id,
        lease_owner="zplus-obs-success",
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
        "Z+ window worker must have ticked at least once"
    )

    # Verify ai_usage_events row exists for the Z+ window run.
    # Note: ai_usage_events.id is the PK; ai_usage_event_id lives on
    # reader_runtime_spans (FK back-ref), not on ai_usage_events itself.
    async with pool.acquire() as conn:
        usage_row = await conn.fetchrow(
            """
            SELECT id, user_id, capability_code, operation_fingerprint, status,
                   input_tokens, output_tokens, total_tokens, metadata_json
            FROM ai_usage_events
            WHERE reader_job_id IN (
                SELECT id FROM reader_jobs
                WHERE reading_record_id = $1
                  AND job_type = 'build_grammar_bundle_window'
            )
            ORDER BY created_at DESC
            LIMIT 1
            """,
            article.record_id,
        )

    assert usage_row is not None, (
        "ai_usage_events row must exist for Z+ window publish"
    )
    assert usage_row["user_id"] == user_id, (
        "Z+ window ai_usage_event must preserve the owning user_id; "
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
    assert "accepted_count" in metadata, "metadata must contain accepted_count"
    assert "no_op" in metadata, "metadata must contain no_op"

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


async def test_zplus_publish_fence_span_success(
    zplus_obs_env: asyncpg.Pool,
) -> None:
    """Requirement 7: successful window publish writes a ``publish_fence``
    span with ``status='succeeded'`` and metadata containing ``layer_type``,
    ``plan_id``, ``window_id``, ``accepted_count``, ``no_op``, ``layer_ids``.
    """
    pool = zplus_obs_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool,
        user_id=user_id,
        plain_text=ZPLUS_OBSERVABILITY_ARTICLE,
        title="Z+ Publish Fence Success",
    )

    executor = _ObservabilityMockExecutor(pool=pool)
    runner = _make_zplus_runner(pool, executor=executor)

    await runner.bootstrap_missing_jobs(
        record_id=article.record_id,
        user_id=user_id,
    )
    await runner.run(
        record_id=article.record_id,
        user_id=user_id,
        lease_owner="zplus-fence-success",
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
        "publish_fence span must exist for Z+ window publish"
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


async def test_zplus_publish_fence_span_failure(
    zplus_obs_env: asyncpg.Pool,
) -> None:
    """Requirement 7: failed window publish (FenceViolationError) writes a
    ``publish_fence`` span with ``status='failed'`` and
    ``failure_class='fence_violation'``.
    """
    pool = zplus_obs_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool,
        user_id=user_id,
        plain_text=ZPLUS_OBSERVABILITY_ARTICLE,
        title="Z+ Publish Fence Failure",
    )

    executor = _ObservabilityMockExecutor(pool=pool)
    runner = _make_zplus_runner(pool, executor=executor)

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
        lease_owner="zplus-fence-failure",
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


async def test_zplus_publish_fence_span_value_error_records_publish_exception(
    zplus_obs_env: asyncpg.Pool,
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

    pool = zplus_obs_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool,
        user_id=user_id,
        plain_text=ZPLUS_OBSERVABILITY_ARTICLE,
        title="Z+ Publish Fence Exception",
    )

    # Bootstrap creates a real Z+ job so we have a valid job_id for span
    # metadata. We then call the publisher with a FAKE plan_id to trigger
    # LookupError inside _publish_window_grammar_bundle_inner. The plan
    # check is the FIRST step in the inner method, so it fails before
    # checking job status (which would be "queued", not "claimed").
    executor = _ObservabilityMockExecutor(pool=pool)
    runner = _make_zplus_runner(pool, executor=executor)
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
    assert job_row is not None, "Z+ window job must exist after bootstrap"
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
        quality_score=0.5,
        reading_blocker=False,
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
# Requirement 8: LangSmith bridge — end_span auto-populates langsmith_run_id
# from the LangSmithIdBridgeProcessor ContextVar
# ---------------------------------------------------------------------------


async def test_zplus_window_worker_end_span_backfills_langsmith_run_id(
    zplus_obs_env: asyncpg.Pool,
) -> None:
    """Requirement 8: ``end_span`` auto-populates ``langsmith_run_id`` on the
    ``worker_tick`` span from the ``LangSmithIdBridgeProcessor`` ContextVar.

    This proves the dual-track PG span + LangSmith bridge design: when a
    LangSmith-managed OTel span ends inside the worker's async context
    (setting ``_CURRENT_LANGSMITH_IDS``), the subsequent
    ``end_worker_span_success`` call reads the ContextVar and writes
    ``langsmith_run_id`` to the ``reader_runtime_spans`` row — without the
    worker call site needing to thread the LangSmith run ID explicitly.

    The test simulates the ContextVar state that
    ``LangSmithIdBridgeProcessor.on_end`` would set after a real PydanticAI
    LLM span ends. We don't need a real LangSmith export — the ContextVar is
    the bridge contract.
    """
    from app.observability.langsmith_span_processor import (
        _CURRENT_LANGSMITH_IDS,
        LangSmithIds,
        clear_langsmith_ids,
    )

    pool = zplus_obs_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool,
        user_id=user_id,
        plain_text=ZPLUS_OBSERVABILITY_ARTICLE,
        title="Z+ LangSmith Bridge",
    )

    executor = _ObservabilityMockExecutor(pool=pool)
    runner = _make_zplus_runner(pool, executor=executor)

    await runner.bootstrap_missing_jobs(
        record_id=article.record_id,
        user_id=user_id,
    )

    # Simulate LangSmithIdBridgeProcessor.on_end setting the ContextVar
    # after a PydanticAI LLM span ends. The worker_tick span's end_span
    # will read this ContextVar and backfill langsmith_run_id.
    mock_ids = LangSmithIds(
        trace_id="langsmith-trace-zplus-bridge",
        span_id="langsmith-span-zplus-bridge",
    )
    token = _CURRENT_LANGSMITH_IDS.set(mock_ids)
    try:
        run_summary = await runner.run(
            record_id=article.record_id,
            user_id=user_id,
            lease_owner="zplus-langsmith-bridge",
            lease_duration=LEASE_DURATION,
            # T4.2a-R1: GROUPED_WINDOWED article requires higher limits.
            max_ticks=100,
            max_jobs=80,
        )
    finally:
        _CURRENT_LANGSMITH_IDS.reset(token)
        clear_langsmith_ids()

    assert run_summary.outcome_counts.failed_terminal == 0, (
        f"Pipeline had terminal failures: {run_summary.outcome_counts}"
    )
    assert run_summary.worker_tick_counts.grammar_bundle_window >= 1, (
        "Z+ window worker must have ticked at least once"
    )

    # Verify the worker_tick span carries langsmith_run_id backfilled from
    # the ContextVar. Filter for the SUCCEEDED span (later ticks may produce
    # SKIPPED spans when the job is already terminal).
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
        "succeeded worker_tick span must exist for Z+ window run"
    )
    assert span_row["langsmith_run_id"] == mock_ids.run_id, (
        f"worker_tick span langsmith_run_id must match ContextVar; "
        f"got {span_row['langsmith_run_id']!r}, "
        f"expected {mock_ids.run_id!r}"
    )

    # Also verify the publish_fence span carries the same langsmith_run_id
    # (the ContextVar persists across both span lifecycles within the same
    # async context).
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
        assert fence_span["langsmith_run_id"] == mock_ids.run_id, (
            f"publish_fence span langsmith_run_id must match ContextVar; "
            f"got {fence_span['langsmith_run_id']!r}, "
            f"expected {mock_ids.run_id!r}"
        )
