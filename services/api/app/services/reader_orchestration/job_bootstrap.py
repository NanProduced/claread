from __future__ import annotations

import hashlib
from dataclasses import dataclass
from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from app.config.settings import Settings
from app.database import connection as db_connection
from app.database.json_compat import jsonb_param
from app.services.reader_orchestration.document_feature_extractor import (
    ArticleRoute,
    DocumentFeatureProfile,
    classify_article_route,
    extract_document_features,
)
from app.services.reader_orchestration.event_runtime import ReaderEventRuntime
from app.services.reader_orchestration.reading_strategy import (
    ReaderVariantStrategy,
    resolve_reader_variant_strategy,
)
from app.services.reader_orchestration.representation_event_payload import (
    build_representation_payload,
)

TRANSLATION_RUN_TYPE = "translation_layer"
TRANSLATION_JOB_TYPE = "translate_unit"
TRANSLATION_TARGET_SCOPE = "unit"
TRANSLATION_TRIGGER_KIND = "system"
TRANSLATION_POLICY_VERSION = "reader_translation_bootstrap_v1"
TRANSLATION_OPERATION_FINGERPRINT = "translation_unit"
DEFAULT_TRANSLATION_TARGET_LANGUAGE = "zh-CN"
DEFAULT_TRANSLATION_MAX_ATTEMPTS = 3
VOCABULARY_RUN_TYPE = "vocabulary_layer"
VOCABULARY_JOB_TYPE = "build_vocabulary_layer"
VOCABULARY_TARGET_SCOPE = "unit"
VOCABULARY_TRIGGER_KIND = "system"
VOCABULARY_POLICY_VERSION = "reader_vocabulary_bootstrap_v1"
VOCABULARY_OPERATION_FINGERPRINT = "vocabulary_unit_v1"
DEFAULT_VOCABULARY_MAX_ATTEMPTS = 3
GRAMMAR_RUN_TYPE = "grammar_bundle"
GRAMMAR_JOB_TYPE = "build_grammar_bundle"
GRAMMAR_TARGET_SCOPE = "unit"
GRAMMAR_TRIGGER_KIND = "system"
GRAMMAR_POLICY_VERSION = "reader_grammar_bundle_bootstrap_v1"
GRAMMAR_OPERATION_FINGERPRINT = "grammar_bundle_unit_v1"
DEFAULT_GRAMMAR_MAX_ATTEMPTS = 3

# T4.1c compact grammar batch path: SHORT_BATCH and STRUCTURED_BATCH
# articles use a single whole-article grammar batch job instead of the
# heavy Z+ analysis-window path. One LLM call covers all unpublished
# units; the publisher splits the output back into per-unit grammar_note
# / sentence_analysis layers. GROUPED_WINDOWED keeps the Z+ path.
#
# Route-specific fingerprints (T4.1b pattern): STRUCTURED_BATCH gets a
# distinct fingerprint base + policy_version so a route change (short ->
# structured on a rebuilt base) triggers _supersede_stale_fingerprint_jobs.
# SHORT_BATCH keeps the shared ``*_v1`` base.
#
# Job-type reuse: ``GRAMMAR_BATCH_JOB_TYPE`` reuses the existing
# ``build_grammar_bundle`` value (already in the reader_jobs.job_type
# CHECK constraint from migration 0017). Batch and per-unit jobs are
# distinguished by ``target_type`` (``unit_range`` vs ``unit``) and by
# the ``operation_fingerprint`` base, so claim methods never collide.
GRAMMAR_BATCH_JOB_TYPE = GRAMMAR_JOB_TYPE  # "build_grammar_bundle"
GRAMMAR_BATCH_TARGET_SCOPE = "unit_range"
GRAMMAR_BATCH_OPERATION_FINGERPRINT = "grammar_bundle_article_v1"
GRAMMAR_BATCH_POLICY_VERSION = "reader_grammar_batch_bootstrap_v1"
GRAMMAR_STRUCTURED_BATCH_OPERATION_FINGERPRINT = (
    "grammar_bundle_article_structured_v1"
)
GRAMMAR_STRUCTURED_BATCH_POLICY_VERSION = (
    "reader_grammar_batch_structured_bootstrap_v1"
)
DEFAULT_GRAMMAR_BATCH_MAX_ATTEMPTS = 3

DISPLAY_TITLE_RUN_TYPE = "display_title_generation"
DISPLAY_TITLE_JOB_TYPE = "generate_display_title_zh"
DISPLAY_TITLE_TARGET_SCOPE = "record"
DISPLAY_TITLE_TRIGGER_KIND = "system"
DISPLAY_TITLE_POLICY_VERSION = "reader_display_title_bootstrap_v1"
DISPLAY_TITLE_OPERATION_FINGERPRINT = "display_title_zh_v1"
DEFAULT_DISPLAY_TITLE_MAX_ATTEMPTS = 5
_BOOTSTRAP_READY_PRODUCT_STATES = frozenset({"readable_enhancing", "processing"})

# T5.3a semantic outline (optional, request-eligible only; not a budget layer).
SEMANTIC_OUTLINE_RUN_TYPE = "semantic_outline_layer"
SEMANTIC_OUTLINE_JOB_TYPE = "build_semantic_outline"
SEMANTIC_OUTLINE_TARGET_SCOPE = "record"
SEMANTIC_OUTLINE_TARGET_KEY = "document"
SEMANTIC_OUTLINE_TRIGGER_KIND = "system"
SEMANTIC_OUTLINE_POLICY_VERSION = "reader_semantic_outline_bootstrap_v1"
SEMANTIC_OUTLINE_OPERATION_FINGERPRINT = "semantic_outline_document_v1"
SEMANTIC_OUTLINE_INPUT_SHAPE_VERSION = "outline_input_v1"
DEFAULT_SEMANTIC_OUTLINE_MAX_ATTEMPTS = 3
_ARTICLE_READY_READINESS_STATES = frozenset(
    {"article_ready", "initial_enhancement_ready", "coverage_complete"}
)

# T1.1 Short-article batch path: whole-article batch compute, per-unit publish.
# When the active base text is below the short-article char threshold, the
# bootstrap creates a single batch job per layer (translation / vocabulary)
# instead of N per-unit jobs. The batch worker makes one LLM call covering all
# units; the batch publisher splits the output back into per-unit
# enhancement_layers rows so the existing frontend snapshot contract is
# preserved.
#
# Design: docs/initiatives/reader-agentic-orchestration/
# adaptive-reader-orchestration-design.md §6.2 (Short Article Recovery Path).
TRANSLATION_BATCH_JOB_TYPE = "translate_article"
TRANSLATION_BATCH_TARGET_SCOPE = "unit_range"
TRANSLATION_BATCH_OPERATION_FINGERPRINT = "translation_article_v1"
TRANSLATION_BATCH_POLICY_VERSION = "reader_translation_batch_bootstrap_v1"
VOCABULARY_BATCH_JOB_TYPE = "build_vocabulary_layer_article"
VOCABULARY_BATCH_TARGET_SCOPE = "unit_range"
VOCABULARY_BATCH_OPERATION_FINGERPRINT = "vocabulary_article_v1"
VOCABULARY_BATCH_POLICY_VERSION = "reader_vocabulary_batch_bootstrap_v1"

# T4.1b structured article batch: STRUCTURED_BATCH gets its own
# operation_fingerprint base and policy_version so the route is auditable
# at the ``reader_jobs.operation_fingerprint`` / ``reader_runs.policy_version``
# column level, and a route change (short -> structured or vice versa on a
# rebuilt base) triggers ``_supersede_stale_fingerprint_jobs`` to supersede
# old jobs of the other route. SHORT_BATCH and GROUPED_WINDOWED keep their
# existing fingerprints (shared ``_v1`` base) to preserve their idempotency
# contracts; the three-way distinction is completed by ``article_route`` in
# ``input_json`` / ``envelope_json``.
TRANSLATION_STRUCTURED_BATCH_OPERATION_FINGERPRINT = (
    "translation_article_structured_v1"
)
TRANSLATION_STRUCTURED_BATCH_POLICY_VERSION = (
    "reader_translation_batch_structured_bootstrap_v1"
)
VOCABULARY_STRUCTURED_BATCH_OPERATION_FINGERPRINT = (
    "vocabulary_article_structured_v1"
)
VOCABULARY_STRUCTURED_BATCH_POLICY_VERSION = (
    "reader_vocabulary_batch_structured_bootstrap_v1"
)

# Legacy short-article char threshold. Retained as an observability /
# documentation constant (existing tests reference it for fixture sanity
# asserts). T4.1a route hardening replaced it as the sole short/non-short
# discriminator: routing now uses ``estimated_word_count`` as the primary
# signal (see ``document_feature_extractor.SHORT_ARTICLE_MAX_WORD_COUNT``)
# with ``content_utf16_length`` only surviving as a coarse structured-tier
# guardrail. The reuters_bbc_970 golden sample (5982 chars / 984 words)
# stays on the short batch path under the new word-based router.
SHORT_ARTICLE_MAX_CHAR_COUNT = 6000

# T3.2b non-short vocabulary grouped execution: when the active base text
# exceeds SHORT_ARTICLE_MAX_CHAR_COUNT, vocabulary bootstrap splits the
# unpublished units into consecutive windows and creates one
# ``build_vocabulary_layer_article`` batch job per window. Each window is
# bounded by a target char count (close the window once reached) and a
# safety max (never exceed). A single unit larger than safety max becomes
# its own window. The unit is the minimum boundary — units are never split.
VOCABULARY_WINDOW_TARGET_CHAR_COUNT = 3000
VOCABULARY_WINDOW_SAFETY_MAX_CHAR_COUNT = 5000

# T3.1 non-short translation grouped execution: when the active base text
# exceeds SHORT_ARTICLE_MAX_CHAR_COUNT, translation bootstrap splits the
# unpublished units into consecutive windows and creates one
# ``translate_article`` batch job per window. Windows are bounded by a
# target char count (close the window once reached) and a safety max
# (never exceed). A single unit larger than safety max becomes its own
# window. The unit is the minimum boundary — units are never split.
#
# Translation windows are intentionally larger than vocabulary windows
# (T3.2b): translation output is per-group translated_text and needs more
# source context for coherent group planning/hydration. A target of 6000
# chars (one short-article equivalent) yields ~5 LLM calls on a 30k-char
# article instead of ~30 per-unit calls, matching the short-article
# per-char cost profile.
TRANSLATION_WINDOW_TARGET_CHAR_COUNT = 6000
TRANSLATION_WINDOW_SAFETY_MAX_CHAR_COUNT = 10000

# Maps each enhancement job_type to the variant policy layer name it belongs
# to. ``generate_display_title_zh`` has no entry because the display title job
# does not consume a per-layer prompt policy; T5 only records strategy metadata
# and fingerprint coverage. T6/T7/T8 will wire layer prompts into the workers.
_LAYER_NAME_BY_JOB_TYPE: dict[str, str] = {
    TRANSLATION_JOB_TYPE: "translation",
    TRANSLATION_BATCH_JOB_TYPE: "translation",
    VOCABULARY_JOB_TYPE: "vocabulary",
    VOCABULARY_BATCH_JOB_TYPE: "vocabulary",
    GRAMMAR_JOB_TYPE: "grammar_bundle",  # also covers GRAMMAR_BATCH_JOB_TYPE (same value)
}


def _compose_operation_fingerprint(
    base: str,
    strategy: ReaderVariantStrategy,
) -> str:
    """Compose a job operation fingerprint that covers the strategy hash.

    Any change to the resolved variant strategy (goal, variant, profile_id,
    annotation_density, strategy_version, or any layer prompt line) changes
    ``strategy_hash`` and therefore changes the composed fingerprint. This
    ensures that a policy text change does not silently reuse old job output:
    the ``reader_jobs.operation_fingerprint`` column differs, so the
    idempotency NOT EXISTS check treats the new fingerprint as a missing job.
    """
    return f"{base}:{strategy.strategy_hash}"


def _build_strategy_metadata(
    strategy: ReaderVariantStrategy,
    layer_name: str | None,
) -> dict[str, Any]:
    """Build the strategy metadata block recorded on job input/envelope JSON.

    T5 only persists metadata for audit and fingerprinting. It does NOT inject
    ``prompt_lines`` into worker prompts; T6/T7/T8 will read this block to
    resolve the per-layer prompt policy.
    """
    layer_policy_hash: str | None
    if layer_name is not None:
        layer = strategy.layers.get(layer_name)
        if layer is None:
            # Defensive: layer_name comes from _LAYER_NAME_BY_JOB_TYPE and the
            # resolver guarantees all REQUIRED_LAYERS are present. Fail closed
            # if a future code path violates that invariant.
            raise RuntimeError(
                f"resolved strategy has no layer {layer_name!r}"
            )
        layer_policy_hash = layer.policy_hash
    else:
        layer_policy_hash = None
    return {
        "reading_goal": strategy.reading_goal,
        "reading_variant": strategy.reading_variant,
        "strategy_version": strategy.strategy_version,
        "strategy_hash": strategy.strategy_hash,
        "layer_policy_hash": layer_policy_hash,
    }


def _fingerprint_matches_base(fingerprint: str, base: str) -> bool:
    """Check if ``fingerprint`` is exactly ``base`` or ``base + ':' + hash``.

    Boundary-aware matching: ``v1`` must NOT match ``v10`` or ``v1abc``.
    Only an exact match (legacy constant fingerprint) or a ``base:hash``
    composed fingerprint (T5 strategy-aware) is accepted.
    """
    return fingerprint == base or fingerprint.startswith(base + ":")


def _build_document_features_metadata(
    profile: DocumentFeatureProfile,
) -> dict[str, Any]:
    """Build a compact document-features block for ``envelope_json``.

    T4.1b: records the deterministic profile signals that drove the route
    decision so the route is auditable and T4.1c (compact grammar path)
    can read them from ``reader_runs.envelope_json.document_features``
    without re-computing. Only observability-relevant fields are included;
    the full ``DocumentFeatureProfile`` stays in the extractor module.

    Note: this block is written to ``envelope_json`` only, NOT to
    ``reader_jobs.input_json``. ``input_json`` carries ``article_route``
    (the route identity) but not the profile signals; workers that need
    the profile should read it from the run envelope.
    """
    return {
        "estimated_word_count": profile.estimated_word_count,
        "estimated_token_count": profile.estimated_token_count,
        "unit_count": profile.unit_count,
        "paragraph_count": profile.paragraph_count,
        "heading_count": profile.heading_count,
        "structural_noise_ratio": profile.structural_noise_ratio,
        "extractor_version": profile.extractor_version,
    }


def _route_document_features(state: _LockedActiveBaseState) -> dict[str, Any] | None:
    """Return the cached document-features block, or ``None`` if no profile.

    T4.1b: the defensive missing-base path caches no profile, so
    ``envelope_json.document_features`` is ``None`` for that branch. The
    normal path (SHORT_BATCH / STRUCTURED_BATCH / GROUPED_WINDOWED) always
    has a cached profile because ``_load_article_route`` populates
    ``state.cached_profile`` before returning.
    """
    if state.cached_profile is None:
        return None
    return _build_document_features_metadata(state.cached_profile)


