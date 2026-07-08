from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

import asyncpg
from pydantic_ai import Agent

from app.config.settings import Settings, get_settings
from app.contracts.annotation import compute_text_range_hash, slice_by_utf16_offsets
from app.database import connection as db_connection
from app.llm.agent_runner import extract_run_usage
from app.llm.call_guard import assert_real_llm_allowed
from app.llm.router import build_model_for_route
from app.llm.routes import MODEL_ROUTE_READER_LAYER_TRANSLATION
from app.schemas.reader_orchestration import (
    TranslationBatchGenerationOutput,
    TranslationBatchGroupOutput,
    TranslationBatchUnitOutput,
    TranslationGroup,
    TranslationLayerGenerationOutput,
    TranslationLayerOutput,
)
from app.services.ai_usage import (
    BILLING_MODE_INTERNAL_ONLY,
    CAPABILITY_READER_TRANSLATION,
    STATUS_FAILED,
    STATUS_SUCCEEDED,
    USAGE_SCOPE_SYSTEM_INTERNAL,
    AIUsageEventCreate,
    record_ai_usage_event,
)
from app.services.analysis.prompting.prompt_loader import (
    get_prompt_version,
    load_agent_instructions,
)

from .job_bootstrap import (
    DEFAULT_TRANSLATION_TARGET_LANGUAGE,
    TRANSLATION_BATCH_JOB_TYPE,
    TRANSLATION_BATCH_OPERATION_FINGERPRINT,
    TRANSLATION_BATCH_TARGET_SCOPE,
    TRANSLATION_JOB_TYPE,
    TRANSLATION_OPERATION_FINGERPRINT,
    TRANSLATION_TARGET_SCOPE,
)
from .job_runtime import ClaimResult, FenceViolationError, ReaderJobRuntime
from .layer_publisher import (
    PublishedTranslationBatch,
    PublishedTranslationLayer,
    TranslationLayerPublisher,
)
from .reading_strategy import (
    ReaderStrategyResolverError,
    resolve_reader_variant_strategy,
)
from .span_recorder import (
    end_worker_span_execution_error,
    end_worker_span_fence_violation,
    end_worker_span_generic_exception,
    end_worker_span_success,
)

DEFAULT_TRANSLATION_RETRY_DELAY = timedelta(minutes=5)
TRANSLATION_PROMPT_AGENT_NAME = "reader_layer_translation"

# Dedicated batch-path agent instructions. The per-unit
# ``reader_layer_translation`` instructions teach the model to choose
# semantic reading groups and forbid one-group-per-anchor-segment; that
# guidance directly contradicts the batch deterministic-grouping contract
# (one backend-predefined group per anchor segment, LLM only echoes
# ``group_id`` + ``translated_text``). Feeding both to the batch agent
# would give the real LLM contradictory signals. The batch path therefore
# uses this dedicated instruction set; the variant policy lines are still
# injected via the ``<strategy>`` section of the per-call prompt.
_TRANSLATION_BATCH_AGENT_INSTRUCTIONS = (
    "You are a batch translation agent for short reading articles.\n"
    "The translation groups are PRE-DEFINED by the backend, listed in "
    "<translation_groups> in reading order. Each group covers one or more "
    "contiguous anchor segments within a reading unit and declares its "
    "group_id, unit_id, anchor_segment_ids, source_text_hash, and "
    "source_text.\n"
    "Your ONLY job is to translate each group's <source_text> into the "
    "target language and return group_id + translated_text for that group.\n"
    "Hard rules:\n"
    "- Return exactly one group entry per listed <translation_group>, in "
    "the same unit_id grouping.\n"
    "- Echo the group_id exactly as given; never invent, merge, split, "
    "reorder, add, or drop groups.\n"
    "- Never output anchor_segment_ids, source_text, source_text_hash, or "
    "any field other than group_id and translated_text.\n"
    "- Never shift a translation onto a different group_id than the one "
    "whose source_text it translates. The backend binds each group_id to "
    "its source anchor deterministically; wrong translated_text under a "
    "group_id is a translation-quality failure, not an anchor remapping.\n"
    "- Translate each group's source_text faithfully and idiomatically "
    "into the target language, respecting the <strategy> policy lines.\n"
)

# Strategy metadata keys that T5 bootstrap writes into reader_jobs.input_json.
# T6 reads them back and validates against the live resolver output. Missing
# keys or hash mismatch fail closed; legacy bare-fingerprint jobs without
# strategy metadata are rejected as validation errors, never silently
# downgraded to a default strategy.
_STRATEGY_INPUT_KEYS: tuple[str, ...] = (
    "reading_goal",
    "reading_variant",
    "strategy_version",
    "strategy_hash",
    "layer_policy_hash",
)
_TRANSLATION_LAYER_NAME = "translation"
_STRATEGY_METADATA_MISSING_CODE = "strategy_metadata_missing"
_STRATEGY_HASH_MISMATCH_CODE = "strategy_hash_mismatch"
_LAYER_POLICY_HASH_MISMATCH_CODE = "layer_policy_hash_mismatch"
_STRATEGY_VERSION_MISMATCH_CODE = "strategy_version_mismatch"


@dataclass(frozen=True, slots=True)
class TranslationAnchorSegmentTarget:
    anchor_segment_id: str
    sentence_id: str | None
    order_index: int
    segment_type: str
    boundary_quality: str
    unit_start_utf16: int
    unit_end_utf16: int
    text_hash: str
    source_text: str


@dataclass(frozen=True, slots=True)
class TranslationJobContext:
    job_id: UUID
    run_id: UUID
    reading_record_id: UUID
    user_id: UUID
    base_id: UUID
    unit_id: str
    order_index: int
    expected_generation: int
    operation_fingerprint: str
    source_language: str
    target_language: str
    source_text: str
    text_hash: str
    anchor_segments: tuple[TranslationAnchorSegmentTarget, ...]
    # T6 strategy fields. Populated by _load_job_context from
    # reader_jobs.input_json (written by T5 bootstrap) and cross-validated
    # against resolve_reader_variant_strategy(). Fail-closed contract:
    # missing metadata or hash mismatch never falls back to a default.
    reading_goal: str
    reading_variant: str
    strategy_version: str
    strategy_hash: str
    layer_policy_hash: str
    translation_prompt_lines: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TranslationExecutionResult:
    output: TranslationLayerGenerationOutput
    usage_data: dict[str, Any] | None = None
    prompt_version: str | None = None
    model_route: str = MODEL_ROUTE_READER_LAYER_TRANSLATION
    model_profile: str | None = None
    model_provider: str | None = None
    model_name: str | None = None


@dataclass(frozen=True, slots=True)
class TranslationJobProcessResult:
    claim: ClaimResult
    context: TranslationJobContext | None
    status: str
    output: TranslationLayerOutput | None = None
    published_layer: PublishedTranslationLayer | None = None
    usage_data: dict[str, Any] | None = None
    prompt_version: str | None = None
    model_route: str | None = None
    model_profile: str | None = None
    model_provider: str | None = None
    model_name: str | None = None


class TranslationExecutionError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        failure_class: str,
        failure_code: str,
        rationale_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.failure_class = failure_class
        self.failure_code = failure_code
        self.rationale_code = rationale_code or failure_code


class TranslationExecutor(Protocol):
    async def translate(
        self,
        context: TranslationJobContext,
    ) -> TranslationExecutionResult: ...


class PydanticAITranslationExecutor:
    def __init__(self, *, settings: Settings | None = None) -> None:
        self._settings = settings

    async def translate(
        self,
        context: TranslationJobContext,
    ) -> TranslationExecutionResult:
        settings = self._settings or get_settings()
        model, model_config = build_model_for_route(
            settings,
            MODEL_ROUTE_READER_LAYER_TRANSLATION,
        )
        if model is None:
            raise TranslationExecutionError(
                "reader_layer_translation model route is not configured",
                retryable=False,
                failure_class="configuration",
                failure_code="model_route_unavailable",
            )

        assert_real_llm_allowed(
            "app.services.reader_orchestration.translation_worker.PydanticAITranslationExecutor",
            model_config=model_config,
        )

        agent = Agent(
            model=model,
            output_type=TranslationLayerGenerationOutput,
            instructions=load_agent_instructions(TRANSLATION_PROMPT_AGENT_NAME),
            name="reader_layer_translation_agent",
            retries={"tools": 1, "output": 2},
        )
        result = await agent.run(_build_translation_prompt(context))
        output = TranslationLayerGenerationOutput.model_validate(result.output)
        usage_data = extract_run_usage(result)

        return TranslationExecutionResult(
            output=output,
            usage_data=usage_data,
            prompt_version=get_prompt_version(),
            model_profile=(
                str(model_config.profile_name) if model_config is not None else None
            ),
            model_provider=(
                str(model_config.provider) if model_config is not None else None
            ),
            model_name=(
                str(model_config.model_name) if model_config is not None else None
            ),
        )


# ---------------------------------------------------------------------------#
# T1.1 short-article batch path: batch compute, unit publish.
# ---------------------------------------------------------------------------#


@dataclass(frozen=True, slots=True)
class TranslationBatchUnitContext:
    """Per-unit slice within a batch translation context."""

    unit_id: str
    order_index: int
    source_text: str
    text_hash: str
    anchor_segments: tuple[TranslationAnchorSegmentTarget, ...]


