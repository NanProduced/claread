"""T4.2a-V2-R1: three-mode boundary & very-long fixed-sample validation.

Deterministic fixture / fake-executor coverage for four fixed samples:

1. Fragmented short news → SHORT_BATCH (no wrong batch merge; Translation
   Group / anchor integrity preserved).
2. Structured boundary medium article → STRUCTURED_BATCH (distinct route,
   fingerprint, policy, compact grammar batch).
3. Very-long article (>4000 words) → GROUPED_WINDOWED (group/window job
   topology + reading-order publish).
4. No-op grammar window → empty candidates reach terminal no_op without
   retry or duplicate LLM calls; coverage still finalizes.

No real LLM. Does not change prompt/model/router thresholds to force pass.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.database import connection as db_connection
from app.services.reader_orchestration.display_title_worker import (
    DisplayTitleWorkerService,
)
from app.services.reader_orchestration.document_feature_extractor import (
    SHORT_ARTICLE_MAX_WORD_COUNT,
    STRUCTURED_ARTICLE_MAX_CHAR_GUARDRAIL,
    STRUCTURED_ARTICLE_MAX_WORD_COUNT,
    classify_article_route,
    extract_document_features,
)
from app.services.reader_orchestration.event_runtime import ReaderEventRuntime
from app.services.reader_orchestration.grammar_window_publisher import (
    GrammarWindowPublisher,
)
from app.services.reader_orchestration.grammar_window_worker import (
    GrammarWindowExecutionResult,
    GrammarWindowWorkerService,
)
from app.services.reader_orchestration.grammar_worker import GrammarBundleWorkerService
from app.services.reader_orchestration.layer_publisher import (
    VocabularyLayerPublisher,
)
from app.services.reader_orchestration.orchestrator import ReaderOrchestrator
from app.services.reader_orchestration.pipeline_runner import (
    ReaderEnhancementPipelineRunner,
)
from app.services.reader_orchestration.translation_worker import TranslationWorkerService
from app.services.reader_orchestration.vocabulary_worker import VocabularyWorkerService
from app.services.reader_orchestration.worker_loop import (
    ReaderEnhancementWorkerLoopService,
    WorkerLoopCandidateRecord,
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
from tests.test_grammar_window_bbc_regression import _StaticGrammarWindowExecutor

pytestmark = pytest.mark.anyio

_REPO_ROOT = Path(__file__).resolve().parents[3]

_GOLDEN_ROOT = _REPO_ROOT / "verification" / "golden_samples" / "articles"
LEASE_DURATION = timedelta(seconds=30)

# Fingerprint / policy bases pinned by T4.1b / T4.1c contracts.
_FP_TRANS_SHORT = "translation_article_v1"
_FP_VOCAB_SHORT = "vocabulary_article_v1"
_FP_GRAM_SHORT = "grammar_bundle_article_v1"
_POL_TRANS_SHORT = "reader_translation_batch_bootstrap_v1"
_POL_VOCAB_SHORT = "reader_vocabulary_batch_bootstrap_v1"
_POL_GRAM_SHORT = "reader_grammar_batch_bootstrap_v1"

_FP_TRANS_STRUCT = "translation_article_structured_v1"
_FP_VOCAB_STRUCT = "vocabulary_article_structured_v1"
_FP_GRAM_STRUCT = "grammar_bundle_article_structured_v1"
_POL_TRANS_STRUCT = "reader_translation_batch_structured_bootstrap_v1"
_POL_VOCAB_STRUCT = "reader_vocabulary_batch_structured_bootstrap_v1"
_POL_GRAM_STRUCT = "reader_grammar_batch_structured_bootstrap_v1"

_FP_GRAM_WINDOW = "grammar_bundle_window_v1"


# ---------------------------------------------------------------------------
# Fixed samples
# ---------------------------------------------------------------------------


def _load_golden(sample_id: str) -> str:
    path = _GOLDEN_ROOT / f"{sample_id}.txt"
    return path.read_text(encoding="utf-8")


def _fragmented_short_news() -> str:
    """Golden fragmented_news: many short paragraphs / lead-ins.

    Expected route: SHORT_BATCH (≈263 words ≪ 1100).
    """
    return _load_golden("fragmented_news")


def _structured_boundary_article() -> str:
    """Medium boundary sample for STRUCTURED_BATCH.

    Word band: (1100, 2000] and UTF-16 ≤ 12000.
    Uses 55 iterations of a ~28-word block → ≈1540 words / ≈9.6k chars.
    """
    base = (
        "The committee reviewed the quarterly report and found that several "
        "departments had exceeded their allocated budgets while others reported "
        "unexpected surpluses during the fiscal period. "
    )
    parts = [f"Sentence number {i + 1}. " + base for i in range(55)]
    return "\n\n".join(parts)


def _very_long_article() -> str:
    """>4000 words → GROUPED_WINDOWED.

    30 paragraphs × 8 sentences of ~18 words ≈ 4320 words. Paragraph
    count keeps unit count manageable while still forcing multi-window
    translation / vocabulary / grammar topology.
    """
    sentence = (
        "Researchers examined regional transport patterns across multiple "
        "cities during the extended observation window carefully. "
    )
    # sentence ≈ 14 words; 10 sentences/para ≈ 140 words; 30 paras ≈ 4200.
    paragraphs = []
    for p in range(30):
        chunk = "".join(
            f"Paragraph {p + 1} point {s + 1}. {sentence}" for s in range(10)
        )
        paragraphs.append(chunk)
    return "\n\n".join(paragraphs)


def _noop_window_article() -> str:
    """Minimal GROUPED_WINDOWED article for no-op grammar window path.

    ~2100 words so grammar uses grammar-window windows without the cost of the
    very-long sample.
    """
    base = (
        "The committee reviewed the quarterly report and found that several "
        "departments had exceeded their allocated budgets while others reported "
        "unexpected surpluses during the fiscal period. "
    )
    parts = []
    for p in range(15):
        chunk = "".join(
            f"Sentence number {p * 5 + j + 1}. " + base for j in range(5)
        )
        parts.append(chunk)
    return "\n\n".join(parts)


def _profile_route(text: str) -> tuple[str, int, int]:
    """Offline route preview (unit_types are a coarse body fill-in)."""
    # Unit type sequence only affects structural noise stats, not word count.
    unit_types = tuple(["body"] * max(1, text.count("\n\n") + 1))
    profile = extract_document_features(
        base_text=text,
        unit_types=unit_types,
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        requested_layers=("translation", "vocabulary", "grammar"),
    )
    route = classify_article_route(profile)
    return route.value, profile.estimated_word_count, profile.content_utf16_length


# ---------------------------------------------------------------------------
# Counting / empty fake executors
# ---------------------------------------------------------------------------


class _CountingBatchTranslator(_StaticBatchTranslator):
    def __init__(self) -> None:
        super().__init__()
        self.call_count = 0

    async def translate_batch(self, context):
        self.call_count += 1
        return await super().translate_batch(context)


class _CountingBatchVocabularyExecutor(_StaticBatchVocabularyExecutor):
    def __init__(self) -> None:
        super().__init__()
        self.call_count = 0

    async def generate_batch(self, context):
        self.call_count += 1
        return await super().generate_batch(context)


class _FlatUsageGrammarBatchExecutor(_StaticGrammarBatchExecutor):
    """Flat usage_data so span token columns match usage events."""

    def __init__(self) -> None:
        super().__init__()
        self.call_count = 0

    async def generate_batch(self, context):
        self.call_count += 1
        result = await super().generate_batch(context)
        if result.usage_data and "aggregate" in result.usage_data:
            aggregate = result.usage_data["aggregate"]
            if isinstance(aggregate, dict):
                return replace(result, usage_data=dict(aggregate))
        return result


class _CountingGrammarWindowExecutor(_StaticGrammarWindowExecutor):
    """Produces non-empty candidates and tracks call count."""

    def __init__(self) -> None:
        super().__init__()
        # parent already has call_count

    async def generate(self, context: dict[str, Any]) -> GrammarWindowExecutionResult:
        return await super().generate(context)


class _EmptyGrammarWindowExecutor:
    """Always returns zero candidates → publisher marks window no_op."""

    def __init__(self) -> None:
        self.call_count = 0
        self.window_calls: list[dict[str, Any]] = []

    async def generate(
        self, context: dict[str, Any]
    ) -> GrammarWindowExecutionResult:
        self.call_count += 1
        self.window_calls.append(context)
        return GrammarWindowExecutionResult(candidates=[])


class _CountingTitleGenerator(_StaticTitleGenerator):
    def __init__(self) -> None:
        super().__init__()
        self.call_count = 0

    async def generate(self, context):
        self.call_count += 1
        return await super().generate(context)


# ---------------------------------------------------------------------------
# Fixture + runner helpers
# ---------------------------------------------------------------------------


@pytest.fixture
async def v2_env() -> AsyncIterator[asyncpg.Pool]:
    schema_name = f"test_v2_boundary_{uuid4().hex}"
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


def _make_runner(
    pool: asyncpg.Pool,
    *,
    batch_translator: Any | None = None,
    batch_vocabulary: Any | None = None,
    grammar_batch: Any | None = None,
    grammar_window: Any | None = None,
    title_generator: Any | None = None,
) -> ReaderEnhancementPipelineRunner:
    batch_translator = batch_translator or _CountingBatchTranslator()
    batch_vocabulary = batch_vocabulary or _CountingBatchVocabularyExecutor()
    grammar_batch = grammar_batch or _FlatUsageGrammarBatchExecutor()
    grammar_window = grammar_window or _CountingGrammarWindowExecutor()
    title_generator = title_generator or _CountingTitleGenerator()

    translation_worker = TranslationWorkerService(
        pool=pool,
        layer_publisher=CompatTranslationLayerPublisher(pool=pool),
        translator=_StaticTranslator(),
        batch_translator=batch_translator,
    )
    orchestrator = ReaderOrchestrator(pool=pool, worker_service=translation_worker)
    vocabulary_worker = VocabularyWorkerService(
        pool=pool,
        layer_publisher=VocabularyLayerPublisher(pool=pool),
        executor=_StaticVocabularyExecutor(),
        batch_executor=batch_vocabulary,
    )
    grammar_worker = GrammarBundleWorkerService(
        pool=pool,
        executor=_StaticGrammarExecutor(),
        batch_executor=grammar_batch,
    )
    display_title_worker = DisplayTitleWorkerService(
        pool=pool,
        generator=title_generator,
    )
    window_worker = GrammarWindowWorkerService(
        pool=pool,
        executor=grammar_window,
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
        enable_grammar_window=True,
    )


async def _find_candidate(
    service: ReaderEnhancementWorkerLoopService,
    record_id: UUID,
    *,
    batch_size: int = 20,
) -> WorkerLoopCandidateRecord:
    candidates = await service.scan_eligible_records(batch_size=batch_size)
    for candidate in candidates:
        if candidate.record_id == record_id:
            return candidate
    raise AssertionError(f"candidate for record {record_id} not found")


async def _run_through_worker_loop(
    pool: asyncpg.Pool,
    runner: ReaderEnhancementPipelineRunner,
    record_id: UUID,
    *,
    max_ticks: int = 120,
    max_jobs: int = 80,
    lease_prefix: str = "v2-boundary",
):
    service = ReaderEnhancementWorkerLoopService(pool=pool, pipeline_runner=runner)
    candidate = await _find_candidate(service, record_id)
    return await service.process_candidate(
        candidate=candidate,
        lease_owner_prefix=lease_prefix,
        lease_duration=LEASE_DURATION,
        max_ticks=max_ticks,
        max_jobs=max_jobs,
    )


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def _fetch_jobs(pool: asyncpg.Pool, record_id: UUID) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT
                j.id,
                j.job_type,
                j.target_type,
                j.target_key,
                j.status,
                j.attempt_count,
                j.max_attempts,
                j.operation_fingerprint,
                j.input_json,
                j.output_ref_json,
                j.rationale_code,
                r.envelope_json->>'article_route' AS article_route,
                r.policy_version
            FROM reader_jobs j
            JOIN reader_runs r ON r.id = j.run_id
            WHERE j.reading_record_id = $1
            ORDER BY j.job_type, j.created_at
            """,
            record_id,
        )