# ---------------------------------------------------------------------------#
# T3.2b: Non-short vocabulary batch window planner
# ---------------------------------------------------------------------------#
# Pure dataclasses + function. No DB access, no side effects. The bootstrap
# method loads unit metadata (unit_id, order_index, text_length) and calls
# ``plan_vocabulary_windows`` to get a list of consecutive, non-overlapping
# windows. Each window becomes one ``build_vocabulary_layer_article`` job.
#
# Design constraints (see implementation-plan.md T3.2b):
# - Unit is the minimum boundary; never split a unit across windows.
# - Windows must be consecutive and non-overlapping, ordered by reading order.
# - A single unit larger than safety max becomes its own window.
# - ``window_id`` is a stable hash of the sorted unit_ids in the window, so
#   re-planning after partial publish produces the same window_id for
#   unchanged windows (idempotency relies on this).


@dataclass(frozen=True, slots=True)
class VocabularyWindowUnit:
    """A single unit's metadata for window planning."""

    unit_id: str
    order_index: int
    text_length: int


@dataclass(frozen=True, slots=True)
class VocabularyWindowPlan:
    """A planned vocabulary batch window: a consecutive range of units."""

    units: tuple[VocabularyWindowUnit, ...]

    @property
    def window_id(self) -> str:
        """Stable 12-char hex hash of the sorted unit_ids in this window.

        Two windows with the same unit set produce the same window_id
        regardless of planning order, so idempotency checks on
        ``target_key = f"{record_id}:window:{window_id}"`` correctly
        detect that a window job already exists.
        """
        sorted_ids = ":".join(sorted(u.unit_id for u in self.units))
        return hashlib.sha256(sorted_ids.encode("utf-8")).hexdigest()[:12]

    @property
    def target_unit_ids(self) -> tuple[str, ...]:
        return tuple(u.unit_id for u in self.units)


def plan_vocabulary_windows(
    units: list[VocabularyWindowUnit] | tuple[VocabularyWindowUnit, ...],
    *,
    target_char_count: int = VOCABULARY_WINDOW_TARGET_CHAR_COUNT,
    safety_max_char_count: int = VOCABULARY_WINDOW_SAFETY_MAX_CHAR_COUNT,
) -> list[VocabularyWindowPlan]:
    """Plan vocabulary batch windows for non-short articles.

    Greedy accumulator over units ordered by ``order_index``:

    1. Start a new window with the first remaining unit.
    2. Add the next unit if ``current_chars + next.text_length`` does not
       exceed ``safety_max_char_count``.
    3. If adding would exceed safety max, close the current window and
       start a new one with that unit.
    4. If the current window reaches ``target_char_count``, close it.

    A single unit larger than safety max becomes its own window (step 2
    skips it, step 3 starts a new window, and on the next iteration step 2
    again skips — but the unit is already in the current window from step 1
    or 3, so it closes immediately at step 4 or end-of-list).

    Returns an empty list if ``units`` is empty. Every input unit appears
    in exactly one output window (coverage + no-overlap).
    """
    if not units:
        return []
    sorted_units = sorted(units, key=lambda u: u.order_index)
    windows: list[VocabularyWindowPlan] = []
    current: list[VocabularyWindowUnit] = []
    current_chars = 0
    for unit in sorted_units:
        if not current:
            current.append(unit)
            current_chars = unit.text_length
            continue
        if current_chars + unit.text_length > safety_max_char_count:
            windows.append(VocabularyWindowPlan(units=tuple(current)))
            current = [unit]
            current_chars = unit.text_length
            continue
        current.append(unit)
        current_chars += unit.text_length
        if current_chars >= target_char_count:
            windows.append(VocabularyWindowPlan(units=tuple(current)))
            current = []
            current_chars = 0
    if current:
        windows.append(VocabularyWindowPlan(units=tuple(current)))
    return windows


# ---------------------------------------------------------------------------#
# T3.1: Non-short translation batch window planner
# ---------------------------------------------------------------------------#
# Pure dataclasses + function. No DB access, no side effects. The bootstrap
# method loads unit metadata (unit_id, order_index, text_length) and calls
# ``plan_translation_windows`` to get a list of consecutive, non-overlapping
# windows. Each window becomes one ``translate_article`` batch job.
#
# Design constraints (see implementation-plan.md T3.1):
# - Unit is the minimum boundary; never split a unit across windows.
# - Windows must be consecutive and non-overlapping, ordered by reading order.
# - A single unit larger than safety max becomes its own window.
# - ``window_id`` is a stable hash of the sorted unit_ids in the window, so
#   re-planning after partial publish produces the same window_id for
#   unchanged windows (idempotency relies on this).
# - The translation and vocabulary planners are intentionally separate: each
#   layer has its own default thresholds and its own idempotency namespace
#   (job_type + operation_fingerprint differ, so window_id collisions across
#   layers never cause idempotency false-positives).


@dataclass(frozen=True, slots=True)
class TranslationWindowUnit:
    """A single unit's metadata for translation window planning."""

    unit_id: str
    order_index: int
    text_length: int


@dataclass(frozen=True, slots=True)
class TranslationWindowPlan:
    """A planned translation batch window: a consecutive range of units."""

    units: tuple[TranslationWindowUnit, ...]

    @property
    def window_id(self) -> str:
        """Stable 12-char hex hash of the sorted unit_ids in this window.

        Two windows with the same unit set produce the same window_id
        regardless of planning order, so idempotency checks on
        ``target_key = f"{record_id}:window:{window_id}"`` correctly
        detect that a window job already exists.
        """
        sorted_ids = ":".join(sorted(u.unit_id for u in self.units))
        return hashlib.sha256(sorted_ids.encode("utf-8")).hexdigest()[:12]

    @property
    def target_unit_ids(self) -> tuple[str, ...]:
        return tuple(u.unit_id for u in self.units)


def plan_translation_windows(
    units: list[TranslationWindowUnit] | tuple[TranslationWindowUnit, ...],
    *,
    target_char_count: int = TRANSLATION_WINDOW_TARGET_CHAR_COUNT,
    safety_max_char_count: int = TRANSLATION_WINDOW_SAFETY_MAX_CHAR_COUNT,
) -> list[TranslationWindowPlan]:
    """Plan translation batch windows for non-short articles.

    Greedy accumulator over units ordered by ``order_index``:

    1. Start a new window with the first remaining unit.
    2. Add the next unit if ``current_chars + next.text_length`` does not
       exceed ``safety_max_char_count``.
    3. If adding would exceed safety max, close the current window and
       start a new one with that unit.
    4. If the current window reaches ``target_char_count``, close it.

    A single unit larger than safety max becomes its own window.

    Returns an empty list if ``units`` is empty. Every input unit appears
    in exactly one output window (coverage + no-overlap).
    """
    if not units:
        return []
    sorted_units = sorted(units, key=lambda u: u.order_index)
    windows: list[TranslationWindowPlan] = []
    current: list[TranslationWindowUnit] = []
    current_chars = 0
    for unit in sorted_units:
        if not current:
            current.append(unit)
            current_chars = unit.text_length
            continue
        if current_chars + unit.text_length > safety_max_char_count:
            windows.append(TranslationWindowPlan(units=tuple(current)))
            current = [unit]
            current_chars = unit.text_length
            continue
        current.append(unit)
        current_chars += unit.text_length
        if current_chars >= target_char_count:
            windows.append(TranslationWindowPlan(units=tuple(current)))
            current = []
            current_chars = 0
    if current:
        windows.append(TranslationWindowPlan(units=tuple(current)))
    return windows


# rationale_code written when a queued/retry_later/paused job is superseded
# because its operation_fingerprint no longer matches the current strategy
# fingerprint. Consumed by diagnostics and the pipeline runner's superseded
# counter.
_STRATEGY_FINGERPRINT_SUPERSEDED_RATIONALE = "strategy_fingerprint_superseded"

# rationale_code written when a queued/retry_later/paused legacy per-unit
# ``translate_unit`` job is superseded because the record has switched to the
# T3.1 grouped/window ``translate_article`` path. Without this supersede the
# worker loop would still dispatch the old per-unit job alongside the new
# window jobs, causing duplicate LLM calls or publish-fence conflicts.
_LEGACY_TRANSLATION_PER_UNIT_SUPERSEDED_RATIONALE = (
    "legacy_per_unit_translation_superseded"
)


async def _supersede_stale_fingerprint_jobs(
    conn: asyncpg.Connection,
    *,
    record_id: UUID,
    base_id: UUID,
    expected_generation: int,
    job_type: str,
    target_scope: str,
    current_fingerprint: str,
) -> int:
    """Mark active stale-fingerprint **ordinary-lane** jobs as superseded.

    Before bootstrapping jobs with the current strategy fingerprint, any
    pre-existing ``queued`` / ``retry_later`` / ``paused`` job of the same
    record / base / generation / job_type / target_scope whose
    ``operation_fingerprint`` differs from ``current_fingerprint`` is marked
    ``superseded`` with rationale_code
    ``strategy_fingerprint_superseded``.

    T5.6b: only the **ordinary** translation lane is superseded
    (``request_origin IS DISTINCT FROM 'section_v1'``). Section jobs must
    never be cancelled by ordinary bootstrap fingerprint rotation.

    ``claimed`` and ``succeeded`` jobs are intentionally left untouched:
    a claimed job is being actively processed by a worker, and a succeeded
    job has already published its layer (superseding it would not unpublish
    the layer).

    Returns the number of rows superseded.
    """
    result = await conn.execute(
        """
        UPDATE reader_jobs
        SET status = 'superseded',
            rationale_code = $7,
            lease_owner = NULL,
            lease_token = NULL,
            lease_expires_at = NULL,
            updated_at = NOW()
        WHERE reading_record_id = $1
          AND base_id = $2
          AND expected_generation = $3
          AND job_type = $4
          AND target_type = $5
          AND operation_fingerprint <> $6
          AND status IN ('queued', 'retry_later', 'paused')
          AND (input_json->>'request_origin') IS DISTINCT FROM 'section_v1'
        """,
        record_id,
        base_id,
        expected_generation,
        job_type,
        target_scope,
        current_fingerprint,
        _STRATEGY_FINGERPRINT_SUPERSEDED_RATIONALE,
    )
    # asyncpg execute returns "UPDATE N" where N is the row count.
    count_str = result.split()[-1] if result else "0"
    try:
        return int(count_str)
    except ValueError:
        return 0


async def _supersede_legacy_translation_per_unit_jobs(
    conn: asyncpg.Connection,
    *,
    record_id: UUID,
    base_id: UUID,
    expected_generation: int,
) -> int:
    """T3.1 cutover: supersede active legacy ``translate_unit`` per-unit jobs.

    When a record switches from the legacy per-unit translation path to the
    T3.1 grouped/window ``translate_article`` path, any pre-existing
    ``queued`` / ``retry_later`` / ``paused`` ``translate_unit`` jobs are
    marked ``superseded`` with rationale
    ``legacy_per_unit_translation_superseded`` so the worker loop no longer
    dispatches them alongside the new window jobs.

    ``claimed`` jobs are intentionally left untouched: a claimed job is being
    actively processed by a worker. Grouped bootstrap excludes claimed legacy
    target units from new windows, so the claimed job can finish without
    making a window job fail because one of its units was already published.
    Once the claimed job finishes (success or failure) the next bootstrap
    will either see the published layer (skip) or supersede the
    ``retry_later`` / ``queued`` job.
    """
    result = await conn.execute(
        """
        UPDATE reader_jobs
        SET status = 'superseded',
            rationale_code = $6,
            lease_owner = NULL,
            lease_token = NULL,
            lease_expires_at = NULL,
            updated_at = NOW()
        WHERE reading_record_id = $1
          AND base_id = $2
          AND expected_generation = $3
          AND job_type = $4
          AND target_type = $5
          AND status IN ('queued', 'retry_later', 'paused')
        """,
        record_id,
        base_id,
        expected_generation,
        TRANSLATION_JOB_TYPE,
        TRANSLATION_TARGET_SCOPE,
        _LEGACY_TRANSLATION_PER_UNIT_SUPERSEDED_RATIONALE,
    )
    count_str = result.split()[-1] if result else "0"
    try:
        return int(count_str)
    except ValueError:
        return 0


@dataclass(frozen=True, slots=True)
class TranslationBootstrapResult:
    run_id: UUID
    job_id: UUID
    reading_record_id: UUID
    base_id: UUID
    unit_id: str
    expected_generation: int
    operation_fingerprint: str


@dataclass(frozen=True, slots=True)
class VocabularyBootstrapResult:
    run_id: UUID
    job_id: UUID
    reading_record_id: UUID
    base_id: UUID
    unit_id: str
    expected_generation: int
    operation_fingerprint: str


@dataclass(frozen=True, slots=True)
class GrammarBootstrapResult:
    run_id: UUID
    job_id: UUID
    reading_record_id: UUID
    base_id: UUID
    unit_id: str
    expected_generation: int
    operation_fingerprint: str


@dataclass(frozen=True, slots=True)
class DisplayTitleBootstrapResult:
    run_id: UUID
    job_id: UUID
    reading_record_id: UUID
    base_id: UUID
    expected_generation: int
    operation_fingerprint: str


@dataclass(frozen=True, slots=True)
class EnhancementBootstrapJobCounts:
    display_title: int = 0
    translation: int = 0
    vocabulary: int = 0
    grammar_bundle: int = 0
    semantic_outline: int = 0


@dataclass(frozen=True, slots=True)
class SemanticOutlineBootstrapResult:
    run_id: UUID
    job_id: UUID
    reading_record_id: UUID
    base_id: UUID
    expected_generation: int
    operation_fingerprint: str


@dataclass(frozen=True, slots=True)
class EnhancementBootstrapSummary:
    record_id: UUID
    base_id: UUID
    expected_generation: int
    last_event_sequence: int
    job_counts: EnhancementBootstrapJobCounts
    display_title_results: tuple[DisplayTitleBootstrapResult, ...] = ()
    translation_results: tuple[TranslationBootstrapResult, ...] = ()
    vocabulary_results: tuple[VocabularyBootstrapResult, ...] = ()
    grammar_results: tuple[GrammarBootstrapResult, ...] = ()
    semantic_outline_results: tuple[SemanticOutlineBootstrapResult, ...] = ()


# Injected request eligibility for semantic outline. Default is always-false
# (opt-in). Tests and future product flags inject predicates; length thresholds
# must not be hard-coded as product freezes in this module.
SemanticOutlineRequestEligibility = Callable[["_LockedActiveBaseState"], bool]


def default_semantic_outline_request_eligibility(
    state: "_LockedActiveBaseState",
) -> bool:
    """Default: do not request outline jobs (explicit opt-in only)."""
    return False


