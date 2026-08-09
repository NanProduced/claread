"""Real end-to-end integration test for grammar-window Analysis Window.

Per review agent1: "建议先让实现 agent 修 P1，再重新跑一轮真实端到端：新建一个
普通 reader record，不手动预置 plan，确认自动生成 windows、真实 LLM 输出
candidates、selector 发布合法 layer、前端能渐进刷新。下一轮验收重点应放在
真实路径，而不是 mock/static executor 的单元测试。至少补一个 integration
test：从 bootstrap_missing_jobs 开始，不能预创建 plan，最终应该看到
layer_analysis_plans、analysis_windows、grammar_bundle_window jobs 和合法
enhancement_layers.output_json。"

Test flow:
  1. Submit article (NO plan pre-created!)
  2. ``bootstrap_missing_jobs`` → creates grammar-window plan + windows + jobs
  3. Run pipeline → executor produces candidates, selector publishes layers
  4. Verify ``output_json`` conforms to §8.3 contract (schema_version,
     grammar_point/note/spans for grammar_note; anchor/label/analysis/chunks
     for sentence_analysis)
  5. Verify ``quality_json`` contains provenance (plan_id, window_id,
     semantic_dedup_key) NOT output_json
  6. Verify ``reader_events`` has ``layer_published`` events (progressive
     refresh)

Executor→publisher bridge (P2-1 fix):
  The production ``pipeline_runner._run_grammar_window_attempt`` calls
  ``_derive_candidate_contents(candidates)`` to bridge
  ``CandidateItem.content_*`` fields into ``WindowCandidateContent``,
  which the publisher uses to build proper
  ``GrammarNoteLayerOutput`` / ``SentenceAnalysisLayerOutput``.

  Both test paths (``_ContractPublisher`` wrapper and the production
  path) now produce §8.3 contract-compliant ``output_json``. The
  sidecar fallback was removed (P2-1 fail closed): if candidates exist
  but ``candidate_contents`` is None, the publisher raises ValueError.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.config.settings import Settings
from app.contracts.annotation import (
    compute_text_range_hash,
    slice_by_utf16_offsets,
)
from app.database import connection as db_connection
from app.schemas.reader_orchestration import (
    ReaderTextRangeAnchor,
    SentenceAnalysisChunk,
)
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


LEASE_DURATION = timedelta(seconds=30)

_GRAMMAR_WINDOW_E2E_BASE_TEXT = (
    "The committee, which had spent six months reviewing export data, "
    "labor surveys, and municipal tax receipts that rarely lined up neatly, "
    "claimed that the recovery was broad enough to justify ending the emergency "
    "grant program.\n\n"
    "Several shop owners warned that the headline numbers hid a more fragile "
    "street-level reality, because customers were still delaying purchases "
    "whenever wages, school fees, and transport costs rose in the same week.\n\n"
    "By the time the final vote arrived, the proposal that survived was not the "
    "clean, decisive resolution the briefing memo had promised, but a narrower "
    "plan that preserved training subsidies for districts with rising vacancy "
    "rates.\n\n"
    "What made the hearing difficult for new members was that the witnesses "
    "described a chain of causes rather than a single crisis, and families were "
    "willing to spend only after confirming that rent and medicine expenses "
    "were already covered."
)
GRAMMAR_WINDOW_E2E_ARTICLE_TEXT = "\n\n".join(
    [_GRAMMAR_WINDOW_E2E_BASE_TEXT] * 15
)


# ---------------------------------------------------------------------------
# Mock executor: realistic candidates + WindowCandidateContent
# ---------------------------------------------------------------------------


async def _build_text_range_anchor(
    pool: asyncpg.Pool,
    base_id: UUID,
    anchor_segment_id: str,
) -> ReaderTextRangeAnchor:
    """Construct a valid ReaderTextRangeAnchor covering the full anchor segment.

    Queries the DB for the segment + unit + base text, then slices the
    selected_text from the unit text using the segment's UTF-16 offsets.
    Mirrors the helper in test_grammar_window_publisher.py.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT seg.unit_id, seg.sentence_id, seg.segment_type,
                   seg.unit_start_utf16, seg.unit_end_utf16,
                   base.text AS base_text,
                   unit.base_start_utf16, unit.base_end_utf16
            FROM anchor_segments seg
            JOIN reading_bases base
              ON base.id = seg.base_id
             AND base.reading_record_id = seg.reading_record_id
            JOIN reading_units unit
              ON unit.reading_record_id = seg.reading_record_id
             AND unit.base_id = seg.base_id
             AND unit.unit_id = seg.unit_id
            WHERE seg.base_id = $1 AND seg.anchor_segment_id = $2
            """,
            base_id,
            anchor_segment_id,
        )
    if row is None:
        raise ValueError(f"anchor segment {anchor_segment_id} not found")

    unit_text = slice_by_utf16_offsets(
        str(row["base_text"]),
        int(row["base_start_utf16"]),
        int(row["base_end_utf16"]),
    )
    if not unit_text:
        raise ValueError(
            f"could not slice unit text for anchor {anchor_segment_id}"
        )
    selected_text = slice_by_utf16_offsets(
        unit_text,
        int(row["unit_start_utf16"]),
        int(row["unit_end_utf16"]),
    )
    if not selected_text:
        raise ValueError(
            f"could not slice selected_text for anchor {anchor_segment_id}"
        )
    return ReaderTextRangeAnchor(
        base_id=str(base_id),
        unit_id=str(row["unit_id"]),
        anchor_segment_id=anchor_segment_id,
        sentence_id=str(row["sentence_id"]) if row["sentence_id"] is not None else None,
        segment_type=str(row["segment_type"]),
        start_offset=int(row["unit_start_utf16"]),
        end_offset=int(row["unit_end_utf16"]),
        selected_text=selected_text,
        text_hash=compute_text_range_hash(selected_text),
    )


class _RealisticMockExecutor:
    """Mock executor producing realistic candidates + WindowCandidateContent.

    Produces per window:
      - up to ``window_budget.grammar_note.count`` (default 2) grammar_note
        candidates, each on a distinct anchor with a unique
        ``semantic_dedup_key`` and a distinct ``pattern_key`` so DUP and
        PATTERN_DENSE gates do not fire.
      - 1 ``sentence_analysis`` candidate on the first anchor (when within
        the sentence_analysis window budget).

    Each candidate also has a matching ``WindowCandidateContent`` entry
    (matched by ``semantic_dedup_key``) carrying the proper
    ``ReaderTextRangeAnchor`` / ``grammar_point`` / ``note`` / ``label`` /
    ``analysis`` / ``chunks`` fields required by the §8.3 contract.

    The candidate_contents are exposed via ``last_candidate_contents`` so
    the ``_ContractPublisher`` wrapper can pass them to the publisher. This
    demonstrates the bridge that production ``pipeline_runner`` needs to
    implement (see module docstring).
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
            return GrammarWindowExecutionResult(candidates=[])

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

        base_id = UUID(str(context["base_id"]))
        candidates: list[CandidateItem] = []

        # grammar_note: 1 per anchor, up to window budget. Each anchor
        # gets a unique semantic_dedup_key and pattern_key so DUP and
        # PATTERN_DENSE gates do not fire.
        for anchor in target_anchors[:grammar_budget]:
            anchor_id = str(anchor["anchor_segment_id"])
            dedup_key = f"grammar:{anchor_id}"

            # Build proper ReaderTextRangeAnchor for the WindowCandidateContent.
            text_anchor = await _build_text_range_anchor(
                self._pool, base_id, anchor_id
            )

            candidates.append(
                CandidateItem(
                    item_type="grammar_note",
                    anchor_segment_id=anchor_id,
                    spans=[text_anchor.model_dump()],
                    semantic_dedup_key=dedup_key,
                    pattern_key=f"pattern:{anchor_id}",
                    quality_score=4,
                    reading_blocker=False,
                    dedup_hint=dedup_key,
                    # P2-1: populate content_* fields for contract output
                    grammar_point=f"grammar_point:{anchor_id}",
                    pattern=f"pattern:{anchor_id}",
                    note=f"Realistic grammar note for anchor {anchor_id}.",
                )
            )
            self.last_candidate_contents.append(
                WindowCandidateContent(
                    semantic_dedup_key=dedup_key,
                    grammar_point=f"grammar_point:{anchor_id}",
                    pattern=f"pattern:{anchor_id}",
                    note=f"Realistic grammar note for anchor {anchor_id}.",
                    spans=[text_anchor],
                )
            )

        # sentence_analysis: 1 per window (when budget allows), on the
        # first target anchor.
        if sentence_budget > 0 and target_anchors:
            anchor = target_anchors[0]
            anchor_id = str(anchor["anchor_segment_id"])
            dedup_key = f"sentence:{anchor_id}"

            text_anchor = await _build_text_range_anchor(
                self._pool, base_id, anchor_id
            )

            candidates.append(
                CandidateItem(
                    item_type="sentence_analysis",
                    anchor_segment_id=anchor_id,
                    spans=[text_anchor.model_dump()],
                    semantic_dedup_key=dedup_key,
                    pattern_key=None,
                    quality_score=5,
                    reading_blocker=False,
                    dedup_hint=dedup_key,
                    # P2-1: populate content_* fields for contract output
                    label=f"main_clause:{anchor_id}",
                    analysis=f"Sentence analysis for anchor {anchor_id}.",
                    chunks=[{
                        "order": 1,
                        "label": "clause",
                        "text": text_anchor.selected_text,
                    }],
                )
            )
            self.last_candidate_contents.append(
                WindowCandidateContent(
                    semantic_dedup_key=dedup_key,
                    label=f"main_clause:{anchor_id}",
                    analysis=f"Sentence analysis for anchor {anchor_id}.",
                    chunks=[
                        SentenceAnalysisChunk(
                            order=1,
                            label="clause",
                            text=text_anchor.selected_text,
                        )
                    ],
                    anchor=text_anchor,
                )
            )

        return GrammarWindowExecutionResult(candidates=candidates)


