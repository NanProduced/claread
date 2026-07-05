from __future__ import annotations

import json
import re
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.contracts.annotation import compute_text_range_hash, utf16_code_unit_length
from app.database import connection as db_connection
from app.schemas.reader_orchestration import (
    GrammarBundleOutput,
    GrammarNoteItem,
    ReaderTextRangeAnchor,
    SentenceAnalysisChunk,
    SentenceAnalysisItem,
    TranslationGenerationGroup,
    TranslationLayerGenerationOutput,
    VocabularyHighlightItem,
    VocabularyLayerOutput,
)
from app.services.reader_orchestration.article_ready_service import (
    ArticleReadyPersistenceService,
)
from app.services.reader_orchestration.display_title_worker import (
    DisplayTitleExecutionResult,
    DisplayTitleJobContext,
    DisplayTitleWorkerService,
)
from app.services.reader_orchestration.grammar_worker import (
    GrammarBundleWorkerService,
    GrammarExecutionResult,
    GrammarJobContext,
)
from app.services.reader_orchestration.orchestrator import ReaderOrchestrator
from app.services.reader_orchestration.pipeline_runner import (
    ReaderEnhancementPipelineRunner,
)
from app.services.reader_orchestration.translation_worker import (
    TranslationExecutionResult,
    TranslationJobContext,
    TranslationWorkerService,
)
from app.services.reader_orchestration.vocabulary_worker import (
    UnconfiguredVocabularyExecutor,
    VocabularyExecutionResult,
    VocabularyJobContext,
    VocabularyWorkerService,
)
from tests.reader_orchestration_test_support import (
    BASELINE_SQL,
    CompatTranslationLayerPublisher,
    connect_admin,
    insert_user,
    long_plain_text_fixture,
    make_pool,
    submit_article_ready,
)

# Migration 0015 adds ``layer_analysis_plans`` + ``analysis_windows`` tables.
# Required because ``bootstrap_missing_jobs`` now routes grammar bootstrap
# based on Z+ plan existence in ``layer_analysis_plans`` (Task C3), and the
# pipeline runner's worker_order dispatch depends on whether a window worker
# is registered (Task C4).
_REPO_ROOT = Path(__file__).resolve().parents[3]
_MIGRATION_0015_SQL = (
    _REPO_ROOT / "infra" / "migrations" / "0015_layer_analysis_plans.sql"
).read_text(encoding="utf-8")

LEASE_DURATION = timedelta(seconds=30)
WORD_RE = re.compile(r"[A-Za-z]+")


class _StaticTranslator:
    async def translate(
        self,
        context: TranslationJobContext,
    ) -> TranslationExecutionResult:
        return TranslationExecutionResult(
            output=TranslationLayerGenerationOutput(
                groups=[
                    TranslationGenerationGroup(
                        anchor_segment_ids=[
                            anchor_segment.anchor_segment_id
                            for anchor_segment in context.anchor_segments
                        ],
                        translated_text=f"译文：{context.source_text}",
                    )
                ]
            ),
            usage_data={"input_tokens": 1, "output_tokens": 1},
            prompt_version="pipeline-test-translation",
            model_profile="fake_translation",
            model_provider="fake",
            model_name="fake-translation",
        )


class _MutatingTranslator:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def translate(
        self,
        context: TranslationJobContext,
    ) -> TranslationExecutionResult:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "UPDATE reading_bases SET status = 'superseded' WHERE id = $1",
                    context.base_id,
                )
                new_base_id = await conn.fetchval(
                    """
                    INSERT INTO reading_bases (
                        reading_record_id,
                        base_version,
                        record_generation,
                        text,
                        content_sha256,
                        content_utf16_length,
                        canonicalizer_version,
                        builder_version,
                        segmenter_version,
                        language,
                        title_snapshot,
                        navigation_json,
                        status
                    )
                    SELECT
                        reading_record_id,
                        base_version + 1,
                        record_generation + 1,
                        text,
                        content_sha256,
                        content_utf16_length,
                        canonicalizer_version,
                        builder_version,
                        segmenter_version,
                        language,
                        title_snapshot,
                        navigation_json,
                        'active'
                    FROM reading_bases
                    WHERE id = $1
                    RETURNING id
                    """,
                    context.base_id,
                )
                assert new_base_id is not None
                await conn.execute(
                    """
                    UPDATE reading_records
                    SET generation = generation + 1,
                        active_base_id = $2
                    WHERE id = $1
                    """,
                    context.reading_record_id,
                    new_base_id,
                )
        return await _StaticTranslator().translate(context)