def allow_semantic_outline_request_eligibility(
    state: "_LockedActiveBaseState",
) -> bool:
    """Controlled / test DI seam only — never wire as production default.

    Product auto-eligibility thresholds (length, route, cost) remain
    undecided; this predicate is for integration tests and explicit
    operator injection in non-production environments.
    """
    del state  # eligibility is unconditional when explicitly injected
    return True


def settings_aware_semantic_outline_request_eligibility(
    settings: Settings,
) -> SemanticOutlineRequestEligibility:
    """T5.8d-dev-activation: build a request-eligibility predicate from settings.

    Dev-only freeze: ``activation_ready = semantic_outline_generation_enabled
    AND reader_semantic_outline_model_profile != ""``. When ``activation_ready``
    is True, every record that has reached the existing ``article_ready``
    milestone auto-qualifies for semantic outline bootstrap (no extra
    thresholds, no whitelist, no CTA).

    The ``article_ready`` readiness_state gate is enforced separately by
    :func:`_bootstrap_semantic_outline_job` (it short-circuits before
    calling the predicate). This predicate only expresses the
    settings-derived activation flag.

    Committed defaults stay closed
    (``semantic_outline_generation_enabled=False``,
    ``reader_semantic_outline_model_profile=""``) so this predicate returns
    False under default settings; the production composition root is the
    only caller that wires it.
    """
    activation_ready = bool(
        settings.semantic_outline_generation_enabled
    ) and bool(settings.reader_semantic_outline_model_profile)

    def _predicate(_state: "_LockedActiveBaseState") -> bool:
        return activation_ready

    return _predicate


@dataclass(frozen=True, slots=True)
class _LockedActiveBaseState:
    record_id: UUID
    user_id: UUID
    base_id: UUID
    expected_generation: int
    base_language: str
    last_event_sequence: int
    strategy: ReaderVariantStrategy
    readiness_state: str = "submitted"
    # T1.1 short-article batch path: cached active base text. Populated
    # lazily by ``_load_article_route`` so the per-article route classifier
    # does not issue a second ``reading_bases.text`` SELECT when both the
    # translation and vocabulary bootstrap checks run for the same record.
    # ``None`` means "not loaded yet"; an empty string is a valid text.
    base_text: str | None = None
    # T4.1 deterministic document feature extractor: cached ordered
    # ``reading_units.unit_type`` sequence for the active base. Populated
    # lazily by ``_load_article_route`` and reused across the translation
    # and vocabulary route checks. ``None`` means "not loaded yet"; an
    # empty tuple is a valid (defensive) value for a base with no units.
    unit_types: tuple[str, ...] | None = None
    # T4.1a: cached route decision. Once computed by
    # ``_load_article_route``, reused for the second call (vocabulary
    # after translation) so the route is stable within one
    # ``bootstrap_missing_jobs`` invocation. This also fixes the
    # missing-base defensive branch: the first call caches
    # ``GROUPED_WINDOWED``; without this cache the second call would see
    # non-None ``base_text=""`` / ``unit_types=()`` and re-evaluate an
    # empty profile, misclassifying it as ``SHORT_BATCH``.
    cached_route: ArticleRoute | None = None
    # T4.1b: cached document feature profile. Populated alongside
    # ``cached_route`` so the batch bootstrap methods can record
    # ``article_route`` (in ``envelope_json`` + ``input_json``) and
    # ``document_features`` (in ``envelope_json`` only) without
    # re-computing the profile. ``None`` means "not computed yet"
    # (including the defensive missing-base path, where no profile is
    # meaningful).
    cached_profile: DocumentFeatureProfile | None = None


async def _load_article_route(
    conn: asyncpg.Connection,
    *,
    state: _LockedActiveBaseState,
) -> ArticleRoute:
    """Classify the active base into its routing mode via deterministic
    document features (T4.1 / T4.1a).

    Replaces the legacy ``_is_short_article`` raw-``content_utf16_length``
    boolean. The base text and the ordered ``reading_units.unit_type``
    sequence are loaded once each and cached on ``state`` (via
    ``object.__setattr__`` because the dataclass is frozen) so the
    vocabulary route check running right after the translation route check
    reuses the cached values and skips the repeated SELECTs.

    Routing decision is delegated to the pure
    :func:`classify_article_route` classifier, which is fully replayable
    offline from the cached ``base_text`` + ``unit_types`` + ``strategy``.

    Defensive missing-base handling mirrors the legacy ``_is_short_article``
    behavior: if the base row was deleted between the lock and this check,
    return :data:`ArticleRoute.GROUPED_WINDOWED` so the grouped path's own
    base validation surfaces the error (instead of creating a batch job on
    an empty base that would fail later in the worker). The decision is
    cached on ``state.cached_route`` so the second call (vocabulary after
    translation) returns the same route rather than re-evaluating an empty
    profile and misclassifying it as ``SHORT_BATCH``.
    """
    if state.cached_route is not None:
        return state.cached_route
    if state.base_text is None:
        row = await conn.fetchrow(
            "SELECT text FROM reading_bases WHERE id = $1",
            state.base_id,
        )
        if row is None:
            # Defensive: _load_locked_active_base_state already validated
            # the base row. A missing row here means the base was deleted
            # between the lock and this check; cache empty values and route
            # to grouped so its own validation surfaces the error.
            object.__setattr__(state, "base_text", "")
            object.__setattr__(state, "unit_types", ())
            object.__setattr__(
                state, "cached_route", ArticleRoute.GROUPED_WINDOWED
            )
            return ArticleRoute.GROUPED_WINDOWED
        object.__setattr__(state, "base_text", str(row["text"] or ""))
    if state.unit_types is None:
        unit_rows = await conn.fetch(
            """
            SELECT unit_type
            FROM reading_units
            WHERE reading_record_id = $1
              AND base_id = $2
            ORDER BY order_index ASC
            """,
            state.record_id,
            state.base_id,
        )
        object.__setattr__(
            state,
            "unit_types",
            tuple(str(r["unit_type"]) for r in unit_rows),
        )
    profile = extract_document_features(
        base_text=state.base_text,
        unit_types=state.unit_types,
        reading_goal=state.strategy.reading_goal,
        reading_variant=state.strategy.reading_variant,
        requested_layers=tuple(state.strategy.layers.keys()),
    )
    route = classify_article_route(profile)
    object.__setattr__(state, "cached_route", route)
    object.__setattr__(state, "cached_profile", profile)
    return route


class TranslationJobBootstrapService:
    def __init__(self, *, pool: asyncpg.Pool | None = None) -> None:
        self._pool = pool

    def get_pool(self) -> asyncpg.Pool:
        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("Database pool not initialized")
        return pool

    async def bootstrap_translation_run(
        self,
        *,
        record_id: UUID,
        user_id: UUID,
        trace_id: UUID | None = None,
    ) -> TranslationBootstrapResult:
        """Enqueue the first translation job for the active base.

        Uses the shared strategy-aware helpers so the created job carries
        the same strategy metadata (reading_goal / reading_variant /
        strategy_version / strategy_hash / layer_policy_hash) and composed
        operation_fingerprint as ``EnhancementJobBootstrapService``. Any
        stale queued / retry_later / paused translation job whose
        operation_fingerprint no longer matches the current strategy is
        marked superseded before the new job is created.
        """
        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                state = await _load_locked_active_base_state(
                    conn,
                    record_id=record_id,
                    user_id=user_id,
                )
                if trace_id is None:
                    # Caller did not provide a shared trace_id; generate one
                    # so envelope still carries the linkage. Tests that don't
                    # care about tracing can omit the parameter.
                    trace_id = uuid4()
                operation_fingerprint = _compose_operation_fingerprint(
                    TRANSLATION_OPERATION_FINGERPRINT, state.strategy
                )
                await _supersede_stale_fingerprint_jobs(
                    conn,
                    record_id=state.record_id,
                    base_id=state.base_id,
                    expected_generation=state.expected_generation,
                    job_type=TRANSLATION_JOB_TYPE,
                    target_scope=TRANSLATION_TARGET_SCOPE,
                    current_fingerprint=operation_fingerprint,
                )

                unit_row = await conn.fetchrow(
                    """
                    SELECT
                        u.unit_id,
                        u.order_index,
                        u.base_start_utf16,
                        u.base_end_utf16,
                        u.text_hash
                    FROM reading_units u
                    WHERE u.reading_record_id = $1
                      AND u.base_id = $2
                      AND NOT EXISTS (
                          SELECT 1
                          FROM enhancement_layers layer
                          WHERE layer.reading_record_id = u.reading_record_id
                            AND layer.base_id = u.base_id
                            AND layer.generation = $3
                            AND layer.layer_type = 'translation'
                            AND layer.target_scope = 'unit'
                            AND layer.target_key = u.unit_id
                            AND layer.status = 'published'
                      )
                    ORDER BY u.order_index ASC
                    LIMIT 1
                    """,
                    state.record_id,
                    state.base_id,
                    state.expected_generation,
                )
                if unit_row is None:
                    raise ValueError("no untranslated reading unit is available")

                existing_job = await conn.fetchrow(
                    """
                    SELECT id, run_id
                    FROM reader_jobs
                    WHERE reading_record_id = $1
                      AND base_id = $2
                      AND job_type = $3
                      AND target_type = $4
                      AND target_key = $5
                      AND expected_generation = $6
                      AND operation_fingerprint = $7
                      AND status IN ('queued', 'claimed', 'retry_later', 'paused')
                    LIMIT 1
                    """,
                    state.record_id,
                    state.base_id,
                    TRANSLATION_JOB_TYPE,
                    TRANSLATION_TARGET_SCOPE,
                    unit_row["unit_id"],
                    state.expected_generation,
                    operation_fingerprint,
                )
                if existing_job is not None:
                    return TranslationBootstrapResult(
                        run_id=existing_job["run_id"],
                        job_id=existing_job["id"],
                        reading_record_id=state.record_id,
                        base_id=state.base_id,
                        unit_id=str(unit_row["unit_id"]),
                        expected_generation=state.expected_generation,
                        operation_fingerprint=operation_fingerprint,
                    )

                run_id, job_id = await _insert_unit_job(
                    conn,
                    state=state,
                    unit_id=str(unit_row["unit_id"]),
                    unit_order_index=int(unit_row["order_index"]),
                    unit_text_hash=str(unit_row["text_hash"]),
                    run_type=TRANSLATION_RUN_TYPE,
                    job_type=TRANSLATION_JOB_TYPE,
                    target_scope=TRANSLATION_TARGET_SCOPE,
                    policy_version=TRANSLATION_POLICY_VERSION,
                    trigger_kind=TRANSLATION_TRIGGER_KIND,
                    operation_fingerprint=operation_fingerprint,
                    max_attempts=DEFAULT_TRANSLATION_MAX_ATTEMPTS,
                    envelope_json={
                        "record_id": str(state.record_id),
                        "base_id": str(state.base_id),
                        "target_scope": TRANSLATION_TARGET_SCOPE,
                        "target_unit_id": str(unit_row["unit_id"]),
                        "target_language": DEFAULT_TRANSLATION_TARGET_LANGUAGE,
                        "trace_id": str(trace_id),
                    },
                    input_signature_suffix=(
                        f"{state.base_language}:{DEFAULT_TRANSLATION_TARGET_LANGUAGE}"
                    ),
                    input_json={
                        "base_language": state.base_language,
                        "target_language": DEFAULT_TRANSLATION_TARGET_LANGUAGE,
                    },
                    layer_name=_LAYER_NAME_BY_JOB_TYPE[TRANSLATION_JOB_TYPE],
                )

                return TranslationBootstrapResult(
                    run_id=run_id,
                    job_id=job_id,
                    reading_record_id=state.record_id,
                    base_id=state.base_id,
                    unit_id=str(unit_row["unit_id"]),
                    expected_generation=state.expected_generation,
                    operation_fingerprint=operation_fingerprint,
                )


class VocabularyJobBootstrapService:
    def __init__(self, *, pool: asyncpg.Pool | None = None) -> None:
        self._pool = pool

    def get_pool(self) -> asyncpg.Pool:
        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("Database pool not initialized")
        return pool

    async def bootstrap_vocabulary_run(
        self,
        *,
        record_id: UUID,
        user_id: UUID,
        trace_id: UUID | None = None,
    ) -> VocabularyBootstrapResult:
        """Enqueue the first vocabulary job for the active base.

        Uses the shared strategy-aware helpers so the created job carries
        the same strategy metadata (reading_goal / reading_variant /
        strategy_version / strategy_hash / layer_policy_hash) and composed
        operation_fingerprint as ``EnhancementJobBootstrapService``. Any
        stale queued / retry_later / paused vocabulary job whose
        operation_fingerprint no longer matches the current strategy is
        marked superseded before the new job is created.
        """
        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                state = await _load_locked_active_base_state(
                    conn,
                    record_id=record_id,
                    user_id=user_id,
                )
                if trace_id is None:
                    trace_id = uuid4()
                operation_fingerprint = _compose_operation_fingerprint(
                    VOCABULARY_OPERATION_FINGERPRINT, state.strategy
                )
                await _supersede_stale_fingerprint_jobs(
                    conn,
                    record_id=state.record_id,
                    base_id=state.base_id,
                    expected_generation=state.expected_generation,
                    job_type=VOCABULARY_JOB_TYPE,
                    target_scope=VOCABULARY_TARGET_SCOPE,
                    current_fingerprint=operation_fingerprint,
                )

                unit_row = await conn.fetchrow(
                    """
                    SELECT
                        u.unit_id,
                        u.order_index,
                        u.text_hash
                    FROM reading_units u
                    WHERE u.reading_record_id = $1
                      AND u.base_id = $2
                      AND NOT EXISTS (
                          SELECT 1
                          FROM enhancement_layers layer
                          WHERE layer.reading_record_id = u.reading_record_id
                            AND layer.base_id = u.base_id
                            AND layer.generation = $3
                            AND layer.layer_type = 'vocabulary'
                            AND layer.target_scope = 'unit'
                            AND layer.target_key = u.unit_id
                            AND layer.status = 'published'
                      )
                    ORDER BY u.order_index ASC
                    LIMIT 1
                    """,
                    state.record_id,
                    state.base_id,
                    state.expected_generation,
                )
                if unit_row is None:
                    raise ValueError("no unprocessed vocabulary reading unit is available")

                existing_job = await conn.fetchrow(
                    """
                    SELECT id, run_id
                    FROM reader_jobs
                    WHERE reading_record_id = $1
                      AND base_id = $2
                      AND job_type = $3
                      AND target_type = $4
                      AND target_key = $5
                      AND expected_generation = $6
                      AND operation_fingerprint = $7
                      AND status IN ('queued', 'claimed', 'retry_later', 'paused')
                    LIMIT 1
                    """,
                    state.record_id,
                    state.base_id,
                    VOCABULARY_JOB_TYPE,
                    VOCABULARY_TARGET_SCOPE,
                    unit_row["unit_id"],
                    state.expected_generation,
                    operation_fingerprint,
                )
                if existing_job is not None:
                    return VocabularyBootstrapResult(
                        run_id=existing_job["run_id"],
                        job_id=existing_job["id"],
                        reading_record_id=state.record_id,
                        base_id=state.base_id,
                        unit_id=str(unit_row["unit_id"]),
                        expected_generation=state.expected_generation,
                        operation_fingerprint=operation_fingerprint,
                    )

                run_id, job_id = await _insert_unit_job(
                    conn,
                    state=state,
                    unit_id=str(unit_row["unit_id"]),
                    unit_order_index=int(unit_row["order_index"]),
                    unit_text_hash=str(unit_row["text_hash"]),
                    run_type=VOCABULARY_RUN_TYPE,
                    job_type=VOCABULARY_JOB_TYPE,
                    target_scope=VOCABULARY_TARGET_SCOPE,
                    policy_version=VOCABULARY_POLICY_VERSION,
                    trigger_kind=VOCABULARY_TRIGGER_KIND,
                    operation_fingerprint=operation_fingerprint,
                    max_attempts=DEFAULT_VOCABULARY_MAX_ATTEMPTS,
                    envelope_json={
                        "record_id": str(state.record_id),
                        "base_id": str(state.base_id),
                        "target_scope": VOCABULARY_TARGET_SCOPE,
                        "target_unit_id": str(unit_row["unit_id"]),
                        "layer_type": "vocabulary",
                        "trace_id": str(trace_id),
                    },
                    input_signature_suffix=f"{state.base_language}:vocabulary:1",
                    input_json={
                        "base_language": state.base_language,
                        "layer_type": "vocabulary",
                    },
                    layer_name=_LAYER_NAME_BY_JOB_TYPE[VOCABULARY_JOB_TYPE],
                )

                return VocabularyBootstrapResult(
                    run_id=run_id,
                    job_id=job_id,
                    reading_record_id=state.record_id,
                    base_id=state.base_id,
                    unit_id=str(unit_row["unit_id"]),
                    expected_generation=state.expected_generation,
                    operation_fingerprint=operation_fingerprint,
                )