# Display-friendly translation group sizing for the batch path.
#
# A reading unit that fits within ``TRANSLATION_GROUP_SAFETY_MAX_CHARS`` is
# translated as a single group (one translation for the whole unit),
# matching the natural-paragraph display the translation strategy expects.
# Longer units are split at anchor-segment boundaries into bounded groups so
# no single LLM translation call exceeds the safety ceiling.
TRANSLATION_GROUP_TARGET_MAX_CHARS = 900
TRANSLATION_GROUP_SAFETY_MAX_CHARS = 1400


@dataclass(frozen=True, slots=True)
class DeterministicTranslationGroup:
    """A backend-predefined translation group for the batch path.

    The batch path no longer lets the LLM choose which anchor segments
    belong to a translation group. The backend deterministically builds
    display-friendly groups: by default one group per reading unit
    (covering all of its contiguous anchor segments); only when a unit is
    too long does it split at anchor-segment boundaries into bounded groups.
    ``group_id = {unit_id}_g{first_order}_{last_order}``,
    ``anchor_segment_ids`` = the contiguous anchor ids in this group,
    ``source_text_hash`` = hash of the span from the first anchor's
    ``unit_start_utf16`` to the last anchor's ``unit_end_utf16``. The
    prompt emits these groups to the LLM with their source text; the LLM
    only returns ``group_id`` + ``translated_text``. The hydrate step maps
    each returned ``group_id`` back to this predefined group, removing the
    previous LLM-selected anchor misalignment vector. Semantic matching
    between ``translated_text`` and this group's source text still depends
    on model quality.
    """

    group_id: str
    unit_id: str
    anchor_segment_ids: tuple[str, ...]
    source_text: str
    source_text_hash: str
    order_index: int


def build_deterministic_translation_groups(
    unit: TranslationBatchUnitContext,
) -> list[DeterministicTranslationGroup]:
    """Build deterministic translation groups for a batch unit.

    Display-friendly grouping contract:
        - By default, one group covering the entire unit (all contiguous
          anchor segments).
        - If the unit's span exceeds ``TRANSLATION_GROUP_SAFETY_MAX_CHARS``,
          split at anchor-segment boundaries into bounded groups.
        - Non-contiguous anchor segments (gaps in ``order_index``) are
          always split into separate groups (the publisher requires
          contiguity within a group).
        - ``group_id`` / ``anchor_segment_ids`` / ``source_text_hash`` are
          derived from the unit's anchor segments, so the hydrate step can
          re-derive the same mapping and reject any LLM output whose
          ``group_id`` set does not exactly match.

    The LLM is NOT allowed to choose, merge, split, reorder, add, or drop
    groups.
    """
    segments = list(unit.anchor_segments)
    if not segments:
        return []

    # Sort defensively by order_index (callers already preserve reading
    # order, but sorting makes the contract explicit).
    segments.sort(key=lambda s: s.order_index)

    # First pass: split into contiguous runs. The publisher requires
    # anchor_segment_ids within a group to be ordered and consecutive in
    # order_index, so a gap (e.g. order 15 then 17 with no 16) forces a
    # group boundary.
    contiguous_runs: list[list[TranslationAnchorSegmentTarget]] = []
    current_run: list[TranslationAnchorSegmentTarget] = [segments[0]]
    for previous, current in zip(segments, segments[1:], strict=False):
        if current.order_index == previous.order_index + 1:
            current_run.append(current)
        else:
            contiguous_runs.append(current_run)
            current_run = [current]
    contiguous_runs.append(current_run)

    # Second pass: for each contiguous run, split into bounded groups if
    # the run's span exceeds the safety ceiling.
    groups: list[DeterministicTranslationGroup] = []
    for run in contiguous_runs:
        groups.extend(_split_run_into_bounded_groups(unit, run))
    return groups


def _split_run_into_bounded_groups(
    unit: TranslationBatchUnitContext,
    segments: list[TranslationAnchorSegmentTarget],
) -> list[DeterministicTranslationGroup]:
    """Split a contiguous run of anchor segments into bounded groups.

    If the entire run fits within ``TRANSLATION_GROUP_SAFETY_MAX_CHARS``,
    emit a single group. Otherwise, greedily accumulate segments until
    adding the next would exceed the safety ceiling, then close the group.
    """
    first = segments[0]
    last = segments[-1]
    run_span_length = last.unit_end_utf16 - first.unit_start_utf16
    if run_span_length <= TRANSLATION_GROUP_SAFETY_MAX_CHARS or len(segments) == 1:
        return [_build_deterministic_group(unit, segments)]

    groups: list[DeterministicTranslationGroup] = []
    current: list[TranslationAnchorSegmentTarget] = [segments[0]]
    current_start = segments[0].unit_start_utf16
    for segment in segments[1:]:
        candidate_length = segment.unit_end_utf16 - current_start
        if candidate_length > TRANSLATION_GROUP_SAFETY_MAX_CHARS and len(current) >= 1:
            groups.append(_build_deterministic_group(unit, current))
            current = [segment]
            current_start = segment.unit_start_utf16
        else:
            current.append(segment)
    if current:
        groups.append(_build_deterministic_group(unit, current))
    return groups


def _build_deterministic_group(
    unit: TranslationBatchUnitContext,
    segments: list[TranslationAnchorSegmentTarget],
) -> DeterministicTranslationGroup:
    """Build a single deterministic translation group from a segment range."""
    first = segments[0]
    last = segments[-1]
    group_source_text = slice_by_utf16_offsets(
        unit.source_text,
        first.unit_start_utf16,
        last.unit_end_utf16,
    )
    if group_source_text is None or not group_source_text:
        raise TranslationExecutionError(
            f"failed to slice deterministic translation group source_text "
            f"for unit {unit.unit_id!r} "
            f"(span {first.unit_start_utf16}:{last.unit_end_utf16})",
            retryable=False,
            failure_class="validation",
            failure_code="translation_group_slice_failed",
        )
    return DeterministicTranslationGroup(
        group_id=(
            f"{unit.unit_id}_g{first.order_index}_{last.order_index}"
        ),
        unit_id=unit.unit_id,
        anchor_segment_ids=tuple(
            segment.anchor_segment_id for segment in segments
        ),
        source_text=group_source_text,
        source_text_hash=compute_text_range_hash(group_source_text),
        order_index=first.order_index,
    )


@dataclass(frozen=True, slots=True)
class TranslationBatchJobContext:
    """Batch translation job context: covers all units of a short article.

    The batch executor receives every unit's source text + anchor segments
    in a single LLM call. The worker then splits the output back into
    per-unit :class:`TranslationLayerOutput` objects for publish.
    """

    job_id: UUID
    run_id: UUID
    reading_record_id: UUID
    user_id: UUID
    base_id: UUID
    expected_generation: int
    operation_fingerprint: str
    source_language: str
    target_language: str
    target_unit_ids: tuple[str, ...]
    units: tuple[TranslationBatchUnitContext, ...]
    # T6 strategy fields (same contract as TranslationJobContext).
    reading_goal: str
    reading_variant: str
    strategy_version: str
    strategy_hash: str
    layer_policy_hash: str
    translation_prompt_lines: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TranslationBatchExecutionResult:
    output: TranslationBatchGenerationOutput
    usage_data: dict[str, Any] | None = None
    prompt_version: str | None = None
    model_route: str = MODEL_ROUTE_READER_LAYER_TRANSLATION
    model_profile: str | None = None
    model_provider: str | None = None
    model_name: str | None = None


@dataclass(frozen=True, slots=True)
class TranslationBatchJobProcessResult:
    claim: ClaimResult
    context: TranslationBatchJobContext | None
    status: str
    published_batch: PublishedTranslationBatch | None = None
    usage_data: dict[str, Any] | None = None
    prompt_version: str | None = None
    model_route: str | None = None
    model_profile: str | None = None
    model_provider: str | None = None
    model_name: str | None = None


class TranslationBatchExecutor(Protocol):
    async def translate_batch(
        self,
        context: TranslationBatchJobContext,
    ) -> TranslationBatchExecutionResult: ...


class PydanticAITranslationBatchExecutor:
    """Batch translation executor: 1 LLM call covering all units.

    Uses :class:`TranslationBatchGenerationOutput` as the structured output
    type so the model returns one ``TranslationBatchUnitOutput`` per unit.
    """

    def __init__(self, *, settings: Settings | None = None) -> None:
        self._settings = settings

    async def translate_batch(
        self,
        context: TranslationBatchJobContext,
    ) -> TranslationBatchExecutionResult:
        settings = self._settings or get_settings()
        model, model_config = build_model_for_route(
            settings,
            MODEL_ROUTE_READER_LAYER_TRANSLATION,
        )
        if model is None:
            raise TranslationExecutionError(
                "reader_layer_translation model route is not configured",
                retryable=False,
                failure_class="configuration",
                failure_code="model_route_unavailable",
            )

        assert_real_llm_allowed(
            "app.services.reader_orchestration.translation_worker.PydanticAITranslationBatchExecutor",
            model_config=model_config,
        )

        agent = Agent(
            model=model,
            output_type=TranslationBatchGenerationOutput,
            instructions=_TRANSLATION_BATCH_AGENT_INSTRUCTIONS,
            name="reader_layer_translation_batch_agent",
            retries={"tools": 1, "output": 2},
        )
        result = await agent.run(_build_translation_batch_prompt(context))
        output = TranslationBatchGenerationOutput.model_validate(result.output)
        usage_data = extract_run_usage(result)

        return TranslationBatchExecutionResult(
            output=output,
            usage_data=usage_data,
            prompt_version=get_prompt_version(),
            model_profile=(
                str(model_config.profile_name) if model_config is not None else None
            ),
            model_provider=(
                str(model_config.provider) if model_config is not None else None
            ),
            model_name=(
                str(model_config.model_name) if model_config is not None else None
            ),
        )