class _StaticVocabularyExecutor:
    async def generate(
        self,
        context: VocabularyJobContext,
    ) -> VocabularyExecutionResult:
        anchor_segment = context.anchor_segments[0]
        word_match = WORD_RE.search(anchor_segment.text)
        assert word_match is not None
        selected_text = word_match.group(0)
        start_offset = anchor_segment.unit_start_utf16 + utf16_code_unit_length(
            anchor_segment.text[: word_match.start()]
        )
        anchor = ReaderTextRangeAnchor(
            base_id=str(context.base_id),
            unit_id=context.unit_id,
            anchor_segment_id=anchor_segment.anchor_segment_id,
            sentence_id=anchor_segment.sentence_id,
            segment_type=anchor_segment.segment_type,
            start_offset=start_offset,
            end_offset=start_offset + utf16_code_unit_length(selected_text),
            selected_text=selected_text,
            text_hash=compute_text_range_hash(selected_text),
        )
        return VocabularyExecutionResult(
            output=VocabularyLayerOutput(
                items=[
                    VocabularyHighlightItem(
                        anchor=anchor,
                        headword=selected_text.lower(),
                        brief_explanation="关键词",
                        reason="pipeline_runner_test",
                    )
                ]
            ),
            usage_data={"input_tokens": 1, "output_tokens": 1},
            prompt_version="pipeline-test-vocabulary",
            model_profile="fake_vocabulary",
            model_provider="fake",
            model_name="fake-vocabulary",
        )


class _StaticGrammarExecutor:
    async def generate(
        self,
        context: GrammarJobContext,
    ) -> GrammarExecutionResult:
        anchor_segment = context.anchor_segments[0]
        word_match = WORD_RE.search(anchor_segment.text)
        assert word_match is not None
        word = word_match.group(0)
        word_start = anchor_segment.unit_start_utf16 + utf16_code_unit_length(
            anchor_segment.text[: word_match.start()]
        )
        word_anchor = ReaderTextRangeAnchor(
            base_id=str(context.base_id),
            unit_id=context.unit_id,
            anchor_segment_id=anchor_segment.anchor_segment_id,
            sentence_id=anchor_segment.sentence_id,
            segment_type=anchor_segment.segment_type,
            start_offset=word_start,
            end_offset=word_start + utf16_code_unit_length(word),
            selected_text=word,
            text_hash=compute_text_range_hash(word),
        )
        sentence_anchor = ReaderTextRangeAnchor(
            base_id=str(context.base_id),
            unit_id=context.unit_id,
            anchor_segment_id=anchor_segment.anchor_segment_id,
            sentence_id=anchor_segment.sentence_id,
            segment_type=anchor_segment.segment_type,
            start_offset=anchor_segment.unit_start_utf16,
            end_offset=anchor_segment.unit_end_utf16,
            selected_text=anchor_segment.text,
            text_hash=compute_text_range_hash(anchor_segment.text),
        )
        return GrammarExecutionResult(
            output=GrammarBundleOutput(
                grammar_notes=[
                    GrammarNoteItem(
                        spans=[word_anchor],
                        grammar_point="core verb",
                        pattern="SVO",
                        note="Marks the sentence core for the pipeline test.",
                    )
                ],
                sentence_analyses=[
                    SentenceAnalysisItem(
                        anchor=sentence_anchor,
                        label="main clause",
                        analysis="Simple clause structure for deterministic pipeline verification.",
                        chunks=[
                            SentenceAnalysisChunk(
                                order=1,
                                label="clause",
                                text=anchor_segment.text,
                            )
                        ],
                    )
                ],
            ),
            usage_data={"input_tokens": 1, "output_tokens": 1},
            prompt_version="pipeline-test-grammar",
            model_profile="fake_grammar",
            model_provider="fake",
            model_name="fake-grammar",
        )