class GrammarJobBootstrapService:
    def __init__(self, *, pool: asyncpg.Pool | None = None) -> None:
        self._pool = pool

    def get_pool(self) -> asyncpg.Pool:
        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("Database pool not initialized")
        return pool

    async def bootstrap_grammar_run(
        self,
        *,
        record_id: UUID,
        user_id: UUID,
        trace_id: UUID | None = None,
    ) -> GrammarBootstrapResult:
        """Enqueue the first grammar bundle job for the active base.

        Uses the shared strategy-aware helpers so the created job carries
        the same strategy metadata (reading_goal / reading_variant /
        strategy_version / strategy_hash / layer_policy_hash) and composed
        operation_fingerprint as ``EnhancementJobBootstrapService``. Any
        stale queued / retry_later / paused grammar job whose
        operation_fingerprint no longer matches the current strategy is
        marked superseded before the new job is created.
        """
        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                state = await _load_locked_active_base_state(
                    conn,
                    record_id=record_id,
                    user_id=user_id,
                )
                if trace_id is None:
                    trace_id = uuid4()
                operation_fingerprint = _compose_operation_fingerprint(
                    GRAMMAR_OPERATION_FINGERPRINT, state.strategy
                )
                await _supersede_stale_fingerprint_jobs(
                    conn,
                    record_id=state.record_id,
                    base_id=state.base_id,
                    expected_generation=state.expected_generation,
                    job_type=GRAMMAR_JOB_TYPE,
                    target_scope=GRAMMAR_TARGET_SCOPE,
                    current_fingerprint=operation_fingerprint,
                )

                unit_row = await conn.fetchrow(
                    """
                    SELECT
                        u.unit_id,
                        u.order_index,
                        u.text_hash
                    FROM reading_units u
                    WHERE u.reading_record_id = $1
                      AND u.base_id = $2
                      AND NOT EXISTS (
                          SELECT 1
                          FROM enhancement_layers layer
                          WHERE layer.reading_record_id = u.reading_record_id
                            AND layer.base_id = u.base_id
                            AND layer.generation = $3
                            AND layer.layer_type IN ('grammar_note', 'sentence_analysis')
                            AND layer.target_scope = 'unit'
                            AND layer.target_key = u.unit_id
                            AND layer.status = 'published'
                      )
                      AND NOT EXISTS (
                          SELECT 1
                          FROM reader_jobs job
                          WHERE job.reading_record_id = u.reading_record_id
                            AND job.base_id = u.base_id
                            AND job.job_type = $4
                            AND job.target_type = $5
                            AND job.target_key = u.unit_id
                            AND job.expected_generation = $3
                            AND job.operation_fingerprint = $6
                            AND job.status = 'succeeded'
                      )
                    ORDER BY u.order_index ASC
                    LIMIT 1
                    """,
                    state.record_id,
                    state.base_id,
                    state.expected_generation,
                    GRAMMAR_JOB_TYPE,
                    GRAMMAR_TARGET_SCOPE,
                    operation_fingerprint,
                )
                if unit_row is None:
                    raise ValueError("no unprocessed grammar reading unit is available")

                existing_job = await conn.fetchrow(
                    """
                    SELECT id, run_id
                    FROM reader_jobs
                    WHERE reading_record_id = $1
                      AND base_id = $2
                      AND job_type = $3
                      AND target_type = $4
                      AND target_key = $5
                      AND expected_generation = $6
                      AND operation_fingerprint = $7
                      AND status IN ('queued', 'claimed', 'retry_later', 'paused')
                    LIMIT 1
                    """,
                    state.record_id,
                    state.base_id,
                    GRAMMAR_JOB_TYPE,
                    GRAMMAR_TARGET_SCOPE,
                    unit_row["unit_id"],
                    state.expected_generation,
                    operation_fingerprint,
                )
                if existing_job is not None:
                    return GrammarBootstrapResult(
                        run_id=existing_job["run_id"],
                        job_id=existing_job["id"],
                        reading_record_id=state.record_id,
                        base_id=state.base_id,
                        unit_id=str(unit_row["unit_id"]),
                        expected_generation=state.expected_generation,
                        operation_fingerprint=operation_fingerprint,
                    )

                run_id, job_id = await _insert_unit_job(
                    conn,
                    state=state,
                    unit_id=str(unit_row["unit_id"]),
                    unit_order_index=int(unit_row["order_index"]),
                    unit_text_hash=str(unit_row["text_hash"]),
                    run_type=GRAMMAR_RUN_TYPE,
                    job_type=GRAMMAR_JOB_TYPE,
                    target_scope=GRAMMAR_TARGET_SCOPE,
                    policy_version=GRAMMAR_POLICY_VERSION,
                    trigger_kind=GRAMMAR_TRIGGER_KIND,
                    operation_fingerprint=operation_fingerprint,
                    max_attempts=DEFAULT_GRAMMAR_MAX_ATTEMPTS,
                    envelope_json={
                        "record_id": str(state.record_id),
                        "base_id": str(state.base_id),
                        "target_scope": GRAMMAR_TARGET_SCOPE,
                        "target_unit_id": str(unit_row["unit_id"]),
                        "layer_types": ["grammar_note", "sentence_analysis"],
                        "trace_id": str(trace_id),
                    },
                    input_signature_suffix=f"{state.base_language}:grammar_bundle:1",
                    input_json={
                        "base_language": state.base_language,
                        "layer_types": ["grammar_note", "sentence_analysis"],
                    },
                    layer_name=_LAYER_NAME_BY_JOB_TYPE[GRAMMAR_JOB_TYPE],
                )

                return GrammarBootstrapResult(
                    run_id=run_id,
                    job_id=job_id,
                    reading_record_id=state.record_id,
                    base_id=state.base_id,
                    unit_id=str(unit_row["unit_id"]),
                    expected_generation=state.expected_generation,
                    operation_fingerprint=operation_fingerprint,
                )


class DisplayTitleJobBootstrapService:
    def __init__(self, *, pool: asyncpg.Pool | None = None) -> None:
        self._pool = pool

    def get_pool(self) -> asyncpg.Pool:
        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("Database pool not initialized")
        return pool

    async def bootstrap_display_title_job(
        self,
        *,
        record_id: UUID,
        user_id: UUID,
        trace_id: UUID | None = None,
    ) -> DisplayTitleBootstrapResult | None:
        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                state = await _load_locked_active_base_state(
                    conn,
                    record_id=record_id,
                    user_id=user_id,
                )
                if trace_id is None:
                    trace_id = uuid4()
                results = await _bootstrap_display_title_job(
                    conn, state=state, trace_id=trace_id
                )
        return results[0] if results else None


