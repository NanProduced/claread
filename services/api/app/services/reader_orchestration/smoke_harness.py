from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal
from uuid import UUID

import asyncpg

from app.config.settings import get_settings
from app.contracts.annotation import compute_text_range_hash, utf16_code_unit_length
from app.database import connection as db_connection
from app.schemas.reader_orchestration import (
    GrammarBundleOutput,
    GrammarNoteItem,
    ReaderPlateSnapshot,
    ReaderTextRangeAnchor,
    SentenceAnalysisChunk,
    SentenceAnalysisItem,
    TranslationLayerOutput,
    VocabularyHighlightItem,
    VocabularyLayerOutput,
)
from app.services.reader_orchestration.article_ready_service import (
    ArticleReadyPersistenceService,
    PlainTextArticleReadySubmitRequest,
)
from app.services.reader_orchestration.grammar_worker import (
    GrammarBundleWorkerService,
    GrammarExecutionResult,
    GrammarJobContext,
)
from app.services.reader_orchestration.orchestrator import ReaderOrchestrator
from app.services.reader_orchestration.pipeline_runner import (
    DEFAULT_PIPELINE_MAX_JOBS,
    DEFAULT_PIPELINE_MAX_TICKS,
    ReaderEnhancementPipelineRunner,
    ReaderPipelineRunSummary,
)
from app.services.reader_orchestration.translation_worker import (
    TranslationExecutionResult,
    TranslationJobContext,
    TranslationWorkerService,
)
from app.services.reader_orchestration.vocabulary_worker import (
    VocabularyExecutionResult,
    VocabularyJobContext,
    VocabularyWorkerService,
)

SmokeExecutorMode = Literal["fake", "real"]

DEFAULT_SMOKE_LEASE_OWNER = "reader-d5-smoke-harness"
DEFAULT_SMOKE_LEASE_DURATION = timedelta(seconds=30)
DEV_FAKE_EXECUTOR_NOTE = "dev/test-only deterministic fake executors"
DEV_FAKE_TRANSLATION_PROMPT_VERSION = "reader-d5-smoke-fake-translation"
DEV_FAKE_VOCABULARY_PROMPT_VERSION = "reader-d5-smoke-fake-vocabulary"
DEV_FAKE_GRAMMAR_PROMPT_VERSION = "reader-d5-smoke-fake-grammar"
DEV_FAKE_MODEL_PROFILE_PREFIX = "reader_d5_smoke_fake"
_WORD_RE = re.compile(r"[A-Za-z]+")


@dataclass(frozen=True, slots=True)
class SmokePublishedLayerCounts:
    translation: int = 0
    vocabulary: int = 0
    grammar_note: int = 0
    sentence_analysis: int = 0


@dataclass(frozen=True, slots=True)
class ReaderSmokeHarnessResult:
    executor_mode: SmokeExecutorMode
    executor_note: str | None
    record_id: UUID
    base_id: UUID
    pipeline_summary: ReaderPipelineRunSummary
    snapshot: ReaderPlateSnapshot
    layer_counts: SmokePublishedLayerCounts


class DevFakeTranslationExecutor:
    async def translate(
        self,
        context: TranslationJobContext,
    ) -> TranslationExecutionResult:
        return TranslationExecutionResult(
            output=TranslationLayerOutput(
                target_language="zh-CN",
                translated_text=f"[DEV FAKE] {context.source_text}",
                notes=[],
                confidence="normal",
            ),
            usage_data={"input_tokens": 1, "output_tokens": 1},
            prompt_version=DEV_FAKE_TRANSLATION_PROMPT_VERSION,
            model_profile=f"{DEV_FAKE_MODEL_PROFILE_PREFIX}_translation",
            model_provider="fake",
            model_name="reader-d5-smoke-fake-translation",
        )


class DevFakeVocabularyExecutor:
    async def generate(
        self,
        context: VocabularyJobContext,
    ) -> VocabularyExecutionResult:
        anchor_segment = context.anchor_segments[0]
        word_match = _WORD_RE.search(anchor_segment.text)
        if word_match is None:
            raise ValueError("dev fake vocabulary executor requires at least one word")
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
                        brief_explanation="Dev smoke keyword",
                        reason="reader_d5_smoke_fake",
                    )
                ]
            ),
            usage_data={"input_tokens": 1, "output_tokens": 1},
            prompt_version=DEV_FAKE_VOCABULARY_PROMPT_VERSION,
            model_profile=f"{DEV_FAKE_MODEL_PROFILE_PREFIX}_vocabulary",
            model_provider="fake",
            model_name="reader-d5-smoke-fake-vocabulary",
        )