def hydrate_translation_batch_output(
    *,
    context: TranslationBatchJobContext,
    generation: TranslationBatchGenerationOutput,
) -> list[tuple[str, TranslationLayerOutput]]:
    """Split a batch generation output into per-unit ``TranslationLayerOutput``.

    Deterministic-grouping contract: the backend pre-defines display-friendly
    translation groups (by default one per reading unit; split at anchor
    boundaries only when the unit is too long — see
    :func:`build_deterministic_translation_groups`). The LLM only returns
    ``group_id`` + ``translated_text`` per group; it MUST NOT choose
    ``anchor_segment_ids``. This function maps each returned
    ``group_id`` back to the predefined group's ``anchor_segment_ids``
    and ``source_text_hash``, then assembles a per-unit
    :class:`TranslationLayerOutput` for publish.

    Fail-closed contract:
        - ``unit_id`` not in batch context → ``translation_batch_unknown_unit``
        - ``group_id`` set does not exactly match the predefined groups
          (missing / extra / duplicate) → dedicated failure codes
        - blank ``translated_text`` → ``translation_batch_empty_translated_text``
        - no attempt is made to auto-align or repair a misaligned output

    The publisher's :func:`_validate_translation_unit_output_core` still
    runs as a second layer of defense against the live DB anchor segments.
    """
    units_by_id = {unit.unit_id: unit for unit in context.units}
    # Cache the deterministic group mapping per unit so we only build it
    # once even if the LLM returns duplicate unit_ids (the duplicate is
    # rejected below before any lookup, but the cache is harmless).
    deterministic_by_unit: dict[str, dict[str, DeterministicTranslationGroup]] = {}
    for unit in context.units:
        deterministic_by_unit[unit.unit_id] = {
            group.group_id: group
            for group in build_deterministic_translation_groups(unit)
        }

    outputs: list[tuple[str, TranslationLayerOutput]] = []
    seen_unit_ids: set[str] = set()
    for batch_unit in generation.units:
        unit_id = batch_unit.unit_id
        if unit_id in seen_unit_ids:
            raise TranslationExecutionError(
                f"translation batch output has duplicate unit_id {unit_id!r}",
                retryable=False,
                failure_class="validation",
                failure_code="translation_batch_duplicate_unit_id",
            )
        seen_unit_ids.add(unit_id)

        if unit_id not in units_by_id:
            raise TranslationExecutionError(
                f"translation batch output references unknown unit_id "
                f"{unit_id!r}",
                retryable=False,
                failure_class="validation",
                failure_code="translation_batch_unknown_unit",
            )

        deterministic_by_id = deterministic_by_unit[unit_id]
        expected_group_ids = set(deterministic_by_id.keys())

        # Walk the LLM-returned groups and validate against the predefined
        # mapping. Track seen group_ids to catch duplicates; track which
        # predefined groups have been covered so we can detect missing
        # groups after the loop.
        seen_group_ids: set[str] = set()
        resolved_pairs: list[tuple[DeterministicTranslationGroup, str]] = []
        extra_group_ids: list[str] = []
        for llm_group in batch_unit.groups:
            group_id = llm_group.group_id
            predefined = deterministic_by_id.get(group_id)
            if predefined is None:
                extra_group_ids.append(group_id)
                continue
            if group_id in seen_group_ids:
                raise TranslationExecutionError(
                    f"translation batch unit {unit_id!r} has duplicate "
                    f"group_id {group_id!r}",
                    retryable=False,
                    failure_class="validation",
                    failure_code="translation_batch_duplicate_group_id",
                )
            seen_group_ids.add(group_id)
            if not llm_group.translated_text.strip():
                raise TranslationExecutionError(
                    f"translation batch group {group_id!r} in unit "
                    f"{unit_id!r} has blank translated_text",
                    retryable=False,
                    failure_class="validation",
                    failure_code="translation_batch_empty_translated_text",
                )
            resolved_pairs.append((predefined, llm_group.translated_text))

        if extra_group_ids:
            raise TranslationExecutionError(
                f"translation batch unit {unit_id!r} returned unknown "
                f"group_ids {extra_group_ids!r}; expected exactly "
                f"{sorted(expected_group_ids)!r}",
                retryable=False,
                failure_class="validation",
                failure_code="translation_batch_extra_group",
            )

        missing_group_ids = expected_group_ids - seen_group_ids
        if missing_group_ids:
            raise TranslationExecutionError(
                f"translation batch unit {unit_id!r} is missing groups "
                f"{sorted(missing_group_ids)!r}; expected exactly "
                f"{sorted(expected_group_ids)!r}",
                retryable=False,
                failure_class="validation",
                failure_code="translation_batch_missing_group",
            )

        # Re-assemble in deterministic reading order so the published layer
        # is independent of the order the LLM returned the groups.
        resolved_pairs.sort(key=lambda pair: pair[0].order_index)
        hydrated_groups: list[TranslationGroup] = []
        for predefined, translated_text in resolved_pairs:
            hydrated_groups.append(
                TranslationGroup(
                    group_id=predefined.group_id,
                    anchor_segment_ids=list(predefined.anchor_segment_ids),
                    source_text_hash=predefined.source_text_hash,
                    translated_text=translated_text,
                )
            )
        outputs.append(
            (unit_id, TranslationLayerOutput(groups=hydrated_groups))
        )
    return outputs


def _build_translation_batch_prompt(context: TranslationBatchJobContext) -> str:
    strategy_section = _format_translation_strategy_section_from_batch(context)
    grouping_section = _format_batch_grouping_contract_section()
    groups_section = _format_batch_translation_groups_section(context)
    return (
        "Translate the following short article batch.\n"
        "The translation groups are PRE-DEFINED by the backend. You MUST NOT "
        "choose, merge, split, reorder, add, or drop groups. For each "
        "<translation_group> below, return its group_id and the translated_text "
        "that translates ONLY that group's <source_text> into the target "
        "language. Do not translate across groups and do not shift a "
        "translation to a different group_id. The backend binds each "
        "group_id to its source anchor deterministically; wrong "
        "translated_text under a group_id is a translation-quality failure, "
        "not an anchor remapping.\n"
        f"source_language: {context.source_language}\n"
        f"target_language: {context.target_language}\n"
        f"{strategy_section}"
        f"{grouping_section}"
        "Return only the structured TranslationBatchGenerationOutput.\n"
        "Each unit must appear exactly once in units[] with its unit_id and "
        "one group entry per pre-defined translation_group, each carrying "
        "only group_id and translated_text.\n"
        "Do not output anchor_segment_ids, source_text, source_text_hash, "
        "segment_sources, profile, source_language, target_language, "
        "diagnostics, confidence, reason, notes, coverage_json, quality_json, "
        "or any UI, Plate, Slate, or DOM fields.\n"
        f"{groups_section}"
    )


def _format_translation_strategy_section_from_batch(
    context: TranslationBatchJobContext,
) -> str:
    if not context.translation_prompt_lines:
        return ""
    rendered = "\n".join(context.translation_prompt_lines)
    return f"<strategy>\n{rendered}\n</strategy>\n"


def _format_batch_grouping_contract_section() -> str:
    """State the deterministic-grouping contract for the batch LLM call.

    Unlike the per-unit path (where the LLM may choose semantic reading
    groups), the batch path uses backend-predefined translation groups.
    This section makes the contract explicit so the model cannot treat the
    group list as a registry it is free to re-shape.
    """
    return (
        "<grouping_contract>\n"
        "Translation groups are fixed by the backend, listed in "
        "<translation_groups> below in reading order. Each group covers "
        "one or more contiguous anchor segments within a reading unit and "
        "already declares its group_id, unit_id, anchor_segment_ids, "
        "source_text_hash, and source_text.\n"
        "You MUST return exactly one group entry per listed "
        "<translation_group>, echoing its group_id and providing the "
        "translated_text for that group's source_text only.\n"
        "You MUST NOT add new groups, drop a group, merge groups, split a "
        "group, reorder groups, or output anchor_segment_ids. You MUST NOT "
        "shift a translation onto a different group_id than the one whose "
        "source_text it translates.\n"
        "</grouping_contract>\n"
    )


def _format_batch_translation_groups_section(
    context: TranslationBatchJobContext,
) -> str:
    """Render the predefined translation groups for the batch LLM call.

    For each unit, emit one ``<translation_group>`` per deterministic
    group with its group_id, unit_id, anchor_segment_ids (one or more
    contiguous ids), source_text_hash, and the group's full source_text
    span. The LLM only needs to translate each group's source_text and
    echo the group_id; it never chooses anchor coverage.
    """
    parts: list[str] = ["<translation_groups>"]
    for unit in context.units:
        parts.append(f'<unit unit_id="{unit.unit_id}">')
        for group in build_deterministic_translation_groups(unit):
            anchor_ids = ",".join(group.anchor_segment_ids)
            parts.append(
                f'<translation_group group_id="{group.group_id}" '
                f'unit_id="{group.unit_id}" '
                f'anchor_segment_ids="{anchor_ids}" '
                f'source_text_hash="{group.source_text_hash}">'
            )
            parts.append("<source_text>")
            parts.append(group.source_text)
            parts.append("</source_text>")
            parts.append("</translation_group>")
        parts.append("</unit>")
    parts.append("</translation_groups>")
    return "\n".join(parts) + "\n"