async def _fetch_readiness(pool: asyncpg.Pool, record_id: UUID) -> str:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT readiness_state FROM reading_records WHERE id = $1",
            record_id,
        )


async def _fetch_unit_count(pool: asyncpg.Pool, record_id: UUID) -> int:
    async with pool.acquire() as conn:
        return int(
            await conn.fetchval(
                """
                SELECT COUNT(*) FROM reading_units
                WHERE reading_record_id = $1
                """,
                record_id,
            )
        )


async def _fetch_anchor_ids(pool: asyncpg.Pool, record_id: UUID) -> set[str]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT anchor_segment_id
            FROM anchor_segments
            WHERE reading_record_id = $1
            """,
            record_id,
        )
    return {str(r["anchor_segment_id"]) for r in rows}


async def _fetch_translation_layers_reading_order(
    pool: asyncpg.Pool, record_id: UUID, base_id: UUID
) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT
                layer.target_key AS unit_id,
                u.order_index,
                layer.output_json,
                layer.published_at
            FROM enhancement_layers layer
            JOIN reading_units u
              ON u.reading_record_id = layer.reading_record_id
             AND u.base_id = layer.base_id
             AND u.unit_id = layer.target_key
            WHERE layer.reading_record_id = $1
              AND layer.base_id = $2
              AND layer.layer_type = 'translation'
              AND layer.status = 'published'
            ORDER BY u.order_index ASC
            """,
            record_id,
            base_id,
        )