class DevFakeGrammarBundleExecutor:
    async def generate(
        self,
        context: GrammarJobContext,
    ) -> GrammarExecutionResult:
        anchor_segment = context.anchor_segments[0]
        word_match = _WORD_RE.search(anchor_segment.text)
        if word_match is None:
            raise ValueError("dev fake grammar executor requires at least one word")
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
                        grammar_point="dev smoke grammar point",
                        pattern="SVO",
                        note="Deterministic grammar note for D5 local smoke.",
                    )
                ],
                sentence_analyses=[
                    SentenceAnalysisItem(
                        anchor=sentence_anchor,
                        label="main clause",
                        analysis="Deterministic sentence analysis for D5 local smoke.",
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
            prompt_version=DEV_FAKE_GRAMMAR_PROMPT_VERSION,
            model_profile=f"{DEV_FAKE_MODEL_PROFILE_PREFIX}_grammar",
            model_provider="fake",
            model_name="reader-d5-smoke-fake-grammar",
        )


class ReaderEnhancementSmokeHarness:
    def __init__(
        self,
        *,
        pool: asyncpg.Pool | None = None,
        article_service: ArticleReadyPersistenceService | None = None,
    ) -> None:
        resolved_article_service = article_service or ArticleReadyPersistenceService(pool=pool)
        self._pool = pool or getattr(resolved_article_service, "_pool", None)
        self._article_service = resolved_article_service

    def get_pool(self) -> asyncpg.Pool:
        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("Database pool not initialized")
        return pool

    async def prepare_record(
        self,
        *,
        user_id: UUID,
        plain_text: str,
        title: str | None,
        executor_mode: SmokeExecutorMode = "real",
        allow_fake_executors: bool = False,
        language: str | None = None,
        lease_owner: str = DEFAULT_SMOKE_LEASE_OWNER,
        lease_duration: timedelta = DEFAULT_SMOKE_LEASE_DURATION,
        max_ticks: int = DEFAULT_PIPELINE_MAX_TICKS,
        max_jobs: int = DEFAULT_PIPELINE_MAX_JOBS,
    ) -> ReaderSmokeHarnessResult:
        if executor_mode == "fake":
            self._assert_fake_mode_allowed(allow_fake_executors=allow_fake_executors)

        submit_result = await self._article_service.submit_plain_text(
            PlainTextArticleReadySubmitRequest(
                user_id=user_id,
                plain_text=plain_text,
                title=title,
                language=language,
                source_metadata={
                    "origin": "reader_d5_smoke_harness",
                    "executor_mode": executor_mode,
                },
            )
        )
        runner = self._build_pipeline_runner(executor_mode=executor_mode)
        pipeline_summary = await runner.run(
            record_id=submit_result.record_id,
            user_id=user_id,
            lease_owner=lease_owner,
            lease_duration=lease_duration,
            max_ticks=max_ticks,
            max_jobs=max_jobs,
        )
        snapshot_expected_base_id = (
            None if pipeline_summary.snapshot_reload_recommended else pipeline_summary.base_id
        )
        snapshot_expected_generation = (
            None
            if pipeline_summary.snapshot_reload_recommended
            else pipeline_summary.expected_generation
        )
        snapshot = await self._article_service.load_snapshot(
            record_id=submit_result.record_id,
            user_id=user_id,
            expected_base_id=snapshot_expected_base_id,
            expected_generation=snapshot_expected_generation,
        )

        return ReaderSmokeHarnessResult(
            executor_mode=executor_mode,
            executor_note=DEV_FAKE_EXECUTOR_NOTE if executor_mode == "fake" else None,
            record_id=submit_result.record_id,
            base_id=pipeline_summary.base_id,
            pipeline_summary=pipeline_summary,
            snapshot=snapshot,
            layer_counts=_count_snapshot_layers(snapshot),
        )

    def _build_pipeline_runner(
        self,
        *,
        executor_mode: SmokeExecutorMode,
    ) -> ReaderEnhancementPipelineRunner:
        pool = self.get_pool()
        if executor_mode == "real":
            return ReaderEnhancementPipelineRunner(pool=pool)

        translation_worker = TranslationWorkerService(
            pool=pool,
            translator=DevFakeTranslationExecutor(),
        )
        orchestrator = ReaderOrchestrator(
            pool=pool,
            worker_service=translation_worker,
        )
        vocabulary_worker = VocabularyWorkerService(
            pool=pool,
            executor=DevFakeVocabularyExecutor(),
        )
        grammar_worker = GrammarBundleWorkerService(
            pool=pool,
            executor=DevFakeGrammarBundleExecutor(),
        )
        return ReaderEnhancementPipelineRunner(
            pool=pool,
            translation_orchestrator=orchestrator,
            vocabulary_worker_service=vocabulary_worker,
            grammar_worker_service=grammar_worker,
        )

    @staticmethod
    def _assert_fake_mode_allowed(*, allow_fake_executors: bool) -> None:
        app_env = get_settings().app_env.strip().lower()
        if app_env == "production":
            raise RuntimeError("fake smoke executors are disabled in production")
        if not allow_fake_executors:
            raise RuntimeError(
                "fake smoke executors require explicit opt-in "
                "with allow_fake_executors=True"
            )


def _count_snapshot_layers(snapshot: ReaderPlateSnapshot) -> SmokePublishedLayerCounts:
    counts = {
        "translation": 0,
        "vocabulary": 0,
        "grammar_note": 0,
        "sentence_analysis": 0,
    }
    for layer in snapshot.enhancement_layers:
        if layer.layer_type in counts:
            counts[layer.layer_type] += 1
    return SmokePublishedLayerCounts(
        translation=counts["translation"],
        vocabulary=counts["vocabulary"],
        grammar_note=counts["grammar_note"],
        sentence_analysis=counts["sentence_analysis"],
    )