class EnhancementJobBootstrapService:
    def __init__(
        self,
        *,
        pool: asyncpg.Pool | None = None,
        semantic_outline_request_eligibility: SemanticOutlineRequestEligibility
        | None = None,
    ) -> None:
        self._pool = pool
        self._semantic_outline_request_eligibility = (
            semantic_outline_request_eligibility
            or default_semantic_outline_request_eligibility
        )

    def get_pool(self) -> asyncpg.Pool:
        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("Database pool not initialized")
        return pool

    async def bootstrap_missing_jobs(
        self,
        *,
        record_id: UUID,
        user_id: UUID,
        trace_id: UUID | None = None,
        force_legacy_grammar: bool = False,
    ) -> EnhancementBootstrapSummary:
        use_zplus_grammar_path = False
        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                state = await _load_locked_active_base_state(
                    conn,
                    record_id=record_id,
                    user_id=user_id,
                )
                if trace_id is None:
                    trace_id = uuid4()
                display_title_results = await _bootstrap_display_title_job(
                    conn,
                    state=state,
                    trace_id=trace_id,
                )
                translation_results = await self._bootstrap_translation_jobs(
                    conn,
                    state=state,
                    trace_id=trace_id,
                )
                vocabulary_results = await self._bootstrap_vocabulary_jobs(
                    conn,
                    state=state,
                    trace_id=trace_id,
                )
                grammar_results, use_zplus_grammar_path = (
                    await self._bootstrap_grammar_jobs_or_zplus(
                        conn,
                        state=state,
                        trace_id=trace_id,
                        force_legacy_grammar=force_legacy_grammar,
                    )
                )
                semantic_outline_results = await self._bootstrap_semantic_outline_job(
                    conn,
                    state=state,
                    trace_id=trace_id,
                )

        # Z+ path: dispatch to ZPlusBootstrapService AFTER the outer
        # transaction commits. ZPlusBootstrapService.bootstrap_grammar_window_plan
        # opens its own transaction and acquires its own FOR UPDATE lock on
        # reading_records, so calling it inside the outer transaction would
        # deadlock against the lock we already hold. Idempotent: if the plan
        # already exists with its windows/jobs, it is reused as-is.
        # Design: analysis-window-zplus-design.md §9 worker migration.
        # Pass the same trace_id used by display/translation/vocab runs so
        # window reader_runs.envelope_json carries the shared trace root
        # (requirement 5: same-record runs share one trace_id).
        if use_zplus_grammar_path:
            from .zplus_bootstrap import ZPlusBootstrapService

            zplus_service = ZPlusBootstrapService(pool=self._pool)
            await zplus_service.bootstrap_grammar_window_plan(
                record_id=state.record_id,
                base_id=state.base_id,
                trace_id=trace_id,
            )

        return EnhancementBootstrapSummary(
            record_id=state.record_id,
            base_id=state.base_id,
            expected_generation=state.expected_generation,
            last_event_sequence=state.last_event_sequence,
            job_counts=EnhancementBootstrapJobCounts(
                display_title=len(display_title_results),
                translation=len(translation_results),
                vocabulary=len(vocabulary_results),
                grammar_bundle=len(grammar_results),
                semantic_outline=len(semantic_outline_results),
            ),
            display_title_results=tuple(display_title_results),
            translation_results=tuple(translation_results),
            vocabulary_results=tuple(vocabulary_results),
            grammar_results=tuple(grammar_results),
            semantic_outline_results=tuple(semantic_outline_results),
        )

    async def bootstrap_semantic_outline_job(
        self,
        *,
        record_id: UUID,
        user_id: UUID,
        trace_id: UUID | None = None,
    ) -> SemanticOutlineBootstrapResult | None:
        """Bootstrap a single outline job when request-eligible + article_ready."""
        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                state = await _load_locked_active_base_state(
                    conn,
                    record_id=record_id,
                    user_id=user_id,
                )
                if trace_id is None:
                    trace_id = uuid4()
                results = await self._bootstrap_semantic_outline_job(
                    conn, state=state, trace_id=trace_id
                )
        return results[0] if results else None

    async def _bootstrap_semantic_outline_job(
        self,
        conn: asyncpg.Connection,
        *,
        state: _LockedActiveBaseState,
        trace_id: UUID | None = None,
    ) -> list[SemanticOutlineBootstrapResult]:
        return await _bootstrap_semantic_outline_job(
            conn,
            state=state,
            trace_id=trace_id,
            request_eligibility=self._semantic_outline_request_eligibility,
        )

    async def _bootstrap_translation_jobs(
        self,
        conn: asyncpg.Connection,
        *,
        state: _LockedActiveBaseState,
        trace_id: UUID | None = None,
    ) -> list[TranslationBootstrapResult]:
        # T4.1a route hardening: classify via deterministic document
        # features (estimated_word_count primary, content_utf16_length as a
        # coarse structured-tier guardrail) instead of the legacy raw
        # ``content_utf16_length`` boolean.
        # T4.1b: SHORT_BATCH and STRUCTURED_BATCH both execute via the
        # whole-article batch job, but with distinct operation_fingerprint
        # / policy_version / input_json.article_route so the route is
        # auditable and a route change supersedes old jobs.
        # GROUPED_WINDOWED splits into per-window batch jobs.
        route = await _load_article_route(conn, state=state)
        if route is not ArticleRoute.GROUPED_WINDOWED:
            return await self._bootstrap_translation_batch_job(
                conn, state=state, route=route, trace_id=trace_id
            )
        # T3.1 non-short grouped path: split unpublished units into
        # consecutive windows and create one ``translate_article`` batch
        # job per window. Replaces the legacy per-unit ``translate_unit``
        # path which caused 50+ LLM calls on ~30k-char articles.
        return await self._bootstrap_translation_grouped_jobs(
            conn, state=state, route=route, trace_id=trace_id
        )

    async def _bootstrap_vocabulary_jobs(
        self,
        conn: asyncpg.Connection,
        *,
        state: _LockedActiveBaseState,
        trace_id: UUID | None = None,
    ) -> list[VocabularyBootstrapResult]:
        # T4.1a route hardening: classify via deterministic document
        # features (see ``_bootstrap_translation_jobs``).
        # T4.1b: SHORT_BATCH and STRUCTURED_BATCH both execute via the
        # whole-article vocabulary batch job, but with distinct
        # operation_fingerprint / policy_version / input_json.article_route.
        # GROUPED_WINDOWED splits into per-window batch jobs.
        route = await _load_article_route(conn, state=state)
        if route is not ArticleRoute.GROUPED_WINDOWED:
            return await self._bootstrap_vocabulary_batch_job(
                conn, state=state, route=route, trace_id=trace_id
            )
        # T3.2b non-short grouped path: split unpublished units into
        # consecutive windows and create one batch job per window.
        return await self._bootstrap_vocabulary_grouped_jobs(
            conn, state=state, route=route, trace_id=trace_id
        )

    async def _bootstrap_vocabulary_grouped_jobs(
        self,
        conn: asyncpg.Connection,
        *,
        state: _LockedActiveBaseState,
        route: ArticleRoute,
        trace_id: UUID | None = None,
    ) -> list[VocabularyBootstrapResult]:
        """T3.2b: non-short vocabulary grouped/window execution.

        Queries unpublished units (ordered by ``order_index``), plans
        consecutive windows via :func:`plan_vocabulary_windows`, and
        creates one ``build_vocabulary_layer_article`` batch job per
        window. Each window job has a distinct ``target_key`` /
        ``idempotency_key`` / ``input_hash`` so multiple windows on the
        same record do not collide.

        T4.1b route identity: ``route`` is recorded as ``article_route``
        in ``envelope_json`` / ``input_json`` for audit consistency with
        the batch path. GROUPED_WINDOWED keeps its existing
        ``vocabulary_article_v1`` fingerprint base (shared with
        SHORT_BATCH) so its idempotency contract is preserved; the
        three-way distinction is completed by ``article_route`` in
        ``input_json``.

        Cross-window duplicate headword policy (v1): each window may
        independently highlight the same headword once. Cross-window
        dedup is NOT performed; this is acceptable for v1 and is locked
        by tests. See implementation-plan.md T3.2b risk A.
        """
        if trace_id is None:
            trace_id = uuid4()
        operation_fingerprint = _compose_operation_fingerprint(
            VOCABULARY_BATCH_OPERATION_FINGERPRINT, state.strategy
        )
        await _supersede_stale_fingerprint_jobs(
            conn,
            record_id=state.record_id,
            base_id=state.base_id,
            expected_generation=state.expected_generation,
            job_type=VOCABULARY_BATCH_JOB_TYPE,
            target_scope=VOCABULARY_BATCH_TARGET_SCOPE,
            current_fingerprint=operation_fingerprint,
        )
        # Load unpublished units with their UTF-16 char length for windowing.
        # ``base_end_utf16 - base_start_utf16`` matches the worker's
        # ``slice_by_utf16_offsets`` unit text length exactly.
        rows = await conn.fetch(
            """
            SELECT
                u.unit_id,
                u.order_index,
                u.base_start_utf16,
                u.base_end_utf16
            FROM reading_units u
            WHERE u.reading_record_id = $1
              AND u.base_id = $2
              AND NOT EXISTS (
                  SELECT 1
                  FROM enhancement_layers layer
                  WHERE layer.reading_record_id = u.reading_record_id
                    AND layer.base_id = u.base_id
                    AND layer.generation = $3
                    AND layer.layer_type = 'vocabulary'
                    AND layer.target_scope = 'unit'
                    AND layer.target_key = u.unit_id
                    AND layer.status = 'published'
              )
            ORDER BY u.order_index ASC
            """,
            state.record_id,
            state.base_id,
            state.expected_generation,
        )
        if not rows:
            return []
        window_units = [
            VocabularyWindowUnit(
                unit_id=str(row["unit_id"]),
                order_index=int(row["order_index"]),
                text_length=int(row["base_end_utf16"]) - int(row["base_start_utf16"]),
            )
            for row in rows
        ]
        windows = plan_vocabulary_windows(window_units)
        results: list[VocabularyBootstrapResult] = []
        for window in windows:
            target_unit_ids = list(window.target_unit_ids)
            window_target_key = f"{state.record_id}:window:{window.window_id}"
            # Per-window idempotency: skip if an active job already exists
            # for this window's target_key + fingerprint.
            existing_job = await conn.fetchrow(
                """
                SELECT id, run_id
                FROM reader_jobs
                WHERE reading_record_id = $1
                  AND base_id = $2
                  AND job_type = $3
                  AND target_type = $4
                  AND target_key = $5
                  AND expected_generation = $6
                  AND operation_fingerprint = $7
                  AND status IN ('queued', 'claimed', 'retry_later', 'paused', 'succeeded')
                LIMIT 1
                """,
                state.record_id,
                state.base_id,
                VOCABULARY_BATCH_JOB_TYPE,
                VOCABULARY_BATCH_TARGET_SCOPE,
                window_target_key,
                state.expected_generation,
                operation_fingerprint,
            )
            if existing_job is not None:
                continue
            run_id, job_id = await _insert_unit_range_job(
                conn,
                state=state,
                run_type=VOCABULARY_RUN_TYPE,
                job_type=VOCABULARY_BATCH_JOB_TYPE,
                target_scope=VOCABULARY_BATCH_TARGET_SCOPE,
                policy_version=VOCABULARY_BATCH_POLICY_VERSION,
                trigger_kind=VOCABULARY_TRIGGER_KIND,
                operation_fingerprint=operation_fingerprint,
                max_attempts=DEFAULT_VOCABULARY_MAX_ATTEMPTS,
                envelope_json={
                    "record_id": str(state.record_id),
                    "base_id": str(state.base_id),
                    "target_scope": VOCABULARY_BATCH_TARGET_SCOPE,
                    "target_unit_ids": target_unit_ids,
                    "layer_type": "vocabulary",
                    "trace_id": str(trace_id),
                    "window_id": window.window_id,
                    "article_route": route.value,
                    "document_features": _route_document_features(state),
                },
                input_signature_suffix=(
                    f"{state.base_language}:vocabulary:window:{window.window_id}:1:batch"
                ),
                input_json={
                    "target_scope": VOCABULARY_BATCH_TARGET_SCOPE,
                    "target_unit_ids": target_unit_ids,
                    "base_language": state.base_language,
                    "layer_type": "vocabulary",
                    "window_id": window.window_id,
                    "article_route": route.value,
                },
                layer_name=_LAYER_NAME_BY_JOB_TYPE[VOCABULARY_BATCH_JOB_TYPE],
                target_key_override=window_target_key,
                idempotency_key_suffix=f"window:{window.window_id}",
            )
            results.append(
                VocabularyBootstrapResult(
                    run_id=run_id,
                    job_id=job_id,
                    reading_record_id=state.record_id,
                    base_id=state.base_id,
                    unit_id=target_unit_ids[0],
                    expected_generation=state.expected_generation,
                    operation_fingerprint=operation_fingerprint,
                )
            )
        return results

    async def _bootstrap_grammar_jobs(
        self,
        conn: asyncpg.Connection,
        *,
        state: _LockedActiveBaseState,
        trace_id: UUID | None = None,
    ) -> list[GrammarBootstrapResult]:
        if trace_id is None:
            trace_id = uuid4()
        operation_fingerprint = _compose_operation_fingerprint(
            GRAMMAR_OPERATION_FINGERPRINT, state.strategy
        )
        await _supersede_stale_fingerprint_jobs(
            conn,
            record_id=state.record_id,
            base_id=state.base_id,
            expected_generation=state.expected_generation,
            job_type=GRAMMAR_JOB_TYPE,
            target_scope=GRAMMAR_TARGET_SCOPE,
            current_fingerprint=operation_fingerprint,
        )
        rows = await conn.fetch(
            """
            SELECT
                u.unit_id,
                u.order_index,
                u.text_hash
            FROM reading_units u
            WHERE u.reading_record_id = $1
              AND u.base_id = $2
              AND NOT EXISTS (
                  SELECT 1
                  FROM enhancement_layers layer
                  WHERE layer.reading_record_id = u.reading_record_id
                    AND layer.base_id = u.base_id
                    AND layer.generation = $3
                    AND layer.layer_type IN ('grammar_note', 'sentence_analysis')
                    AND layer.target_scope = 'unit'
                    AND layer.target_key = u.unit_id
                    AND layer.status = 'published'
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM reader_jobs job
                  WHERE job.reading_record_id = u.reading_record_id
                    AND job.base_id = u.base_id
                    AND job.job_type = $4
                    AND job.target_type = $5
                    AND job.target_key = u.unit_id
                    AND job.expected_generation = $3
                    AND job.operation_fingerprint = $6
                    AND job.status IN ('queued', 'claimed', 'retry_later', 'paused', 'succeeded')
              )
            ORDER BY u.order_index ASC
            """,
            state.record_id,
            state.base_id,
            state.expected_generation,
            GRAMMAR_JOB_TYPE,
            GRAMMAR_TARGET_SCOPE,
            operation_fingerprint,
        )
        results: list[GrammarBootstrapResult] = []
        for row in rows:
            run_id, job_id = await _insert_unit_job(
                conn,
                state=state,
                unit_id=str(row["unit_id"]),
                unit_order_index=int(row["order_index"]),
                unit_text_hash=str(row["text_hash"]),
                run_type=GRAMMAR_RUN_TYPE,
                job_type=GRAMMAR_JOB_TYPE,
                target_scope=GRAMMAR_TARGET_SCOPE,
                policy_version=GRAMMAR_POLICY_VERSION,
                trigger_kind=GRAMMAR_TRIGGER_KIND,
                operation_fingerprint=operation_fingerprint,
                max_attempts=DEFAULT_GRAMMAR_MAX_ATTEMPTS,
                envelope_json={
                    "record_id": str(state.record_id),
                    "base_id": str(state.base_id),
                    "target_scope": GRAMMAR_TARGET_SCOPE,
                    "target_unit_id": str(row["unit_id"]),
                    "layer_types": ["grammar_note", "sentence_analysis"],
                    "trace_id": str(trace_id),
                },
                input_signature_suffix=f"{state.base_language}:grammar_bundle:1",
                input_json={
                    "unit_id": str(row["unit_id"]),
                    "unit_order_index": int(row["order_index"]),
                    "unit_text_hash": str(row["text_hash"]),
                    "base_language": state.base_language,
                    "layer_types": ["grammar_note", "sentence_analysis"],
                },
                layer_name=_LAYER_NAME_BY_JOB_TYPE[GRAMMAR_JOB_TYPE],
            )
            results.append(
                GrammarBootstrapResult(
                    run_id=run_id,
                    job_id=job_id,
                    reading_record_id=state.record_id,
                    base_id=state.base_id,
                    unit_id=str(row["unit_id"]),
                    expected_generation=state.expected_generation,
                    operation_fingerprint=operation_fingerprint,
                )
            )
        return results

    async def _bootstrap_grammar_jobs_or_zplus(
        self,
        conn: asyncpg.Connection,
        *,
        state: _LockedActiveBaseState,
        trace_id: UUID | None = None,
        force_legacy_grammar: bool = False,
    ) -> tuple[list[GrammarBootstrapResult], bool]:
        """Route-aware grammar bootstrap routing (T4.1c).

        Three-way split:

        - ``force_legacy_grammar=True`` → legacy per-unit
          ``_bootstrap_grammar_jobs`` (fallback, returns ``([], False)``).
        - ``GROUPED_WINDOWED`` → Z+ analysis-window path (returns
          ``([], True)``; caller dispatches to
          ``ZPlusBootstrapService.bootstrap_grammar_window_plan`` after
          the outer transaction commits). Long-article grammar contract
          is unchanged.
        - ``SHORT_BATCH`` / ``STRUCTURED_BATCH`` → compact grammar batch
          path (returns ``(results, False)``). One
          ``build_grammar_bundle`` / ``unit_range`` batch job covers all
          unpublished units in a single LLM call; no
          ``analysis_windows`` / ``layer_analysis_plans`` are created.

        Design: docs/initiatives/reader-agentic-orchestration/
        adaptive-reader-orchestration-design.md §6.3 / §4.2.
        """
        if force_legacy_grammar:
            results = await self._bootstrap_grammar_jobs(
                conn,
                state=state,
                trace_id=trace_id,
            )
            return results, False
        # T4.1c: route-aware split. GROUPED_WINDOWED keeps the Z+ path;
        # SHORT_BATCH / STRUCTURED_BATCH use the compact batch path.
        route = await _load_article_route(conn, state=state)
        if route is ArticleRoute.GROUPED_WINDOWED:
            # Z+ path. ZPlusBootstrapService 在外层事务提交后被调用，
            # 其内部幂等：plan 已存在时直接复用。
            return [], True
        # Compact grammar batch path for SHORT_BATCH / STRUCTURED_BATCH.
        results = await self._bootstrap_grammar_batch_job(
            conn,
            state=state,
            route=route,
            trace_id=trace_id,
        )
        return results, False

    async def _bootstrap_grammar_batch_job(
        self,
        conn: asyncpg.Connection,
        *,
        state: _LockedActiveBaseState,
        route: ArticleRoute,
        trace_id: UUID | None = None,
    ) -> list[GrammarBootstrapResult]:
        """T4.1c: compact grammar batch bootstrap for short/structured articles.

        Creates a single ``build_grammar_bundle`` / ``unit_range``
        reader job whose ``input_json.target_unit_ids`` lists every unit
        that still needs a grammar layer. The batch worker makes one LLM
        call covering all units; the batch publisher splits the output
        back into per-unit ``enhancement_layers`` rows.

        T4.1c route identity: ``route`` selects the operation_fingerprint
        base and policy_version. ``STRUCTURED_BATCH`` gets a distinct
        fingerprint so a route change (short -> structured on a rebuilt
        base) triggers ``_supersede_stale_fingerprint_jobs``. Both
        ``SHORT_BATCH`` and ``STRUCTURED_BATCH`` record ``article_route``
        in ``envelope_json`` and ``input_json``; ``document_features``
        is recorded in ``envelope_json`` only (workers needing the
        profile read it from the run envelope).

        No ``analysis_windows`` / ``layer_analysis_plans`` are created —
        this is the key cost/latency win over the Z+ path for short and
        medium articles.
        """
        if trace_id is None:
            trace_id = uuid4()
        if route is ArticleRoute.STRUCTURED_BATCH:
            fingerprint_base = GRAMMAR_STRUCTURED_BATCH_OPERATION_FINGERPRINT
            policy_version = GRAMMAR_STRUCTURED_BATCH_POLICY_VERSION
            route_suffix = "structured"
        else:
            fingerprint_base = GRAMMAR_BATCH_OPERATION_FINGERPRINT
            policy_version = GRAMMAR_BATCH_POLICY_VERSION
            route_suffix = "short"
        operation_fingerprint = _compose_operation_fingerprint(
            fingerprint_base, state.strategy
        )
        await _supersede_stale_fingerprint_jobs(
            conn,
            record_id=state.record_id,
            base_id=state.base_id,
            expected_generation=state.expected_generation,
            job_type=GRAMMAR_BATCH_JOB_TYPE,
            target_scope=GRAMMAR_BATCH_TARGET_SCOPE,
            current_fingerprint=operation_fingerprint,
        )
        rows = await conn.fetch(
            """
            SELECT u.unit_id, u.order_index, u.text_hash
            FROM reading_units u
            WHERE u.reading_record_id = $1
              AND u.base_id = $2
              AND NOT EXISTS (
                  SELECT 1
                  FROM enhancement_layers layer
                  WHERE layer.reading_record_id = u.reading_record_id
                    AND layer.base_id = u.base_id
                    AND layer.generation = $3
                    AND layer.layer_type IN ('grammar_note', 'sentence_analysis')
                    AND layer.target_scope = 'unit'
                    AND layer.target_key = u.unit_id
                    AND layer.status = 'published'
              )
            ORDER BY u.order_index ASC
            """,
            state.record_id,
            state.base_id,
            state.expected_generation,
        )
        if not rows:
            return []
        target_unit_ids = [str(row["unit_id"]) for row in rows]

        existing_job = await conn.fetchrow(
            """
            SELECT id, run_id
            FROM reader_jobs
            WHERE reading_record_id = $1
              AND base_id = $2
              AND job_type = $3
              AND target_type = $4
              AND target_key = $5
              AND expected_generation = $6
              AND operation_fingerprint = $7
              AND status IN ('queued', 'claimed', 'retry_later', 'paused', 'succeeded')
            LIMIT 1
            """,
            state.record_id,
            state.base_id,
            GRAMMAR_BATCH_JOB_TYPE,
            GRAMMAR_BATCH_TARGET_SCOPE,
            str(state.record_id),
            state.expected_generation,
            operation_fingerprint,
        )
        if existing_job is not None:
            # Idempotent: batch job already exists.
            return []

        run_id, job_id = await _insert_unit_range_job(
            conn,
            state=state,
            run_type=GRAMMAR_RUN_TYPE,
            job_type=GRAMMAR_BATCH_JOB_TYPE,
            target_scope=GRAMMAR_BATCH_TARGET_SCOPE,
            policy_version=policy_version,
            trigger_kind=GRAMMAR_TRIGGER_KIND,
            operation_fingerprint=operation_fingerprint,
            max_attempts=DEFAULT_GRAMMAR_BATCH_MAX_ATTEMPTS,
            envelope_json={
                "record_id": str(state.record_id),
                "base_id": str(state.base_id),
                "target_scope": GRAMMAR_BATCH_TARGET_SCOPE,
                "target_unit_ids": target_unit_ids,
                "layer_types": ["grammar_note", "sentence_analysis"],
                "trace_id": str(trace_id),
                "article_route": route.value,
                "document_features": _route_document_features(state),
            },
            input_signature_suffix=(
                f"{state.base_language}:grammar_bundle:{route_suffix}:batch"
            ),
            input_json={
                "target_scope": GRAMMAR_BATCH_TARGET_SCOPE,
                "target_unit_ids": target_unit_ids,
                "base_language": state.base_language,
                "layer_types": ["grammar_note", "sentence_analysis"],
                "article_route": route.value,
            },
            layer_name=_LAYER_NAME_BY_JOB_TYPE[GRAMMAR_BATCH_JOB_TYPE],
        )
        return [
            GrammarBootstrapResult(
                run_id=run_id,
                job_id=job_id,
                reading_record_id=state.record_id,
                base_id=state.base_id,
                unit_id=target_unit_ids[0],
                expected_generation=state.expected_generation,
                operation_fingerprint=operation_fingerprint,
            )
        ]

    async def _bootstrap_translation_batch_job(
        self,
        conn: asyncpg.Connection,
        *,
        state: _LockedActiveBaseState,
        route: ArticleRoute,
        trace_id: UUID | None = None,
    ) -> list[TranslationBootstrapResult]:
        """T1.1 / T4.1b: whole-article translation batch bootstrap.

        Creates a single ``translate_article`` / ``unit_range`` reader job
        whose ``input_json.target_unit_ids`` lists every unit that still
        needs a translation layer. The batch worker makes one LLM call
        covering all units; the batch publisher splits the output back
        into per-unit ``enhancement_layers`` rows.

        T4.1b route identity: ``route`` selects the operation_fingerprint
        base and policy_version. ``STRUCTURED_BATCH`` gets a distinct
        fingerprint so a route change (short -> structured on a rebuilt
        base) triggers ``_supersede_stale_fingerprint_jobs``. Both
        ``SHORT_BATCH`` and ``STRUCTURED_BATCH`` record ``article_route``
        in ``envelope_json`` and ``input_json``; ``document_features``
        is recorded in ``envelope_json`` only (workers needing the
        profile read it from the run envelope). This is the T4.1c
        grammar compact path hook.

        Idempotent: if a batch job already exists for this record / base /
        generation / fingerprint with status in
        ``('queued', 'claimed', 'retry_later', 'paused', 'succeeded')``,
        no new job is created.

        Returns a single-element list (or empty list when nothing to do).
        The ``unit_id`` field on the result is informational only; the
        pipeline runner only inspects ``job_id`` for the batch path.
        """
        if trace_id is None:
            trace_id = uuid4()
        if route is ArticleRoute.STRUCTURED_BATCH:
            fingerprint_base = TRANSLATION_STRUCTURED_BATCH_OPERATION_FINGERPRINT
            policy_version = TRANSLATION_STRUCTURED_BATCH_POLICY_VERSION
            route_suffix = "structured"
        else:
            fingerprint_base = TRANSLATION_BATCH_OPERATION_FINGERPRINT
            policy_version = TRANSLATION_BATCH_POLICY_VERSION
            route_suffix = "short"
        operation_fingerprint = _compose_operation_fingerprint(
            fingerprint_base, state.strategy
        )
        await _supersede_stale_fingerprint_jobs(
            conn,
            record_id=state.record_id,
            base_id=state.base_id,
            expected_generation=state.expected_generation,
            job_type=TRANSLATION_BATCH_JOB_TYPE,
            target_scope=TRANSLATION_BATCH_TARGET_SCOPE,
            current_fingerprint=operation_fingerprint,
        )
        rows = await conn.fetch(
            """
            SELECT u.unit_id, u.order_index, u.text_hash
            FROM reading_units u
            WHERE u.reading_record_id = $1
              AND u.base_id = $2
              AND NOT EXISTS (
                  SELECT 1
                  FROM enhancement_layers layer
                  WHERE layer.reading_record_id = u.reading_record_id
                    AND layer.base_id = u.base_id
                    AND layer.generation = $3
                    AND layer.layer_type = 'translation'
                    AND layer.target_scope = 'unit'
                    AND layer.target_key = u.unit_id
                    AND layer.status = 'published'
              )
            ORDER BY u.order_index ASC
            """,
            state.record_id,
            state.base_id,
            state.expected_generation,
        )
        if not rows:
            return []
        target_unit_ids = [str(row["unit_id"]) for row in rows]

        existing_job = await conn.fetchrow(
            """
            SELECT id, run_id
            FROM reader_jobs
            WHERE reading_record_id = $1
              AND base_id = $2
              AND job_type = $3
              AND target_type = $4
              AND target_key = $5
              AND expected_generation = $6
              AND operation_fingerprint = $7
              AND status IN ('queued', 'claimed', 'retry_later', 'paused', 'succeeded')
            LIMIT 1
            """,
            state.record_id,
            state.base_id,
            TRANSLATION_BATCH_JOB_TYPE,
            TRANSLATION_BATCH_TARGET_SCOPE,
            str(state.record_id),
            state.expected_generation,
            operation_fingerprint,
        )
        if existing_job is not None:
            # Idempotent: batch job already exists for this record / base /
            # generation / fingerprint. Return empty list to match per-unit
            # bootstrap semantics where units with existing queued jobs are
            # filtered out and produce no new results.
            return []

        run_id, job_id = await _insert_unit_range_job(
            conn,
            state=state,
            run_type=TRANSLATION_RUN_TYPE,
            job_type=TRANSLATION_BATCH_JOB_TYPE,
            target_scope=TRANSLATION_BATCH_TARGET_SCOPE,
            policy_version=policy_version,
            trigger_kind=TRANSLATION_TRIGGER_KIND,
            operation_fingerprint=operation_fingerprint,
            max_attempts=DEFAULT_TRANSLATION_MAX_ATTEMPTS,
            envelope_json={
                "record_id": str(state.record_id),
                "base_id": str(state.base_id),
                "target_scope": TRANSLATION_BATCH_TARGET_SCOPE,
                "target_unit_ids": target_unit_ids,
                "target_language": DEFAULT_TRANSLATION_TARGET_LANGUAGE,
                "trace_id": str(trace_id),
                "article_route": route.value,
                "document_features": _route_document_features(state),
            },
            input_signature_suffix=(
                f"{state.base_language}:{DEFAULT_TRANSLATION_TARGET_LANGUAGE}:"
                f"{route_suffix}:batch"
            ),
            input_json={
                "target_scope": TRANSLATION_BATCH_TARGET_SCOPE,
                "target_unit_ids": target_unit_ids,
                "base_language": state.base_language,
                "target_language": DEFAULT_TRANSLATION_TARGET_LANGUAGE,
                "article_route": route.value,
            },
            layer_name=_LAYER_NAME_BY_JOB_TYPE[TRANSLATION_BATCH_JOB_TYPE],
        )
        return [
            TranslationBootstrapResult(
                run_id=run_id,
                job_id=job_id,
                reading_record_id=state.record_id,
                base_id=state.base_id,
                unit_id=target_unit_ids[0],
                expected_generation=state.expected_generation,
                operation_fingerprint=operation_fingerprint,
            )
        ]

    async def _bootstrap_translation_grouped_jobs(
        self,
        conn: asyncpg.Connection,
        *,
        state: _LockedActiveBaseState,
        route: ArticleRoute,
        trace_id: UUID | None = None,
    ) -> list[TranslationBootstrapResult]:
        """T3.1: non-short translation grouped/window execution.

        Queries unpublished units (ordered by ``order_index``), plans
        consecutive windows via :func:`plan_translation_windows`, and
        creates one ``translate_article`` batch job per window. Each
        window job has a distinct ``target_key`` / ``idempotency_key`` /
        ``input_hash`` so multiple windows on the same record do not
        collide.

        The batch worker and publisher are window-agnostic: they read
        ``input_json.target_unit_ids`` and only process/publish that
        subset. Each unit's ``output_json.groups`` is still produced by
        :func:`build_deterministic_translation_groups` (T1.1a), preserving
        the Translation Group semantic contract regardless of how many
        units a window covers. No parallel job type or migration is
        introduced.

        T4.1b route identity: ``route`` is recorded as ``article_route``
        in ``envelope_json`` / ``input_json`` for audit consistency with
        the batch path. GROUPED_WINDOWED keeps its existing
        ``translation_article_v1`` fingerprint base (shared with
        SHORT_BATCH) so its idempotency contract is preserved; the
        three-way distinction is completed by ``article_route`` in
        ``input_json``.

        Cutover safety (review P1):

        - Legacy ``translate_unit`` per-unit jobs in ``queued`` /
          ``retry_later`` / ``paused`` are superseded before planning
          windows, so the worker loop no longer dispatches them alongside
          the new window jobs. ``claimed`` legacy jobs are left untouched
          but their target units are excluded from newly planned windows.
        - Units already targeted by an active ``translate_article`` window
          job (``queued`` / ``claimed`` / ``retry_later`` / ``paused``)
          are excluded from the unpublished-units query, preventing
          overlapping windows when a re-bootstrap runs while a previous
          window job is still in flight.
        """
        if trace_id is None:
            trace_id = uuid4()
        operation_fingerprint = _compose_operation_fingerprint(
            TRANSLATION_BATCH_OPERATION_FINGERPRINT, state.strategy
        )
        await _supersede_stale_fingerprint_jobs(
            conn,
            record_id=state.record_id,
            base_id=state.base_id,
            expected_generation=state.expected_generation,
            job_type=TRANSLATION_BATCH_JOB_TYPE,
            target_scope=TRANSLATION_BATCH_TARGET_SCOPE,
            current_fingerprint=operation_fingerprint,
        )
        # T3.1 cutover: supersede legacy per-unit ``translate_unit`` jobs
        # that are still queued/retry_later/paused so the worker loop does
        # not dispatch them alongside the new window jobs. ``claimed`` jobs
        # are left untouched (see docstring on the helper).
        await _supersede_legacy_translation_per_unit_jobs(
            conn,
            record_id=state.record_id,
            base_id=state.base_id,
            expected_generation=state.expected_generation,
        )
        # Load unpublished units with their UTF-16 char length for windowing.
        # ``base_end_utf16 - base_start_utf16`` matches the worker's
        # ``slice_by_utf16_offsets`` unit text length exactly.
        #
        # Three exclusion clauses:
        # 1. Already-published translation layers (partial publish skip).
        # 2. Units already targeted by an active ``translate_article`` window
        #    job (prevents overlapping windows when a re-bootstrap runs while
        #    a previous window job is still queued/claimed/retry_later).
        # 3. Units currently claimed by a legacy ``translate_unit`` job
        #    (prevents a claimed per-unit job from making a batch window fail
        #    after it publishes one of the same units).
        rows = await conn.fetch(
            """
            SELECT
                u.unit_id,
                u.order_index,
                u.base_start_utf16,
                u.base_end_utf16
            FROM reading_units u
            WHERE u.reading_record_id = $1
              AND u.base_id = $2
              AND NOT EXISTS (
                  SELECT 1
                  FROM enhancement_layers layer
                  WHERE layer.reading_record_id = u.reading_record_id
                    AND layer.base_id = u.base_id
                    AND layer.generation = $3
                    AND layer.layer_type = 'translation'
                    AND layer.target_scope = 'unit'
                    AND layer.target_key = u.unit_id
                    AND layer.status = 'published'
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM reader_jobs job
                  CROSS JOIN LATERAL
                       jsonb_array_elements_text(job.input_json->'target_unit_ids') AS tgt(unit_id)
                  WHERE job.reading_record_id = u.reading_record_id
                    AND job.base_id = u.base_id
                    AND job.expected_generation = $3
                    AND job.job_type = $4
                    AND job.target_type = $5
                    AND job.status IN ('queued', 'claimed', 'retry_later', 'paused')
                    AND tgt.unit_id = u.unit_id
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM reader_jobs job
                  WHERE job.reading_record_id = u.reading_record_id
                    AND job.base_id = u.base_id
                    AND job.expected_generation = $3
                    AND job.job_type = $6
                    AND job.target_type = $7
                    AND job.target_key = u.unit_id
                    AND job.status = 'claimed'
              )
            ORDER BY u.order_index ASC
            """,
            state.record_id,
            state.base_id,
            state.expected_generation,
            TRANSLATION_BATCH_JOB_TYPE,
            TRANSLATION_BATCH_TARGET_SCOPE,
            TRANSLATION_JOB_TYPE,
            TRANSLATION_TARGET_SCOPE,
        )
        if not rows:
            return []
        window_units = [
            TranslationWindowUnit(
                unit_id=str(row["unit_id"]),
                order_index=int(row["order_index"]),
                text_length=int(row["base_end_utf16"]) - int(row["base_start_utf16"]),
            )
            for row in rows
        ]
        windows = plan_translation_windows(window_units)
        results: list[TranslationBootstrapResult] = []
        for window in windows:
            target_unit_ids = list(window.target_unit_ids)
            window_target_key = f"{state.record_id}:window:{window.window_id}"
            # Per-window idempotency: skip if an active job already exists
            # for this window's target_key + fingerprint.
            existing_job = await conn.fetchrow(
                """
                SELECT id, run_id
                FROM reader_jobs
                WHERE reading_record_id = $1
                  AND base_id = $2
                  AND job_type = $3
                  AND target_type = $4
                  AND target_key = $5
                  AND expected_generation = $6
                  AND operation_fingerprint = $7
                  AND status IN ('queued', 'claimed', 'retry_later', 'paused', 'succeeded')
                LIMIT 1
                """,
                state.record_id,
                state.base_id,
                TRANSLATION_BATCH_JOB_TYPE,
                TRANSLATION_BATCH_TARGET_SCOPE,
                window_target_key,
                state.expected_generation,
                operation_fingerprint,
            )
            if existing_job is not None:
                continue
            run_id, job_id = await _insert_unit_range_job(
                conn,
                state=state,
                run_type=TRANSLATION_RUN_TYPE,
                job_type=TRANSLATION_BATCH_JOB_TYPE,
                target_scope=TRANSLATION_BATCH_TARGET_SCOPE,
                policy_version=TRANSLATION_BATCH_POLICY_VERSION,
                trigger_kind=TRANSLATION_TRIGGER_KIND,
                operation_fingerprint=operation_fingerprint,
                max_attempts=DEFAULT_TRANSLATION_MAX_ATTEMPTS,
                envelope_json={
                    "record_id": str(state.record_id),
                    "base_id": str(state.base_id),
                    "target_scope": TRANSLATION_BATCH_TARGET_SCOPE,
                    "target_unit_ids": target_unit_ids,
                    "target_language": DEFAULT_TRANSLATION_TARGET_LANGUAGE,
                    "trace_id": str(trace_id),
                    "window_id": window.window_id,
                    "article_route": route.value,
                    "document_features": _route_document_features(state),
                },
                input_signature_suffix=(
                    f"{state.base_language}:{DEFAULT_TRANSLATION_TARGET_LANGUAGE}:"
                    f"window:{window.window_id}:batch"
                ),
                input_json={
                    "target_scope": TRANSLATION_BATCH_TARGET_SCOPE,
                    "target_unit_ids": target_unit_ids,
                    "base_language": state.base_language,
                    "target_language": DEFAULT_TRANSLATION_TARGET_LANGUAGE,
                    "window_id": window.window_id,
                    "article_route": route.value,
                },
                layer_name=_LAYER_NAME_BY_JOB_TYPE[TRANSLATION_BATCH_JOB_TYPE],
                target_key_override=window_target_key,
                idempotency_key_suffix=f"window:{window.window_id}",
            )
            results.append(
                TranslationBootstrapResult(
                    run_id=run_id,
                    job_id=job_id,
                    reading_record_id=state.record_id,
                    base_id=state.base_id,
                    unit_id=target_unit_ids[0],
                    expected_generation=state.expected_generation,
                    operation_fingerprint=operation_fingerprint,
                )
            )
        return results

    async def _bootstrap_vocabulary_batch_job(
        self,
        conn: asyncpg.Connection,
        *,
        state: _LockedActiveBaseState,
        route: ArticleRoute,
        trace_id: UUID | None = None,
    ) -> list[VocabularyBootstrapResult]:
        """T1.1 / T4.1b: whole-article vocabulary batch bootstrap.

        Mirrors :meth:`_bootstrap_translation_batch_job` for the vocabulary
        layer. Same idempotency contract; ``target_unit_ids`` lists every
        unit that still needs a vocabulary layer.

        T4.1b route identity: ``route`` selects the operation_fingerprint
        base and policy_version. ``STRUCTURED_BATCH`` gets a distinct
        fingerprint so a route change (short -> structured on a rebuilt
        base) triggers ``_supersede_stale_fingerprint_jobs``. Both
        ``SHORT_BATCH`` and ``STRUCTURED_BATCH`` record ``article_route``
        in ``envelope_json`` and ``input_json``; ``document_features``
        is recorded in ``envelope_json`` only (workers needing the
        profile read it from the run envelope). This is the T4.1c
        grammar compact path hook.
        """
        if trace_id is None:
            trace_id = uuid4()
        if route is ArticleRoute.STRUCTURED_BATCH:
            fingerprint_base = VOCABULARY_STRUCTURED_BATCH_OPERATION_FINGERPRINT
            policy_version = VOCABULARY_STRUCTURED_BATCH_POLICY_VERSION
            route_suffix = "structured"
        else:
            fingerprint_base = VOCABULARY_BATCH_OPERATION_FINGERPRINT
            policy_version = VOCABULARY_BATCH_POLICY_VERSION
            route_suffix = "short"
        operation_fingerprint = _compose_operation_fingerprint(
            fingerprint_base, state.strategy
        )
        await _supersede_stale_fingerprint_jobs(
            conn,
            record_id=state.record_id,
            base_id=state.base_id,
            expected_generation=state.expected_generation,
            job_type=VOCABULARY_BATCH_JOB_TYPE,
            target_scope=VOCABULARY_BATCH_TARGET_SCOPE,
            current_fingerprint=operation_fingerprint,
        )
        rows = await conn.fetch(
            """
            SELECT u.unit_id, u.order_index, u.text_hash
            FROM reading_units u
            WHERE u.reading_record_id = $1
              AND u.base_id = $2
              AND NOT EXISTS (
                  SELECT 1
                  FROM enhancement_layers layer
                  WHERE layer.reading_record_id = u.reading_record_id
                    AND layer.base_id = u.base_id
                    AND layer.generation = $3
                    AND layer.layer_type = 'vocabulary'
                    AND layer.target_scope = 'unit'
                    AND layer.target_key = u.unit_id
                    AND layer.status = 'published'
              )
            ORDER BY u.order_index ASC
            """,
            state.record_id,
            state.base_id,
            state.expected_generation,
        )
        if not rows:
            return []
        target_unit_ids = [str(row["unit_id"]) for row in rows]

        existing_job = await conn.fetchrow(
            """
            SELECT id, run_id
            FROM reader_jobs
            WHERE reading_record_id = $1
              AND base_id = $2
              AND job_type = $3
              AND target_type = $4
              AND target_key = $5
              AND expected_generation = $6
              AND operation_fingerprint = $7
              AND status IN ('queued', 'claimed', 'retry_later', 'paused', 'succeeded')
            LIMIT 1
            """,
            state.record_id,
            state.base_id,
            VOCABULARY_BATCH_JOB_TYPE,
            VOCABULARY_BATCH_TARGET_SCOPE,
            str(state.record_id),
            state.expected_generation,
            operation_fingerprint,
        )
        if existing_job is not None:
            # Idempotent: batch job already exists. Return empty list to
            # match per-unit bootstrap semantics.
            return []

        run_id, job_id = await _insert_unit_range_job(
            conn,
            state=state,
            run_type=VOCABULARY_RUN_TYPE,
            job_type=VOCABULARY_BATCH_JOB_TYPE,
            target_scope=VOCABULARY_BATCH_TARGET_SCOPE,
            policy_version=policy_version,
            trigger_kind=VOCABULARY_TRIGGER_KIND,
            operation_fingerprint=operation_fingerprint,
            max_attempts=DEFAULT_VOCABULARY_MAX_ATTEMPTS,
            envelope_json={
                "record_id": str(state.record_id),
                "base_id": str(state.base_id),
                "target_scope": VOCABULARY_BATCH_TARGET_SCOPE,
                "target_unit_ids": target_unit_ids,
                "layer_type": "vocabulary",
                "trace_id": str(trace_id),
                "article_route": route.value,
                "document_features": _route_document_features(state),
            },
            input_signature_suffix=(
                f"{state.base_language}:vocabulary:{route_suffix}:batch"
            ),
            input_json={
                "target_scope": VOCABULARY_BATCH_TARGET_SCOPE,
                "target_unit_ids": target_unit_ids,
                "base_language": state.base_language,
                "layer_type": "vocabulary",
                "article_route": route.value,
            },
            layer_name=_LAYER_NAME_BY_JOB_TYPE[VOCABULARY_BATCH_JOB_TYPE],
        )
        return [
            VocabularyBootstrapResult(
                run_id=run_id,
                job_id=job_id,
                reading_record_id=state.record_id,
                base_id=state.base_id,
                unit_id=target_unit_ids[0],
                expected_generation=state.expected_generation,
                operation_fingerprint=operation_fingerprint,
            )
        ]