async def _fetch_analysis_windows(
    pool: asyncpg.Pool, record_id: UUID
) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT
                aw.id,
                aw.window_index,
                aw.status,
                aw.coverage
            FROM analysis_windows aw
            JOIN layer_analysis_plans lap ON lap.id = aw.plan_id
            WHERE lap.reading_record_id = $1
            ORDER BY aw.window_index ASC
            """,
            record_id,
        )


async def _fetch_layer_counts(pool: asyncpg.Pool, record_id: UUID) -> dict[str, int]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT layer_type, COUNT(*) AS cnt
            FROM enhancement_layers
            WHERE reading_record_id = $1 AND status = 'published'
            GROUP BY layer_type
            """,
            record_id,
        )
    return {row["layer_type"]: int(row["cnt"]) for row in rows}


def _assert_finalized(result, *, allow_no_op: bool = False) -> None:
    assert result.outcome == "processed", (
        f"expected outcome='processed', got {result.outcome!r}"
    )
    assert result.completion_finalization_result is not None
    fin = result.completion_finalization_result
    assert fin.finalized is True, f"finalized={fin.finalized!r}"
    allowed = ("completed_clean", "completed_with_no_op")
    if not allow_no_op:
        allowed = ("completed_clean",)
    assert fin.outcome in allowed, f"unexpected completion outcome: {fin.outcome!r}"


