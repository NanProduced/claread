"""Synthetic expanded long-form regression test for Z+ window architecture.

Originally a BBC 858-word regression test (Task C5b) that verified grammar
LLM call reduction from 37 (per-unit) to 3-5 (per-window). After T4.1c,
the original BBC article (858 words) routes to SHORT_BATCH and uses the
grammar batch path, not the Z+ window path. To keep exercising the Z+
window path, the fixture now repeats the BBC article 3x (~2574 words) so
the route is GROUPED_WINDOWED.

This means:
- The test no longer verifies the original BBC 3-5 window regression.
- The original BBC 858-word sample as a SHORT/STRUCTURED routing regression
  is NOT covered here; it should be added as a separate test if needed.
- Expected window count is 9-15 (not 3-5) due to the 3x expansion.

The test exercises the full pipeline:
  bootstrap → plan/windows/jobs → worker preflight → LLM (mock executor) →
  selector (8 hard gates) → publisher → enhancement_layers.

A ``_StaticGrammarWindowExecutor`` produces realistic candidates that pass
all 8 selector hard gates (unique semantic_dedup_key / pattern_key per
anchor, consistent unit_id per candidate, within window_budget).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.contracts.annotation import compute_text_range_hash
from app.database import connection as db_connection
from app.services.reader_orchestration.display_title_worker import (
    DisplayTitleWorkerService,
)
from app.services.reader_orchestration.grammar_window_publisher import (
    GrammarWindowPublisher,
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
from app.services.reader_orchestration.zplus_bootstrap import ZPlusBootstrapService
from tests.fixtures.bbc_cd6684a0_expected_windows import (
    assert_expected_grammar_note_total,
    assert_expected_sentence_analysis_total,
    assert_expected_window_count,
)
from tests.fixtures.bbc_cd6684a0_input import (
    BBC_ARTICLE_TEXT,
    BBC_ARTICLE_TITLE,
    BBC_SOURCE_LANGUAGE,
)
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
_MIGRATION_0015_SQL = "SELECT 1"  # folded into infra/migrations/0001_initial.sql
_MIGRATION_0016_SQL = "SELECT 1"  # folded into infra/migrations/0001_initial.sql
# T4.2a-R1: migration 0017 adds ``translate_article`` and
# ``build_vocabulary_layer_article`` to the ``reader_jobs.job_type`` CHECK
# constraint. Required because the BBC article (>6000 chars) triggers the
# T3.1 grouped translation/vocabulary path which creates these job types.
_MIGRATION_0017_SQL = "SELECT 1"  # folded into infra/migrations/0001_initial.sql

LEASE_DURATION = timedelta(seconds=30)


# ---------------------------------------------------------------------------
# Static grammar window executor (mock LLM)
# ---------------------------------------------------------------------------


class _StaticGrammarWindowExecutor:
    """Mock executor producing realistic candidates for BBC regression.

    Produces up to ``window_budget.grammar_note.count`` grammar_note
    candidates per window using distinct target anchors. Each candidate
    carries a unique ``semantic_dedup_key`` and ``pattern_key`` derived
    from ``anchor_segment_id`` so the DUP and PATTERN_DENSE gates do not
    fire. Each candidate's spans share a single ``unit_id`` so the
    MULTI_UNIT_SPAN gate passes.

    Also emits at most 1 ``sentence_analysis`` per window, capped to the
    first 3 windows, so the total stays within the sentence_analysis
    record density cap.
    """

    def __init__(self) -> None:
        self.call_count = 0
        self.window_calls: list[dict[str, Any]] = []
        self._sentence_windows_emitted = 0

    async def generate(
        self, context: dict[str, Any]
    ) -> GrammarWindowExecutionResult:
        self.call_count += 1
        self.window_calls.append(context)

        candidates: list[CandidateItem] = []
        target_anchors = context.get("target_anchors", [])
        if not target_anchors:
            return GrammarWindowExecutionResult(candidates=candidates)

        base_id = str(context.get("base_id", ""))

        window_budget = context.get("window_budget", {})
        if isinstance(window_budget, dict):
            grammar_budget = int(
                window_budget.get("grammar_note", {}).get("count", 2)
            )
            sentence_budget = int(
                window_budget.get("sentence_analysis", {}).get("count", 1)
            )
        else:
            grammar_budget = 2
            sentence_budget = 1

        # grammar_note: 1 per anchor, up to window budget.
        for anchor in target_anchors[:grammar_budget]:
            anchor_id = str(anchor["anchor_segment_id"])
            unit_id = str(anchor["unit_id"])
            source_text = str(anchor.get("source_text", "x" * 10))[:10] or "xxxxxxxxxx"
            text_hash = compute_text_range_hash(source_text)
            candidates.append(
                CandidateItem(
                    item_type="grammar_note",
                    anchor_segment_id=anchor_id,
                    spans=[{
                        "base_id": base_id,
                        "unit_id": unit_id,
                        "anchor_segment_id": anchor_id,
                        "start_offset": 0,
                        "end_offset": max(1, len(source_text)),
                        "selected_text": source_text,
                        "text_hash": text_hash,
                    }],
                    semantic_dedup_key=f"grammar:{anchor_id}",
                    pattern_key=f"pattern:{anchor_id}",
                    quality_score=4,
                    reading_blocker=False,
                    dedup_hint=f"grammar:{anchor_id}",
                    # P2-1: populate content_* fields for contract output
                    grammar_point=f"grammar_point:{anchor_id}",
                    pattern=f"pattern:{anchor_id}",
                    note=f"Grammar note for {anchor_id}.",
                )
            )

        # sentence_analysis: only for the first 3 windows, 1 per window.
        if self._sentence_windows_emitted < 3 and sentence_budget > 0:
            anchor = target_anchors[0]
            anchor_id = str(anchor["anchor_segment_id"])
            unit_id = str(anchor["unit_id"])
            source_text = str(anchor.get("source_text", "x" * 10))[:10] or "xxxxxxxxxx"
            text_hash = compute_text_range_hash(source_text)
            candidates.append(
                CandidateItem(
                    item_type="sentence_analysis",
                    anchor_segment_id=anchor_id,
                    spans=[{
                        "base_id": base_id,
                        "unit_id": unit_id,
                        "anchor_segment_id": anchor_id,
                        "start_offset": 0,
                        "end_offset": max(1, len(source_text)),
                        "selected_text": source_text,
                        "text_hash": text_hash,
                    }],
                    semantic_dedup_key=f"sentence:{anchor_id}",
                    pattern_key=None,
                    quality_score=5,
                    reading_blocker=False,
                    dedup_hint=f"sentence:{anchor_id}",
                    # P2-1: populate content_* fields for contract output
                    label=f"main_clause:{anchor_id}",
                    analysis=f"Sentence analysis for {anchor_id}.",
                    chunks=[{
                        "order": 1,
                        "label": "clause",
                        "text": source_text,
                    }],
                )
            )
            self._sentence_windows_emitted += 1

        return GrammarWindowExecutionResult(candidates=candidates)


# ---------------------------------------------------------------------------
# Fixture: BBC article + Z+ plan bootstrapped
# ---------------------------------------------------------------------------


@pytest.fixture
async def bbc_regression_env() -> AsyncIterator[
    tuple[asyncpg.Pool, UUID, UUID, UUID]
]:
    """Set up a DB with the BBC article and Z+ plan bootstrapped.

    Returns ``(pool, record_id, base_id, user_id)``. The Z+ plan, windows,
    and window reader_jobs are created by ``ZPlusBootstrapService``. When
    the pipeline runner calls ``bootstrap_missing_jobs``, it finds the
    existing plan and routes grammar to the Z+ path (no legacy
    ``build_grammar_bundle`` jobs are created).
    """
    schema_name = f"test_zplus_bbc_regression_{uuid4().hex}"
    admin = await connect_admin()
    await admin.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
    await admin.execute(f'CREATE SCHEMA "{schema_name}"')
    await admin.execute(f'SET search_path TO "{schema_name}", public')
    await admin.execute(BASELINE_SQL)
    await admin.execute(_MIGRATION_0015_SQL)
    await admin.execute(_MIGRATION_0016_SQL)
    await admin.execute(_MIGRATION_0017_SQL)
    await admin.close()

    pool = await make_pool(schema_name)
    previous_pool = db_connection.DB_POOL
    db_connection.DB_POOL = pool
    try:
        user_id = await insert_user(pool)
        # T4.2a-R1: repeat the BBC article 3x to exceed 2000 words so the
        # grammar route is GROUPED_WINDOWED (not SHORT_BATCH). Before T4.1c,
        # all Z+ enabled articles used the Z+ window path; after T4.1c, only
        # GROUPED_WINDOWED articles do. The original BBC article (858 words)
        # would route to SHORT_BATCH and use the grammar batch path instead,
        # defeating the test's purpose of verifying Z+ window call reduction.
        expanded_text = "\n\n".join([BBC_ARTICLE_TEXT] * 3)
        article = await submit_article_ready(
            pool,
            user_id=user_id,
            plain_text=expanded_text,
            title=BBC_ARTICLE_TITLE,
            language=BBC_SOURCE_LANGUAGE,
        )
        record_id = article.record_id
        base_id = article.base_id

        # Bootstrap Z+ plan + windows + window reader_jobs.
        bootstrap = ZPlusBootstrapService(pool=pool)
        await bootstrap.bootstrap_grammar_window_plan(
            record_id=record_id,
            base_id=base_id,
        )

        yield pool, record_id, base_id, user_id
    finally:
        db_connection.DB_POOL = previous_pool
        await pool.close()
        cleanup = await connect_admin()
        await cleanup.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await cleanup.close()


# ---------------------------------------------------------------------------
# Runner builder: static legacy executors + Z+ window worker/publisher
# ---------------------------------------------------------------------------


def _make_runner(
    pool: asyncpg.Pool,
    *,
    window_executor: _StaticGrammarWindowExecutor,
) -> ReaderEnhancementPipelineRunner:
    """Build a pipeline runner with static legacy executors + Z+ window stack.

    Mirrors ``_make_runner`` in ``test_reader_orchestration_pipeline_runner.py``
    but adds ``grammar_window_worker_service`` and ``grammar_window_publisher``
    so the Z+ path is exercised end-to-end.

    T4.2a-R1: inject fake batch executors for translation / vocabulary /
    grammar batch paths so the runner never falls back to real LLM executors
    when ``enable_zplus_grammar=True`` (default). The BBC article exceeds
    6000 chars, so the T3.1 grouped path creates ``translate_article`` and
    ``build_vocabulary_layer_article`` batch jobs; without fake batch
    executors the batch workers would call real LLM.
    """
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
        # T4.2a-R1: safety net — GROUPED_WINDOWED articles should never
        # invoke the grammar batch path, but inject a fake executor so
        # any accidental batch-first fallback is caught deterministically
        # instead of calling real LLM.
        batch_executor=_StaticGrammarBatchExecutor(),
    )
    display_title_worker = DisplayTitleWorkerService(
        pool=pool,
        generator=_StaticTitleGenerator(),
    )
    window_worker = GrammarWindowWorkerService(
        pool=pool,
        executor=window_executor,
    )
    publisher = GrammarWindowPublisher(pool=pool)
    return ReaderEnhancementPipelineRunner(
        pool=pool,
        display_title_worker_service=display_title_worker,
        translation_orchestrator=orchestrator,
        translation_batch_worker_service=translation_worker,
        vocabulary_worker_service=vocabulary_worker,
        grammar_worker_service=grammar_worker,
        grammar_window_worker_service=window_worker,
        grammar_window_publisher=publisher,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_dedup_keys(rows: list[asyncpg.Record]) -> list[str]:
    """Extract semantic_dedup_key values from published layer quality_json.

    P2-1: semantic_dedup_key is now stored in quality_json (provenance),
    not output_json (which holds the GrammarNoteLayerOutput contract).
    """
    keys: list[str] = []
    for row in rows:
        quality = row["quality_json"]
        if isinstance(quality, str):
            quality = json.loads(quality)
        for item in quality.get("items", []):
            keys.append(str(item["semantic_dedup_key"]))
    return keys


# ---------------------------------------------------------------------------
# Synthetic expanded long-form regression test
# ---------------------------------------------------------------------------


async def test_synthetic_expanded_long_form_grammar_window_regression(
    bbc_regression_env: tuple[asyncpg.Pool, UUID, UUID, UUID],
) -> None:
    """Synthetic expanded long-form: Z+ window path grammar regression.

    Uses BBC article text repeated 3x (~2574 words, ~111 units) to route to
    GROUPED_WINDOWED. Verifies the Z+ window architecture produces correct
    grammar_note / sentence_analysis layers with no cross-window duplicates.

    This is NOT the original BBC 858-word 3-5 window regression. The original
    BBC sample now routes to SHORT_BATCH (T4.1c); a separate SHORT/STRUCTURED
    routing regression for the 858-word sample should be added if needed.

    Verifies:
    1. Window count (LLM call count) is 9-15 (expanded article)
    2. grammar_note total ≤ 18 (scaled budget for expanded article)
    3. sentence_analysis total ≤ 5 (scaled budget for expanded article)
    4. Per-unit at most 1 grammar_note + 1 sentence_analysis
    5. No cross-window semantic duplicates
    """
    pool, record_id, base_id, user_id = bbc_regression_env

    executor = _StaticGrammarWindowExecutor()
    runner = _make_runner(pool, window_executor=executor)

    summary = await runner.run(
        record_id=record_id,
        user_id=user_id,
        lease_owner="bbc-regression-zplus",
        lease_duration=LEASE_DURATION,
        # T4.2a-R1: expanded article (3x) creates more units/windows/jobs.
        max_ticks=600,
        max_jobs=400,
    )

    # Sanity: pipeline completed without attention.
    assert summary.stopped_reason in {
        "all_workers_no_job",
        "max_jobs_reached",
        "max_ticks_reached",
    }, f"Pipeline stopped unexpectedly: {summary.stopped_reason}"

    # 1. Window count (executor called once per window = one LLM call per window).
    assert_expected_window_count(executor.call_count)

    # Query DB for published layers.
    async with pool.acquire() as conn:
        grammar_layers = await conn.fetch(
            "SELECT target_key, output_json, quality_json FROM enhancement_layers "
            "WHERE reading_record_id = $1 AND layer_type = 'grammar_note' "
            "AND status = 'published'",
            record_id,
        )
        sentence_layers = await conn.fetch(
            "SELECT target_key, output_json, quality_json FROM enhancement_layers "
            "WHERE reading_record_id = $1 AND layer_type = 'sentence_analysis' "
            "AND status = 'published'",
            record_id,
        )

    # Published grammar_note layers > 0 (candidates actually accepted, not
    # all no_op). This guards against a trivially-passing test where the
    # executor produces candidates that all fail selector gates.
    assert len(grammar_layers) > 0, (
        "Expected >0 published grammar_note layers; "
        "executor candidates may be failing selector gates"
    )

    # 2. grammar_note total ≤ 14 (§7.3 record budget).
    assert_expected_grammar_note_total(len(grammar_layers))

    # 3. sentence_analysis total ≤ 3 (§7.3 record budget).
    assert_expected_sentence_analysis_total(len(sentence_layers))

    # 4. Per-unit constraint: at most 1 grammar_note + 1 sentence_analysis
    # per unit (target_key = unit_id).
    unit_grammar: dict[str, int] = {}
    for layer in grammar_layers:
        unit_id = str(layer["target_key"])
        unit_grammar[unit_id] = unit_grammar.get(unit_id, 0) + 1
    for unit_id, count in unit_grammar.items():
        assert count <= 1, (
            f"Unit {unit_id} has {count} grammar_note layers; expected ≤1"
        )

    unit_sentence: dict[str, int] = {}
    for layer in sentence_layers:
        unit_id = str(layer["target_key"])
        unit_sentence[unit_id] = unit_sentence.get(unit_id, 0) + 1
    for unit_id, count in unit_sentence.items():
        assert count <= 1, (
            f"Unit {unit_id} has {count} sentence_analysis layers; expected ≤1"
        )

    # 5. No cross-window semantic duplicates (DUP gate enforcement).
    grammar_keys = _extract_dedup_keys(grammar_layers)
    assert len(grammar_keys) == len(set(grammar_keys)), (
        f"Cross-window grammar_note semantic duplicates: {grammar_keys}"
    )

    sentence_keys = _extract_dedup_keys(sentence_layers)
    assert len(sentence_keys) == len(set(sentence_keys)), (
        f"Cross-window sentence_analysis semantic duplicates: {sentence_keys}"
    )