def _build_batch_quality_json(
    execution: TranslationBatchExecutionResult,
    *,
    unit_count: int,
) -> dict[str, Any]:
    quality_json: dict[str, Any] = {
        "unit_count": unit_count,
        "batch": True,
    }
    if execution.prompt_version is not None:
        quality_json["prompt_version"] = execution.prompt_version
    if execution.model_route:
        quality_json["model_route"] = execution.model_route
    if execution.model_profile is not None:
        quality_json["model_profile"] = execution.model_profile
    if execution.model_provider is not None:
        quality_json["model_provider"] = execution.model_provider
    if execution.model_name is not None:
        quality_json["model_name"] = execution.model_name
    return quality_json


class TranslationWorkerService:
    def __init__(
        self,
        *,
        pool: asyncpg.Pool | None = None,
        job_runtime: ReaderJobRuntime | None = None,
        layer_publisher: TranslationLayerPublisher | None = None,
        translator: TranslationExecutor | None = None,
        batch_translator: TranslationBatchExecutor | None = None,
    ) -> None:
        self._pool = pool
        self._job_runtime = job_runtime or ReaderJobRuntime(pool=pool)
        self._layer_publisher = layer_publisher or TranslationLayerPublisher(pool=pool)
        self._translator = translator or PydanticAITranslationExecutor()
        self._batch_translator = batch_translator or PydanticAITranslationBatchExecutor()

    def get_pool(self) -> asyncpg.Pool:
        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("Database pool not initialized")
        return pool

    async def claim_translation_job(
        self,
        *,
        lease_owner: str,
        lease_duration: timedelta,
    ) -> ClaimResult | None:
        claim = await self._job_runtime.claim_next_job(
            lease_owner=lease_owner,
            lease_duration=lease_duration,
            job_type=TRANSLATION_JOB_TYPE,
            target_type=TRANSLATION_TARGET_SCOPE,
            operation_fingerprint=TRANSLATION_OPERATION_FINGERPRINT,
        )
        if claim is None:
            return None
        if claim.job_type != TRANSLATION_JOB_TYPE or claim.target_type != TRANSLATION_TARGET_SCOPE:
            raise RuntimeError(
                "translation worker claimed unsupported job "
                f"{claim.job_type}/{claim.target_type}"
            )
        await self._mark_run_running(claim.run_id)
        return claim

    async def claim_translation_job_for_record(
        self,
        *,
        record_id: UUID,
        base_id: UUID,
        expected_generation: int,
        lease_owner: str,
        lease_duration: timedelta,
    ) -> ClaimResult | None:
        claim = await self._job_runtime.claim_next_job(
            lease_owner=lease_owner,
            lease_duration=lease_duration,
            job_type=TRANSLATION_JOB_TYPE,
            target_type=TRANSLATION_TARGET_SCOPE,
            operation_fingerprint=TRANSLATION_OPERATION_FINGERPRINT,
            reading_record_id=record_id,
            base_id=base_id,
            expected_generation=expected_generation,
        )
        if claim is None:
            return None
        if claim.job_type != TRANSLATION_JOB_TYPE or claim.target_type != TRANSLATION_TARGET_SCOPE:
            raise RuntimeError(
                "translation worker claimed unsupported job "
                f"{claim.job_type}/{claim.target_type}"
            )
        await self._mark_run_running(claim.run_id)
        return claim

    async def heartbeat_translation_job(
        self,
        *,
        job_id: UUID,
        lease_token: UUID,
        lease_duration: timedelta,
    ) -> datetime:
        return await self._job_runtime.heartbeat(
            job_id=job_id,
            lease_token=lease_token,
            lease_duration=lease_duration,
        )

    async def process_next_translation_job(
        self,
        *,
        lease_owner: str,
        lease_duration: timedelta,
        retry_delay: timedelta = DEFAULT_TRANSLATION_RETRY_DELAY,
    ) -> TranslationJobProcessResult | None:
        claim = await self.claim_translation_job(
            lease_owner=lease_owner,
            lease_duration=lease_duration,
        )
        if claim is None:
            return None
        return await self.process_claimed_translation_job(
            claim=claim,
            retry_delay=retry_delay,
        )

    async def process_next_translation_job_for_record(
        self,
        *,
        record_id: UUID,
        base_id: UUID,
        expected_generation: int,
        lease_owner: str,
        lease_duration: timedelta,
        retry_delay: timedelta = DEFAULT_TRANSLATION_RETRY_DELAY,
    ) -> TranslationJobProcessResult | None:
        claim = await self.claim_translation_job_for_record(
            record_id=record_id,
            base_id=base_id,
            expected_generation=expected_generation,
            lease_owner=lease_owner,
            lease_duration=lease_duration,
        )
        if claim is None:
            return None
        return await self.process_claimed_translation_job(
            claim=claim,
            retry_delay=retry_delay,
        )

    async def process_claimed_translation_job(
        self,
        *,
        claim: ClaimResult,
        retry_delay: timedelta = DEFAULT_TRANSLATION_RETRY_DELAY,
    ) -> TranslationJobProcessResult:
        context: TranslationJobContext | None = None

        try:
            context = await self._load_job_context(claim.job_id)
            execution = await self._translator.translate(context)
            output = hydrate_translation_layer_output(
                context=context,
                generation=execution.output,
            )
            published_layer = await self._layer_publisher.publish_unit_translation(
                job_id=claim.job_id,
                lease_token=claim.lease_token,
                output=output,
                quality_json=_build_quality_json(output, execution),
            )
            event_id = await self._record_usage_event(
                context=context,
                execution=execution,
                published_layer=published_layer,
                status=STATUS_SUCCEEDED,
            )
            await end_worker_span_success(
                ai_usage_event_id=event_id,
                usage_data=execution.usage_data,
                model_route=execution.model_route,
                model_name=execution.model_name,
                model_provider=execution.model_provider,
                capability_code=CAPABILITY_READER_TRANSLATION,
            )
            return TranslationJobProcessResult(
                claim=claim,
                context=context,
                status="succeeded",
                output=output,
                published_layer=published_layer,
                usage_data=execution.usage_data,
                prompt_version=execution.prompt_version,
                model_route=execution.model_route,
                model_profile=execution.model_profile,
                model_provider=execution.model_provider,
                model_name=execution.model_name,
            )
        except FenceViolationError:
            await end_worker_span_fence_violation()
            await self._job_runtime.transition(
                job_id=claim.job_id,
                target_status="superseded",
                lease_token=claim.lease_token,
                rationale_code="publish_fence_failed",
            )
            await self._mark_run_status(
                claim.run_id,
                status="superseded",
                failure_class="publish_guard",
                failure_code="publish_fence_failed",
                finished_at=datetime.now(UTC),
            )
            raise
        except TranslationExecutionError as exc:
            if exc.retryable:
                available_at = datetime.now(UTC) + retry_delay
                await self._job_runtime.transition(
                    job_id=claim.job_id,
                    target_status="retry_later",
                    lease_token=claim.lease_token,
                    available_at=available_at,
                    rationale_code=exc.rationale_code,
                )
                await self._mark_run_status(
                    claim.run_id,
                    status="failed_retryable",
                    failure_class=exc.failure_class,
                    failure_code=exc.failure_code,
                    finished_at=None,
                )
                await self._record_failed_usage_event(
                    context=context,
                    error_code=exc.failure_code,
                    error_message=str(exc),
                )
                await end_worker_span_execution_error(
                    failure_class=exc.failure_class,
                    failure_code=exc.failure_code,
                )
                return TranslationJobProcessResult(
                    claim=claim,
                    context=context,
                    status="retry_later",
                )

            await self._job_runtime.transition(
                job_id=claim.job_id,
                target_status="failed_terminal",
                lease_token=claim.lease_token,
                failure_class=exc.failure_class,
                failure_code=exc.failure_code,
                failure_message=str(exc),
                rationale_code=exc.rationale_code,
            )
            await self._mark_run_status(
                claim.run_id,
                status="failed_terminal",
                failure_class=exc.failure_class,
                failure_code=exc.failure_code,
                finished_at=datetime.now(UTC),
            )
            await self._record_failed_usage_event(
                context=context,
                error_code=exc.failure_code,
                error_message=str(exc),
            )
            await end_worker_span_execution_error(
                failure_class=exc.failure_class,
                failure_code=exc.failure_code,
            )
            return TranslationJobProcessResult(
                claim=claim,
                context=context,
                status="failed_terminal",
            )
        except Exception as exc:
            await self._job_runtime.transition(
                job_id=claim.job_id,
                target_status="failed_terminal",
                lease_token=claim.lease_token,
                failure_class="translation_execution",
                failure_code=type(exc).__name__,
                failure_message=str(exc),
                rationale_code="translation_execution_failed",
            )
            await self._mark_run_status(
                claim.run_id,
                status="failed_terminal",
                failure_class="translation_execution",
                failure_code=type(exc).__name__,
                finished_at=datetime.now(UTC),
            )
            await self._record_failed_usage_event(
                context=context,
                error_code=type(exc).__name__,
                error_message=str(exc),
            )
            await end_worker_span_generic_exception(layer="translation", exc=exc)
            return TranslationJobProcessResult(
                claim=claim,
                context=context,
                status="failed_terminal",
            )

    # ------------------------------------------------------------------#
    # T1.1 short-article batch path: claim / process / context loading.
    # ------------------------------------------------------------------#

    async def claim_translation_batch_job_for_record(
        self,
        *,
        record_id: UUID,
        base_id: UUID,
        expected_generation: int,
        lease_owner: str,
        lease_duration: timedelta,
    ) -> ClaimResult | None:
        """Claim a pending ``translate_article`` batch job for the record."""
        claim = await self._job_runtime.claim_next_job(
            lease_owner=lease_owner,
            lease_duration=lease_duration,
            job_type=TRANSLATION_BATCH_JOB_TYPE,
            target_type=TRANSLATION_BATCH_TARGET_SCOPE,
            operation_fingerprint=TRANSLATION_BATCH_OPERATION_FINGERPRINT,
            reading_record_id=record_id,
            base_id=base_id,
            expected_generation=expected_generation,
        )
        if claim is None:
            return None
        if (
            claim.job_type != TRANSLATION_BATCH_JOB_TYPE
            or claim.target_type != TRANSLATION_BATCH_TARGET_SCOPE
        ):
            raise RuntimeError(
                "translation batch worker claimed unsupported job "
                f"{claim.job_type}/{claim.target_type}"
            )
        await self._mark_run_running(claim.run_id)
        return claim

    async def process_next_translation_batch_job_for_record(
        self,
        *,
        record_id: UUID,
        base_id: UUID,
        expected_generation: int,
        lease_owner: str,
        lease_duration: timedelta,
        retry_delay: timedelta = DEFAULT_TRANSLATION_RETRY_DELAY,
    ) -> TranslationBatchJobProcessResult | None:
        """Claim and process the next translation batch job for the record."""
        claim = await self.claim_translation_batch_job_for_record(
            record_id=record_id,
            base_id=base_id,
            expected_generation=expected_generation,
            lease_owner=lease_owner,
            lease_duration=lease_duration,
        )
        if claim is None:
            return None
        return await self.process_claimed_translation_batch_job(
            claim=claim,
            retry_delay=retry_delay,
        )

    async def process_claimed_translation_batch_job(
        self,
        *,
        claim: ClaimResult,
        retry_delay: timedelta = DEFAULT_TRANSLATION_RETRY_DELAY,
    ) -> TranslationBatchJobProcessResult:
        """Run the batch LLM call and publish N per-unit translation layers.

        Exception handling mirrors :meth:`process_claimed_translation_job`:
        ``FenceViolationError`` → ``superseded``;
        ``TranslationExecutionError`` (retryable) → ``retry_later``;
        ``TranslationExecutionError`` (non-retryable) → ``failed_terminal``;
        any other ``Exception`` → ``failed_terminal``.
        """
        context: TranslationBatchJobContext | None = None

        try:
            context = await self._load_batch_job_context(claim.job_id)
            execution = await self._batch_translator.translate_batch(context)
            outputs = hydrate_translation_batch_output(
                context=context,
                generation=execution.output,
            )
            published_batch = await self._layer_publisher.publish_article_translation_batch(
                job_id=claim.job_id,
                lease_token=claim.lease_token,
                outputs=outputs,
                quality_json=_build_batch_quality_json(
                    execution,
                    unit_count=len(context.units),
                ),
            )
            event_id = await self._record_batch_usage_event(
                context=context,
                execution=execution,
                published_batch=published_batch,
                status=STATUS_SUCCEEDED,
            )
            await end_worker_span_success(
                ai_usage_event_id=event_id,
                usage_data=execution.usage_data,
                model_route=execution.model_route,
                model_name=execution.model_name,
                model_provider=execution.model_provider,
                capability_code=CAPABILITY_READER_TRANSLATION,
            )
            return TranslationBatchJobProcessResult(
                claim=claim,
                context=context,
                status="succeeded",
                published_batch=published_batch,
                usage_data=execution.usage_data,
                prompt_version=execution.prompt_version,
                model_route=execution.model_route,
                model_profile=execution.model_profile,
                model_provider=execution.model_provider,
                model_name=execution.model_name,
            )
        except FenceViolationError:
            await self._job_runtime.transition(
                job_id=claim.job_id,
                target_status="superseded",
                lease_token=claim.lease_token,
                rationale_code="publish_fence_failed",
            )
            await self._mark_run_status(
                claim.run_id,
                status="superseded",
                failure_class="publish_guard",
                failure_code="publish_fence_failed",
                finished_at=datetime.now(UTC),
            )
            raise
        except TranslationExecutionError as exc:
            if exc.retryable:
                available_at = datetime.now(UTC) + retry_delay
                await self._job_runtime.transition(
                    job_id=claim.job_id,
                    target_status="retry_later",
                    lease_token=claim.lease_token,
                    available_at=available_at,
                    rationale_code=exc.rationale_code,
                )
                await self._mark_run_status(
                    claim.run_id,
                    status="failed_retryable",
                    failure_class=exc.failure_class,
                    failure_code=exc.failure_code,
                    finished_at=None,
                )
                await self._record_batch_failed_usage_event(
                    context=context,
                    error_code=exc.failure_code,
                    error_message=str(exc),
                )
                await end_worker_span_execution_error(
                    failure_class=exc.failure_class,
                    failure_code=exc.failure_code,
                )
                return TranslationBatchJobProcessResult(
                    claim=claim,
                    context=context,
                    status="retry_later",
                )

            await self._job_runtime.transition(
                job_id=claim.job_id,
                target_status="failed_terminal",
                lease_token=claim.lease_token,
                failure_class=exc.failure_class,
                failure_code=exc.failure_code,
                failure_message=str(exc),
                rationale_code=exc.rationale_code,
            )
            await self._mark_run_status(
                claim.run_id,
                status="failed_terminal",
                failure_class=exc.failure_class,
                failure_code=exc.failure_code,
                finished_at=datetime.now(UTC),
            )
            await self._record_batch_failed_usage_event(
                context=context,
                error_code=exc.failure_code,
                error_message=str(exc),
            )
            await end_worker_span_execution_error(
                failure_class=exc.failure_class,
                failure_code=exc.failure_code,
            )
            return TranslationBatchJobProcessResult(
                claim=claim,
                context=context,
                status="failed_terminal",
            )
        except Exception as exc:
            await self._job_runtime.transition(
                job_id=claim.job_id,
                target_status="failed_terminal",
                lease_token=claim.lease_token,
                failure_class="translation_batch_execution",
                failure_code=type(exc).__name__,
                failure_message=str(exc),
                rationale_code="translation_batch_execution_failed",
            )
            await self._mark_run_status(
                claim.run_id,
                status="failed_terminal",
                failure_class="translation_batch_execution",
                failure_code=type(exc).__name__,
                finished_at=datetime.now(UTC),
            )
            await self._record_batch_failed_usage_event(
                context=context,
                error_code=type(exc).__name__,
                error_message=str(exc),
            )
            await end_worker_span_generic_exception(layer="translation", exc=exc)
            return TranslationBatchJobProcessResult(
                claim=claim,
                context=context,
                status="failed_terminal",
            )

    async def _load_batch_job_context(
        self,
        job_id: UUID,
    ) -> TranslationBatchJobContext:
        """Load the batch job context covering all units in ``target_unit_ids``.

        Mirrors :meth:`_load_job_context` but loads every unit listed in the
        job ``input_json.target_unit_ids`` and validates each unit's text hash
        + anchor segment hashes (same fail-closed contract as the per-unit
        path).
        """
        async with self.get_pool().acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT job.id,
                       job.run_id,
                       job.reading_record_id,
                       job.user_id,
                       job.base_id,
                       job.target_key,
                       job.expected_generation,
                       job.operation_fingerprint,
                       job.input_json,
                       COALESCE(job.input_json->>'target_language', $2) AS target_language,
                       base.language AS source_language,
                       base.text AS base_text
                FROM reader_jobs job
                JOIN reading_bases base
                  ON base.id = job.base_id
                 AND base.reading_record_id = job.reading_record_id
                WHERE job.id = $1
                """,
                job_id,
                DEFAULT_TRANSLATION_TARGET_LANGUAGE,
            )
            if row is None:
                raise LookupError(f"reader job {job_id} not found")

            input_json = row["input_json"]
            target_unit_ids: list[str] = list(input_json.get("target_unit_ids") or [])
            if not target_unit_ids:
                raise TranslationExecutionError(
                    f"translation batch job {job_id} has no target_unit_ids",
                    retryable=False,
                    failure_class="validation",
                    failure_code="translation_batch_empty_target_units",
                )

            base_text = str(row["base_text"])
            unit_rows = await conn.fetch(
                """
                SELECT unit_id, order_index, base_start_utf16, base_end_utf16, text_hash
                FROM reading_units
                WHERE reading_record_id = $1
                  AND base_id = $2
                  AND unit_id = ANY($3::text[])
                ORDER BY order_index ASC
                """,
                row["reading_record_id"],
                row["base_id"],
                target_unit_ids,
            )
            if len(unit_rows) != len(target_unit_ids):
                missing = set(target_unit_ids) - {
                    str(r["unit_id"]) for r in unit_rows
                }
                raise TranslationExecutionError(
                    f"translation batch job {job_id} references missing units "
                    f"{sorted(missing)!r}",
                    retryable=False,
                    failure_class="validation",
                    failure_code="translation_batch_missing_unit",
                )

            units: list[TranslationBatchUnitContext] = []
            for unit_row in unit_rows:
                unit_id = str(unit_row["unit_id"])
                source_text = slice_by_utf16_offsets(
                    base_text,
                    int(unit_row["base_start_utf16"]),
                    int(unit_row["base_end_utf16"]),
                )
                if source_text is None or not source_text:
                    raise TranslationExecutionError(
                        f"translation batch unit {unit_id} could not be sliced from base text",
                        retryable=False,
                        failure_class="validation",
                        failure_code="unit_slice_failed",
                    )
                actual_hash = compute_text_range_hash(source_text)
                expected_hash = str(unit_row["text_hash"])
                if actual_hash != expected_hash:
                    raise TranslationExecutionError(
                        f"translation batch unit {unit_id} hash mismatch: "
                        f"{actual_hash} != {expected_hash}",
                        retryable=False,
                        failure_class="validation",
                        failure_code="unit_hash_mismatch",
                    )

                segment_rows = await conn.fetch(
                    """
                    SELECT anchor_segment_id,
                           sentence_id,
                           order_index,
                           segment_type,
                           boundary_quality,
                           unit_start_utf16,
                           unit_end_utf16,
                           text_hash
                    FROM anchor_segments
                    WHERE reading_record_id = $1
                      AND base_id = $2
                      AND unit_id = $3
                    ORDER BY order_index ASC
                    """,
                    row["reading_record_id"],
                    row["base_id"],
                    unit_id,
                )
                anchor_segments: list[TranslationAnchorSegmentTarget] = []
                for segment_row in segment_rows:
                    segment_text = slice_by_utf16_offsets(
                        source_text,
                        int(segment_row["unit_start_utf16"]),
                        int(segment_row["unit_end_utf16"]),
                    )
                    if segment_text is None or not segment_text:
                        raise TranslationExecutionError(
                            f"translation batch anchor segment "
                            f"{segment_row['anchor_segment_id']} could not be sliced",
                            retryable=False,
                            failure_class="validation",
                            failure_code="anchor_segment_slice_failed",
                        )
                    segment_hash = str(segment_row["text_hash"])
                    actual_segment_hash = compute_text_range_hash(segment_text)
                    if actual_segment_hash != segment_hash:
                        raise TranslationExecutionError(
                            f"translation batch anchor segment "
                            f"{segment_row['anchor_segment_id']} hash mismatch",
                            retryable=False,
                            failure_class="validation",
                            failure_code="anchor_segment_hash_mismatch",
                        )
                    anchor_segments.append(
                        TranslationAnchorSegmentTarget(
                            anchor_segment_id=str(segment_row["anchor_segment_id"]),
                            sentence_id=(
                                str(segment_row["sentence_id"])
                                if segment_row["sentence_id"] is not None
                                else None
                            ),
                            order_index=int(segment_row["order_index"]),
                            segment_type=str(segment_row["segment_type"]),
                            boundary_quality=str(
                                segment_row["boundary_quality"] or "normal"
                            ),
                            unit_start_utf16=int(segment_row["unit_start_utf16"]),
                            unit_end_utf16=int(segment_row["unit_end_utf16"]),
                            text_hash=segment_hash,
                            source_text=segment_text,
                        )
                    )
                if not anchor_segments:
                    raise TranslationExecutionError(
                        f"translation batch unit {unit_id} has no anchor segments",
                        retryable=False,
                        failure_class="validation",
                        failure_code="anchor_segments_missing",
                    )
                units.append(
                    TranslationBatchUnitContext(
                        unit_id=unit_id,
                        order_index=int(unit_row["order_index"]),
                        source_text=source_text,
                        text_hash=expected_hash,
                        anchor_segments=tuple(anchor_segments),
                    )
                )

            strategy_metadata = _validate_translation_strategy_metadata(input_json)

            return TranslationBatchJobContext(
                job_id=row["id"],
                run_id=row["run_id"],
                reading_record_id=row["reading_record_id"],
                user_id=row["user_id"],
                base_id=row["base_id"],
                expected_generation=int(row["expected_generation"]),
                operation_fingerprint=str(row["operation_fingerprint"]),
                source_language=str(row["source_language"] or "en"),
                target_language=str(
                    row["target_language"] or DEFAULT_TRANSLATION_TARGET_LANGUAGE
                ),
                target_unit_ids=tuple(target_unit_ids),
                units=tuple(units),
                reading_goal=strategy_metadata.reading_goal,
                reading_variant=strategy_metadata.reading_variant,
                strategy_version=strategy_metadata.strategy_version,
                strategy_hash=strategy_metadata.strategy_hash,
                layer_policy_hash=strategy_metadata.layer_policy_hash,
                translation_prompt_lines=strategy_metadata.translation_prompt_lines,
            )

    async def _record_batch_usage_event(
        self,
        *,
        context: TranslationBatchJobContext,
        execution: TranslationBatchExecutionResult,
        published_batch: PublishedTranslationBatch,
        status: str,
    ) -> UUID | None:
        return await record_ai_usage_event(
            AIUsageEventCreate(
                usage_scope=USAGE_SCOPE_SYSTEM_INTERNAL,
                capability_code=CAPABILITY_READER_TRANSLATION,
                billing_mode=BILLING_MODE_INTERNAL_ONLY,
                status=status,
                user_id=context.user_id,
                reading_record_id=context.reading_record_id,
                reader_run_id=context.run_id,
                reader_job_id=context.job_id,
                enhancement_layer_id=published_batch.layers[0].layer_id
                if published_batch.layers
                else None,
                workflow_name="reader_orchestration",
                workflow_version="t1-1-translation-batch-worker",
                prompt_version=execution.prompt_version,
                model_route=execution.model_route,
                model_profile_id=execution.model_profile,
                model_profile=execution.model_profile,
                model_provider=execution.model_provider,
                model_name=execution.model_name,
                planner_kind="llm_worker",
                usage_data=execution.usage_data,
                operation_fingerprint=context.operation_fingerprint,
                metadata_json={
                    "base_id": str(context.base_id),
                    "target_unit_ids": list(context.target_unit_ids),
                    "unit_count": len(context.units),
                    "target_language": context.target_language,
                    "source_language": context.source_language,
                    "batch": True,
                },
            )
        )

    async def _record_batch_failed_usage_event(
        self,
        *,
        context: TranslationBatchJobContext | None,
        error_code: str,
        error_message: str,
    ) -> UUID | None:
        if context is None:
            return None
        return await record_ai_usage_event(
            AIUsageEventCreate(
                usage_scope=USAGE_SCOPE_SYSTEM_INTERNAL,
                capability_code=CAPABILITY_READER_TRANSLATION,
                billing_mode=BILLING_MODE_INTERNAL_ONLY,
                status=STATUS_FAILED,
                user_id=context.user_id,
                reading_record_id=context.reading_record_id,
                reader_run_id=context.run_id,
                reader_job_id=context.job_id,
                workflow_name="reader_orchestration",
                workflow_version="t1-1-translation-batch-worker",
                model_route=MODEL_ROUTE_READER_LAYER_TRANSLATION,
                planner_kind="llm_worker",
                operation_fingerprint=context.operation_fingerprint,
                error_code=error_code,
                error_message=error_message,
                metadata_json={
                    "base_id": str(context.base_id),
                    "target_unit_ids": list(context.target_unit_ids),
                    "unit_count": len(context.units),
                    "target_language": context.target_language,
                    "source_language": context.source_language,
                    "batch": True,
                },
            )
        )

    async def _load_job_context(self, job_id: UUID) -> TranslationJobContext:
        async with self.get_pool().acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT job.id,
                       job.run_id,
                       job.reading_record_id,
                       job.user_id,
                       job.base_id,
                       job.target_key,
                       job.expected_generation,
                       job.operation_fingerprint,
                       job.input_json,
                       COALESCE(job.input_json->>'target_language', $2) AS target_language,
                       base.language AS source_language,
                       base.text AS base_text,
                       unit.order_index,
                       unit.base_start_utf16,
                       unit.base_end_utf16,
                       unit.text_hash
                FROM reader_jobs job
                JOIN reading_bases base
                  ON base.id = job.base_id
                 AND base.reading_record_id = job.reading_record_id
                JOIN reading_units unit
                  ON unit.reading_record_id = job.reading_record_id
                 AND unit.base_id = job.base_id
                 AND unit.unit_id = job.target_key
                WHERE job.id = $1
                """,
                job_id,
                DEFAULT_TRANSLATION_TARGET_LANGUAGE,
            )
            if row is None:
                raise LookupError(f"reader job {job_id} not found")

            base_text = str(row["base_text"])
            source_text = slice_by_utf16_offsets(
                base_text,
                int(row["base_start_utf16"]),
                int(row["base_end_utf16"]),
            )
            if source_text is None or not source_text:
                raise TranslationExecutionError(
                    f"translation unit {row['target_key']} could not be sliced from base text",
                    retryable=False,
                    failure_class="validation",
                    failure_code="unit_slice_failed",
                )
            actual_hash = compute_text_range_hash(source_text)
            expected_hash = str(row["text_hash"])
            if actual_hash != expected_hash:
                raise TranslationExecutionError(
                    (
                        f"translation unit {row['target_key']} hash mismatch: "
                        f"{actual_hash} != {expected_hash}"
                    ),
                    retryable=False,
                    failure_class="validation",
                    failure_code="unit_hash_mismatch",
                )

            segment_rows = await conn.fetch(
                """
                SELECT anchor_segment_id,
                       sentence_id,
                       order_index,
                       segment_type,
                       boundary_quality,
                       unit_start_utf16,
                       unit_end_utf16,
                       text_hash
                FROM anchor_segments
                WHERE reading_record_id = $1
                  AND base_id = $2
                  AND unit_id = $3
                ORDER BY order_index ASC
                """,
                row["reading_record_id"],
                row["base_id"],
                row["target_key"],
            )

        anchor_segments: list[TranslationAnchorSegmentTarget] = []
        for segment_row in segment_rows:
            segment_text = slice_by_utf16_offsets(
                source_text,
                int(segment_row["unit_start_utf16"]),
                int(segment_row["unit_end_utf16"]),
            )
            if segment_text is None or not segment_text:
                raise TranslationExecutionError(
                    (
                        f"translation anchor segment {segment_row['anchor_segment_id']} "
                        "could not be sliced from unit text"
                    ),
                    retryable=False,
                    failure_class="validation",
                    failure_code="anchor_segment_slice_failed",
                )
            segment_hash = str(segment_row["text_hash"])
            actual_segment_hash = compute_text_range_hash(segment_text)
            if actual_segment_hash != segment_hash:
                raise TranslationExecutionError(
                    (
                        f"translation anchor segment {segment_row['anchor_segment_id']} "
                        f"hash mismatch: {actual_segment_hash} != {segment_hash}"
                    ),
                    retryable=False,
                    failure_class="validation",
                    failure_code="anchor_segment_hash_mismatch",
                )
            anchor_segments.append(
                TranslationAnchorSegmentTarget(
                    anchor_segment_id=str(segment_row["anchor_segment_id"]),
                    sentence_id=(
                        str(segment_row["sentence_id"])
                        if segment_row["sentence_id"] is not None
                        else None
                    ),
                    order_index=int(segment_row["order_index"]),
                    segment_type=str(segment_row["segment_type"]),
                    boundary_quality=str(segment_row["boundary_quality"] or "normal"),
                    unit_start_utf16=int(segment_row["unit_start_utf16"]),
                    unit_end_utf16=int(segment_row["unit_end_utf16"]),
                    text_hash=segment_hash,
                    source_text=segment_text,
                )
            )
        if not anchor_segments:
            raise TranslationExecutionError(
                f"translation unit {row['target_key']} has no anchor segments",
                retryable=False,
                failure_class="validation",
                failure_code="anchor_segments_missing",
            )

        # T6: read strategy metadata written by T5 bootstrap from
        # input_json and cross-validate against the live resolver. Missing
        # metadata or hash mismatch fail closed; legacy bare-fingerprint
        # jobs without strategy metadata are rejected, never silently
        # downgraded to a default strategy.
        input_json = row["input_json"]
        strategy_metadata = _validate_translation_strategy_metadata(input_json)

        return TranslationJobContext(
            job_id=row["id"],
            run_id=row["run_id"],
            reading_record_id=row["reading_record_id"],
            user_id=row["user_id"],
            base_id=row["base_id"],
            unit_id=str(row["target_key"]),
            order_index=int(row["order_index"]),
            expected_generation=int(row["expected_generation"]),
            operation_fingerprint=str(row["operation_fingerprint"]),
            source_language=str(row["source_language"] or "en"),
            target_language=str(row["target_language"] or DEFAULT_TRANSLATION_TARGET_LANGUAGE),
            source_text=source_text,
            text_hash=expected_hash,
            anchor_segments=tuple(anchor_segments),
            reading_goal=strategy_metadata.reading_goal,
            reading_variant=strategy_metadata.reading_variant,
            strategy_version=strategy_metadata.strategy_version,
            strategy_hash=strategy_metadata.strategy_hash,
            layer_policy_hash=strategy_metadata.layer_policy_hash,
            translation_prompt_lines=strategy_metadata.translation_prompt_lines,
        )

    async def _mark_run_running(self, run_id: UUID) -> None:
        async with self.get_pool().acquire() as conn:
            await conn.execute(
                """
                UPDATE reader_runs
                SET status = 'running',
                    failure_class = NULL,
                    failure_code = NULL,
                    finished_at = NULL,
                    started_at = COALESCE(started_at, NOW()),
                    updated_at = NOW()
                WHERE id = $1
                """,
                run_id,
            )

    async def _mark_run_status(
        self,
        run_id: UUID,
        *,
        status: str,
        failure_class: str | None,
        failure_code: str | None,
        finished_at: datetime | None,
    ) -> None:
        async with self.get_pool().acquire() as conn:
            await conn.execute(
                """
                UPDATE reader_runs
                SET status = $2,
                    failure_class = $3,
                    failure_code = $4,
                    finished_at = $5,
                    updated_at = NOW()
                WHERE id = $1
                """,
                run_id,
                status,
                failure_class,
                failure_code,
                finished_at,
            )

    async def _record_usage_event(
        self,
        *,
        context: TranslationJobContext,
        execution: TranslationExecutionResult,
        published_layer: PublishedTranslationLayer,
        status: str,
    ) -> UUID | None:
        return await record_ai_usage_event(
            AIUsageEventCreate(
                usage_scope=USAGE_SCOPE_SYSTEM_INTERNAL,
                capability_code=CAPABILITY_READER_TRANSLATION,
                billing_mode=BILLING_MODE_INTERNAL_ONLY,
                status=status,
                user_id=context.user_id,
                reading_record_id=context.reading_record_id,
                reader_run_id=context.run_id,
                reader_job_id=context.job_id,
                enhancement_layer_id=published_layer.layer_id,
                workflow_name="reader_orchestration",
                workflow_version="d4-p1-translation-worker",
                prompt_version=execution.prompt_version,
                model_route=execution.model_route,
                model_profile_id=execution.model_profile,
                model_profile=execution.model_profile,
                model_provider=execution.model_provider,
                model_name=execution.model_name,
                planner_kind="llm_worker",
                usage_data=execution.usage_data,
                operation_fingerprint=context.operation_fingerprint,
                metadata_json={
                    "base_id": str(context.base_id),
                    "unit_id": context.unit_id,
                    "target_language": context.target_language,
                    "source_language": context.source_language,
                },
            )
        )

    async def _record_failed_usage_event(
        self,
        *,
        context: TranslationJobContext | None,
        error_code: str,
        error_message: str,
    ) -> UUID | None:
        if context is None:
            return None
        return await record_ai_usage_event(
            AIUsageEventCreate(
                usage_scope=USAGE_SCOPE_SYSTEM_INTERNAL,
                capability_code=CAPABILITY_READER_TRANSLATION,
                billing_mode=BILLING_MODE_INTERNAL_ONLY,
                status=STATUS_FAILED,
                user_id=context.user_id,
                reading_record_id=context.reading_record_id,
                reader_run_id=context.run_id,
                reader_job_id=context.job_id,
                workflow_name="reader_orchestration",
                workflow_version="d4-p1-translation-worker",
                model_route=MODEL_ROUTE_READER_LAYER_TRANSLATION,
                planner_kind="llm_worker",
                operation_fingerprint=context.operation_fingerprint,
                error_code=error_code,
                error_message=error_message,
                metadata_json={
                    "base_id": str(context.base_id),
                    "unit_id": context.unit_id,
                    "target_language": context.target_language,
                    "source_language": context.source_language,
                },
            )
        )