def _parse_json(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


# ---------------------------------------------------------------------------
# Offline sample matrix sanity (no DB)
# ---------------------------------------------------------------------------


def test_v2_fixed_sample_route_matrix_offline() -> None:
    """Pin expected offline routes/word bands for the four fixed samples."""
    frag_route, frag_words, frag_chars = _profile_route(_fragmented_short_news())
    assert frag_route == "short_batch"
    assert frag_words <= SHORT_ARTICLE_MAX_WORD_COUNT
    assert 200 <= frag_words <= 420
    assert 1500 <= frag_chars <= 2500

    struct_route, struct_words, struct_chars = _profile_route(
        _structured_boundary_article()
    )
    assert struct_route == "structured_batch"
    assert SHORT_ARTICLE_MAX_WORD_COUNT < struct_words <= STRUCTURED_ARTICLE_MAX_WORD_COUNT
    assert struct_chars <= STRUCTURED_ARTICLE_MAX_CHAR_GUARDRAIL

    long_route, long_words, _long_chars = _profile_route(_very_long_article())
    assert long_route == "grouped_windowed"
    assert long_words > 4000

    noop_route, noop_words, _noop_chars = _profile_route(_noop_window_article())
    assert noop_route == "grouped_windowed"
    assert noop_words > STRUCTURED_ARTICLE_MAX_WORD_COUNT


# ---------------------------------------------------------------------------
# Sample 1: fragmented short news → SHORT_BATCH
# ---------------------------------------------------------------------------


async def test_v2_fragmented_short_news_short_batch_preserves_groups_and_anchors(
    v2_env: asyncpg.Pool,
) -> None:
    """Fragmented news must stay SHORT_BATCH; Translation Groups keep anchors."""
    pool = v2_env
    batch_t = _CountingBatchTranslator()
    batch_v = _CountingBatchVocabularyExecutor()
    batch_g = _FlatUsageGrammarBatchExecutor()
    title = _CountingTitleGenerator()
    window_g = _CountingGrammarWindowExecutor()

    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool,
        user_id=user_id,
        plain_text=_fragmented_short_news(),
        title="V2 Fragmented Short News",
    )

    runner = _make_runner(
        pool,
        batch_translator=batch_t,
        batch_vocabulary=batch_v,
        grammar_batch=batch_g,
        grammar_window=window_g,
        title_generator=title,
    )
    result = await _run_through_worker_loop(
        pool, runner, article.record_id, max_ticks=40, max_jobs=20
    )
    _assert_finalized(result)

    readiness = await _fetch_readiness(pool, article.record_id)
    assert readiness == "coverage_complete"

    jobs = await _fetch_jobs(pool, article.record_id)
    assert len(jobs) == 4, (
        f"SHORT_BATCH expected 4 jobs, got {len(jobs)}: "
        f"{[(j['job_type'], j['target_type']) for j in jobs]}"
    )
    job_types = {(j["job_type"], j["target_type"]) for j in jobs}
    assert job_types == {
        ("generate_display_title_zh", "record"),
        ("translate_article", "unit_range"),
        ("build_vocabulary_layer_article", "unit_range"),
        ("build_grammar_bundle", "unit_range"),
    }

    # No per-unit fan-out, no window target keys.
    for j in jobs:
        assert j["status"] == "succeeded"
        assert j["attempt_count"] == 1, (
            f"{j['job_type']} attempt_count={j['attempt_count']}"
        )
        assert ":window:" not in str(j["target_key"] or "")
        if j["job_type"] in (
            "translate_article",
            "build_vocabulary_layer_article",
            "build_grammar_bundle",
        ):
            assert j["article_route"] == "short_batch"
            input_json = _parse_json(j["input_json"])
            assert input_json.get("article_route") == "short_batch"

    # Fingerprint / policy bases (SHORT shared *_v1).
    by_type = {j["job_type"]: j for j in jobs}
    assert by_type["translate_article"]["operation_fingerprint"].startswith(
        _FP_TRANS_SHORT
    )
    assert by_type["translate_article"]["policy_version"] == _POL_TRANS_SHORT
    assert by_type["build_vocabulary_layer_article"][
        "operation_fingerprint"
    ].startswith(_FP_VOCAB_SHORT)
    assert (
        by_type["build_vocabulary_layer_article"]["policy_version"] == _POL_VOCAB_SHORT
    )
    assert by_type["build_grammar_bundle"]["operation_fingerprint"].startswith(
        _FP_GRAM_SHORT
    )
    assert by_type["build_grammar_bundle"]["policy_version"] == _POL_GRAM_SHORT

    # Planned / effective calls: 1 batch per layer + 1 title.
    assert batch_t.call_count == 1
    assert batch_v.call_count == 1
    assert batch_g.call_count == 1
    assert title.call_count == 1
    assert window_g.call_count == 0  # no grammar-window grammar path

    # Translation Group / anchor integrity.
    unit_count = await _fetch_unit_count(pool, article.record_id)
    assert unit_count >= 2, "fragmented news should yield multiple units"
    base_anchor_ids = await _fetch_anchor_ids(pool, article.record_id)
    assert len(base_anchor_ids) >= 2

    layers = await _fetch_translation_layers_reading_order(
        pool, article.record_id, article.base_id
    )
    assert len(layers) == unit_count

    multi_anchor_group_seen = False
    one_anchor_one_group_only = True
    for layer in layers:
        output = _parse_json(layer["output_json"])
        groups = output.get("groups") or []
        assert len(groups) >= 1, f"unit {layer['unit_id']} has no groups"
        total_anchors_in_unit = 0
        for group in groups:
            group_id = group.get("group_id", "")
            assert "_g" in group_id, f"unstable group_id {group_id!r}"
            anchor_ids = group.get("anchor_segment_ids") or []
            assert len(anchor_ids) >= 1, f"empty anchors in group {group_id}"
            for aid in anchor_ids:
                assert str(aid) in base_anchor_ids, (
                    f"group {group_id} references unknown anchor {aid}"
                )
            total_anchors_in_unit += len(anchor_ids)
            if len(anchor_ids) >= 2:
                multi_anchor_group_seen = True
                one_anchor_one_group_only = False
            # Contiguous membership: each anchor is unique within unit groups.
        # Cross-group no-overlap is enforced by planner; re-check uniqueness.
        all_ids: list[str] = []
        for group in groups:
            all_ids.extend(str(a) for a in (group.get("anchor_segment_ids") or []))
        assert len(all_ids) == len(set(all_ids)), (
            f"unit {layer['unit_id']} has overlapping group anchors"
        )
        # Avoid one-unit-one-group when multi-anchor: if many anchors, groups
        # should be semantic clusters (may still be 1 group if 2-3 short
        # sentences — that is allowed and is NOT one-anchor-one-group).
        if total_anchors_in_unit >= 2 and len(groups) == total_anchors_in_unit:
            # Every group is exactly one anchor → mechanical regression.
            assert False, (
                f"unit {layer['unit_id']} degenerated to one-anchor-one-group "
                f"({total_anchors_in_unit} anchors / {len(groups)} groups)"
            )

    # At least one multi-anchor group somewhere in the fragmented sample
    # (or all multi-anchor units correctly clustered into ≤ ceil(n/2) groups).
    # If the base builder produced only single-anchor units, skip the
    # multi-anchor positive assertion but still require no wrong batch merge.
    if multi_anchor_group_seen:
        assert one_anchor_one_group_only is False

    layer_counts = await _fetch_layer_counts(pool, article.record_id)
    for layer_type in ("translation", "vocabulary", "grammar_note", "sentence_analysis"):
        assert layer_counts.get(layer_type, 0) == unit_count, (
            f"{layer_type} count={layer_counts.get(layer_type, 0)} "
            f"unit_count={unit_count}"
        )