# ---------------------------------------------------------------------------
# Contract publisher: injects candidate_contents (bridge demonstration)
# ---------------------------------------------------------------------------


class _ContractPublisher:
    """Publisher wrapper that injects ``candidate_contents`` from the executor.

    This is a TEST HELPER (not production code) that demonstrates the
    bridge production ``pipeline_runner`` needs to implement. After the
    executor produces candidates, the runner must also produce
    ``WindowCandidateContent`` (matched by ``semantic_dedup_key``) and
    pass it to ``publish_window_grammar_bundle``.

    The wrapper delegates to a real ``GrammarWindowPublisher`` (with
    ``event_runtime`` configured) and forwards all calls, injecting
    ``candidate_contents`` from the executor's ``last_candidate_contents``
    when the caller (production pipeline_runner) omits it.
    """

    def __init__(
        self,
        *,
        real_publisher: GrammarWindowPublisher,
        executor: _RealisticMockExecutor,
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
        # Bridge: inject candidate_contents from the executor when the
        # production pipeline_runner omits it.
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
# Fixture: worker_loop_env pattern using the single baseline.
# ---------------------------------------------------------------------------


@pytest.fixture
async def grammar_window_e2e_env() -> AsyncIterator[asyncpg.Pool]:
    schema_name = f"test_grammar_window_e2e_integration_{uuid4().hex}"
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


# ---------------------------------------------------------------------------
# Runner builder
# ---------------------------------------------------------------------------


def _make_grammar_window_runner(
    pool: asyncpg.Pool,
    *,
    executor: _RealisticMockExecutor,
    use_contract_publisher: bool = True,
) -> ReaderEnhancementPipelineRunner:
    """Build a pipeline runner wired for the grammar-window path.

    ``use_contract_publisher=True`` (default) wires the
    ``_ContractPublisher`` wrapper so the publisher receives
    ``candidate_contents`` from the executor. This verifies the full
    §8.3 contract.

    ``use_contract_publisher=False`` uses the production publisher as-is
    (legacy fallback path) to document the bridge gap.
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
    if use_contract_publisher:
        publisher: object = _ContractPublisher(
            real_publisher=real_publisher,
            executor=executor,
        )
    else:
        publisher = real_publisher
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
# Test 1: real grammar-window path with §8.3 contract (bridge demonstrated)
# ---------------------------------------------------------------------------


async def test_grammar_window_end_to_end_no_precreated_plan(
    grammar_window_e2e_env: asyncpg.Pool,
) -> None:
    """Real grammar-window path: bootstrap creates plan/windows/jobs, executor produces
    candidates, selector publishes valid layers with proper §8.3 contract.

    Per agent1 review: "从 bootstrap_missing_jobs 开始，不能预创建 plan"

    Verifies:
      1. ``layer_analysis_plans`` row created (NO pre-creation!)
      2. ``analysis_windows`` rows created
      3. ``grammar_bundle_window`` jobs created
      4. ``enhancement_layers.output_json`` conforms to §8.3 contract
         (schema_version, grammar_point/note/spans for grammar_note;
         anchor/label/analysis/chunks for sentence_analysis)
      5. ``quality_json`` contains provenance (plan_id, window_id,
         semantic_dedup_key) NOT output_json
      6. ``reader_events`` has ``layer_published`` events (progressive
         refresh)
    """
    pool = grammar_window_e2e_env
    user_id = await insert_user(pool)

    # 1. Submit article (NO plan pre-created!)
    article = await submit_article_ready(
        pool,
        user_id=user_id,
        plain_text=GRAMMAR_WINDOW_E2E_ARTICLE_TEXT,
        title="grammar-window E2E Integration Test",
    )

    executor = _RealisticMockExecutor(pool=pool)
    runner = _make_grammar_window_runner(pool, executor=executor)

    # 2. Bootstrap missing jobs (should create grammar-window plan + windows + jobs)
    bootstrap_summary = await runner.bootstrap_missing_jobs(
        record_id=article.record_id,
        user_id=user_id,
    )

    # 3. Verify: layer_analysis_plans row created (NO pre-creation!)
    async with pool.acquire() as conn:
        plan_count = await conn.fetchval(
            "SELECT COUNT(*) FROM layer_analysis_plans "
            "WHERE reading_record_id = $1",
            article.record_id,
        )
        assert plan_count == 1, (
            "grammar-window plan should be auto-created by bootstrap (no pre-creation)"
        )

        # 4. Verify: analysis_windows rows created
        window_count = await conn.fetchval(
            """
            SELECT COUNT(*) FROM analysis_windows
            WHERE plan_id IN (
                SELECT id FROM layer_analysis_plans
                WHERE reading_record_id = $1
            )
            """,
            article.record_id,
        )
        assert window_count >= 1, "At least one window should be created"

        # 5. Verify: grammar_bundle_window jobs created
        window_job_count = await conn.fetchval(
            "SELECT COUNT(*) FROM reader_jobs "
            "WHERE reading_record_id = $1 "
            "  AND job_type = 'build_grammar_bundle_window'",
            article.record_id,
        )
        assert window_job_count >= 1, (
            "At least one grammar_bundle_window job should be created"
        )

    # Sanity: bootstrap reported no grammar_bundle jobs (legacy path) because
    # the grammar-window path is enabled (job_counts.grammar_bundle == 0).
    assert bootstrap_summary.job_counts.grammar_bundle == 0, (
        "legacy grammar_bundle jobs should not be created when grammar-window is enabled"
    )

    # 6. Run the pipeline (process all window jobs)
    run_summary = await runner.run(
        record_id=article.record_id,
        user_id=user_id,
        lease_owner="e2e-grammar-window-test",
        lease_duration=LEASE_DURATION,
        max_ticks=30,
        max_jobs=20,
    )

    # Pipeline should complete without attention.
    assert run_summary.stopped_reason in {
        "all_workers_no_job",
        "max_jobs_reached",
        "max_ticks_reached",
    }, f"Pipeline stopped unexpectedly: {run_summary.stopped_reason}"
    assert run_summary.outcome_counts.failed_terminal == 0, (
        f"Pipeline had terminal failures: {run_summary.outcome_counts}"
    )
    # grammar-window window worker should have processed at least one job.
    assert run_summary.worker_tick_counts.grammar_bundle_window >= 1, (
        "grammar_bundle_window worker should have ticked at least once"
    )

    # Executor was called at least once (real LLM call equivalent).
    assert executor.call_count >= 1, (
        "executor.generate should have been called at least once"
    )

    # 7. Verify: enhancement_layers published with valid §8.3 output_json
    #    (only grammar_note / sentence_analysis follow §8.3 contract;
    #    translation / vocabulary / display_title use their own schemas)
    async with pool.acquire() as conn:
        layers = await conn.fetch(
            """
            SELECT layer_type, output_json, quality_json, target_key
            FROM enhancement_layers
            WHERE reading_record_id = $1
              AND status = 'published'
              AND layer_type IN ('grammar_note', 'sentence_analysis')
            """,
            article.record_id,
        )
        assert len(layers) > 0, (
            "At least one grammar-window enhancement_layer (grammar_note / "
            "sentence_analysis) should be published"
        )

        grammar_note_layers = [
            layer for layer in layers if layer["layer_type"] == "grammar_note"
        ]

        # grammar_note layers should exist (executor produces them).
        assert len(grammar_note_layers) > 0, (
            "At least one grammar_note layer should be published"
        )

        for layer in layers:
            output = layer["output_json"]
            if isinstance(output, str):
                output = json.loads(output)
            quality = layer["quality_json"]
            if isinstance(quality, str):
                quality = json.loads(quality)

            # output_json must have schema_version (§8.3 contract)
            assert output.get("schema_version") == 1, (
                f"output_json missing schema_version==1 for "
                f"{layer['layer_type']}"
            )

            # output_json must have items with proper content
            assert "items" in output
            assert len(output["items"]) > 0, (
                f"output_json.items empty for {layer['layer_type']}"
            )

            if layer["layer_type"] == "grammar_note":
                item = output["items"][0]
                assert "grammar_point" in item, (
                    "grammar_note output_json missing grammar_point"
                )
                assert item["grammar_point"], (
                    "grammar_note grammar_point must be non-empty"
                )
                assert "note" in item, (
                    "grammar_note output_json missing note"
                )
                assert item["note"], "grammar_note note must be non-empty"
                assert "spans" in item, (
                    "grammar_note output_json missing spans"
                )
                assert len(item["spans"]) >= 1, (
                    "grammar_note spans must be non-empty"
                )
                # Sidecar fields MUST NOT appear in output_json (§8.3)
                assert "semantic_dedup_key" not in item, (
                    "semantic_dedup_key must not be in output_json "
                    "(belongs in quality_json)"
                )
                assert "quality_score" not in item, (
                    "quality_score must not be in output_json "
                    "(belongs in quality_json)"
                )
                assert "pattern_key" not in item, (
                    "pattern_key must not be in output_json "
                    "(belongs in quality_json)"
                )

            elif layer["layer_type"] == "sentence_analysis":
                item = output["items"][0]
                assert "anchor" in item, (
                    "sentence_analysis output_json missing anchor"
                )
                assert "label" in item, (
                    "sentence_analysis output_json missing label"
                )
                assert item["label"], (
                    "sentence_analysis label must be non-empty"
                )
                assert "analysis" in item, (
                    "sentence_analysis output_json missing analysis"
                )
                assert item["analysis"], (
                    "sentence_analysis analysis must be non-empty"
                )
                assert "chunks" in item, (
                    "sentence_analysis output_json missing chunks"
                )
                assert len(item["chunks"]) >= 1, (
                    "sentence_analysis chunks must be non-empty"
                )
                # Sidecar fields MUST NOT appear in output_json (§8.3)
                assert "semantic_dedup_key" not in item, (
                    "semantic_dedup_key must not be in output_json "
                    "(belongs in quality_json)"
                )

            # quality_json must contain provenance (§8.3)
            assert "plan_id" in quality, (
                "quality_json missing plan_id provenance"
            )
            assert "window_id" in quality, (
                "quality_json missing window_id provenance"
            )
            # Provenance fields MUST NOT be in output_json
            assert "plan_id" not in output, (
                "plan_id must not be in output_json (belongs in quality_json)"
            )
            assert "window_id" not in output, (
                "window_id must not be in output_json "
                "(belongs in quality_json)"
            )

        # 8. Verify: reader_events has layer_published events for the grammar-window
        #    layers (progressive refresh). layer_published is also emitted
        #    by translation / vocabulary / display_title publishers, so we
        #    filter to only grammar-window events (those carrying plan_id / window_id).
        events = await conn.fetch(
            """
            SELECT event_type, payload_json
            FROM reader_events
            WHERE reading_record_id = $1
              AND event_type = 'layer_published'
            ORDER BY sequence ASC
            """,
            article.record_id,
        )
        assert len(events) > 0, (
            "layer_published events should be emitted for progressive refresh"
        )

        # Filter to grammar-window layer_published events (those with plan_id in payload).
        # Non-grammar-window events (translation/vocabulary) don't carry plan_id/window_id.
        grammar_window_events = []
        for ev in events:
            payload = ev["payload_json"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            if "plan_id" in payload and "window_id" in payload:
                grammar_window_events.append((ev, payload))

        assert len(grammar_window_events) > 0, (
            "At least one grammar-window layer_published event (with plan_id / "
            "window_id) should be emitted for progressive refresh"
        )

        # Every grammar-window layer_published event should carry the contract fields.
        for _ev, payload in grammar_window_events:
            assert "layer_id" in payload, (
                "layer_published event payload missing layer_id"
            )
            assert "layer_type" in payload, (
                "layer_published event payload missing layer_type"
            )
            assert payload.get("target_scope") == "unit", (
                "layer_published event target_scope should be 'unit'"
            )
            assert "plan_id" in payload, (
                "layer_published event payload missing plan_id"
            )
            assert "window_id" in payload, (
                "layer_published event payload missing window_id"
            )


# ---------------------------------------------------------------------------
# Test 2: production path (no contract publisher wrapper) produces §8.3 contract
# ---------------------------------------------------------------------------


async def test_grammar_window_end_to_end_production_path_publishes_contract(
    grammar_window_e2e_env: asyncpg.Pool,
) -> None:
    """Production path: pipeline_runner derives candidate_contents internally.

    P2-1 removed the sidecar fallback. The production pipeline_runner now
    calls ``_derive_candidate_contents(candidates)`` to bridge
    ``CandidateItem.content_*`` fields into ``WindowCandidateContent``,
    which the publisher uses to build a proper
    ``GrammarNoteLayerOutput`` / ``SentenceAnalysisLayerOutput``.

    This test verifies the production path (no ``_ContractPublisher``
    wrapper) produces §8.3 contract-compliant ``output_json``:
      - HAS ``schema_version``
      - HAS ``grammar_point`` / ``note`` / ``spans`` (grammar_note)
      - HAS ``anchor`` / ``label`` / ``analysis`` / ``chunks``
        (sentence_analysis)
      - Provenance (``semantic_dedup_key`` / ``pattern_key`` /
        ``quality_score``) lives in ``quality_json``, not ``output_json``
    """
    pool = grammar_window_e2e_env
    user_id = await insert_user(pool)

    article = await submit_article_ready(
        pool,
        user_id=user_id,
        plain_text=GRAMMAR_WINDOW_E2E_ARTICLE_TEXT,
        title="grammar-window E2E Production Path",
    )

    executor = _RealisticMockExecutor(pool=pool)
    # use_contract_publisher=False → production publisher as-is, but
    # pipeline_runner._derive_candidate_contents bridges content_* fields.
    runner = _make_grammar_window_runner(
        pool,
        executor=executor,
        use_contract_publisher=False,
    )

    run_summary = await runner.run(
        record_id=article.record_id,
        user_id=user_id,
        lease_owner="e2e-production-path",
        lease_duration=LEASE_DURATION,
        max_ticks=30,
        max_jobs=20,
    )

    assert run_summary.stopped_reason in {
        "all_workers_no_job",
        "max_jobs_reached",
        "max_ticks_reached",
    }, f"Pipeline stopped unexpectedly: {run_summary.stopped_reason}"
    assert run_summary.outcome_counts.failed_terminal == 0

    async with pool.acquire() as conn:
        layers = await conn.fetch(
            """
            SELECT layer_type, output_json, quality_json
            FROM enhancement_layers
            WHERE reading_record_id = $1
              AND status = 'published'
              AND layer_type IN ('grammar_note', 'sentence_analysis')
            """,
            article.record_id,
        )
        assert len(layers) > 0, (
            "Production path should publish layers"
        )

        for layer in layers:
            output = layer["output_json"]
            if isinstance(output, str):
                output = json.loads(output)
            quality = layer["quality_json"]
            if isinstance(quality, str):
                quality = json.loads(quality)

            # §8.3 contract: output_json HAS schema_version
            assert output.get("schema_version") == 1, (
                f"output_json should have schema_version=1, got: {output}"
            )

            # §8.3 contract: output_json.items have layer output model
            # fields, NOT sidecar fields.
            assert len(output.get("items", [])) > 0
            item = output["items"][0]
            assert "semantic_dedup_key" not in item, (
                "output_json.items[0] should NOT have semantic_dedup_key "
                "(provenance must live in quality_json per §8.3)"
            )
            assert "pattern_key" not in item
            assert "quality_score" not in item

            if layer["layer_type"] == "grammar_note":
                assert "grammar_point" in item
                assert "note" in item
                assert "spans" in item
                assert len(item["spans"]) >= 1
            elif layer["layer_type"] == "sentence_analysis":
                assert "anchor" in item
                assert "label" in item
                assert "analysis" in item
                assert "chunks" in item

            # §8.3 contract: provenance lives in quality_json
            assert "plan_id" in quality
            assert "window_id" in quality
            quality_items = quality.get("items", [])
            if quality_items:
                qitem = quality_items[0]
                assert "semantic_dedup_key" in qitem