class _StaticTitleGenerator:
    async def generate(
        self,
        context: DisplayTitleJobContext,
    ) -> DisplayTitleExecutionResult:
        return DisplayTitleExecutionResult(
            title_zh="管线测试文章标题",
            usage_data={"input_tokens": 1, "output_tokens": 1},
            prompt_version="pipeline-test-display-title",
            model_profile="fake_title",
            model_provider="fake",
            model_name="fake-title",
        )


@pytest.fixture
async def pipeline_runner_env() -> asyncpg.Pool:
    schema_name = f"test_reader_pipeline_runner_{uuid4().hex}"
    admin = await connect_admin()
    await admin.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
    await admin.execute(f'CREATE SCHEMA "{schema_name}"')
    await admin.execute(f'SET search_path TO "{schema_name}", public')
    await admin.execute(BASELINE_SQL)
    await admin.execute(_MIGRATION_0015_SQL)
    await admin.close()

    pool = await make_pool(schema_name)
    previous_pool = db_connection.DB_POOL
    db_connection.DB_POOL = pool
    try:
        yield pool
    finally:
        db_connection.DB_POOL = previous_pool
        await pool.close()
        cleanup = await connect_admin()
        await cleanup.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await cleanup.close()


def _plain_text(unit_count: int) -> str:
    paragraphs = [
        "First sentence for pipeline runner.",
        "Second sentence for pipeline runner.",
        "Third sentence for pipeline runner.",
    ]
    return "\n\n".join(paragraphs[:unit_count])


def _make_runner(
    pool: asyncpg.Pool,
    *,
    title_generator: object | None = None,
    translator: object | None = None,
    vocabulary_executor: object | None = None,
    grammar_executor: object | None = None,
) -> ReaderEnhancementPipelineRunner:
    translation_worker = (
        TranslationWorkerService(
            pool=pool,
            layer_publisher=CompatTranslationLayerPublisher(pool=pool),
            translator=translator,
        )
        if translator is not None
        else None
    )
    orchestrator = ReaderOrchestrator(
        pool=pool,
        worker_service=translation_worker,
    )
    vocabulary_worker = (
        VocabularyWorkerService(pool=pool, executor=vocabulary_executor)
        if vocabulary_executor is not None
        else None
    )
    grammar_worker = (
        GrammarBundleWorkerService(pool=pool, executor=grammar_executor)
        if grammar_executor is not None
        else None
    )
    display_title_worker = DisplayTitleWorkerService(
        pool=pool,
        generator=title_generator or _StaticTitleGenerator(),
    )
    return ReaderEnhancementPipelineRunner(
        pool=pool,
        display_title_worker_service=display_title_worker,
        translation_orchestrator=orchestrator,
        vocabulary_worker_service=vocabulary_worker,
        grammar_worker_service=grammar_worker,
    )


async def _count_units(pool: asyncpg.Pool, record_id: UUID, base_id: UUID) -> int:
    async with pool.acquire() as conn:
        return int(
            await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM reading_units
                WHERE reading_record_id = $1
                  AND base_id = $2
                """,
                record_id,
                base_id,
            )
        )


async def _count_jobs(pool: asyncpg.Pool, record_id: UUID, job_type: str) -> int:
    async with pool.acquire() as conn:
        return int(
            await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM reader_jobs
                WHERE reading_record_id = $1
                  AND job_type = $2
                """,
                record_id,
                job_type,
            )
        )


async def _count_jobs_by_status(
    pool: asyncpg.Pool,
    record_id: UUID,
    status: str,
) -> int:
    async with pool.acquire() as conn:
        return int(
            await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM reader_jobs
                WHERE reading_record_id = $1
                  AND status = $2
                """,
                record_id,
                status,
            )
        )


async def _count_layers(pool: asyncpg.Pool, record_id: UUID, layer_type: str) -> int:
    async with pool.acquire() as conn:
        return int(
            await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM enhancement_layers
                WHERE reading_record_id = $1
                  AND layer_type = $2
                  AND status = 'published'
                """,
                record_id,
                layer_type,
            )
        )