def _build_translation_prompt(context: TranslationJobContext) -> str:
    strategy_section = _format_translation_strategy_section(context)
    grouping_section = _format_grouping_guidance_section()
    target_segments_section = _format_target_segments_section(context)
    return (
        "Translate the following reading unit.\n"
        f"source_language: {context.source_language}\n"
        f"target_language: {context.target_language}\n"
        f"unit_id: {context.unit_id}\n"
        f"{strategy_section}"
        f"{grouping_section}"
        "Return only the structured TranslationLayerGenerationOutput.\n"
        "Only output groups[].anchor_segment_ids and groups[].translated_text.\n"
        "Do not output source_text, source_text_hash, group_id, segment_sources, "
        "profile, source_language, target_language, diagnostics, confidence, "
        "reason, notes, coverage_json, quality_json, or any UI, Plate, Slate, "
        "or DOM fields.\n"
        'If boundary_quality="low", treat it only as a hint that the boundary '
        "is unreliable. You may merge such segments with adjacent segments when "
        "that improves readability, but do not force a split or skip.\n"
        "<source_text>\n"
        f"{context.source_text}\n"
        "</source_text>\n"
        f"{target_segments_section}"
    )


def _format_grouping_guidance_section() -> str:
    """Render the per-call semantic grouping guidance that goes alongside the
    variant-specific policy lines. This section is variant-independent; it
    teaches the model how to think about group granularity for any variant.

    Hard rules:
      - Translate the unit as a whole; do NOT fill a row per anchor segment.
      - Each group must cover a contiguous semantic reading unit.
      - Short consecutive sentences that jointly express one semantic move,
        argument step, example, contrast, or explanation chain must merge
        into a single group.
      - Titles, list items, and isolated long/complex sentences may stand
        alone as their own group.
      - No fixed min/max group size; the semantic reading unit decides.
      - `target_segments` is a registry of anchor handles, not a row template.
    """
    return (
        "<grouping_guidance>\n"
        "Your main translation object is the complete unit source_text in this "
        "prompt, not a per-anchor-segment fill-in-the-blank table.\n"
        "You must output semantic reading groups: each group should cover a "
        "span of contiguous anchor_segment_ids that jointly form one reading "
        "unit (one semantic action, one argumentative step, one example, one "
        "turn/contrast, or one explanation chain).\n"
        "Do NOT mechanically create one group per anchor segment. A group may "
        "cover one or more consecutive anchor_segment_ids.\n"
        "Short, consecutive sentences that share a single semantic move, "
        "argument step, example, contrast, or explanation chain must be merged "
        "into a single group rather than split one-per-segment.\n"
        "Titles, list items, and isolated long/complex sentences may stand "
        "alone as their own group.\n"
        "Do not aim to collapse everything into one giant group, and do not "
        "aim to split one sentence per group either. Let the semantic reading "
        "unit decide the granularity.\n"
        "Do not pad groups or force splits to hit a count.\n"
        "There is no fixed minimum or maximum group size. There is no fixed "
        "number of groups. Granularity is decided by the semantic reading "
        "structure of the text.\n"
        "<target_segments_registry_note>\n"
        "The `<target_segments>` block below lists the available anchor "
        "handles you may reference in `groups[].anchor_segment_ids`. It is a "
        "registry of valid ids, not a row-by-row output template. You choose "
        "which anchor_segment_ids belong to each group; you do not need to "
        "produce one row per listed id.\n"
        "</target_segments_registry_note>\n"
        "</grouping_guidance>\n"
    )


