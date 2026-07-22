from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal
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
    TranslationBatchGenerationOutput,
    TranslationBatchGroupOutput,
    TranslationBatchUnitOutput,
    TranslationGenerationGroup,
    TranslationLayerGenerationOutput,
    VocabularyHighlightItem,
    VocabularyLayerOutput,
)
from app.services.reader_orchestration.article_ready_service import (
    ArticleReadyPersistenceService,
    PlainTextArticleReadySubmitRequest,
)
from app.services.reader_orchestration.display_title_worker import (
    DisplayTitleExecutionResult,
    DisplayTitleJobContext,
    DisplayTitleWorkerService,
)
from app.services.reader_orchestration.event_runtime import ReaderEventRuntime
from app.services.reader_orchestration.grammar_window_publisher import (
    GrammarWindowPublisher,
)
from app.services.reader_orchestration.grammar_window_worker import (
    GrammarWindowExecutionResult,
    GrammarWindowWorkerService,
)
from app.services.reader_orchestration.grammar_worker import (
    GrammarBatchExecutionResult,
    GrammarBatchJobContext,
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
    TranslationBatchExecutionResult,
    TranslationBatchJobContext,
    TranslationExecutionResult,
    TranslationJobContext,
    TranslationWorkerService,
    build_deterministic_translation_groups,
)
from app.services.reader_orchestration.vocabulary_worker import (
    VocabularyBatchCandidateOutput,
    VocabularyBatchExecutionResult,
    VocabularyBatchJobContext,
    VocabularyBatchUnitCandidateOutput,
    VocabularyExecutionResult,
    VocabularyHighlightCandidateItem,
    VocabularyJobContext,
    VocabularyWorkerService,
)
from app.services.reader_orchestration.window_selector import CandidateItem