def _translation_nodes(snapshot) -> list[dict[str, object]]:
    return [
        child
        for unit_node in snapshot.value
        for child in unit_node["children"]  # type: ignore[index]
        if isinstance(child, dict) and child.get("type") == "reader_translation_group"
    ]


def _vocabulary_marked_leaves(snapshot) -> list[dict[str, object]]:
    return [
        leaf
        for unit_node in snapshot.value
        for child in unit_node["children"]  # type: ignore[index]
        if isinstance(child, dict) and child.get("type") == "reader_source_block"
        for anchor_node in child["children"]  # type: ignore[index]
        if isinstance(anchor_node, dict) and anchor_node.get("type") == "reader_anchor_segment"
        for leaf in anchor_node["children"]  # type: ignore[index]
        if isinstance(leaf, dict) and leaf.get("reader_vocabulary_marks")
    ]


def _grammar_marked_leaves(snapshot) -> list[dict[str, object]]:
    return [
        leaf
        for unit_node in snapshot.value
        for child in unit_node["children"]  # type: ignore[index]
        if isinstance(child, dict) and child.get("type") == "reader_source_block"
        for anchor_node in child["children"]  # type: ignore[index]
        if isinstance(anchor_node, dict) and anchor_node.get("type") == "reader_anchor_segment"
        for leaf in anchor_node["children"]  # type: ignore[index]
        if isinstance(leaf, dict) and leaf.get("reader_grammar_note_marks")
    ]


def _sentence_analysis_nodes(snapshot) -> list[dict[str, object]]:
    return [
        child
        for unit_node in snapshot.value
        for child in unit_node["children"]  # type: ignore[index]
        if isinstance(child, dict) and child.get("type") == "reader_sentence_analysis"
    ]