def _format_target_segments_section(context: TranslationJobContext) -> str:
    lines = ["<target_segments>"]
    lines.append(
        "Each entry below is an anchor handle you may reference from "
        "`groups[].anchor_segment_ids`. Pick the contiguous ids that form "
        "each semantic reading group; do NOT treat this list as a row "
        "template that requires one output row per id."
    )
    for segment in context.anchor_segments:
        lines.extend(
            [
                "- anchor_segment_id: " + segment.anchor_segment_id,
                f"  sentence_id: {segment.sentence_id or ''}",
                f"  order_index: {segment.order_index}",
                f"  segment_type: {segment.segment_type}",
                f"  boundary_quality: {segment.boundary_quality}",
                f"  source_text_hash: {segment.text_hash}",
                "  <source_text>",
                segment.source_text,
                "  </source_text>",
            ]
        )
    lines.append("</target_segments>")
    return "\n".join(lines)


def _format_translation_strategy_section(context: TranslationJobContext) -> str:
    """Format the concrete translation policy lines as a prompt section.

    The strategy section carries the resolved variant-first policy lines
    (from ``reader_variants.yaml`` via ``resolve_reader_variant_strategy``)
    so the translation agent can vary its output by ``reading_goal`` /
    ``reading_variant``. The accompanying hashes are included for
    traceability and so that prompt-level evals can group by strategy.
    """
    lines_bullet = "\n".join(
        f"- {line}" for line in context.translation_prompt_lines
    )
    return (
        "<reader_strategy>\n"
        f"reading_goal: {context.reading_goal}\n"
        f"reading_variant: {context.reading_variant}\n"
        f"strategy_version: {context.strategy_version}\n"
        f"strategy_hash: {context.strategy_hash}\n"
        f"layer_policy_hash: {context.layer_policy_hash}\n"
        "<policy_lines>\n"
        f"{lines_bullet}\n"
        "</policy_lines>\n"
        "</reader_strategy>\n"
    )