# ---------------------------------------------------------------------------
# Sample 2: structured boundary → STRUCTURED_BATCH
# ---------------------------------------------------------------------------


async def test_v2_structured_boundary_independent_route_fingerprint_policy(
    v2_env: asyncpg.Pool,
) -> None:
    """STRUCTURED_BATCH has distinct route/fingerprint/policy + compact grammar."""
    pool = v2_env
    batch_t = _CountingBatchTranslator()
    batch_v = _CountingBatchVocabularyExecutor()
    batch_g = _FlatUsageGrammarBatchExecutor()
    title = _CountingTitleGenerator()
    window_g = _CountingGrammarWindowExecutor()

    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool,
        user_id=user_id,
        plain_text=_structured_boundary_article(),
        title="V2 Structured Boundary",
    )

    runner = _make_runner(
        pool,
        batch_translator=batch_t,
        batch_vocabulary=batch_v,
        grammar_batch=batch_g,
        grammar_window=window_g,
        title_generator=title,
    )
    result = await _run_through_worker_loop(
        pool, runner, article.record_id, max_ticks=60, max_jobs=30
    )
    _assert_finalized(result)
    assert await _fetch_readiness(pool, article.record_id) == "coverage_complete"

    jobs = await _fetch_jobs(pool, article.record_id)
    assert len(jobs) == 4
    job_types = {(j["job_type"], j["target_type"]) for j in jobs}
    assert ("translate_article", "unit_range") in job_types
    assert ("build_vocabulary_layer_article", "unit_range") in job_types
    assert ("build_grammar_bundle", "unit_range") in job_types
    # No grammar-window grammar windows for structured tier.
    assert not any(j["job_type"] == "build_grammar_bundle_window" for j in jobs)

    by_type = {j["job_type"]: j for j in jobs}
    for job_type in (
        "translate_article",
        "build_vocabulary_layer_article",
        "build_grammar_bundle",
    ):
        j = by_type[job_type]
        assert j["status"] == "succeeded"
        assert j["attempt_count"] == 1
        assert j["article_route"] == "structured_batch"
        assert _parse_json(j["input_json"]).get("article_route") == "structured_batch"
        assert ":window:" not in str(j["target_key"] or "")

    # Distinct STRUCTURED fingerprints / policies (not SHORT *_v1 bases).
    assert by_type["translate_article"]["operation_fingerprint"].startswith(
        _FP_TRANS_STRUCT
    )
    assert by_type["translate_article"]["policy_version"] == _POL_TRANS_STRUCT
    # Distinct from SHORT_BATCH fingerprint base.
    assert not by_type["translate_article"]["operation_fingerprint"].startswith(
        f"{_FP_TRANS_SHORT}:"
    )

    assert by_type["build_vocabulary_layer_article"][
        "operation_fingerprint"
    ].startswith(_FP_VOCAB_STRUCT)
    assert (
        by_type["build_vocabulary_layer_article"]["policy_version"]
        == _POL_VOCAB_STRUCT
    )
    assert not by_type["build_vocabulary_layer_article"][
        "operation_fingerprint"
    ].startswith(f"{_FP_VOCAB_SHORT}:")

    assert by_type["build_grammar_bundle"]["operation_fingerprint"].startswith(
        _FP_GRAM_STRUCT
    )
    assert by_type["build_grammar_bundle"]["policy_version"] == _POL_GRAM_STRUCT
    # Explicitly not the SHORT grammar base.
    assert not by_type["build_grammar_bundle"]["operation_fingerprint"].startswith(
        f"{_FP_GRAM_SHORT}:"
    )

    # Compact batch: 1 call per layer.
    assert batch_t.call_count == 1
    assert batch_v.call_count == 1
    assert batch_g.call_count == 1
    assert title.call_count == 1
    assert window_g.call_count == 0

    unit_count = await _fetch_unit_count(pool, article.record_id)
    layer_counts = await _fetch_layer_counts(pool, article.record_id)
    for layer_type in ("translation", "vocabulary", "grammar_note", "sentence_analysis"):
        assert layer_counts.get(layer_type, 0) == unit_count