async def _load_locked_active_base_state(
    conn: asyncpg.Connection,
    *,
    record_id: UUID,
    user_id: UUID,
) -> _LockedActiveBaseState:
    record_row = await conn.fetchrow(
        """
        SELECT
            id,
            generation,
            active_base_id,
            lifecycle_status,
            product_state,
            readiness_state,
            reading_goal,
            reading_variant
        FROM reading_records
        WHERE id = $1
          AND user_id = $2
          AND deleted_at IS NULL
        FOR UPDATE
        """,
        record_id,
        user_id,
    )
    if record_row is None:
        raise LookupError(f"reading record {record_id} not found for user {user_id}")
    if record_row["lifecycle_status"] != "active":
        raise ValueError("enhancement bootstrap requires an active reading record")
    if record_row["product_state"] not in _BOOTSTRAP_READY_PRODUCT_STATES:
        raise ValueError("reading record is not ready for enhancement bootstrap")

    base_id = record_row["active_base_id"]
    if base_id is None:
        raise ValueError("enhancement bootstrap requires an active base")

    base_row = await conn.fetchrow(
        """
        SELECT id, record_generation, status, language
        FROM reading_bases
        WHERE id = $1
          AND reading_record_id = $2
        """,
        base_id,
        record_id,
    )
    if base_row is None:
        raise ValueError("active base does not belong to the requested record")

    expected_generation = int(record_row["generation"])
    if int(base_row["record_generation"]) != expected_generation:
        raise ValueError(
            "active base generation does not match the reading record generation"
        )
    if base_row["status"] != "active":
        raise ValueError("enhancement bootstrap requires status='active' base")

    # Resolve the variant-first strategy from the reading record's first-class
    # reading_goal / reading_variant columns. These are persisted facts (T1 +
    # migration 0012), NOT inferred from source_metadata. The resolver fails
    # closed on missing/illegal pairs (including academic / academic_general);
    # there is no default fallback here. Historical records use the DB default
    # (daily_reading / intermediate_reading) which is a valid pair.
    strategy = resolve_reader_variant_strategy(
        str(record_row["reading_goal"]),
        str(record_row["reading_variant"]),
    )

    return _LockedActiveBaseState(
        record_id=record_id,
        user_id=user_id,
        base_id=base_id,
        expected_generation=expected_generation,
        base_language=str(base_row["language"] or "en"),
        last_event_sequence=await _load_last_event_sequence(conn, record_id=record_id),
        strategy=strategy,
        readiness_state=str(record_row["readiness_state"] or "submitted"),
    )