@dataclass(frozen=True, slots=True)
class _TranslationStrategyMetadata:
    """Validated strategy metadata extracted from a translation job's
    input_json and cross-checked against the live resolver."""

    reading_goal: str
    reading_variant: str
    strategy_version: str
    strategy_hash: str
    layer_policy_hash: str
    translation_prompt_lines: tuple[str, ...]


def _validate_translation_strategy_metadata(
    input_json: Any,
) -> _TranslationStrategyMetadata:
    """Read strategy metadata from input_json and validate against the resolver.

    Fail-closed contract:
        - ``input_json`` must be a mapping containing every key in
          :data:`_STRATEGY_INPUT_KEYS` with a non-empty string value.
          Legacy bare-fingerprint jobs without strategy metadata are
          rejected with ``strategy_metadata_missing``; there is NO default
          fallback.
        - The ``(reading_goal, reading_variant)`` pair must resolve via
          :func:`resolve_reader_variant_strategy`. Resolver errors
          (unknown variant, missing layer, etc.) propagate as
          :class:`TranslationExecutionError` with failure_class
          ``strategy_resolution``.
        - ``strategy_version``, ``strategy_hash`` and
          ``layer_policy_hash`` from input_json must match the resolver
          output exactly. Any mismatch fails closed with a dedicated
          failure_code.
    """
    if not isinstance(input_json, Mapping):
        raise TranslationExecutionError(
            "translation job input_json is not a mapping; "
            "strategy metadata cannot be read",
            retryable=False,
            failure_class="validation",
            failure_code=_STRATEGY_METADATA_MISSING_CODE,
        )

    missing: list[str] = []
    for key in _STRATEGY_INPUT_KEYS:
        value = input_json.get(key)
        if not isinstance(value, str) or not value:
            missing.append(key)
    if missing:
        raise TranslationExecutionError(
            "translation job input_json is missing strategy metadata: "
            + ", ".join(missing),
            retryable=False,
            failure_class="validation",
            failure_code=_STRATEGY_METADATA_MISSING_CODE,
        )

    reading_goal = str(input_json["reading_goal"])
    reading_variant = str(input_json["reading_variant"])
    expected_strategy_version = str(input_json["strategy_version"])
    expected_strategy_hash = str(input_json["strategy_hash"])
    expected_layer_policy_hash = str(input_json["layer_policy_hash"])

    try:
        strategy = resolve_reader_variant_strategy(reading_goal, reading_variant)
    except ReaderStrategyResolverError as exc:
        raise TranslationExecutionError(
            f"translation strategy resolver rejected pair "
            f"({reading_goal!r}, {reading_variant!r}): {exc}",
            retryable=False,
            failure_class="strategy_resolution",
            failure_code="strategy_resolver_error",
        ) from exc

    if strategy.strategy_version != expected_strategy_version:
        raise TranslationExecutionError(
            f"translation strategy_version mismatch: input_json has "
            f"{expected_strategy_version!r} but resolver produced "
            f"{strategy.strategy_version!r}",
            retryable=False,
            failure_class="validation",
            failure_code=_STRATEGY_VERSION_MISMATCH_CODE,
        )

    if strategy.strategy_hash != expected_strategy_hash:
        raise TranslationExecutionError(
            f"translation strategy_hash mismatch: input_json has "
            f"{expected_strategy_hash!r} but resolver produced "
            f"{strategy.strategy_hash!r}",
            retryable=False,
            failure_class="validation",
            failure_code=_STRATEGY_HASH_MISMATCH_CODE,
        )

    layer = strategy.layers.get(_TRANSLATION_LAYER_NAME)
    if layer is None:
        # Defensive: the resolver guarantees all REQUIRED_LAYERS are
        # present. Fail closed if a future code path violates that.
        raise TranslationExecutionError(
            f"resolved strategy has no layer {_TRANSLATION_LAYER_NAME!r}",
            retryable=False,
            failure_class="strategy_resolution",
            failure_code="strategy_resolver_error",
        )

    if layer.policy_hash != expected_layer_policy_hash:
        raise TranslationExecutionError(
            f"translation layer_policy_hash mismatch: input_json has "
            f"{expected_layer_policy_hash!r} but resolver produced "
            f"{layer.policy_hash!r}",
            retryable=False,
            failure_class="validation",
            failure_code=_LAYER_POLICY_HASH_MISMATCH_CODE,
        )

    return _TranslationStrategyMetadata(
        reading_goal=reading_goal,
        reading_variant=reading_variant,
        strategy_version=strategy.strategy_version,
        strategy_hash=strategy.strategy_hash,
        layer_policy_hash=layer.policy_hash,
        translation_prompt_lines=layer.prompt_lines,
    )