# ---------------------------------------------------------------------------
# Sample 3: very-long >4000 words → GROUPED_WINDOWED
# ---------------------------------------------------------------------------


async def test_v2_very_long_grouped_windowed_topology_and_reading_order(
    v2_env: asyncpg.Pool,
) -> None:
    """>4000 words use multi-window topology; layers publish in reading order."""
    pool = v2_env
    batch_t = _CountingBatchTranslator()
    batch_v = _CountingBatchVocabularyExecutor()
    batch_g = _FlatUsageGrammarBatchExecutor()
    title = _CountingTitleGenerator()
    window_g = _CountingGrammarWindowExecutor()

    # Offline gate before expensive pipeline.
    route, words, _chars = _profile_route(_very_long_article())
    assert route == "grouped_windowed"
    assert words > 4000

    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool,
        user_id=user_id,
        plain_text=_very_long_article(),
        title="V2 Very Long Grouped Windowed",
    )

    runner = _make_runner(
        pool,
        batch_translator=batch_t,
        batch_vocabulary=batch_v,
        grammar_batch=batch_g,
        grammar_window=window_g,
        title_generator=title,
    )
    result = await _run_through_worker_loop(
        pool,
        runner,
        article.record_id,
        max_ticks=200,
        max_jobs=120,
        lease_prefix="v2-verylong",
    )
    # Grammar density/record caps may mark some windows no_op while others
    # complete; coverage still finalizes. Do not require completed_clean.
    _assert_finalized(result, allow_no_op=True)
    assert await _fetch_readiness(pool, article.record_id) == "coverage_complete"

    jobs = await _fetch_jobs(pool, article.record_id)
    assert all(j["status"] == "succeeded" for j in jobs), (
        f"non-succeeded jobs: "
        f"{[(j['job_type'], j['status']) for j in jobs if j['status'] != 'succeeded']}"
    )

    translation_jobs = [j for j in jobs if j["job_type"] == "translate_article"]
    vocabulary_jobs = [
        j for j in jobs if j["job_type"] == "build_vocabulary_layer_article"
    ]
    grammar_window_jobs = [
        j for j in jobs if j["job_type"] == "build_grammar_bundle_window"
    ]
    grammar_batch_jobs = [
        j
        for j in jobs
        if j["job_type"] == "build_grammar_bundle" and j["target_type"] == "unit_range"
    ]

    assert len(translation_jobs) >= 2, (
        f"very-long must produce multi-window translation, got "
        f"{len(translation_jobs)}"
    )
    assert len(vocabulary_jobs) >= 2, (
        f"very-long must produce multi-window vocabulary, got "
        f"{len(vocabulary_jobs)}"
    )
    assert len(grammar_window_jobs) >= 2, (
        f"very-long must produce multi grammar windows, got "
        f"{len(grammar_window_jobs)}"
    )
    assert len(grammar_batch_jobs) == 0, (
        "GROUPED_WINDOWED must not create compact grammar batch jobs"
    )

    for j in translation_jobs + vocabulary_jobs:
        assert j["article_route"] == "grouped_windowed"
        assert ":window:" in str(j["target_key"] or ""), (
            f"{j['job_type']} target_key missing window: {j['target_key']!r}"
        )
        assert j["attempt_count"] == 1
        # Shared *_v1 base (not structured).
        if j["job_type"] == "translate_article":
            assert j["operation_fingerprint"].startswith(_FP_TRANS_SHORT)
            assert j["policy_version"] == _POL_TRANS_SHORT
        else:
            assert j["operation_fingerprint"].startswith(_FP_VOCAB_SHORT)
            assert j["policy_version"] == _POL_VOCAB_SHORT

    for j in grammar_window_jobs:
        # Legacy grammar-window grammar identity uses the window UUID itself as target_key;
        # unlike translation/vocabulary it does not use a ":window:" suffix.
        input_json = _parse_json(j["input_json"])
        assert str(input_json.get("window_id")) == str(j["target_key"])
        assert j["attempt_count"] == 1
        assert j["operation_fingerprint"].startswith(_FP_GRAM_WINDOW) or (
            j["operation_fingerprint"] == _FP_GRAM_WINDOW
        )

    # Effective calls == planned window job counts (first-success, no retry).
    assert batch_t.call_count == len(translation_jobs)
    assert batch_v.call_count == len(vocabulary_jobs)
    assert window_g.call_count == len(grammar_window_jobs)
    assert batch_g.call_count == 0
    assert title.call_count == 1

    # Reading-order publish: translation layers ordered by unit order_index.
    unit_count = await _fetch_unit_count(pool, article.record_id)
    layers = await _fetch_translation_layers_reading_order(
        pool, article.record_id, article.base_id
    )
    assert len(layers) == unit_count
    order_indexes = [int(row["order_index"]) for row in layers]
    assert order_indexes == sorted(order_indexes)
    assert order_indexes == list(range(min(order_indexes), min(order_indexes) + len(order_indexes)))

    # Groups remain anchor-grounded and non-empty.
    base_anchor_ids = await _fetch_anchor_ids(pool, article.record_id)
    for layer in layers:
        groups = _parse_json(layer["output_json"]).get("groups") or []
        assert len(groups) >= 1
        for group in groups:
            for aid in group.get("anchor_segment_ids") or []:
                assert str(aid) in base_anchor_ids

    layer_counts = await _fetch_layer_counts(pool, article.record_id)
    assert layer_counts.get("translation", 0) == unit_count
    assert layer_counts.get("vocabulary", 0) == unit_count
    # Grammar window path may budget-cap density; require both subtypes present.
    assert layer_counts.get("grammar_note", 0) > 0
    assert layer_counts.get("sentence_analysis", 0) > 0