async def _bootstrap_semantic_outline_job(
    conn: asyncpg.Connection,
    *,
    state: _LockedActiveBaseState,
    trace_id: UUID | None = None,
    request_eligibility: SemanticOutlineRequestEligibility,
) -> list[SemanticOutlineBootstrapResult]:
    """Create one base-scoped outline job when eligible.

    Hard gates:
    - readiness_state has reached article_ready milestone
    - injected request eligibility returns True
    - stale fingerprint jobs superseded (queued/retry_later/paused only)
    """
    if state.readiness_state not in _ARTICLE_READY_READINESS_STATES:
        return []
    if not request_eligibility(state):
        return []

    if trace_id is None:
        trace_id = uuid4()

    fingerprint_base = (
        f"{SEMANTIC_OUTLINE_OPERATION_FINGERPRINT}:"
        f"{SEMANTIC_OUTLINE_INPUT_SHAPE_VERSION}"
    )
    operation_fingerprint = _compose_operation_fingerprint(
        fingerprint_base, state.strategy
    )
    await _supersede_stale_fingerprint_jobs(
        conn,
        record_id=state.record_id,
        base_id=state.base_id,
        expected_generation=state.expected_generation,
        job_type=SEMANTIC_OUTLINE_JOB_TYPE,
        target_scope=SEMANTIC_OUTLINE_TARGET_SCOPE,
        current_fingerprint=operation_fingerprint,
    )

    existing_job = await conn.fetchrow(
        """
        SELECT id
        FROM reader_jobs
        WHERE reading_record_id = $1
          AND base_id = $2
          AND job_type = $3
          AND target_type = $4
          AND target_key = $5
          AND expected_generation = $6
          AND operation_fingerprint = $7
          AND status IN ('queued', 'claimed', 'retry_later', 'paused', 'succeeded')
        ORDER BY created_at ASC, id ASC
        LIMIT 1
        """,
        state.record_id,
        state.base_id,
        SEMANTIC_OUTLINE_JOB_TYPE,
        SEMANTIC_OUTLINE_TARGET_SCOPE,
        str(state.record_id),
        state.expected_generation,
        operation_fingerprint,
    )
    if existing_job is not None:
        return []

    run_id, job_id = await _insert_record_job(
        conn,
        state=state,
        run_type=SEMANTIC_OUTLINE_RUN_TYPE,
        job_type=SEMANTIC_OUTLINE_JOB_TYPE,
        target_scope=SEMANTIC_OUTLINE_TARGET_SCOPE,
        policy_version=SEMANTIC_OUTLINE_POLICY_VERSION,
        trigger_kind=SEMANTIC_OUTLINE_TRIGGER_KIND,
        operation_fingerprint=operation_fingerprint,
        max_attempts=DEFAULT_SEMANTIC_OUTLINE_MAX_ATTEMPTS,
        envelope_json={
            "record_id": str(state.record_id),
            "base_id": str(state.base_id),
            "target_scope": SEMANTIC_OUTLINE_TARGET_SCOPE,
            "target_key": SEMANTIC_OUTLINE_TARGET_KEY,
            "layer_type": "semantic_outline",
            "input_shape_version": SEMANTIC_OUTLINE_INPUT_SHAPE_VERSION,
            "trace_id": str(trace_id),
            "source_identity": {
                "base_id": str(state.base_id),
                "generation": state.expected_generation,
            },
        },
        input_signature_suffix=(
            f"{state.base_language}:semantic_outline:"
            f"{SEMANTIC_OUTLINE_INPUT_SHAPE_VERSION}"
        ),
        input_json={
            "target_scope": SEMANTIC_OUTLINE_TARGET_SCOPE,
            "target_key": SEMANTIC_OUTLINE_TARGET_KEY,
            "layer_type": "semantic_outline",
            "input_shape_version": SEMANTIC_OUTLINE_INPUT_SHAPE_VERSION,
            "base_language": state.base_language,
            "source_identity": {
                "base_id": str(state.base_id),
                "generation": state.expected_generation,
            },
        },
        layer_name=None,
    )
    return [
        SemanticOutlineBootstrapResult(
            run_id=run_id,
            job_id=job_id,
            reading_record_id=state.record_id,
            base_id=state.base_id,
            expected_generation=state.expected_generation,
            operation_fingerprint=operation_fingerprint,
        )
    ]