SmokeExecutorMode = Literal["fake", "real"]
# T4.2a-R1: smoke/acceptance harness grammar topology.
# ``legacy`` keeps the pre-T4.2a 4-worker path (enable_zplus_grammar=False,
# per-unit grammar for all routes). ``production`` faithfully reproduces
# the production route-aware split: SHORT_BATCH/STRUCTURED_BATCH → compact
# grammar batch job, GROUPED_WINDOWED → Z+ window, with fake executors
# injected so no real LLM is ever called.
SmokeGrammarTopology = Literal["legacy", "production"]

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
            output=TranslationLayerGenerationOutput(
                groups=[
                    TranslationGenerationGroup(
                        anchor_segment_ids=[
                            anchor_segment.anchor_segment_id
                            for anchor_segment in context.anchor_segments
                        ],
                        translated_text=f"[DEV FAKE] {context.source_text}",
                    )
                ]
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


class DevFakeGrammarBatchExecutor:
    """T4.2a-R1 fake compact grammar batch executor for the smoke harness.

    Mirrors :class:`DevFakeGrammarBundleExecutor` but covers all units in a
    single batch LLM call (one ``GrammarBatchExecutionResult`` with N
    per-unit outputs). Used by the SHORT_BATCH / STRUCTURED_BATCH route so
    the batch path never falls back to the real ``PydanticAIGrammarBatchExecutor``.
    """

    async def generate_batch(
        self,
        context: GrammarBatchJobContext,
    ) -> GrammarBatchExecutionResult:
        outputs: list[tuple[str, GrammarBundleOutput]] = []
        for unit in context.units:
            if not unit.anchor_segments:
                outputs.append((unit.unit_id, GrammarBundleOutput()))
                continue
            anchor_segment = unit.anchor_segments[0]
            word_match = _WORD_RE.search(anchor_segment.text)
            if word_match is None:
                outputs.append((unit.unit_id, GrammarBundleOutput()))
                continue
            word = word_match.group(0)
            word_start = anchor_segment.unit_start_utf16 + utf16_code_unit_length(
                anchor_segment.text[: word_match.start()]
            )
            word_anchor = ReaderTextRangeAnchor(
                base_id=str(context.base_id),
                unit_id=unit.unit_id,
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
                unit_id=unit.unit_id,
                anchor_segment_id=anchor_segment.anchor_segment_id,
                sentence_id=anchor_segment.sentence_id,
                segment_type=anchor_segment.segment_type,
                start_offset=anchor_segment.unit_start_utf16,
                end_offset=anchor_segment.unit_end_utf16,
                selected_text=anchor_segment.text,
                text_hash=compute_text_range_hash(anchor_segment.text),
            )
            outputs.append(
                (
                    unit.unit_id,
                    GrammarBundleOutput(
                        grammar_notes=[
                            GrammarNoteItem(
                                spans=[word_anchor],
                                grammar_point="dev smoke batch grammar point",
                                pattern="SVO",
                                note="Deterministic batch grammar note for D5 local smoke.",
                            )
                        ],
                        sentence_analyses=[
                            SentenceAnalysisItem(
                                anchor=sentence_anchor,
                                label="main clause",
                                analysis=(
                                    "Deterministic batch sentence analysis"
                                    " for D5 local smoke."
                                ),
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
                )
            )
        return GrammarBatchExecutionResult(
            outputs=outputs,
            usage_data={"input_tokens": 1, "output_tokens": 1},
            prompt_version=DEV_FAKE_GRAMMAR_PROMPT_VERSION,
            model_profile=f"{DEV_FAKE_MODEL_PROFILE_PREFIX}_grammar_batch",
            model_provider="fake",
            model_name="reader-d5-smoke-fake-grammar-batch",
        )


class DevFakeGrammarWindowExecutor:
    """T4.2a-R1 fake Z+ grammar window executor for the smoke harness.

    Produces one ``grammar_note`` candidate per target anchor (capped to the
    window budget) so the GROUPED_WINDOWED route can complete without calling
    the real ``PydanticAIGrammarWindowExecutor``. Injected into
    :class:`GrammarWindowWorkerService` when ``enable_zplus_grammar=True``.
    """

    async def generate(
        self, context: dict[str, Any]
    ) -> GrammarWindowExecutionResult:
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
        else:
            grammar_budget = 2

        for anchor in target_anchors[:grammar_budget]:
            anchor_id = str(anchor["anchor_segment_id"])
            unit_id = str(anchor["unit_id"])
            source_text = str(anchor.get("source_text", "smoke"))[:10] or "smoke"
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
                    semantic_dedup_key=f"smoke_grammar:{anchor_id}",
                    pattern_key=f"smoke_pattern:{anchor_id}",
                    quality_score=4,
                    reading_blocker=False,
                    dedup_hint=f"smoke_grammar:{anchor_id}",
                    grammar_point=f"smoke grammar point:{anchor_id}",
                    pattern="SVO",
                    note=f"Dev smoke window grammar note for {anchor_id}.",
                )
            )
        return GrammarWindowExecutionResult(
            candidates=candidates,
            usage_data={"input_tokens": 1, "output_tokens": 1},
            prompt_version=DEV_FAKE_GRAMMAR_PROMPT_VERSION,
            model_profile=f"{DEV_FAKE_MODEL_PROFILE_PREFIX}_grammar_window",
            model_provider="fake",
            model_name="reader-d5-smoke-fake-grammar-window",
        )


class DevFakeDisplayTitleGenerator:
    async def generate(
        self,
        context: DisplayTitleJobContext,
    ) -> DisplayTitleExecutionResult:
        return DisplayTitleExecutionResult(
            title_zh="本地冒烟标题",
            usage_data={"input_tokens": 1, "output_tokens": 1},
            prompt_version="reader-d5-smoke-fake-display-title",
            model_profile=f"{DEV_FAKE_MODEL_PROFILE_PREFIX}_display_title",
            model_provider="fake",
            model_name="reader-d5-smoke-fake-display-title",
        )


class DevFakeTranslationBatchExecutor:
    """T1.1 fake batch translation executor for the smoke harness.

    Deterministic-grouping contract: echoes the backend-predefined
    group_ids from :func:`build_deterministic_translation_groups` (which
    may be one group per unit or split for long units) and returns a
    translated_text with a ``[DEV FAKE BATCH]`` prefix per group.
    """

    async def translate_batch(
        self,
        context: TranslationBatchJobContext,
    ) -> TranslationBatchExecutionResult:
        units: list[TranslationBatchUnitOutput] = []
        for unit in context.units:
            groups = [
                TranslationBatchGroupOutput(
                    group_id=group.group_id,
                    translated_text=f"[DEV FAKE BATCH] {group.source_text}",
                )
                for group in build_deterministic_translation_groups(unit)
            ]
            units.append(
                TranslationBatchUnitOutput(
                    unit_id=unit.unit_id,
                    groups=groups,
                )
            )
        return TranslationBatchExecutionResult(
            output=TranslationBatchGenerationOutput(units=units),
            usage_data={"input_tokens": 1, "output_tokens": 1},
            prompt_version=DEV_FAKE_TRANSLATION_PROMPT_VERSION,
            model_profile=f"{DEV_FAKE_MODEL_PROFILE_PREFIX}_translation_batch",
            model_provider="fake",
            model_name="reader-d5-smoke-fake-translation-batch",
        )


class DevFakeVocabularyBatchExecutor:
    """T1.1 fake batch vocabulary executor for the smoke harness.

    Produces one ``VocabularyBatchUnitCandidateOutput`` per unit with a
    single ``vocab_highlight`` candidate item (mirroring the per-unit
    ``DevFakeVocabularyExecutor``). The batch worker resolves the
    candidates into per-unit ``VocabularyLayerOutput`` via
    ``_build_vocabulary_batch_outputs``.
    """

    async def generate_batch(
        self,
        context: VocabularyBatchJobContext,
    ) -> VocabularyBatchExecutionResult:
        units: list[VocabularyBatchUnitCandidateOutput] = []
        for unit in context.units:
            anchor_segment = unit.anchor_segments[0]
            word_match = _WORD_RE.search(anchor_segment.text)
            if word_match is None:
                raise ValueError(
                    "dev fake vocabulary batch executor requires at least one word"
                )
            selected_text = word_match.group(0)
            units.append(
                VocabularyBatchUnitCandidateOutput(
                    unit_id=unit.unit_id,
                    items=[
                        VocabularyHighlightCandidateItem(
                            anchor_segment_id=anchor_segment.anchor_segment_id,
                            selected_text=selected_text,
                            headword=selected_text.lower(),
                            brief_explanation="Dev smoke batch keyword",
                            reason="reader_d5_smoke_fake_batch",
                        )
                    ],
                )
            )
        return VocabularyBatchExecutionResult(
            output=VocabularyBatchCandidateOutput(schema_version=1, units=units),
            usage_data={"input_tokens": 1, "output_tokens": 1},
            prompt_version=DEV_FAKE_VOCABULARY_PROMPT_VERSION,
            model_profile=f"{DEV_FAKE_MODEL_PROFILE_PREFIX}_vocabulary_batch",
            model_provider="fake",
            model_name="reader-d5-smoke-fake-vocabulary-batch",
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
        # Optional reading metadata. When ``None`` (the default) the
        # existing ``PlainTextArticleReadySubmitRequest`` defaults
        # are kept, which preserves the smoke harness's production
        # behaviour. The baseline harness and the focused tests pass
        # these explicitly so the persisted ``reading_records`` row
        # matches the manifest / CLI override.
        reading_goal: str | None = None,
        reading_variant: str | None = None,
        grammar_topology: SmokeGrammarTopology = "legacy",
    ) -> ReaderSmokeHarnessResult:
        if executor_mode == "fake":
            self._assert_fake_mode_allowed(allow_fake_executors=allow_fake_executors)

        # T4.2a-R1: In real mode the production runner always uses the
        # route-aware split (``enable_zplus_grammar=True``), so the
        # effective topology is ``production`` regardless of the caller's
        # ``grammar_topology`` argument. Recording ``legacy`` here would
        # make real smoke records' ``source_metadata`` misreport the
        # topology that actually executed, breaking observability truth.
        effective_topology: SmokeGrammarTopology = (
            "production" if executor_mode == "real" else grammar_topology
        )

        submit_kwargs: dict[str, Any] = {
            "user_id": user_id,
            "plain_text": plain_text,
            "title": title,
            "language": language,
            "source_metadata": {
                "origin": "reader_d5_smoke_harness",
                "executor_mode": executor_mode,
                "grammar_topology": effective_topology,
            },
        }
        if reading_goal is not None:
            submit_kwargs["reading_goal"] = reading_goal
        if reading_variant is not None:
            submit_kwargs["reading_variant"] = reading_variant
        submit_result = await self._article_service.submit_plain_text(
            PlainTextArticleReadySubmitRequest(**submit_kwargs)
        )
        runner = self._build_pipeline_runner(
            executor_mode=executor_mode,
            grammar_topology=effective_topology,
        )
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
        grammar_topology: SmokeGrammarTopology = "legacy",
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
        # T1.1: dedicated batch translation worker with a fake batch executor.
        # Bypasses the orchestrator so the batch path can be exercised in
        # smoke / fake mode independently of the per-unit orchestrator path.
        translation_batch_worker = TranslationWorkerService(
            pool=pool,
            batch_translator=DevFakeTranslationBatchExecutor(),
        )
        vocabulary_worker = VocabularyWorkerService(
            pool=pool,
            executor=DevFakeVocabularyExecutor(),
            batch_executor=DevFakeVocabularyBatchExecutor(),
        )
        # T4.2a-R1: always inject the fake batch executor so the compact
        # grammar batch path (SHORT_BATCH / STRUCTURED_BATCH) never falls
        # back to the real ``PydanticAIGrammarBatchExecutor``. The per-unit
        # executor stays as the legacy ``DevFakeGrammarBundleExecutor`` for
        # the ``force_legacy_grammar`` fallback.
        grammar_worker = GrammarBundleWorkerService(
            pool=pool,
            executor=DevFakeGrammarBundleExecutor(),
            batch_executor=DevFakeGrammarBatchExecutor(),
        )
        display_title_worker = DisplayTitleWorkerService(
            pool=pool,
            generator=DevFakeDisplayTitleGenerator(),
        )

        if grammar_topology == "production":
            # T4.2a-R1: faithfully reproduce the production route-aware split.
            # ``enable_zplus_grammar=True`` activates the route-aware bootstrap
            # (SHORT_BATCH/STRUCTURED_BATCH → compact batch, GROUPED_WINDOWED
            # → Z+ window). A fake window executor + real publisher are
            # injected so the GROUPED_WINDOWED path completes without calling
            # the real ``PydanticAIGrammarWindowExecutor``.
            window_worker = GrammarWindowWorkerService(
                pool=pool,
                executor=DevFakeGrammarWindowExecutor(),
            )
            window_publisher = GrammarWindowPublisher(
                pool=pool,
                event_runtime=ReaderEventRuntime(pool=pool),
            )
            return ReaderEnhancementPipelineRunner(
                pool=pool,
                display_title_worker_service=display_title_worker,
                translation_orchestrator=orchestrator,
                translation_batch_worker_service=translation_batch_worker,
                vocabulary_worker_service=vocabulary_worker,
                grammar_worker_service=grammar_worker,
                grammar_window_worker_service=window_worker,
                grammar_window_publisher=window_publisher,
                enable_zplus_grammar=True,
            )

        # legacy topology: 4-worker path, per-unit grammar for all routes.
        return ReaderEnhancementPipelineRunner(
            pool=pool,
            display_title_worker_service=display_title_worker,
            translation_orchestrator=orchestrator,
            translation_batch_worker_service=translation_batch_worker,
            vocabulary_worker_service=vocabulary_worker,
            grammar_worker_service=grammar_worker,
            enable_zplus_grammar=False,
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
