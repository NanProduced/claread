from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from app.config.settings import Settings
from app.database import connection as db_connection
from app.database.json_compat import jsonb_param
from app.services.reader_orchestration.analysis_section_jobs import (
    ANALYSIS_SECTION_ORIGINS,
    ANALYSIS_SECTION_REQUEST_ORIGIN,
    GRAMMAR_ANALYSIS_SECTION_FINGERPRINT,
    GRAMMAR_ANALYSIS_SECTION_POLICY_VERSION,
    VOCABULARY_ANALYSIS_SECTION_FINGERPRINT,
    VOCABULARY_ANALYSIS_SECTION_POLICY_VERSION,
    is_resumable_user_paused_analysis_job,
)
from app.services.reader_orchestration.analysis_section_plan import (
    ANALYSIS_SECTION_PLAN_VERSION,
    AnalysisSection,
    AnalysisSectionUnit,
    plan_analysis_sections,
)
from app.services.reader_orchestration.automatic_layer_policy import (
    AutomaticLayerName,
    AutomaticLayerTargetUnit,
    build_semantic_fence_input_fields,
    compose_semantic_fingerprint_token,
    filter_units_for_automatic_layer,
    generation_semantic_fence_from_targets,
    get_automatic_layer_policy_mode,
    policy_from_unit_metadata,
)
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
from app.services.reader_orchestration.translation_prompt_profile import (
    build_translation_prompt_profile_contract,
    compose_translation_prompt_profile_fingerprint_token,
    translation_prompt_profile_input_fields,
)
from app.services.reader_orchestration.translation_window_plan import (
    TRANSLATION_WINDOW_SAFETY_MAX_CHAR_COUNT as TRANSLATION_WINDOW_SAFETY_MAX_CHAR_COUNT,
)
from app.services.reader_orchestration.translation_window_plan import (
    TRANSLATION_WINDOW_TARGET_CHAR_COUNT as TRANSLATION_WINDOW_TARGET_CHAR_COUNT,
)
from app.services.reader_orchestration.translation_window_plan import (
    TranslationWindowPlan as TranslationWindowPlan,
)
from app.services.reader_orchestration.translation_window_plan import (
    TranslationWindowUnit,
    plan_translation_windows,
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

# Compact grammar batch path: SHORT_BATCH and STRUCTURED_BATCH
# articles use a single whole-article grammar batch job instead of the
# heavy grammar-window analysis-window path. One LLM call covers all unpublished
# units; the publisher splits the output back into per-unit grammar_note
# / sentence_analysis layers. GROUPED_WINDOWED uses one first-section
# compact grammar batch job instead of analysis windows.
#
# Route-specific fingerprints (pattern): STRUCTURED_BATCH gets a
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

# Explicit failed-enhancement recovery entry (same-generation successor
# jobs). ``failed`` records are rejected by the ordinary bootstrap gate;
# only ``recover_failed_enhancement_jobs`` may widen the gate to these
# states. ``readable_enhancing`` is accepted so a repeated recovery call
# (the first call already restored the state) stays an idempotent
# missing-job bootstrap instead of failing closed.
_RECOVERY_ELIGIBLE_PRODUCT_STATES = frozenset({"failed", "readable_enhancing"})
RECOVERY_TRIGGER_MANUAL = "manual"
RECOVERY_TRIGGER_AUTOMATIC = "automatic"
_RECOVERY_TRIGGER_KINDS = frozenset({RECOVERY_TRIGGER_MANUAL, RECOVERY_TRIGGER_AUTOMATIC})
RECOVERY_EVENT_SCHEMA = "reader_parse_recovery_requested_v1"
RECOVERY_MODE_SAME_GENERATION_SUCCESSOR_JOBS = "same_generation_successor_jobs"
RECOVERY_BILLING_MODE = "internal_only"

# Semantic outline (optional, request-eligible only; not a budget layer).
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

# Semantic outline content-sufficiency short-circuit.
# When the stable document already carries at least this many ``heading``
# reading_units, the backend skips semantic outline job creation: the
# Markdown headings already form a usable outline. The threshold is
# frozen as a module-level constant (not a Settings flag) per the plan:
# this is a content-type eligibility short-circuit, NOT a third runtime
# activation flag. The existing ``generation_enabled AND profile_configured``
# activation predicate is unchanged.
SEMANTIC_OUTLINE_HEADINGS_SUFFICIENT_THRESHOLD = 2
SEMANTIC_OUTLINE_SKIP_DIAGNOSTIC = "skipped_markdown_headings_sufficient"

_logger = logging.getLogger(__name__)

# Short-article batch path: whole-article batch compute, per-unit publish.
# When the active base text is below the short-article char threshold, the
# bootstrap creates a single batch job per layer (translation / vocabulary)
# instead of N per-unit jobs. The batch worker makes one LLM call covering all
# units; the batch publisher splits the output back into per-unit
# enhancement_layers rows so the existing frontend snapshot contract is
# preserved.
#
# Design: docs/architecture/reader-orchestration.md (Short Article Recovery Path).
TRANSLATION_BATCH_JOB_TYPE = "translate_article"
TRANSLATION_BATCH_TARGET_SCOPE = "unit_range"
TRANSLATION_BATCH_OPERATION_FINGERPRINT = "translation_article_v1"
TRANSLATION_BATCH_POLICY_VERSION = "reader_translation_batch_bootstrap_v1"
VOCABULARY_BATCH_JOB_TYPE = "build_vocabulary_layer_article"
VOCABULARY_BATCH_TARGET_SCOPE = "unit_range"
VOCABULARY_BATCH_OPERATION_FINGERPRINT = "vocabulary_article_v1"
VOCABULARY_BATCH_POLICY_VERSION = "reader_vocabulary_batch_bootstrap_v1"

# Structured article batch: STRUCTURED_BATCH gets its own
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
# asserts). Route hardening replaced it as the sole short/non-short
# discriminator: routing now uses ``estimated_word_count`` as the primary
# signal (see ``document_feature_extractor.SHORT_ARTICLE_MAX_WORD_COUNT``)
# with ``content_utf16_length`` only surviving as a coarse structured-tier
# guardrail. The reuters_bbc_970 golden sample (5982 chars / 984 words)
# stays on the short batch path under the new word-based router.
SHORT_ARTICLE_MAX_CHAR_COUNT = 6000

# Non-short vocabulary grouped execution: when the active base text
# exceeds SHORT_ARTICLE_MAX_CHAR_COUNT, vocabulary bootstrap splits the
# unpublished units into consecutive windows and creates one
# ``build_vocabulary_layer_article`` batch job per window. Each window is
# bounded by a target char count (close the window once reached) and a
# safety max (never exceed). A single unit larger than safety max becomes
# its own window. The unit is the minimum boundary — units are never split.
VOCABULARY_WINDOW_TARGET_CHAR_COUNT = 3000
VOCABULARY_WINDOW_SAFETY_MAX_CHAR_COUNT = 5000

# Non-short translation grouped execution: when the active base text
# exceeds SHORT_ARTICLE_MAX_CHAR_COUNT, translation bootstrap splits the
# unpublished units into consecutive windows and creates one
# ``translate_article`` batch job per window. Windows are bounded by a
# target char count (close the window once reached) and a safety max
# (never exceed). A single unit larger than safety max becomes its own
# window. The unit is the minimum boundary — units are never split.
#
# Translation window target/safety constants live in
# ``translation_window_plan`` and are re-exported above.

# Maps each enhancement job_type to the variant policy layer name it belongs
# to. ``generate_display_title_zh`` has no entry because the display title job
# does not consume a per-layer prompt policy; only records strategy metadata
# and fingerprint coverage. Layer prompts are wired into the workers.
_LAYER_NAME_BY_JOB_TYPE: dict[str, str] = {
    TRANSLATION_JOB_TYPE: "translation",
    TRANSLATION_BATCH_JOB_TYPE: "translation",
    VOCABULARY_JOB_TYPE: "vocabulary",
    VOCABULARY_BATCH_JOB_TYPE: "vocabulary",
    GRAMMAR_JOB_TYPE: "grammar_bundle",  # also covers GRAMMAR_BATCH_JOB_TYPE (same value)
}

# Enhancement job types managed by this bootstrap. Scopes the
# failed-predecessor collection for recovery events; unrelated terminal
# jobs (e.g. analysis-section lanes) are not reported as predecessors.
# ``build_grammar_bundle_window`` is kept as a literal because importing
# grammar_window_bootstrap at module load would create an import cycle
# (it must stay equal to grammar_window_bootstrap.GRAMMAR_WINDOW_JOB_TYPE).
_RECOVERY_ENHANCEMENT_JOB_TYPES: tuple[str, ...] = (
    DISPLAY_TITLE_JOB_TYPE,
    TRANSLATION_JOB_TYPE,
    TRANSLATION_BATCH_JOB_TYPE,
    VOCABULARY_JOB_TYPE,
    VOCABULARY_BATCH_JOB_TYPE,
    GRAMMAR_JOB_TYPE,
    "build_grammar_bundle_window",
    SEMANTIC_OUTLINE_JOB_TYPE,
)


def _compose_operation_fingerprint(
    base: str,
    strategy: ReaderVariantStrategy,
    *,
    semantic_token: str | None = None,
) -> str:
    """Compose a job operation fingerprint that covers the strategy hash.

    Any change to the resolved variant strategy (goal, variant, profile_id,
    annotation_density, strategy_version, or any layer prompt line) changes
    ``strategy_hash`` and therefore changes the composed fingerprint. This
    ensures that a policy text change does not silently reuse old job output:
    the ``reader_jobs.operation_fingerprint`` column differs, so the
    idempotency NOT EXISTS check treats the new fingerprint as a missing job.

    ``semantic_token`` (contract/resolver versions) is appended so automatic
    layer policy upgrades fence queued/retry jobs without reinterpreting them
    under a newer resolver.
    """
    composed = f"{base}:{strategy.strategy_hash}"
    if semantic_token:
        return f"{composed}:{semantic_token}"
    return composed


def _ensure_metadata_dict(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if hasattr(raw, "keys"):
        return dict(raw)
    return {}


def _unit_rows_to_maps(rows: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        item: dict[str, Any] = {
            "unit_id": str(row["unit_id"]),
            "order_index": int(row["order_index"]),
            "metadata_json": _ensure_metadata_dict(row.get("metadata_json")),
        }
        if "text_hash" in row and row["text_hash"] is not None:
            item["text_hash"] = str(row["text_hash"])
        if "base_start_utf16" in row and row["base_start_utf16"] is not None:
            item["base_start_utf16"] = int(row["base_start_utf16"])
        if "base_end_utf16" in row and row["base_end_utf16"] is not None:
            item["base_end_utf16"] = int(row["base_end_utf16"])
        if "unit_type" in row and row["unit_type"] is not None:
            item["unit_type"] = str(row["unit_type"])
        result.append(item)
    return result


async def _plan_first_analysis_section(
    conn: asyncpg.Connection,
    *,
    state: _LockedActiveBaseState,
) -> AnalysisSection | None:
    rows = await conn.fetch(
        """
        SELECT unit_id, order_index, base_start_utf16, base_end_utf16
        FROM reading_units
        WHERE reading_record_id = $1
          AND base_id = $2
        ORDER BY order_index ASC
        """,
        state.record_id,
        state.base_id,
    )
    if not rows:
        return None
    sections = plan_analysis_sections(
        str(state.base_id),
        [
            AnalysisSectionUnit(
                unit_id=str(row["unit_id"]),
                order_index=int(row["order_index"]),
                text_length=int(row["base_end_utf16"]) - int(row["base_start_utf16"]),
            )
            for row in rows
        ],
    )
    return sections[0] if sections else None


def _units_in_analysis_section(
    units: list[dict[str, Any]],
    section: AnalysisSection,
) -> list[dict[str, Any]]:
    by_id = {str(unit["unit_id"]): unit for unit in units}
    return [
        by_id[unit_id]
        for unit_id in section.target_unit_ids
        if unit_id in by_id
    ]


def _analysis_section_job_fields(
    section: AnalysisSection,
    *,
    article_route: str,
    request_origin: str = ANALYSIS_SECTION_REQUEST_ORIGIN,
) -> dict[str, Any]:
    return {
        "request_origin": request_origin,
        "analysis_section_id": section.section_id,
        "analysis_section_plan_version": ANALYSIS_SECTION_PLAN_VERSION,
        "analysis_section_order_index": section.order_index,
        "analysis_section_unit_ids": list(section.target_unit_ids),
        "requires_translation_terminal": True,
        "article_route": article_route,
    }


async def _resume_paused_analysis_section_job(
    conn: asyncpg.Connection,
    existing: asyncpg.Record,
) -> bool:
    payload = existing["input_json"] if isinstance(existing["input_json"], dict) else {}
    if not is_resumable_user_paused_analysis_job(
        job_type=str(existing["job_type"]),
        operation_fingerprint=str(existing["operation_fingerprint"] or ""),
        request_origin=payload.get("request_origin"),
        plan_version=payload.get("analysis_section_plan_version"),
        status=existing["status"],
        pause_owner=existing["pause_owner"],
        rationale_code=existing["rationale_code"],
        failure_class=existing["failure_class"],
        failure_code=existing["failure_code"],
    ):
        return False
    from app.services.reader_orchestration.job_runtime import (
        STATUS_QUEUED,
        ReaderJobRuntime,
    )

    await ReaderJobRuntime().transition_in_transaction(
        conn,
        job_id=existing["id"],
        target_status=STATUS_QUEUED,
        rationale_code="user_explicit_resume",
    )
    return True


def _filter_units_for_layer(
    rows: Any,
    layer: AutomaticLayerName,
    *,
    record_id: UUID | str | None = None,
    generation: int | None = None,
) -> list[dict[str, Any]]:
    """Shared pre-planning filter for all automatic bootstrap topologies.

    Mode is read from the single settings key
    ``reader_automatic_layer_policy_mode`` (off | shadow | enforce).
    """
    return filter_units_for_automatic_layer(
        _unit_rows_to_maps(rows),
        layer,
        mode=get_automatic_layer_policy_mode(),
        record_id=str(record_id) if record_id is not None else None,
        generation=generation,
    )


def build_semantic_fence_from_unit_maps(
    units: list[dict[str, Any]],
) -> dict[str, str | None]:
    """Public shared seam: build a semantic fence from raw unit maps.

    Single source of truth for both automatic bootstrap and explicit-section
    bootstrap. Delegates to :func:`generation_semantic_fence_from_targets`
    and raises :class:`SemanticFenceConstructionError` when target units
    carry mixed contract / resolver versions, or a mix of legacy and
    semantic units. Bootstrap callers MUST catch this error and fail closed
    before persisting any reader_jobs / reader_runs row.
    """
    typed: list[AutomaticLayerTargetUnit] = []
    for unit in units:
        resolved = policy_from_unit_metadata(unit.get("metadata_json") or {})
        typed.append(
            AutomaticLayerTargetUnit(
                unit_id=str(unit["unit_id"]),
                order_index=int(unit.get("order_index") or 0),
                metadata_json=dict(unit.get("metadata_json") or {}),
                contract_version=resolved.contract_version,
                resolver_version=(
                    "legacy_open" if resolved.is_legacy else resolved.resolver_version
                ),
                content_role=resolved.content_role,
                policy=resolved.policy,
            )
        )
    return generation_semantic_fence_from_targets(typed)


# Backwards-compat alias for in-tree callers that still import the private
# name. New code should call :func:`build_semantic_fence_from_unit_maps`
# directly; this alias exists only to keep the cross-module import surface
# stable during the convergence refactor.
_semantic_fence_from_unit_maps = build_semantic_fence_from_unit_maps


def _semantic_input_fields(
    fence: dict[str, str | None],
    *,
    layer: AutomaticLayerName | str,
) -> dict[str, Any]:
    """Freeze current policy mode into job input/fingerprint identity."""
    return build_semantic_fence_input_fields(
        fence,
        layer=layer,
        mode=get_automatic_layer_policy_mode(),
    )


def _semantic_fingerprint_token(fence: dict[str, str | None]) -> str:
    return compose_semantic_fingerprint_token(
        fence,
        mode=get_automatic_layer_policy_mode(),
    )


def _translation_profile_contract_for_units(
    units: list[dict[str, Any]],
    *,
    explicit_section: bool = False,
) -> dict[str, Any]:
    """Build the frozen translation prompt contract for target units."""

    return build_translation_prompt_profile_contract(
        units,
        explicit_section=explicit_section,
    )


def _translation_profile_fingerprint_token(contract: dict[str, Any]) -> str:
    return compose_translation_prompt_profile_fingerprint_token(contract)


def _build_strategy_metadata(
    strategy: ReaderVariantStrategy,
    layer_name: str | None,
) -> dict[str, Any]:
    """Build the strategy metadata block recorded on job input/envelope JSON.

     only persists metadata for audit and fingerprinting. It does NOT inject
    ``prompt_lines`` into worker prompts; will read this block to
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
    composed fingerprint (strategy-aware) is accepted.
    """
    return fingerprint == base or fingerprint.startswith(base + ":")


def _build_document_features_metadata(
    profile: DocumentFeatureProfile,
) -> dict[str, Any]:
    """Build a compact document-features block for ``envelope_json``.

    Records the deterministic profile signals that drove the route
    decision so the route is auditable and (compact grammar path)
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

    The defensive missing-base path caches no profile, so
    ``envelope_json.document_features`` is ``None`` for that branch. The
    normal path (SHORT_BATCH / STRUCTURED_BATCH / GROUPED_WINDOWED) always
    has a cached profile because ``_load_article_route`` populates
    ``state.cached_profile`` before returning.
    """
    if state.cached_profile is None:
        return None
    return _build_document_features_metadata(state.cached_profile)


# ---------------------------------------------------------------------------#
# Non-short vocabulary batch window planner
# ---------------------------------------------------------------------------#
# Pure dataclasses + function. No DB access, no side effects. The bootstrap
# method loads unit metadata (unit_id, order_index, text_length) and calls
# ``plan_vocabulary_windows`` to get a list of consecutive, non-overlapping
# windows. Each window becomes one ``build_vocabulary_layer_article`` job.
#
# Design constraints (see docs/development/mainline.md and docs/operations/testing.md):
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


# Translation window planner is imported from translation_window_plan.


# rationale_code written when a queued/retry_later/paused job is superseded
# because its operation_fingerprint no longer matches the current strategy
# fingerprint. Consumed by diagnostics and the pipeline runner's superseded
# counter.
_STRATEGY_FINGERPRINT_SUPERSEDED_RATIONALE = "strategy_fingerprint_superseded"

# rationale_code written when a queued/retry_later/paused legacy per-unit
# ``translate_unit`` job is superseded because the record has switched to the
# Grouped window ``translate_article`` path. Without this supersede the
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
    target_key: str | None = None,
) -> int:
    """Mark active stale-fingerprint jobs as superseded.

    Before bootstrapping jobs with the current strategy fingerprint, any
    pre-existing ``queued`` / ``retry_later`` / ``paused`` job of the same
    record / base / generation / job_type / target_scope whose
    ``operation_fingerprint`` differs from ``current_fingerprint`` is marked
    ``superseded`` with rationale_code
    ``strategy_fingerprint_superseded``.

    When ``target_key`` is set (analysis-section vocabulary/grammar),
    rotation is confined to that section. Ordinary callers omit it and
    keep the previous record-wide behavior.

    Only the **ordinary** translation lane is superseded
    (``request_origin IS DISTINCT FROM 'section_v1'``).

    ``claimed`` and ``succeeded`` jobs are intentionally left untouched:
    a claimed job is being actively processed by a worker, and a succeeded
    job has already published its layer (superseding it would not unpublish
    the layer).

    Returns the number of rows superseded.
    """
    params: list[object] = [
        record_id,
        base_id,
        expected_generation,
        job_type,
        target_scope,
        current_fingerprint,
        _STRATEGY_FINGERPRINT_SUPERSEDED_RATIONALE,
    ]
    target_clause = ""
    if target_key is not None:
        params.append(target_key)
        target_clause = f"AND target_key = ${len(params)}"
    result = await conn.execute(
        f"""
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
          {target_clause}
        """,
        *params,
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
    """Cutover: supersede active legacy ``translate_unit`` per-unit jobs.

    When a record switches from the legacy per-unit translation path to the
    grouped/window ``translate_article`` path, any pre-existing
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


@dataclass(frozen=True, slots=True)
class _EnhancementBootstrapBatch:
    """Per-capability results of one shared bootstrap-core transaction."""

    display_title_results: list[DisplayTitleBootstrapResult]
    translation_results: list[TranslationBootstrapResult]
    vocabulary_results: list[VocabularyBootstrapResult]
    grammar_results: list[GrammarBootstrapResult]
    semantic_outline_results: list[SemanticOutlineBootstrapResult]
    use_grammar_window_path: bool


@dataclass(frozen=True, slots=True)
class EnhancementRecoverySummary:
    """Outcome of one ``recover_failed_enhancement_jobs`` invocation.

    ``recovered`` is True only when the call actually created successor
    jobs or performed the failed -> readable_enhancing restore; a
    deterministic no-op (nothing left to recover) returns ``recovered=False``
    with no state change and no recovery event.
    """

    record_id: UUID
    base_id: UUID
    expected_generation: int
    trigger: str
    previous_product_state: str
    next_product_state: str
    predecessor_job_ids: tuple[UUID, ...] = ()
    successor_job_ids: tuple[UUID, ...] = ()
    successor_run_ids: tuple[UUID, ...] = ()
    successor_job_types: tuple[str, ...] = ()
    grammar_window_successor_job_ids: tuple[UUID, ...] = ()
    recovered: bool = False
    event_written: bool = False


# Injected request eligibility for semantic outline. Default is always-false
# (opt-in). Tests and future product flags inject predicates; length thresholds
# must not be hard-coded as product freezes in this module.
SemanticOutlineRequestEligibility = Callable[["_LockedActiveBaseState"], bool]


def default_semantic_outline_request_eligibility(
    state: _LockedActiveBaseState,
) -> bool:
    """Default: do not request outline jobs (explicit opt-in only)."""
    return False


def allow_semantic_outline_request_eligibility(
    state: _LockedActiveBaseState,
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
    """Build a request-eligibility predicate from settings.

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

     content-sufficiency short-circuit: when ``activation_ready`` is
    True AND ``state.unit_types`` is populated with at least
    :data:`SEMANTIC_OUTLINE_HEADINGS_SUFFICIENT_THRESHOLD` ``heading``
    units, the predicate returns False (skip outline job) and emits a
    structured ``skipped_markdown_headings_sufficient`` diagnostic log.
    When ``state.unit_types`` is ``None`` (not loaded by the caller), the
    predicate fail-closed to the activation-only result (``activation_ready``)
    so the existing behavior is preserved on code paths that do not
    pre-load units. This is a content-type eligibility short-circuit, NOT
    a third runtime activation flag; the ``generation_enabled AND
    profile_configured`` activation predicate is unchanged.
    """
    activation_ready = bool(
        settings.semantic_outline_generation_enabled
    ) and bool(settings.reader_semantic_outline_model_profile)

    def _predicate(state: _LockedActiveBaseState) -> bool:
        if not activation_ready:
            return False
        # Fail-closed when unit_types is not loaded — preserve existing
        # behavior (do not skip) on code paths that did not pre-load units.
        if state.unit_types is None:
            return True
        heading_count = sum(
            1 for unit_type in state.unit_types if unit_type == "heading"
        )
        if heading_count >= SEMANTIC_OUTLINE_HEADINGS_SUFFICIENT_THRESHOLD:
            _logger.info(
                "semantic_outline_skip reason=%s heading_count=%d threshold=%d "
                "record_id=%s base_id=%s",
                SEMANTIC_OUTLINE_SKIP_DIAGNOSTIC,
                heading_count,
                SEMANTIC_OUTLINE_HEADINGS_SUFFICIENT_THRESHOLD,
                state.record_id,
                state.base_id,
            )
            return False
        return True

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
    # Product state observed when the record row was locked. Ordinary
    # bootstrap only ever sees ``processing`` / ``readable_enhancing``;
    # the explicit recovery entry also loads ``failed`` records.
    product_state: str = "processing"
    # Short-article batch path: cached active base text. Populated
    # lazily by ``_load_article_route`` so the per-article route classifier
    # does not issue a second ``reading_bases.text`` SELECT when both the
    # translation and vocabulary bootstrap checks run for the same record.
    # ``None`` means "not loaded yet"; an empty string is a valid text.
    base_text: str | None = None
    # Deterministic document feature extractor: cached ordered
    # ``reading_units.unit_type`` sequence for the active base. Populated
    # lazily by ``_load_article_route`` and reused across the translation
    # and vocabulary route checks. ``None`` means "not loaded yet"; an
    # empty tuple is a valid (defensive) value for a base with no units.
    unit_types: tuple[str, ...] | None = None
    # Cached route decision. Once computed by
    # ``_load_article_route``, reused for the second call (vocabulary
    # after translation) so the route is stable within one
    # ``bootstrap_missing_jobs`` invocation. This also fixes the
    # missing-base defensive branch: the first call caches
    # ``GROUPED_WINDOWED``; without this cache the second call would see
    # non-None ``base_text=""`` / ``unit_types=()`` and re-evaluate an
    # empty profile, misclassifying it as ``SHORT_BATCH``.
    cached_route: ArticleRoute | None = None
    # Cached document feature profile. Populated alongside
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
    document features.

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

                unit_rows = await conn.fetch(
                    """
                    SELECT
                        u.unit_id,
                        u.order_index,
                        u.base_start_utf16,
                        u.base_end_utf16,
                        u.text_hash,
                        u.unit_type,
                        u.metadata_json
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
                allowed_units = _filter_units_for_layer(
                    unit_rows,
                    "translation",
                    record_id=state.record_id,
                    generation=state.expected_generation,
                )
                if not allowed_units:
                    raise ValueError("no untranslated reading unit is available")
                unit_row = allowed_units[0]
                semantic_fence = _semantic_fence_from_unit_maps(allowed_units)
                semantic_token = _semantic_fingerprint_token(semantic_fence)
                translation_profile_contract = _translation_profile_contract_for_units(
                    [unit_row]
                )
                translation_profile_token = _translation_profile_fingerprint_token(
                    translation_profile_contract
                )
                operation_fingerprint = _compose_operation_fingerprint(
                    TRANSLATION_OPERATION_FINGERPRINT,
                    state.strategy,
                    semantic_token=f"{semantic_token}:{translation_profile_token}",
                )
                translation_profile_fields = translation_prompt_profile_input_fields(
                    translation_profile_contract
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
                        **_semantic_input_fields(semantic_fence, layer='translation'),
                        **translation_profile_fields,
                    },
                    input_signature_suffix=(
                        f"{state.base_language}:{DEFAULT_TRANSLATION_TARGET_LANGUAGE}"
                    ),
                    input_json={
                        "base_language": state.base_language,
                        "target_language": DEFAULT_TRANSLATION_TARGET_LANGUAGE,
                        **_semantic_input_fields(semantic_fence, layer='translation'),
                        **translation_profile_fields,
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

                unit_rows = await conn.fetch(
                    """
                    SELECT
                        u.unit_id,
                        u.order_index,
                        u.text_hash,
                        u.metadata_json
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
                allowed_units = _filter_units_for_layer(
                    unit_rows,
                    "vocabulary",
                    record_id=state.record_id,
                    generation=state.expected_generation,
                )
                if not allowed_units:
                    raise ValueError("no unprocessed vocabulary reading unit is available")
                unit_row = allowed_units[0]
                semantic_fence = _semantic_fence_from_unit_maps(allowed_units)
                semantic_token = _semantic_fingerprint_token(semantic_fence)
                operation_fingerprint = _compose_operation_fingerprint(
                    VOCABULARY_OPERATION_FINGERPRINT,
                    state.strategy,
                    semantic_token=semantic_token,
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
                        **_semantic_input_fields(semantic_fence, layer='vocabulary'),
                    },
                    input_signature_suffix=f"{state.base_language}:vocabulary:1",
                    input_json={
                        "base_language": state.base_language,
                        "layer_type": "vocabulary",
                        **_semantic_input_fields(semantic_fence, layer='vocabulary'),
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

                # Pre-filter candidates for semantic policy before fingerprint.
                # Succeeded-job exclusion still applied after fingerprint is known.
                candidate_rows = await conn.fetch(
                    """
                    SELECT
                        u.unit_id,
                        u.order_index,
                        u.text_hash,
                        u.metadata_json
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
                from app.services.reader_orchestration.automatic_layer_policy import (
                    filter_units_for_any_grammar,
                )
                merged = filter_units_for_any_grammar(
                    _unit_rows_to_maps(candidate_rows),
                    mode=get_automatic_layer_policy_mode(),
                    record_id=str(state.record_id),
                    generation=state.expected_generation,
                )
                if not merged:
                    raise ValueError("no unprocessed grammar reading unit is available")
                semantic_fence = _semantic_fence_from_unit_maps(merged)
                semantic_token = _semantic_fingerprint_token(semantic_fence)
                operation_fingerprint = _compose_operation_fingerprint(
                    GRAMMAR_OPERATION_FINGERPRINT,
                    state.strategy,
                    semantic_token=semantic_token,
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
                # Drop units that already have a succeeded job for this fingerprint.
                unit_row = None
                for candidate in merged:
                    succeeded = await conn.fetchval(
                        """
                        SELECT 1
                        FROM reader_jobs job
                        WHERE job.reading_record_id = $1
                          AND job.base_id = $2
                          AND job.job_type = $3
                          AND job.target_type = $4
                          AND job.target_key = $5
                          AND job.expected_generation = $6
                          AND job.operation_fingerprint = $7
                          AND job.status = 'succeeded'
                        LIMIT 1
                        """,
                        state.record_id,
                        state.base_id,
                        GRAMMAR_JOB_TYPE,
                        GRAMMAR_TARGET_SCOPE,
                        candidate["unit_id"],
                        state.expected_generation,
                        operation_fingerprint,
                    )
                    if succeeded is None:
                        unit_row = candidate
                        break
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
                        **_semantic_input_fields(semantic_fence, layer='grammar_note'),
                    },
                    input_signature_suffix=f"{state.base_language}:grammar_bundle:1",
                    input_json={
                        "base_language": state.base_language,
                        "layer_types": ["grammar_note", "sentence_analysis"],
                        **_semantic_input_fields(semantic_fence, layer='grammar_note'),
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

    async def enqueue_analysis_section_jobs(
        self,
        conn: asyncpg.Connection,
        *,
        state: _LockedActiveBaseState,
        section: AnalysisSection,
        request_origin: str,
        include_vocabulary: bool,
        include_grammar: bool,
        resume_user_paused: bool = False,
    ) -> list[str]:
        """Create or resume missing V/G section jobs. Returns mutated capabilities."""
        route = await _load_article_route(conn, state=state)
        created: list[str] = []
        if include_vocabulary:
            vocab = await self._bootstrap_vocabulary_batch_job(
                conn,
                state=state,
                route=route,
                analysis_section=section,
                request_origin=request_origin,
                resume_user_paused=resume_user_paused,
            )
            if vocab:
                created.append("vocabulary")
        if include_grammar:
            grammar = await self._bootstrap_grammar_batch_job(
                conn,
                state=state,
                route=route,
                analysis_section=section,
                request_origin=request_origin,
                resume_user_paused=resume_user_paused,
            )
            if grammar:
                created.append("grammar")
        return created

    async def bootstrap_missing_jobs(
        self,
        *,
        record_id: UUID,
        user_id: UUID,
        trace_id: UUID | None = None,
        force_legacy_grammar: bool = False,
    ) -> EnhancementBootstrapSummary:
        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                state = await _load_locked_active_base_state(
                    conn,
                    record_id=record_id,
                    user_id=user_id,
                )
                if trace_id is None:
                    trace_id = uuid4()
                batch = await self._bootstrap_all_enhancement_jobs(
                    conn,
                    state=state,
                    trace_id=trace_id,
                    force_legacy_grammar=force_legacy_grammar,
                )

        # grammar-window path: dispatch to GrammarWindowBootstrapService AFTER the outer
        # transaction commits. GrammarWindowBootstrapService.bootstrap_grammar_window_plan
        # opens its own transaction and acquires its own FOR UPDATE lock on
        # reading_records, so calling it inside the outer transaction would
        # deadlock against the lock we already hold. Idempotent: if the plan
        # already exists with its windows/jobs, it is reused as-is.
        # Design: docs/architecture/reader-orchestration.md worker migration.
        # Pass the same trace_id used by display/translation/vocab runs so
        # window reader_runs.envelope_json carries the shared trace root
        # (requirement 5: same-record runs share one trace_id).
        if batch.use_grammar_window_path:
            await self._dispatch_grammar_window_plan(state=state, trace_id=trace_id)

        return EnhancementBootstrapSummary(
            record_id=state.record_id,
            base_id=state.base_id,
            expected_generation=state.expected_generation,
            last_event_sequence=state.last_event_sequence,
            job_counts=EnhancementBootstrapJobCounts(
                display_title=len(batch.display_title_results),
                translation=len(batch.translation_results),
                vocabulary=len(batch.vocabulary_results),
                grammar_bundle=len(batch.grammar_results),
                semantic_outline=len(batch.semantic_outline_results),
            ),
            display_title_results=tuple(batch.display_title_results),
            translation_results=tuple(batch.translation_results),
            vocabulary_results=tuple(batch.vocabulary_results),
            grammar_results=tuple(batch.grammar_results),
            semantic_outline_results=tuple(batch.semantic_outline_results),
        )

    async def _bootstrap_all_enhancement_jobs(
        self,
        conn: asyncpg.Connection,
        *,
        state: _LockedActiveBaseState,
        trace_id: UUID,
        force_legacy_grammar: bool = False,
        include_analysis_sections: bool = True,
    ) -> _EnhancementBootstrapBatch:
        """Shared transactional core: run every capability bootstrap helper.

        Used by both ``bootstrap_missing_jobs`` (ordinary gate) and
        ``recover_failed_enhancement_jobs`` (recovery-widened gate) so the
        two entry points cannot drift. Callers own the surrounding
        transaction; eligibility gates are enforced beforehand.

        ``include_analysis_sections=False`` is the record-level recovery
        lane: GROUPED_WINDOWED vocab/grammar bootstrap would otherwise
        rebuild first-section jobs whose failures belong to the
        analysis-section request flow.
        """
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
            include_analysis_sections=include_analysis_sections,
        )
        grammar_results, use_grammar_window_path = (
            await self._bootstrap_grammar_jobs_or_windowed(
                conn,
                state=state,
                trace_id=trace_id,
                force_legacy_grammar=force_legacy_grammar,
                include_analysis_sections=include_analysis_sections,
            )
        )
        semantic_outline_results = await self._bootstrap_semantic_outline_job(
            conn,
            state=state,
            trace_id=trace_id,
        )
        return _EnhancementBootstrapBatch(
            display_title_results=display_title_results,
            translation_results=translation_results,
            vocabulary_results=vocabulary_results,
            grammar_results=grammar_results,
            semantic_outline_results=semantic_outline_results,
            use_grammar_window_path=use_grammar_window_path,
        )

    async def _dispatch_grammar_window_plan(
        self,
        *,
        state: _LockedActiveBaseState,
        trace_id: UUID,
    ) -> None:
        """Post-commit idempotent grammar-window plan dispatch."""
        from .grammar_window_bootstrap import GrammarWindowBootstrapService

        grammar_window_service = GrammarWindowBootstrapService(pool=self._pool)
        await grammar_window_service.bootstrap_grammar_window_plan(
            record_id=state.record_id,
            base_id=state.base_id,
            trace_id=trace_id,
        )

    async def recover_failed_enhancement_jobs(
        self,
        *,
        record_id: UUID,
        user_id: UUID,
        trigger: str,
        trace_id: UUID | None = None,
    ) -> EnhancementRecoverySummary:
        """Explicit same-generation recovery for failed enhancement work.

        The ONLY entry point allowed to bootstrap a ``failed`` record.
        Contract:

        - trigger fail-closed (``manual`` / ``automatic``); article-ready
          readiness gate; ordinary bootstrap eligibility otherwise.
        - ``failed_terminal`` predecessors stay immutable audit rows;
          successors are new runs/jobs via the shared idempotent helpers.
        - Analysis-section lanes are excluded from predecessors AND from
          successor creation (their own request flow recovers them).
        - Window successors, the product_state restore, and the single
          ``record_state_changed`` recovery event all commit atomically in
          one transaction under the record lock; no work created means no
          flip and no event. No billing rows are written.
        """
        # Fail-closed trigger validation before touching the database.
        if trigger not in _RECOVERY_TRIGGER_KINDS:
            raise ValueError(
                "recovery trigger must be one of: "
                f"{sorted(_RECOVERY_TRIGGER_KINDS)}"
            )
        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                state = await _load_locked_active_base_state(
                    conn,
                    record_id=record_id,
                    user_id=user_id,
                    allowed_product_states=_RECOVERY_ELIGIBLE_PRODUCT_STATES,
                )
                # Fail-closed readiness gate: only article-ready records may
                # be restored to readable_enhancing.
                if state.readiness_state not in _ARTICLE_READY_READINESS_STATES:
                    raise ValueError(
                        "recovery requires an article-ready record "
                        f"(readiness_state={state.readiness_state!r})"
                    )
                if trace_id is None:
                    trace_id = uuid4()
                previous_product_state = state.product_state
                # Ordinary-lane predecessors only: analysis-section jobs
                # (request_origin in ANALYSIS_SECTION_ORIGINS) share some
                # job types but are recovered via their own request flow;
                # treating them as ordinary predecessors would report
                # failures this entry cannot rebuild.
                predecessor_rows = await conn.fetch(
                    """
                    SELECT id
                    FROM reader_jobs
                    WHERE reading_record_id = $1
                      AND base_id = $2
                      AND expected_generation = $3
                      AND status = 'failed_terminal'
                      AND job_type = ANY($4::text[])
                      AND COALESCE(input_json->>'request_origin', '')
                          <> ALL($5::text[])
                    ORDER BY created_at ASC, id ASC
                    """,
                    state.record_id,
                    state.base_id,
                    state.expected_generation,
                    list(_RECOVERY_ENHANCEMENT_JOB_TYPES),
                    list(ANALYSIS_SECTION_ORIGINS),
                )
                predecessor_job_ids = tuple(
                    UUID(str(row["id"])) for row in predecessor_rows
                )
                batch = await self._bootstrap_all_enhancement_jobs(
                    conn,
                    state=state,
                    trace_id=trace_id,
                    include_analysis_sections=False,
                )
                created_jobs: list[tuple[UUID, UUID, str]] = [
                    (result.run_id, result.job_id, job_type)
                    for results, job_type in (
                        (batch.display_title_results, DISPLAY_TITLE_JOB_TYPE),
                        (batch.translation_results, TRANSLATION_BATCH_JOB_TYPE),
                        (batch.vocabulary_results, VOCABULARY_BATCH_JOB_TYPE),
                        (batch.grammar_results, GRAMMAR_BATCH_JOB_TYPE),
                        (batch.semantic_outline_results, SEMANTIC_OUTLINE_JOB_TYPE),
                    )
                    for result in results
                ]
                # Grammar-window lane (legacy window plans): create window
                # successors inside THIS transaction under the same record
                # lock. ``recovered`` / the state flip / the event below are
                # driven only by successors actually created here; any
                # creation failure rolls the entire recovery back, and
                # concurrent recoveries serialize on the FOR UPDATE record
                # lock, so there is no pre-check/dispatch TOCTOU gap.
                from .grammar_window_bootstrap import (
                    GRAMMAR_WINDOW_JOB_TYPE,
                    GrammarWindowBootstrapService,
                )

                window_successors = await (
                    GrammarWindowBootstrapService(
                        pool=self._pool
                    ).recover_failed_terminal_window_jobs_in_transaction(
                        conn,
                        state=state,
                        trace_id=trace_id,
                    )
                )
                window_successor_job_ids = tuple(
                    job_id for _, job_id in window_successors
                )
                created_jobs.extend(
                    (run_id, job_id, GRAMMAR_WINDOW_JOB_TYPE)
                    for run_id, job_id in window_successors
                )
                recovered = bool(created_jobs)
                next_product_state = previous_product_state
                event_written = False
                if recovered:
                    if previous_product_state == "failed":
                        result = await conn.execute(
                            """
                            UPDATE reading_records
                            SET product_state = 'readable_enhancing',
                                updated_at = NOW()
                            WHERE id = $1
                              AND generation = $2
                              AND deleted_at IS NULL
                              AND lifecycle_status = 'active'
                              AND product_state = 'failed'
                            """,
                            state.record_id,
                            state.expected_generation,
                        )
                        if result != "UPDATE 1":
                            raise RuntimeError(
                                "recovery product_state restore failed for "
                                f"record {state.record_id}"
                            )
                        next_product_state = "readable_enhancing"
                    successor_job_types = tuple(
                        dict.fromkeys(job_type for _, _, job_type in created_jobs)
                    )
                    payload = {
                        "event_schema": RECOVERY_EVENT_SCHEMA,
                        "trigger": trigger,
                        "recovery_mode": RECOVERY_MODE_SAME_GENERATION_SUCCESSOR_JOBS,
                        "record_id": str(state.record_id),
                        "base_id": str(state.base_id),
                        "generation": state.expected_generation,
                        "trace_id": str(trace_id),
                        "previous_product_state": previous_product_state,
                        "next_product_state": next_product_state,
                        "predecessor_job_ids": [
                            str(job_id) for job_id in predecessor_job_ids
                        ],
                        "successor_job_ids": [
                            str(job_id) for _, job_id, _ in created_jobs
                        ],
                        "successor_run_ids": [
                            str(run_id) for run_id, _, _ in created_jobs
                        ],
                        "successor_job_types": list(successor_job_types),
                        "billing_mode": RECOVERY_BILLING_MODE,
                    }
                    await ReaderEventRuntime().publish_event_in_transaction(
                        conn,
                        record_id=state.record_id,
                        event_type="record_state_changed",
                        payload_json=payload,
                    )
                    event_written = True
                    _logger.info(
                        "reader_enhancement_recovery record_id=%s base_id=%s "
                        "trigger=%s previous_product_state=%s "
                        "next_product_state=%s predecessors=%d successors=%d "
                        "window_successors=%d",
                        state.record_id,
                        state.base_id,
                        trigger,
                        previous_product_state,
                        next_product_state,
                        len(predecessor_job_ids),
                        len(created_jobs),
                        len(window_successor_job_ids),
                    )

        return EnhancementRecoverySummary(
            record_id=state.record_id,
            base_id=state.base_id,
            expected_generation=state.expected_generation,
            trigger=trigger,
            previous_product_state=previous_product_state,
            next_product_state=next_product_state,
            predecessor_job_ids=predecessor_job_ids,
            successor_job_ids=tuple(job_id for _, job_id, _ in created_jobs),
            successor_run_ids=tuple(run_id for run_id, _, _ in created_jobs),
            successor_job_types=(
                tuple(dict.fromkeys(job_type for _, _, job_type in created_jobs))
            ),
            grammar_window_successor_job_ids=window_successor_job_ids,
            recovered=recovered,
            event_written=event_written,
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
        # Route hardening: classify via deterministic document
        # features (estimated_word_count primary, content_utf16_length as a
        # coarse structured-tier guardrail) instead of the legacy raw
        # ``content_utf16_length`` boolean.
        # SHORT_BATCH and STRUCTURED_BATCH both execute via the
        # whole-article batch job, but with distinct operation_fingerprint
        # / policy_version / input_json.article_route so the route is
        # auditable and a route change supersedes old jobs.
        # GROUPED_WINDOWED splits into per-window batch jobs.
        route = await _load_article_route(conn, state=state)
        if route is not ArticleRoute.GROUPED_WINDOWED:
            return await self._bootstrap_translation_batch_job(
                conn, state=state, route=route, trace_id=trace_id
            )
        # Non-short grouped path: split unpublished units into
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
        include_analysis_sections: bool = True,
    ) -> list[VocabularyBootstrapResult]:
        # Route hardening: classify via deterministic document
        # features (see ``_bootstrap_translation_jobs``).
        # SHORT_BATCH and STRUCTURED_BATCH both execute via the
        # whole-article vocabulary batch job, but with distinct
        # operation_fingerprint / policy_version / input_json.article_route.
        # GROUPED_WINDOWED creates one first-section vocabulary batch job.
        route = await _load_article_route(conn, state=state)
        if route is not ArticleRoute.GROUPED_WINDOWED:
            return await self._bootstrap_vocabulary_batch_job(
                conn, state=state, route=route, trace_id=trace_id
            )
        if not include_analysis_sections:
            # Record-level recovery lane: section jobs are recovered via
            # their own request flow; never rebuild the first section here.
            return []
        section = await _plan_first_analysis_section(conn, state=state)
        if section is None:
            return []
        return await self._bootstrap_vocabulary_batch_job(
            conn,
            state=state,
            route=route,
            trace_id=trace_id,
            analysis_section=section,
        )

    async def _bootstrap_vocabulary_grouped_jobs(
        self,
        conn: asyncpg.Connection,
        *,
        state: _LockedActiveBaseState,
        route: ArticleRoute,
        trace_id: UUID | None = None,
    ) -> list[VocabularyBootstrapResult]:
        """Non-short vocabulary grouped/window execution.

        Queries unpublished units (ordered by ``order_index``), plans
        consecutive windows via :func:`plan_vocabulary_windows`, and
        creates one ``build_vocabulary_layer_article`` batch job per
        window. Each window job has a distinct ``target_key`` /
        ``idempotency_key`` / ``input_hash`` so multiple windows on the
        same record do not collide.

         route identity: ``route`` is recorded as ``article_route``
        in ``envelope_json`` / ``input_json`` for audit consistency with
        the batch path. GROUPED_WINDOWED keeps its existing
        ``vocabulary_article_v1`` fingerprint base (shared with
        SHORT_BATCH) so its idempotency contract is preserved; the
        three-way distinction is completed by ``article_route`` in
        ``input_json``.

        Cross-window duplicate headword policy (v1): each window may
        independently highlight the same headword once. Cross-window
        dedup is NOT performed; this is acceptable for v1 and is locked
        by tests. See docs/development/mainline.md and docs/operations/testing.md (risk A).
        """
        if trace_id is None:
            trace_id = uuid4()
        # Load unpublished units first so the semantic fence can enter the fingerprint.
        # Load unpublished units with their UTF-16 char length for windowing.
        # ``base_end_utf16 - base_start_utf16`` matches the worker's
        # ``slice_by_utf16_offsets`` unit text length exactly.
        rows = await conn.fetch(
            """
            SELECT
                u.unit_id,
                u.order_index,
                u.base_start_utf16,
                u.base_end_utf16,
                u.metadata_json
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
        allowed = _filter_units_for_layer(
            rows,
            "vocabulary",
            record_id=state.record_id,
            generation=state.expected_generation,
        )
        if not allowed:
            return []
        semantic_fence = _semantic_fence_from_unit_maps(allowed)
        semantic_token = _semantic_fingerprint_token(semantic_fence)
        operation_fingerprint = _compose_operation_fingerprint(
            VOCABULARY_BATCH_OPERATION_FINGERPRINT,
            state.strategy,
            semantic_token=semantic_token,
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
        window_units = [
            VocabularyWindowUnit(
                unit_id=str(row["unit_id"]),
                order_index=int(row["order_index"]),
                text_length=int(row["base_end_utf16"]) - int(row["base_start_utf16"]),
            )
            for row in allowed
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
                    **_semantic_input_fields(semantic_fence, layer='vocabulary'),
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
                    **_semantic_input_fields(semantic_fence, layer='vocabulary'),
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

    async def _bootstrap_grammar_jobs_or_windowed(
        self,
        conn: asyncpg.Connection,
        *,
        state: _LockedActiveBaseState,
        trace_id: UUID | None = None,
        force_legacy_grammar: bool = False,
        include_analysis_sections: bool = True,
    ) -> tuple[list[GrammarBootstrapResult], bool]:
        """Route-aware grammar bootstrap routing.

        Three-way split:

        - ``force_legacy_grammar=True`` → legacy per-unit
          ``_bootstrap_grammar_jobs`` (fallback, returns ``([], False)``).
        - ``GROUPED_WINDOWED`` → grammar-window analysis-window path (returns
          ``([], True)``; caller dispatches to
          ``GrammarWindowBootstrapService.bootstrap_grammar_window_plan`` after
          the outer transaction commits). Long-article grammar contract
          is unchanged.
        - ``SHORT_BATCH`` / ``STRUCTURED_BATCH`` → compact grammar batch
          path (returns ``(results, False)``). One
          ``build_grammar_bundle`` / ``unit_range`` batch job covers all
          unpublished units in a single LLM call; no
          ``analysis_windows`` / ``layer_analysis_plans`` are created.

        Design: docs/architecture/reader-orchestration.md.
        """
        if force_legacy_grammar:
            results = await self._bootstrap_grammar_jobs(
                conn,
                state=state,
                trace_id=trace_id,
            )
            return results, False
        # Route-aware split. GROUPED_WINDOWED uses one first-section
        # compact grammar batch job. SHORT_BATCH / STRUCTURED_BATCH keep
        # the whole-article compact batch path.
        route = await _load_article_route(conn, state=state)
        if route is ArticleRoute.GROUPED_WINDOWED:
            if not include_analysis_sections:
                # Record-level recovery lane: section jobs are recovered
                # via their own request flow; never rebuild the first
                # section here.
                return [], False
            section = await _plan_first_analysis_section(conn, state=state)
            if section is None:
                return [], False
            results = await self._bootstrap_grammar_batch_job(
                conn,
                state=state,
                route=route,
                trace_id=trace_id,
                analysis_section=section,
            )
            return results, False
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
        analysis_section: AnalysisSection | None = None,
        request_origin: str = ANALYSIS_SECTION_REQUEST_ORIGIN,
        resume_user_paused: bool = False,
    ) -> list[GrammarBootstrapResult]:
        """Compact grammar batch bootstrap for short/structured articles.

        Creates a single ``build_grammar_bundle`` / ``unit_range``
        reader job whose ``input_json.target_unit_ids`` lists every unit
        that still needs a grammar layer. The batch worker makes one LLM
        call covering all units; the batch publisher splits the output
        back into per-unit ``enhancement_layers`` rows.

         route identity: ``route`` selects the operation_fingerprint
        base and policy_version. ``STRUCTURED_BATCH`` gets a distinct
        fingerprint so a route change (short -> structured on a rebuilt
        base) triggers ``_supersede_stale_fingerprint_jobs``. Both
        ``SHORT_BATCH`` and ``STRUCTURED_BATCH`` record ``article_route``
        in ``envelope_json`` and ``input_json``; ``document_features``
        is recorded in ``envelope_json`` only (workers needing the
        profile read it from the run envelope).

        No ``analysis_windows`` / ``layer_analysis_plans`` are created —
        this is the key cost/latency win over the grammar-window path for short and
        medium articles.
        """
        if trace_id is None:
            trace_id = uuid4()
        if analysis_section is not None:
            fingerprint_base = GRAMMAR_ANALYSIS_SECTION_FINGERPRINT
            policy_version = GRAMMAR_ANALYSIS_SECTION_POLICY_VERSION
            route_suffix = f"analysis_section:{analysis_section.section_id}"
        elif route is ArticleRoute.STRUCTURED_BATCH:
            fingerprint_base = GRAMMAR_STRUCTURED_BATCH_OPERATION_FINGERPRINT
            policy_version = GRAMMAR_STRUCTURED_BATCH_POLICY_VERSION
            route_suffix = "structured"
        else:
            fingerprint_base = GRAMMAR_BATCH_OPERATION_FINGERPRINT
            policy_version = GRAMMAR_BATCH_POLICY_VERSION
            route_suffix = "short"
        rows = await conn.fetch(
            """
            SELECT u.unit_id, u.order_index, u.text_hash, u.metadata_json
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
        from app.services.reader_orchestration.automatic_layer_policy import (
            filter_units_for_any_grammar,
        )
        allowed = filter_units_for_any_grammar(
            _unit_rows_to_maps(rows),
            mode=get_automatic_layer_policy_mode(),
            record_id=str(state.record_id),
            generation=state.expected_generation,
        )
        if analysis_section is not None:
            allowed = _units_in_analysis_section(allowed, analysis_section)
        if not allowed:
            return []
        target_unit_ids = [str(row["unit_id"]) for row in allowed]
        section_fields = (
            _analysis_section_job_fields(
                analysis_section,
                article_route=route.value,
                request_origin=request_origin,
            )
            if analysis_section is not None
            else {}
        )
        target_key = (
            analysis_section.section_id
            if analysis_section is not None
            else str(state.record_id)
        )
        semantic_fence = _semantic_fence_from_unit_maps(allowed)
        semantic_token = _semantic_fingerprint_token(semantic_fence)
        operation_fingerprint = _compose_operation_fingerprint(
            fingerprint_base,
            state.strategy,
            semantic_token=semantic_token,
        )
        await _supersede_stale_fingerprint_jobs(
            conn,
            record_id=state.record_id,
            base_id=state.base_id,
            expected_generation=state.expected_generation,
            job_type=GRAMMAR_BATCH_JOB_TYPE,
            target_scope=GRAMMAR_BATCH_TARGET_SCOPE,
            current_fingerprint=operation_fingerprint,
            target_key=target_key if analysis_section is not None else None,
        )

        existing_job = await conn.fetchrow(
            """
            SELECT *
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
            target_key,
            state.expected_generation,
            operation_fingerprint,
        )
        if existing_job is not None:
            if resume_user_paused and await _resume_paused_analysis_section_job(
                conn, existing_job
            ):
                return [
                    GrammarBootstrapResult(
                        run_id=existing_job["run_id"],
                        job_id=existing_job["id"],
                        reading_record_id=state.record_id,
                        base_id=state.base_id,
                        unit_id=target_unit_ids[0],
                        expected_generation=state.expected_generation,
                        operation_fingerprint=operation_fingerprint,
                    )
                ]
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
                **_semantic_input_fields(semantic_fence, layer='grammar_note'),
                **section_fields,
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
                **_semantic_input_fields(semantic_fence, layer='grammar_note'),
                **section_fields,
            },
            layer_name=_LAYER_NAME_BY_JOB_TYPE[GRAMMAR_BATCH_JOB_TYPE],
            target_key_override=target_key,
            idempotency_key_suffix=(
                f"analysis_section:{analysis_section.section_id}"
                if analysis_section is not None
                else "batch"
            ),
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
        """ whole-article translation batch bootstrap.

        Creates a single ``translate_article`` / ``unit_range`` reader job
        whose ``input_json.target_unit_ids`` lists every unit that still
        needs a translation layer. The batch worker makes one LLM call
        covering all units; the batch publisher splits the output back
        into per-unit ``enhancement_layers`` rows.

         route identity: ``route`` selects the operation_fingerprint
        base and policy_version. ``STRUCTURED_BATCH`` gets a distinct
        fingerprint so a route change (short -> structured on a rebuilt
        base) triggers ``_supersede_stale_fingerprint_jobs``. Both
        ``SHORT_BATCH`` and ``STRUCTURED_BATCH`` record ``article_route``
        in ``envelope_json`` and ``input_json``; ``document_features``
        is recorded in ``envelope_json`` only (workers needing the
        profile read it from the run envelope). This is the
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
        rows = await conn.fetch(
            """
            SELECT u.unit_id, u.order_index, u.text_hash, u.unit_type, u.metadata_json
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
        allowed = _filter_units_for_layer(
            rows,
            "translation",
            record_id=state.record_id,
            generation=state.expected_generation,
        )
        if not allowed:
            return []
        target_unit_ids = [str(row["unit_id"]) for row in allowed]
        semantic_fence = _semantic_fence_from_unit_maps(allowed)
        semantic_token = _semantic_fingerprint_token(semantic_fence)
        translation_profile_contract = _translation_profile_contract_for_units(allowed)
        translation_profile_token = _translation_profile_fingerprint_token(
            translation_profile_contract
        )
        operation_fingerprint = _compose_operation_fingerprint(
            fingerprint_base,
            state.strategy,
            semantic_token=f"{semantic_token}:{translation_profile_token}",
        )
        translation_profile_fields = translation_prompt_profile_input_fields(
            translation_profile_contract
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
                **_semantic_input_fields(semantic_fence, layer='translation'),
                **translation_profile_fields,
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
                **_semantic_input_fields(semantic_fence, layer='translation'),
                **translation_profile_fields,
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
        """Non-short translation grouped/window execution.

        Queries unpublished units (ordered by ``order_index``), plans
        consecutive windows via :func:`plan_translation_windows`, and
        creates one ``translate_article`` batch job per window. Each
        window job has a distinct ``target_key`` / ``idempotency_key`` /
        ``input_hash`` so multiple windows on the same record do not
        collide.

        The batch worker and publisher are window-agnostic: they read
        ``input_json.target_unit_ids`` and only process/publish that
        subset. Each unit's ``output_json.groups`` is still produced by
        :func:`build_deterministic_translation_groups`, preserving
        the Translation Group semantic contract regardless of how many
        units a window covers. No parallel job type or migration is
        introduced.

         route identity: ``route`` is recorded as ``article_route``
        in ``envelope_json`` / ``input_json`` for audit consistency with
        the batch path. GROUPED_WINDOWED keeps its existing
        ``translation_article_v1`` fingerprint base (shared with
        SHORT_BATCH) so its idempotency contract is preserved; the
        three-way distinction is completed by ``article_route`` in
        ``input_json``.

        Cutover safety (review):

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
        # Supersede MUST run before the unpublished-units query because that
        # query excludes units already targeted by any active translate_article
        # job (any fingerprint). Without superseding first, a strategy/policy
        # upgrade would see zero candidates and never rotate jobs.
        meta_rows = await conn.fetch(
            """
            SELECT unit_id, order_index, unit_type, metadata_json
            FROM reading_units
            WHERE reading_record_id = $1 AND base_id = $2
            ORDER BY order_index ASC
            """,
            state.record_id,
            state.base_id,
        )
        meta_maps = _unit_rows_to_maps(meta_rows)
        semantic_fence = _semantic_fence_from_unit_maps(meta_maps)
        semantic_token = _semantic_fingerprint_token(semantic_fence)
        translation_profile_operation_contract = (
            _translation_profile_contract_for_units(meta_maps)
        )
        translation_profile_token = _translation_profile_fingerprint_token(
            translation_profile_operation_contract
        )
        operation_fingerprint = _compose_operation_fingerprint(
            TRANSLATION_BATCH_OPERATION_FINGERPRINT,
            state.strategy,
            semantic_token=f"{semantic_token}:{translation_profile_token}",
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
        # Cutover: supersede legacy per-unit ``translate_unit`` jobs
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
                u.base_end_utf16,
                u.unit_type,
                u.metadata_json
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
        allowed = _filter_units_for_layer(
            rows,
            "translation",
            record_id=state.record_id,
            generation=state.expected_generation,
        )
        if not allowed:
            return []
        window_units = [
            TranslationWindowUnit(
                unit_id=str(row["unit_id"]),
                order_index=int(row["order_index"]),
                text_length=int(row["base_end_utf16"]) - int(row["base_start_utf16"]),
            )
            for row in allowed
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
            window_maps = [
                row for row in allowed if str(row["unit_id"]) in target_unit_ids
            ]
            translation_profile_contract = _translation_profile_contract_for_units(
                window_maps
            )
            translation_profile_fields = translation_prompt_profile_input_fields(
                translation_profile_contract,
                fingerprint_contract=translation_profile_operation_contract,
            )
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
                    **_semantic_input_fields(semantic_fence, layer='translation'),
                    **translation_profile_fields,
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
                    **_semantic_input_fields(semantic_fence, layer='translation'),
                    **translation_profile_fields,
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
        analysis_section: AnalysisSection | None = None,
        request_origin: str = ANALYSIS_SECTION_REQUEST_ORIGIN,
        resume_user_paused: bool = False,
    ) -> list[VocabularyBootstrapResult]:
        """ whole-article vocabulary batch bootstrap.

        Mirrors :meth:`_bootstrap_translation_batch_job` for the vocabulary
        layer. Same idempotency contract; ``target_unit_ids`` lists every
        unit that still needs a vocabulary layer.

         route identity: ``route`` selects the operation_fingerprint
        base and policy_version. ``STRUCTURED_BATCH`` gets a distinct
        fingerprint so a route change (short -> structured on a rebuilt
        base) triggers ``_supersede_stale_fingerprint_jobs``. Both
        ``SHORT_BATCH`` and ``STRUCTURED_BATCH`` record ``article_route``
        in ``envelope_json`` and ``input_json``; ``document_features``
        is recorded in ``envelope_json`` only (workers needing the
        profile read it from the run envelope). This is the
        grammar compact path hook.
        """
        if trace_id is None:
            trace_id = uuid4()
        if analysis_section is not None:
            fingerprint_base = VOCABULARY_ANALYSIS_SECTION_FINGERPRINT
            policy_version = VOCABULARY_ANALYSIS_SECTION_POLICY_VERSION
            route_suffix = f"analysis_section:{analysis_section.section_id}"
        elif route is ArticleRoute.STRUCTURED_BATCH:
            fingerprint_base = VOCABULARY_STRUCTURED_BATCH_OPERATION_FINGERPRINT
            policy_version = VOCABULARY_STRUCTURED_BATCH_POLICY_VERSION
            route_suffix = "structured"
        else:
            fingerprint_base = VOCABULARY_BATCH_OPERATION_FINGERPRINT
            policy_version = VOCABULARY_BATCH_POLICY_VERSION
            route_suffix = "short"
        rows = await conn.fetch(
            """
            SELECT u.unit_id, u.order_index, u.text_hash, u.metadata_json
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
        allowed = _filter_units_for_layer(
            rows,
            "vocabulary",
            record_id=state.record_id,
            generation=state.expected_generation,
        )
        if analysis_section is not None:
            allowed = _units_in_analysis_section(allowed, analysis_section)
        if not allowed:
            return []
        target_unit_ids = [str(row["unit_id"]) for row in allowed]
        section_fields = (
            _analysis_section_job_fields(
                analysis_section,
                article_route=route.value,
                request_origin=request_origin,
            )
            if analysis_section is not None
            else {}
        )
        target_key = (
            analysis_section.section_id
            if analysis_section is not None
            else str(state.record_id)
        )
        semantic_fence = _semantic_fence_from_unit_maps(allowed)
        semantic_token = _semantic_fingerprint_token(semantic_fence)
        operation_fingerprint = _compose_operation_fingerprint(
            fingerprint_base,
            state.strategy,
            semantic_token=semantic_token,
        )
        await _supersede_stale_fingerprint_jobs(
            conn,
            record_id=state.record_id,
            base_id=state.base_id,
            expected_generation=state.expected_generation,
            job_type=VOCABULARY_BATCH_JOB_TYPE,
            target_scope=VOCABULARY_BATCH_TARGET_SCOPE,
            current_fingerprint=operation_fingerprint,
            target_key=target_key if analysis_section is not None else None,
        )

        existing_job = await conn.fetchrow(
            """
            SELECT *
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
            target_key,
            state.expected_generation,
            operation_fingerprint,
        )
        if existing_job is not None:
            if resume_user_paused and await _resume_paused_analysis_section_job(
                conn, existing_job
            ):
                return [
                    VocabularyBootstrapResult(
                        run_id=existing_job["run_id"],
                        job_id=existing_job["id"],
                        reading_record_id=state.record_id,
                        base_id=state.base_id,
                        unit_id=target_unit_ids[0],
                        expected_generation=state.expected_generation,
                        operation_fingerprint=operation_fingerprint,
                    )
                ]
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
                **_semantic_input_fields(semantic_fence, layer='vocabulary'),
                "layer_type": "vocabulary",
                "trace_id": str(trace_id),
                "article_route": route.value,
                "document_features": _route_document_features(state),
                **section_fields,
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
                **_semantic_input_fields(semantic_fence, layer='vocabulary'),
                **section_fields,
            },
            layer_name=_LAYER_NAME_BY_JOB_TYPE[VOCABULARY_BATCH_JOB_TYPE],
            target_key_override=target_key,
            idempotency_key_suffix=(
                f"analysis_section:{analysis_section.section_id}"
                if analysis_section is not None
                else "batch"
            ),
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
    allowed_product_states: frozenset[str] | None = None,
) -> _LockedActiveBaseState:
    """Lock the record and validate the active-base fence.

    ``allowed_product_states`` defaults to
    :data:`_BOOTSTRAP_READY_PRODUCT_STATES`; only the explicit
    failed-enhancement recovery entry widens the gate (it passes
    :data:`_RECOVERY_ELIGIBLE_PRODUCT_STATES`). All other eligibility
    checks (ownership, lifecycle, base ownership, generation fence,
    base status) are identical in both modes.
    """
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
    ready_states = (
        allowed_product_states
        if allowed_product_states is not None
        else _BOOTSTRAP_READY_PRODUCT_STATES
    )
    if record_row["product_state"] not in ready_states:
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
    # reading_goal / reading_variant columns. These are persisted facts (
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
        product_state=str(record_row["product_state"]),
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

    : before invoking ``request_eligibility``, lazily load
    ``state.unit_types`` when not already cached so the settings-aware
    predicate can apply the content-sufficiency short-circuit (heading
    count ≥ threshold). Code paths that already populated ``unit_types``
    (e.g., ``_bootstrap_missing_jobs`` via ``_load_article_route``) reuse
    the cached value with no extra DB call.
    """
    if state.readiness_state not in _ARTICLE_READY_READINESS_STATES:
        return []
    # Ensure unit_types is loaded so the predicate can inspect heading
    # count for the content-sufficiency short-circuit. Fail-closed: if the
    # load returns no rows, ``unit_types`` becomes ``()`` (not ``None``),
    # which the predicate treats as "no headings" → no skip.
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
    """Short-article batch path: insert one record-level batch job.

    Mirrors :func:`_insert_unit_job` but covers a range of units in a single
    job. Differences from the per-unit helper:

    - ``target_key`` defaults to ``str(state.record_id)`` (record-level,
      like the display-title job), not a single ``unit_id``.
      ``target_key_override`` lets the caller set a window-specific
      target_key for non-short grouped vocabulary jobs.
    - ``input_json`` carries ``target_scope: "unit_range"`` and
      ``target_unit_ids: [...]`` (list of every unit id covered by the
      batch). The caller is responsible for putting these fields in
      ``input_json``; this helper only adds the standard record-level
      metadata (record_id / base_id / expected_generation) and the
      strategy metadata block.
    - ``idempotency_key`` is suffixed with ``idempotency_key_suffix``
      (default ``:batch``) so the per-unit and per-article idempotency
      spaces do not collide. passes ``window:{window_id}`` to keep
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