async def _bootstrap_display_title_job(
    conn: asyncpg.Connection,
    *,
    state: _LockedActiveBaseState,
    trace_id: UUID | None = None,
) -> list[DisplayTitleBootstrapResult]:
    if trace_id is None:
        trace_id = uuid4()
    operation_fingerprint = _compose_operation_fingerprint(
        DISPLAY_TITLE_OPERATION_FINGERPRINT, state.strategy
    )
    await _supersede_stale_fingerprint_jobs(
        conn,
        record_id=state.record_id,
        base_id=state.base_id,
        expected_generation=state.expected_generation,
        job_type=DISPLAY_TITLE_JOB_TYPE,
        target_scope=DISPLAY_TITLE_TARGET_SCOPE,
        current_fingerprint=operation_fingerprint,
    )
    row = await conn.fetchrow(
        """
        SELECT title_generation_status
        FROM reading_records
        WHERE id = $1
          AND user_id = $2
          AND deleted_at IS NULL
        """,
        state.record_id,
        state.user_id,
    )
    if row is None:
        raise LookupError(f"reading record {state.record_id} not found")
    if row["title_generation_status"] == "succeeded":
        return []

    existing_job = await conn.fetchrow(
        """
        SELECT id
        FROM reader_jobs
        WHERE reading_record_id = $1
          AND base_id = $2
          AND job_type = $3
          AND target_type = $4
          AND target_key = $5
          AND expected_generation = $6
          AND operation_fingerprint = $7
          AND status IN ('queued', 'claimed', 'retry_later', 'paused', 'succeeded')
        ORDER BY created_at ASC, id ASC
        LIMIT 1
        """,
        state.record_id,
        state.base_id,
        DISPLAY_TITLE_JOB_TYPE,
        DISPLAY_TITLE_TARGET_SCOPE,
        str(state.record_id),
        state.expected_generation,
        operation_fingerprint,
    )
    if existing_job is not None:
        return []

    updated_id = await conn.fetchval(
        """
        UPDATE reading_records
        SET title_generation_status = 'pending',
            title_generation_error_code = NULL,
            title_generation_error_message = NULL,
            title_generation_updated_at = NOW(),
            updated_at = NOW()
        WHERE id = $1
          AND user_id = $2
          AND generation = $3
          AND active_base_id = $4
          AND deleted_at IS NULL
          AND title_generation_status IS DISTINCT FROM 'pending'
          AND title_generation_status <> 'succeeded'
        RETURNING id
        """,
        state.record_id,
        state.user_id,
        state.expected_generation,
        state.base_id,
    )
    if updated_id is not None:
        # Only publish a representation event when the status actually
        # transitioned to ``pending``. A true no-op (already pending) does
        # NOT advance the sequence. This runs inside the caller's outer
        # transaction so the title status change, job insert, and event
        # publish commit atomically.
        payload = build_representation_payload(
            representation_section="record_metadata",
            operation="status_changed",
            generation=state.expected_generation,
            base_id=str(state.base_id),
            target_keys=[
                "title_generation_status",
                "title_generation_error_code",
                "title_generation_error_message",
            ],
        )
        await ReaderEventRuntime().publish_event_in_transaction(
            conn,
            record_id=state.record_id,
            event_type="record_state_changed",
            payload_json=payload,
        )

    run_id, job_id = await _insert_record_job(
        conn,
        state=state,
        run_type=DISPLAY_TITLE_RUN_TYPE,
        job_type=DISPLAY_TITLE_JOB_TYPE,
        target_scope=DISPLAY_TITLE_TARGET_SCOPE,
        policy_version=DISPLAY_TITLE_POLICY_VERSION,
        trigger_kind=DISPLAY_TITLE_TRIGGER_KIND,
        operation_fingerprint=operation_fingerprint,
        max_attempts=DEFAULT_DISPLAY_TITLE_MAX_ATTEMPTS,
        envelope_json={
            "record_id": str(state.record_id),
            "base_id": str(state.base_id),
            "target_scope": DISPLAY_TITLE_TARGET_SCOPE,
            "target_language": "zh-CN",
            "trace_id": str(trace_id),
        },
        input_signature_suffix=f"{state.base_language}:display_title_zh:1",
        input_json={
            "target_language": "zh-CN",
            "base_language": state.base_language,
        },
        layer_name=None,
    )
    return [
        DisplayTitleBootstrapResult(
            run_id=run_id,
            job_id=job_id,
            reading_record_id=state.record_id,
            base_id=state.base_id,
            expected_generation=state.expected_generation,
            operation_fingerprint=operation_fingerprint,
        )
    ]


async def _load_last_event_sequence(
    conn: asyncpg.Connection,
    *,
    record_id: UUID,
) -> int:
    row = await conn.fetchrow(
        """
        SELECT next_sequence
        FROM reader_event_sequences
        WHERE reading_record_id = $1
        """,
        record_id,
    )
    if row is None or row["next_sequence"] is None:
        return 0
    return max(0, int(row["next_sequence"]) - 1)


async def _insert_record_job(
    conn: asyncpg.Connection,
    *,
    state: _LockedActiveBaseState,
    run_type: str,
    job_type: str,
    target_scope: str,
    policy_version: str,
    trigger_kind: str,
    operation_fingerprint: str,
    max_attempts: int,
    envelope_json: dict[str, Any],
    input_signature_suffix: str,
    input_json: dict[str, Any],
    layer_name: str | None,
) -> tuple[UUID, UUID]:
    strategy_metadata = _build_strategy_metadata(state.strategy, layer_name)
    run_row = await conn.fetchrow(
        """
        INSERT INTO reader_runs (
            reading_record_id,
            user_id,
            run_type,
            status,
            record_generation,
            envelope_json,
            policy_version,
            trigger_kind
        )
        VALUES (
            $1,
            $2,
            $3,
            'queued',
            $4,
            $5::jsonb,
            $6,
            $7
        )
        RETURNING id
        """,
        state.record_id,
        state.user_id,
        run_type,
        state.expected_generation,
        jsonb_param({**envelope_json, "strategy": strategy_metadata}),
        policy_version,
        trigger_kind,
    )
    if run_row is None:
        raise RuntimeError("reader_runs insert did not return a row")

    input_signature = (
        f"{state.base_id}:{state.record_id}:{state.expected_generation}:"
        f"{operation_fingerprint}:{input_signature_suffix}:"
        f"{state.strategy.strategy_hash}"
    )
    input_hash = hashlib.sha256(input_signature.encode("utf-8")).hexdigest()
    job_row = await conn.fetchrow(
        """
        INSERT INTO reader_jobs (
            reading_record_id,
            base_id,
            run_id,
            user_id,
            job_type,
            target_type,
            target_key,
            status,
            priority,
            expected_generation,
            operation_fingerprint,
            idempotency_key,
            input_hash,
            input_json,
            max_attempts
        )
        VALUES (
            $1,
            $2,
            $3,
            $4,
            $5,
            $6,
            $7,
            'queued',
            10,
            $8,
            $9,
            $10,
            $11,
            $12::jsonb,
            $13
        )
        RETURNING id
        """,
        state.record_id,
        state.base_id,
        run_row["id"],
        state.user_id,
        job_type,
        target_scope,
        str(state.record_id),
        state.expected_generation,
        operation_fingerprint,
        f"{operation_fingerprint}:{state.record_id}",
        input_hash,
        jsonb_param(
            {
                **input_json,
                **strategy_metadata,
                "record_id": str(state.record_id),
                "base_id": str(state.base_id),
                "expected_generation": state.expected_generation,
            }
        ),
        max_attempts,
    )
    if job_row is None:
        raise RuntimeError("reader_jobs insert did not return a row")
    return run_row["id"], job_row["id"]


async def _insert_unit_range_job(
    conn: asyncpg.Connection,
    *,
    state: _LockedActiveBaseState,
    run_type: str,
    job_type: str,
    target_scope: str,
    policy_version: str,
    trigger_kind: str,
    operation_fingerprint: str,
    max_attempts: int,
    envelope_json: dict[str, Any],
    input_signature_suffix: str,
    input_json: dict[str, Any],
    layer_name: str,
    target_key_override: str | None = None,
    idempotency_key_suffix: str = "batch",
) -> tuple[UUID, UUID]:
    """T1.1 short-article batch path: insert one record-level batch job.

    Mirrors :func:`_insert_unit_job` but covers a range of units in a single
    job. Differences from the per-unit helper:

    - ``target_key`` defaults to ``str(state.record_id)`` (record-level,
      like the display-title job), not a single ``unit_id``.
      ``target_key_override`` (T3.2b) lets the caller set a window-specific
      target_key for non-short grouped vocabulary jobs.
    - ``input_json`` carries ``target_scope: "unit_range"`` and
      ``target_unit_ids: [...]`` (list of every unit id covered by the
      batch). The caller is responsible for putting these fields in
      ``input_json``; this helper only adds the standard record-level
      metadata (record_id / base_id / expected_generation) and the
      strategy metadata block.
    - ``idempotency_key`` is suffixed with ``idempotency_key_suffix``
      (default ``:batch``) so the per-unit and per-article idempotency
      spaces do not collide. T3.2b passes ``window:{window_id}`` to keep
      multiple window jobs on the same record distinct.
    - ``input_hash`` is derived from the record-level signature
      (``base_id:record_id:input_signature_suffix:strategy_hash``) so the
      same unit range + strategy produces a stable hash.
    """
    strategy_metadata = _build_strategy_metadata(state.strategy, layer_name)
    run_row = await conn.fetchrow(
        """
        INSERT INTO reader_runs (
            reading_record_id,
            user_id,
            run_type,
            status,
            record_generation,
            envelope_json,
            policy_version,
            trigger_kind
        )
        VALUES (
            $1,
            $2,
            $3,
            'queued',
            $4,
            $5::jsonb,
            $6,
            $7
        )
        RETURNING id
        """,
        state.record_id,
        state.user_id,
        run_type,
        state.expected_generation,
        jsonb_param({**envelope_json, "strategy": strategy_metadata}),
        policy_version,
        trigger_kind,
    )
    if run_row is None:
        raise RuntimeError("reader_runs insert did not return a row")

    unit_range_signature = (
        f"{state.base_id}:{state.record_id}:{input_signature_suffix}:"
        f"{state.strategy.strategy_hash}"
    )
    input_hash = hashlib.sha256(unit_range_signature.encode("utf-8")).hexdigest()
    job_row = await conn.fetchrow(
        """
        INSERT INTO reader_jobs (
            reading_record_id,
            base_id,
            run_id,
            user_id,
            job_type,
            target_type,
            target_key,
            status,
            priority,
            expected_generation,
            operation_fingerprint,
            idempotency_key,
            input_hash,
            input_json,
            max_attempts
        )
        VALUES (
            $1,
            $2,
            $3,
            $4,
            $5,
            $6,
            $7,
            'queued',
            0,
            $8,
            $9,
            $10,
            $11,
            $12::jsonb,
            $13
        )
        RETURNING id
        """,
        state.record_id,
        state.base_id,
        run_row["id"],
        state.user_id,
        job_type,
        target_scope,
        target_key_override if target_key_override is not None else str(state.record_id),
        state.expected_generation,
        operation_fingerprint,
        f"{operation_fingerprint}:{state.record_id}:{idempotency_key_suffix}",
        input_hash,
        jsonb_param(
            {
                **input_json,
                **strategy_metadata,
                "record_id": str(state.record_id),
                "base_id": str(state.base_id),
                "expected_generation": state.expected_generation,
            }
        ),
        max_attempts,
    )
    if job_row is None:
        raise RuntimeError("reader_jobs insert did not return a row")
    return run_row["id"], job_row["id"]


async def _insert_unit_job(
    conn: asyncpg.Connection,
    *,
    state: _LockedActiveBaseState,
    unit_id: str,
    unit_order_index: int,
    unit_text_hash: str,
    run_type: str,
    job_type: str,
    target_scope: str,
    policy_version: str,
    trigger_kind: str,
    operation_fingerprint: str,
    max_attempts: int,
    envelope_json: dict[str, Any],
    input_signature_suffix: str,
    input_json: dict[str, Any],
    layer_name: str,
) -> tuple[UUID, UUID]:
    strategy_metadata = _build_strategy_metadata(state.strategy, layer_name)
    run_row = await conn.fetchrow(
        """
        INSERT INTO reader_runs (
            reading_record_id,
            user_id,
            run_type,
            status,
            record_generation,
            envelope_json,
            policy_version,
            trigger_kind
        )
        VALUES (
            $1,
            $2,
            $3,
            'queued',
            $4,
            $5::jsonb,
            $6,
            $7
        )
        RETURNING id
        """,
        state.record_id,
        state.user_id,
        run_type,
        state.expected_generation,
        jsonb_param({**envelope_json, "strategy": strategy_metadata}),
        policy_version,
        trigger_kind,
    )
    if run_row is None:
        raise RuntimeError("reader_runs insert did not return a row")

    unit_text_signature = (
        f"{state.base_id}:{unit_id}:{unit_text_hash}:{input_signature_suffix}:"
        f"{state.strategy.strategy_hash}"
    )
    input_hash = hashlib.sha256(unit_text_signature.encode("utf-8")).hexdigest()
    job_row = await conn.fetchrow(
        """
        INSERT INTO reader_jobs (
            reading_record_id,
            base_id,
            run_id,
            user_id,
            job_type,
            target_type,
            target_key,
            status,
            priority,
            expected_generation,
            operation_fingerprint,
            idempotency_key,
            input_hash,
            input_json,
            max_attempts
        )
        VALUES (
            $1,
            $2,
            $3,
            $4,
            $5,
            $6,
            $7,
            'queued',
            0,
            $8,
            $9,
            $10,
            $11,
            $12::jsonb,
            $13
        )
        RETURNING id
        """,
        state.record_id,
        state.base_id,
        run_row["id"],
        state.user_id,
        job_type,
        target_scope,
        unit_id,
        state.expected_generation,
        operation_fingerprint,
        f"{operation_fingerprint}:{unit_id}",
        input_hash,
        jsonb_param(
            {
                **input_json,
                **strategy_metadata,
                "unit_id": unit_id,
                "unit_order_index": unit_order_index,
                "unit_text_hash": unit_text_hash,
            }
        ),
        max_attempts,
    )
    if job_row is None:
        raise RuntimeError("reader_jobs insert did not return a row")
    return run_row["id"], job_row["id"]