def hydrate_translation_layer_output(
    *,
    context: TranslationJobContext,
    generation: TranslationLayerGenerationOutput,
) -> TranslationLayerOutput:
    anchor_segments_by_id = {
        segment.anchor_segment_id: segment for segment in context.anchor_segments
    }
    hydrated_groups: list[TranslationGroup] = []
    for group in generation.groups:
        resolved_segments: list[TranslationAnchorSegmentTarget] = []
        for anchor_segment_id in group.anchor_segment_ids:
            segment = anchor_segments_by_id.get(anchor_segment_id)
            if segment is None:
                raise TranslationExecutionError(
                    f"translation group references unknown anchor_segment_id "
                    f"{anchor_segment_id!r}",
                    retryable=False,
                    failure_class="validation",
                    failure_code="translation_unknown_anchor_segment",
                )
            resolved_segments.append(segment)

        first_segment = resolved_segments[0]
        last_segment = resolved_segments[-1]
        if first_segment.unit_start_utf16 > last_segment.unit_end_utf16:
            raise TranslationExecutionError(
                f"translation group span is inverted for unit {context.unit_id}: "
                f"{first_segment.anchor_segment_id} -> {last_segment.anchor_segment_id}",
                retryable=False,
                failure_class="validation",
                failure_code="translation_group_span_inverted",
            )

        group_source_text = slice_by_utf16_offsets(
            context.source_text,
            first_segment.unit_start_utf16,
            last_segment.unit_end_utf16,
        )
        if group_source_text is None or not group_source_text:
            raise TranslationExecutionError(
                f"translation group source span could not be sliced for unit "
                f"{context.unit_id}",
                retryable=False,
                failure_class="validation",
                failure_code="translation_group_slice_failed",
            )

        hydrated_groups.append(
            TranslationGroup(
                group_id=(
                    f"{context.unit_id}_g"
                    f"{first_segment.order_index}_{last_segment.order_index}"
                ),
                anchor_segment_ids=list(group.anchor_segment_ids),
                source_text_hash=compute_text_range_hash(group_source_text),
                translated_text=group.translated_text,
            )
        )

    return TranslationLayerOutput(groups=hydrated_groups)


def _build_quality_json(
    output: TranslationLayerOutput,
    execution: TranslationExecutionResult,
) -> dict[str, Any]:
    quality_json: dict[str, Any] = {
        "group_count": len(output.groups),
    }
    if execution.prompt_version is not None:
        quality_json["prompt_version"] = execution.prompt_version
    if execution.model_route:
        quality_json["model_route"] = execution.model_route
    if execution.model_profile is not None:
        quality_json["model_profile"] = execution.model_profile
    if execution.model_provider is not None:
        quality_json["model_provider"] = execution.model_provider
    if execution.model_name is not None:
        quality_json["model_name"] = execution.model_name
    return quality_json