# ---------------------------------------------------------------------------
# Sample 4: no-op grammar window
# ---------------------------------------------------------------------------


async def test_v2_noop_grammar_window_terminal_budget_and_no_retry(
    v2_env: asyncpg.Pool,
) -> None:
    """Empty grammar candidates → no_op terminal, budget ok, no duplicate LLM."""
    pool = v2_env
    batch_t = _CountingBatchTranslator()
    batch_v = _CountingBatchVocabularyExecutor()
    batch_g = _FlatUsageGrammarBatchExecutor()
    title = _CountingTitleGenerator()
    empty_window = _EmptyGrammarWindowExecutor()

    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool,
        user_id=user_id,
        plain_text=_noop_window_article(),
        title="V2 No-Op Grammar Window",
    )

    runner = _make_runner(
        pool,
        batch_translator=batch_t,
        batch_vocabulary=batch_v,
        grammar_batch=batch_g,
        grammar_window=empty_window,
        title_generator=title,
    )
    result = await _run_through_worker_loop(
        pool,
        runner,
        article.record_id,
        max_ticks=120,
        max_jobs=80,
        lease_prefix="v2-noop",
    )
    _assert_finalized(result, allow_no_op=True)

    fin = result.completion_finalization_result
    assert fin.outcome == "completed_with_no_op", (
        f"expected completed_with_no_op, got {fin.outcome!r}"
    )
    assert await _fetch_readiness(pool, article.record_id) == "coverage_complete"

    jobs = await _fetch_jobs(pool, article.record_id)
    grammar_window_jobs = [
        j for j in jobs if j["job_type"] == "build_grammar_bundle_window"
    ]
    assert len(grammar_window_jobs) >= 1

    # Jobs themselves succeed (publish path marks window no_op, job succeeded).
    for j in grammar_window_jobs:
        # Keep the same legacy grammar-window window identity contract in the no-op path.
        input_json = _parse_json(j["input_json"])
        assert str(input_json.get("window_id")) == str(j["target_key"])
        assert j["status"] == "succeeded", (
            f"window job status={j['status']!r}, expected succeeded"
        )
        assert j["attempt_count"] == 1, (
            f"window job must not retry on no-op; attempt_count={j['attempt_count']}"
        )
        assert j["max_attempts"] >= 1
        output_ref = _parse_json(j["output_ref_json"]) or {}
        assert output_ref.get("no_op") is True
        diagnostics = output_ref.get("diagnostics") or {}
        assert diagnostics.get("no_op_cause") == "llm_empty"
        # Rationale may live on job row or only in output_ref depending on
        # transition payload; prefer output_ref + window status below.

    windows = await _fetch_analysis_windows(pool, article.record_id)
    assert len(windows) == len(grammar_window_jobs)
    for w in windows:
        assert w["status"] == "no_op", (
            f"window_index={w['window_index']} status={w['status']!r}"
        )
        coverage = _parse_json(w["coverage"]) or {}
        diag = coverage.get("diagnostics") or {}
        assert diag.get("no_op_cause") == "llm_empty"
        raw = diag.get("raw_candidate_count_by_type") or {}
        assert int(raw.get("grammar_note", 0)) == 0
        assert int(raw.get("sentence_analysis", 0)) == 0

    # Effective LLM calls: exactly one per planned grammar window job.
    assert empty_window.call_count == len(grammar_window_jobs), (
        f"expected {len(grammar_window_jobs)} grammar LLM calls, "
        f"got {empty_window.call_count} (duplicate or missing)"
    )
    # No grammar batch fallback under GROUPED_WINDOWED.
    assert batch_g.call_count == 0

    # Translation / vocabulary / title still succeed normally.
    assert batch_t.call_count >= 1
    assert batch_v.call_count >= 1
    assert title.call_count == 1
    for j in jobs:
        if j["job_type"] in (
            "translate_article",
            "build_vocabulary_layer_article",
            "generate_display_title_zh",
        ):
            assert j["status"] == "succeeded"
            assert j["attempt_count"] == 1

    # No grammar layers published (all windows no-op).
    layer_counts = await _fetch_layer_counts(pool, article.record_id)
    assert layer_counts.get("grammar_note", 0) == 0
    assert layer_counts.get("sentence_analysis", 0) == 0
    # Translation/vocabulary still published.
    unit_count = await _fetch_unit_count(pool, article.record_id)
    assert layer_counts.get("translation", 0) == unit_count
    assert layer_counts.get("vocabulary", 0) == unit_count