@pytest.mark.anyio
async def test_bootstrap_missing_jobs_covers_all_units_and_is_idempotent(
    pipeline_runner_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(pipeline_runner_env)
    article = await submit_article_ready(
        pipeline_runner_env,
        user_id=user_id,
        plain_text=_plain_text(3),
        title="Pipeline Bootstrap",
    )
    runner = _make_runner(pipeline_runner_env)

    first = await runner.bootstrap_missing_jobs(
        record_id=article.record_id,
        user_id=user_id,
    )
    unit_count = await _count_units(
        pipeline_runner_env,
        article.record_id,
        article.base_id,
    )
    assert unit_count == 3
    assert first.job_counts.display_title == 1
    assert first.job_counts.translation == unit_count
    assert first.job_counts.vocabulary == unit_count
    assert first.job_counts.grammar_bundle == unit_count
    assert len(first.translation_results) == unit_count
    assert len(first.vocabulary_results) == unit_count
    assert len(first.grammar_results) == unit_count

    second = await runner.bootstrap_missing_jobs(
        record_id=article.record_id,
        user_id=user_id,
    )
    assert second.job_counts.translation == 0
    assert second.job_counts.vocabulary == 0
    assert second.job_counts.grammar_bundle == 0
    assert second.job_counts.display_title == 0

    assert await _count_jobs(
        pipeline_runner_env,
        article.record_id,
        "generate_display_title_zh",
    ) == 1
    assert await _count_jobs(
        pipeline_runner_env,
        article.record_id,
        "translate_unit",
    ) == unit_count
    assert await _count_jobs(
        pipeline_runner_env,
        article.record_id,
        "build_vocabulary_layer",
    ) == unit_count
    assert await _count_jobs(
        pipeline_runner_env,
        article.record_id,
        "build_grammar_bundle",
    ) == unit_count


@pytest.mark.anyio
async def test_run_only_drains_jobs_for_target_record(
    pipeline_runner_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(pipeline_runner_env)
    older_article = await submit_article_ready(
        pipeline_runner_env,
        user_id=user_id,
        plain_text=_plain_text(1),
        title="Pipeline Older Record",
    )
    target_article = await submit_article_ready(
        pipeline_runner_env,
        user_id=user_id,
        plain_text=_plain_text(1),
        title="Pipeline Target Record",
    )
    runner = _make_runner(
        pipeline_runner_env,
        translator=_StaticTranslator(),
        vocabulary_executor=_StaticVocabularyExecutor(),
        grammar_executor=_StaticGrammarExecutor(),
    )
    older_bootstrap = await runner.bootstrap_missing_jobs(
        record_id=older_article.record_id,
        user_id=user_id,
    )
    assert older_bootstrap.job_counts.display_title == 1
    assert older_bootstrap.job_counts.translation == 1
    assert older_bootstrap.job_counts.vocabulary == 1
    assert older_bootstrap.job_counts.grammar_bundle == 1

    summary = await runner.run(
        record_id=target_article.record_id,
        user_id=user_id,
        lease_owner="pipeline-record-scope",
        lease_duration=LEASE_DURATION,
        max_ticks=4,
        max_jobs=4,
    )

    assert summary.record_id == target_article.record_id
    assert summary.base_id == target_article.base_id
    assert summary.total_jobs == 4
    assert summary.total_ticks == 4
    assert summary.stopped_reason == "max_jobs_reached"
    assert summary.outcome_counts.succeeded == 4
    assert summary.outcome_counts.no_job == 0

    assert await _count_layers(
        pipeline_runner_env,
        target_article.record_id,
        "translation",
    ) == 1
    assert await _count_layers(
        pipeline_runner_env,
        target_article.record_id,
        "vocabulary",
    ) == 1
    assert await _count_layers(
        pipeline_runner_env,
        target_article.record_id,
        "grammar_note",
    ) == 1
    assert await _count_layers(
        pipeline_runner_env,
        target_article.record_id,
        "sentence_analysis",
    ) == 1

    assert await _count_layers(
        pipeline_runner_env,
        older_article.record_id,
        "translation",
    ) == 0
    assert await _count_layers(
        pipeline_runner_env,
        older_article.record_id,
        "vocabulary",
    ) == 0
    assert await _count_layers(
        pipeline_runner_env,
        older_article.record_id,
        "grammar_note",
    ) == 0
    assert await _count_layers(
        pipeline_runner_env,
        older_article.record_id,
        "sentence_analysis",
    ) == 0
    assert await _count_jobs_by_status(
        pipeline_runner_env,
        older_article.record_id,
        "queued",
    ) == 4
    assert await _count_jobs_by_status(
        pipeline_runner_env,
        target_article.record_id,
        "succeeded",
    ) == 4


@pytest.mark.anyio
async def test_run_with_fake_executors_publishes_all_layers_and_snapshot_reload_sees_them(
    pipeline_runner_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(pipeline_runner_env)
    article = await submit_article_ready(
        pipeline_runner_env,
        user_id=user_id,
        plain_text=_plain_text(2),
        title="Pipeline Success",
    )
    runner = _make_runner(
        pipeline_runner_env,
        translator=_StaticTranslator(),
        vocabulary_executor=_StaticVocabularyExecutor(),
        grammar_executor=_StaticGrammarExecutor(),
    )

    summary = await runner.run(
        record_id=article.record_id,
        user_id=user_id,
        lease_owner="pipeline-success",
        lease_duration=LEASE_DURATION,
        max_ticks=13,
        max_jobs=12,
    )

    assert summary.bootstrapped_job_counts.translation == 2
    assert summary.bootstrapped_job_counts.vocabulary == 2
    assert summary.bootstrapped_job_counts.grammar_bundle == 2
    assert summary.bootstrapped_job_counts.display_title == 1
    assert summary.worker_tick_counts.display_title == 3
    assert summary.worker_tick_counts.translation == 3
    assert summary.worker_tick_counts.vocabulary == 3
    assert summary.worker_tick_counts.grammar_bundle == 3
    assert summary.outcome_counts.succeeded == 7
    assert summary.outcome_counts.retry_later == 0
    assert summary.outcome_counts.failed_terminal == 0
    assert summary.outcome_counts.superseded == 0
    assert summary.outcome_counts.no_job == 5
    assert summary.total_jobs == 7
    assert summary.total_ticks == 12
    assert summary.stopped_reason == "all_workers_no_job"
    assert summary.stopped_worker_type is None
    assert summary.snapshot_reload_recommended is True
    assert summary.last_event_sequence > summary.bootstrap.last_event_sequence

    assert await _count_layers(
        pipeline_runner_env,
        article.record_id,
        "translation",
    ) == 2
    assert await _count_layers(
        pipeline_runner_env,
        article.record_id,
        "vocabulary",
    ) == 2
    assert await _count_layers(
        pipeline_runner_env,
        article.record_id,
        "grammar_note",
    ) == 2
    assert await _count_layers(
        pipeline_runner_env,
        article.record_id,
        "sentence_analysis",
    ) == 2

    async with pipeline_runner_env.acquire() as conn:
        before_counts = {
            "reader_events": await conn.fetchval(
                "SELECT COUNT(*) FROM reader_events WHERE reading_record_id = $1",
                article.record_id,
            ),
            "reader_job_events": await conn.fetchval(
                "SELECT COUNT(*) FROM reader_job_events WHERE reading_record_id = $1",
                article.record_id,
            ),
            "enhancement_layers": await conn.fetchval(
                "SELECT COUNT(*) FROM enhancement_layers WHERE reading_record_id = $1",
                article.record_id,
            ),
        }

    snapshot = await ArticleReadyPersistenceService(pool=pipeline_runner_env).load_snapshot(
        record_id=article.record_id,
        user_id=user_id,
    )

    async with pipeline_runner_env.acquire() as conn:
        after_counts = {
            "reader_events": await conn.fetchval(
                "SELECT COUNT(*) FROM reader_events WHERE reading_record_id = $1",
                article.record_id,
            ),
            "reader_job_events": await conn.fetchval(
                "SELECT COUNT(*) FROM reader_job_events WHERE reading_record_id = $1",
                article.record_id,
            ),
            "enhancement_layers": await conn.fetchval(
                "SELECT COUNT(*) FROM enhancement_layers WHERE reading_record_id = $1",
                article.record_id,
            ),
        }

    assert before_counts == after_counts
    layer_types = [layer.layer_type for layer in snapshot.enhancement_layers]
    assert layer_types.count("translation") == 2
    assert layer_types.count("vocabulary") == 2
    assert layer_types.count("grammar_note") == 2
    assert layer_types.count("sentence_analysis") == 2
    translation_nodes = _translation_nodes(snapshot)
    assert len(translation_nodes) == 2
    assert all(node["type"] == "reader_translation_group" for node in translation_nodes)
    assert all(node["owner"] == "system_ai" for node in translation_nodes)
    assert all(isinstance(node["group_id"], str) and node["group_id"] for node in translation_nodes)
    assert all(isinstance(node["covered_anchor_segment_ids"], list) for node in translation_nodes)
    assert all(isinstance(node["source_text_hash"], str) for node in translation_nodes)
    assert all(node["children"][0]["text"].startswith("译文：") for node in translation_nodes)  # type: ignore[index]
    assert all(
        forbidden_key not in node
        for node in translation_nodes
        for forbidden_key in (
            "target_language",
            "confidence",
            "notes",
            "source_text",
            "translated_text",
        )
    )
    assert _vocabulary_marked_leaves(snapshot)
    assert _grammar_marked_leaves(snapshot)
    assert len(_sentence_analysis_nodes(snapshot)) == 2
    assert summary.last_event_sequence == snapshot.last_event_sequence
    assert "render_scene_json" not in json.dumps(snapshot.value, ensure_ascii=False)


@pytest.mark.anyio
async def test_run_with_long_text_fixture_projects_sentence_analysis_nodes_on_snapshot_reload(
    pipeline_runner_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(pipeline_runner_env)
    article = await submit_article_ready(
        pipeline_runner_env,
        user_id=user_id,
        plain_text=long_plain_text_fixture(),
        title="Pipeline Long Sentence Analysis",
    )
    runner = _make_runner(
        pipeline_runner_env,
        translator=_StaticTranslator(),
        vocabulary_executor=_StaticVocabularyExecutor(),
        grammar_executor=_StaticGrammarExecutor(),
    )

    summary = await runner.run(
        record_id=article.record_id,
        user_id=user_id,
        lease_owner="pipeline-long-text",
        lease_duration=LEASE_DURATION,
        max_ticks=18,
        max_jobs=18,
    )

    unit_count = await _count_units(
        pipeline_runner_env,
        article.record_id,
        article.base_id,
    )
    assert unit_count >= 1
    assert summary.outcome_counts.failed_terminal == 0
    assert summary.outcome_counts.retry_later == 0
    assert summary.snapshot_reload_recommended is True
    assert await _count_layers(
        pipeline_runner_env,
        article.record_id,
        "grammar_note",
    ) == unit_count
    assert await _count_layers(
        pipeline_runner_env,
        article.record_id,
        "sentence_analysis",
    ) == unit_count

    async with pipeline_runner_env.acquire() as conn:
        before_counts = {
            "reader_events": await conn.fetchval(
                "SELECT COUNT(*) FROM reader_events WHERE reading_record_id = $1",
                article.record_id,
            ),
            "reader_job_events": await conn.fetchval(
                "SELECT COUNT(*) FROM reader_job_events WHERE reading_record_id = $1",
                article.record_id,
            ),
            "enhancement_layers": await conn.fetchval(
                "SELECT COUNT(*) FROM enhancement_layers WHERE reading_record_id = $1",
                article.record_id,
            ),
        }

    snapshot = await ArticleReadyPersistenceService(pool=pipeline_runner_env).load_snapshot(
        record_id=article.record_id,
        user_id=user_id,
    )

    async with pipeline_runner_env.acquire() as conn:
        after_counts = {
            "reader_events": await conn.fetchval(
                "SELECT COUNT(*) FROM reader_events WHERE reading_record_id = $1",
                article.record_id,
            ),
            "reader_job_events": await conn.fetchval(
                "SELECT COUNT(*) FROM reader_job_events WHERE reading_record_id = $1",
                article.record_id,
            ),
            "enhancement_layers": await conn.fetchval(
                "SELECT COUNT(*) FROM enhancement_layers WHERE reading_record_id = $1",
                article.record_id,
            ),
        }

    assert before_counts == after_counts
    sentence_analysis_nodes = _sentence_analysis_nodes(snapshot)
    assert len(sentence_analysis_nodes) == unit_count
    assert all(
        node["type"] == "reader_sentence_analysis" for node in sentence_analysis_nodes
    )
    assert all(node["owner"] == "system_ai" for node in sentence_analysis_nodes)
    assert any(
        len(WORD_RE.findall(str(node["selected_text"]))) >= 25
        for node in sentence_analysis_nodes
    )
    assert sentence_analysis_nodes[0]["label"] == "main clause"
    assert summary.last_event_sequence == snapshot.last_event_sequence
    assert "render_scene_json" not in json.dumps(snapshot.value, ensure_ascii=False)


@pytest.mark.anyio
async def test_run_reports_superseded_when_publish_fence_fails(
    pipeline_runner_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(pipeline_runner_env)
    article = await submit_article_ready(
        pipeline_runner_env,
        user_id=user_id,
        plain_text=_plain_text(1),
        title="Pipeline Fence",
    )
    runner = _make_runner(
        pipeline_runner_env,
        translator=_MutatingTranslator(pipeline_runner_env),
        vocabulary_executor=_StaticVocabularyExecutor(),
        grammar_executor=_StaticGrammarExecutor(),
    )

    summary = await runner.run(
        record_id=article.record_id,
        user_id=user_id,
        lease_owner="pipeline-fence",
        lease_duration=LEASE_DURATION,
        max_ticks=6,
        max_jobs=6,
    )

    assert summary.stopped_reason == "attention_required"
    assert summary.stopped_worker_type == "translation"
    assert summary.stopped_outcome == "superseded"
    assert summary.attention_code == "publish_fence_failed"
    assert summary.outcome_counts.succeeded == 1
    assert summary.outcome_counts.superseded >= 1
    assert summary.total_jobs == 2
    assert summary.snapshot_reload_recommended is True
    assert await _count_layers(
        pipeline_runner_env,
        article.record_id,
        "translation",
    ) == 0


@pytest.mark.anyio
async def test_run_fail_closed_on_unconfigured_vocabulary_executor(
    pipeline_runner_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(pipeline_runner_env)
    article = await submit_article_ready(
        pipeline_runner_env,
        user_id=user_id,
        plain_text=_plain_text(1),
        title="Pipeline Fail Closed",
    )
    runner = _make_runner(
        pipeline_runner_env,
        translator=_StaticTranslator(),
        vocabulary_executor=UnconfiguredVocabularyExecutor(),
    )

    summary = await runner.run(
        record_id=article.record_id,
        user_id=user_id,
        lease_owner="pipeline-fail-closed",
        lease_duration=LEASE_DURATION,
        max_ticks=6,
        max_jobs=6,
    )

    assert summary.stopped_reason == "attention_required"
    assert summary.stopped_worker_type == "vocabulary"
    assert summary.stopped_outcome == "failed_terminal"
    assert summary.attention_code == "vocabulary_executor_unconfigured"
    assert summary.outcome_counts.succeeded == 2
    assert summary.outcome_counts.failed_terminal == 1
    assert await _count_layers(
        pipeline_runner_env,
        article.record_id,
        "translation",
    ) == 1
    assert await _count_layers(
        pipeline_runner_env,
        article.record_id,
        "vocabulary",
    ) == 0


@pytest.mark.anyio
async def test_run_respects_max_ticks(
    pipeline_runner_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(pipeline_runner_env)
    article = await submit_article_ready(
        pipeline_runner_env,
        user_id=user_id,
        plain_text=_plain_text(1),
        title="Pipeline Max Ticks",
    )
    runner = _make_runner(
        pipeline_runner_env,
        translator=_StaticTranslator(),
        vocabulary_executor=_StaticVocabularyExecutor(),
        grammar_executor=_StaticGrammarExecutor(),
    )

    summary = await runner.run(
        record_id=article.record_id,
        user_id=user_id,
        lease_owner="pipeline-max-ticks",
        lease_duration=LEASE_DURATION,
        max_ticks=2,
        max_jobs=6,
    )

    assert summary.stopped_reason == "max_ticks_reached"
    assert summary.total_ticks == 2
    assert summary.total_jobs == 2
    assert summary.outcome_counts.succeeded == 2
    assert await _count_layers(
        pipeline_runner_env,
        article.record_id,
        "translation",
    ) == 1
    assert await _count_layers(
        pipeline_runner_env,
        article.record_id,
        "vocabulary",
    ) == 0
    assert await _count_layers(
        pipeline_runner_env,
        article.record_id,
        "grammar_note",
    ) == 0


@pytest.mark.anyio
async def test_run_respects_max_jobs(
    pipeline_runner_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(pipeline_runner_env)
    article = await submit_article_ready(
        pipeline_runner_env,
        user_id=user_id,
        plain_text=_plain_text(1),
        title="Pipeline Max Jobs",
    )
    runner = _make_runner(
        pipeline_runner_env,
        translator=_StaticTranslator(),
        vocabulary_executor=_StaticVocabularyExecutor(),
        grammar_executor=_StaticGrammarExecutor(),
    )

    summary = await runner.run(
        record_id=article.record_id,
        user_id=user_id,
        lease_owner="pipeline-max-jobs",
        lease_duration=LEASE_DURATION,
        max_ticks=6,
        max_jobs=2,
    )

    assert summary.stopped_reason == "max_jobs_reached"
    assert summary.total_jobs == 2
    assert summary.total_ticks == 2
    assert summary.outcome_counts.succeeded == 2
    assert await _count_layers(
        pipeline_runner_env,
        article.record_id,
        "translation",
    ) == 1
    assert await _count_layers(
        pipeline_runner_env,
        article.record_id,
        "vocabulary",
    ) == 0
    assert await _count_layers(
        pipeline_runner_env,
        article.record_id,
        "grammar_note",
    ) == 0
